import csv
import io
import os
import uuid
from collections import defaultdict
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    Response, session
)
from werkzeug.security import generate_password_hash, check_password_hash

import psycopg2
import psycopg2.extras
import parser as bomparser

# Load environment variables from a local .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "bom-workspace-dev-secret")

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    # Neon requires sslmode='require'
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    conn.cursor_factory = psycopg2.extras.DictCursor
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS manual_matches (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS boms (
            id SERIAL PRIMARY KEY,
            team TEXT NOT NULL,
            project TEXT NOT NULL,
            notes TEXT,
            bom_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            upload_date TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'Pending'
        );
        CREATE TABLE IF NOT EXISTS bom_items (
            id SERIAL PRIMARY KEY,
            bom_id INTEGER NOT NULL REFERENCES boms(id) ON DELETE CASCADE,
            row_index INTEGER,
            category TEXT,
            qty REAL,
            item TEXT,
            specs TEXT,
            link TEXT,
            vendor TEXT,
            other_vendor TEXT,
            resolved_vendor TEXT,
            event_category TEXT,
            other_category TEXT,
            unit_cost REAL,
            total_cost REAL,
            physical TEXT,
            subsystem TEXT,
            units_per_package REAL,
            units_per_bot REAL,
            comments TEXT,
            orderable INTEGER DEFAULT 1,
            match_key TEXT,
            flagged INTEGER DEFAULT 0,
            order_status TEXT DEFAULT 'Not Ordered',
            storage_location TEXT DEFAULT '',
            group_override_id TEXT,
            is_given INTEGER DEFAULT 0,
            needs_return INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

init_db()

PENDING = {}

GOOGLE_SHEET_LINKS = {
    "bot": "https://docs.google.com/spreadsheets/d/17qD1AEyG1CTrCvH-arksRlbwpSFXzngoIwlpLEWm0D4/edit?usp=sharing",
    "general": "https://docs.google.com/spreadsheets/d/17Z1K6nDZC06eY-0sx92mCxc0LjPqgKk_zKDKtVu-YGM/edit?usp=sharing",
}

ORDER_STATUSES = [
    "Not Ordered",
    "Ordered",
    "Shipped",
    "Delivered",
    "Backordered",
    "Cancelled",
]

STORAGE_LOCATIONS = [
    "",
    "Shelf A",
    "Shelf B",
    "Bin 1",
    "Bin 2",
    "Lab Main Table",
]

SHARED_ADMIN_PASSWORD = os.getenv("SHARED_ADMIN_PASSWORD", "adminpassword123")

def now_iso():
    from datetime import datetime
    return datetime.utcnow().isoformat()

def get_submission(sub_id):
    conn = get_conn()
    bom = conn.execute("SELECT * FROM boms WHERE id = %s", (sub_id,)).fetchone()
    if not bom:
        conn.close()
        return None
    items = conn.execute("SELECT * FROM bom_items WHERE bom_id = %s ORDER BY id", (sub_id,)).fetchall()
    conn.close()
    return {
        **dict(bom),
        "items": [dict(it) for it in items]
    }

def get_all_pending_submissions():
    conn = get_conn()
    boms = conn.execute("SELECT * FROM boms WHERE status = 'Pending' AND active = 1 ORDER BY id DESC").fetchall()
    conn.close()
    results = []
    for b in boms:
        sub = get_submission(b["id"])
        if sub:
            results.append(sub)
    return results

def update_submission_status(sub_id, status):
    conn = get_conn()
    conn.execute("UPDATE boms SET status = %s WHERE id = %s", (status, sub_id))
    conn.commit()
    conn.close()

def delete_bom_item(item_id):
    conn = get_conn()
    conn.execute("DELETE FROM bom_items WHERE id = %s", (item_id,))
    conn.commit()
    conn.close()

def update_vendor_order_status(vendor, order_status, storage_location):
    conn = get_conn()
    conn.execute("""
        UPDATE bom_items 
        SET order_status = %s, storage_location = %s 
        WHERE resolved_vendor = %s AND bom_id IN (
            SELECT id FROM boms WHERE active = 1 AND status = 'Approved'
        )
    """, (order_status, storage_location, vendor))
    conn.commit()
    conn.close()

def new_match_id():
    return uuid.uuid4().hex[:12]

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session or not session.get("is_admin"):
            flash("Administrator access required.", "error")
            return redirect(url_for("upload"))
        return f(*args, **kwargs)
    return decorated_function

def money(v):
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"

app.jinja_env.filters["money"] = money
app.jinja_env.globals["ORDER_STATUSES"] = ORDER_STATUSES
app.jinja_env.globals["STORAGE_LOCATIONS"] = STORAGE_LOCATIONS
app.jinja_env.globals["GOOGLE_SHEET_LINKS"] = GOOGLE_SHEET_LINKS

def fetch_items(active_only=True, orderable_only=False, team_filter=None, approved_only=True):
    conn = get_conn()
    q = """
        SELECT bi.*, b.team, b.project, b.bom_type, b.id as bom_pk,
               b.upload_date, b.active as bom_active, b.filename, b.status
        FROM bom_items bi
        JOIN boms b ON bi.bom_id = b.id
    """
    clauses = []
    if active_only:
        clauses.append("b.active = 1")
    if approved_only:
        clauses.append("b.status = 'Approved'")
    if orderable_only:
        clauses.append("bi.orderable = 1")
    if team_filter:
        clauses.append("b.team = %s")
    
    if not session.get("is_admin") and session.get("username"):
        clauses.append("b.team = %s")

    params = []
    if team_filter:
        params.append(team_filter)
    
    if not session.get("is_admin") and session.get("username"):
        params.append(session.get("username"))

    if clauses:
        q += " WHERE " + " AND ".join(clauses)
        
    rows = conn.execute(q, tuple(params)).fetchall()
    conn.close()
    
    parsed_rows = []
    for r in rows:
        d = dict(r)
        upp = d.get("units_per_package")
        if upp and upp > 1:
            d["qty"] = (d["qty"] or 0) * upp
        parsed_rows.append(d)
    return parsed_rows

def group_items(items):
    groups = defaultdict(lambda: {
        "item": "", "specs": "", "vendor": "", "category": "", "link": "",
        "qty": 0.0, "cost": 0.0, "teams": set(), "projects": set(),
        "boms": set(), "row_ids": [], "manual": False, "key": "",
        "status_counts": defaultdict(float),
    })
    for it in items:
        key = it["group_override_id"] if it["group_override_id"] else f"item_row__{it['id']}"
        g = groups[key]
        g["key"] = key
        g["manual"] = bool(it["group_override_id"])
        if not g["item"]:
            g["item"] = it["item"]
            g["specs"] = it["specs"]
            g["vendor"] = it["resolved_vendor"] or "Unspecified"
            g["category"] = it["category"]
            g["link"] = it["link"]
        g["qty"] += it["qty"] or 0
        g["cost"] += it["total_cost"] or 0
        g["teams"].add(it["team"])
        g["projects"].add(it["project"])
        g["boms"].add(f"{it['project']} ({it['team']})")
        g["row_ids"].append(it["id"])
        g["status_counts"][it["order_status"] or "Not Ordered"] += (it["qty"] or 0)
    
    result = []
    for g in groups.values():
        unit_cost = (g["cost"] / g["qty"]) if g["qty"] else 0
        statuses_present = [s for s in ORDER_STATUSES if g["status_counts"].get(s)]
        if len(statuses_present) == 1:
            status_label = statuses_present[0]
        elif len(statuses_present) > 1:
            status_label = "Mixed"
        else:
            status_label = "Not Ordered"
        result.append({
            **g,
            "unit_cost": unit_cost,
            "status_label": status_label,
        })
    result.sort(key=lambda g: g["item"].lower())
    return result

def apply_filters(items, args):
    q = (args.get("q") or "").strip().lower()
    team = args.get("team") or ""
    project = args.get("project") or ""
    category = args.get("category") or ""
    vendor = args.get("vendor") or ""
    bom_type = args.get("bom_type") or ""
    status = args.get("status") or ""

    def keep(it):
        if q and q not in (it["item"] + " " + it["specs"] + " " + (it["comments"] or "")).lower():
            return False
        if team and it["team"] != team:
            return False
        if project and it["project"] != project:
            return False
        if category and (it["category"] or "") != category:
            return False
        if vendor and (it["resolved_vendor"] or "Unspecified") != vendor:
            return False
        if bom_type and it["bom_type"] != bom_type:
            return False
        if status and (it["order_status"] or "Not Ordered") != status:
            return False
        return True

    return [it for it in items if keep(it)]

def filter_options(items):
    return {
        "teams": sorted({it["team"] for it in items}),
        "projects": sorted({it["project"] for it in items}),
        "categories": sorted({it["category"] for it in items if it["category"]}),
        "vendors": sorted({it["resolved_vendor"] or "Unspecified" for it in items}),
    }

def groups_to_csv(groups, include_teams=True):
    buf = io.StringIO()
    w = csv.writer(buf)
    header = ["Item", "Ordering Specs", "Vendor/Source", "Category", "Total Qty",
              "Unit Cost", "Combined Cost", "Link", "Order Status"]
    if include_teams:
        header.append("Requesting Teams/Projects")
    w.writerow(header)
    for g in groups:
        row = [g["item"], g["specs"], g["vendor"], g["category"], g["qty"],
               f"{g['unit_cost']:.2f}", f"{g['cost']:.2f}", g["link"], g["status_label"]]
        if include_teams:
            row.append("; ".join(sorted(g["boms"])))
        w.writerow(row)
    return buf.getvalue()

def csv_response(text, filename):
    return Response(
        text, mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    if username.lower() == "admin":
        if password == SHARED_ADMIN_PASSWORD:
            session["user_id"] = 0
            session["username"] = "admin"
            session["is_admin"] = True
            flash("Logged in successfully as Administrator.", "success")
            return redirect(url_for("order_view"))
        else:
            flash("Invalid administrator password.", "error")
            return redirect(url_for("login"))

    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE username = %s", (username,)).fetchone()
    conn.close()

    if user and check_password_hash(user["password_hash"], password):
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["is_admin"] = False
        flash(f"Welcome back, {username}!", "success")
        return redirect(url_for("upload"))

    flash("Invalid username or password.", "error")
    return redirect(url_for("login"))

@app.route("/register", methods=["POST"])
def register():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("login"))

    if username.lower() == "admin":
        flash("The username 'admin' is reserved.", "error")
        return redirect(url_for("login"))

    hashed_pw = generate_password_hash(password)

    conn = get_conn()
    try:
        conn.execute("INSERT INTO users (username, password, password_hash, is_admin) VALUES (%s, %s, %s, 0)",
                     (username, password, hashed_pw))
        conn.commit()
        flash("Account created successfully. Please log in.", "success")
    except psycopg2.IntegrityError:
        conn.rollback()
        flash("Username already taken.", "error")
    finally:
        conn.close()

    return redirect(url_for("login"))

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    if session.get("is_admin"):
        return redirect(url_for("order_view"))
    return redirect(url_for("upload"))

@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "GET":
        return render_template("upload.html")

    f = request.files.get("file")
    if not f or f.filename == "":
        flash("Please choose a file to upload.", "error")
        return redirect(url_for("upload"))

    try:
        raw = f.read()
        grid = bomparser.load_grid(f.filename, raw)
    except Exception as e:
        flash(f"Could not read that file: {e}", "error")
        return redirect(url_for("upload"))

    bom_type, details = bomparser.detect_template(grid)

    if bom_type is None:
        return render_template(
            "upload.html",
            detect_error=True,
            details=details,
            filename=f.filename,
        )

    if bom_type == "general":
        items = bomparser.parse_general_rows(grid)
    else:
        items = bomparser.parse_bot_rows(grid)

    for it in items:
        specs_lower = (it.get("specs") or "").strip().lower()
        if any(keyword in specs_lower for keyword in ["filament", "stock", "urethane"]):
            it["orderable"] = 0

    token = uuid.uuid4().hex[:10]
    PENDING[token] = {
        "filename": f.filename,
        "bom_type": bom_type,
        "items": items,
    }
    return redirect(url_for("review", token=token))

@app.route("/download_template/<kind>")
@login_required
def download_template(kind):
    if kind not in GOOGLE_SHEET_LINKS:
        flash("Unknown template requested.", "error")
        return redirect(url_for("upload"))
    return redirect(GOOGLE_SHEET_LINKS[kind])

@app.route("/review/<token>", methods=["GET"])
@login_required
def review(token):
    pending = PENDING.get(token)
    if not pending:
        flash("That upload has expired. Please upload again.", "error")
        return redirect(url_for("upload"))

    items = pending["items"]
    orderable_count = 0
    tracked_count = 0
    est_cost = 0.0

    for i in items:
        specs_lower = (i.get("specs") or "").strip().lower()
        is_rail_material = any(k in specs_lower for k in ["filament", "stock", "urethane"])
        
        if i["orderable"] or is_rail_material:
            orderable_count += 1
            est_cost += (i["total_cost"] or 0)
        else:
            tracked_count += 1

    flagged = [i for i in items if i["flagged"]]

    return render_template(
        "review.html", token=token, bom_type=pending["bom_type"],
        filename=pending["filename"], items=items,
        orderable_count=orderable_count, tracked_count=tracked_count,
        flagged=flagged, est_cost=est_cost,
    )

@app.route("/review/<token>/confirm", methods=["POST"])
@login_required
def review_confirm(token):
    pending = PENDING.get(token)
    if not pending:
        flash("That upload has expired. Please upload again.", "error")
        return redirect(url_for("upload"))

    team = request.form.get("team", "").strip() or session.get("username", "Unassigned Team")
    project = request.form.get("project", "").strip() or "Untitled Project"
    notes = request.form.get("notes", "").strip()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO boms (team, project, notes, bom_type, filename, upload_date, active, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, 1, 'Pending') RETURNING id",
        (team, project, notes, pending["bom_type"], pending["filename"], now_iso()),
    )
    bom_id = cur.fetchone()[0]
    
    for it in pending["items"]:
        specs_lower = (it.get("specs") or "").strip().lower()
        orderable = 0 if any(k in specs_lower for k in ["filament", "stock", "urethane"]) else it["orderable"]
        
        conn.execute(
            """INSERT INTO bom_items
               (bom_id, row_index, category, qty, item, specs, link, vendor,
                other_vendor, resolved_vendor, event_category, other_category,
                unit_cost, total_cost, physical, subsystem, units_per_package,
                units_per_bot, comments, orderable, match_key, flagged,
                order_status, storage_location, is_given, needs_return)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, 0, 0)""",
            (
                bom_id, it["row_index"], it["category"], it["qty"], it["item"],
                it["specs"], it["link"], it["vendor"], it["other_vendor"],
                it["resolved_vendor"], it["event_category"], it["other_category"],
                it["unit_cost"], it["total_cost"], it["physical"], it["subsystem"],
                it["units_per_package"], it["units_per_bot"], it["comments"],
                orderable, it["match_key"], it["flagged"],
                "Not Ordered", "",
            ),
        )
    conn.commit()
    cur.close()
    conn.close()
    del PENDING[token]
    flash(f"Submitted '{project}' for admin review.", "success")
    return redirect(url_for("history"))

@app.route("/review/<token>/cancel", methods=["POST"])
@login_required
def review_cancel(token):
    PENDING.pop(token, None)
    flash("Upload discarded.", "info")
    return redirect(url_for("upload"))

@app.route("/history")
@login_required
def history():
    conn = get_conn()
    if session.get("is_admin"):
        boms = conn.execute("SELECT * FROM boms ORDER BY id DESC").fetchall()
    else:
        boms = conn.execute("SELECT * FROM boms WHERE team = %s ORDER BY id DESC", (session.get("username"),)).fetchall()
    conn.close()
    rows = []
    def is_special_item_row(i):
        s = " ".join([str(i.get("item") or ""), str(i.get("specs") or ""), str(i.get("category") or ""), str(i.get("resolved_vendor") or "")]).lower()
        return ("filament" in s) or ("urethane" in s) or ("tpu" in s) or ("stock" in s)

    for b in boms:
        b_dict = dict(b)
        items = get_submission(b_dict["id"])["items"]
        cost = sum((i["total_cost"] or 0) for i in items if i.get("orderable") or is_special_item_row(i))
        rows.append({
            "bom": b_dict,
            "count": len(items),
            "orderable": sum(1 for i in items if i["orderable"]),
            "cost": cost,
        })
    return render_template("history.html", rows=rows)

@app.route("/team/bom/<int:sub_id>")
@login_required
def view_team_bom(sub_id):
    submission = get_submission(sub_id)
    if not submission or (not session.get("is_admin") and submission["team"] != session.get("username")):
        flash("Submission not found or unauthorized.", "error")
        return redirect(url_for("history"))
    def is_special_item(it):
        s = " ".join([str(it.get("item") or ""), str(it.get("specs") or ""), str(it.get("category") or ""), str(it.get("resolved_vendor") or "")]).lower()
        return ("filament" in s) or ("urethane" in s) or ("tpu" in s) or ("stock" in s)

    total_cost = 0.0
    for it in submission["items"]:
        it["display_tracked"] = (not bool(it.get("orderable"))) and (not is_special_item(it))
        if it.get("orderable") or is_special_item(it):
            total_cost += float(it.get("total_cost") or 0)

    submission["total_cost"] = total_cost
    return render_template("view_bom.html", submission=submission)

@app.route("/admin/approvals")
@admin_required
def admin_approvals():
    pending_subs = get_all_pending_submissions()
    return render_template("admin_approvals.html", submissions=pending_subs)

@app.route("/admin/item/<int:item_id>/delete", methods=["POST"])
@admin_required
def admin_delete_item(item_id):
    delete_bom_item(item_id)
    flash("Line item deleted.", "info")
    return redirect(url_for("admin_approvals"))

@app.route("/admin/approvals/<int:sub_id>/<action>", methods=["POST"])
@admin_required
def handle_approval(sub_id, action):
    if action == 'approve':
        return_item_ids = request.form.getlist("needs_return_items")
        conn = get_conn()
        conn.execute("UPDATE bom_items SET needs_return = 0 WHERE bom_id = %s", (sub_id,))
        if return_item_ids:
            qmarks = ",".join(["%s"] * len(return_item_ids))
            conn.execute(f"UPDATE bom_items SET needs_return = 1 WHERE id IN ({qmarks}) AND bom_id = %s", (*return_item_ids, sub_id))
        conn.commit()
        conn.close()

        update_submission_status(sub_id, 'Approved')
        flash("BOM approved, return configuration saved, and added to master order list.", "success")
        return redirect(url_for('tracked_view'))
    elif action == 'reject':
        update_submission_status(sub_id, 'Rejected')
        flash("BOM submission rejected.", "info")
    elif action == 'unapprove':
        update_submission_status(sub_id, 'Pending')
        flash("BOM approval cancelled.", "info")

    return redirect(url_for('admin_approvals'))

@app.route("/admin/returns", methods=["GET"])
@admin_required
def admin_returns():
    conn = get_conn()
    teams_query = conn.execute("SELECT DISTINCT b.team FROM boms b JOIN bom_items bi ON b.id = bi.bom_id WHERE b.active = 1 AND b.status = 'Approved' ORDER BY b.team").fetchall()
    teams = [t["team"] for t in teams_query]

    selected_team = request.args.get("team", "").strip()
    return_items = []
    
    if selected_team == "ALL":
        q = """
            SELECT bi.*, b.team, b.project, b.filename
            FROM bom_items bi
            JOIN boms b ON bi.bom_id = b.id
            WHERE b.active = 1 AND b.status = 'Approved' AND bi.needs_return = 1
            ORDER BY b.team, b.project
        """
        return_items = [dict(row) for row in conn.execute(q).fetchall()]
    elif selected_team:
        q = """
            SELECT bi.*, b.team, b.project, b.filename
            FROM bom_items bi
            JOIN boms b ON bi.bom_id = b.id
            WHERE b.active = 1 AND b.status = 'Approved' AND b.team = %s AND bi.needs_return = 1
            ORDER BY b.project
        """
        return_items = [dict(row) for row in conn.execute(q, (selected_team,)).fetchall()]

    conn.close()
    return render_template("returns.html", teams=teams, selected_team=selected_team, return_items=return_items)

@app.route("/bom/<int:bom_id>/delete", methods=["POST"])
@login_required
def delete_bom(bom_id):
    conn = get_conn()
    if not session.get("is_admin"):
        bom = conn.execute("SELECT * FROM boms WHERE id = %s AND team = %s", (bom_id, session.get("username"))).fetchone()
        if not bom:
            conn.close()
            flash("Unauthorized action.", "error")
            return redirect(url_for("history"))
    conn.execute("UPDATE boms SET active = 0 WHERE id = %s", (bom_id,))
    conn.commit()
    conn.close()
    flash("BOM removed.", "info")
    return redirect(url_for("history"))

@app.route("/bom/<int:bom_id>/restore", methods=["POST"])
@login_required
def restore_bom(bom_id):
    conn = get_conn()
    if not session.get("is_admin"):
        bom = conn.execute("SELECT * FROM boms WHERE id = %s AND team = %s", (bom_id, session.get("username"))).fetchone()
        if not bom:
            conn.close()
            flash("Unauthorized action.", "error")
            return redirect(url_for("history"))
    conn.execute("UPDATE boms SET active = 1 WHERE id = %s", (bom_id,))
    conn.commit()
    conn.close()
    flash("BOM restored.", "info")
    return redirect(url_for("history"))

@app.route("/order")
@admin_required
def order_view():
    all_items = fetch_items(active_only=True, orderable_only=True, approved_only=True)
    options = filter_options(all_items)
    items = apply_filters(all_items, request.args)
    groups = group_items(items)

    all_bom_items = fetch_items(active_only=True, approved_only=True)
    summary = {
        "boms": len({i["bom_pk"] for i in all_bom_items}),
        "unique_items": len(group_items(fetch_items(active_only=True, orderable_only=True, approved_only=True))),
        "total_units": sum(g["qty"] for g in groups),
        "total_cost": sum(g["cost"] for g in groups),
    }
    return render_template("order.html", groups=groups, options=options,
                           args=request.args, summary=summary)

@app.route("/order/tracked", methods=["GET", "POST"])
@admin_required
def tracked_view():
    conn = get_conn()
    if request.method == "POST":
        item_id = request.form.get("item_id")
        action = request.form.get("action")
        if action == "toggle_given":
            conn.execute("UPDATE bom_items SET is_given = NOT COALESCE(is_given, FALSE) WHERE id = %s", (item_id,))
            conn.commit()
        conn.close()
        return redirect(url_for("tracked_view"))

    items = fetch_items(active_only=True, orderable_only=False, approved_only=True)
    
    def is_ignored_material(it):
        s = " ".join([str(it.get("item") or ""), str(it.get("specs") or ""), str(it.get("category") or ""), str(it.get("resolved_vendor") or "")]).lower()
        return ("filament" in s) or ("urethane" in s) or ("tpu" in s) or ("stock" in s)

    def is_ignored_by_vendor(it):
        name = (it.get("item") or "").lower()
        rv = (it.get("resolved_vendor") or "").lower()
        return ("repeat mk2" in name) or ("railsid" in rv) or ("railside" in rv)

    active_tracked = [i for i in items if i.get("orderable") == 1 and not i.get("is_given") and not is_ignored_material(i) and not is_ignored_by_vendor(i)]
    given_tracked = [i for i in items if i.get("orderable") == 1 and i.get("is_given") and not is_ignored_material(i) and not is_ignored_by_vendor(i)]
    
    active_tracked = apply_filters(active_tracked, request.args)
    given_tracked = apply_filters(given_tracked, request.args)
    
    options = filter_options(fetch_items(active_only=True, approved_only=True))
    conn.close()
    return render_template("tracked.html", items=active_tracked, given_items=given_tracked, options=options, args=request.args)

@app.route("/admin/teams")
@admin_required
def admin_teams_view():
    conn = get_conn()
    team_names = set()
    users = conn.execute("SELECT username, password FROM users WHERE is_admin = 0").fetchall()
    user_passwords = {}
    for u in users:
        team_names.add(u["username"])
        user_passwords[u["username"]] = u["password"] or "(Not available)"
        
    boms_teams = conn.execute("SELECT DISTINCT team FROM boms WHERE team IS NOT NULL AND team != ''").fetchall()
    for b in boms_teams:
        team_names.add(b["team"])
    
    team_data = []
    for t_name in sorted(team_names):
        spent_row = conn.execute("""
            SELECT SUM(bi.total_cost) as total_spent
            FROM bom_items bi
            JOIN boms b ON bi.bom_id = b.id
            WHERE b.team = %s AND b.active = 1 AND b.status = 'Approved'
              AND (
                  bi.orderable = 1
                  OR lower(bi.item) LIKE '%filament%'
                  OR lower(bi.specs) LIKE '%filament%'
                  OR lower(bi.item) LIKE '%urethane%'
                  OR lower(bi.specs) LIKE '%urethane%'
                  OR lower(bi.item) LIKE '%stock%'
                  OR lower(bi.specs) LIKE '%stock%'
              )
        """, (t_name,)).fetchone()
        
        total_spent = spent_row["total_spent"] if spent_row and spent_row["total_spent"] else 0.0
        
        pending_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM boms WHERE team = %s AND status = 'Pending' AND active = 1", 
            (t_name,)
        ).fetchone()["cnt"]
        
        approved_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM boms WHERE team = %s AND status = 'Approved' AND active = 1", 
            (t_name,)
        ).fetchone()["cnt"]
        
        team_data.append({
            "username": t_name,
            "password": user_passwords.get(t_name, "Registered via BOM upload"),
            "total_spent": total_spent,
            "has_pending": pending_count > 0,
            "pending_count": pending_count,
            "approved_count": approved_count
        })
        
    conn.close()
    return render_template("teams.html", team_data=team_data)

@app.route("/vendors")
@admin_required
def vendors_view():
    items = fetch_items(active_only=True, orderable_only=True, approved_only=True)
    items = apply_filters(items, request.args)
    by_vendor = defaultdict(list)
    for it in items:
        by_vendor[it["resolved_vendor"] or "Unspecified"].append(it)
    vendor_groups = {}
    for vendor, its in by_vendor.items():
        g = group_items(its)
        vendor_groups[vendor] = {
            "groups": g,
            "subtotal": sum(x["cost"] for x in g),
            "units": sum(x["qty"] for x in g),
        }
    vendor_groups = dict(sorted(vendor_groups.items(), key=lambda kv: -kv[1]["subtotal"]))
    options = filter_options(fetch_items(active_only=True, orderable_only=True, approved_only=True))
    grand_total = sum(v["subtotal"] for v in vendor_groups.values())
    return render_template("vendors.html", vendor_groups=vendor_groups, options=options,
                           args=request.args, grand_total=grand_total)

@app.route("/matches")
@admin_required
def matches_view():
    items = fetch_items(active_only=True, approved_only=True)
    clusters = []
    if items:
        clusters.append({
            "name": "All Available Master Items (Manual Selection)",
            "rows": items
        })

    conn = get_conn()
    active_matches = conn.execute(
        "SELECT * FROM manual_matches WHERE active = 1 ORDER BY id DESC"
    ).fetchall()
    conn.close()
    
    match_rows = []
    for m in active_matches:
        m_dict = dict(m)
        members = [i for i in items if i["group_override_id"] == m_dict["id"]]
        if members:
            match_rows.append({"match": m_dict, "rows": members})

    return render_template("matches.html", clusters=clusters, match_rows=match_rows)

@app.route("/matches/combine", methods=["POST"])
@admin_required
def matches_combine():
    ids = request.form.getlist("item_ids")
    if len(ids) < 2:
        flash("Select at least two rows to combine.", "error")
        return redirect(url_for("matches_view"))
    match_id = new_match_id()
    conn = get_conn()
    conn.execute(
        "INSERT INTO manual_matches (id, created_at, active) VALUES (%s, %s, 1)",
        (match_id, now_iso()),
    )
    for iid in ids:
        conn.execute(
            "UPDATE bom_items SET group_override_id = %s WHERE id = %s", (match_id, iid)
        )
    conn.commit()
    conn.close()
    flash("Items successfully combined via manual override.", "success")
    return redirect(url_for("matches_view"))

@app.route("/matches/undo/<match_id>", methods=["POST"])
@admin_required
def matches_undo(match_id):
    conn = get_conn()
    conn.execute("UPDATE bom_items SET group_override_id = NULL WHERE group_override_id = %s", (match_id,))
    conn.execute("UPDATE manual_matches SET active = 0 WHERE id = %s", (match_id,))
    conn.commit()
    conn.close()
    flash("Manual match undone.", "info")
    return redirect(url_for("matches_view"))

@app.route("/status")
@login_required
def status_view():
    team_arg = request.args.get("team", "").strip()
    
    if session.get("is_admin"):
        target_team = team_arg if team_arg else None
    else:
        target_team = session.get("username")

    items = fetch_items(active_only=True, orderable_only=False, team_filter=target_team, approved_only=not session.get("is_admin"))
    options = filter_options(fetch_items(active_only=True, orderable_only=False, approved_only=not session.get("is_admin")))
    
    def is_ignored_material(it):
        s = " ".join([str(it.get("item") or ""), str(it.get("specs") or ""), str(it.get("category") or ""), str(it.get("resolved_vendor") or "")]).lower()
        return ("filament" in s) or ("urethane" in s) or ("tpu" in s) or ("stock" in s)

    raw_items = [i for i in items if not is_ignored_material(i)]

    orderable_raw = [i for i in raw_items if i.get("orderable")]
    tracked_raw = [i for i in raw_items if not i.get("orderable") or "mk2 brushed" in (i.get("item") or "").lower()]

    combined_orderable_map = {}
    for it in orderable_raw:
        k = (it["team"], (it["item"] or "").strip().lower(), (it["specs"] or "").strip().lower(), (it["resolved_vendor"] or "").strip().lower())
        if k not in combined_orderable_map:
            new_it = dict(it)
            combined_orderable_map[k] = new_it
        else:
            combined_orderable_map[k]["qty"] = (combined_orderable_map[k]["qty"] or 0) + (it["qty"] or 0)
            existing_proj = combined_orderable_map[k].get("project", "")
            curr_proj = it.get("project", "")
            if curr_proj and curr_proj not in existing_proj:
                combined_orderable_map[k]["project"] = f"{existing_proj}, {curr_proj}"

    processed_orderable = list(combined_orderable_map.values())
    processed_tracked = [i for i in tracked_raw if not i.get("orderable")]

    items = processed_orderable + processed_tracked
    items = apply_filters(items, request.args)
    items.sort(key=lambda i: (i["resolved_vendor"] or "Unspecified", i["item"].lower()))

    counts = defaultdict(float)
    for i in items:
        counts[i["order_status"] or "Not Ordered"] += (i["qty"] or 0)

    conn = get_conn()
    if session.get("is_admin"):
        boms = conn.execute("SELECT team as username, filename, status, upload_date as updated_at FROM boms WHERE active = 1 ORDER BY upload_date DESC").fetchall()
    else:
        boms = conn.execute("SELECT team as username, filename, status, upload_date as updated_at FROM boms WHERE active = 1 AND team = %s ORDER BY upload_date DESC", (target_team,)).fetchall()
    statuses = [dict(b) for b in boms]
    conn.close()

    by_vendor = defaultdict(list)
    for it in items:
        by_vendor[it["resolved_vendor"] or "Unspecified"].append(it)

    return render_template("status.html", items=items, by_vendor=by_vendor, options=options,
                           args=request.args, counts=counts, statuses=statuses)

@app.route("/status/update/<int:item_id>", methods=["POST"])
@admin_required
def status_update(item_id):
    order_status = request.form.get("order_status", "Not Ordered")
    storage_location = request.form.get("storage_location", "")
    if order_status not in ORDER_STATUSES:
        order_status = "Not Ordered"
    if storage_location not in STORAGE_LOCATIONS:
        storage_location = ""
    conn = get_conn()
    conn.execute(
        "UPDATE bom_items SET order_status = %s, storage_location = %s WHERE id = %s",
        (order_status, storage_location, item_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("status_view", **request.args))

@app.route("/status/vendor_update", methods=["POST"])
@admin_required
def status_vendor_update():
    vendor = request.form.get("vendor")
    order_status = request.form.get("order_status", "Not Ordered")
    storage_location = request.form.get("storage_location", "")
    if vendor:
        update_vendor_order_status(vendor, order_status, storage_location)
        flash(f"Updated all items for vendor '{vendor}' to status: {order_status}.", "success")
    return redirect(url_for("status_view"))

@app.route("/status/bulk_update", methods=["POST"])
@admin_required
def status_bulk_update():
    row_ids = request.form.getlist("row_ids")
    order_status = request.form.get("order_status", "Not Ordered")
    storage_location = request.form.get("storage_location", "")
    if order_status not in ORDER_STATUSES:
        order_status = "Not Ordered"
    if storage_location not in STORAGE_LOCATIONS:
        storage_location = ""
    if row_ids:
        conn = get_conn()
        qmarks = ",".join(["%s"] * len(row_ids))
        conn.execute(
            f"UPDATE bom_items SET order_status = %s, storage_location = %s WHERE id IN ({qmarks})",
            (order_status, storage_location, *row_ids),
        )
        conn.commit()
        conn.close()
        flash("Status updated for all rows.", "success")
    next_url = request.form.get("next") or url_for("order_view")
    return redirect(next_url)

@app.route("/export/combined.csv")
@admin_required
def export_combined():
    items = apply_filters(fetch_items(active_only=True, orderable_only=True, approved_only=True), request.args)
    groups = group_items(items)
    return csv_response(groups_to_csv(groups), "combined_order.csv")

@app.route("/export/vendor/<path:vendor>.csv")
@admin_required
def export_vendor(vendor):
    items = fetch_items(active_only=True, orderable_only=True, approved_only=True)
    its = [i for i in items if (i["resolved_vendor"] or "Unspecified") == vendor]
    groups = group_items(its)
    safe = "".join(c if c.isalnum() else "_" for c in vendor)
    return csv_response(groups_to_csv(groups), f"order_{safe}.csv")

@app.route("/export/tracked.csv")
@admin_required
def export_tracked():
    items = fetch_items(active_only=True, orderable_only=False, approved_only=True)
    tracked = [i for i in items if i["orderable"] == 1]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Item", "Ordering Specs", "Category", "Qty", "Subsystem", "Team", "Project", "Comments"])
    for i in tracked:
        w.writerow([i["item"], i["specs"], i["category"], i["qty"], i["subsystem"],
                    i["team"], i["project"], i["comments"]])
    return csv_response(buf.getvalue(), "tracked_only.csv")

@app.route("/export/status.csv")
@admin_required
def export_status():
    items = apply_filters(fetch_items(active_only=True, orderable_only=True, approved_only=True), request.args)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Item", "Ordering Specs", "Vendor", "Qty", "Team", "Project",
                "Order Status", "Storage Location"])
    for i in items:
        w.writerow([i["item"], i["specs"], i["resolved_vendor"], i["qty"], i["team"],
                    i["project"], i["order_status"], i["storage_location"]])
    return csv_response(buf.getvalue(), "item_status.csv")

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
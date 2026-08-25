import os
import uuid
from datetime import datetime
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

ORDER_STATUSES = [
    "Not Ordered",
    "Ordered",
    "Shipped",
    "Partially Arrived",
    "Arrived",
    "Backordered",
    "Cancelled"
]

STORAGE_LOCATIONS = [
    "Bin A",
    "Bin B",
    "Shelf 1",
    "Shelf 2",
    "Cabinet",
    "Large Storage",
    "Other"
]

def get_conn():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    conn.cursor_factory = psycopg2.extras.DictCursor
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0
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
        CREATE TABLE IF NOT EXISTS manual_matches (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

def now_iso():
    return datetime.utcnow().isoformat()

def get_submission(sub_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM boms WHERE id = %s", (sub_id,))
    bom = cur.fetchone()
    if not bom:
        cur.close()
        conn.close()
        return None
    cur.execute("SELECT * FROM bom_items WHERE bom_id = %s ORDER BY row_index", (sub_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()
    return {
        "bom": dict(bom),
        "items": [dict(i) for i in items],
        "team": bom["team"],
        "project": bom["project"],
        "status": bom["status"],
        "id": bom["id"]
    }

def get_all_pending_submissions():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM boms WHERE status = 'Pending' AND active = 1 ORDER BY id DESC")
    boms = cur.fetchall()
    results = []
    for b in boms:
        cur.execute("SELECT * FROM bom_items WHERE bom_id = %s ORDER BY row_index", (b["id"],))
        items = cur.fetchall()
        results.append({
            "bom": dict(b),
            "items": [dict(i) for i in items],
            "team": b["team"],
            "project": b["project"],
            "status": b["status"],
            "id": b["id"]
        })
    cur.close()
    conn.close()
    return results

def update_submission_status(sub_id, status):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE boms SET status = %s WHERE id = %s", (status, sub_id))
    conn.commit()
    cur.close()
    conn.close()

def delete_bom_item(item_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM bom_items WHERE id = %s", (item_id,))
    conn.commit()
    cur.close()
    conn.close()

def new_match_id():
    return uuid.uuid4().hex[:12]

def update_vendor_order_status(vendor, status, location):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """UPDATE bom_items SET order_status = %s, storage_location = %s
           WHERE resolved_vendor = %s AND bom_id IN (SELECT id FROM boms WHERE status = 'Approved' AND active = 1)""",
        (status, location, vendor)
    )
    conn.commit()
    cur.close()
    conn.close()
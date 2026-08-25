import sqlite3
from datetime import datetime

DB_NAME = "database.db"

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
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS boms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT NOT NULL,
            project TEXT NOT NULL,
            notes TEXT,
            bom_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            upload_date TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'Pending'
        )
    """)
    
    # Check if bom_items table exists, create or migrate it safely
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bom_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bom_id INTEGER NOT NULL,
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
            needs_return INTEGER DEFAULT 0,
            FOREIGN KEY (bom_id) REFERENCES boms (id)
        )
    """)
    
    # Safely ensure column exists if upgrading database
    try:
        conn.execute("ALTER TABLE bom_items ADD COLUMN needs_return INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass # Column already exists

    conn.execute("""
        CREATE TABLE IF NOT EXISTS manual_matches (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()

def now_iso():
    return datetime.now().isoformat()

def get_submission(sub_id):
    conn = get_conn()
    bom = conn.execute("SELECT * FROM boms WHERE id = ?", (sub_id,)).fetchone()
    if not bom:
        conn.close()
        return None
    items = conn.execute("SELECT * FROM bom_items WHERE bom_id = ? ORDER BY row_index", (sub_id,)).fetchall()
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
    boms = conn.execute("SELECT * FROM boms WHERE status = 'Pending' AND active = 1 ORDER BY id DESC").fetchall()
    results = []
    for b in boms:
        items = conn.execute("SELECT * FROM bom_items WHERE bom_id = ? ORDER BY row_index", (b["id"],)).fetchall()
        results.append({
            "bom": dict(b),
            "items": [dict(i) for i in items],
            "team": b["team"],
            "project": b["project"],
            "status": b["status"],
            "id": b["id"]
        })
    conn.close()
    return results

def update_submission_status(sub_id, status):
    conn = get_conn()
    conn.execute("UPDATE boms SET status = ? WHERE id = ?", (status, sub_id))
    conn.commit()
    conn.close()

def delete_bom_item(item_id):
    conn = get_conn()
    conn.execute("DELETE FROM bom_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()

def new_match_id():
    import uuid
    return uuid.uuid4().hex[:12]

def update_vendor_order_status(vendor, status, location):
    conn = get_conn()
    conn.execute(
        """UPDATE bom_items SET order_status = ?, storage_location = ?
           WHERE resolved_vendor = ? AND bom_id IN (SELECT id FROM boms WHERE status = 'Approved' AND active = 1)""",
        (status, location, vendor)
    )
    conn.commit()
    conn.close()
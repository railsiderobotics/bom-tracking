from app import app
import db

items = [
    ("Repeat Battle Ready Servo", "40kg", 1, 20.0),
    ("Repeat Dominion Dual Brushed ESC", "", 1, 22.0),
    ("2S 300mah LiPo Battery", "45C", 1, 29.99),
    ("Repeat Mk2 Brushed", "4mm Shaft", 2, 20.0),
    ("Overture TPU", "Filament", 1, 20.0),
    ("AR 500 1/4\" Stock", "Stock", 1, 30.0),
    ("45A Urethane", "Urethane", 1, 5.0),
    ("#4 x 1/2\" Plastites", "Black Oxide", 1, 6.45),
]

conn = db.get_db()
conn.execute("INSERT INTO boms (team, project, notes, bom_type, filename, upload_date, active, status) VALUES (?,?,?,?,?,?,1,'Pending')",('BONK','Bonkramp','notes','bot','f.csv','2026-08-23'))
bid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
for idx, (item, specs, qty, unit) in enumerate(items, start=1):
    total = qty * unit
    # determine orderable: Mk2 Brushed and those with 'Filament','Stock','Urethane' are special
    specs_lower = (specs or '').lower()
    item_lower = item.lower()
    if any(k in specs_lower for k in ['filament','stock','urethane']) or any(k in item_lower for k in ['filament','stock','urethane']):
        orderable = 0
    elif item_lower.startswith('repeat mk2 brushed'):
        orderable = 0
    else:
        orderable = 1
    conn.execute("INSERT INTO bom_items (bom_id, row_index, category, qty, item, specs, link, vendor, other_vendor, resolved_vendor, event_category, other_category, unit_cost, total_cost, physical, subsystem, units_per_package, units_per_bot, comments, orderable, match_key, flagged, order_status, storage_location, is_given) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                 (bid, idx, '', qty, item, specs, '', 'Railside' if 'Rail' in item or 'Rail' in specs else 'Repeat Robotics', '', '', '', '', unit, total, '', '', None, None, '', orderable, '', '', 'Not Ordered', '',))
conn.commit()
conn.close()

c = app.test_client()
with c.session_transaction() as sess:
    sess['user_id'] = 0
    sess['username'] = 'admin'
    sess['is_admin'] = True

r = c.get(f'/team/bom/{bid}')
print('status', r.status_code)
html = r.data.decode()
print('\n--- HTML excerpt around metadata ---')
pos = html.find('Submission Metadata')
print(html[pos:pos+800])
import re
m = re.search(r'Est\.\s*Cost[:\s]*\$([0-9,.]+)', html)
print('found total', m.group(1) if m else 'not found')

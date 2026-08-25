from app import app
import db

# Insert pending submission
conn = db.get_db()
conn.execute("INSERT INTO boms (team, project, notes, bom_type, filename, upload_date, active, status) VALUES (?,?,?,?,?,?,1,'Pending')",('TEAMX','ProjectX','notes','bot','x.csv','2026-08-23'))
bid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
conn.execute("INSERT INTO bom_items (bom_id, row_index, item, qty, unit_cost, total_cost, orderable) VALUES (?,?,?,?,?,?,1)",(bid,1,'part',2,3.0,6.0))
conn.commit()
conn.close()

c = app.test_client()
with c.session_transaction() as sess:
    sess['user_id'] = 0
    sess['username'] = 'admin'
    sess['is_admin'] = True

r = c.get('/admin/approvals')
print('status', r.status_code)
print('length', len(r.data))
print(r.data.decode()[:400])

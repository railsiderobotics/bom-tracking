from app import app
import db

# create test submission
conn = db.get_db()
conn.execute("INSERT INTO boms (team, project, notes, bom_type, filename, upload_date, active, status) VALUES (?,?,?,?,?,?,1,'Pending')",('TEST','TP','notes','bot','f.csv','2026-08-23'))
bid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
conn.execute("INSERT INTO bom_items (bom_id, row_index, item, qty, unit_cost, total_cost, orderable) VALUES (?,?,?,?,?,?,1)",(bid,1,'x',1,5.0,5.0))
conn.commit()
conn.close()

c = app.test_client()
with c.session_transaction() as sess:
    sess['user_id'] = 0
    sess['username'] = 'admin'
    sess['is_admin'] = True

r = c.get(f'/team/bom/{bid}')
print('view', r.status_code)

r2 = c.post(f'/admin/approvals/{bid}/approve', follow_redirects=False)
print('approve', r2.status_code, r2.headers.get('Location'))

s = db.get_submission(bid)
print('status', s['status'])

# now unapprove
r3 = c.post(f'/admin/approvals/{bid}/unapprove', follow_redirects=False)
print('unapprove', r3.status_code, r3.headers.get('Location'))
s2 = db.get_submission(bid)
print('status after unapprove', s2['status'])

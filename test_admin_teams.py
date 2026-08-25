from app import app
c=app.test_client()
with c.session_transaction() as s:
    s['user_id']=0
    s['username']='admin'
    s['is_admin']=True
r=c.get('/admin/teams')
print('status', r.status_code)
html = r.data.decode()
import re
m = re.search(r'<td><strong>(?P<team>[^<]+)</strong></td>\s*<td>\s*<span class="password-field"[^>]*>[^<]*</span>\s*<button[^>]*>Show</button>\s*</td>\s*<td><strong>\$(?P<amt>[0-9.,]+)</strong></td>', html)
if m:
    print('team', m.group('team'), 'total_spent', m.group('amt'))
else:
    print('TEAM row not found in snippet')

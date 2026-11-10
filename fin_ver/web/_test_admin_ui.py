""" Full integration test: admin dashboard """
import httpx, os, sqlite3, hashlib, secrets

BASE = 'http://127.0.0.1:5099'

# Clean DB
db_path = os.path.join(os.path.dirname(__file__) or '.', 'data.db')
if os.path.exists(db_path):
    os.remove(db_path)

import database; database.init_db()

# Create admin + normal user
conn = sqlite3.connect(db_path)
for uid,uname,pw,is_admin in [('uid_admin','admin','admin',1),('uid_teacher','teacher','teacher',0)]:
    salt = secrets.token_hex(16)
    pwd_hash = f"{salt}:{hashlib.sha256(f'{salt}:{pw}'.encode()).hexdigest()}"
    conn.execute("INSERT INTO users (id, username, password_hash, display_name, is_admin) VALUES (?,?,?,?,?)",
                 (uid, uname, pwd_hash, uname, is_admin))
conn.commit()
conn.close()

def test(name, fn):
    try:
        ok = fn()
        status = 'OK' if ok else 'FAIL'
        print(f'  [{status}] {name}')
        return ok
    except Exception as e:
        print(f'  [FAIL] {name}: {e}')
        return False

# 1. admin page loads
r = httpx.get(f'{BASE}/admin')
assert r.status_code == 200
assert 'admin.html' not in r.text  # it renders as HTML

# Login both
r = httpx.post(f'{BASE}/api/auth/login', json={'username': 'admin', 'password': 'admin'})
admin_token = r.json()['token']
AH = {'Authorization': f'Bearer {admin_token}'}

r = httpx.post(f'{BASE}/api/auth/login', json={'username': 'teacher', 'password': 'teacher'})
teacher_token = r.json()['token']
TH = {'Authorization': f'Bearer {teacher_token}'}

# Create a session for the teacher
r = httpx.post(f'{BASE}/api/session', headers=TH, json={'level': 2, 'scenario_id': 's1', 'scenario_title': '课堂测试'})

results = []

# 2. admin stats
results.append(test('Stats', lambda: (
    (r := httpx.get(f'{BASE}/api/admin/stats', headers=AH)),
    r.status_code == 200 and r.json()['total_users'] == 2 and r.json()['total_sessions'] == 1
)))

# 3. teacher can't access
results.append(test('Teacher blocked', lambda: (
    (r := httpx.get(f'{BASE}/api/admin/stats', headers=TH)),
    r.status_code == 403
)))

# 4. admin sees users
results.append(test('Users list', lambda: (
    (r := httpx.get(f'{BASE}/api/admin/users', headers=AH)),
    r.status_code == 200 and r.json()['total'] == 2
)))

# 5. admin sees sessions
results.append(test('Sessions list', lambda: (
    (r := httpx.get(f'{BASE}/api/admin/sessions', headers=AH)),
    r.status_code == 200 and r.json()['total'] == 1
)))

# 6. admin user detail
results.append(test('User detail', lambda: (
    (r := httpx.get(f'{BASE}/api/admin/users/uid_teacher', headers=AH)),
    r.status_code == 200 and r.json()['username'] == 'teacher'
)))

# 7. admin CSS/JS loads
results.append(test('Admin CSS', lambda: (
    (r := httpx.get(f'{BASE}/css/admin.css')),
    r.status_code == 200 and '.stat-card' in r.text
)))
results.append(test('Admin JS', lambda: (
    (r := httpx.get(f'{BASE}/js/admin.js')),
    r.status_code == 200 and 'init' in r.text
)))

print(f'\n{"="*40}')
print(f'Passed: {sum(results)}/{len(results)}')
if not all(results):
    print('FAILURES FOUND')
    exit(1)

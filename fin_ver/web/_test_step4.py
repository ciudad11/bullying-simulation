""" Step 4 admin API integration test """
import httpx, os

BASE = 'http://127.0.0.1:5099'

# Delete old DB
db_path = os.path.join(os.path.dirname(__file__) or '.', 'data.db')
if os.path.exists(db_path):
    os.remove(db_path)

import database; database.init_db()

# Create admin user directly in DB
import sqlite3
conn = sqlite3.connect(db_path)
import hashlib, secrets
salt = secrets.token_hex(16)
pwd_hash = f"{salt}:{hashlib.sha256(f'{salt}:admin'.encode()).hexdigest()}"
conn.execute("INSERT INTO users (id, username, password_hash, display_name, is_admin) VALUES (?,?,?,?,?)",
             ('u_admin', 'admin', pwd_hash, '管理员', 1))
conn.commit()
conn.close()

# Login as admin
r = httpx.post(f'{BASE}/api/auth/login', json={'username': 'admin', 'password': 'admin'})
token = r.json()['token']
print(f"Admin login: {r.status_code} username={r.json()['user']['username']} is_admin={r.json()['user']['is_admin']}")
assert r.json()['user']['is_admin'] == True

H = {'Authorization': f'Bearer {token}'}

# Test stats
def t(name, fn):
    try:
        fn()
        print(f'  [OK] {name}')
    except Exception as e:
        print(f'  [FAIL] {name}: {e}')

# 1. admin stats
t('Stats', lambda: (
    (r := httpx.get(f'{BASE}/api/admin/stats', headers=H)),
    print(f'    total_users={r.json().get("total_users")} sessions={r.json().get("total_sessions")}'),
    r.status_code == 200 and 'total_users' in r.json()
))

# 2. admin users
t('Users', lambda: (
    (r := httpx.get(f'{BASE}/api/admin/users', headers=H)),
    print(f'    count={r.json().get("total")}'),
    r.status_code == 200 and r.json()['total'] >= 1
))

# 3. admin sessions
t('Sessions', lambda: (
    (r := httpx.get(f'{BASE}/api/admin/sessions', headers=H)),
    print(f'    total={r.json().get("total")}'),
    r.status_code == 200
))

# 4. user detail
t('User detail', lambda: (
    (r := httpx.get(f'{BASE}/api/admin/users/u_admin', headers=H)),
    print(f'    username={r.json().get("username")}'),
    r.status_code == 200 and r.json()['username'] == 'admin'
))

# 5. non-admin rejected
r2 = httpx.post(f'{BASE}/api/auth/register', json={'username': 'normal', 'password': '123456', 'display_name': '普通用户'})
normal_token = r2.json()['token']
NH = {'Authorization': f'Bearer {normal_token}'}
t('Non-admin blocked', lambda: (
    (r := httpx.get(f'{BASE}/api/admin/stats', headers=NH)),
    print(f'    {r.status_code} {r.json().get("detail","?")[:30]}'),
    r.status_code == 403
))

# 6. no-auth blocked
t('No-auth 401', lambda: (
    (r := httpx.get(f'{BASE}/api/admin/stats')),
    r.status_code == 401
))

# 7. user not found
t('404 user', lambda: (
    (r := httpx.get(f'{BASE}/api/admin/users/nonexist', headers=H)),
    r.status_code == 404
))

print('\n=== Step 4 done ===')

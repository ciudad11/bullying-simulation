from fastapi.testclient import TestClient
import os

db_path = os.path.join(os.path.dirname(__file__) or '.', 'data.db')
if os.path.exists(db_path):
    os.remove(db_path)

from database import init_db; init_db()
from app import app

client = TestClient(app)

# 1. register
r = client.post('/api/auth/register', json={'username': 'lihua', 'password': '888888', 'display_name': '李华'})
assert r.status_code == 200, f"register failed: {r.json()}"
uid = r.json()['user']['id']
token = r.json()['token']
print(f'1.register OK: {uid}')

# 2. login
r2 = client.post('/api/auth/login', json={'username': 'lihua', 'password': '888888'})
assert r2.status_code == 200, f"login failed: {r2.json()}"
token2 = r2.json()['token']
print(f'2.login OK: {r2.json()["user"]["username"]}')

# 3. /me
r3 = client.get('/api/auth/me', headers={'Authorization': f'Bearer {token2}'})
assert r3.status_code == 200, f"me failed: {r3.json()}"
print(f'3./me OK: {r3.json()["user"]["username"]}')

# 4. 未登录拦截 - 应该返回 401
r4 = client.get('/api/sessions')
assert r4.status_code == 401, f"auth block failed: {r4.status_code}"
print(f'4.auth block OK: {r4.json()["detail"]}')

# 5. create session
r5 = client.post('/api/session', headers={'Authorization': f'Bearer {token2}'},
                 json={'level': 2, 'scenario_id': 'scenario_1', 'use_llm': False})
assert r5.status_code == 200, f"create session failed: {r5.status_code} {r5.text}"
assert 'user_id' in r5.json()
print(f'5.session OK: user_id={r5.json()["user_id"]}')

# 6. sessions list
r6 = client.get('/api/sessions', headers={'Authorization': f'Bearer {token2}'})
assert r6.status_code == 200
count = len(r6.json().get('sessions', []))
assert count >= 1, f"Expected >=1 sessions, got {count}"
print(f'6.sessions OK: {count} session(s)')

# 7. 错误密码
r7 = client.post('/api/auth/login', json={'username': 'lihua', 'password': 'wrong'})
assert r7.status_code == 401
print(f'7.wrong password blocked OK')

print()
print('=== Step 2 全部通过 ===')

import httpx, os, time, uuid

base = 'http://127.0.0.1:5099'
unique = uuid.uuid4().hex[:6]

# 1. register
r = httpx.post(f'{base}/api/auth/register', json={'username': f'user_{unique}', 'password': '888888', 'display_name': '测试老师'})
print(f'1.register: {r.status_code} {r.text[:80]}')
assert r.status_code == 200, f"FAIL: {r.text}"
token = r.json()['token']

# 2. login
r2 = httpx.post(f'{base}/api/auth/login', json={'username': f'user_{unique}', 'password': '888888'})
print(f'2.login: {r2.status_code} user={r2.json()["user"]["username"]}')
assert r2.status_code == 200
token2 = r2.json()['token']

# 3. /me
r3 = httpx.get(f'{base}/api/auth/me', headers={'Authorization': f'Bearer {token2}'})
print(f'3./me: {r3.status_code} user={r3.json()["user"]["username"]}')
assert r3.status_code == 200

# 4. no auth
r4 = httpx.get(f'{base}/api/sessions')
print(f'4.no-auth: {r4.status_code} {r4.text[:80]}')
assert r4.status_code == 401

# 5. create session
r5 = httpx.post(f'{base}/api/session', headers={'Authorization': f'Bearer {token2}'}, json={'level': 2, 'scenario_id': 'scenario_1', 'use_llm': False})
print(f'5.session: {r5.status_code} has_user_id={"user_id" in r5.json()}')
assert r5.status_code == 200

# 6. sessions list
r6 = httpx.get(f'{base}/api/sessions', headers={'Authorization': f'Bearer {token2}'})
print(f'6.list: {r6.status_code} count={len(r6.json().get("sessions",[]))}')
assert r6.status_code == 200

# 7. wrong password
r7 = httpx.post(f'{base}/api/auth/login', json={'username': f'user_{unique}', 'password': 'wrong'})
print(f'7.wrong_pwd: {r7.status_code}')
assert r7.status_code == 401

print('=== Step 2 ALL PASS ===')

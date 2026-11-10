import httpx
BASE = 'http://127.0.0.1:5099'

# Login admin
r = httpx.post(f'{BASE}/api/auth/login', json={'username': 'admin', 'password': 'admin'})
print(f"Login: {r.status_code}")
if r.status_code != 200:
    print(r.text)
    exit()
token = r.json()['token']

# Test stats
r2 = httpx.get(f'{BASE}/api/admin/stats', headers={'Authorization': f'Bearer {token}'})
print(f"Stats: {r2.status_code}")
print(f"Headers: {r2.headers.get('content-type')}")
print(f"Body ({len(r2.text)} chars): {r2.text[:500]}")

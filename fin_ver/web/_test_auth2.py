from fastapi.testclient import TestClient
import os

db_path = os.path.join(os.path.dirname(__file__) or '.', 'data.db')
if os.path.exists(db_path):
    os.remove(db_path)

from database import init_db; init_db()
from app import app

client = TestClient(app)

# Register + login  
r = client.post('/api/auth/register', json={'username': 'debug', 'password': '123456'})
token = r.json()['token']
print(f'reg ok, token={token[:12]}...')

# Try /me - manually constructing headers
headers = {'Authorization': f'Bearer {token}'}
print(f'testing /me with headers: {headers}')
try:
    r3 = client.get('/api/auth/me', headers=headers)
    print(f'me: {r3.status_code} {r3.text[:200]}')
except Exception as e:
    print(f'FAIL: {e}')

import httpx

base = 'http://127.0.0.1:5099'

# 4. no auth - let's see what comes back
r4 = httpx.get(f'{base}/api/sessions')
print(f'status: {r4.status_code}')
print(f'headers: {dict(r4.headers)}')
print(f'body: {repr(r4.text[:500])}')

""" Step 3 integration test - tests auth flow end-to-end """
import httpx, subprocess, time, os, sys

BASE = 'http://127.0.0.1:5099'
TEST_USER = 'test_teacher'
TEST_PASS = '888888'

def test(name, fn):
    try:
        fn()
        print(f'  [OK] {name}')
        return True
    except Exception as e:
        print(f'  [FAIL] {name}: {e}')
        return False

results = []

# 1. register
def t1():
    r = httpx.post(f'{BASE}/api/auth/register', json={
        'username': TEST_USER, 'password': TEST_PASS, 'display_name': '测试老师'
    })
    assert r.status_code == 200, f'{r.status_code}: {r.text}'
    assert 'token' in r.json()
results.append(test('注册', t1))

# 2. duplicate register
def t2():
    r = httpx.post(f'{BASE}/api/auth/register', json={
        'username': TEST_USER, 'password': 'whatever', 'display_name': 'dup'
    })
    assert r.status_code == 400 or r.status_code == 409, f'{r.status_code}: {r.text}'
results.append(test('重复注册拒绝', t2))

# 3. login
def t3():
    r = httpx.post(f'{BASE}/api/auth/login', json={'username': TEST_USER, 'password': TEST_PASS})
    assert r.status_code == 200, f'{r.status_code}: {r.text}'
    assert 'token' in r.json()
    global TOKEN; TOKEN = r.json()['token']
results.append(test('登录', t3))
TOKEN = None

# refresh token
r = httpx.post(f'{BASE}/api/auth/login', json={'username': TEST_USER, 'password': TEST_PASS})
TOKEN = r.json()['token']

# 4. me
def t4():
    r = httpx.get(f'{BASE}/api/auth/me', headers={'Authorization': f'Bearer {TOKEN}'})
    assert r.status_code == 200, f'{r.status_code}: {r.text}'
    assert r.json()['user']['username'] == TEST_USER
results.append(test('获取当前用户', t4))

# 5. protected without auth
def t5():
    r = httpx.get(f'{BASE}/api/sessions')
    assert r.status_code == 401, f'{r.status_code}: {r.text}'
results.append(test('未登录拦截', t5))

# 6. create session with auth
def t6():
    r = httpx.post(f'{BASE}/api/session', headers={'Authorization': f'Bearer {TOKEN}'},
                   json={'level': 2, 'scenario_id': 's1', 'use_llm': False})
    assert r.status_code == 200, f'{r.status_code}: {r.text}'
    assert 'user_id' in r.json()
results.append(test('创建会话绑定用户', t6))

# 7. wrong password
def t7():
    r = httpx.post(f'{BASE}/api/auth/login', json={'username': TEST_USER, 'password': 'wrong'})
    assert r.status_code == 401
results.append(test('错误密码拒绝', t7))

# 8. frontend HTML loads
def t8():
    r = httpx.get(f'{BASE}/')
    assert r.status_code == 200
    html = r.text
    assert 'auth-overlay' in html, 'missing auth-overlay in HTML'
    assert 'auth.css' in html, 'missing auth.css in HTML'
    assert 'app.js' in html, 'missing app.js in HTML'
    assert 'btn-logout' in html, 'missing btn-logout in HTML'
    assert 'user-display' in html, 'missing user-display in HTML'
results.append(test('前端页面加载', t8))

# 9. auth.css serves
def t9():
    r = httpx.get(f'{BASE}/css/auth.css')
    assert r.status_code == 200
    assert '.auth-overlay' in r.text
results.append(test('认证CSS加载', t9))

print(f'\n{"="*40}')
print(f'Passed: {sum(results)}/{len(results)}')
if all(results):
    print('🎉 Step 3 全部通过!')
else:
    print('⚠️ 有测试失败')
    sys.exit(1)

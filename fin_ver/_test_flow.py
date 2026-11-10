import urllib.request, json, time

BASE = 'http://localhost:5000'

def api(method, url, data=None, token=None):
    req = urllib.request.Request(url, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    if data:
        req.data = json.dumps(data, ensure_ascii=False).encode('utf-8')
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {'error': e.code, 'body': e.read().decode('utf-8')}

# 1. Get participants
r = api('GET', f'{BASE}/api/participants')
print(f"1. Participants ({len(r['participants'])}):")
for p in r['participants']:
    print(f"   {p['id']}: {p['name']} (role={p['role']})")

# 2. Create custom participant
r = api('POST', f'{BASE}/api/participants', {
    'name': '测试新人',
    'role': 'bully',
    'roleName': '霸凌者',
    'personality': {'background': '新来的转校生', 'traits': ['攻击', '暴躁'], 'speakingStyle': ['简短', '直接']}
})
custom_id = r['participant']['id']
print(f"\n2. Created custom: {custom_id}")

# 3. Start session
r = api('POST', f'{BASE}/api/session', {
    'level': 2,
    'use_llm': False,
    'participant_ids': [custom_id, 'bully', 'victim', 'bystander_a', 'bystander_b'],
    'participants_data': [{
        'id': custom_id,
        'name': '测试新人',
        'role': 'bully',
        'roleName': '霸凌者',
        'personality': {'background': '新来的转校生', 'traits': ['攻击', '暴躁'], 'speakingStyle': ['简短', '直接']}
    }]
})
sid = r['session_id']
print(f"\n3. Session: {sid}")

# 4. Run rounds
for i in range(6):
    r = api('GET', f'{BASE}/api/session/{sid}')
    msgs = r.get('messages', [])
    print(f"\n4.{i} Round={r.get('round',0)}, Messages={len(msgs)}")
    for m in msgs[-4:]:
        agent_name = m.get('agent','?')
        msg_name = m.get('name','?')
        text = (m.get('text','') or '')[:80]
        print(f"   [{agent_name}] {msg_name}: {text}")
    time.sleep(0.5)

print("\n5. Done")

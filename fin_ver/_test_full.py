import urllib.request, json, sys

BASE = "http://localhost:5000"
BASE_NR = "http://localhost:5001"

def api(method, url, data=None, token=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data:
        req.data = json.dumps(data).encode()
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"error": e.code, "body": body}

def test(label, ok):
    status = "OK" if ok else "FAIL"
    print(f"  {status}: {label}")

print("=== fin_ver (5000) ===")
r = api("GET", f"{BASE}/api/interventions")
test(f"GET /interventions ({len(r.get('interventions',[]))} items)", len(r.get('interventions',[])) >= 20)

r = api("GET", f"{BASE}/api/scenarios")
test(f"GET /scenarios ({len(r.get('scenarios',[]))} items)", len(r.get('scenarios',[])) >= 8)

r = api("GET", f"{BASE}/api/participants")
test(f"GET /participants ({len(r.get('participants',[]))} items)", len(r.get('participants',[])) >= 4)

# Register
import random
uname = f"final_{random.randint(100,999)}"
r = api("POST", f"{BASE}/api/auth/register", {"username": uname, "password": "pass", "display_name": "Final"})
test(f"Register '{uname}'", "token" in r)
token = r.get("token", "")

r = api("GET", f"{BASE}/api/auth/me", token=token)
test(f"Auth /me", r.get("user", {}).get("username") == uname)

# Session
r = api("POST", f"{BASE}/api/session", {"level": 2, "max_rounds": 3, "use_llm": False, "participant_ids": ["bully","victim","bystander_a","bystander_b"], "participants_data": []}, token=token)
sid = r.get("session_id", "")
test(f"Create session ({sid})", sid != "")

# Report
import time; time.sleep(0.5)
r = api("GET", f"{BASE}/api/session/{sid}/report", token=token)
test(f"Report ({r.get('messages', [])} msgs)", "messages" in r)

# Custom CRUD
r = api("POST", f"{BASE}/api/interventions", {"title": "QATest", "content": "qa"}, token=token)
iid = r.get("intervention", {}).get("id", "")
test(f"Create intervention ({iid})", iid != "")

r = api("PUT", f"{BASE}/api/interventions/{iid}", {"title": "QATest-Updated", "content": "qa2"}, token=token)
test(f"Update intervention", r.get("intervention", {}).get("title") == "QATest-Updated")

r = api("DELETE", f"{BASE}/api/interventions/{iid}", token=token)
test(f"Delete intervention", "error" not in r)

# Scenarios CRUD
r = api("POST", f"{BASE}/api/scenarios", {"title": "QAScene", "description": "qa", "subject": "math", "location": "class"}, token=token)
sid2 = r.get("scenario", {}).get("id", "")
test(f"Create scenario ({sid2})", sid2 != "")
r = api("DELETE", f"{BASE}/api/scenarios/{sid2}", token=token)
test(f"Delete scenario", "error" not in r)

# Participants CRUD
r = api("POST", f"{BASE}/api/participants", {"name": "QAGuy", "role_type": "bully", "background": "bg", "traits": "bold", "speaking_style": "loud"}, token=token)
pid = r.get("participant", {}).get("id", "")
test(f"Create participant ({pid})", pid != "")
r = api("DELETE", f"{BASE}/api/participants/{pid}", token=token)
test(f"Delete participant", "error" not in r)

print()
print("=== noreset (5001) ===")
r = api("GET", f"{BASE_NR}/api/interventions")
test(f"GET /interventions ({len(r.get('interventions',[]))} items)", len(r.get('interventions',[])) >= 20)

r = api("GET", f"{BASE_NR}/api/participants")
test(f"GET /participants ({len(r.get('participants',[]))} items)", len(r.get('participants',[])) >= 4)

# Login on noreset
r = api("POST", f"{BASE_NR}/api/auth/login", {"username": uname, "password": "pass"})
test(f"Login '{uname}' on noreset", "token" in r)

# Admin
r = api("POST", f"{BASE_NR}/api/auth/login", {"username": "admintest", "password": "admin123"})
token_a = r.get("token", "")
r = api("GET", f"{BASE_NR}/api/admin/stats", token=token_a)
test(f"Admin stats", "total_users" in r)

print()
print("Done.")

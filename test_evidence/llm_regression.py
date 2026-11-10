# -*- coding: utf-8 -*-
"""LLM 修复后回归：终止规则 15 例 + LLM-off 在线主流程快速路径（5 轮自动结束）。"""
import json
import os
import sqlite3
import subprocess
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\bully_simulation_pack_v2_20260713"
EV = ROOT + r"\test_evidence"
BASE = "http://127.0.0.1:5000"
DB = ROOT + r"\fin_ver\web\data.db"
USER = "qa_llmoff_1109a"
PWD = "QaTest2026!"
out = {"rounds": []}

# 1) 离线终止规则 15 例
env = dict(os.environ, PYTHONIOENCODING="utf-8")
log_path = os.path.join(EV, "llm_test_termination.log")
with open(log_path, "w", encoding="utf-8") as f:
    p = subprocess.run([sys.executable, "tests/test_termination.py"],
                       cwd=os.path.join(ROOT, "fin_ver"), env=env,
                       stdout=f, stderr=subprocess.STDOUT)
out["termination_exit"] = p.returncode
lines = open(log_path, encoding="utf-8").read().splitlines()
out["termination_summary"] = lines[-1] if lines else ""
out["termination_pass"] = ("15 通过 / 0 失败" in out["termination_summary"])

# 2) LLM-off 在线主流程
r = requests.post(f"{BASE}/api/auth/register",
                  json={"username": USER, "password": PWD, "display_name": "LLM-off 回归"}, timeout=30)
if r.status_code == 200:
    token = r.json()["token"]
else:
    r = requests.post(f"{BASE}/api/auth/login", json={"username": USER, "password": PWD}, timeout=30)
    r.raise_for_status()
    token = r.json()["token"]
H = {"Authorization": f"Bearer {token}"}
out["account"] = USER

r = requests.get(f"{BASE}/api/scenarios", headers=H, timeout=30)
scens = r.json().get("scenarios", [])
scen = next((s for s in scens if s.get("id") == "scenario_1"), scens[0])

r = requests.post(f"{BASE}/api/session", headers=H,
                  json={"level": 1, "scenario_id": scen.get("id"), "use_llm": False}, timeout=30)
r.raise_for_status()
sess = r.json()
SID, DB_SID = sess["session_id"], sess["db_session_id"]
out["session"] = {"session_id": SID, "db_session_id": DB_SID,
                  "use_llm": False, "level": 1,
                  "llm_available": sess.get("llm_available")}

for i in range(1, 7):
    r = requests.post(f"{BASE}/api/session/{SID}/run", headers=H, timeout=180)
    if r.status_code != 200:
        out["run_error"] = {"round": i, "code": r.status_code, "body": r.text[:300]}
        break
    d = r.json()
    st = requests.get(f"{BASE}/api/session/{SID}", headers=H, timeout=30).json()["status"]
    rec = {"round": i, "code": r.status_code, "done": d.get("done"),
           "remaining": st.get("remaining_to_stop"),
           "streak": st.get("bullying_stopped_rounds"),
           "finished": st.get("finished"), "result_type": st.get("result_type")}
    out["rounds"].append(rec)
    if d.get("done"):
        out["end_round"] = i
        out["end_result_type"] = d.get("result_type")
        out["end_reason"] = d.get("auto_end_reason")
        break

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
row = conn.execute("select id,status,result_type,auto_end_reason,completed_at "
                   "from sessions where id=?", (DB_SID,)).fetchone()
conn.close()
out["db_session"] = dict(row) if row else None
out["checks"] = {
    "termination_15_pass": bool(out["termination_pass"]),
    "llm_off_runs_no_error": all(x["code"] == 200 for x in out["rounds"]),
    "auto_end_round_5": out.get("end_round") == 5,
    "result_type_success": out.get("end_result_type") == "success",
    "remaining_sequence_5_to_0": [4, 3, 2, 1, 0] == [x["remaining"] for x in out["rounds"]],
    "db_completed_success": bool(row) and row["status"] == "completed" and row["result_type"] == "success",
}
with open(os.path.join(EV, "llm_regression.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))

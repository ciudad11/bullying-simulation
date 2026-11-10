# -*- coding: utf-8 -*-
"""验收脚本：只读校验已完成会话的状态接口、报告接口与 DB 落库结果。"""
import json
import sqlite3
import sys
import requests

BASE = "http://127.0.0.1:5000"
USER = "qa_autoend_1109a"
PWD = "QaTest2026!"
SESSION_ID = sys.argv[1] if len(sys.argv) > 1 else "6882d1a1"

out = {}

r = requests.post(f"{BASE}/api/auth/login", json={"username": USER, "password": PWD}, timeout=30)
r.raise_for_status()
token = r.json()["token"]
headers = {"Authorization": f"Bearer {token}"}

r = requests.get(f"{BASE}/api/session/{SESSION_ID}", headers=headers, timeout=30)
out["status_code"] = r.status_code
st = r.json().get("status", {})
keys = [
    "round", "finished", "termination_reason", "result_type",
    "attack_frequency", "attack_severity", "persistence", "victim_safety",
    "follow_up_status", "retaliation_risk", "bullying_stopped_rounds",
    "bullying_stopped_required", "attack_frequency_max",
    "bullying_intensity_max", "remaining_to_stop", "bullying_intensity",
]
out["status_subset"] = {k: st.get(k) for k in keys}
out["status_has_all_diag_fields"] = all(k in st for k in keys)

r = requests.get(f"{BASE}/api/session/{SESSION_ID}/report", headers=headers, timeout=60)
out["report_code"] = r.status_code
try:
    rep = r.json()
    out["report_keys"] = sorted(rep.keys())[:25]
    out["report_result_type"] = rep.get("result_type")
    out["report_auto_end_reason"] = rep.get("auto_end_reason")
    out["report_summary_excerpt"] = str(rep.get("summary", rep.get("report", "")))[:200]
except Exception as e:
    out["report_parse_error"] = str(e)

conn = sqlite3.connect(r"D:\bully_simulation_pack_v2_20260713\fin_ver\web\data.db")
conn.row_factory = sqlite3.Row
row = conn.execute(
    "select id,user_id,level,status,result_type,auto_end_reason,created_at,completed_at "
    "from sessions where user_id=(select id from users where username=?) order by created_at desc limit 1",
    (USER,),
).fetchone()
out["db_row"] = dict(row) if row else None
conn.close()

with open(r"D:\bully_simulation_pack_v2_20260713\test_evidence\api_verify.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))

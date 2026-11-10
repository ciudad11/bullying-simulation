# -*- coding: utf-8 -*-
"""noreset(5001) 实机冒烟：建会话、跑轮次、核对诊断字段与自动结束、报告与 DB。"""
import json
import sqlite3
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8")
B = "http://127.0.0.1:5001"
USER = "qa_noreset_1109"
PWD = "QaNoReset2026!"
out = {}

# 注册（若已存在则登录）
r = requests.post(B + "/api/auth/register", json={"username": USER, "password": PWD, "display_name": "QA noreset冒烟"}, timeout=30)
if r.status_code != 200:
    r = requests.post(B + "/api/auth/login", json={"username": USER, "password": PWD}, timeout=30)
r.raise_for_status()
tok = r.json()["token"]
h = {"Authorization": "Bearer " + tok}

r = requests.post(B + "/api/session", json={"level": 1, "use_llm": False}, headers=h, timeout=60)
r.raise_for_status()
created = r.json()
sid = created["session_id"]
out["create"] = {"session_id": sid, "db_session_id": created.get("db_session_id"),
                 "status_keys": sorted(created.get("status", {}).keys())}
out["create_diag_fields_present"] = all(k in created["status"] for k in (
    "attack_frequency", "attack_severity", "persistence", "victim_safety",
    "follow_up_status", "retaliation_risk", "bullying_stopped_rounds",
    "bullying_stopped_required", "attack_frequency_max", "bullying_intensity_max",
    "remaining_to_stop"))

rounds = []
for i in range(1, 7):
    r = requests.post(f"{B}/api/session/{sid}/run", headers=h, timeout=180)
    body = r.json()
    st = body.get("status", {})
    rounds.append({
        "round": i, "http": r.status_code, "done": body.get("done"),
        "auto_ended": body.get("auto_ended"), "result_type": body.get("result_type"),
        "reason": body.get("auto_end_reason"),
        "B": st.get("bullying_intensity"), "F": st.get("attack_frequency"),
        "streak": st.get("bullying_stopped_rounds"), "remaining": st.get("remaining_to_stop"),
        "msgs": len(body.get("messages", []) or []),
    })
    if body.get("done"):
        break
out["rounds"] = rounds

st = requests.get(f"{B}/api/session/{sid}", headers=h, timeout=30).json()["status"]
out["final_status"] = {k: st.get(k) for k in (
    "round", "finished", "result_type", "termination_reason", "attack_frequency",
    "attack_frequency_max", "bullying_stopped_rounds", "bullying_stopped_required",
    "remaining_to_stop", "bullying_intensity")}

r = requests.get(f"{B}/api/session/{sid}/report", headers=h, timeout=120)
out["report_code"] = r.status_code
rep = r.json()
out["report_metrics"] = rep.get("metrics", {})
out["report_total_rounds"] = rep.get("total_rounds")

conn = sqlite3.connect(r"D:\bully_simulation_pack_v2_20260713\noreset\web\data.db")
conn.row_factory = sqlite3.Row
row = conn.execute("select id,status,result_type,auto_end_reason,completed_at from sessions where id=?",
                   (created.get("db_session_id"),)).fetchone()
out["db_row"] = dict(row) if row else None
conn.close()

with open(r"D:\bully_simulation_pack_v2_20260713\test_evidence\noreset_5001_smoke.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))

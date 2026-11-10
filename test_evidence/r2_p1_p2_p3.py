# -*- coding: utf-8 -*-
"""r2 独立复验：P1 报告快照 / P2 调参校验 / P3 剩余轮数（API + SQLite）。"""
import json
import sqlite3
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:5000"
DB = r"D:\bully_simulation_pack_v2_20260713\fin_ver\web\data.db"
EV = r"D:\bully_simulation_pack_v2_20260713\test_evidence"
USER = "qa_r2fix_1109a"
PWD = "QaTest2026!"
out = {"p3_rounds": [], "p2_invalid": []}


def p(*a):
    print(*a)


# ---------- 注册/登录 ----------
r = requests.post(f"{BASE}/api/auth/register",
                  json={"username": USER, "password": PWD, "display_name": "r2 复验"},
                  timeout=30)
if r.status_code == 200:
    token = r.json()["token"]
    out["account"] = {"username": USER, "created": True}
else:
    r = requests.post(f"{BASE}/api/auth/login", json={"username": USER, "password": PWD}, timeout=30)
    r.raise_for_status()
    token = r.json()["token"]
    out["account"] = {"username": USER, "created": False}
H = {"Authorization": f"Bearer {token}"}
p("[account]", out["account"])

r = requests.get(f"{BASE}/api/scenarios", headers=H, timeout=30)
r.raise_for_status()
scens = r.json().get("scenarios", [])
scen = next((s for s in scens if s.get("id") == "scenario_1"), scens[0])
out["scenario"] = {"id": scen.get("id"), "title": scen.get("title")}

# ---------- P1 + P3：真实跑一局到自动结束 ----------
r = requests.post(f"{BASE}/api/session", headers=H, json={
    "level": 1, "scenario_id": scen.get("id"), "use_llm": False}, timeout=30)
r.raise_for_status()
sess = r.json()
SID = sess["session_id"]
DB_SID = sess["db_session_id"]
out["p1_session"] = {"session_id": SID, "db_session_id": DB_SID, "level": 1, "use_llm": False}

st = requests.get(f"{BASE}/api/session/{SID}", headers=H, timeout=30).json()["status"]
out["p3_rounds"].append({"stage": "created", "round": st.get("round"),
                         "remaining_to_stop": st.get("remaining_to_stop"),
                         "streak": st.get("bullying_stopped_rounds"),
                         "finished": st.get("finished")})

for i in range(1, 9):
    r = requests.post(f"{BASE}/api/session/{SID}/run", headers=H, timeout=180)
    if r.status_code != 200:
        out["p1_run_error"] = {"round": i, "code": r.status_code, "body": r.text[:500]}
        break
    d = r.json()
    st = requests.get(f"{BASE}/api/session/{SID}", headers=H, timeout=30).json()["status"]
    rec = {"stage": f"after_round_{i}", "run_status": r.status_code,
           "run_done": d.get("done"), "run_round": d.get("round"),
           "status_round": st.get("round"),
           "remaining_to_stop": st.get("remaining_to_stop"),
           "run_metrics_remaining": (d.get("metrics") or {}).get("remaining_to_stop"),
           "streak": st.get("bullying_stopped_rounds"),
           "attack_frequency": st.get("attack_frequency"),
           "attack_frequency_max": st.get("attack_frequency_max"),
           "finished": st.get("finished"),
           "result_type": st.get("result_type"),
           "termination_reason": st.get("termination_reason")}
    out["p3_rounds"].append(rec)
    p("[round]", i, "remaining=", rec["remaining_to_stop"], "streak=", rec["streak"],
      "done=", rec["run_done"], "type=", rec["result_type"])
    if d.get("done"):
        out["end_round"] = i
        out["end_reason"] = d.get("auto_end_reason")
        out["end_result_type"] = d.get("result_type")
        break

# ---------- P1：SQLite reports 快照 ----------
def report_rows(db_sid):
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(x) for x in conn.execute(
        "select id,session_id,user_id,report_json,created_at from reports where session_id=? order by created_at",
        (db_sid,)).fetchall()]
    sess_row = conn.execute(
        "select id,status,result_type,auto_end_reason,completed_at from sessions where id=?",
        (db_sid,)).fetchone()
    conn.close()
    return rows, (dict(sess_row) if sess_row else None)

rows, sess_row = report_rows(DB_SID)
out["p1_db_after_autostop"] = {"session_row": sess_row, "report_row_count": len(rows)}
if rows:
    snap = json.loads(rows[0]["report_json"])
    m = snap.get("metrics", {})
    rounds = sorted({msg.get("round") for msg in snap.get("messages", [])
                     if msg.get("round") is not None})
    out["p1_snapshot"] = {
        "report_id": rows[0]["id"],
        "metrics_round": m.get("round"),
        "finished": m.get("finished"),
        "result_type": m.get("result_type"),
        "termination_reason": m.get("termination_reason"),
        "message_rounds": rounds,
        "message_count": len(snap.get("messages", [])),
        "total_rounds": snap.get("total_rounds"),
    }
    out["p1_snapshot_checks"] = {
        "round_eq_5": m.get("round") == 5,
        "finished_true": m.get("finished") is True,
        "result_type_success": m.get("result_type") == "success",
        "reason_nonempty": bool(m.get("termination_reason")),
        "messages_cover_1_to_5": rounds == [1, 2, 3, 4, 5],
        "single_report_row": len(rows) == 1,
    }

# ---------- P1：report 接口调用两次 ----------
reports_api = []
for k in (1, 2):
    r = requests.get(f"{BASE}/api/session/{SID}/report", headers=H, timeout=120)
    body = r.json() if r.status_code == 200 else None
    reports_api.append({
        "call": k, "code": r.status_code,
        "report_id_before": rows[0]["id"] if rows else None,
        "metrics": {kk: (body or {}).get("metrics", {}).get(kk)
                    for kk in ("round", "finished", "result_type", "termination_reason")},
        "messages_len": len((body or {}).get("messages", [])),
        "total_rounds": (body or {}).get("total_rounds"),
    })
    time.sleep(1.2)

rows2, _ = report_rows(DB_SID)
out["p1_report_api_calls"] = reports_api
out["p1_after_two_report_calls"] = {
    "report_row_count": len(rows2),
    "same_report_id": (len(rows2) == 1 and bool(rows) and rows2[0]["id"] == rows[0]["id"]),
    "created_at": rows2[0]["created_at"] if rows2 else None,
}
if rows2:
    snap2 = json.loads(rows2[0]["report_json"])
    m2 = snap2.get("metrics", {})
    out["p1_after_two_report_calls"]["metrics_round"] = m2.get("round")
    out["p1_after_two_report_calls"]["metrics_finished"] = m2.get("finished")
    out["p1_after_two_report_calls"]["metrics_result_type"] = m2.get("result_type")
    out["p1_after_two_report_calls"]["metrics_reason"] = m2.get("termination_reason")

# ---------- P2：调参校验（独立会话） ----------
r = requests.post(f"{BASE}/api/session", headers=H, json={
    "level": 1, "scenario_id": scen.get("id"), "use_llm": False}, timeout=30)
r.raise_for_status()
S2 = r.json()
SID2, DB_SID2 = S2["session_id"], S2["db_session_id"]
out["p2_session"] = {"session_id": SID2, "db_session_id": DB_SID2}

cfg_before = requests.post(f"{BASE}/api/session/{SID2}/auto-end-config", headers=H, json={},
                           timeout=30).json()
out["p2_config_before"] = {"config": cfg_before.get("config"),
                           "convergence": cfg_before.get("convergence")}

invalid_cases = [
    ("ratio_negative", {"attack_frequency_max": -0.1}),
    ("ratio_gt_1", {"attack_frequency_max": 1.5}),
    ("rounds_zero", {"min_rounds": 0}),
    ("rounds_float", {"min_rounds": 2.5}),
    ("rounds_string", {"max_rounds": "abc"}),
    ("rounds_null", {"bullying_stopped_rounds": None}),
    ("ratio_bool", {"victim_safety_min": True}),
    ("batch_valid_then_invalid", {"attack_frequency_max": 0.1, "min_rounds": 0}),
]
for name, payload in invalid_cases:
    r = requests.post(f"{BASE}/api/session/{SID2}/auto-end-config", headers=H,
                      json=payload, timeout=30)
    out["p2_invalid"].append({"case": name, "payload": payload,
                              "code": r.status_code, "detail": (r.json() or {}).get("detail")
                              if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]})

# NaN（非标准 JSON，Python json 接受）应 400 而非 500
r = requests.post(f"{BASE}/api/session/{SID2}/auto-end-config",
                  data='{"attack_frequency_max": NaN}',
                  headers={**H, "Content-Type": "application/json"}, timeout=30)
out["p2_invalid"].append({"case": "ratio_nan", "payload": "NaN",
                          "code": r.status_code, "detail": r.text[:200]})

cfg_after = requests.post(f"{BASE}/api/session/{SID2}/auto-end-config", headers=H, json={},
                          timeout=30).json()
out["p2_config_after_invalid"] = {"config": cfg_after.get("config"),
                                  "convergence": cfg_after.get("convergence")}
out["p2_config_unchanged"] = (
    cfg_before.get("config") == cfg_after.get("config")
    and cfg_before.get("convergence") == cfg_after.get("convergence"))

# 非法尝试后会话仍能正常运行
r = requests.post(f"{BASE}/api/session/{SID2}/run", headers=H, timeout=180)
out["p2_run_after_invalid"] = {"code": r.status_code,
                               "body_ok": r.status_code == 200,
                               "round": (r.json().get("round") if r.status_code == 200 else None),
                               "done": (r.json().get("done") if r.status_code == 200 else None),
                               "detail": r.text[:300] if r.status_code != 200 else None}

# 合法值提交并验证生效
legal = requests.post(f"{BASE}/api/session/{SID2}/auto-end-config", headers=H, json={
    "attack_frequency_max": 0.10}, timeout=30)
out["p2_legal"] = {"code": legal.status_code, "body": legal.json()}
st2 = requests.get(f"{BASE}/api/session/{SID2}", headers=H, timeout=30).json()["status"]
out["p2_legal_effective"] = {
    "status_attack_frequency_max": st2.get("attack_frequency_max"),
    "expected": 0.10,
    "match": abs((st2.get("attack_frequency_max") or -1) - 0.10) < 1e-9,
}
# 再跑一局，确认合法调参后不 500
r = requests.post(f"{BASE}/api/session/{SID2}/run", headers=H, timeout=180)
out["p2_run_after_legal"] = {"code": r.status_code,
                             "done": (r.json().get("done") if r.status_code == 200 else None),
                             "round": (r.json().get("round") if r.status_code == 200 else None)}

with open(EV + r"\r2_p1_p2_p3.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
p("\n[saved]", EV + r"\r2_p1_p2_p3.json")
p("[summary]", json.dumps({
    "p1_snapshot_checks": out.get("p1_snapshot_checks"),
    "p3_remaining": [x.get("remaining_to_stop") for x in out["p3_rounds"]],
    "end_round": out.get("end_round"),
    "p2_invalid_codes": [x["code"] for x in out["p2_invalid"]],
    "p2_config_unchanged": out.get("p2_config_unchanged"),
    "p2_legal_effective": out.get("p2_legal_effective"),
}, ensure_ascii=False))

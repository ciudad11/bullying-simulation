# -*- coding: utf-8 -*-
"""5001（noreset）真实 UI 自动结束验收：LLM-off 5 轮 + 诊断面板 + DB/report 落库。"""
import json
import sqlite3
import sys
import time

import requests

from cdp import CDP

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:5001"
ROOT = r"D:\bully_simulation_pack_v2_20260713"
EV = ROOT + r"\test_evidence"
DB = ROOT + r"\noreset\web\data.db"
USER = "qa_nr_1109a"
PWD = "QaTest2026!"
summary = {"steps": [], "remaining": []}


def log(step, data=None):
    summary["steps"].append({"step": step, "data": data})
    print(step, json.dumps(data, ensure_ascii=False) if data is not None else "")


r = requests.post(f"{BASE}/api/auth/register",
                  json={"username": USER, "password": PWD, "display_name": "nr 验收"}, timeout=30)
if r.status_code == 200:
    token = r.json()["token"]
else:
    r = requests.post(f"{BASE}/api/auth/login", json={"username": USER, "password": PWD}, timeout=30)
    r.raise_for_status()
    token = r.json()["token"]
H = {"Authorization": f"Bearer {token}"}
summary["account"] = USER


def api_status(sid):
    st = requests.get(f"{BASE}/api/session/{sid}", headers=H, timeout=30).json().get("status", {})
    return {k: st.get(k) for k in ("round", "remaining_to_stop", "bullying_stopped_rounds",
                                   "attack_frequency", "attack_frequency_max",
                                   "finished", "result_type", "termination_reason")}


def panel(c):
    return c.eval("""(() => {
      const t=id=>{const e=document.getElementById(id); return e? e.textContent.trim(): null};
      return {round:t('status-round'), frequency:t('value-frequency'),
              checkIntensity:t('check-intensity'), checkFrequency:t('check-frequency'),
              streak:t('check-streak'), remaining:t('check-remaining'), gap:t('check-gap'),
              badge: document.getElementById('session-badge')?.textContent.trim()};
    })()""")


c = CDP(9335)
c.navigate(BASE, 2.5)
c.eval("window.__errs=window.__errs||[];window.addEventListener('error',e=>window.__errs.push(String(e.message)));")
c.wait_js("!!document.getElementById('login-username')||!!document.getElementById('btn-start')", 40)
if not c.eval("!!document.getElementById('btn-logout') && document.getElementById('btn-logout').offsetParent!==null"):
    c.eval("""(() => {
      const set=(id,v)=>{const el=document.getElementById(id); if(!el) return; el.value=v;
        el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));};
      set('login-username', %s); set('login-password', %s);
      document.getElementById('btn-login').click(); return true;
    })()""" % (json.dumps(USER), json.dumps(PWD)))
    c.wait_js("document.getElementById('btn-logout') && document.getElementById('btn-logout').offsetParent!==null", 40)
log("logged_in", {"user": USER})
time.sleep(1.0)

c.eval("document.getElementById('btn-new-session-alt').click()")
time.sleep(0.8)
c.eval("""(() => {
  const s=document.getElementById('scenario-select'); s.value='scenario_1';
  s.dispatchEvent(new Event('change',{bubbles:true}));
  document.querySelector('.level-btn[data-level="1"]').click();
  const cb=document.getElementById('use-llm'); if(cb.checked){cb.checked=false;cb.dispatchEvent(new Event('change',{bubbles:true}));}
  return true;
})()""")
time.sleep(1.0)
log("setup", c.eval("""(() => ({
  scenario: document.getElementById('scenario-select').value,
  level1: document.querySelector('.level-btn[data-level="1"]').classList.contains('active'),
  useLlm: document.getElementById('use-llm').checked,
  startVisible: document.getElementById('btn-start').offsetParent!==null
}))()"""))
c.screenshot(EV + r"\nr_01_setup.png")

c.eval("document.getElementById('btn-start').click()")
c.wait_js("document.querySelector('#messages-list') && document.querySelector('#messages-list').innerText.includes('第 1 轮对话')", 120)
c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要') && b.offsetParent!==null)", 120)
sid = c.eval("typeof state!=='undefined' ? state.sessionId : null")
summary["session_id"] = sid
time.sleep(0.8)
for rnd, shot in ((1, r"\nr_02_round1_status.png"), (2, None), (3, r"\nr_03_round3_streak.png"), (4, None)):
    if rnd > 1:
        c.eval("""(() => {const b=Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes('不需要') && x.offsetParent!==null); if(b) b.click();})()""")
        c.wait_js("document.querySelector('#messages-list').innerText.includes('第 %d 轮对话')" % rnd, 120)
        c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要') && b.offsetParent!==null)", 120)
        time.sleep(0.8)
    ui = panel(c)
    api = api_status(sid)
    rec = {"round": rnd, "ui": ui, "api": api}
    num = None
    txt = ui.get("remaining") or ""
    for tok in txt.replace("距结束还需", " ").replace("轮", " ").split():
        if tok.isdigit():
            num = int(tok)
    rec["ui_remaining_num"] = num
    summary["remaining"].append(rec)
    log(f"round{rnd}", rec)
    if shot:
        c.eval("window.scrollTo(0,0)")
        c.screenshot(EV + shot)

# 第 5 轮
c.eval("""(() => {const b=Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes('不需要') && x.offsetParent!==null); if(b) b.click();})()""")
c.wait_js("document.querySelector('#messages-list').innerText.includes('第 5 轮对话')", 150)
c.wait_js("document.getElementById('autoend-modal') && getComputedStyle(document.getElementById('autoend-modal')).display!=='none'", 150)
time.sleep(1.0)
modal = c.eval("""(() => {
  const t=id=>{const e=document.getElementById(id);return e?e.textContent.trim():null};
  return {title:t('autoend-title'), reason:t('autoend-reason'),
          display:getComputedStyle(document.getElementById('autoend-modal')).display,
          lastMessages: Array.from(document.querySelectorAll('.message')).slice(-4).map(e=>e.innerText.replace(/\\s+/g,' ').trim()),
          interventionPromptVisible: Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要') && b.offsetParent!==null),
          ui:{round:t('status-round'), streak:t('check-streak'), remaining:t('check-remaining'), gap:t('check-gap'), frequency:t('value-frequency')},
          badge:t('session-badge')};
})()""")
api5 = api_status(sid)
summary["round5"] = {"ui": modal, "api": api5}
summary["remaining"].append({"round": 5, "ui": modal["ui"], "api": api5,
                             "ui_remaining_num": 0 if modal["ui"]["remaining"] == "已达结束条件" else None})
log("round5_autoend", summary["round5"])
c.eval("window.scrollTo(0,0)")
c.screenshot(EV + r"\nr_04_round5_autoend.png")
summary["js_errors"] = c.eval("window.__errs")

# DB 落库
sess_list = requests.get(f"{BASE}/api/sessions", headers=H, timeout=30).json().get("sessions", [])
summary["db_session_api"] = sess_list[0] if sess_list else None
db_sid = (sess_list[0] or {}).get("id") if sess_list else None
summary["db_session_id"] = db_sid
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
srow = conn.execute("select id,status,result_type,auto_end_reason,completed_at from sessions where id=?",
                    (db_sid,)).fetchone()
rrows = conn.execute("select id,report_json from reports where session_id=?", (db_sid,)).fetchall()
conn.close()
summary["db_session_row"] = dict(srow) if srow else None
summary["report_row_count"] = len(rrows)
if rrows:
    snap = json.loads(rrows[0]["report_json"])
    m = snap.get("metrics", {})
    rounds = sorted({x.get("round") for x in snap.get("messages", []) if x.get("round") is not None})
    summary["report_snapshot"] = {"report_id": rrows[0]["id"], "metrics_round": m.get("round"),
                                  "finished": m.get("finished"), "result_type": m.get("result_type"),
                                  "termination_reason": m.get("termination_reason"),
                                  "message_rounds": rounds,
                                  "total_rounds": snap.get("total_rounds")}

checks = {
    "ui_remaining_4_to_0": [x.get("ui_remaining_num") for x in summary["remaining"]] == [4, 3, 2, 1, 0],
    "api_remaining_4_to_0": [x["api"].get("remaining_to_stop") for x in summary["remaining"]] == [4, 3, 2, 1, 0],
    "modal_success_text": "调解成功" in (modal.get("title") or ""),
    "final_dialogue_present": any("实训结束" in x for x in modal.get("lastMessages", [])),
    "no_extra_prompt": not modal.get("interventionPromptVisible"),
    "db_completed_success": bool(srow) and srow["status"] == "completed" and srow["result_type"] == "success" and bool(srow["auto_end_reason"]),
    "report_single_row": len(rrows) == 1,
    "report_round5_finished": bool(rrows) and summary["report_snapshot"]["metrics_round"] == 5
                              and summary["report_snapshot"]["finished"] is True,
    "report_messages_1_to_5": bool(rrows) and all(r in summary["report_snapshot"]["message_rounds"] for r in (1, 2, 3, 4, 5)),
}
summary["checks"] = checks
with open(EV + r"\nr_ui_acceptance.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
c.close()
print("OK", json.dumps({"session": sid, "db": db_sid, "checks": checks},
                       ensure_ascii=False))

# -*- coding: utf-8 -*-
"""r2 UI 复验：level1 + 关闭 LLM，真实点击 5 轮，核对剩余轮数文本并截图。"""
import json
import sys
import time

import requests

from cdp import CDP

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:5000"
EV = r"D:\bully_simulation_pack_v2_20260713\test_evidence"
USER = "qa_r2ui_1109a"
PWD = "QaTest2026!"
summary = {"steps": []}


def log(step, data=None):
    summary["steps"].append({"step": step, "data": data})
    print(step, json.dumps(data, ensure_ascii=False) if data is not None else "")


# 专用账号（API 注册，避免复用历史数据）
r = requests.post(f"{BASE}/api/auth/register",
                  json={"username": USER, "password": PWD, "display_name": "r2 UI"}, timeout=30)
if r.status_code == 200:
    token = r.json()["token"]
else:
    r = requests.post(f"{BASE}/api/auth/login", json={"username": USER, "password": PWD}, timeout=30)
    r.raise_for_status()
    token = r.json()["token"]
H = {"Authorization": f"Bearer {token}"}
summary["account"] = USER


def api_status(sid):
    d = requests.get(f"{BASE}/api/session/{sid}", headers=H, timeout=30).json()
    st = d.get("status", {})
    return {k: st.get(k) for k in (
        "round", "remaining_to_stop", "bullying_stopped_rounds", "attack_frequency",
        "attack_frequency_max", "finished", "result_type", "termination_reason")}


def status_panel(c):
    return c.eval("""(() => {
      const t=id=>{const e=document.getElementById(id); return e? e.textContent.trim(): null};
      return {round:t('status-round'), intensity:t('value-intensity'), distress:t('value-distress'),
              frequency:t('value-frequency'), checkIntensity:t('check-intensity'),
              checkFrequency:t('check-frequency'), streak:t('check-streak'),
              remaining:t('check-remaining'), gap:t('check-gap'),
              badge: document.getElementById('session-badge')?.textContent.trim()};
    })()""")


def click_text(c, text):
    return c.eval("""(() => {
      const t=%s; const btns=Array.from(document.querySelectorAll('button'));
      const b=btns.find(x=>x.textContent.includes(t) && x.offsetParent!==null);
      if(!b) return {clicked:false};
      b.click(); return {clicked:true, id:b.id, text:b.textContent.trim()};
    })()""" % json.dumps(text))


c = CDP(9333)
c.navigate(BASE, 2.5)
c.eval("window.__errs=window.__errs||[];window.addEventListener('error',e=>window.__errs.push(String(e.message)));")
c.wait_js("!!document.getElementById('login-username')||!!document.getElementById('btn-start')", 40)
logged_in = c.eval("!!document.getElementById('btn-logout') && document.getElementById('btn-logout').offsetParent!==null")
if not logged_in:
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
  level1Active: document.querySelector('.level-btn[data-level="1"]').classList.contains('active'),
  levelInfo: document.getElementById('level-info').textContent.replace(/\\s+/g,' ').trim(),
  useLlm: document.getElementById('use-llm').checked,
  startVisible: document.getElementById('btn-start').offsetParent!==null,
  scenarioOptions: document.getElementById('scenario-select').options.length
}))()"""))
c.screenshot(EV + r"\r2_01_setup.png")

c.eval("document.getElementById('btn-start').click()")
c.wait_js("document.querySelector('#messages-list') && document.querySelector('#messages-list').innerText.includes('第 1 轮对话')", 90)
c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要') && b.offsetParent!==null)", 90)
sid = c.eval("typeof state!=='undefined' ? state.sessionId : null")
summary["session_id"] = sid
time.sleep(0.8)
panel = status_panel(c)
api = api_status(sid)
log("round1", {"ui": panel, "api": api})
c.eval("window.scrollTo(0,0)")
c.screenshot(EV + r"\r2_02_round1_status.png")

for rnd in (2, 3, 4):
    click_text(c, "不需要")
    c.wait_js("document.querySelector('#messages-list').innerText.includes('第 %d 轮对话')" % rnd, 120)
    c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要') && b.offsetParent!==null)", 120)
    time.sleep(0.8)
    panel = status_panel(c)
    api = api_status(sid)
    log(f"round{rnd}", {"ui": panel, "api": api})
    if rnd == 3:
        c.eval("window.scrollTo(0,0)")
        c.screenshot(EV + r"\r2_03_round3_streak.png")
    if rnd == 4:
        c.eval("window.scrollTo(0,0)")
        c.screenshot(EV + r"\r2_04_round4_status.png")

# 第 5 轮：点击“不需要”后自动结束（同轮终止，无需额外点击）
click_text(c, "不需要")
c.wait_js("document.querySelector('#messages-list').innerText.includes('第 5 轮对话')", 120)
c.wait_js("document.getElementById('autoend-modal') && getComputedStyle(document.getElementById('autoend-modal')).display!=='none'", 120)
time.sleep(0.8)
modal = c.eval("""(() => ({
  title: document.getElementById('autoend-title')?.textContent.trim(),
  reason: document.getElementById('autoend-reason')?.textContent.trim(),
  display: getComputedStyle(document.getElementById('autoend-modal')).display,
  lastMessages: Array.from(document.querySelectorAll('.message')).slice(-4).map(e=>e.innerText.replace(/\\s+/g,' ').trim()),
  interventionPromptVisible: Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要') && b.offsetParent!==null),
  ui: (()=>{const t=id=>{const e=document.getElementById(id);return e?e.textContent.trim():null};
    return {round:t('status-round'),streak:t('check-streak'),remaining:t('check-remaining'),gap:t('check-gap'),frequency:t('value-frequency')}})()
}))()""")
api5 = api_status(sid)
log("round5_autoend", {"ui": modal, "api": api5})
c.eval("window.scrollTo(0,0)")
c.screenshot(EV + r"\r2_05_round5_autoend.png")

summary["js_errors"] = c.eval("window.__errs")
# 逐轮 UI 文本 vs API 字段一致性
rows = []
for s in summary["steps"]:
    ui = (s.get("data") or {}).get("ui")
    api = (s.get("data") or {}).get("api")
    if ui and api:
        text = ui.get("remaining") or ""
        num = None
        for tok in text.replace("距结束还需", " ").replace("轮", " ").split():
            if tok.isdigit():
                num = int(tok)
        rows.append({"round": api.get("round"), "ui_text": text,
                     "ui_num": num, "api_remaining": api.get("remaining_to_stop"),
                     "match": (num == api.get("remaining_to_stop")) if num is not None
                     else (text == "已达结束条件" and api.get("remaining_to_stop") == 0)})
summary["remaining_consistency"] = rows
summary["remaining_all_match"] = all(x["match"] for x in rows) and len(rows) >= 5
with open(EV + r"\r2_ui_acceptance.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
c.close()
print("OK", json.dumps({"session_id": sid,
                        "remaining": [x["api_remaining"] for x in rows],
                        "all_match": summary["remaining_all_match"],
                        "js_errors": summary["js_errors"]}, ensure_ascii=False))

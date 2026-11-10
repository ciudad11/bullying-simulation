# -*- coding: utf-8 -*-
"""5000 端口浏览器真实交互验收：level1 + 关闭 LLM，连续 5 轮，验证自动结束与诊断面板。"""
import json
import sys
import time

from cdp import CDP

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:5000"
EV = r"D:\bully_simulation_pack_v2_20260713\test_evidence"
USER = "qa_autoend_1109a"
PWD = "QaTest2026!"

summary = {"steps": []}


def log(step, data=None):
    summary["steps"].append({"step": step, "data": data})
    print(step, json.dumps(data, ensure_ascii=False) if data is not None else "")


def status_panel(c):
    return c.eval("""(() => {
      const t=id=>{const e=document.getElementById(id); return e? e.textContent.trim(): null};
      return {round:t('status-round'), intensity:t('value-intensity'), distress:t('value-distress'),
              frequency:t('value-frequency'), checkIntensity:t('check-intensity'),
              checkFrequency:t('check-frequency'), streak:t('check-streak'),
              remaining:t('check-remaining'), gap:t('check-gap'),
              badge: document.getElementById('session-badge')?.textContent.trim()};
    })()""")


def click_text(c, text, visible_only=False):
    return c.eval("""(() => {
      const t=%s; const btns=Array.from(document.querySelectorAll('button'));
      const b=btns.find(x=>x.textContent.includes(t) && (%s || x.offsetParent!==null));
      if(!b) return {clicked:false, candidates:btns.filter(x=>x.textContent.includes(t)).map(x=>({id:x.id,vis:x.offsetParent!==null}))};
      b.click(); return {clicked:true, id:b.id, text:b.textContent.trim()};
    })()""" % (json.dumps(text), "true" if not visible_only else "false"))


c = CDP(9333)
c.navigate(BASE, 2.0)
c.eval("window.__errs=window.__errs||[];window.addEventListener('error',e=>window.__errs.push(String(e.message)));")
c.wait_js("!!document.getElementById('login-username')||!!document.getElementById('btn-start')", 30)
logged_in = c.eval("!!document.getElementById('btn-logout') && document.getElementById('btn-logout').offsetParent!==null")
if not logged_in:
    c.eval("""(() => {
      const set=(id,v)=>{const el=document.getElementById(id); if(!el) return; el.value=v;
        el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));};
      set('login-username', %s); set('login-password', %s);
      document.getElementById('btn-login').click(); return true;
    })()""" % (json.dumps(USER), json.dumps(PWD)))
    c.wait_js("document.getElementById('btn-logout') && document.getElementById('btn-logout').offsetParent!==null", 30)
log("logged_in", {"user": USER})
time.sleep(1.5)

# 按真实用户流程：先点“新会话”进入准备状态，再选择情境/级别
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
  startDisplay: getComputedStyle(document.getElementById('btn-start')).display,
  panelClass: document.getElementById('start-panel').className,
  panelDisplay: getComputedStyle(document.getElementById('start-panel')).display,
  scenarioOptions: document.getElementById('scenario-select').options.length
}))()"""))
c.screenshot(EV + r"\01_setup_ready.png")

c.eval("document.getElementById('btn-start').click()")
c.wait_js("document.querySelector('#messages-list') && document.querySelector('#messages-list').innerText.includes('第 1 轮对话')", 60)
c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要') && b.offsetParent!==null)", 60)
sid = c.eval("typeof state!=='undefined' ? state.sessionId : null")
log("round1_done", {"session_id": sid, "status": status_panel(c)})
c.eval("window.scrollTo(0,0)")
c.screenshot(EV + r"\02_round1_status.png")

for r in (2, 3, 4):
    click_text(c, "不需要")
    c.wait_js("document.querySelector('#messages-list').innerText.includes('第 %d 轮对话')" % r, 90)
    c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要') && b.offsetParent!==null)", 90)
    st = status_panel(c)
    log(f"round{r}_done", st)
    if r == 3:
        c.eval("window.scrollTo(0,0)")
        c.screenshot(EV + r"\03_round3_streak.png")

# 第 5 轮：点击“不需要”后应自动结束，不再出现第 6 轮或新的干预确认
click_text(c, "不需要")
c.wait_js("document.querySelector('#messages-list').innerText.includes('第 5 轮对话')", 90)
c.wait_js("document.getElementById('autoend-modal') && getComputedStyle(document.getElementById('autoend-modal')).display!=='none'", 90)
time.sleep(0.8)
modal = c.eval("""(() => ({
  title: document.getElementById('autoend-title')?.textContent.trim(),
  reason: document.getElementById('autoend-reason')?.textContent.trim(),
  display: getComputedStyle(document.getElementById('autoend-modal')).display,
  lastMessages: Array.from(document.querySelectorAll('.message')).slice(-4).map(e=>e.innerText.replace(/\\s+/g,' ').trim()),
  interventionPromptVisible: Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要') && b.offsetParent!==null),
  status: (()=>{const t=id=>{const e=document.getElementById(id);return e?e.textContent.trim():null};
    return {round:t('status-round'),streak:t('check-streak'),remaining:t('check-remaining'),gap:t('check-gap'),frequency:t('value-frequency')}})()
}))()""")
log("round5_autoend", modal)
c.eval("window.scrollTo(0,0)")
c.screenshot(EV + r"\04_round5_autoend_modal.png")

summary["session_id"] = sid
summary["js_errors"] = c.eval("window.__errs")
with open(EV + r"\ui_acceptance.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
c.close()
print("OK")

# -*- coding: utf-8 -*-
"""诚实诊断 + 手动停止 + 干预确认流程验收（5000 端口）。"""
import json
import sys
import time

from cdp import CDP

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:5000"
EV = r"D:\bully_simulation_pack_v2_20260713\test_evidence"
out = {"steps": []}


def log(step, data=None):
    out["steps"].append({"step": step, "data": data})
    print(step, json.dumps(data, ensure_ascii=False) if data is not None else "")


def panel(c):
    return c.eval("""(() => {
      const t=id=>{const e=document.getElementById(id); return e? e.textContent.trim(): null};
      return {round:t('status-round'), intensity:t('value-intensity'), frequency:t('value-frequency'),
              checkIntensity:t('check-intensity'), checkFrequency:t('check-frequency'),
              streak:t('check-streak'), remaining:t('check-remaining'), gap:t('check-gap'),
              badge:t('session-badge'), autoendDisplay:getComputedStyle(document.getElementById('autoend-modal')).display};
    })()""")


def click_visible(c, text):
    return c.eval("""(() => {
      const b=Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes(%s)&&x.offsetParent!==null);
      if(!b) return {clicked:false}; b.click(); return {clicked:true,text:b.textContent.trim()};
    })()""" % json.dumps(text))


c = CDP(9333)
c.navigate(BASE, 2.0)
c.wait_js("!!document.getElementById('btn-logout') && document.getElementById('btn-logout').offsetParent!==null", 30)
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
log("setup", c.eval("""(() => ({scenario:document.getElementById('scenario-select').value,
  level1:document.querySelector('.level-btn[data-level="1"]').classList.contains('active'),
  useLlm:document.getElementById('use-llm').checked,
  startVisible:document.getElementById('btn-start').offsetParent!==null}))()"""))
c.eval("document.getElementById('btn-start').click()")
c.wait_js("document.querySelector('#messages-list').innerText.includes('第 1 轮对话')", 60)
c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要')&&b.offsetParent!==null)", 60)
sid = c.eval("typeof state!=='undefined' ? state.sessionId : null")
log("round1", {"session_id": sid, "panel": panel(c)})

cfg = c.eval("""(async () => {
  const r = await apiCall('/api/session/' + state.sessionId + '/auto-end-config', 'POST', {attack_frequency_max: 0.10});
  const s = await apiCall('/api/session/' + state.sessionId);
  return {response: r, status_thresholds: {attack_frequency_max: s.status.attack_frequency_max,
          bullying_intensity_max: s.status.bullying_intensity_max, required: s.status.bullying_stopped_required}};
})()""", timeout_ms=60000)
log("config_applied", cfg)

for r in (2, 3, 4, 5, 6):
    click_visible(c, "不需要")
    c.wait_js("document.querySelector('#messages-list').innerText.includes('第 %d 轮对话')" % r, 90)
    c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要')&&b.offsetParent!==null)", 90)
    p = panel(c)
    log(f"round{r}", p)
    if r == 2:
        c.eval("window.scrollTo(0,0)")
        c.screenshot(EV + r"\05_diag_frequency_gap.png")
    if r == 6:
        c.eval("window.scrollTo(0,0)")
        c.screenshot(EV + r"\06_diag_no_autoend_round6.png")

# 干预确认流程：需要干预 → 输入 → 发送 → 自动进入下一轮
dec = click_visible(c, "需要干预")
c.wait_js("!!document.getElementById('intervention-input') && !document.getElementById('intervention-input').disabled", 20)
c.eval("""(() => {
  const el=document.getElementById('intervention-input');
  el.value='同学们，请停止嘲笑，尊重每一位同学的作品，我们课后继续讨论。';
  el.dispatchEvent(new Event('input',{bubbles:true}));
  document.getElementById('btn-intervene').click(); return true;
})()""")
c.wait_js("document.querySelector('#messages-list').innerText.includes('张老师')", 60)
c.wait_js("document.querySelector('#messages-list').innerText.includes('第 7 轮对话')", 90)
c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要')&&b.offsetParent!==null)", 90)
iv = c.eval("""(async () => {
  const s = await apiCall('/api/session/' + state.sessionId);
  return {intervention_count: s.status.intervention_count, round: s.status.round,
          teacherMessage: Array.from(document.querySelectorAll('.message')).some(e=>e.innerText.includes('张老师'))};
})()""", timeout_ms=60000)
log("intervention_flow", {"decision_click": dec, **iv})
c.eval("window.scrollTo(0,0)")
c.screenshot(EV + r"\07_intervention_sent.png")

# 手动停止
c.eval("document.getElementById('btn-stop').click()")
c.wait_js("document.getElementById('session-badge').textContent.trim()==='已结束'", 90)
time.sleep(1.0)
stop = c.eval("""(() => ({badge:document.getElementById('session-badge').textContent.trim(),
  autoendDisplay:getComputedStyle(document.getElementById('autoend-modal')).display,
  reportModalActive:document.getElementById('report-modal').classList.contains('active'),
  hasEndMarker:document.querySelector('#messages-list').innerText.includes('实训结束')}))()""")
log("manual_stop", stop)
c.eval("window.scrollTo(0,0)")
c.screenshot(EV + r"\08_manual_stop.png")

out["session_id"] = sid
out["js_errors"] = c.eval("window.__errs||[]")
with open(EV + r"\diagnostics_ui.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
c.close()
print("OK")

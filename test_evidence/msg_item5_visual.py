# -*- coding: utf-8 -*-
"""补充：5000 主动停止弹窗的截图证据（轮间停止），并核验报告按钮。"""
import json
import sys
import time

from cdp import CDP

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:5000"
EV = r"D:\bully_simulation_pack_v2_20260713\test_evidence"

c = CDP(9333)
c.navigate(BASE, 3.0)
c.wait_js("!!document.getElementById('btn-logout') && document.getElementById('btn-logout').offsetParent!==null", 30)
c.eval("document.getElementById('btn-new-session-alt').click()")
time.sleep(1.0)
c.eval("""(() => {
  const s=document.getElementById('scenario-select'); s.value='scenario_1';
  s.dispatchEvent(new Event('change',{bubbles:true}));
  document.querySelector('.level-btn[data-level="1"]').click();
  const cb=document.getElementById('use-llm'); if(cb.checked){cb.checked=false;cb.dispatchEvent(new Event('change',{bubbles:true}));}
  document.getElementById('btn-start').click(); return true;
})()""")
c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要')&&b.offsetParent!==null)", 180)
time.sleep(0.5)
c.eval("document.getElementById('btn-stop').click()")
c.wait_js("document.getElementById('autoend-modal').classList.contains('active')", 240)
time.sleep(1.0)
c.eval("window.scrollTo(0,0)")
c.screenshot(EV + r"\msg_item5_modal.png")
m = c.eval("""(() => ({title:document.getElementById('autoend-title').textContent,
      subtitle:document.getElementById('autoend-subtitle').textContent,
      reason:document.getElementById('autoend-reason').textContent,
      badge:document.getElementById('session-badge').textContent,
      endMessageCount:(((document.querySelector('#messages-list')||{}).innerText)||'').match(/🛑 实训结束/g)?.length||0,
      sessionId:state.sessionId}))()""")
c.eval("document.getElementById('autoend-modal').classList.remove('active')")
c.eval("document.getElementById('btn-view-report').click()")
c.wait_js("document.getElementById('report-modal').classList.contains('active')", 60)
out = {"modal": m, "report_open": c.eval("document.getElementById('report-modal').classList.contains('active')")}
json.dump(out, open(EV + r"\msg_item5_visual.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False))
c.close()

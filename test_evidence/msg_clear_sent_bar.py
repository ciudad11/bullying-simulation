# -*- coding -*-
"""定向验收：开启新对话后清空「干预已发送」提示栏残留（fin_ver 5000）。
方法：Edge headless + CDP；注册专用账号登录；人为置 sent/sending 状态模拟残留，
点击「新会话」(resetToStart) 与直接 resetInterventionSendState，断言状态条清空。
不发送真实干预、不新建模拟会话、不写业务 DB（仅注册账号）。"""
import json
import sys
import time

from cdp import CDP

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:5000"
EV = r"D:\bully_simulation_pack_v2_20260713\test_evidence"

c = CDP(9336)
c.navigate(BASE, 2.5)
# 注册并登录专用账号（仅账号，无会话）
reg = c.eval("""(async () => {
  const r = await fetch('/api/auth/register', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({username:'msg_clear_1110a', password:'Test123456', display_name:'msg清除验收', email:''})});
  const d = await r.json();
  if (d.token) localStorage.setItem('auth_token', d.token);
  return {status:r.status, hasToken:!!d.token};
})()""")
c.navigate(BASE, 2.5)
c.wait_js("typeof resetInterventionSendState === 'function' && !!document.getElementById('intervention-send-status')", 30)

out = {"register": reg, "checks": {}}

# 1) 模拟上一会话残留的「已发送」状态条
c.eval("""(() => {
  interventionSend.status='sent'; interventionSend.content='残留的已发送干预话语'; interventionSend.token='iv_old';
  renderSendStatus({content:'残留的已发送干预话语：请温和而坚定地制止嘲笑', sent_at:'2026-11-09 21:00', quality_score:80, round:3});
  return true;
})()""")
before = c.eval("""(() => ({html:document.getElementById('intervention-send-status').innerHTML,
  cls:document.getElementById('intervention-send-status').className, status:interventionSend.status}))()""")
out["before"] = before
out["checks"]["residual_present_before"] = ("干预已发送" in before["html"] and "残留的已发送干预话语" in before["html"])

# 2) 点击「新会话」→ resetToStart，应清空状态条
c.eval("document.getElementById('btn-new-session-alt').click()")
time.sleep(0.6)
after_new = c.eval("""(() => ({html:document.getElementById('intervention-send-status').innerHTML,
  cls:document.getElementById('intervention-send-status').className, status:interventionSend.status,
  token:interventionSend.token, content:interventionSend.content,
  btnLabel:document.getElementById('btn-intervene').textContent}))()""")
out["after_new_session"] = after_new
out["checks"]["cleared_on_new_session"] = (after_new["html"] == "" and after_new["status"] == "idle"
                                          and after_new["cls"].endswith("idle") and after_new["token"] is None
                                          and after_new["content"] == "")

# 3) 模拟 sending + 慢速计时器残留，直接 reset 应取消计时器并复原按钮文案
c.eval("""(() => {
  interventionSend.status='sending'; interventionSend.startedAt=Date.now()-3000;
  interventionSend.slowTimer=setTimeout(()=>{}, 100000);
  document.getElementById('btn-intervene').classList.add('is-sending');
  document.getElementById('btn-intervene').textContent='⏳ 发送中…';
  renderSendStatus();
  return true;
})()""")
c.eval("resetInterventionSendState()")
after_reset = c.eval("""(() => ({html:document.getElementById('intervention-send-status').innerHTML,
  status:interventionSend.status, timer:interventionSend.slowTimer,
  btnLabel:document.getElementById('btn-intervene').textContent,
  btnSendingClass:document.getElementById('btn-intervene').classList.contains('is-sending')}))()""")
out["after_reset_sending"] = after_reset
out["checks"]["sending_reset_clears_timer_and_label"] = (after_reset["html"] == "" and after_reset["status"] == "idle"
                                                         and after_reset["timer"] is None
                                                         and after_reset["btnLabel"] == "📤 发送干预"
                                                         and after_reset["btnSendingClass"] is False)

# 4) 再次置 sent 后走「开始新会话」路径的复位函数等价性（startSimulation 内调用同一 reset）
c.eval("""(() => { interventionSend.status='sent'; renderSendStatus({content:'第二次残留', sent_at:'x', round:1}); return true; })()""")
c.eval("resetInterventionSendState()")
out["checks"]["second_residual_cleared"] = c.eval("document.getElementById('intervention-send-status').innerHTML === ''")

out["console_errors"] = c.eval("window.__errs || []")
json.dump(out, open(EV + r"\msg_clear_sent_bar.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False))
c.close()
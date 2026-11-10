# -*- coding: utf-8 -*-
"""真实 LLM 验收：use_llm=true，浏览器真实点击跑 2 轮，断言真实模型文本、无兜底。

仅使用新建测试账号；LLM 调用次数按服务端日志新增的 `LLM raw` 行统计。
"""
import json
import re
import sys
import time

import requests

from cdp import CDP

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:5000"
ROOT = r"D:\bully_simulation_pack_v2_20260713"
EV = ROOT + r"\test_evidence"
USER = "qa_llm_1109a"
PWD = "QaTest2026!"
OUT_LOG = ROOT + r"\fin_ver\_run_out.log"
ERR_LOG = ROOT + r"\fin_ver\_run_err.log"
FALLBACK = "[LLM暂时不可用，请检查API配置]"
summary = {"steps": []}


def log(step, data=None):
    summary["steps"].append({"step": step, "data": data})
    print(step, json.dumps(data, ensure_ascii=False) if data is not None else "")


def lines(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


base_out = len(lines(OUT_LOG))
base_err = len(lines(ERR_LOG))
summary["log_baseline"] = {"out_lines": base_out, "err_lines": base_err,
                           "out_mtime_before": time.ctime()}

# 账号准备
r = requests.post(f"{BASE}/api/auth/register",
                  json={"username": USER, "password": PWD, "display_name": "LLM 验收"}, timeout=30)
if r.status_code == 200:
    token = r.json()["token"]
else:
    r = requests.post(f"{BASE}/api/auth/login", json={"username": USER, "password": PWD}, timeout=30)
    r.raise_for_status()
    token = r.json()["token"]
H = {"Authorization": f"Bearer {token}"}
summary["account"] = USER

c = CDP(9334)
c.navigate(BASE, 2.5)
c.eval("window.__errs=window.__errs||[];window.addEventListener('error',e=>window.__errs.push(String(e.message)));")
c.wait_js("!!document.getElementById('login-username')||!!document.getElementById('btn-start')", 40)
logged = c.eval("!!document.getElementById('btn-logout') && document.getElementById('btn-logout').offsetParent!==null")
if not logged:
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
setup = c.eval("""(() => {
  const s=document.getElementById('scenario-select'); s.value='scenario_1';
  s.dispatchEvent(new Event('change',{bubbles:true}));
  const lv=document.querySelector('.level-btn[data-level="2"]'); if(lv) lv.click();
  const cb=document.getElementById('use-llm');
  if(cb && !cb.checked){ cb.checked=true; cb.dispatchEvent(new Event('change',{bubbles:true})); }
  return {scenario:s.value, level2: lv? lv.classList.contains('active'):null,
          useLlm: cb? cb.checked:null, useLlmDisabled: cb? cb.disabled:null,
          startVisible: document.getElementById('btn-start').offsetParent!==null};
})()""")
time.sleep(1.0)
setup = c.eval("""(() => ({
  scenario: document.getElementById('scenario-select').value,
  level2: document.querySelector('.level-btn[data-level="2"]').classList.contains('active'),
  useLlm: document.getElementById('use-llm').checked,
  useLlmDisabled: document.getElementById('use-llm').disabled,
  startVisible: document.getElementById('btn-start').offsetParent!==null
}))()""")
log("setup", setup)

c.eval("document.getElementById('btn-start').click()")
c.wait_js("document.querySelector('#messages-list') && document.querySelector('#messages-list').innerText.includes('第 1 轮对话')", 240)
c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要') && b.offsetParent!==null)", 240)
sid = c.eval("typeof state!=='undefined' ? state.sessionId : null")
summary["session_id"] = sid
time.sleep(0.8)


def api_messages():
    r = requests.get(f"{BASE}/api/session/{sid}/messages", headers=H, timeout=30)
    return r.json().get("messages", []) if r.status_code == 200 else []


def agent_texts(msgs):
    return [m for m in msgs if m.get("sender") not in (None, "System") and m.get("role") != "system"]


log("round1", {"api_status": requests.get(f"{BASE}/api/session/{sid}", headers=H, timeout=30).json().get("status", {}),
               "messages": agent_texts(api_messages())})
c.eval("window.scrollTo(0,0)")
c.screenshot(EV + r"\llm_round1_real_output.png")

# 第二轮：点击“不需要”推进
c.eval("""(() => {
  const b=Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes('不需要') && x.offsetParent!==null);
  if(b) b.click();
})()""")
c.wait_js("document.querySelector('#messages-list').innerText.includes('第 2 轮对话')", 300)
c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要') && b.offsetParent!==null)", 300)
time.sleep(1.0)
msgs = agent_texts(api_messages())
log("round2", {"api_status": requests.get(f"{BASE}/api/session/{sid}", headers=H, timeout=30).json().get("status", {}),
               "messages": msgs})
c.eval("window.scrollTo(0,0)")
c.screenshot(EV + r"\llm_round2_real_output.png")

# 完成会话绑定信息（DB session id）
sess_list = requests.get(f"{BASE}/api/sessions", headers=H, timeout=30).json().get("sessions", [])
summary["db_session"] = sess_list[0] if sess_list else None

# 兜底 / 预设回复检测
def flatten_presets():
    vals = []
    try:
        sys.path.insert(0, ROOT + r"\fin_ver\src")
        from multi_agent.llm_client import PRESET_RESPONSES
        for lv in PRESET_RESPONSES.values():
            for arr in lv.values():
                vals.extend(arr)
    except Exception as e:
        summary["preset_load_error"] = str(e)
    return set(vals)


presets = flatten_presets()
all_msgs = agent_texts(api_messages())
contents = [m.get("content", "") for m in all_msgs]
summary["assertions"] = {
    "message_count": len(all_msgs),
    "rounds_present": sorted({m.get("round") for m in all_msgs if m.get("round") is not None}),
    "fallback_marker_hits": [t for t in contents if FALLBACK in t],
    "preset_exact_hits": [t for t in contents if t.strip() in presets],
    "empty_contents": [i for i, t in enumerate(contents) if not t.strip()],
    "sample_texts": contents[:8],
}

# 服务端日志新增部分
new_out = lines(OUT_LOG)[base_out:]
new_err = lines(ERR_LOG)[base_err:]
joined = "\n".join(new_out)
summary["log_new"] = {
    "out_new_lines": len(new_out),
    "err_new_lines": len(new_err),
    "llm_raw_count": len(re.findall(r"\[DEBUG\].*?LLM raw:", joined)),
    "invalid_port": joined.count("Invalid port"),
    "fallback_text": joined.count("LLM暂时不可用"),
    "api_call_failed": joined.count("API调用失败"),
    "retry_failed": joined.count("重试3次仍失败"),
    "llm_raw_samples": [ln.strip() for ln in new_out if "LLM raw:" in ln][:10],
}
summary["js_errors"] = c.eval("window.__errs")

checks = {
    "use_llm_enabled_and_checked": bool(setup.get("useLlm")) and not bool(setup.get("useLlmDisabled")),
    "real_model_text": (not summary["assertions"]["fallback_marker_hits"]
                        and summary["assertions"]["preset_exact_hits"] == []
                        and summary["assertions"]["empty_contents"] == []
                        and summary["log_new"]["llm_raw_count"] >= 3),
    "no_three_errors_in_new_log": (summary["log_new"]["invalid_port"] == 0
                                   and summary["log_new"]["fallback_text"] == 0
                                   and summary["log_new"]["api_call_failed"] == 0),
}
summary["checks"] = checks
with open(EV + r"\llm_real_run.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
with open(EV + r"\llm_run_out_excerpt.log", "w", encoding="utf-8") as f:
    f.write("\n".join(new_out))
with open(EV + r"\llm_run_err_excerpt.log", "w", encoding="utf-8") as f:
    f.write("\n".join(new_err))
c.close()
print("OK", json.dumps({"session": sid, "checks": checks,
                        "llm_raw": summary["log_new"]["llm_raw_count"],
                        "assertions": {k: (v if k != "sample_texts" else "...") for k, v in summary["assertions"].items()}},
                       ensure_ascii=False))

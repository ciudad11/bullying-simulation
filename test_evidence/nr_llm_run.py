# -*- coding: utf-8 -*-
"""5001（noreset）真实 LLM 验收：LLM-on 2 轮，真实模型文本、0 兜底，日志无三类错误。"""
import json
import re
import sys
import time

import requests

from cdp import CDP

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:5001"
ROOT = r"D:\bully_simulation_pack_v2_20260713"
EV = ROOT + r"\test_evidence"
LOG = ROOT + r"\noreset\_run_5001_out.log"
ERR = ROOT + r"\noreset\_run_5001_err.log"
USER = "qa_nrllm_1109a"
PWD = "QaTest2026!"
FALLBACK = "[LLM暂时不可用，请检查API配置]"
summary = {"steps": []}


def log(step, data=None):
    summary["steps"].append({"step": step, "data": data})
    print(step, json.dumps(data, ensure_ascii=False) if data is not None else "")


def read_lines(path, enc="gbk"):
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode(enc, errors="replace").splitlines()


base_out = len(read_lines(LOG))
base_err = len(read_lines(ERR, "utf-8"))
summary["log_baseline"] = {"out_lines": base_out, "err_lines": base_err}

r = requests.post(f"{BASE}/api/auth/register",
                  json={"username": USER, "password": PWD, "display_name": "nr LLM"}, timeout=30)
if r.status_code == 200:
    token = r.json()["token"]
else:
    r = requests.post(f"{BASE}/api/auth/login", json={"username": USER, "password": PWD}, timeout=30)
    r.raise_for_status()
    token = r.json()["token"]
H = {"Authorization": f"Bearer {token}"}
summary["account"] = USER

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
  const lv=document.querySelector('.level-btn[data-level="2"]'); if(lv) lv.click();
  const cb=document.getElementById('use-llm'); if(cb && !cb.checked){cb.checked=true;cb.dispatchEvent(new Event('change',{bubbles:true}));}
  return true;
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
c.wait_js("document.querySelector('#messages-list') && document.querySelector('#messages-list').innerText.includes('第 1 轮对话')", 300)
c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要') && b.offsetParent!==null)", 300)
sid = c.eval("typeof state!=='undefined' ? state.sessionId : null")
summary["session_id"] = sid
time.sleep(0.8)


def agent_texts():
    msgs = requests.get(f"{BASE}/api/session/{sid}/messages", headers=H, timeout=30).json().get("messages", [])
    return [m for m in msgs if m.get("sender") not in (None, "System") and m.get("role") != "system"]


log("round1", {"messages": agent_texts()})
c.eval("window.scrollTo(0,0)")
c.screenshot(EV + r"\nr_llm_round1.png")

c.eval("""(() => {const b=Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes('不需要') && x.offsetParent!==null); if(b) b.click();})()""")
c.wait_js("document.querySelector('#messages-list').innerText.includes('第 2 轮对话')", 360)
c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要') && b.offsetParent!==null)", 360)
time.sleep(1.0)
msgs = agent_texts()
log("round2", {"messages": msgs})
c.eval("window.scrollTo(0,0)")
c.screenshot(EV + r"\nr_llm_round2.png")
summary["js_errors"] = c.eval("window.__errs")

sess_list = requests.get(f"{BASE}/api/sessions", headers=H, timeout=30).json().get("sessions", [])
summary["db_session"] = sess_list[0] if sess_list else None

# 预设/兜底检测
presets = set()
try:
    sys.path.insert(0, ROOT + r"\noreset\src")
    from multi_agent.llm_client import PRESET_RESPONSES
    for lv in PRESET_RESPONSES.values():
        for arr in lv.values():
            presets.update(arr)
except Exception as e:
    summary["preset_load_error"] = str(e)

contents = [m.get("content", "") for m in msgs]
summary["assertions"] = {
    "message_count": len(msgs),
    "rounds_present": sorted({m.get("round") for m in msgs if m.get("round") is not None}),
    "fallback_marker_hits": [t for t in contents if FALLBACK in t],
    "preset_exact_hits": [t for t in contents if t.strip() in presets],
    "empty_contents": [i for i, t in enumerate(contents) if not t.strip()],
    "sample_texts": contents[:6],
}

# 等待日志落盘后读取新增部分
time.sleep(1.5)
new_out = read_lines(LOG)[base_out:]
new_err = read_lines(ERR, "utf-8")[base_err:]
joined = "\n".join(new_out)
summary["log_new"] = {
    "out_new_lines": len(new_out),
    "err_new_lines": len(new_err),
    "llm_raw_count": len(re.findall(r"\[DEBUG\].*?LLM raw:", joined)),
    "invalid_port": joined.count("Invalid port"),
    "fallback_text": joined.count("LLM暂时不可用"),
    "api_call_failed": joined.count("API调用失败"),
    "retry_failed": joined.count("重试3次仍失败"),
    "llm_raw_samples": [ln.strip() for ln in new_out if "LLM raw:" in ln][:8],
}
full = "\n".join(read_lines(LOG))
summary["log_full_scan"] = {"invalid_port": full.count("Invalid port"),
                            "fallback_text": full.count("LLM暂时不可用"),
                            "api_call_failed": full.count("API调用失败"),
                            "retry_failed": full.count("重试3次仍失败")}

summary["checks"] = {
    "use_llm_enabled_and_checked": bool(setup.get("useLlm")) and not bool(setup.get("useLlmDisabled")),
    "real_model_text": (not summary["assertions"]["fallback_marker_hits"]
                        and summary["assertions"]["preset_exact_hits"] == []
                        and summary["assertions"]["empty_contents"] == []
                        and summary["log_new"]["llm_raw_count"] >= 3),
    "no_three_errors_in_new_log": (summary["log_new"]["invalid_port"] == 0
                                   and summary["log_new"]["fallback_text"] == 0
                                   and summary["log_new"]["api_call_failed"] == 0),
    "no_three_errors_in_full_log": (summary["log_full_scan"]["invalid_port"] == 0
                                    and summary["log_full_scan"]["fallback_text"] == 0
                                    and summary["log_full_scan"]["api_call_failed"] == 0),
}
with open(EV + r"\nr_llm_run.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
with open(EV + r"\nr_run_5001_out_excerpt.log", "w", encoding="utf-8") as f:
    f.write("\n".join(new_out))
c.close()
print("OK", json.dumps({"session": sid, "db": (summary.get("db_session") or {}).get("id"),
                        "checks": summary["checks"],
                        "llm_raw": summary["log_new"]["llm_raw_count"]}, ensure_ascii=False))

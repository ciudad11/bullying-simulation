# -*- coding: utf-8 -*-
"""任务#23 项7：5001（noreset）复核 —— 首页缓存版本、/js/app.js 响应 MD5、真实 UI 项1与项5各一次。"""
import hashlib
import json
import re
import sys
import time
import traceback

import requests

from cdp import CDP

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:5001"
ROOT = r"D:\bully_simulation_pack_v2_20260713"
EV = ROOT + r"\test_evidence"
DISK_JS = ROOT + r"\noreset\web\js\app.js"
USER = "msg_nr_1109a"
PWD = "MsgTest2026!"
summary = {"base": BASE, "account": USER}
CURRENT_SID = None

SKIP_JS = ("""(() => {const b=Array.from(document.querySelectorAll('button'))"""
           """.find(x=>x.textContent.includes('不需要') && x.offsetParent!==null); """
           """if(b){b.click(); return true;} return false;})()""")

HELPERS = """(() => {
  window.__errs = window.__errs || [];
  if (!window.__errHooked) {
    window.__errHooked = true;
    const oe = console.error.bind(console);
    console.error = function(){ try{window.__errs.push(Array.from(arguments).map(String).join(' '));}catch(e){} oe.apply(console, arguments); };
    window.addEventListener('error', e => window.__errs.push('window.error: ' + String(e.message)));
  }
  return true;
})()"""


def panel():
    return c.eval("""(() => {
      const t=id=>{const e=document.getElementById(id); return e? e.textContent.trim(): null};
      return {round:t('status-round'), remaining:t('check-remaining'), streak:t('check-streak'),
              checkIntensity:t('check-intensity'), checkFrequency:t('check-frequency'),
              badge:t('session-badge')};
    })()""")


def modal_active():
    return bool(c.eval("document.getElementById('autoend-modal').classList.contains('active')"))


def capture_modal():
    return c.eval("""(() => {
      const t=id=>{const e=document.getElementById(id);return e?e.textContent:null};
      return {icon:t('autoend-icon'), title:t('autoend-title'), subtitle:t('autoend-subtitle'), reason:t('autoend-reason'),
              active:document.getElementById('autoend-modal').classList.contains('active'),
              badge:t('session-badge'),
              endMessageCount:(((document.querySelector('#messages-list')||{}).innerText)||'').match(/🛑 实训结束/g)?.length||0,
              lastMessages:Array.from(document.querySelectorAll('.message')).slice(-5).map(e=>e.innerText.replace(/\\s+/g,' ').trim()),
              jsErrors:(window.__errs||[]).slice(-10)};
    })()""")


def start_new(level):
    global CURRENT_SID
    c.eval("document.getElementById('btn-new-session-alt').click()")
    time.sleep(1.0)
    c.eval("""(() => {
      const s=document.getElementById('scenario-select'); s.value='scenario_1';
      s.dispatchEvent(new Event('change',{bubbles:true}));
      document.querySelector('.level-btn[data-level="%d"]').click();
      const cb=document.getElementById('use-llm'); if(cb.checked){cb.checked=false;cb.dispatchEvent(new Event('change',{bubbles:true}));}
      return true;
    })()""" % level)
    time.sleep(0.8)
    c.eval("document.getElementById('btn-start').click()")
    c.wait_js("(((document.querySelector('#messages-list')||{}).innerText)||'').includes('第 1 轮对话')", 180)
    time.sleep(0.8)
    CURRENT_SID = c.eval("typeof state!=='undefined' ? state.sessionId : null")
    return CURRENT_SID


def shot(name):
    try:
        c.eval("window.scrollTo(0,0)")
        c.screenshot(EV + "\\" + name)
    except Exception as e:
        summary.setdefault("screenshot_errors", []).append({"name": name, "error": str(e)})


# ---- 静态检查 ----
home = requests.get(BASE + "/", timeout=30).text
js = requests.get(BASE + "/js/app.js", timeout=30).content
disk = open(DISK_JS, "rb").read()
summary["home_cache"] = re.findall(r"app\.js\?v=\d+", home)
summary["served_appjs_md5"] = hashlib.md5(js).hexdigest()
summary["disk_appjs_md5"] = hashlib.md5(disk).hexdigest()
summary["appjs_matches_disk"] = summary["served_appjs_md5"] == summary["disk_appjs_md5"]

# ---- 登录 ----
r = requests.post(f"{BASE}/api/auth/register",
                  json={"username": USER, "password": PWD, "display_name": "msg复核"}, timeout=30)
if r.status_code == 200:
    token = r.json()["token"]
else:
    r = requests.post(f"{BASE}/api/auth/login", json={"username": USER, "password": PWD}, timeout=30)
    r.raise_for_status()
    token = r.json()["token"]
H = {"Authorization": f"Bearer {token}"}

c = CDP(9334)
c.navigate(BASE, 3.0)
c.eval(HELPERS)
c.wait_js("!!document.getElementById('login-username')||!!document.getElementById('btn-start')", 40)
if not c.eval("!!document.getElementById('btn-logout') && document.getElementById('btn-logout').offsetParent!==null"):
    c.eval("""(() => {
      const set=(id,v)=>{const el=document.getElementById(id); if(!el) return; el.value=v;
        el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));};
      set('login-username', %s); set('login-password', %s);
      document.getElementById('btn-login').click(); return true;
    })()""" % (json.dumps(USER), json.dumps(PWD)))
    c.wait_js("document.getElementById('btn-logout') && document.getElementById('btn-logout').offsetParent!==null", 40)
time.sleep(1.0)

try:
    # ---- 项1 复跑 ----
    sid = start_new(1)
    rounds = []
    for rnd in range(1, 6):
        c.wait_js("(((document.querySelector('#messages-list')||{}).innerText)||'').includes('第 %d 轮对话')" % rnd, 180)
        c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要')&&b.offsetParent!==null) || document.getElementById('autoend-modal').classList.contains('active')", 180)
        time.sleep(0.6)
        if modal_active():
            break
        ui = panel()
        txt = ui.get("remaining") or ""
        nums = [int(x) for x in re.findall(r"\d+", txt)]
        rounds.append({"round": rnd, "ui": ui, "ui_remaining_num": nums[-1] if nums else (0 if txt == "已达结束条件" else None)})
        c.eval(SKIP_JS)
        c.wait_js("document.getElementById('autoend-modal').classList.contains('active') || (((document.querySelector('#messages-list')||{}).innerText)||'').includes('第 %d 轮对话')" % (rnd + 1), 240)
        if modal_active():
            break
    c.wait_js("document.getElementById('autoend-modal').classList.contains('active')", 240)
    time.sleep(1.0)
    m1 = capture_modal()
    summary["item1_recheck"] = {"session_id": sid, "rounds": rounds, "modal": m1}
    expected = ("🎉 恭喜您有效制止了欺凌行为，保护了受害者！\n"
                "温馨提示：欺凌行为已连续 3 轮停止，教师保护与跟进记录不足，按实际干预评分。")
    summary["item1_recheck"]["checks"] = {
        "title": m1["title"] == "🎉 调解成功",
        "subtitle": m1["subtitle"] == "恭喜！您有效制止了欺凌行为，保护了受害者",
        "reason_exact": m1["reason"] == expected,
        "remaining_4_to_0": [x.get("ui_remaining_num") for x in rounds] == [4, 3, 2, 1, 0],
        "no_js_errors": not m1.get("jsErrors"),
    }
    shot("msg_5001_item1.png")

    # ---- 项5 复跑 ----
    c.eval("document.getElementById('autoend-modal').classList.remove('active')")
    sid = start_new(1)
    c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要')&&b.offsetParent!==null)", 180)
    time.sleep(0.5)
    c.eval("document.getElementById('btn-stop').click()")
    c.wait_js("document.getElementById('autoend-modal').classList.contains('active')", 240)
    time.sleep(1.0)
    m5 = capture_modal()
    c.eval("stopSimulation();")
    time.sleep(0.8)
    m5b = capture_modal()
    summary["item5_recheck"] = {"session_id": sid, "modal": m5, "after_repeat": m5b}
    summary["item5_recheck"]["checks"] = {
        "title": m5["title"] == "📋 实训已结束",
        "subtitle": m5["subtitle"] == "您已主动结束本次模拟",
        "reason": m5["reason"] == "报告将基于已有对话与干预数据生成分析，供您复盘参考。",
        "badge": m5["badge"] == "已结束",
        "single_end_message": m5b["endMessageCount"] == 1,
        "no_js_errors": not m5.get("jsErrors"),
    }
    shot("msg_5001_item5.png")
except Exception as e:
    summary["error"] = str(e)
    summary["trace"] = traceback.format_exc()[-3000:]

with open(EV + r"\msg_5001_recheck.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print("DONE", json.dumps({"home": summary.get("home_cache"),
                          "md5ok": summary.get("appjs_matches_disk")}, ensure_ascii=False), flush=True)
c.close()

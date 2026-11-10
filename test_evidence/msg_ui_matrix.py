# -*- coding: utf-8 -*-
"""任务#23：结束弹窗文案真实交互验收（5000，scenario_1，LLM-off，逐项新建会话）。

矩阵：
1) 成功+记录不足（默认配置，level1，5 轮不需要）
2) 干净成功（victim_safety_min=0, follow_up_status_min=0）
3) 失败（level5, victim_crisis_threshold=0.80, victim_crisis_rounds=1）
4) 超时（level1, max_rounds=3）
5) 主动停止（运行中 + 轮间；重复停止；报告按钮）
6) 收敛顺序（best effort：bullying_stopped_rounds=20,max_rounds=30,victim_safety_min=0,follow_up_status_min=0,retaliation_risk_max=1）
"""
import json
import re
import sqlite3
import sys
import time
import traceback

import requests

from cdp import CDP

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:5000"
ROOT = r"D:\bully_simulation_pack_v2_20260713"
EV = ROOT + r"\test_evidence"
DB = ROOT + r"\fin_ver\web\data.db"
USER = "msg_qa_1109a"
PWD = "MsgTest2026!"

summary = {"account": USER, "base": BASE, "items": {}, "js_errors": []}
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
    window.addEventListener('unhandledrejection', e => window.__errs.push('unhandledrejection: ' + String(e.reason)));
  }
  window.modalActive = () => document.getElementById('autoend-modal').classList.contains('active');
  window.convActive = () => getComputedStyle(document.getElementById('convergence-modal')).display !== 'none';
  window.promptVisible = () => Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要')&&b.offsetParent!==null);
  return true;
})()"""


def modal_active():
    return bool(c.eval("document.getElementById('autoend-modal').classList.contains('active')"))


def conv_active():
    return bool(c.eval("getComputedStyle(document.getElementById('convergence-modal')).display!=='none'"))


def prompt_visible():
    return bool(c.eval("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要')&&b.offsetParent!==null)"))


def panel():
    return c.eval("""(() => {
      const t=id=>{const e=document.getElementById(id); return e? e.textContent.trim(): null};
      return {round:t('status-round'), intensity:t('value-intensity'), distress:t('value-distress'),
              frequency:t('value-frequency'), checkIntensity:t('check-intensity'), checkFrequency:t('check-frequency'),
              streak:t('check-streak'), remaining:t('check-remaining'), gap:t('check-gap'),
              badge:t('session-badge')};
    })()""")


def capture_modal():
    return c.eval("""(() => {
      const t=id=>{const e=document.getElementById(id);return e?e.textContent:null};
      const g=id=>{const e=document.getElementById(id);return e?getComputedStyle(e).display:null};
      const errs=window.__errs||[];
      return {icon:t('autoend-icon'), title:t('autoend-title'), subtitle:t('autoend-subtitle'),
              reason:t('autoend-reason'),
              whiteSpace:getComputedStyle(document.getElementById('autoend-reason')).whiteSpace,
              active:document.getElementById('autoend-modal').classList.contains('active'),
              display:g('autoend-modal'), convergenceDisplay:g('convergence-modal'),
              reportModalActive:document.getElementById('report-modal').classList.contains('active'),
              badge:t('session-badge'),
              panel:{round:t('status-round'), remaining:t('check-remaining'), streak:t('check-streak'),
                     intensity:t('value-intensity'), frequency:t('value-frequency')},
              endMessageCount:(((document.querySelector('#messages-list')||{}).innerText)||'').match(/🛑 实训结束/g)?.length||0,
              lastMessages:Array.from(document.querySelectorAll('.message')).slice(-6).map(e=>e.innerText.replace(/\\s+/g,' ').trim()),
              jsErrors:errs.slice(-10)};
    })()""")


def api_status(sid):
    st = requests.get(f"{BASE}/api/session/{sid}", headers=H, timeout=30).json().get("status", {})
    return {k: st.get(k) for k in ("round", "remaining_to_stop", "bullying_stopped_rounds",
                                   "attack_frequency", "attack_frequency_max",
                                   "victim_distress", "victim_crisis_rounds",
                                   "finished", "result_type", "termination_reason")}


def apply_config(sid, cfg):
    r = requests.post(f"{BASE}/api/session/{sid}/auto-end-config", headers=H, json=cfg, timeout=30)
    out = {"status_code": r.status_code, "written": cfg}
    try:
        out["response"] = r.json()
    except Exception:
        out["response"] = r.text
    rb = requests.post(f"{BASE}/api/session/{sid}/auto-end-config", headers=H, json={}, timeout=30)
    out["readback"] = rb.json() if rb.status_code == 200 else rb.text
    return out


def latest_db_sid():
    lst = requests.get(f"{BASE}/api/sessions", headers=H, timeout=30).json().get("sessions", [])
    return lst[0].get("id") if lst else None


def db_check(sid):
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    srow = conn.execute("select id,status,result_type,auto_end_reason,completed_at from sessions where id=?",
                        (sid,)).fetchone()
    rrows = conn.execute("select id,report_json from reports where session_id=?", (sid,)).fetchall()
    conn.close()
    out = {"session_row": dict(srow) if srow else None, "report_row_count": len(rrows)}
    if rrows:
        snap = json.loads(rrows[0]["report_json"])
        m = snap.get("metrics", {})
        rounds = sorted({x.get("round") for x in snap.get("messages", []) if x.get("round") is not None})
        out["report_snapshot"] = {"metrics_round": m.get("round"), "finished": m.get("finished"),
                                  "result_type": m.get("result_type"),
                                  "termination_reason": m.get("termination_reason"),
                                  "message_rounds": rounds, "total_rounds": snap.get("total_rounds")}
    return out


def wait_round_or_modal(rnd, timeout=180):
    c.wait_js("modalActive() || convActive() || (((document.querySelector('#messages-list')||{}).innerText)||'').includes('第 %d 轮对话')" % rnd,
              timeout)


def drive(max_rounds=10, stop_on_conv=True):
    """点击「不需要」推进；返回 (rounds, end_kind, at_round)。"""
    rounds = []
    rnd = 1
    while rnd <= max_rounds:
        wait_round_or_modal(rnd)
        if modal_active():
            return rounds, "modal_before_round", rnd - 1
        c.wait_js("modalActive() || convActive() || Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要')&&b.offsetParent!==null)", 180)
        time.sleep(0.6)
        if conv_active():
            return rounds, "convergence", rnd
        if modal_active():
            return rounds, "modal", rnd
        rec = {"round": rnd, "ui": panel(), "api": api_status(CURRENT_SID)}
        txt = rec["ui"].get("remaining") or ""
        nums = [int(x) for x in re.findall(r"\d+", txt)]
        rec["ui_remaining_num"] = nums[-1] if nums else (0 if txt == "已达结束条件" else None)
        rounds.append(rec)
        if not c.eval(SKIP_JS):
            return rounds, "skip_button_missing", rnd
        c.wait_js("modalActive() || convActive() || (((document.querySelector('#messages-list')||{}).innerText)||'').includes('第 %d 轮对话')" % (rnd + 1), 240)
        if modal_active():
            return rounds, "modal_after_skip", rnd + 1
        if conv_active() and stop_on_conv:
            return rounds, "convergence_after_skip", rnd
        rnd += 1
    return rounds, "max_rounds", max_rounds


def start_new(level):
    c.eval("""(() => {
      document.getElementById('autoend-modal').classList.remove('active');
      const cm=document.getElementById('convergence-modal'); if(cm) cm.style.display='none';
      document.getElementById('report-modal').classList.remove('active');
      return true;
    })()""")
    time.sleep(0.4)
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
    assert c.eval("document.getElementById('btn-start').offsetParent!==null"), "start not visible"
    c.eval("document.getElementById('btn-start').click()")
    c.wait_js("(((document.querySelector('#messages-list')||{}).innerText)||'').includes('第 1 轮对话')", 180)
    time.sleep(0.8)
    global CURRENT_SID
    CURRENT_SID = c.eval("typeof state!=='undefined' ? state.sessionId : null")
    return CURRENT_SID


def finish_capture(sid):
    modal = capture_modal()
    api = api_status(sid)
    return {"modal": modal, "api": api}


def shot(name):
    try:
        c.eval("window.scrollTo(0,0)")
        c.screenshot(EV + "\\" + name)
    except Exception as e:
        summary["items"].setdefault("screenshot_errors", []).append({"name": name, "error": str(e)})


def guard(item_id, fn):
    try:
        fn()
        print("[OK]", item_id, flush=True)
    except Exception as e:
        summary["items"][item_id] = {"error": str(e), "trace": traceback.format_exc()[-3000:]}
        print("[FAIL]", item_id, e, flush=True)
    finally:
        try:
            with open(EV + r"\msg_ui_matrix.json", "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


# ---------------- login / register ----------------
r = requests.post(f"{BASE}/api/auth/register",
                  json={"username": USER, "password": PWD, "display_name": "msg验收"}, timeout=30)
if r.status_code == 200:
    token = r.json()["token"]
else:
    r = requests.post(f"{BASE}/api/auth/login", json={"username": USER, "password": PWD}, timeout=30)
    r.raise_for_status()
    token = r.json()["token"]
H = {"Authorization": f"Bearer {token}"}
summary["register_status"] = r.status_code

c = CDP(9333)
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
summary["logged_in"] = True
summary["home_cache"] = c.eval("Array.from(document.scripts).map(s=>s.src).filter(x=>x.includes('app.js'))")

# ---------------- 项1 成功+记录不足 ----------------
def item1():
    sid = start_new(1)
    summary["items"]["item1"] = {"session_id": sid,
                                 "setup": c.eval("""(() => ({scenario:document.getElementById('scenario-select').value,
                                    level1:document.querySelector('.level-btn[data-level="1"]').classList.contains('active'),
                                    useLlm:document.getElementById('use-llm').checked}))()""")}
    rounds, kind, at = drive(max_rounds=8)
    if not modal_active():
        c.wait_js("modalActive()", 240)
    time.sleep(1.2)
    cap = finish_capture(sid)
    db_sid = latest_db_sid()
    summary["items"]["item1"].update({"rounds": rounds, "end_kind": kind, "at_round": at,
                                      "modal": cap["modal"], "api": cap["api"],
                                      "db_session_id": db_sid, "db": db_check(db_sid)})
    m = cap["modal"]
    expected_reason = ("🎉 恭喜您有效制止了欺凌行为，保护了受害者！\n"
                       "温馨提示：欺凌行为已连续 3 轮停止，教师保护与跟进记录不足，按实际干预评分。")
    seq = [x.get("ui_remaining_num") for x in rounds]
    final_zero = m["panel"].get("remaining") == "已达结束条件"
    checks = {
        "title": m["title"] == "🎉 调解成功",
        "subtitle": m["subtitle"] == "恭喜！您有效制止了欺凌行为，保护了受害者",
        "reason_exact": m["reason"] == expected_reason,
        "remaining_4_to_0": seq == [4, 3, 2, 1] and final_zero,
        "end_round5": cap["api"].get("round") == 5,
    }
    db = summary["items"]["item1"]["db"]
    srow = db.get("session_row") or {}
    snap = db.get("report_snapshot") or {}
    checks.update({
        "db_completed_success_reason": srow.get("status") == "completed" and srow.get("result_type") == "success" and bool(srow.get("auto_end_reason")),
        "report_single_row": db.get("report_row_count") == 1,
        "report_round5_finished": snap.get("metrics_round") == 5 and snap.get("finished") is True,
        "report_messages_1_to_5": all(r in (snap.get("message_rounds") or []) for r in (1, 2, 3, 4, 5)),
        "final_dialogue_marker": any("第 5 轮对话" in x or "🛑 实训结束" in x for x in m.get("lastMessages", [])),
        "no_js_errors": not m.get("jsErrors"),
    })
    summary["items"]["item1"]["checks"] = checks
    shot("msg_item1_success_reminder.png")


# ---------------- 项2 干净成功 ----------------
def item2():
    sid = start_new(1)
    cfg = {"victim_safety_min": 0, "follow_up_status_min": 0}
    applied = apply_config(sid, cfg)
    summary["items"]["item2"] = {"session_id": sid, "config": applied}
    rounds, kind, at = drive(max_rounds=8)
    if not modal_active():
        c.wait_js("modalActive()", 240)
    time.sleep(1.2)
    cap = finish_capture(sid)
    m = cap["modal"]
    summary["items"]["item2"].update({"rounds": rounds, "end_kind": kind, "at_round": at,
                                      "modal": m, "api": cap["api"]})
    expected_reason = "🎉 恭喜您有效制止了欺凌行为，保护了受害者！"
    checks = {
        "config_applied": (applied.get("response") or {}).get("applied") == cfg
                           and (applied.get("readback") or {}).get("convergence", {}).get("victim_safety_min") == 0
                           and (applied.get("readback") or {}).get("convergence", {}).get("follow_up_status_min") == 0,
        "title": m["title"] == "🎉 调解成功",
        "subtitle": m["subtitle"] == "恭喜！您有效制止了欺凌行为，保护了受害者",
        "reason_exact_pure": m["reason"] == expected_reason,
        "no_reminder": "温馨提示" not in (m["reason"] or ""),
        "end_round5": cap["api"].get("round") == 5,
        "no_js_errors": not m.get("jsErrors"),
    }
    summary["items"]["item2"]["checks"] = checks
    shot("msg_item2_clean_success.png")


# ---------------- 项3 失败 ----------------
def item3():
    sid = start_new(5)
    cfg = {"victim_crisis_threshold": 0.80, "victim_crisis_rounds": 1}
    applied = apply_config(sid, cfg)
    summary["items"]["item3"] = {"session_id": sid, "config": applied}
    rounds, kind, at = drive(max_rounds=8)
    if not modal_active():
        c.wait_js("modalActive()", 240)
    time.sleep(1.2)
    cap = finish_capture(sid)
    m = cap["modal"]
    summary["items"]["item3"].update({"rounds": rounds, "end_kind": kind, "at_round": at,
                                      "modal": m, "api": cap["api"]})
    expected_reason = "建议复盘干预时机与方式，调整策略后再试一次！\n事实原因：受害者压力过大，调解失败。"
    checks = {
        "config_applied": (applied.get("response") or {}).get("applied") == cfg,
        "title": m["title"] == "💪 调解未成功",
        "subtitle": m["subtitle"] == "别灰心，每次尝试都是宝贵的经验",
        "reason_exact": m["reason"] == expected_reason,
        "api_failure": cap["api"].get("result_type") == "failure",
        "no_js_errors": not m.get("jsErrors"),
    }
    summary["items"]["item3"]["checks"] = checks
    shot("msg_item3_failure.png")


# ---------------- 项4 超时 ----------------
def item4():
    sid = start_new(1)
    cfg = {"max_rounds": 3}
    applied = apply_config(sid, cfg)
    summary["items"]["item4"] = {"session_id": sid, "config": applied}
    rounds, kind, at = drive(max_rounds=6)
    if not modal_active():
        c.wait_js("modalActive()", 240)
    time.sleep(1.2)
    cap = finish_capture(sid)
    m = cap["modal"]
    summary["items"]["item4"].update({"rounds": rounds, "end_kind": kind, "at_round": at,
                                      "modal": m, "api": cap["api"]})
    checks = {
        "config_applied": (applied.get("response") or {}).get("applied") == cfg,
        "title": m["title"] == "⏰ 本轮模拟结束",
        "subtitle": m["subtitle"] == "对话轮次已达上限",
        "reason_exact": m["reason"] == "报告将基于已有数据生成分析，欢迎调整策略再试一次！",
        "end_round3": cap["api"].get("round") == 3,
        "api_timeout": cap["api"].get("result_type") == "timeout",
        "no_js_errors": not m.get("jsErrors"),
    }
    summary["items"]["item4"]["checks"] = checks
    shot("msg_item4_timeout.png")


# ---------------- 项5 主动停止 ----------------
def stop_and_capture(sid, repeat_js):
    c.wait_js("modalActive()", 240)
    time.sleep(1.0)
    cap = finish_capture(sid)
    # 重复停止（结束后再触发一次）
    c.eval(repeat_js)
    time.sleep(1.0)
    cap2 = capture_modal()
    # 报告按钮可用
    c.eval("document.getElementById('autoend-modal').classList.remove('active')")
    time.sleep(0.4)
    c.eval("document.getElementById('btn-view-report').click()")
    c.wait_js("document.getElementById('report-modal').classList.contains('active')", 60)
    report_open = c.eval("document.getElementById('report-modal').classList.contains('active')")
    return {"first": cap, "after_repeat": cap2, "report_modal_open": report_open}


def item5():
    out = {}
    # 5a：轮次运行中停止
    sid = start_new(1)
    c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要')&&b.offsetParent!==null)", 180)
    c.eval(SKIP_JS)
    c.wait_js("typeof state!=='undefined' && state.roundInProgress===true", 30)
    time.sleep(0.3)
    c.eval("document.getElementById('btn-stop').click()")
    c.eval("document.getElementById('btn-stop').click()")
    a = stop_and_capture(sid, "stopSimulation();")
    out["during_round"] = {"session_id": sid, **a}
    # 5b：轮间停止
    sid = start_new(1)
    c.wait_js("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('不需要')&&b.offsetParent!==null)", 180)
    time.sleep(0.5)
    c.eval("document.getElementById('btn-stop').click()")
    b = stop_and_capture(sid, "stopSimulation(); stopSimulation();")
    out["between_rounds"] = {"session_id": sid, **b}
    summary["items"]["item5"] = out
    checks = {}
    for key in ("during_round", "between_rounds"):
        d = out[key]
        m = d["first"]["modal"]
        checks[key + "_title"] = m["title"] == "📋 实训已结束"
        checks[key + "_subtitle"] = m["subtitle"] == "您已主动结束本次模拟"
        checks[key + "_reason"] = m["reason"] == "报告将基于已有对话与干预数据生成分析，供您复盘参考。"
        checks[key + "_badge"] = m["badge"] == "已结束"
        checks[key + "_single_end_message"] = d["after_repeat"]["endMessageCount"] == 1
        checks[key + "_report_usable"] = d["report_modal_open"] is True
        checks[key + "_no_js_errors"] = not m.get("jsErrors")
    summary["items"]["item5"]["checks"] = checks
    shot("msg_item5_active_stop.png")


# ---------------- 项6 收敛顺序（best effort） ----------------
def item6():
    sid = start_new(1)
    cfg = {"bullying_stopped_rounds": 20, "max_rounds": 30, "victim_safety_min": 0,
           "follow_up_status_min": 0, "retaliation_risk_max": 1}
    applied = apply_config(sid, cfg)
    out = {"session_id": sid, "config": applied, "attempt": "prescribed keys"}
    try:
        rounds, kind, at = drive(max_rounds=10, stop_on_conv=True)
        out.update({"rounds": rounds, "end_kind": kind, "at_round": at})
        if conv_active():
            conv = c.eval("""(() => ({title:document.getElementById('convergence-title').textContent,
                 message:document.getElementById('convergence-message').textContent,
                 reportBtn:getComputedStyle(document.getElementById('btn-convergence-report')).display}))()""")
            out["convergence"] = conv
            shot("msg_item6_convergence.png")
            c.eval("document.getElementById('btn-convergence-report').click()")
            c.wait_js("modalActive()", 240)
            time.sleep(1.2)
            cap = finish_capture(sid)
            out["after_view_report"] = cap
            out["ordering"] = {
                "convergence_hidden": c.eval("getComputedStyle(document.getElementById('convergence-modal')).display") == "none",
                "autoend_active": cap["modal"]["active"],
                "report_not_stacked": cap["modal"]["reportModalActive"] is False,
                "single_end_message": cap["modal"]["endMessageCount"] == 1,
                "title": cap["modal"]["title"],
                "subtitle": cap["modal"]["subtitle"],
                "reason": cap["modal"]["reason"],
            }
        else:
            out["convergence"] = None
            if modal_active():
                out["autoend"] = finish_capture(sid)
            else:
                # 未触发收敛：主动停止收尾，避免遗留运行会话
                c.eval("document.getElementById('btn-stop').click()")
                c.wait_js("modalActive()", 120)
                out["autoend"] = finish_capture(sid)
    finally:
        # 兜底：若仍无结束弹窗，主动停止
        if not modal_active():
            try:
                c.eval("document.getElementById('btn-stop').click()")
                c.wait_js("modalActive()", 120)
            except Exception:
                pass
        summary["items"]["item6"] = out
        shot("msg_item6_after_report.png")


guard("item1", item1)
guard("item2", item2)
guard("item3", item3)
guard("item4", item4)
guard("item5", item5)
guard("item6", item6)

summary["session_ids"] = {k: (v.get("session_id") if isinstance(v, dict) else None)
                          for k, v in summary["items"].items()}
summary["js_errors"] = c.eval("window.__errs||[]")
with open(EV + r"\msg_ui_matrix.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print("DONE", json.dumps(summary.get("session_ids"), ensure_ascii=False), flush=True)
c.close()

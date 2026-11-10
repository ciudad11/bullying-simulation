# -*- coding: utf-8 -*-
"""Task #22 lightweight headless smoke: verify end-of-simulation modal copy.

Read-only against 5000 (no session starts, no DB writes; apiCall is stubbed in page).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from cdp import CDP  # noqa: E402

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9444
PROFILE = os.path.join(tempfile.gettempdir(), "edge_task22_smoke_%d" % int(time.time()))
BASE = "http://127.0.0.1:5000"

proc = subprocess.Popen([
    EDGE,
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--remote-debugging-port=%d" % PORT,
    "--remote-allow-origins=*",
    "--user-data-dir=%s" % PROFILE,
    "about:blank",
])

result = {"checks": {}, "console": []}
try:
    cdp = CDP(PORT)
    cdp.navigate(BASE, wait=2.0)
    ready = cdp.wait_js("typeof finalizeStop === 'function' && typeof stopSimulation === 'function' && typeof apiCall === 'function'", timeout=40)
    result["checks"]["page_ready"] = ready

    # stub network so finalizeStop's GET /report does not touch backend
    cdp.eval("window.apiCall = async function(){ return {}; };")
    result["checks"]["api_stubbed"] = cdp.eval("(async()=>{const r = await apiCall('/x'); return !!r && Object.keys(r).length === 0;})()")

    cdp.eval(r"""
    window.__smoke = async function(autoEnded, reason, type, userStopped) {
        const modal = document.getElementById('autoend-modal');
        modal.classList.remove('active');
        const conv = document.getElementById('convergence-modal');
        if (conv) conv.style.display = 'none';
        await finalizeStop(autoEnded, reason, type, userStopped);
        return {
            icon: document.getElementById('autoend-icon').textContent,
            title: document.getElementById('autoend-title').textContent,
            subtitle: document.getElementById('autoend-subtitle').textContent,
            reason: document.getElementById('autoend-reason').textContent,
            active: modal.classList.contains('active'),
            whiteSpace: getComputedStyle(document.getElementById('autoend-reason')).whiteSpace
        };
    };
    window.__stopSmoke = async function() {
        const modal = document.getElementById('autoend-modal');
        modal.classList.remove('active');
        state.sessionId = 'smoke-session';
        state.isRunning = true;
        state.isStopping = false;
        state.roundInProgress = false;
        state.abortController = null;
        window.__endMsgCount = 0;
        const orig = window.addSystemMessage;
        window.addSystemMessage = function(c) {
            if (String(c).indexOf('实训结束') >= 0) window.__endMsgCount++;
            return orig(c);
        };
        await stopSimulation();
        const first = {
            icon: document.getElementById('autoend-icon').textContent,
            title: document.getElementById('autoend-title').textContent,
            subtitle: document.getElementById('autoend-subtitle').textContent,
            reason: document.getElementById('autoend-reason').textContent,
            active: modal.classList.contains('active'),
            endMessages: window.__endMsgCount
        };
        // duplicate click after ended: guard should prevent a second finalizeStop
        await stopSimulation();
        const second = { endMessages: window.__endMsgCount, active: modal.classList.contains('active') };
        window.addSystemMessage = orig;
        return { first: first, second: second };
    };
    window.__stackSmoke = async function() {
        const conv = document.getElementById('convergence-modal');
        const modal = document.getElementById('autoend-modal');
        conv.style.display = 'flex';
        modal.classList.remove('active');
        await finalizeStop(true, '其它原因', '', false);
        return {
            convergenceDisplay: conv.style.display,
            autoendActive: modal.classList.contains('active')
        };
    };
    """)
    result["checks"]["helpers_defined"] = cdp.eval("typeof window.__smoke === 'function' && typeof window.__stopSmoke === 'function' && typeof window.__stackSmoke === 'function'")

    scenarios = {
        "success_clean": ("true", "'欺凌行为已连续3轮被有效制止，调解成功'", "'success'", "false"),
        "success_with_reminder": ("true", "'欺凌强度持续下降'", "'success'", "false"),
        "failure": ("true", "'干预未达阈值，欺凌仍未停止'", "'failure'", "false"),
        "timeout": ("true", "'达到最大轮次'", "'timeout'", "false"),
        "unknown_fallback": ("true", "'其它终止原因'", "''", "false"),
        "user_stop_branch": ("false", "''", "''", "true"),
    }
    for name, (a, r, t, u) in scenarios.items():
        result["checks"][name] = cdp.eval("(async()=>await window.__smoke(%s, %s, %s, %s))()" % (a, r, t, u))

    result["checks"]["stop_button_flow"] = cdp.eval("(async()=>await window.__stopSmoke())()")
    result["checks"]["no_stack"] = cdp.eval("(async()=>await window.__stackSmoke())()")
finally:
    try:
        cdp.close()
    except Exception:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    shutil.rmtree(PROFILE, ignore_errors=True)

with open(os.path.join(HERE, "task22_modal_smoke.json"), "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(json.dumps(result, ensure_ascii=False, indent=2))

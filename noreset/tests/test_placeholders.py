#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R28 占位符与风险去重持久化测试

覆盖：
1. `_fill_placeholders` 把预设占位符替换为当前会话真实姓名（欺凌者/受害者/旁观者）；
2. 未识别的占位符原样保留（不误替换）；
3. 不含占位符时内容原样返回；
4. 干预记录写入 `placeholder_filled` 明细，便于教师核对；
5. 风险副作用去重集合写回 session_data（随日志持久化，state 重建后可恢复）。

运行：
    python tests/test_placeholders.py
"""

import io
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "web"))

RESULTS = []


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


def make_session_manager():
    import database
    database.init_db()
    from session_manager import SessionManager
    sm = SessionManager.__new__(SessionManager)
    sm.runner = None
    sm.session_data = {}
    return sm


class _Mem:
    def __init__(self, name):
        self.name = name


class _State:
    def __init__(self):
        self.agent_memories = {"bully": _Mem("陈浩"), "victim": _Mem("思远"),
                              "bystander_a": _Mem("林雨桐"), "bystander_b": _Mem("赵磊")}
        self.applied_risk_effects = set()


@test("R28 占位符替换为当前会话真实姓名")
def t_fill_placeholders():
    sm = make_session_manager()
    sm.runner = type("R", (), {"state": _State()})()
    text = "【欺凌者】，你刚才嘲笑【受害者】的画；【旁观者】你也看到了。"
    out, filled = sm._fill_placeholders(text)
    assert "陈浩" in out and "思远" in out and "林雨桐" in out, out
    assert "【欺凌者】" not in out and "【受害者】" not in out, out
    assert len(filled) == 3, filled


@test("R28 未识别占位符原样保留")
def t_keep_unknown():
    sm = make_session_manager()
    sm.runner = type("R", (), {"state": _State()})()
    text = "【受害者】，你安全吗？【未知角色】"
    out, filled = sm._fill_placeholders(text)
    assert "【未知角色】" in out, out
    assert len(filled) == 1, filled


@test("R28 不含占位符时原样返回")
def t_no_placeholder():
    sm = make_session_manager()
    sm.runner = type("R", (), {"state": _State()})()
    text = "陈浩，请立刻停止嘲笑思远的画。"
    out, filled = sm._fill_placeholders(text)
    assert out == text and filled == [], (out, filled)


@test("R28 端到端：占位符被自动替换、干预记录含明细、去重集合已持久化")
def t_end_to_end():
    import database
    database.init_db()
    from fastapi.testclient import TestClient
    import app as webapp

    client = TestClient(webapp.app)
    uname = f"ph_{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"username": uname, "password": "pass1234",
                                                "display_name": "P"})
    token = r.json().get("token", "")
    auth = {"Authorization": f"Bearer {token}"}
    r = client.post("/api/session", json={"level": 1, "max_rounds": 3, "use_llm": False,
                                          "participant_ids": ["bully", "victim", "bystander_a", "bystander_b"],
                                          "participants_data": []}, headers=auth)
    sid = r.json().get("session_id", "")
    assert sid, r.text
    client.post(f"/api/session/{sid}/run", json={}, headers=auth)
    content = "【欺凌者】，你刚才嘲笑【受害者】的画，这是言语欺凌，我明确不接受，请立刻停止。"
    r = client.post(f"/api/session/{sid}/intervene",
                    json={"content": content, "client_token": "ph1"}, headers=auth)
    assert r.status_code == 200, r.text
    data = r.json()
    sess = webapp.sessions.get(sid)
    rec = (sess.session_data.get("interventions") or [{}])[-1]
    sent = rec.get("content", "")
    assert "【欺凌者】" not in sent and "【受害者】" not in sent, f"占位符未被替换：{sent}"
    assert rec.get("placeholder_filled"), f"缺少替换明细：{rec}"
    # 风险去重集合已写回 session_data（list，可 JSON 持久化）
    assert isinstance(sess.session_data.get("applied_risk_effects"), list), \
        sess.session_data.get("applied_risk_effects")


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in RESULTS:
        try:
            fn()
            print(f"  [PASS] {name}")
            passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {name}: {e}")
            failed += 1
        except Exception as e:  # noqa
            print(f"  [ERROR] {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n=== 占位符与去重持久化测试：{passed} 通过 / {failed} 失败 ===")
    sys.exit(1 if failed else 0)

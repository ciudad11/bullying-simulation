# -*- coding: utf-8 -*-
"""
R12 干预发送反馈机制测试（接口级，不依赖真实 LLM）

验证点：
1. 首次发送返回明确的发送状态字段（send_status=sent / sent_at / content）；
2. 同一 client_token 在 TTL 内重复提交 → send_status=duplicate，且只产生一条干预记录；
3. 不同 token 视为新的干预；
4. 空内容返回 400（明确的失败反馈，不产生干预）。

运行：
    python web/_test_intervene_send.py
"""

import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

import database  # noqa: E402

database.init_db()

from fastapi.testclient import TestClient  # noqa: E402
import app as webapp  # noqa: E402

client = TestClient(webapp.app)
RESULTS = []


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


@test("R12 首次发送返回明确的发送状态字段")
def t_first_send_status():
    uname = f"send_{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"username": uname, "password": "pass1234", "display_name": "T"})
    token = r.json().get("token", "")
    r = client.post("/api/session",
                    json={"level": 2, "max_rounds": 3, "use_llm": False,
                          "participant_ids": ["bully", "victim", "bystander_a", "bystander_b"],
                          "participants_data": []},
                    headers={"Authorization": f"Bearer {token}"})
    sid = r.json().get("session_id", "")
    assert sid, r.text

    r = client.post(f"/api/session/{sid}/intervene",
                    json={"content": "立刻停止嘲笑，并检查思远是否安全。",
                          "client_token": "tok_" + uuid.uuid4().hex[:8]},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["send_status"] == "sent", data
    assert data["sent_at"], data
    assert data["content"] == "立刻停止嘲笑，并检查思远是否安全。"
    assert data["duplicate"] is False
    assert data["message"]["role"] == "teacher"
    assert data["intervention"]["send_status"] == "sent"
    return sid, token, uname


@test("R12 同一 token 重复提交 → duplicate 且只产生一条干预记录")
def t_duplicate_send():
    uname = f"dup_{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"username": uname, "password": "pass1234", "display_name": "T"})
    token = r.json().get("token", "")
    r = client.post("/api/session",
                    json={"level": 2, "max_rounds": 3, "use_llm": False,
                          "participant_ids": ["bully", "victim", "bystander_a", "bystander_b"],
                          "participants_data": []},
                    headers={"Authorization": f"Bearer {token}"})
    sid = r.json().get("session_id", "")
    ctoken = "tok_" + uuid.uuid4().hex[:8]
    body = {"content": "请立刻停止对他人的嘲笑。", "client_token": ctoken}

    first = client.post(f"/api/session/{sid}/intervene", json=body,
                        headers={"Authorization": f"Bearer {token}"}).json()
    assert first["send_status"] == "sent", first

    second = client.post(f"/api/session/{sid}/intervene", json=body,
                         headers={"Authorization": f"Bearer {token}"}).json()
    assert second["send_status"] == "duplicate", second
    assert second["duplicate"] is True
    # 消息不应重复写入
    assert second["message"]["id"] == first["message"]["id"], (first["message"]["id"], second["message"]["id"])

    # 会话中只应有一条干预记录
    r = client.get(f"/api/session/{sid}/report", headers={"Authorization": f"Bearer {token}"})
    interventions = r.json().get("interventions", [])
    assert len(interventions) == 1, [iv.get("content") for iv in interventions]

    # 不同 token → 视为新的干预
    third = client.post(f"/api/session/{sid}/intervene",
                        json={"content": "请立刻停止对他人的嘲笑。", "client_token": "tok_" + uuid.uuid4().hex[:8]},
                        headers={"Authorization": f"Bearer {token}"}).json()
    assert third["send_status"] == "sent", third


@test("R12 空内容返回 400（失败反馈明确，不产生干预）")
def t_empty_content():
    uname = f"empty_{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"username": uname, "password": "pass1234", "display_name": "T"})
    token = r.json().get("token", "")
    r = client.post("/api/session",
                    json={"level": 2, "max_rounds": 3, "use_llm": False,
                          "participant_ids": ["bully", "victim", "bystander_a", "bystander_b"],
                          "participants_data": []},
                    headers={"Authorization": f"Bearer {token}"})
    sid = r.json().get("session_id", "")
    r = client.post(f"/api/session/{sid}/intervene", json={"content": "   "},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400, r.text
    assert "empty" in r.text.lower() or "cannot" in r.text.lower()


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
    print(f"\n=== 发送反馈测试：{passed} 通过 / {failed} 失败 ===")
    sys.exit(1 if failed else 0)

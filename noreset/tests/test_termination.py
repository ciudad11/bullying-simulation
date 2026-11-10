#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R26 自动停止规则测试（_check_termination）

覆盖：
1. 欺凌行为已停止（无干预）→ 连续 bully_stop_threshold 轮后自动结束（R26 修复的出口）；
2. 攻击仍在进行（attack_frequency / bullying_intensity 超阈值）→ 不结束且计数归零；
3. 教师干预有效（victim_safety / follow_up_status 达标）→ 结束文案为「调解成功」；
4. 压力平息阈值（victim_distress）参与行为停止判定；
5. 受害者危机路径不受影响（victim_distress >= 0.95 连续 3 轮 → failure）；
6. max_rounds 超时路径不受影响。

运行：
    python tests/test_termination.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
# R29：P1（session_manager）与 P2（app 校验函数）测试需要 web 目录可导入
sys.path.insert(0, os.path.join(ROOT, "web"))

from multi_agent.state import SharedState          # noqa: E402
from multi_agent.supervisor import _check_termination, check_termination_once  # noqa: E402

RESULTS = []


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


def make_state(**kw):
    st = SharedState()
    st.config = kw.pop("config", {})
    for k, v in kw.items():
        setattr(st, k, v)
    return st


@test("R26 欺凌行为已停止（无干预）→ 连续 3 轮后自动结束")
def t_stop_without_intervention():
    st = make_state(round=5, attack_frequency=0.10, bullying_intensity=0.12,
                    victim_distress=0.05, retaliation_risk=0.0,
                    victim_safety=0.0, follow_up_status=0.0)
    for i in range(3):
        r = _check_termination(st)
        if i < 2:
            assert not r["should_end"], f"第 {i + 1} 轮不应结束"
    assert r["should_end"], "连续 3 轮行为停止后应结束"
    assert r["result_type"] == "success"
    assert "教师保护与跟进记录不足" in r["reason"], r["reason"]


@test("R26 攻击仍在进行 → 不结束且计数归零")
def t_no_stop_when_attacking():
    st = make_state(round=5, attack_frequency=0.30, bullying_intensity=0.30,
                    victim_distress=0.10, retaliation_risk=0.0,
                    victim_safety=0.8, follow_up_status=1.0)
    # 先累计 2 轮达标计数
    st.attack_frequency, st.bullying_intensity = 0.10, 0.10
    _check_termination(st)
    _check_termination(st)
    assert st.bullying_stopped_rounds == 2
    # 攻击回升 → 计数应归零且不结束
    st.attack_frequency, st.bullying_intensity = 0.30, 0.30
    r = _check_termination(st)
    assert not r["should_end"], "攻击进行中不应结束"
    assert st.bullying_stopped_rounds == 0, "计数应归零"


@test("R26 教师干预有效 → 结束文案为「调解成功」")
def t_stop_with_intervention():
    st = make_state(round=5, attack_frequency=0.10, bullying_intensity=0.12,
                    victim_distress=0.05, retaliation_risk=0.0,
                    victim_safety=0.75, follow_up_status=0.8)
    for _ in range(3):
        r = _check_termination(st)
    assert r["should_end"], "干预有效且行为停止应结束"
    assert r["result_type"] == "success"
    assert "调解成功" in r["reason"], r["reason"]
    assert "记录不足" not in r["reason"], "干预有效时不应提示记录不足"


@test("R27 压力未平息：仍应停止，但文案提示「压力未恢复」")
def t_stop_when_distressed():
    st = make_state(round=5, attack_frequency=0.10, bullying_intensity=0.05,
                    victim_distress=0.60, retaliation_risk=0.0,
                    victim_safety=0.0, follow_up_status=0.0)
    for _ in range(3):
        r = _check_termination(st)
    assert r["should_end"], "欺凌行为停止即应结束（压力未恢复不阻塞停止）"
    assert r["result_type"] == "success"
    assert "压力仍未恢复" in r["reason"], r["reason"]


@test("R27 报复风险高：仍应停止，但文案提示「注意隔离与跟进」")
def t_stop_when_retaliation():
    st = make_state(round=5, attack_frequency=0.10, bullying_intensity=0.05,
                    victim_distress=0.0, retaliation_risk=0.35,
                    victim_safety=0.0, follow_up_status=0.0)
    for _ in range(3):
        r = _check_termination(st)
    assert r["should_end"], "报复风险不阻塞停止"
    assert "报复风险" in r["reason"], r["reason"]


@test("R26 受害者危机路径不受影响（压力超阈值连续 3 轮 → failure）")
def t_crisis_unchanged():
    st = make_state(round=5, victim_distress=0.97)
    for i in range(3):
        r = _check_termination(st)
    assert r["should_end"], "危机 3 轮应结束"
    assert r["result_type"] == "failure", r


@test("R26 超时路径不受影响（round >= max_rounds → timeout）")
def t_timeout_unchanged():
    st = make_state(round=40, max_rounds=40, attack_frequency=0.30)
    r = _check_termination(st)
    assert r["should_end"] and r["result_type"] == "timeout", r


@test("R26 min_rounds 保护：前几轮行为停止不提前结束")
def t_min_rounds_guard():
    st = make_state(round=1, attack_frequency=0.10, bullying_intensity=0.12,
                    victim_distress=0.0, retaliation_risk=0.0,
                    victim_safety=0.0, follow_up_status=0.0)
    for _ in range(5):
        r = _check_termination(st)
    assert not r["should_end"], "round < min_rounds 时行为停止计数不触发结束"


@test("R28 隐藏门槛回显：B<0.20 且 F>=0.20 → 不结束，状态接口可读 F 与计数")
def t_hidden_frequency_gate_visible():
    st = make_state(round=5, attack_frequency=0.21,
                    attack_severity=0.15, persistence=0.15)
    st.bullying_intensity = st.derive_bullying_intensity()
    assert st.bullying_intensity < 0.20, st.bullying_intensity  # 仅看 B 会误以为达标

    for _ in range(6):
        r = _check_termination(st)
    assert not r["should_end"], "攻击频率未达标时不应结束"
    assert st.bullying_stopped_rounds == 0

    summary = st.to_summary_dict()
    required_keys = [
        "attack_frequency", "attack_severity", "persistence",
        "victim_safety", "follow_up_status", "retaliation_risk",
        "bullying_stopped_rounds", "bullying_stopped_required",
        "attack_frequency_max", "bullying_intensity_max", "remaining_to_stop",
    ]
    for key in required_keys:
        assert key in summary, f"状态接口缺少字段 {key}"
    assert summary["attack_frequency"] == 0.21
    assert summary["bullying_stopped_rounds"] == 0
    assert summary["remaining_to_stop"] == summary["bullying_stopped_required"]


@test("R28 同一轮快照重复判定，连续计数不翻倍")
def t_same_snapshot_no_double_count():
    st = make_state(round=5, attack_frequency=0.10, bullying_intensity=0.10,
                    victim_distress=0.0)
    # 轮首判定（上一轮快照）→ 计 1 次
    r1 = check_termination_once(st, snapshot_round=st.round - 1)
    # 同一快照被重复判定（例如重试/重复调用）→ 不再累加
    r2 = check_termination_once(st, snapshot_round=st.round - 1)
    assert st.bullying_stopped_rounds == 1, st.bullying_stopped_rounds
    assert not r1["should_end"] and not r2["should_end"]
    assert r2.get("skipped_duplicate_snapshot") is True

    # 评估后（本轮快照）→ 再计 1 次，共 2 次
    check_termination_once(st, snapshot_round=st.round)
    assert st.bullying_stopped_rounds == 2, st.bullying_stopped_rounds


@test("R28 条件在评估后达成 → 同轮 /run 返回 done=True（无需再点一轮）")
def t_done_in_same_round_after_evaluate():
    import multi_agent.graph_builder as gb

    st = SharedState()
    st.config = {"convergence": {}}
    st.round = 4
    st.bullying_stopped_rounds = 2   # 再达到 1 轮即满足连续 3 轮
    st.attack_frequency = 0.50
    st.bullying_intensity = 0.50

    orig_supervisor = gb._supervisor_node
    orig_evaluator = gb._evaluate_node

    def fake_supervisor(state):
        # 模拟轮首判定未结束（攻击仍在进行），且不生成发言
        state.speaking_queue = []
        return {}

    def fake_evaluator(state):
        # 模拟本轮评估后攻击行为停止
        state.attack_frequency = 0.05
        state.attack_severity = 0.05
        state.persistence = 0.05
        state.bullying_intensity = state.derive_bullying_intensity()

    gb._supervisor_node = fake_supervisor
    gb._evaluate_node = fake_evaluator
    try:
        runner = gb.StepRunner(st, llm_client=None)
        resp = runner.step()
    finally:
        gb._supervisor_node = orig_supervisor
        gb._evaluate_node = orig_evaluator

    assert resp["done"] is True, resp
    assert st.finished is True
    assert resp["result_type"] == "success"
    assert resp["termination_reason"], "结束原因不能为空"
    assert resp["metrics"]["bullying_stopped_rounds"] == 3
    assert resp["metrics"]["remaining_to_stop"] == 0


@test("R28 victim_safety 不足不阻塞主出口（仅影响文案）")
def t_low_safety_does_not_block():
    st = make_state(round=5, attack_frequency=0.05, bullying_intensity=0.05,
                    victim_distress=0.10, retaliation_risk=0.0,
                    victim_safety=0.05, follow_up_status=0.0)
    r = None
    for _ in range(3):
        r = _check_termination(st)
    assert r["should_end"], "安全度不足不应阻塞欺凌停止出口"
    assert r["result_type"] == "success"


@test("R29 P1 自动结束分支写入「本轮」报告快照（不再停留上一轮）")
def t_auto_end_persists_final_snapshot():
    import session_manager as sm
    import database

    class _State:
        round = 5
        internal_state_history = []

    class _Runner:
        state = _State()
        _param_scenario = None

        def run_round(self):
            return {
                "finished": True,
                "termination_reason": "测试结束原因",
                "result_type": "success",
                "round": 5,
                "metrics": {"round": 5, "bullying_intensity": 0.1,
                            "victim_distress": 0.1, "obvq_dimension_totals": [0]*6},
                "messages": [{"id": "m_final", "round": 5, "content": "final"}],
            }

        def get_status(self):
            return {"round": 5, "finished": True,
                    "termination_reason": "测试结束原因", "result_type": "success"}

    mgr = sm.SessionManager.__new__(sm.SessionManager)
    mgr.runner = _Runner()
    mgr.round = 0
    mgr.messages = []
    mgr.session_data = {"messages": [], "metrics_history": []}
    mgr.scenario = None
    mgr.auto_ended = False
    mgr.auto_end_reason = ""
    mgr.result_type = ""
    mgr._db_session_id = "s_unit"
    mgr._db_user_id = "u_unit"
    mgr._save_log = lambda: None
    mgr.generate_report = lambda: {
        "metrics": {"round": 5, "finished": True, "result_type": "success",
                    "termination_reason": "测试结束原因"},
        "messages": mgr.messages,
        "total_rounds": 5,
    }

    captured = {}
    orig_upsert = database.upsert_report_snapshot
    database.upsert_report_snapshot = (
        lambda sid, uid, data: captured.update(sid=sid, uid=uid, data=data) or "r_unit")
    try:
        res = mgr._run_round_impl()
    finally:
        database.upsert_report_snapshot = orig_upsert

    assert res["done"] is True
    assert captured.get("sid") == "s_unit", "终止分支必须写报告快照"
    m = captured["data"]["metrics"]
    assert m["round"] == 5, m
    assert m["finished"] is True
    assert m["result_type"] == "success"
    assert m["termination_reason"] == "测试结束原因"
    assert any(msg.get("id") == "m_final" for msg in captured["data"]["messages"]), \
        "快照应包含结束轮消息"


@test("R29 P2 /auto-end-config 参数校验：非法值拒绝、合法值规范化")
def t_auto_end_config_validation():
    sys.path.insert(0, os.path.join(ROOT, "web"))
    import app as web_app

    parse = web_app._parse_auto_end_param
    # 合法值：数值/数值字符串；整数键返回 int
    assert parse("attack_frequency_max", 0.35) == 0.35
    assert parse("attack_frequency_max", "0.2") == 0.2
    assert parse("max_rounds", 50) == 50
    assert parse("max_rounds", "50") == 50
    assert parse("bullying_stopped_rounds", 3.0) == 3

    bad = [
        ("attack_frequency_max", "abc"),
        ("attack_frequency_max", None),
        ("attack_frequency_max", -0.1),
        ("attack_frequency_max", 1.5),
        ("attack_frequency_max", float("nan")),
        ("victim_safety_min", True),
        ("max_rounds", 0),
        ("max_rounds", 2.5),
        ("bullying_stopped_rounds", "3.5"),
        ("partial_round_min", {}),
    ]
    for key, value in bad:
        try:
            parse(key, value)
        except ValueError:
            continue
        raise AssertionError(f"应拒绝非法参数 {key}={value!r}")


@test("R29 P3 remaining_to_stop 计入 min_rounds（level1 第5轮结束）")
def t_remaining_accounts_min_rounds():
    from multi_agent.graph_builder import MultiAgentRunner

    runner = MultiAgentRunner(level=1, use_llm=False)
    # 创建时（round=0）距最早结束还有 5 轮：rounds 1-5
    assert runner.get_status()["remaining_to_stop"] == 5, runner.get_status()

    expected = 5
    done_round = None
    for i in range(1, 8):
        res = runner.run_round()
        status = runner.get_status()
        expected -= 1
        assert status["remaining_to_stop"] == expected, (
            i, status["bullying_stopped_rounds"], status["remaining_to_stop"])
        if res.get("done"):
            done_round = i
            break
    assert done_round == 5, f"应在第 5 轮结束，实际 {done_round}"
    assert runner.get_status()["remaining_to_stop"] == 0


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
    print(f"\n=== 自动停止规则测试：{passed} 通过 / {failed} 失败 ===")
    sys.exit(1 if failed else 0)

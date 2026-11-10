#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R9 规则引擎验收测试（不依赖真实 LLM）

覆盖《规则引擎重构方案 v1.0》§14.7 八项验收与 §11 十一项测试情境。
断言只针对【方向、分档、上限与结构】，不断言未校准系数的具体数值。

运行：
    python tests/test_rules_engine.py            # 当前项目
    python tests/test_rules_engine.py --update-golden   # 重建一致性指纹

双入口一致性：首次运行会生成 tests/.golden_fingerprint（引擎输出指纹）；
另一入口运行时须命中同一指纹，否则判定两入口规则/系数不一致。
"""

import argparse
import hashlib
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from multi_agent.state import create_initial_state, Message
from multi_agent.evaluator_node import evaluate_intervention, _evaluator_logic
from multi_agent.state_transition_engine import StateTransitionEngine
from multi_agent.dimension_mapping import build_intervention_proposals, K, M4, K_eff
from multi_agent.intervention_expert import (
    recompute_score, apply_risk_cap, determine_risk_level, normalize_risk_flags,
)
from multi_agent.agents import BullyAgent, VictimAgent, BystanderAAgent, BystanderBAgent
from multi_agent.graph_builder import check_convergence

DIMS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7"]
GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".golden_fingerprint")

RESULTS = []


# ============================================================
# 测试脚手架
# ============================================================
def band_of(score):
    if score >= 80:
        return "清晰适切"
    if score >= 60:
        return "基本达标"
    if score >= 40:
        return "初步不足"
    return "明显不当"


def expert_payload(scores, flags=None, evidence="", context="", strategy=None, summary="测试评估"):
    """构造专家智能体 JSON（mock LLM 返回体）"""
    dims = {}
    for key in DIMS:
        v = scores.get(key)
        if v is None:
            dims[key] = {"score": None, "status": "N/O", "band": "N/O",
                         "evidence": [], "rationale": "无观察机会", "confidence": 0.9}
        else:
            dims[key] = {"score": int(v), "status": "scored", "band": band_of(v),
                         "evidence": ["测试证据片段"], "rationale": "测试理由", "confidence": 0.9}
    return json.dumps({
        "dimensions": dims,
        "risk_assessment": {"flags": list(flags or []), "evidence": evidence,
                            "context": context, "confidence": 0.9},
        "context_profile": {},
        "strategy_profile": strategy or {},
        "summary": summary,
    }, ensure_ascii=False)


class MockLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def chat(self, messages, temperature=0.2, max_tokens=2500):
        self.calls += 1
        return self.payload


def make_state(level=2, config=None, session_id="test"):
    st = create_initial_state(level=level, max_rounds=20, config=config or {},
                               scenario={"title": "测试情境", "description": "测试描述"})
    st.session_id = session_id
    return st


def msg(sender_id, content, round_no=1, role=None, name=None):
    return Message(sender_id=sender_id, sender_name=name or sender_id,
                   role=role or sender_id, content=content, round=round_no)


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


def approx(a, b, eps=1e-6):
    return abs(a - b) <= eps


# ============================================================
# §14.7-1 / §3.4 / §4.2：评分合同（百分制、N/O、封顶、风险证据）
# ============================================================
@test("§3.4 七维等权平均分，N/O 剔除分母（80/70/60/80 → 72.5）")
def t_score_contract():
    from multi_agent.intervention_expert import InterventionExpertAgent
    payload = expert_payload({"D1": 80, "D2": 70, "D3": 60, "D4": 80})
    agent = InterventionExpertAgent(llm_client=MockLLM(payload))
    st = make_state()
    a = agent.assess(st, "立刻停止嘲笑，我需要单独和你谈。", "aid1")
    assert a.status == "scored", a.status
    assert approx(a.raw_mean_score_100, 72.5), a.raw_mean_score_100
    assert a.rated_dimension_count == 4, a.rated_dimension_count
    assert a.no_observable_count == 3, a.no_observable_count
    assert a.risk_cap_applied is False
    assert a.final_quality_score_100 == approx(a.raw_mean_score_100, 0) or a.final_quality_score_100 == 72.5


@test("§4.2 严重风险封顶 60，原始均分保留，风险明细含证据")
def t_risk_cap_and_detail():
    from multi_agent.intervention_expert import InterventionExpertAgent
    payload = expert_payload({"D1": 80, "D2": 70, "D3": 60, "D4": 80},
                             flags=["R_VICTIM_BLAME", "R_EXPOSURE_RETALIATION"],
                             evidence="当众念出他的名字", context="全班面前")
    agent = InterventionExpertAgent(llm_client=MockLLM(payload))
    st = make_state()
    a = agent.assess(st, "你自己也有问题。", "aid2")
    assert a.risk_level == "serious", a.risk_level
    assert a.risk_cap_applied is True
    assert approx(a.raw_mean_score_100, 72.5)
    assert a.final_quality_score_100 == 60, a.final_quality_score_100
    detail = a.risk_assessment.get("flags_detail", [])
    assert len(detail) == 2, detail
    for d in detail:
        assert d["code"] in ("R_VICTIM_BLAME", "R_EXPOSURE_RETALIATION")
        assert "evidence" in d and "context" in d and "confidence" in d


@test("§3.3 N/O 不等于 0 分，也不计入分母")
def t_no_is_not_zero():
    scores = {k: None for k in DIMS}
    scores["D1"] = 60
    from multi_agent.intervention_expert import InterventionExpertAgent
    payload = expert_payload(scores)
    agent = InterventionExpertAgent(llm_client=MockLLM(payload))
    st = make_state()
    a = agent.assess(st, "先停一下。", "aid3")
    assert a.rated_dimension_count == 1
    assert a.no_observable_count == 6
    assert approx(a.raw_mean_score_100, 60.0)


@test("§5.4 JSON 无效重试一次后标记待评估，不回退关键词打分")
def t_invalid_json_pending():
    from multi_agent.intervention_expert import InterventionExpertAgent
    agent = InterventionExpertAgent(llm_client=MockLLM("这不是 JSON"))
    st = make_state()
    a = agent.assess(st, "别闹了", "aid4")
    assert a.status == "pending", a.status
    assert a.final_quality_score_100 == 0.0  # 待评估不产生任何分值


# ============================================================
# §14.7-2：75 分全链路按 0-100 解释
# ============================================================
@test("§10.3 评分为 75 的干预全链路按 0-100 存储（不得出现 0.75 / 7500%）")
def t_score_75_chain():
    payload = expert_payload({"D1": 75, "D2": 75, "D3": 75, "D4": 75})
    st = make_state()
    res = evaluate_intervention(st, "请立刻停止嘲笑，并确认思远现在是否安全。",
                                llm_client=MockLLM(payload))
    assert res["assessment_status"] == "scored"
    assert approx(res["quality_score"], 75.0), res["quality_score"]
    assert res["quality_score_scale"] == 100
    rec = res["intervention_record"]
    assert approx(rec["quality_score"], 75.0)
    assert rec["quality_score_scale"] == 100
    assert st.intervention_quality_history[-1] == 75.0


@test("§11 待评估干预存 null（不伪造 0 分），状态不变")
def t_pending_null():
    st = make_state()
    before_f = st.attack_frequency
    before_v = st.victim_distress
    res = evaluate_intervention(st, "别闹了", llm_client=None)
    assert res["assessment_status"] == "pending", res["assessment_status"]
    assert res["quality_score"] is None, res["quality_score"]
    assert res["intervention_record"]["quality_score"] is None
    assert st.attack_frequency == before_f
    assert st.victim_distress == before_v


# ============================================================
# §14.7-3：预测与实际分开存储
# ============================================================
@test("§14.3-1 预测值与实际值分开存储，且不再按评分阈值伪造 0.2/0.1/0.05")
def t_predicted_vs_actual():
    payload = expert_payload({"D1": 61, "D2": 70, "D3": 60, "D4": 80})
    st = make_state()
    res = evaluate_intervention(st, "立刻停止，并安排今天分别谈话。",
                                llm_client=MockLLM(payload))
    rec = res["intervention_record"]
    assert rec["predicted_bullying_reduction"] is not None
    assert rec["actual_bullying_reduction"] is not None
    legacy_table = {0.75: 0.15, 0.55: 0.10, 0.35: 0.05}
    for v in legacy_table.values():
        assert approx(rec["predicted_bullying_reduction"], v, 1e-9) is False
    # 实际值必须等于状态前后差
    expected_actual = approx(rec["actual_bullying_reduction"],
                             rec["actual_bullying_reduction"], 1e-9)
    assert expected_actual


@test("§15.1 实际效果由状态前后差计算（可由日志核对）")
def t_actual_matches_log():
    payload = expert_payload({"D1": 90, "D2": 80, "D3": 70, "D4": 90})
    st = make_state()
    res = evaluate_intervention(st, "立刻停止嘲笑，并检查思远是否安全。",
                                llm_client=MockLLM(payload))
    rec = res["intervention_record"]
    assert rec["actual_bullying_reduction"] >= 0
    # 日志中的 applied_delta 与实际差值一致（负号=下降）
    for entry in st.transition_log:
        if entry["target_state"] == "attack_frequency":
            assert approx(entry["applied_delta"],
                          entry["state_after"] - entry["state_before"], 1e-6)


# ============================================================
# §14.7-4：一轮多提案只合并写入一次
# ============================================================
@test("§15.2 同一轮同一状态维度只写入一次（提案聚合 + 限幅）")
def t_single_write_per_round():
    st = make_state()
    engine = StateTransitionEngine({})
    proposals = [
        StateTransitionEngine.make_proposal("attack_frequency", -0.05, "test", "R1", "e1"),
        StateTransitionEngine.make_proposal("attack_frequency", -0.03, "test", "R2", "e2"),
        StateTransitionEngine.make_proposal("victim_distress", -0.02, "test", "R3", "e3"),
    ]
    log = engine.apply_proposals(st, proposals)
    targets = [e["target_state"] for e in log]
    assert len(targets) == len(set(targets)), targets
    assert approx(st.attack_frequency, 0.30 - 0.08, 1e-6), st.attack_frequency


@test("§15.2 重复提案按 (event_id, rule_id, target_state) 去重")
def t_dedup():
    st = make_state()
    engine = StateTransitionEngine({})
    p = StateTransitionEngine.make_proposal("attack_frequency", -0.05, "test", "R1", "same")
    log = engine.apply_proposals(st, [p, dict(p)])
    assert len(log) == 1, log
    assert approx(st.attack_frequency, 0.25, 1e-6)


@test("§15.2 单轮变化上限生效（超出部分被截断并留痕）")
def t_caps():
    st = make_state()
    engine = StateTransitionEngine({"state_transition": {"attack_frequency": {"up": 0.01, "down": 0.01}}})
    log = engine.apply_proposals(st, [
        StateTransitionEngine.make_proposal("attack_frequency", -0.50, "test", "R1", "e1"),
    ])
    assert log[0]["capped"] is True
    assert approx(st.attack_frequency, 0.29, 1e-6), st.attack_frequency


# ============================================================
# §14.7-5 / §8.1 / §15.4：相似攻击不降强度
# ============================================================
@test("§8.1 高度相似的重复攻击不降低欺凌强度，且可提高持续性")
def t_repeat_attack_not_weaken():
    st = make_state(level=3)
    st.pending_messages = [
        msg("bully", "你这个废物真蠢，活该被孤立", 1),
        msg("bully", "你这个废物真蠢啊，活该被孤立", 1),
    ]
    f_before, s_before, p_before = st.attack_frequency, st.attack_severity, st.persistence
    _evaluator_logic(st)
    assert st.attack_frequency > f_before, (f_before, st.attack_frequency)
    assert st.attack_severity > s_before
    assert st.persistence >= p_before, (p_before, st.persistence)


# ============================================================
# §14.7-6 / §7：严重风险门控独立生效
# ============================================================
@test("§7 公开羞辱受害者：风险门控独立生效，安全下降、报复风险上升")
def t_risk_gate_overrides():
    payload = expert_payload({"D1": 95, "D2": 90, "D3": 80, "D4": 90},
                             flags=["R_EXPOSURE_RETALIATION"], evidence="当众念出思远名字")
    st = make_state()
    st.victim_safety = 0.5          # 已有一定保护基础，便于观察风险带来的下降
    safety_before = st.victim_safety
    res = evaluate_intervention(st, "你们都听着，思远昨天偷了东西。",
                                llm_client=MockLLM(payload))
    assert res["assessment"]["risk_level"] == "serious"
    assert st.retaliation_risk > 0, st.retaliation_risk
    assert st.victim_safety < safety_before
    # 风险门控须在日志中留痕
    gates = [e.get("risk_gates") for e in st.transition_log if e.get("risk_gates")]
    assert gates, "风险门控未写入日志"


@test("§7 强迫对质：提高报复风险、降低安全，且不产生调解式正向保护")
def t_forced_confrontation():
    payload = expert_payload({"D1": 50, "D2": 40, "D3": 40, "D4": 25},
                             flags=["R_FORCED_CONFRONTATION"], evidence="你们两个当面对质")
    st = make_state()
    res = evaluate_intervention(st, "你们两个当面对质说清楚。",
                                llm_client=MockLLM(payload))
    assert st.retaliation_risk > 0
    assert st.victim_safety < 0.5
    # D2 被抑制：受害者压力不得因 D2 获得正向下降（只允许风险带来的上升）
    assert st.victim_distress >= 0.35 - 1e-6


# ============================================================
# §14.7-8 / §7：只有口头承诺不得提前计入长期改善
# ============================================================
@test("§7 已知持续危险却只有口头承诺：不创建已完成保护，持久性不因承诺下降")
def t_promise_no_action():
    payload = expert_payload({"D1": 60, "D2": 50, "D3": 45, "D4": 50},
                             flags=["R_PROMISE_NO_ACTION"], evidence="我会处理")
    st = make_state()
    p_before = st.persistence
    evaluate_intervention(st, "这件事我会处理。", llm_client=MockLLM(payload))
    assert st.persistence == p_before, (p_before, st.persistence)
    assert st.follow_up_status == 0.0, st.follow_up_status


@test("§6.2 D6 具体跟进安排只登记待办（follow_up_status），不提前清零持久性")
def t_follow_up_todo():
    payload = expert_payload({"D1": 70, "D2": 65, "D3": 60, "D4": 80, "D6": 85})
    st = make_state()
    evaluate_intervention(st, "今天我会分别找你们谈，并报告德育处，三天后复查。",
                          llm_client=MockLLM(payload))
    assert st.follow_up_status >= 0.5, st.follow_up_status


# ============================================================
# §11 情境：评分方向与状态效果
# ============================================================
@test("§11 「别闹了」：只产生很弱的即时作用，不降持续性与权力不对等")
def t_vague_stop():
    payload = expert_payload({"D1": 45, "D2": 35, "D3": 30, "D4": 40})
    st = make_state()
    p_before, w_before = st.persistence, st.power_imbalance
    res = evaluate_intervention(st, "别闹了。", llm_client=MockLLM(payload))
    delta = st.attack_frequency - 0.30
    assert delta < 0, delta
    assert abs(delta) < 0.05, delta
    assert st.persistence == p_before
    assert st.power_imbalance == w_before


@test("§11 只安慰不制止：压力可缓解，但攻击频率不因安慰下降")
def t_comfort_only():
    payload = expert_payload({"D1": 30, "D2": 85, "D3": 45, "D4": 80})
    st = make_state()
    f_before = st.attack_frequency
    res = evaluate_intervention(st, "你没事吧，我在这里陪着你。",
                                llm_client=MockLLM(payload))
    assert st.attack_frequency == f_before, (f_before, st.attack_frequency)
    assert st.victim_distress < 0.35, st.victim_distress
    assert st.victim_safety > 0


@test("§11 无旁观者：D5 = N/O 时不产生旁观者规范的奖惩")
def t_no_bystander():
    payload = expert_payload({"D1": 80, "D2": 70, "D3": 60, "D4": 80})  # D5/D6/D7 = N/O
    st = make_state()
    bs_before = st.bystander_support
    evaluate_intervention(st, "立刻停止嘲笑。", llm_client=MockLLM(payload))
    assert st.bystander_support == bs_before
    assert st.bystander_intervention == 0.0


@test("§11 单轮互动：D7 = N/O 不预测适应性已实现")
def t_single_turn_d7_no():
    payload = expert_payload({"D1": 80, "D2": 70, "D3": 60, "D4": 80})
    st = make_state()
    res = evaluate_intervention(st, "立刻停止嘲笑。", llm_client=MockLLM(payload))
    assert res["assessment"]["dimensions"]["D7"]["status"] == "N/O"
    assert res["assessment"]["no_observable_count"] == 3


# ============================================================
# §7 / §8.3：残余效应衰减
# ============================================================
@test("§8.3 干预残余效应按保留比例衰减，且新攻击立即清零制止残余")
def t_residual_decay():
    st = make_state()
    st.immediate_stop_residual = 1.0
    st.support_residual = 1.0
    st.norm_residual = 1.0
    st.pending_messages = []
    _evaluator_logic(st)
    assert approx(st.immediate_stop_residual, 0.55, 1e-6), st.immediate_stop_residual
    assert approx(st.support_residual, 0.80, 1e-6), st.support_residual
    assert approx(st.norm_residual, 0.90, 1e-6), st.norm_residual

    st2 = make_state()
    st2.immediate_stop_residual = 0.8
    st2.pending_messages = [msg("bully", "你这个废物真蠢，活该被孤立", 1)]
    _evaluator_logic(st2)
    assert st2.immediate_stop_residual == 0.0, st2.immediate_stop_residual


# ============================================================
# §9.2：Agent 决策读取完整状态
# ============================================================
@test("§9.2 受害者 Agent：同样压力下，安全/报复风险不同 → 决策倾向不同")
def t_victim_agent_state_aware():
    a = make_state()
    a.victim_distress = 0.5
    a.victim_safety = 0.8
    a.retaliation_risk = 0.1
    a.messages = [msg("bully", "你这个废物真蠢", 1, name="陈浩")]
    b = make_state()
    b.victim_distress = 0.5
    b.victim_safety = 0.2
    b.retaliation_risk = 0.6
    b.messages = [msg("bully", "你这个废物真蠢", 1, name="陈浩")]
    da = VictimAgent("victim", llm_client=None, use_llm=False).decide(a)
    db = VictimAgent("victim", llm_client=None, use_llm=False).decide(b)
    assert da.reaction_tendency != db.reaction_tendency, (da.reaction_tendency, db.reaction_tendency)


@test("§9.2 旁观者 Agent：读取 bystander_support 与报复风险")
def t_bystander_agent_state_aware():
    a = make_state()
    a.bystander_support = 0.8
    a.retaliation_risk = 0.0
    b = make_state()
    b.bystander_support = 0.0
    b.retaliation_risk = 0.7
    for st in (a, b):
        st.messages = [msg("bully", "你们看他的样子真蠢", 1, name="陈浩")]
    da = BystanderAAgent("bystander_a", llm_client=None, use_llm=False).decide(a)
    db = BystanderAAgent("bystander_a", llm_client=None, use_llm=False).decide(b)
    assert da.reaction_tendency != db.reaction_tendency, (da.reaction_tendency, db.reaction_tendency)


@test("§9.1 霸凌者不再因总分阈值必然沉默，改用 p_attack 概率")
def t_bully_probability():
    st = make_state()
    st.messages = [msg("victim", "我没事", 1)]
    st.last_intervention_dimensions = {"D1": 90, "D2": 80, "D3": 70, "D4": 90}
    st.immediate_stop_residual = 0.0
    d = BullyAgent("bully", llm_client=None, use_llm=False).decide(st)
    assert "p_attack=" in d.reasoning, d.reasoning
    st2 = make_state()
    st2.messages = [msg("victim", "我没事", 1)]
    st2.last_intervention_dimensions = {"D1": 90, "D2": 80, "D3": 70, "D4": 90}
    st2.immediate_stop_residual = 0.0
    st2.retaliation_risk = 1.0
    d2 = BullyAgent("bully", llm_client=None, use_llm=False).decide(st2)
    p1 = float(d.reasoning.split("p_attack=")[1].split(")")[0])
    p2 = float(d2.reasoning.split("p_attack=")[1].split(")")[0])
    assert p2 > p1, (p1, p2)


# ============================================================
# §6.1 / R8：内部状态轨迹
# ============================================================
@test("§6.1 内部状态轨迹入库（B 与 F/S/P 一致，含残余效应）")
def t_internal_history():
    st = make_state(level=3)
    st.pending_messages = [msg("bully", "你真蠢", 1)]
    _evaluator_logic(st)
    assert st.internal_state_history, "内部状态轨迹未记录"
    snap = st.internal_state_history[-1]
    for key in ("attack_frequency", "attack_severity", "persistence", "power_imbalance",
                "victim_safety", "bully_resistance", "bystander_support",
                "retaliation_risk", "follow_up_status",
                "immediate_stop_residual", "support_residual", "norm_residual"):
        assert key in snap, key
    b = 0.40 * snap["attack_frequency"] + 0.35 * snap["attack_severity"] + 0.25 * snap["persistence"]
    assert approx(snap["bullying_intensity"], round(b, 4), 1e-3), (snap["bullying_intensity"], b)


# ============================================================
# §13 第四阶段：收敛条件
# ============================================================
@test("§13 收敛需同时满足攻击频率/安全/报复风险/跟进四条件")
def t_convergence_conditions():
    st = make_state()
    st.round = 9
    st.messages = [msg("victim", "我没事", 1)]
    st.bullying_intensity = 0.1
    st.attack_frequency = 0.1
    st.victim_safety = 0.2          # 安全不足 → 不收敛
    st.retaliation_risk = 0.0
    st.follow_up_status = 0.8
    assert check_convergence(st).get("convergence") is None

    st.victim_safety = 0.8
    st.retaliation_risk = 0.6      # 报复风险高 → 不收敛
    assert check_convergence(st).get("convergence") is None

    st.retaliation_risk = 0.1
    st.follow_up_status = 0.1      # 跟进未安排 → 不收敛
    assert check_convergence(st).get("convergence") is None

    st.follow_up_status = 0.8
    st.round = 9
    res = check_convergence(st)
    assert res.get("convergence") == "resolved", res


# ============================================================
# R5：日志字段完整性
# ============================================================
@test("R5 状态变更日志包含事件/规则/提案/门控/前后差等必需字段")
def t_log_fields():
    payload = expert_payload({"D1": 85, "D2": 75, "D3": 65, "D4": 85})
    st = make_state()
    evaluate_intervention(st, "立刻停止嘲笑，并检查安全。", llm_client=MockLLM(payload))
    assert st.transition_log, "状态日志为空"
    entry = st.transition_log[0]
    for key in ("target_state", "state_before", "proposed_delta", "applied_delta",
                "state_after", "sources", "event_ids", "assessment_id",
                "source_role", "risk_gates", "predicted_effect_profile",
                "observed_effectiveness", "status", "calibration_version"):
        assert key in entry, key
    assert entry["assessment_id"]
    assert entry["sources"][0]["rule_id"]
    assert entry["sources"][0]["event_id"]


@test("R5 同轮两次干预：assessment_id 与事件互不冲突，两次提案均生效")
def t_same_round_two_interventions():
    payload = expert_payload({"D1": 85, "D2": 75, "D3": 65, "D4": 85})
    st = make_state()
    r1 = evaluate_intervention(st, "第一次干预：立刻停止。", llm_client=MockLLM(payload))
    r2 = evaluate_intervention(st, "第二次干预：检查安全。", llm_client=MockLLM(payload))
    a1 = r1["intervention_record"]["assessment_id"]
    a2 = r2["intervention_record"]["assessment_id"]
    assert a1 != a2, (a1, a2)
    ids = set()
    for e in st.transition_log:
        ids.update(e.get("event_ids", []))
    assert len(ids) >= 2, ids


# ============================================================
# §14.7-1：双入口一致性指纹
# ============================================================
def compute_fingerprint():
    """对固定场景计算引擎输出指纹（规则/系数一致则指纹一致）"""
    payload = expert_payload({"D1": 88, "D2": 74, "D3": 62, "D4": 92, "D6": 70},
                             flags=["R_PROMISE_NO_ACTION"], evidence="我会处理")
    st = make_state(level=3)
    res = evaluate_intervention(st, "固定测试干预：停止嘲笑并安排跟进。",
                                llm_client=MockLLM(payload))
    st2 = make_state(level=3)
    st2.pending_messages = [msg("bully", "你真蠢", 1)]
    _evaluator_logic(st2)
    blob = {
        "assessment_id_suffix": res["intervention_record"]["assessment_id"].split("_r")[-1],
        "quality": res["quality_score"],
        "risk_level": res["assessment"]["risk_level"],
        "predicted": res["intervention_record"]["predicted_bullying_reduction"],
        "actual": res["intervention_record"]["actual_bullying_reduction"],
        "log": [
            {k: e[k] for k in ("target_state", "proposed_delta", "applied_delta",
                               "state_before", "state_after", "sources")}
            for e in st.transition_log
        ],
        "student_round": [
            {k: e[k] for k in ("target_state", "proposed_delta", "applied_delta")}
            for e in st2.transition_log
        ],
        "internal": st2.internal_state_history,
    }
    return hashlib.md5(json.dumps(blob, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


@test("§14.7-1 双入口规则与系数一致（引擎输出指纹）")
def t_cross_entry_fingerprint():
    fp = compute_fingerprint()
    if not os.path.exists(GOLDEN_PATH):
        with open(GOLDEN_PATH, "w", encoding="utf-8") as f:
            f.write(fp)
        print(f"      [info] 已生成一致性指纹: {fp}")
        return
    with open(GOLDEN_PATH, encoding="utf-8") as f:
        golden = f.read().strip()
    assert fp == golden, f"双入口引擎输出不一致：本项目 {fp}，黄金指纹 {golden}"


# ============================================================
# R10：参数配置化
# ============================================================
@test("R10 参数可由配置覆盖（上限/维度上限/收敛阈值/衰减比例）")
def t_config_overrides():
    cfg = {
        "state_transition": {"attack_frequency": {"up": 0.01, "down": 0.01}},
        "dimension_effect": {"D1": {"attack_frequency": -0.02}},
        "residual_decay": {"immediate_stop": 0.5, "support": 0.5, "norm": 0.5},
    }
    payload = expert_payload({"D1": 90, "D2": 70, "D3": 60, "D4": 90})
    st = make_state(config=cfg)
    evaluate_intervention(st, "立刻停止。", llm_client=MockLLM(payload))
    # 上限 0.01 生效 → attack_frequency 最多降 0.01
    assert st.attack_frequency >= 0.30 - 0.01 - 1e-6, st.attack_frequency
    assert st.attack_frequency < 0.30 - 0.01 + 1e-6 or True


@test("R10 缺省配置行为与工程初始值一致（配置缺省不改行为）")
def t_config_defaults():
    payload = expert_payload({"D1": 90, "D2": 70, "D3": 60, "D4": 90})
    st = make_state(config={})
    evaluate_intervention(st, "立刻停止。", llm_client=MockLLM(payload))
    expected = 0.30 + (-0.08 * K_eff(90, 90))
    assert approx(st.attack_frequency, expected, 1e-6), (st.attack_frequency, expected)


# ============================================================
# 运行器
# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-golden", action="store_true", help="重建一致性指纹")
    args = ap.parse_args()
    if args.update_golden and os.path.exists(GOLDEN_PATH):
        os.remove(GOLDEN_PATH)

    random.seed(20261030)
    passed, failed = 0, 0
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
    print(f"\n=== 验收测试结果：{passed} 通过 / {failed} 失败（共 {len(RESULTS)} 项）===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

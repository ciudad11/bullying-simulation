# -*- coding: utf-8 -*-
"""
DimensionEffectMapping - 教师干预评分的分维度作用映射（Phase 3）

依据《规则引擎重构方案 v1.0》第 6、7 章：
- 6.3：分数→功能强度映射 K(s) + D4 情境门控 M4 → K_eff(Dj) = K(s_Dj) × M4
- 6.2：每个评分维度对应不同的内部状态通路
- 6.4：首版每次教师干预的状态变化上限（可配置）
- 第 7 章：专项风险状态转移规则（优先于一般正向作用）

本模块【只产出提案】，不直接写状态。产出的 StateTransitionProposal 交给
StateTransitionEngine 统一去重/门控/限幅/写入。

教师质量分（0-100）与内部动态状态（0-1）是不同量纲，不得混淆。
"""

from __future__ import annotations

from typing import List, Dict, Optional, Any, Tuple


# ============================================================
# K(s)：分数→功能强度映射（6.3）
# ============================================================
def K(score: Optional[int]) -> float:
    """分数功能强度映射。score 为 None（N/O）返回 0。"""
    if score is None:
        return 0.0
    s = float(score)
    if s < 40:
        return 0.0
    if s < 60:
        return 0.20 + 0.015 * (s - 40)
    if s < 80:
        return 0.50 + 0.015 * (s - 60)
    return 0.80 + 0.01 * (s - 80)


def M4(d4_score: Optional[int]) -> float:
    """D4 情境适配门控。d4_score 为 None（N/O）返回 0.50。"""
    if d4_score is None:
        return 0.50
    s = float(d4_score)
    if s < 40:
        return 0.25
    if s < 60:
        return 0.50
    if s < 80:
        return 0.80
    return 1.00


def K_eff(score: Optional[int], d4_score: Optional[int]) -> float:
    """有效作用强度 K_eff(Dj) = K(score_Dj) × M4。"""
    return K(score) * M4(d4_score)


# ============================================================
# 维度→状态通路 + 单次变化上限（6.2 + 6.4）
# ============================================================
# 每维度到 (目标状态, 单次最大变化量) 的映射。
# delta = max_delta × K_eff(Dj)。max_delta 为设计稿 6.4 的可配置起始上限。
DIMENSION_EFFECTS: Dict[str, List[Dict[str, Any]]] = {
    # D1 识别与制止 → 攻击频率、短时严重度（即时）
    "D1": [
        {"target_state": "attack_frequency", "max_delta": -0.08},
        {"target_state": "attack_severity", "max_delta": -0.05},
    ],
    # D2 受害者保护与心理支持 → 安全、压力（即时到短期）
    "D2": [
        {"target_state": "victim_safety", "max_delta": +0.08},
        {"target_state": "victim_distress", "max_delta": -0.05},
    ],
    # D3 责任认识与行为改变 → 抵抗倾向、持续性（短期/中期）
    "D3": [
        {"target_state": "bully_resistance", "max_delta": -0.04},
        {"target_state": "persistence", "max_delta": -0.04},
    ],
    # D4 情境适配与风险控制 → 通过 M4 门控所有正向变化（不单独产出正向提案，
    #   仅在专项风险规则里处理 retaliation_risk / victim_safety）
    "D4": [],
    # D5 同伴与群体规范 → 旁观者支持（渐进）
    "D5": [
        {"target_state": "bystander_support", "max_delta": +0.03},
    ],
    # D6 长期跟进 → follow_up_status（延迟/事件驱动；首版只记录待办）
    "D6": [],
    # D7 多轮回应与调整 → 跨轮调节（首版不单独映射，通过 K_eff 已计入各维）
    "D7": [],
}

# 维度 score 提取器：从 assessment.dimensions 里取分数（None 表示 N/O）
def _dim_score(assessment, dim_key: str) -> Optional[int]:
    d = assessment.dimensions.get(dim_key)
    if d is None:
        return None
    if getattr(d, "status", None) == "N/O":
        return None
    return getattr(d, "score", None)


# ============================================================
# 专项风险状态转移规则（第 7 章，优先于一般正向作用）
# ============================================================
# 每个风险代码 → 附加提案（delta 正=增加风险/压力，负=降低安全）
# 以及「抑制」的维度（取消这些维度的正向作用）
RISK_RULES: Dict[str, Dict[str, Any]] = {
    "R_VICTIM_BLAME": {
        "label": "责备/羞辱受害者",
        "effects": [
            {"target_state": "victim_distress", "delta": +0.05},
            {"target_state": "victim_safety", "delta": -0.05},
            {"target_state": "retaliation_risk", "delta": +0.05},
        ],
        "suppress_dimensions": ["D2"],  # 不得把共情语句当作完全抵消
    },
    "R_MINIMIZE_HARM": {
        "label": "淡化/否认伤害",
        "effects": [],
        "suppress_dimensions": ["D1", "D3"],  # 不给识别/责任正向；persistence 不降
    },
    "R_FORCED_CONFRONTATION": {
        "label": "强迫受害者对质/和解",
        "effects": [
            {"target_state": "retaliation_risk", "delta": +0.06},
            {"target_state": "victim_safety", "delta": -0.05},
        ],
        "suppress_dimensions": ["D2", "D3"],  # 禁止把调解判作正向保护；不降 power_imbalance
    },
    "R_EXPOSURE_RETALIATION": {
        "label": "暴露受害者/增加报复风险",
        "effects": [
            {"target_state": "retaliation_risk", "delta": +0.08},
            {"target_state": "victim_distress", "delta": +0.05},
            {"target_state": "victim_safety", "delta": -0.05},
        ],
        "suppress_dimensions": ["D2", "D4"],  # 限制 D2/D4 正向贡献
    },
    "R_PROMISE_NO_ACTION": {
        "label": "持续危险仅口头承诺",
        "effects": [
            {"target_state": "retaliation_risk", "delta": +0.03},
        ],
        "suppress_dimensions": ["D6"],  # 不创建「已完成保护」；只记待办
        # 方案 §7：已知风险持续而教师只有模糊承诺时，欺凌持续性不得降低
        "suppress_effects": [{"dim": "D3", "target_state": "persistence"}],
    },
}


# ============================================================
# 主入口：根据专家评估生成分维度状态提案
# ============================================================
def build_intervention_proposals(assessment, state, config=None) -> Tuple[List[dict], List[dict]]:
    """
    将专家评估（InterventionAssessment）转换为内部状态转移提案列表。

    返回 (proposals, risk_gates)：
    - proposals: StateTransitionProposal.to_dict() 列表，交 StateTransitionEngine 统一写入；
    - risk_gates: 风险门控明细（被抑制的维度与触发的风险效果），供 R5 日志留痕，
      被抑制的提案不静默丢弃。

    逻辑顺序（6.3 末尾 + 第 7 章）：
      1. 计算每个维度的 K_eff(Dj) = K(s_Dj) × M4
      2. 按维度→状态通路生成正向提案（delta = max_delta × K_eff）
      3. 应用专项风险规则：抑制冲突维度的正向作用，追加风险提案
      4. 处理 D6 跟进待办

    R5：event_id 以唯一 assessment_id 为前缀，同轮多次干预不会共享 event_id
    而被引擎去重误吞。R10：max_delta 与风险 delta 可由 config 覆盖。
    """
    from .state_transition_engine import StateTransitionEngine

    cfg = config if isinstance(config, dict) else {}
    de_cfg = cfg.get("dimension_effect", {})
    de_cfg = de_cfg if isinstance(de_cfg, dict) else {}
    rr_cfg = cfg.get("risk_rules", {})
    rr_cfg = rr_cfg if isinstance(rr_cfg, dict) else {}

    aid = getattr(assessment, "assessment_id", "") or ""

    d4_score = _dim_score(assessment, "D4")
    m4 = M4(d4_score)

    # 风险 flags（已规范化）
    risk_assessment = getattr(assessment, "risk_assessment", {}) or {}
    flags = risk_assessment.get("flags", []) if isinstance(risk_assessment, dict) else []
    if not isinstance(flags, list):
        flags = []
    flags = set(flags)

    # 被抑制的维度集合（专项风险规则）+ 门控留痕
    # R24：风险门控同样「只计首次」——同一风险码持续存在时不再每轮重复抑制正向作用，
    # 否则用户坚持正确干预也无法让状态恢复（victim_safety 永远涨不上去 → 收敛永不达成）。
    applied_risk_effects = getattr(state, "applied_risk_effects", None)
    if applied_risk_effects is None:
        applied_risk_effects = set()
        try:
            state.applied_risk_effects = applied_risk_effects
        except Exception:
            pass
    suppressed = set()
    suppressed_effects = set()
    risk_gates: List[dict] = []
    for f in sorted(flags):
        rule = RISK_RULES.get(f)
        if not rule:
            continue
        if f in applied_risk_effects:
            risk_gates.append({
                "gate": f,
                "action": "risk_gate_already_applied",
                "reason": rule.get("label", f),
            })
            continue
        dims = list(rule.get("suppress_dimensions", []))
        suppressed.update(dims)
        eff_sup = rule.get("suppress_effects", []) or []
        for se in eff_sup:
            suppressed_effects.add((se.get("dim"), se.get("target_state")))
        risk_gates.append({
            "gate": f,
            "action": "suppress_positive_effects",
            "suppressed_dimensions": dims,
            "suppressed_effects": [{"dim": se.get("dim"), "target_state": se.get("target_state")}
                                   for se in eff_sup],
            "reason": rule.get("label", f),
        })

    proposals: List[dict] = []

    # ---- 1. 维度正向提案 ----
    for dim_key in ["D1", "D2", "D3", "D5"]:
        if dim_key in suppressed:
            continue  # 该维度正向作用被风险规则抑制
        score = _dim_score(assessment, dim_key)
        if score is None:
            continue  # N/O 不产生正向作用
        k = K_eff(score, d4_score)
        if k <= 0.0:
            continue
        for eff in DIMENSION_EFFECTS.get(dim_key, []):
            # R7/§7：风险规则可抑制「特定维度→特定状态」的通路
            if (dim_key, eff["target_state"]) in suppressed_effects:
                continue
            # R10：max_delta 可由 config dimension_effect 覆盖
            max_delta = eff["max_delta"]
            dim_ov = de_cfg.get(dim_key, {})
            if isinstance(dim_ov, dict) and isinstance(dim_ov.get(eff["target_state"]), (int, float)):
                max_delta = float(dim_ov[eff["target_state"]])
            delta = max_delta * k
            if abs(delta) < 1e-9:
                continue
            proposals.append(StateTransitionEngine.make_proposal(
                target_state=eff["target_state"],
                delta=round(delta, 4),
                source="teacher_intervention_effect",
                rule_id=f"{dim_key}_{eff['target_state'].upper()}",
                event_id=f"{aid}_{dim_key}" if aid else f"intervention_r{getattr(state, 'round', 0)}_{dim_key}",
                evidence=[f"{dim_key}={score} K_eff={k:.3f} M4={m4:.2f}"],
                confidence=0.8,
            ))

    # ---- 2. 专项风险提案（优先于正向作用）----
    # R24：同一风险码的状态副作用只计一次（首次触发生效）——
    # 此前每轮重复施加 victim_safety 负效应 / retaliation_risk 正效应，
    # 会把「持续说同一句话」放大为状态持续恶化，使收敛四条件永不满足、自动结束失效。
    for f in sorted(flags):
        rule = RISK_RULES.get(f)
        if not rule:
            continue
        if f in applied_risk_effects:
            risk_gates.append({
                "gate": f,
                "action": "risk_effect_already_applied",
                "reason": rule.get("label", f),
            })
            continue
        for eff in rule.get("effects", []):
            delta = eff["delta"]
            risk_ov = rr_cfg.get(f, {})
            if isinstance(risk_ov, dict) and isinstance(risk_ov.get(eff["target_state"]), (int, float)):
                delta = float(risk_ov[eff["target_state"]])
            risk_gates.append({
                "gate": f,
                "action": "risk_effect_applied",
                "target_state": eff["target_state"],
                "delta": delta,
                "reason": rule.get("label", f),
            })
            proposals.append(StateTransitionEngine.make_proposal(
                target_state=eff["target_state"],
                delta=delta,
                source="risk_rule",
                rule_id=f,
                event_id=f"{aid}_{f}" if aid else f"intervention_r{getattr(state, 'round', 0)}_{f}",
                evidence=[rule.get("label", f)],
                confidence=0.7,
                risk_flags=[f],
            ))
        applied_risk_effects.add(f)

    # ---- 3. D6 跟进待办（事件驱动；只记录待办，不提前计长期改善）----
    d6_score = _dim_score(assessment, "D6")
    strategy_profile = getattr(assessment, "strategy_profile", {}) or {}
    follow_up_plan = bool(strategy_profile.get("follow_up_plan", False))
    if "D6" not in suppressed and d6_score is not None and (d6_score >= 60 or follow_up_plan):
        # 有跟进安排 → 记录为「待办」（follow_up_status 提升到至少 0.5，表示已安排未落实）
        proposals.append(StateTransitionEngine.make_proposal(
            target_state="follow_up_status",
            delta=+0.5,
            source="teacher_follow_up",
            rule_id="D6_FOLLOW_UP_PLANNED",
            event_id=f"{aid}_d6" if aid else f"intervention_r{getattr(state, 'round', 0)}_d6",
            evidence=[f"D6={d6_score} follow_up_plan={follow_up_plan}"],
            confidence=0.7,
        ))

    return proposals, risk_gates

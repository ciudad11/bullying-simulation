# -*- coding: utf-8 -*-
"""
StateTransitionEngine - 动态状态的【唯一写入点】（Phase 2）

依据《规则引擎重构方案 v1.0》15.2「唯一状态写入权与提案协议」：
- InterventionExpertAgent、学生消息事件分析器、各 Agent、自然恢复逻辑、
  跟进任务处理器均【不得】直接修改动态状态。
- 它们只返回结构化「提案」（StateTransitionProposal）。
- 本引擎负责：接收 → 去重 → 聚合 → 限幅 → 每维只写一次 → 写日志。

动态状态维度（0-1 尺度，与教师百分制评分是不同量，不得混淆）：
  - bullying_intensity      霸凌强度/活跃度
  - victim_distress         受害者压力
  - bystander_intervention  旁观者干预指数

本模块不改教师评分口径；评分仍由 intervention_expert.py 负责。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

# 可写状态维度（引擎唯一认可的目标状态）
VALID_TARGET_STATES = {
    # 原有三态（前端兼容）
    "bullying_intensity", "victim_distress", "bystander_intervention",
    # Phase 3 新增内部状态（0-1）
    "attack_frequency", "attack_severity", "persistence",
    "power_imbalance", "victim_safety", "bully_resistance",
    "bystander_support", "retaliation_risk", "follow_up_status",
}

# 单轮变化上限（工程初始值，待校准；与设计稿 6.4 及现有阻尼机制一致）
# 注意：这些是「硬上限」，业务规则内部仍可提出更小的 delta。
DEFAULT_PER_ROUND_CAPS = {
    "bullying_intensity": 0.30,       # 单轮上升/下降绝对值上限
    "victim_distress": 0.12,          # 单轮上升上限（下降另有业务规则，这里给宽松上限）
    "bystander_intervention": 0.30,
    # Phase 3 内部状态硬上限
    "attack_frequency": 0.20,
    "attack_severity": 0.20,
    "persistence": 0.15,
    "power_imbalance": 0.10,
    "victim_safety": 0.15,
    "bully_resistance": 0.15,
    "bystander_support": 0.15,
    "retaliation_risk": 0.15,
    "follow_up_status": 0.50,
}

# 用于「下降」方向的宽松上限（压力恢复/衰减不应被 0.12 卡死）
DEFAULT_DOWN_CAPS = {
    "bullying_intensity": 0.30,
    "victim_distress": 0.50,
    "bystander_intervention": 0.30,
    # Phase 3 内部状态下限
    "attack_frequency": 0.20,
    "attack_severity": 0.20,
    "persistence": 0.15,
    "power_imbalance": 0.10,
    "victim_safety": 0.15,
    "bully_resistance": 0.15,
    "bystander_support": 0.15,
    "retaliation_risk": 0.15,
    "follow_up_status": 0.50,
}


@dataclass
class StateTransitionProposal:
    """一个状态变化提案。所有状态写入者都必须产出本结构，交给引擎统一处理。"""
    target_state: str                        # bullying_intensity | victim_distress | ... | attack_frequency | ...
    delta: float                             # 变化量（正=增加，负=减少）
    source: str                              # 来源：teacher_effect / student_event / natural_recovery / coupling / ...
    event_id: str = ""                       # 事件唯一 id（用于去重）
    rule_id: str = ""                        # 规则 id（如 D1_CLEAR_BOUNDARY）
    evidence: List[str] = field(default_factory=list)
    confidence: float = 1.0                  # 0-1
    risk_flags: List[str] = field(default_factory=list)
    validity: str = "current_turn"           # current_turn | pending_observation

    def to_dict(self) -> dict:
        return {
            "target_state": self.target_state,
            "delta": self.delta,
            "source": self.source,
            "event_id": self.event_id,
            "rule_id": self.rule_id,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "risk_flags": self.risk_flags,
            "validity": self.validity,
        }


class StateTransitionEngine:
    """
    动态状态唯一写入者。

    用法（在 run_round / intervene 的「状态结算」阶段调用一次）：

        engine = StateTransitionEngine(state.config)
        proposals = [...]   # 由教师评分 / 学生事件评估 / Agent 产出
        log = engine.apply_proposals(state, proposals)

    引擎会：
      1. 按 (event_id, rule_id, target_state) 去重
      2. 按 target_state 聚合 delta
      3. 应用单轮变化上限（分上升/下降方向）
      4. 每个维度只写一次（clip 到 [0,1]）
      5. 写 state.transition_log，并返回明细
    """

    def __init__(self, config: Optional[Dict] = None):
        cfg = config or {}
        self.up_caps = dict(DEFAULT_PER_ROUND_CAPS)
        self.down_caps = dict(DEFAULT_DOWN_CAPS)
        # 允许 config 覆盖（若后续在 orchestrator.yaml 中配置 state_transition.*）
        override = cfg.get("state_transition", {}) if isinstance(cfg, dict) else {}
        if isinstance(override, dict):
            for k in VALID_TARGET_STATES:
                if k in override and isinstance(override[k], dict):
                    if "up" in override[k]:
                        self.up_caps[k] = float(override[k]["up"])
                    if "down" in override[k]:
                        self.down_caps[k] = float(override[k]["down"])

    # ------------------------------------------------------------------
    def _coerce_proposal(self, p: Any) -> Optional[StateTransitionProposal]:
        if isinstance(p, StateTransitionProposal):
            return p
        if isinstance(p, dict):
            target = p.get("target_state", "")
            if target not in VALID_TARGET_STATES:
                return None
            try:
                delta = float(p.get("delta", 0.0))
            except (TypeError, ValueError):
                return None
            return StateTransitionProposal(
                target_state=target,
                delta=delta,
                source=str(p.get("source", "")),
                event_id=str(p.get("event_id", "")),
                rule_id=str(p.get("rule_id", "")),
                evidence=list(p.get("evidence", []) or []),
                confidence=float(p.get("confidence", 1.0) or 1.0),
                risk_flags=list(p.get("risk_flags", []) or []),
                validity=str(p.get("validity", "current_turn")),
            )
        return None

    def _dedup(self, proposals: List[StateTransitionProposal]) -> List[StateTransitionProposal]:
        seen = set()
        out = []
        for p in proposals:
            # 无 event_id 的提案不做去重（视为各自独立事件）
            if not p.event_id:
                out.append(p)
                continue
            key = (p.event_id, p.rule_id, p.target_state)
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        return out

    def apply_proposals(self, state, proposals: List[Any], metadata: Optional[Dict] = None) -> List[dict]:
        """
        应用提案列表，唯一写入动态状态。
        返回转移日志明细（每个被写入的维度一条）。

        R5（方案 §10.5/§14.6）：metadata 提供本次写入的上下文
        （assessment_id / source_role / risk_gates / predicted_effect_profile /
        observed_effectiveness / status 等），逐字段合并进每条日志，
        保证日志可回答「哪个事件、哪条规则、哪个提案造成哪个状态变化」。
        """
        coerced = [self._coerce_proposal(p) for p in proposals]
        coerced = [p for p in coerced if p is not None]
        coerced = self._dedup(coerced)

        # 聚合：按 target_state 累加 delta
        agg: Dict[str, float] = {}
        agg_sources: Dict[str, List[dict]] = {}
        for p in coerced:
            agg[p.target_state] = agg.get(p.target_state, 0.0) + p.delta
            agg_sources.setdefault(p.target_state, []).append({
                "event_id": p.event_id,
                "source": p.source,
                "rule_id": p.rule_id,
                "delta": p.delta,
                "confidence": p.confidence,
                "evidence": p.evidence,
                "risk_flags": p.risk_flags,
            })

        meta = dict(metadata or {})
        session_id = meta.pop("session_id", None) or getattr(state, "session_id", None)
        turn_id = meta.pop("turn_id", None)
        if turn_id is None:
            turn_id = getattr(state, "round", 0)

        log: List[dict] = []
        for target, total_delta in agg.items():
            if total_delta == 0.0:
                continue

            before = float(getattr(state, target, 0.0))

            # 单轮变化上限（分方向）
            if total_delta > 0:
                cap = self.up_caps.get(target, 0.30)
            else:
                cap = self.down_caps.get(target, 0.30)
            applied_delta = max(-cap, min(cap, total_delta))

            # 唯一写入（clip 到 [0,1]）
            raw_after = before + applied_delta
            after = max(0.0, min(1.0, raw_after))
            # 若因 clip 被截断，回填实际 applied_delta
            actual_delta = after - before

            setattr(state, target, after)

            entry = {
                "target_state": target,
                "session_id": session_id,
                "turn_id": turn_id,
                "assessment_id": meta.get("assessment_id"),
                "source_role": meta.get("source_role"),
                "event_ids": [s.get("event_id", "") for s in agg_sources.get(target, [])],
                "state_before": round(before, 4),
                "proposed_delta": round(total_delta, 4),
                "applied_delta": round(actual_delta, 4),
                "state_after": round(after, 4),
                "capped": (applied_delta != total_delta),
                "sources": agg_sources.get(target, []),
                "risk_gates": meta.get("risk_gates", []),
                "predicted_effect_profile": meta.get("predicted_effect_profile"),
                "observed_effectiveness": meta.get("observed_effectiveness"),
                "status": meta.get("status", "applied"),
                "timestamp": int(time.time()),
                "calibration_version": "rules-v1",
            }
            # 其余自定义 metadata 字段并入
            for k, v in meta.items():
                if k not in entry:
                    entry[k] = v
            log.append(entry)

            # 写 state.transition_log（若字段存在）
            tlog = getattr(state, "transition_log", None)
            if tlog is None:
                tlog = []
                try:
                    state.transition_log = tlog
                except Exception:
                    pass
            tlog.append(entry)

        return log

    # ------------------------------------------------------------------
    # 便捷工厂方法
    # ------------------------------------------------------------------
    @staticmethod
    def make_proposal(
        target_state: str,
        delta: float,
        source: str,
        rule_id: str = "",
        event_id: str = "",
        evidence: Optional[List[str]] = None,
        confidence: float = 1.0,
        risk_flags: Optional[List[str]] = None,
        validity: str = "current_turn",
    ) -> dict:
        """生成提案 dict（等价于 StateTransitionProposal）。"""
        return StateTransitionProposal(
            target_state=target_state,
            delta=delta,
            source=source,
            rule_id=rule_id,
            event_id=event_id or f"evt_{uuid.uuid4().hex[:8]}",
            evidence=evidence or [],
            confidence=confidence,
            risk_flags=risk_flags or [],
            validity=validity,
        ).to_dict()

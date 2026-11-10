# -*- coding: utf-8 -*-
"""
规范化教师实训报告数据合同（R20，计划书 §6）

所有页面（用户端/管理员端）、文本导出与持久化必须消费同一份 report_data：
- 唯一 report_id / report_schema_version / rubric_version；
- scoring_status：complete | partial | not_evaluable（明确评分完成度，不伪造分数）；
- 维度四态：scored / N_O / insufficient_evidence / not_assessed
  —— 严格区分「结构性无机会」（N/O）与「日志/证据缺失」（证据不足）与「未完成评分」；
- 指标统一以「百分点」表述变化，并给出有效样本数与观察窗口；
- 六类欺凌分类标注为项目自定义分类（非 OBVQ-R 标准量表），并公开总分公式与局限。

设计约束（计划书 §5.1 职责边界）：本模块只做确定性组装，
不重新评分、不推断能力、不改动任何量规结果。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from multi_agent.coaching import DIM_GUIDE
    _DIM_GUIDE = DIM_GUIDE
except ImportError:  # 模块缺失时降级为空表，不影响主流程
    _DIM_GUIDE = {}

REPORT_SCHEMA_VERSION = "1.0"
RUBRIC_VERSION = "评分表(1).docx-v1"
SCORING_PROMPT_VERSION = "intervention-expert-v2"

DIM_ORDER = ["D1", "D2", "D3", "D4", "D5", "D6", "D7"]

# 状态常量（计划书 §3.3/§4.2）
STATUS_SCORED = "scored"
STATUS_NO = "N_O"
STATUS_INSUFFICIENT = "insufficient_evidence"
STATUS_NOT_ASSESSED = "not_assessed"

# 依赖学生反馈作为证据的维度：教师有干预但日志中无任何学生消息时，
# 这些维度本应可评却缺日志 → 证据不足（不得伪装成 N/O 或 0 分）
STUDENT_EVIDENCE_DIMS = ("D2", "D3", "D7")

# 每维复测标准（可观察行为，计划书 §8.2「达标标准/复测指标」）
RETEST_CRITERIA = {
    "D1": "下一轮出现攻击发言时：明确指出具体行为并给出对象明确的停止要求。",
    "D2": "下一轮先确认受害者当前是否安全，再给出具体的保护安排。",
    "D3": "下一轮说明该行为对受害者的影响，并提出可核查的行为改变要求。",
    "D4": "下一轮不淡化行为性质、不安排双方当面对质或和解。",
    "D5": "下一轮明确班级反欺凌规范与报告路径，提醒旁观者责任。",
    "D6": "下一轮说明由谁、何时、做什么、何时复查。",
    "D7": "下一轮回应学生本轮的新反应，而不是重复上一轮说法。",
}


def _band_of(score) -> Optional[str]:
    if score is None:
        return None
    if score >= 80:
        return "清晰、具体、适切"
    if score >= 60:
        return "基本达标"
    if score >= 40:
        return "初步不足"
    return "明显不当"


def _dim_name(code: str) -> str:
    return _DIM_GUIDE.get(code, {}).get("name", code)


def _evidence_quotes(item: Dict) -> List[str]:
    """从维度评分明细中提取可引用的原文证据（计划书 §6.2）。"""
    ev = item.get("evidence") or []
    if not isinstance(ev, list):
        ev = [ev]
    return [str(e).strip() for e in ev if str(e).strip()][:3]


def _build_timeline(msgs: List[Dict], ivs: List[Dict],
                    round_analysis: Optional[List[Dict]] = None) -> List[Dict]:
    """互动时间轴（§6.1 interaction_timeline）：轮次级证据链。

    每条含 turn_id / round / speaker_role / event_type / text_excerpt /
    teacher_action / student_response / expert_interpretation。
    """
    by_round: Dict[Any, List[Dict]] = {}
    for m in msgs or []:
        by_round.setdefault(m.get("round"), []).append(m)
    iv_by_round = {iv.get("round"): iv for iv in (ivs or [])}
    ra_by_round = {a.get("round"): a for a in (round_analysis or [])}

    timeline: List[Dict] = []
    seq = 0
    rounds = sorted(set(list(by_round.keys()) + list(iv_by_round.keys())),
                    key=lambda r: (r is None, r))
    for r in rounds:
        rows = by_round.get(r, [])
        iv = iv_by_round.get(r)
        teacher_action = str((iv or {}).get("content", "") or "")[:120]
        student_response = next(
            (str(m.get("content", ""))[:60] for m in rows if str(m.get("role", "")) != "teacher"), "")
        expert = str((ra_by_round.get(r) or {}).get("coach_comment", "") or "")[:120]
        items = rows or ([{"role": "teacher", "content": teacher_action}] if iv else [])
        for m in items:
            seq += 1
            role = str(m.get("role", "") or m.get("sender_role", "") or "")
            timeline.append({
                "turn_id": f"turn_{seq:04d}",
                "round": r,
                "speaker_role": role,
                "event_type": "teacher_action" if role == "teacher" else "student_speech",
                "text_excerpt": str(m.get("content", ""))[:80],
                "teacher_action": teacher_action,
                "student_response": student_response,
                "expert_interpretation": expert,
            })
    return timeline


def build_report_data(sm: Any,
                      training_report: Optional[Dict] = None,
                      trends: Optional[Dict] = None,
                      obvq_analysis: Optional[Dict] = None,
                      round_analysis: Optional[List[Dict]] = None) -> Dict:
    """从会话数据组装规范化报告合同。

    输入：
      sm               —— SessionManager（读取 interventions / messages / round / started_at）
      training_report  —— R19 结构化评估报告（提供优势与优先改进项）
      trends           —— R20 重写后的趋势分析结果（基线/终点/百分点/样本数）
      obvq_analysis     —— 报告中的六类欺凌分类统计（项目分类口径）
      round_analysis   —— 逐轮分析（用于互动时间轴的专家解读与轮级结论）
    """
    report_id = f"report_{uuid.uuid4().hex[:12]}"
    generated_at = datetime.now().isoformat()

    # ---- 模型标识（§6.1 model_id）：记录本次评分使用的模型，便于复核 ----
    model_id = ""
    try:
        _client = getattr(getattr(sm, "runner", None), "llm_client", None)
        model_id = str(getattr(_client, "model", "") or "")
    except Exception:
        model_id = ""

    ivs = list(getattr(sm, "interventions", []) or [])
    msgs = list(getattr(sm, "messages", []) or [])

    # ---- 最新一次完成的完整互动量规评分（报告以最近一次完成为准）----
    last_assessment: Optional[Dict] = None
    for iv in reversed(ivs):
        a = iv.get("assessment")
        if isinstance(a, dict) and a.get("status") == "scored":
            last_assessment = a
            break

    student_roles = ("bully", "victim", "bystander_a", "bystander_b")
    student_msgs = [m for m in msgs
                    if str(m.get("role", "") or m.get("sender_role", "")) in student_roles]

    # ---- 维度四态（计划书 §4.2：N/O ≠ 证据不足 ≠ 未评估 ≠ 低分）----
    dimensions: List[Dict] = []
    rated = no_opp = insuff = not_assessed = 0
    log_gap_note = ""

    if last_assessment is not None:
        dim_items = last_assessment.get("dimensions") or {}
        # 确定性证据不足检查：教师有干预但日志中无任何学生消息
        has_log_gap = bool(ivs) and not student_msgs
        for d in DIM_ORDER:
            item = dim_items.get(d) if isinstance(dim_items.get(d), dict) else {}
            guide = _DIM_GUIDE.get(d, {})
            score = item.get("score")
            rationale = str(item.get("rationale") or "").strip()
            evidence = _evidence_quotes(item)
            if item.get("status") == "N/O" or score is None:
                status = STATUS_NO
                no_opp += 1
                score_100 = None
            elif has_log_gap and d in STUDENT_EVIDENCE_DIMS:
                # 日志缺失：本应可能评价但记录不足 → 证据不足，不伪装成 N/O
                status = STATUS_INSUFFICIENT
                insuff += 1
                score_100 = None
            elif not evidence and not rationale:
                # 评分存在但既无证据也无理由 → 证据不足，不计入均分
                status = STATUS_INSUFFICIENT
                insuff += 1
                score_100 = None
            else:
                status = STATUS_SCORED
                rated += 1
                score_100 = score
            dimensions.append({
                "code": d,
                "name": guide.get("name", d),
                "status": status,
                "score_100": score_100,
                "band": _band_of(score_100),
                "evidence_refs": evidence,
                "rationale": rationale,
                "missing_behaviors": [guide.get("gap", "")] if (score_100 is not None and score_100 < 60 and guide.get("gap")) else [],
                "next_target": guide.get("fix", ""),
                "sample_utterance": guide.get("sample", ""),
                "retest_criteria": RETEST_CRITERIA.get(d, ""),
            })
        if has_log_gap:
            log_gap_note = ("本次日志中没有记录到任何学生发言，依赖学生反馈作证据的维度"
                            "（D2/D3/D7）按「证据不足」处理，不计入均分，不伪装成 N/O 或 0 分。")
    else:
        # 评分未完成（模型不可用 / 输出无效）→ 未评估，不伪造分数
        reason = ""
        for iv in ivs:
            a = iv.get("assessment")
            if isinstance(a, dict) and a.get("status") == "pending":
                reason = str(a.get("parse_error") or "")
                break
        for d in DIM_ORDER:
            guide = _DIM_GUIDE.get(d, {})
            dimensions.append({
                "code": d, "name": guide.get("name", d),
                "status": STATUS_NOT_ASSESSED, "score_100": None, "band": None,
                "evidence_refs": [], "rationale": "",
                "missing_behaviors": [], "next_target": "",
                "sample_utterance": "",
                "retest_criteria": "",
            })
            not_assessed += 1
        if ivs:
            log_gap_note = ("本次干预的量规评分未完成（未评估），报告不生成总分与能力结论；"
                            + (f"评分失败原因：{reason}" if reason else "请检查评分服务配置。"))

    # ---- scoring_status（计划书 §6.1 / RUB-10）----
    # 注意：必须存在至少一个可计分维度才允许 complete；
    # 全部 N/O 或全部证据不足 → not_evaluable（不生成总分）。
    if last_assessment is not None and rated > 0 and insuff == 0 and no_opp + rated == len(DIM_ORDER):
        scoring_status = "complete"
    elif last_assessment is not None and rated > 0:
        scoring_status = "partial"      # 部分维度证据不足/无机会
    elif last_assessment is not None:
        scoring_status = "not_evaluable"  # 有评分记录但无任何可计分维度
    elif ivs:
        scoring_status = "partial"       # 有干预但量规评分未完成（基础统计可用）
    else:
        scoring_status = "not_evaluable"  # 无干预记录

    # ---- summary ----
    summary: Dict[str, Any] = {
        "raw_mean_score_100": None,
        "final_quality_score_100": None,
        "risk_level": "none",
        "risk_cap_applied": False,
        "rated_count": rated,
        "no_opportunity_count": no_opp,
        "insufficient_evidence_count": insuff,
        "not_assessed_count": not_assessed,
    }
    if last_assessment is not None:
        summary["raw_mean_score_100"] = last_assessment.get("raw_mean_score_100")
        summary["final_quality_score_100"] = last_assessment.get("final_quality_score_100")
        summary["risk_level"] = last_assessment.get("risk_level") or "none"
        summary["risk_cap_applied"] = bool(last_assessment.get("risk_cap_applied"))

    # ---- risks（带证据引用与修正动作）----
    risks: List[Dict] = []
    if last_assessment is not None:
        ra = last_assessment.get("risk_assessment") or {}
        for f in (ra.get("flags_detail") or []):
            if not isinstance(f, dict):
                continue
            code = str(f.get("code", ""))
            risks.append({
                "code": code,
                "level": summary.get("risk_level", "none"),
                "evidence_refs": [str(e) for e in (f.get("evidence") or [])][:3],
                "context": f.get("context", ""),
                "rationale": str(f.get("context", "")),
                "recommended_correction": _DIM_GUIDE_RISK_FIX.get(code, ""),
            })

    # ---- metrics（基线→终点、百分点，模拟结果 ≠ 能力评价）----
    metrics: Dict[str, Any] = {}
    if trends:
        for key, label in (("bullying_metric", "bullying_intensity"),
                            ("distress_metric", "victim_distress")):
            m = trends.get(key)
            if isinstance(m, dict):
                metrics[label] = {
                    "baseline": m.get("baseline"),
                    "final": m.get("final"),
                    "delta_percentage_points": m.get("delta_percentage_points"),
                    "valid_sample_count": m.get("valid_sample_count"),
                    "source_window": m.get("source_window", ""),
                    "text": m.get("text", ""),
                }
    metrics["note"] = ("以上为模拟状态变化（0–1 状态比例，变化以百分点表述），"
                       "反映模拟演变结果，不等于教师能力评分，亦不构成因果结论。")

    # ---- bullying_type_profile（项目分类口径 + 公式 + 局限）----
    dim_analysis = (obvq_analysis or {}).get("dimension_analysis") or []
    bullying_type_profile = {
        "method": "项目自定义六类关键词/强度分类（借用 OBVQ 名称与 0–4 级频率口径，"
                  "但未按 OBVQ-R 标准量表的条目、施测对象与计分流程实施，非标准量表总分）",
        "version": "project-classification-v1",
        "categories": [
            {"name": c.get("name"), "total": c.get("total"), "level": c.get("level"),
             "description": c.get("description", "")}
            for c in dim_analysis
        ],
        "total_score": (obvq_analysis or {}).get("total_score"),
        "total_formula": ("每次识别到的欺凌事件按敌意强度映射为 0–4 分，累加到所有命中的类别；"
                           "总分 = min(80, 六类累计分之和)。同一事件可重复计入多个类别，"
                           "因此六类分项之和可能大于总分上限。"),
        "interpretive_limitations": ("该统计用于刻画模拟中出现的欺凌行为类型，"
                                     "不用于教师 D1–D7 能力评分，也不可与 OBVQ-R 标准分比较。"),
    }

    # ---- strengths / priority_growth_areas（≤3 项，来自结构化评估报告）----
    strengths: List[Dict] = []
    priorities: List[Dict] = []
    tr = training_report or {}
    profile = tr.get("capability_profile") or {}
    for row in (profile.get("dimensions") or []):
        avg = row.get("avg")
        if isinstance(avg, (int, float)) and avg >= 60:
            strengths.append({
                "dimension": row.get("dim"),
                "name": row.get("name"),
                "score": avg,
                "explanation": f"{row.get('name')} 均分 {avg}（{row.get('level')}），可作为已具备的能力基础。",
            })
        if len(strengths) >= 3:
            break
    es = tr.get("executive_summary") or {}
    for i, act in enumerate((es.get("top_actions") or [])[:3], 1):
        priorities.append({
            "priority": i,
            "dimension": act.get("dim"),
            "name": act.get("name"),
            "reason": f"{act.get('name')}是当前累计拉低总分最多的维度（预计补齐可提升约 {act.get('expected_gain')} 分）。",
            "action": act.get("action", ""),
        })

    # ---- training_plan（§6.1 / §8.2 标准结构：目标·步骤·示范·安全约束·达标·复测）----
    training_plan: List[Dict] = []
    for i, act in enumerate((es.get("top_actions") or [])[:3], 1):
        code = act.get("dim") or ""
        guide = _DIM_GUIDE.get(code, {})
        training_plan.append({
            "priority": i,
            "dimension": code,
            "objective": f"下一次干预中补齐「{act.get('name')}」要素",
            "actions": [x for x in [act.get("action", ""), guide.get("fix", "")] if x],
            "sample_utterance": guide.get("sample", ""),
            "safety_constraints": [
                "不安排受害者与欺凌者当面对质或和解",
                "不公开受害者身份与细节",
                "不把责任或原谅的决定权交给受害者",
            ],
            "success_criteria": RETEST_CRITERIA.get(code, ""),
            "retest_task": "在相似但不同的情境中独立完成一次干预，检查能否迁移而非背诵话术。",
        })

    # ---- reflection_questions（§6.1：报告生成侧的反思问题）----
    reflection_questions: List[str] = [
        "本轮你最先注意到的是谁（欺凌者 / 受害者 / 旁观者）？换一个人先回应会有什么不同？",
        "你的「停止要求」是否具体到可执行（对象 + 行为 + 立即停止）？",
        "跟进安排是否包含「谁、何时、做什么、何时复查」四要素？",
    ]
    _weakest = (profile.get("weakest") or {}).get("dim")
    if _weakest:
        reflection_questions.append(
            f"针对「{_DIM_GUIDE.get(_weakest, {}).get('name', _weakest)}」："
            "若时间只够说一句话，你会说哪一句？为什么？")
    if risks:
        reflection_questions.append(
            "本次触发的风险中，哪一条换一种说法就不会触发？请写出替换前后的两句。")

    # ---- data_notes（可复核性说明）----
    limitations: List[str] = []
    if log_gap_note:
        limitations.append(log_gap_note)
    if scoring_status != "complete":
        limitations.append("量规评价未完全完成：报告中的能力结论以已完成评分为准，缺失部分不按 0 分处理。")
    limitations.append("教师干预质量评分与模拟状态变化分别存储；前者为量规评价，后者为模拟演变结果。")

    session = {
        "session_id": getattr(sm, "session_id", None),
        "total_rounds": getattr(sm, "round", 0),
        "total_messages": len(msgs),
        "intervention_count": len(ivs),
        "skip_count": len(getattr(sm, "skip_decisions", []) or []),
        "duration_seconds": round((datetime.now() - getattr(sm, "started_at", datetime.now())).total_seconds()),
    }

    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "session_id": getattr(sm, "session_id", None),
        "generated_at": generated_at,
        "rubric_version": RUBRIC_VERSION,
        "scoring_prompt_version": SCORING_PROMPT_VERSION,
        "model_id": model_id,
        "scoring_status": scoring_status,
        "session": session,
        "summary": summary,
        "dimensions": dimensions,
        "risks": risks,
        "metrics": metrics,
        "bullying_type_profile": bullying_type_profile,
        "strengths": strengths,
        "priority_growth_areas": priorities,
        "training_plan": training_plan,
        "reflection_questions": reflection_questions,
        "interaction_timeline": _build_timeline(msgs, ivs, round_analysis),
        "data_notes": {
            "limitations": limitations,
            "stable_threshold_pp": (trends or {}).get("stable_threshold_pp"),
            "unit_note": (trends or {}).get("unit_note", ""),
            "score_formula": "总分 = 有效评分维度等权平均（N/O 与证据不足不计入分母，也不按 0 分计）；"
                             "严重风险时总分封顶 60（仅当原始均分高于 60 时生效）。",
        },
    }


# 风险代码 → 正向修正动作（与 coaching.RISK_GUIDE 对齐；延迟导入避免循环依赖）
def _load_risk_fix_map() -> Dict[str, str]:
    try:
        from multi_agent.coaching import RISK_GUIDE
        return {code: g.get("fix", "") or g.get("advice", "") for code, g in RISK_GUIDE.items()}
    except ImportError:
        return {}


_DIM_GUIDE_RISK_FIX = _load_risk_fix_map()


def record_report_meta(session_data: Dict, report_data: Dict) -> None:
    """报告快照元数据：保留历史版本记录（计划书 §6.3 重新生成不静默覆盖）。"""
    history = session_data.setdefault("report_history", [])
    history.append({
        "report_id": report_data.get("report_id"),
        "generated_at": report_data.get("generated_at"),
        "scoring_status": report_data.get("scoring_status"),
        "final_quality_score_100": (report_data.get("summary") or {}).get("final_quality_score_100"),
        "rubric_version": report_data.get("rubric_version"),
    })
    if len(history) > 20:
        del history[:len(history) - 20]

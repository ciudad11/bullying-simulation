# -*- coding: utf-8 -*-
"""
Coaching - 用户指导引擎（R14）

提供三类指导能力，全部为纯函数（不依赖 LLM、不写状态，便于测试）：

1. `build_round_analysis()`  逐轮对话分析与针对性改进建议（最终报告用）
2. `build_improvement_plan()` 汇总改进方案（维度缺口排序 / 优先改进项 / 可套用话术 / 行动计划）
3. `build_real_time_coaching()` 干预发送后的实时指导（模拟过程中即时提示）
4. `build_streak_alert()` 连续 N 轮低于阈值时的及时预警
5. `build_writing_checklist()` 干预输入前的写作提示（预防性指导）

指导依据全部来自专家评分明细（各维分数/证据/理由、风险 flags 明细）与原始对话，
不使用关键词猜测，也不改动任何评分口径。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

DIMS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7"]

DEFAULT_THRESHOLD = 60

# ============================================================
# 维度指导库：失分表现 → 原因 → 可执行改法 → 可套用话术
# ============================================================
DIM_GUIDE: Dict[str, Dict[str, str]] = {
    "D1": {
        "name": "欺凌识别与制止",
        "gap": "未识别核心欺凌，或只有温和口头劝阻，缺少「行为定性 + 明确停止要求」",
        "why": "量规要求明确指出具体行为不可接受并给出清晰、可执行的停止要求；"
               "「别闹了」「不要这样」等无对象劝阻属于初步不足。",
        "fix": "先用一句话给行为定性（这是言语欺凌/关系欺凌，我明确不接受），"
               "再给出对象明确的停止要求（请立刻停止对 XX 的嘲笑/起哄）。",
        "sample": "「韩雪，你不要攻击同学——这是言语欺凌，我明确不接受。请立刻停止对温言的嘲笑。」",
        "avoid": "避免只说「别闹了」「不要这样」或只处理旁支小节。",
    },
    "D2": {
        "name": "受害者保护与心理支持",
        "gap": "只表达共情，未做安全确认与具体保护；或把决定权交回受害者",
        "why": "量规要求兼顾安全、情绪与实际需要；把「要不要原谅」交给受害者属于把责任转给受害者。",
        "fix": "先确认安全（你现在安全吗），再给具体保护安排（安排座位/陪同离场/单独谈话），最后才谈感受。",
        "sample": "「小宇，你现在安全吗？我先安排你坐到老师旁边，下课我陪你到办公室，我们再慢慢说。」",
        "avoid": "避免用「你愿意原谅他吗」把停止条件的决定权交给受害者。",
    },
    "D3": {
        "name": "欺凌者责任认识与行为改变",
        "gap": "只有口头劝导或要求道歉，未说明行为影响，也未提出可执行的改变要求",
        "why": "量规要求使实施者承担责任并改变行为；「不是故意的」不能替代责任说明，仅要求道歉不等于责任已改变。",
        "fix": "说明行为对受害者的影响，再给具体改变要求（以后想开玩笑前先问对方 / 换位想一想）。",
        "sample": "「你这样说会让小宇觉得自己被攻击、被孤立；以后想开玩笑之前先问一句对方愿不愿意。」",
        "avoid": "避免用「不是故意的」「你也是好孩子」消解责任。",
    },
    "D4": {
        "name": "情境适配与风险控制",
        "gap": "淡化伤害性质、忽视权力差异，或在未评估安全时安排双方碰面/和解",
        "why": "量规要求策略与风险匹配；强迫对质、暴露受害者会造成报复风险，且不能被其他维度高分抵消。",
        "fix": "不淡化行为定性；高风险或权力不对等时先分开谈话并安排保护，不安排双方当面对质。",
        "sample": "「我先分别和你们谈，今天不安排你们碰面；我要确保温言这段时间是安全的。」",
        "avoid": "避免「你们都是同学，一起沟通一下」这类促成和解的安排。",
    },
    "D5": {
        "name": "同伴与群体规范",
        "gap": "未涉及旁观者与群体规范，起哄未被处理",
        "why": "量规要求适当影响旁观者与群体氛围；旁观者沉默会被读作默许。",
        "fix": "明确班级反欺凌规范与报告路径，并提醒起哄者也需承担相应责任。",
        "sample": "「我们班的规矩是不允许起哄和嘲笑；看到有人被欺负要告诉老师，在场的每个人都有责任。」",
        "avoid": "避免只对欺凌者说话、完全不涉及旁观者与群体规范。",
    },
    "D6": {
        "name": "长期欺凌干预与跟进",
        "gap": "没有任何调查、报告、保护、复查安排（仅口头承诺）",
        "why": "量规要求行动、责任主体与跟进方式明确；「我会处理」属于初步不足，持续危险下会被判为低分。",
        "fix": "把跟进说成可执行安排：谁做、什么时候、做什么、什么时候复查。",
        "sample": "「我今天放学前分别找你们谈一次，周三我再过来确认情况，后续需要时会报告德育处。」",
        "avoid": "避免只说「我会处理」；每轮至少补一句带时间点的跟进。",
    },
    "D7": {
        "name": "多轮回应与调整",
        "gap": "重复同一套话术，未依据学生新反应调整",
        "why": "量规要求根据新信息调整策略；连续几轮使用同一逻辑会被判为机械重复。",
        "fix": "引用学生刚才的反应再调整策略：回应他说的内容，并说明为什么现在这样做。",
        "sample": "「你说你不是故意的，但温言刚才已经哭了，所以我们先分开谈，这周我每天都会关注。」",
        "avoid": "避免多轮使用完全相同的安抚句式而不回应新信息。",
    },
}

# ============================================================
# 风险指导库（与 intervention_expert.RISK_DEFS 对应）
# ============================================================
RISK_GUIDE: Dict[str, Dict[str, str]] = {
    "R_VICTIM_BLAME": {
        "label": "责备或羞辱受害者",
        "advice": "不要把责任、原谅或停止条件的决定权交给受害者；由教师承担裁定，并明确记录与跟进其感受。",
        "fix": "由教师承担裁定：改为「这件事由我处理，你的感受我会记录并跟进」；若需回应受害者，"
               "用「你的感受很重要，我们先确保你安全」而非征求其是否原谅。",
        "avoid": "避免「你愿意原谅他吗」「都是你太敏感了」这类表述。",
    },
    "R_MINIMIZE_HARM": {
        "label": "淡化或否认欺凌伤害",
        "advice": "只评价行为，不评价动机；把伤害说成「闹着玩」「不是故意的」「没关系」会被判定为淡化伤害。",
        "fix": "只评价行为、不评价动机：把「不是故意的、闹着玩」换成「我理解你的出发点，但我不接受这种行为」；"
               "把「没关系」换成「这件事对他是有伤害的」。",
        "avoid": "避免「不小心」「没关系」「都是开玩笑」。",
    },
    "R_FORCED_CONFRONTATION": {
        "label": "强迫受害者对质或和解",
        "advice": "未评估安全与受害者意愿前，不安排双方碰面、不促成和解、不要求当众复述。",
        "fix": "改为分开处理：把「你们一起沟通」换成「我先分别和你们谈，今天不安排你们碰面；"
               "我要确保××这段时间是安全的」。",
        "avoid": "避免「你们俩谈谈」「达成个约定」「互相原谅一下」。",
    },
    "R_EXPOSURE_RETALIATION": {
        "label": "暴露受害者并增加报复风险",
        "advice": "不公开受害者身份或细节，不要求当众复述经过；保护措施到位前避免公开讨论。",
        "fix": "保护措施到位前不公开讨论：不点名受害者复述经过，改为「我看到刚才有人在笑一位同学，"
               "我们下课单独说」；必要时安排其暂时离开教室。",
        "avoid": "避免在全班面前点名受害者或让其讲述细节。",
    },
    "R_PROMISE_NO_ACTION": {
        "label": "有持续危险却仅口头承诺",
        "advice": "「我会处理」不构成跟进；已知危险仍在继续时，必须给出行动、责任人与时间，否则持续性不会下降。",
        "fix": "把承诺变成安排：把「我会处理」换成「今天放学前我分别找你们谈一次，周三我再过来确认情况，"
               "必要时报告德育处」。",
        "avoid": "避免只说「我会处理」「以后不会了」而没有具体安排。",
    },
}

BAND_LABELS = [
    (80, "清晰、具体、适切"),
    (60, "基本达标"),
    (40, "初步不足"),
    (0, "明显不当"),
]


def _band(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    for lo, name in BAND_LABELS:
        if score >= lo:
            return name
    return "明显不当"


def _truncate(text: Any, limit: int = 60) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[:limit] + "…"


def _assessment_of(intervention: Dict) -> Dict:
    a = intervention.get("assessment")
    return a if isinstance(a, dict) else {}


def _dimension_scores(assessment: Dict) -> Dict[str, Optional[int]]:
    """从 assessment 提取七维分数（N/O 为 None）。"""
    out: Dict[str, Optional[int]] = {}
    dims = assessment.get("dimensions") or {}
    for d in DIMS:
        item = dims.get(d)
        if isinstance(item, dict):
            if item.get("status") == "N/O" or item.get("score") is None:
                out[d] = None
            else:
                try:
                    out[d] = int(item.get("score"))
                except (TypeError, ValueError):
                    out[d] = None
        else:
            out[d] = None
    return out


def _dimension_reason(assessment: Dict, dim: str) -> str:
    dims = assessment.get("dimensions") or {}
    item = dims.get(dim)
    if not isinstance(item, dict):
        return ""
    reason = str(item.get("rationale") or "").strip()
    evidence = item.get("evidence") or []
    if isinstance(evidence, list) and evidence:
        ev = _truncate(evidence[0], 40)
        reason = f"{reason}（原文依据：{ev}）" if reason else f"原文依据：{ev}"
    return reason


def _risk_flags(assessment: Dict) -> List[Dict]:
    """返回 [{code, label, evidence, context, confidence}]。"""
    ra = assessment.get("risk_assessment") or {}
    detail = ra.get("flags_detail")
    out: List[Dict] = []
    if isinstance(detail, list) and detail:
        for d in detail:
            if not isinstance(d, dict):
                continue
            code = str(d.get("code", ""))
            guide = RISK_GUIDE.get(code, {})
            out.append({
                "code": code,
                "label": guide.get("label", code),
                "evidence": d.get("evidence") or [],
                "context": d.get("context", ""),
                "confidence": d.get("confidence", 0),
                "advice": guide.get("advice", ""),
                "fix": guide.get("fix", ""),
                "avoid": guide.get("avoid", ""),
            })
        return out
    # 兼容仅有 flags 的旧记录
    flags = ra.get("flags") or []
    if isinstance(flags, list):
        for c in flags:
            code = str(c)
            guide = RISK_GUIDE.get(code, {})
            out.append({
                "code": code, "label": guide.get("label", code), "evidence": [],
                "context": "", "confidence": 0,
                "advice": guide.get("advice", ""), "fix": guide.get("fix", ""),
                "avoid": guide.get("avoid", ""),
            })
    return out


# ============================================================
# 1. 逐轮分析
# ============================================================
def _build_model_answer(weak: List[Dict], risks: List[Dict],
                        threshold: int = DEFAULT_THRESHOLD) -> Dict:
    """
    生成一轮干预的完整示范话术（报告阶段专用）。

    约束：模拟过程中不提供可照抄话术（R15），仅在最终评估报告中作为教学参考给出。
    """
    focus_dims = [w.get("dim") for w in (weak or [])]
    elements = [
        {"label": "① 行为定性 + 明确停止要求", "dim": "D1", "text": DIM_GUIDE["D1"]["sample"]},
        {"label": "② 受害者保护与安全确认", "dim": "D2", "text": DIM_GUIDE["D2"]["sample"]},
        {"label": "③ 责任与行为改变", "dim": "D3", "text": DIM_GUIDE["D3"]["sample"]},
        {"label": "④ 跟进与复查安排", "dim": "D6", "text": DIM_GUIDE["D6"]["sample"]},
        {"label": "⑤ 群体规范与报告路径", "dim": "D5", "text": DIM_GUIDE["D5"]["sample"]},
    ]
    if risks:
        elements.insert(2, {"label": "⚠ 风险规避：先分开处理，不安排当面对质",
                            "dim": "D4", "text": DIM_GUIDE["D4"]["sample"]})
    # 优先呈现本轮最需要补强的要素
    elements.sort(key=lambda e: 0 if e["dim"] in focus_dims else 1)
    full = " ".join(e["text"] for e in elements)
    return {
        "elements": elements,
        "full": full,
        "focus_dims": focus_dims,
        "note": "示范话术已按本轮失分维度排序，优先补齐前两项即可显著提升本轮得分。",
    }


def build_round_analysis(round_no: int, messages: List[Dict], intervention: Optional[Dict],
                         threshold: int = DEFAULT_THRESHOLD) -> Dict:
    """对某一轮做逐轮分析：情境概括 + 评分 + 失分维度 + 针对性改进建议 + 下一步动作。"""
    messages = messages or []
    roles = {"bully": [], "victim": [], "bystander_a": [], "bystander_b": [], "teacher": []}
    for m in messages:
        r = m.get("role")
        if r in roles:
            roles[r].append(m)

    analysis: Dict[str, Any] = {
        "round": round_no,
        "has_intervention": False,
        "score": None,
        "band": None,
        "raw_score": None,
        "risk_level": None,
        "risk_cap_applied": False,
        "dialogue_counts": {k: len(v) for k, v in roles.items() if v},
        "event_summary": "",
        "what_you_did": "",
        "student_reactions": [],
        "dimension_scores": {},
        "dimension_detail": [],
        "weak_dimensions": [],
        "risk_flags": [],
        "improvement_actions": [],
        "teacher_alignment": [],
        "model_answer": "",
        "coach_comment": "",
        "next_step": "",
    }

    # —— 情境概括 ——
    parts = []
    if roles["bully"]:
        parts.append("欺凌者：" + _truncate(roles["bully"][0].get("content"), 46))
    if roles["victim"]:
        parts.append("受害者：" + _truncate(roles["victim"][0].get("content"), 40))
    if roles["bystander_a"] or roles["bystander_b"]:
        nb = len(roles["bystander_a"]) + len(roles["bystander_b"])
        parts.append(f"旁观者发言 {nb} 次")
    if not parts:
        parts.append("本轮无实质对话")
    if not roles["bully"]:
        parts.append("（本轮未出现新的欺凌发言）")
    analysis["event_summary"] = "；".join(parts)

    # —— 学生反应明细（逐条列出，便于教师核对现场情境）——
    reactions = []
    for role, label in (("bully", "欺凌者"), ("victim", "受害者"),
                        ("bystander_a", "旁观者A"), ("bystander_b", "旁观者B")):
        for m in roles[role]:
            reactions.append({
                "role": role,
                "label": label,
                "name": m.get("sender_name") or m.get("sender") or "",
                "content": _truncate(m.get("content"), 80),
            })
    analysis["student_reactions"] = reactions

    if not intervention:
        analysis["coach_comment"] = "本轮未进行干预：可在欺凌行为发生时及时介入。"
        analysis["next_step"] = "下一轮遇到新的欺凌发言时，请给出「行为定性 + 明确停止要求」。"
        return analysis

    analysis["has_intervention"] = True
    content = intervention.get("content", "")
    analysis["what_you_did"] = _truncate(content, 80)
    score = intervention.get("quality_score")
    analysis["score"] = score
    analysis["band"] = _band(score)

    assessment = _assessment_of(intervention)
    dims = _dimension_scores(assessment)
    analysis["dimension_scores"] = dims
    analysis["risk_flags"] = _risk_flags(assessment)
    analysis["risk_level"] = assessment.get("risk_level")
    analysis["risk_cap_applied"] = bool(assessment.get("risk_cap_applied"))
    analysis["raw_score"] = assessment.get("raw_mean_score_100")

    # —— 全维度明细（含达标维度、专家理由与原文证据）——
    dim_items = assessment.get("dimensions") or {}
    detail = []
    for d in DIMS:
        v = dims.get(d)
        guide = DIM_GUIDE.get(d, {})
        item = dim_items.get(d) if isinstance(dim_items, dict) else None
        item = item if isinstance(item, dict) else {}
        ev = item.get("evidence") or []
        if not isinstance(ev, list):
            ev = [ev]
        detail.append({
            "dim": d,
            "name": guide.get("name", d),
            "score": v,
            "band": _band(v),
            "status": item.get("status") or ("scored" if v is not None else "N/O"),
            "is_weak": (v is not None and v < threshold),
            "requirement": guide.get("why", ""),
            "gap": guide.get("gap", ""),
            "reason": _dimension_reason(assessment, d),
            "evidence": [_truncate(e, 70) for e in ev[:2] if e],
            "advice": guide.get("fix", ""),
            "avoid": guide.get("avoid", ""),
            "sample": guide.get("sample", ""),
        })
    analysis["dimension_detail"] = detail

    scored = [(d, v) for d, v in dims.items() if v is not None]
    n_scored = len(scored) or 1
    weak = []
    for d, v in sorted(scored, key=lambda x: x[1]):
        if v >= threshold:
            continue
        guide = DIM_GUIDE.get(d, {})
        weak.append({
            "dim": d,
            "name": guide.get("name", d),
            "score": v,
            "band": _band(v),
            "gap_points": round(threshold - v, 1),
            # 该维度对「未达标」的贡献：拉低总分（阈值 - 实际分）÷ 有效维度数
            "contribution": round((threshold - v) / n_scored, 2),
            "gap": guide.get("gap", ""),
            "reason": _dimension_reason(assessment, d),
            "advice": guide.get("fix", ""),
            "sample": guide.get("sample", ""),
            "avoid": guide.get("avoid", ""),
        })
    analysis["weak_dimensions"] = weak

    # —— 分条改进动作（可勾选，含预计贡献）——
    actions = []
    for w in weak:
        actions.append({
            "dim": w["dim"],
            "name": w["name"],
            "text": f"补齐「{w['name']}」：{w['advice']}",
            "avoid": w.get("avoid", ""),
            "expected_gain": w.get("contribution", 0.0),
            "kind": "dimension",
        })
    for r in analysis["risk_flags"][:2]:
        actions.append({
            "dim": None,
            "name": r.get("label", ""),
            "text": f"规避「{r.get('label', '')}」：{r.get('advice', '')}",
            "avoid": r.get("avoid", ""),
            "expected_gain": 0.0,
            "kind": "risk",
        })
    analysis["improvement_actions"] = actions

    # —— 你的处理方式 vs 量规要求（逐项对照）——
    align = []
    for item in detail:
        if item["score"] is None:
            verdict = "无可观察证据，判 N/O（不计入平均分）"
        elif item["is_weak"]:
            verdict = f"不符合「{item['band']}」要求（{item['score']} 分）"
        elif item["score"] >= 80:
            verdict = "符合要求，可作为范例"
        else:
            verdict = f"符合基本要求（{item['score']} 分），仍可再具体化"
        align.append({
            "dim": item["dim"],
            "name": item["name"],
            "requirement": item["requirement"],
            "your_gap": item["gap"] if item["is_weak"] else "",
            "your_reason": item["reason"],
            "verdict": verdict,
            "fix": item["advice"],
        })
    analysis["teacher_alignment"] = align

    # —— 本轮示范话术（报告阶段可给出完整参考答案；模拟过程中不提供）——
    analysis["model_answer"] = _build_model_answer(weak, analysis["risk_flags"], threshold)

    # —— 点评与下一步 ——
    if score is None:
        analysis["coach_comment"] = "本轮评分待评估：专家评分未返回有效结构化结果，无法给出针对性点评。"
        analysis["next_step"] = "请检查模型/API 配置，确保专家评分可用后再评估干预效果。"
    elif score >= 80:
        analysis["coach_comment"] = f"本轮 {score} 分（{analysis['band']}）：表达清晰、具体且适切，可作为范例。"
        analysis["next_step"] = "保持这一表达结构：行为定性 → 停止要求 → 责任与改变 → 跟进安排。"
    elif score >= threshold:
        analysis["coach_comment"] = f"本轮 {score} 分（{analysis['band']}）：整体达标，" + (
            "、".join(f"{w['name']}仅 {w['score']}" for w in weak[:2]) + " 仍可再打磨。")
        analysis["next_step"] = (weak[0]["advice"] if weak else "保持当前结构并补充跟进安排。")
    else:
        top = weak[0] if weak else None
        analysis["coach_comment"] = (
            f"本轮 {score} 分（{analysis['band']}）：" +
            (f"最主要是「{top['name']}」仅 {top['score']} 分" if top else "各维度均有提升空间") +
            "；" + (top["gap"] if top else ""))
        analysis["next_step"] = (top["advice"] if top else "按四要素模板补全干预表达。")

    if analysis["risk_flags"]:
        analysis["next_step"] += " 另需规避风险：" + "；".join(r["advice"] for r in analysis["risk_flags"][:2])

    return analysis


# ============================================================
# 2. 汇总改进方案
# ============================================================
def build_improvement_plan(round_analyses: List[Dict], threshold: int = DEFAULT_THRESHOLD) -> Dict:
    """汇总逐轮分析 → 维度缺口、优先改进项、可套用话术、行动计划。"""
    analyses = [a for a in (round_analyses or []) if isinstance(a, dict)]
    scored = [a for a in analyses if a.get("score") is not None]
    plan: Dict[str, Any] = {
        "rounds_analyzed": len(analyses),
        "rounds_with_intervention": len(scored),
        "summary": "",
        "dimension_gaps": [],
        "priorities": [],
        "risk_reminders": [],
        "templates": [],
        "action_plan": [],
        "round_reviews": [],
    }
    if not analyses:
        plan["summary"] = "本次模拟没有可分析的轮次。"
        return plan

    # —— 维度缺口汇总 ——
    agg: Dict[str, Dict] = {}
    for a in scored:
        for w in a.get("weak_dimensions", []):
            d = w["dim"]
            slot = agg.setdefault(d, {"dim": d, "name": w["name"], "scores": [], "gap_points": 0.0,
                                      "contribution": 0.0, "advice": w["advice"], "sample": w["sample"],
                                      "avoid": w["avoid"], "gap": w["gap"], "rounds": [],
                                      "reasons": []})
            slot["scores"].append(w["score"])
            slot["gap_points"] += w["gap_points"]
            slot["contribution"] += w["contribution"]
            slot["rounds"].append(a.get("round"))
            if w.get("reason"):
                slot["reasons"].append(f"第{a.get('round')}轮：{w['reason']}")
    gaps = []
    for d, slot in agg.items():
        gaps.append({
            "dim": d,
            "name": slot["name"],
            "avg_score": round(sum(slot["scores"]) / len(slot["scores"]), 1),
            "lowest_score": min(slot["scores"]),
            "times_below": len(slot["scores"]),
            "gap_points": round(slot["gap_points"], 1),
            "contribution": round(slot["contribution"], 2),
            "advice": slot["advice"],
            "sample": slot["sample"],
            "avoid": slot["avoid"],
            "gap": slot["gap"],
            "reasons": slot["reasons"][:4],
            "rounds": slot["rounds"],
        })
    gaps.sort(key=lambda x: -x["contribution"])
    plan["dimension_gaps"] = gaps

    # —— 优先改进项（Top3）——
    for i, g in enumerate(gaps[:3], 1):
        plan["priorities"].append({
            "rank": i,
            "dim": g["dim"],
            "name": g["name"],
            "why": f"{g['times_below']} 轮低于 {threshold} 分，平均 {g['avg_score']} 分，"
                   f"累计拉低总分约 {g['gap_points']} 分（占总缺口 {round(g['contribution'] / max(sum(x['contribution'] for x in gaps), 0.01) * 100, 1)}%）",
            "action": g["advice"],
            "sample": g["sample"],
            "avoid": g["avoid"],
        })

    # —— 风险提醒 ——
    risk_count: Dict[str, Dict] = {}
    for a in scored:
        for r in a.get("risk_flags", []):
            slot = risk_count.setdefault(r["code"], {"code": r["code"], "label": r.get("label", r["code"]),
                                                      "advice": r.get("advice", ""), "avoid": r.get("avoid", ""),
                                                      "rounds": [], "evidence": []})
            slot["rounds"].append(a.get("round"))
            if r.get("evidence"):
                slot["evidence"].append(_truncate(r["evidence"][0], 50))
    plan["risk_reminders"] = [
        {**v, "times": len(v["rounds"]), "evidence": v["evidence"][:3]} for v in risk_count.values()
    ]
    plan["risk_reminders"].sort(key=lambda x: -x["times"])

    # —— 可套用话术（按薄弱维度 + 风险生成）——
    templates = []
    for p in plan["priorities"]:
        if p.get("sample"):
            templates.append({"target": f"{p['name']}（{p['dim']}）", "text": p["sample"],
                              "why": p["action"]})
    for r in plan["risk_reminders"][:2]:
        templates.append({"target": f"规避风险：{r['label']}", "text": r.get("avoid", ""),
                          "why": r.get("advice", "")})
    if not templates:
        templates.append({"target": "通用四要素模板",
                          "text": "「这是××欺凌，我明确不接受。请立刻停止××。这样会让××感到受伤，你需要在××前做出改变。我今天分别找你们谈一次，××我再确认情况。」",
                          "why": "同时覆盖行为定性、停止要求、责任与改变、跟进安排。"})
    plan["templates"] = templates

    # —— 行动计划（可勾选）——
    action_plan = []
    for p in plan["priorities"]:
        action_plan.append(f"下一轮干预中补齐「{p['name']}」要素：{p['action']}")
    for r in plan["risk_reminders"][:2]:
        action_plan.append(f"规避「{r['label']}」：{r.get('avoid', '')}")
    if not action_plan:
        action_plan.append("保持当前的「定性 → 制止 → 责任 → 跟进」表达结构，并继续补充具体时间点的跟进安排。")
    plan["action_plan"] = action_plan

    # —— 逐轮简表 ——
    plan["round_reviews"] = [{
        "round": a.get("round"),
        "score": a.get("score"),
        "band": a.get("band"),
        "has_intervention": a.get("has_intervention"),
        "coach_comment": a.get("coach_comment"),
        "weak": [f"{w['dim']}={w['score']}" for w in a.get("weak_dimensions", [])],
        "risks": [r.get("label") for r in a.get("risk_flags", [])],
    } for a in analyses]

    # —— 总体诊断 ——
    if scored:
        avg = sum(a["score"] for a in scored) / len(scored)
        below = sum(1 for a in scored if a["score"] < threshold)
        top = gaps[0]["name"] if gaps else "—"
        plan["summary"] = (
            f"共分析 {len(analyses)} 轮，其中 {len(scored)} 轮进行了干预；"
            f"干预平均 {avg:.1f} 分，低于 {threshold} 分的 {below} 轮。"
            f"失分最集中的维度是「{top}」，"
            + ("其累计拉低总分最多，是下一阶段最应优先补强的部分。" if gaps else "各维度表现均衡。")
        )
    else:
        plan["summary"] = f"共分析 {len(analyses)} 轮，但没有产生可用的专家评分，无法给出维度级改进建议。"
    return plan


# ============================================================
# 3. 实时指导（干预发送后立即返回；仅策略方向，不给答案）
# ============================================================
def build_real_time_coaching(intervention: Dict, threshold: int = DEFAULT_THRESHOLD,
                             messages: Optional[List[Dict]] = None) -> Dict:
    """干预发送后的实时指导：最强的一条改进建议 + 风险提醒 + 下一步动作。

    R15 约束：只描述策略方向（做什么），严禁给出可照抄的话术（不给答案）。
    messages：本轮对话消息，用于生成情境化提示。
    """
    score = intervention.get("quality_score")
    assessment = _assessment_of(intervention)
    dims = _dimension_scores(assessment)
    risks = _risk_flags(assessment)

    scored = [(d, v) for d, v in dims.items() if v is not None]
    n_scored = len(scored) or 1
    ranked = sorted(
        [d for d in scored if d[1] < threshold],
        key=lambda x: (threshold - x[1]) / n_scored, reverse=True)

    focus = None
    if ranked:
        d, v = ranked[0]
        guide = DIM_GUIDE.get(d, {})
        focus = {
            "dim": d,
            "name": guide.get("name", d),
            "score": v,
            "band": _band(v),
            "gap": guide.get("gap", ""),
            # 策略方向（不含可照抄话术）
            "advice": STRATEGY_HINTS.get(d, guide.get("fix", "")),
            "avoid": guide.get("avoid", ""),
        }

    # 全部低于阈值的维度（供连续低分预警聚合使用）
    weak_dims = []
    for d, v in sorted(scored, key=lambda x: x[1]):
        if v >= threshold:
            continue
        guide = DIM_GUIDE.get(d, {})
        weak_dims.append({
            "dim": d,
            "name": guide.get("name", d),
            "score": v,
            "band": _band(v),
            "advice": STRATEGY_HINTS.get(d, guide.get("fix", "")),
        })

    if score is None:
        headline = "本轮评分待评估，暂时无法给出针对性指导。"
    elif score >= 80:
        headline = f"本轮 {score} 分，表达清晰具体，继续保持。"
    elif score >= threshold:
        headline = f"本轮 {score} 分（基本达标）。"
    else:
        headline = f"本轮 {score} 分（{_band(score)}），尚未达到基本达标。"

    tips = []
    if focus:
        tips.append(f"最需改进：{focus['name']}（{focus['score']} 分）——{focus['advice']}")
    if risks:
        tips.append("风险提醒：" + "；".join(r["advice"] for r in risks[:2]))
    if not tips:
        tips.append("一次有效的干预通常同时覆盖：明确制止、受害者保护、责任要求与后续跟进。")

    return {
        "score": score,
        "band": _band(score),
        "headline": headline,
        "focus": focus,
        "weak_dimensions": weak_dims,
        "dimension_scores": dims,
        "risk_flags": risks,
        "tips": tips,
        # R15：情境化策略提示（最小限度帮助，不含可照抄话术）
        "context_hints": build_context_hints(messages, weak_dims, risks, threshold),
    }


# ============================================================
# 4. 连续低分预警
# ============================================================
def build_streak_alert(streak_records: List[Dict], threshold: int = DEFAULT_THRESHOLD,
                       min_streak: int = 3) -> Optional[Dict]:
    """连续多轮低于阈值时给出预警；未触发返回 None。

    streak_records: [{round, score, weak_dimensions:[{dim,name,score}], risk_flags:[...]}]
    """
    recs = [r for r in (streak_records or []) if r.get("score") is not None]
    if len(recs) < min_streak:
        return None
    if not all(r["score"] < threshold for r in recs[-min_streak:]):
        return None

    window = recs[-min_streak:]
    dim_agg: Dict[str, Dict] = {}
    risk_agg: Dict[str, Dict] = {}
    for r in window:
        for w in r.get("weak_dimensions", []):
            slot = dim_agg.setdefault(w["dim"], {"dim": w["dim"], "name": w.get("name", w["dim"]),
                                                  "scores": [], "advice": w.get("advice", "")})
            slot["scores"].append(w.get("score", 0))
        for f in r.get("risk_flags", []):
            code = f.get("code", "")
            slot = risk_agg.setdefault(code, {"code": code, "label": f.get("label", code),
                                              "advice": f.get("advice", "")})
            slot["times"] = slot.get("times", 0) + 1

    worst = sorted(dim_agg.items(), key=lambda kv: sum(kv[1]["scores"]) / len(kv[1]["scores"]))
    diagnosis_parts = []
    for _, v in worst[:3]:
        diagnosis_parts.append(f"{v['name']}（平均 {sum(v['scores']) / len(v['scores']):.0f}）")
    diagnosis = "主要失分维度：" + "、".join(diagnosis_parts) if diagnosis_parts else "各维度均有提升空间"

    actions = []
    for _, v in worst[:2]:
        if v.get("advice"):
            actions.append(v["advice"])
    for r in list(risk_agg.values())[:2]:
        if r.get("advice"):
            actions.append(r["advice"])
    if not actions:
        actions.append("一次有效的干预通常同时覆盖：明确制止、受害者保护、责任要求与后续跟进。")

    # R15：只给策略方向，不给可照抄话术（不给答案）
    strategies = []
    for dim_key, v in worst[:3]:
        strategy = STRATEGY_HINTS.get(dim_key, "")
        if strategy:
            strategies.append(f"{v.get('name', dim_key)}：{strategy}")
    for r in list(risk_agg.values())[:2]:
        if r.get("advice"):
            strategies.append(f"规避 {r.get('label', '')}：{r['advice']}")
    if not strategies:
        strategies.append("对照本轮情境检查：是否明确制止、是否保护受害者、是否有可核查的跟进。")

    return {
        "triggered": True,
        "streak": len(window),
        "threshold": threshold,
        "rounds": [{"round": r.get("round"), "score": r.get("score")} for r in window],
        "headline": f"已连续 {len(window)} 轮干预评分低于 {threshold} 分，建议立即调整干预方式",
        "diagnosis": diagnosis,
        "actions": actions,
        "strategies": strategies,
        "severity": "warning",
    }


# ============================================================
# 5. 情境化策略提示（最小限度帮助）
# ============================================================
# 严格约束（R15）：模拟过程中的提示只描述「应做什么」（策略层面），
# 严禁提供可直接照抄的话术（不给答案）。以下提示均不含示例句。
STRATEGY_HINTS: Dict[str, str] = {
    "D1": "点名带头者的具体行为，并给出对象明确、可执行的停止要求；避免无对象的泛泛劝阻。",
    "D2": "先确认受害者当前是否安全，再给出具体的保护安排（如调整接触机会、安排单独谈话）。",
    "D3": "说明该行为对受害者造成的实际影响，并提出可核查的行为改变要求。",
    "D4": "策略须与风险匹配：不淡化行为性质；未确认安全前不安排双方当面对质。",
    "D5": "明确班级对这类行为的要求与报告路径，提示旁观者同样负有责任。",
    "D6": "给出可核查的后续安排：由谁、在何时、做什么、何时复查。",
    "D7": "回应学生本轮的新反应并据此调整，而不是重复上一轮的说法。",
}


def build_context_hints(messages: Optional[List[Dict]],
                        weak_dims: Optional[List[Dict]],
                        risks: Optional[List[Dict]],
                        threshold: int = DEFAULT_THRESHOLD) -> List[Dict]:
    """依据本轮实际对话 + 近期失分点，给出最小限度的策略提示。

    约束：只描述策略方向（做什么），不给出可照抄的话术（不给答案）；
    提示文本中不得出现引号包裹的示例句。
    """
    hints: List[Dict] = []
    messages = messages or []

    # —— 情境线索（来自本轮真实对话） ——
    bully = [m for m in messages if m.get("role") == "bully"]
    victim = [m for m in messages if m.get("role") == "victim"]
    bystanders = [m for m in messages if m.get("role") in ("bystander_a", "bystander_b")]
    if bully:
        hints.append({
            "title": "情境线索：本轮仍有攻击发言",
            "text": "欺凌者有针对性的发言，注意识别带头者与具体行为，而非只作整体劝阻。",
        })
    if victim:
        hints.append({
            "title": "情境线索：受害者有反应",
            "text": "受害者本轮有回应，可先确认其当前状态是否安全，再处理行为问题。",
        })
    if bystanders:
        hints.append({
            "title": "情境线索：旁观者在场",
            "text": "有旁观者参与讨论，可考虑明确班级对这类行为的要求。",
        })

    # —— 失分点 → 策略方向 ——
    for w in (weak_dims or [])[:3]:
        strategy = STRATEGY_HINTS.get(w.get("dim", ""), w.get("advice", ""))
        hints.append({
            "title": f"上轮失分：{w.get('name')}（{w.get('score')} 分）",
            "text": strategy,
        })

    # —— 风险规避 ——
    for r in (risks or [])[:2]:
        hints.append({
            "title": f"需规避：{r.get('label', '')}",
            "text": r.get("advice", ""),
        })

    if not hints:
        hints.append({
            "title": "策略提醒",
            "text": "一次有效的干预通常同时覆盖：明确制止、受害者保护、责任要求与后续跟进。",
        })
    # 安全阀：任何提示都不得包含可照抄话术（引号句）
    for h in hints:
        h["text"] = h["text"].replace("「", "“").replace("」", "”")
    return hints


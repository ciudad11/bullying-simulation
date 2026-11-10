# -*- coding: utf-8 -*-
"""
教师实训评估报告构建器（R19）

在 R14 的「逐轮分析 + 改进意见」基础上，扩展为结构化的教师实训评估报告：

1. executive_summary   —— 总体评估结论（等级、趋势、距达标差距、最高性价比动作）
2. capability_profile  —— 七维能力画像（均分/最好/最差/达标率/趋势/等级）
3. failure_attribution —— 失分归因（四类：要素缺失/表达与对象/风险操作/节奏与时机）
4. pitfalls            —— 典型表述误区对照（真实原话 → 判定 → 原因 → 改法）
5. training_roadmap    —— 分阶段训练路线（三阶段目标、练习、检查标准）
6. scripts             —— 可直接套用的完整干预话术（按场景）
7. self_check          —— 发送前自检清单（逐条对应维度）
8. rubric_explainer    —— 评分标准速查（分档、N/O、风险码、封顶规则）

设计约束：
- 纯函数，无副作用，不改动任何评分口径；
- 所有结论均来自专家评分明细（七维分数/理由/证据、风险 flags 明细）与原始对话；
- 报告阶段允许给出完整示范话术（模拟过程中的「最小限度帮助」约束不适用于事后复盘）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .coaching import DIMS, DIM_GUIDE, RISK_GUIDE, DEFAULT_THRESHOLD, _band, _truncate

# 维度 → 失分归因分类
DIM_CATEGORY = {
    "D1": "要素缺失",
    "D2": "表达与对象",
    "D3": "要素缺失",
    "D4": "风险操作",
    "D5": "要素缺失",
    "D6": "要素缺失",
    "D7": "节奏与时机",
}

CATEGORY_ORDER = ["要素缺失", "表达与对象", "风险操作", "节奏与时机"]

CATEGORY_DESC = {
    "要素缺失": "该说的要素没有说出口——缺少行为定性、停止要求、责任说明、群体规范或跟进安排。",
    "表达与对象": "话说到了但落点不准——未确认安全、未给出具体安排，或把决定权交给了受害者。",
    "风险操作": "表达方式触发了风险条款（淡化伤害、促成对质、暴露受害者、仅口头承诺等）。",
    "节奏与时机": "介入时机或跨轮次调整存在问题——该介入未介入、话术跨轮重复未回应新反应。",
}

LEVEL_BANDS = [
    (80, "清晰、具体、适切"),
    (60, "基本达标"),
    (40, "初步不足"),
    (0, "明显不当"),
]


def _level_of(avg: Optional[float]) -> str:
    if avg is None:
        return "无可用数据"
    for lo, name in LEVEL_BANDS:
        if avg >= lo:
            return name
    return "明显不当"


def _trend(values: List[float]) -> Dict[str, Any]:
    """由分数序列判断趋势（首尾 + 前后半段对比）。"""
    if not values:
        return {"direction": "无数据", "delta": 0.0, "first": None, "last": None}
    first, last = values[0], values[-1]
    delta = round(last - first, 1)
    half = len(values) // 2
    if half >= 1:
        early = sum(values[:half]) / half
        late = sum(values[half:]) / max(1, len(values) - half)
    else:
        early = late = first
    diff = round(late - early, 1)
    if diff >= 3:
        direction = "上升"
    elif diff <= -3:
        direction = "下降"
    else:
        direction = "持平"
    return {"direction": direction, "delta": delta, "half_diff": diff,
            "first": first, "last": last}


# ============================================================
# 1. 总体评估结论
# ============================================================
def build_executive_summary(analyses: List[Dict], threshold: int = DEFAULT_THRESHOLD) -> Dict:
    analyses = [a for a in (analyses or []) if isinstance(a, dict)]
    scored = [a for a in analyses if a.get("score") is not None]
    summary: Dict[str, Any] = {
        "rounds_total": len(analyses),
        "rounds_intervened": len(scored),
        "rounds_passed": sum(1 for a in scored if a["score"] >= threshold),
        "avg_score": None,
        "best_score": None,
        "worst_score": None,
        "level": "无可用数据",
        "level_desc": "",
        "trend": _trend([]),
        "gap_to_pass": None,
        "key_problem": "",
        "top_actions": [],
        "score_formula": "总分 = 七维等权平均（N/O 维度不计入分母，也不按 0 分计）",
        "rules_note": "",
        "headline": "",
    }
    if not scored:
        summary["headline"] = "本次模拟没有产生可用的专家评分，无法给出量化结论。"
        summary["level_desc"] = "建议检查模型/API 配置后重新演练。"
        return summary

    scores = [a["score"] for a in scored]
    avg = round(sum(scores) / len(scores), 1)
    passed = summary["rounds_passed"]
    trend = _trend(scores)
    summary["avg_score"] = avg
    summary["best_score"] = max(scores)
    summary["worst_score"] = min(scores)
    summary["trend"] = trend
    summary["gap_to_pass"] = round(max(0.0, threshold - avg), 1)

    if avg >= 80:
        summary["level"] = "优秀"
        summary["level_desc"] = "干预表达清晰、具体、适切，四个阶段要素基本齐备，可作为校内示范。"
    elif avg >= threshold:
        summary["level"] = "基本达标"
        summary["level_desc"] = "整体达到基本达标水平，个别维度仍可再具体化。"
    elif avg >= 40:
        summary["level"] = "需提升"
        summary["level_desc"] = "已有干预意识与部分要素，但要素覆盖不全或表达偏笼统，需按清单补齐。"
    else:
        summary["level"] = "需大幅提升"
        summary["level_desc"] = "多数干预停留在安抚或口头劝导层面，尚未形成「定性—制止—责任—保护—跟进」的完整链条。"

    # 规则是否造成扣分（澄清「规则太严」的误解）
    capped_rounds = [a for a in scored if a.get("risk_cap_applied")]
    serious_rounds = [a for a in scored if a.get("risk_level") == "serious"]
    if capped_rounds:
        rules_note = (f"有 {len(capped_rounds)} 轮因严重风险被封顶至 60 分；"
                      f"其余轮次的失分全部来自七维分本身。")
    elif serious_rounds:
        rules_note = (f"有 {len(serious_rounds)} 轮被判定存在严重风险，但原始均分本就低于 60 分，"
                      f"封顶规则实际未额外扣分——失分全部来自七维分本身。")
    else:
        rules_note = "本次未触发任何实际扣分或封顶条款，失分全部来自七维分本身。"
    summary["rules_note"] = rules_note

    # 关键问题与最高性价比动作
    gap_agg: Dict[str, Dict] = {}
    for a in scored:
        for w in a.get("weak_dimensions", []):
            slot = gap_agg.setdefault(w["dim"], {"dim": w["dim"], "name": w["name"],
                                                 "contribution": 0.0, "advice": w.get("advice", ""),
                                                 "times": 0})
            slot["contribution"] += w.get("contribution", 0.0)
            slot["times"] += 1
    ranked = sorted(gap_agg.values(), key=lambda x: -x["contribution"])
    if ranked:
        top = ranked[0]
        summary["key_problem"] = (
            f"最集中的失分是「{top['name']}」（{top['times']} 轮低于 {threshold} 分，"
            f"累计拉低总分约 {round(top['contribution'], 1)} 分）。")
        summary["top_actions"] = [
            {"dim": g["dim"], "name": g["name"], "action": g["advice"],
             "expected_gain": round(g["contribution"], 1)}
            for g in ranked[:3]
        ]
    else:
        summary["key_problem"] = "各维度均达到基本达标水平。"
        summary["top_actions"] = [{"dim": "", "name": "保持", "action": "保持当前表达结构并持续补充具体跟进安排。",
                                   "expected_gain": 0.0}]

    summary["headline"] = (
        f"共 {len(analyses)} 轮、{len(scored)} 轮干预，平均 {avg} 分，"
        f"{passed} 轮达到 {threshold} 分；整体水平「{summary['level']}」，趋势{trend['direction']}。")
    return summary


# ============================================================
# 2. 七维能力画像
# ============================================================
def build_capability_profile(analyses: List[Dict], threshold: int = DEFAULT_THRESHOLD) -> Dict:
    analyses = [a for a in (analyses or []) if isinstance(a, dict)]
    scored = [a for a in analyses if a.get("score") is not None]
    rows: List[Dict[str, Any]] = []
    for d in DIMS:
        guide = DIM_GUIDE.get(d, {})
        vals: List[float] = []
        for a in scored:
            v = (a.get("dimension_scores") or {}).get(d)
            if v is not None:
                vals.append(float(v))
        rounds_scored = len(vals)
        if rounds_scored:
            avg = round(sum(vals) / rounds_scored, 1)
            pass_rate = round(sum(1 for v in vals if v >= threshold) / rounds_scored, 2)
            tr = _trend(vals)
            rows.append({
                "dim": d,
                "name": guide.get("name", d),
                "avg": avg,
                "best": max(vals),
                "worst": min(vals),
                "band": _band(avg),
                "level": _level_of(avg),
                "pass_rate": pass_rate,
                "rounds_scored": rounds_scored,
                "trend": tr["direction"],
                "trend_delta": tr["delta"],
                "requirement": guide.get("why", ""),
                "gap": guide.get("gap", ""),
                "advice": guide.get("fix", ""),
                "avoid": guide.get("avoid", ""),
                "sample": guide.get("sample", ""),
            })
        else:
            rows.append({
                "dim": d, "name": guide.get("name", d), "avg": None, "best": None, "worst": None,
                "band": None, "level": "无可观察证据", "pass_rate": 0.0, "rounds_scored": 0,
                "trend": "无数据", "trend_delta": 0.0,
                "requirement": guide.get("why", ""), "gap": guide.get("gap", ""),
                "advice": guide.get("fix", ""), "avoid": guide.get("avoid", ""),
                "sample": guide.get("sample", ""),
            })

    with_data = [r for r in rows if r["avg"] is not None]
    strongest = max(with_data, key=lambda r: r["avg"]) if with_data else None
    weakest = min(with_data, key=lambda r: r["avg"]) if with_data else None
    no_obs = [r["dim"] for r in rows if r["rounds_scored"] == 0]

    if strongest and weakest:
        summary = (f"最强项是「{strongest['name']}」（均分 {strongest['avg']}，"
                   f"{strongest['level']}）；最弱项是「{weakest['name']}」（均分 {weakest['avg']}，"
                   f"{weakest['level']}）。")
    else:
        summary = "本次没有可用的维度分，无法给出能力画像。"

    return {
        "dimensions": rows,
        "strongest": strongest,
        "weakest": weakest,
        "no_observable_dims": no_obs,
        "summary": summary,
        "threshold": threshold,
    }


# ============================================================
# 3. 失分归因（四类）
# ============================================================
def build_failure_attribution(analyses: List[Dict], threshold: int = DEFAULT_THRESHOLD) -> Dict:
    analyses = [a for a in (analyses or []) if isinstance(a, dict)]
    scored = [a for a in analyses if a.get("score") is not None]
    buckets: Dict[str, List[Dict]] = {c: [] for c in CATEGORY_ORDER}

    for a in scored:
        rn = a.get("round")
        for w in a.get("weak_dimensions", []):
            cat = DIM_CATEGORY.get(w["dim"], "要素缺失")
            buckets[cat].append({
                "round": rn,
                "dim": w["dim"],
                "name": w["name"],
                "detail": w.get("reason") or w.get("gap", ""),
                "gap": w.get("gap", ""),
                "evidence": w.get("reason", ""),
                "fix": w.get("advice", ""),
                "avoid": w.get("avoid", ""),
                "impact": w.get("contribution", 0.0),
            })
        for r in a.get("risk_flags", []):
            buckets["风险操作"].append({
                "round": rn,
                "dim": None,
                "name": r.get("label", r.get("code", "")),
                "detail": (r.get("evidence") or [""])[0] if r.get("evidence") else "",
                "gap": r.get("advice", ""),
                "evidence": "；".join(r.get("evidence", [])[:2]),
                "fix": r.get("fix") or r.get("advice", ""),
                "avoid": r.get("avoid", ""),
                "impact": 0.0,
                "risk_code": r.get("code", ""),
            })
    # 未干预轮次 → 节奏与时机
    for a in analyses:
        if not a.get("has_intervention"):
            buckets["节奏与时机"].append({
                "round": a.get("round"),
                "dim": None,
                "name": "该轮未介入",
                "detail": a.get("event_summary", ""),
                "gap": "欺凌行为发生时未及时介入，轮次机会被浪费。",
                "evidence": "",
                "fix": "在出现针对个人的嘲笑、起哄、孤立动作时立即介入，先制止再处理感受。",
                "avoid": "避免等到冲突升级或受害者已崩溃才介入。",
                "impact": 0.0,
            })

    categories = []
    total_impact = 0.0
    for cat in CATEGORY_ORDER:
        items = buckets.get(cat, [])
        if not items:
            continue
        imp = round(sum(i.get("impact", 0.0) for i in items), 2)
        total_impact += imp
        rounds = sorted({i.get("round") for i in items if i.get("round") is not None})
        categories.append({
            "category": cat,
            "desc": CATEGORY_DESC[cat],
            "count": len(items),
            "rounds": rounds,
            "impact": imp,
            "share": 0.0,
            "items": items,
        })
    for c in categories:
        c["share"] = round(c["impact"] / total_impact * 100, 1) if total_impact > 0 else 0.0

    if categories:
        top_cat = max(categories, key=lambda c: c["impact"] if c["impact"] > 0 else c["count"])
        summary = (f"失分主要来自「{top_cat['category']}」（{top_cat['count']} 项，"
                   f"占总缺口 {top_cat['share']}%）：{top_cat['desc']}")
    else:
        summary = "本次未发现明显失分项，各维度均达到要求。"

    return {
        "categories": categories,
        "summary": summary,
        "total_impact": round(total_impact, 2),
        "principle": "归因只依据专家评分明细与原始对话，不做关键词猜测；「风险操作」类条目表示表达触发了风险条款，本身不等于额外扣分（封顶仅在原始均分高于 60 时生效）。",
    }


# ============================================================
# 4. 典型表述误区对照（真实原话 → 判定 → 原因 → 改法）
# ============================================================
def build_pitfalls(analyses: List[Dict], threshold: int = DEFAULT_THRESHOLD) -> Dict:
    analyses = [a for a in (analyses or []) if isinstance(a, dict)]
    rows: List[Dict[str, Any]] = []
    seen = set()

    for a in analyses:
        if not a.get("has_intervention"):
            continue
        excerpt = _truncate(a.get("what_you_did", ""), 70)
        for r in a.get("risk_flags", []):
            key = (a.get("round"), r.get("code"))
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "round": a.get("round"),
                "excerpt": excerpt,
                "trigger": f"触发风险：{r.get('label', r.get('code', ''))}",
                "verdict": "该表述被判定为风险操作",
                "why": r.get("advice", ""),
                "evidence": "；".join(r.get("evidence", [])[:2]),
                "fix": r.get("fix") or RISK_GUIDE.get(r.get("code", ""), {}).get("fix", "") or r.get("advice", ""),
                "avoid": r.get("avoid", ""),
                "kind": "risk",
            })
        for w in a.get("weak_dimensions", [])[:2]:
            key = (a.get("round"), w.get("dim"))
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "round": a.get("round"),
                "excerpt": excerpt,
                "trigger": f"失分维度：{w.get('name')}（{w.get('score')} 分）",
                "verdict": f"该表述未达到「{w.get('band')}」要求",
                "why": w.get("reason") or w.get("gap", ""),
                "evidence": "",
                "fix": w.get("advice", ""),
                "avoid": w.get("avoid", ""),
                "kind": "dimension",
            })

    rows.sort(key=lambda r: (r.get("round") if r.get("round") is not None else 0))
    summary = ("以下条目全部取自你本次实际使用的表述，按「原话 → 判定 → 原因 → 改法」逐条对照。"
               if rows else "本次未发现需要对照的典型表述。")
    return {"items": rows, "summary": summary, "count": len(rows)}


# ============================================================
# 5. 分阶段训练路线
# ============================================================
def build_training_roadmap(analyses: List[Dict], threshold: int = DEFAULT_THRESHOLD) -> Dict:
    profile = build_capability_profile(analyses, threshold)
    avg_by_dim = {r["dim"]: r["avg"] for r in profile["dimensions"] if r["avg"] is not None}

    def _stage(idx: int, title: str, dims: List[str], goal: str, drills: List[str],
               checkpoint: str) -> Dict:
        weak = [d for d in dims if avg_by_dim.get(d) is None or avg_by_dim.get(d, 0) < threshold]
        script = " ".join(DIM_GUIDE.get(d, {}).get("sample", "") for d in dims)
        parts = []
        for d in dims:
            if avg_by_dim.get(d) is not None:
                parts.append(f"{DIM_GUIDE.get(d, {}).get('name', d)} {avg_by_dim[d]}")
        avg_text = "、".join(parts) if parts else "无数据"
        return {
            "stage": idx,
            "title": title,
            "target_dims": dims,
            "target_names": [DIM_GUIDE.get(d, {}).get("name", d) for d in dims],
            "goal": goal,
            "why_now": (f"当前这些维度平均分为 {avg_text}，"
                        f"其中 {len(weak)} 项低于 {threshold} 分，是当前优先补强对象。"),
            "drills": drills,
            "checkpoint": checkpoint,
            "script": script,
            "script_note": "可先照此结构练习，再替换成现场情境的具体内容。",
            "target_score": threshold,
        }

    stages = [
        _stage(1, "第一阶段：定性 + 制止（先让行为停下来）", ["D1", "D3"],
               "能在 10 秒内说清「这是什么行为 + 立刻停止 + 谁要改变」，避免只做口头劝导。",
               ["对着镜或在空教室练习：一句话定性 + 一句话停止要求（2 句，20 秒内说完）。",
                "复述本班 3 个真实场景，分别写出「行为定性句 + 停止要求句」。",
                "练习时故意省略责任句与跟进句，感受自己解释时的语速变化。"],
               f"连续 3 次模拟中 D1 与 D3 均达到 {threshold} 分以上，且不再出现「只说了别闹了」式表达。"),
        _stage(2, "第二阶段：保护 + 风险控制（不让事情变糟）", ["D2", "D4"],
               "在处理行为之前先确认受害者是否安全，并能识别哪些安排会提高报复风险。",
               ["练习「先问安全、再给安排」的开场句，并准备 3 种具体保护措施（调位、陪同、单独谈话）。",
                "判断题训练：给出 6 种常见安排（当面对质、道歉、集体讨论、报告德育处等），练习判断风险。",
                "演练「不安排碰面」的替代说法，避免因追求和解而暴露受害者。"],
               f"D2 与 D4 均达到 {threshold} 分以上，且不再触发「淡化伤害 / 强迫对质 / 暴露受害者」类风险。"),
        _stage(3, "第三阶段：跟进 + 群体规范（让改变持续）", ["D6", "D5", "D7"],
               "把跟进说成可核查的安排，并让旁观者与班级规范进入干预内容。",
               ["每次干预固定补一句跟进：谁、何时、做什么、何时复查（写在纸上再开口）。",
                "练习 3 句群体规范句（班级规矩、报告路径、旁观者责任），插入到现有话术中。",
                "跨轮次练习：回应学生上一轮的新反应，而不是重复上一轮说法。"],
               f"D6 稳定达到 {threshold} 分以上，D5 有明确表述，D7 出现「依据新反应调整」的表述。"),
    ]
    return {
        "stages": stages,
        "summary": "三阶段按「先止损 → 再保护 → 后巩固」推进；每阶段都有可当场练习的动作与可检验的达标标准。",
        "method": "每阶段达标后再进入下一阶段；未达标时不要追求表达长度，先保证要素齐全。",
    }


# ============================================================
# 6. 可直接套用的完整干预话术（按场景）
# ============================================================
def build_scripts(analyses: List[Dict], threshold: int = DEFAULT_THRESHOLD) -> Dict:
    scripts = [
        {"scene": "通用四要素（任何场景的底稿）",
         "script": "「××，你刚才的做法属于××欺凌，我明确不接受这种行为。请立刻停止××。"
                   "这样会让××感到被排斥和伤害，你需要在××之前做出改变。"
                   "我今天放学前分别找你们谈一次，××我再过来确认情况。」",
         "covers": ["D1", "D3", "D6"],
         "when_to_use": "时间紧、拿不准时先用这套，任何场景都不会低于基本要求。"},
        {"scene": "言语嘲笑 / 取笑（现场制止）",
         "script": DIM_GUIDE["D1"]["sample"] + " " + DIM_GUIDE["D3"]["sample"],
         "covers": ["D1", "D3"],
         "when_to_use": "出现「你们看××真蠢」「××就是废物」一类起哄，当场点名具体行为并停止。"},
        {"scene": "关系孤立 / 排挤",
         "script": "「××，我看到你们没有叫××一起玩，还说了那句话。排除同伴同样是欺凌的一部分，"
                   "我明确不接受。今天你们要做的第一件事是邀请××一起加入；"
                   "我今天会分别找你们谈，周三我再确认××的情况。」",
         "covers": ["D1", "D3", "D6"],
         "when_to_use": "孤立没有明显肢体冲突时，必须点明「排除同伴」本身也属于欺凌行为。"},
        {"scene": "群体起哄与旁观者",
         "script": DIM_GUIDE["D5"]["sample"] + " 起哄的同学也要承担相应责任：下次再听到这种嘲笑，"
                                            "请直接告诉老师，而不是跟着笑。",
         "covers": ["D5", "D1"],
         "when_to_use": "全班围观、笑声起哄时，必须处理旁观者，否则下次照旧。"},
        {"scene": "存在报复或安全风险（先分开处理）",
         "script": DIM_GUIDE["D4"]["sample"] + " " + DIM_GUIDE["D2"]["sample"],
         "covers": ["D4", "D2"],
         "when_to_use": "权力明显不对等、受害者已受伤或疑似会被报复时——不安排碰面、先做保护。"},
        {"scene": "事后跟进与复查",
         "script": DIM_GUIDE["D6"]["sample"] + " 我也会找你确认：你今天有没有再被嘲笑？"
                                            "如果有，下次可以直接来办公室找我。",
         "covers": ["D6", "D2", "D7"],
         "when_to_use": "每轮干预结束后补一句，形成闭环；同时兼顾受害者的后续感受。"},
    ]
    return {
        "scripts": scripts,
        "summary": "以下话术为结构模板，请把「××」替换为现场的真实对象与行为；照结构说即可达标，不必逐字背诵。",
        "note": "报告阶段提供完整示范；模拟进行中系统只给策略方向，不提供可直接照抄的句子。",
    }


# ============================================================
# 7. 发送前自检清单
# ============================================================
def build_self_check(analyses: List[Dict], threshold: int = DEFAULT_THRESHOLD) -> Dict:
    items = [
        {"dim": "D1", "text": "我点明了这是哪一类欺凌，并明确说「我不接受这种行为」"},
        {"dim": "D1", "text": "我指名（或明确指向）了具体对象，并给出可执行的停止要求"},
        {"dim": "D2", "text": "我先确认了受害者现在是否安全"},
        {"dim": "D2", "text": "我给出了具体保护安排（调座位 / 陪同离场 / 单独谈话），而不是只说安慰话"},
        {"dim": "D3", "text": "我说明了该行为对受害者的影响"},
        {"dim": "D3", "text": "我提出了可核查的行为改变要求，而不只是要求道歉"},
        {"dim": "D4", "text": "我没有淡化动机或伤害（「不是故意的」「闹着玩」「没关系」）"},
        {"dim": "D4", "text": "我没有安排双方当面对质、互相道歉或当众复述经过"},
        {"dim": "D4", "text": "我没有公开受害者的身份与细节"},
        {"dim": "D5", "text": "我提到了旁观者与班级规范 / 报告路径"},
        {"dim": "D6", "text": "我说清了跟进安排：谁、何时、做什么、什么时候复查"},
        {"dim": "D7", "text": "我回应了学生这一轮的新反应，而不是重复上一轮的说法"},
    ]
    for it in items:
        it["name"] = DIM_GUIDE.get(it["dim"], {}).get("name", it["dim"])
    return {
        "items": [{"id": i + 1, **it} for i, it in enumerate(items)],
        "summary": "发送前逐条对照：每勾选一项，对应维度的得分就有保障；前 4 项是最容易拿到的分。",
        "hint": "建议每次干预只盯 3 项：先保证「定性 + 停止要求 + 跟进句」三件套稳定出现。",
    }


# ============================================================
# 8. 评分标准速查
# ============================================================
def build_rubric_explainer(threshold: int = DEFAULT_THRESHOLD) -> Dict:
    bands = [
        {"range": "80–100", "label": "清晰、具体、适切",
         "desc": "行为定性明确、停止要求可执行、责任与改变具体、保护与跟进安排齐全且与风险匹配。"},
        {"range": "60–79", "label": "基本达标",
         "desc": "覆盖主要要素，个别环节（如跟进时间点、群体规范）仍显笼统。"},
        {"range": "40–59", "label": "初步不足",
         "desc": "有干预意识但要素不全：只安慰未制止、只制止未保护、无任何跟进安排等。"},
        {"range": "0–39", "label": "明显不当",
         "desc": "未构成有效干预：与情境无关、敷衍应付、淡化伤害、把责任交给受害者等。"},
    ]
    dimensions = []
    for d in DIMS:
        g = DIM_GUIDE.get(d, {})
        dimensions.append({
            "dim": d,
            "name": g.get("name", d),
            "criteria": g.get("why", ""),
            "gap": g.get("gap", ""),
            "fix": g.get("fix", ""),
        })
    risk_codes = []
    for code, g in RISK_GUIDE.items():
        risk_codes.append({
            "code": code,
            "label": g.get("label", code),
            "trigger": g.get("advice", ""),
            "avoid": g.get("avoid", ""),
            "penalty": "风险本身不直接扣分，但会拉低相关维度；≥2 项风险（或公开羞辱/强迫对质这类高风险）判为严重风险，总分封顶 60。",
        })
    return {
        "bands": bands,
        "dimensions": dimensions,
        "no_rule": "D5/D6/D7 在确无可观察证据时判 N/O（无可观察），不计入平均分母——既不会拉低总分，也不会被当成 0 分。",
        "risk_codes": risk_codes,
        "cap_rule": "严重风险时总分封顶 60；只有原始均分高于 60 时封顶才真正生效。",
        "principles": [
            "评分依据实际证据，不按关键词数量或句子长度给分。",
            "处罚、道歉、讲规则、同理心、和解都不自带高分；分数取决于这些做法在本情境中是否适当、具体、可执行。",
            "维度之间等权平均，因此单点改进很难单独拉过线，需要成组补齐。",
        ],
        "threshold": threshold,
    }


# ============================================================
# 汇总
# ============================================================
def build_training_report(round_analyses: List[Dict], threshold: int = DEFAULT_THRESHOLD) -> Dict:
    analyses = [a for a in (round_analyses or []) if isinstance(a, dict)]
    return {
        "executive_summary": build_executive_summary(analyses, threshold),
        "capability_profile": build_capability_profile(analyses, threshold),
        "failure_attribution": build_failure_attribution(analyses, threshold),
        "pitfalls": build_pitfalls(analyses, threshold),
        "training_roadmap": build_training_roadmap(analyses, threshold),
        "scripts": build_scripts(analyses, threshold),
        "self_check": build_self_check(analyses, threshold),
        "rubric_explainer": build_rubric_explainer(threshold),
    }

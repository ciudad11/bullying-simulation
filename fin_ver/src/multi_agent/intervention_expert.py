# -*- coding: utf-8 -*-
"""
Intervention Expert Agent - 教师干预专家评分（Phase 1: 统一评分合同）

依据《教师干预能力评分编码手册》v1.0（2026-10-21 定稿）对整段互动进行
D1–D7 七维百分制评分。本模块是教师干预质量的【单一评分入口】。

设计合同（规则引擎重构方案 v1.0）：
- 评分对象 = 截至当前轮的【完整互动】，不是教师的最后一句。
- 评分口径 = 0–100（百分制），N/O 维度剔除分母。
- 专家【只评分、不改状态】；不输出 bullying_intensity / victim_distress 的数值增减。
- 严重风险（serious）时最终总分封顶 60。
- 后端重算总分，以程序值为准；JSON 无效重试一次，再失败标「待评估」。
- 严禁回退关键词打分。

角色称呼保持中性化：量规描述用「欺凌者 / 受害者 / 正义旁观者 / 从众旁观者」。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Tuple

# ============================================================
# 常量：七维定义与量规（中性化）
# ============================================================

DIMENSION_DEFS: Dict[str, Dict[str, str]] = {
    "D1": {
        "name": "欺凌识别与制止",
        "focus": "是否识别问题，并明确要求停止",
    },
    "D2": {
        "name": "受害者保护与心理支持",
        "focus": "是否兼顾安全、情绪与实际需要",
    },
    "D3": {
        "name": "欺凌者责任认识与行为改变",
        "focus": "是否使实施者承担责任并改变行为",
    },
    "D4": {
        "name": "干预策略的情境适配性与风险控制",
        "focus": "策略是否匹配严重程度、权力关系与风险",
    },
    "D5": {
        "name": "同伴与群体行为规范",
        "focus": "是否适当影响旁观者与群体氛围",
    },
    "D6": {
        "name": "长期欺凌干预与跟进",
        "focus": "是否安排调查、报告、保护、复查和必要的多方协作",
    },
    "D7": {
        "name": "多轮回应与调整",
        "focus": "是否根据新信息调整干预",
    },
}

# 四档分界（供专家理解 band，不用于机械扣分）
BAND_RANGES: Dict[str, Tuple[int, int]] = {
    "clear_specific_appropriate": (80, 100),   # 清晰、具体、适切
    "basic_adequate": (60, 79),                 # 基本达标
    "preliminary_insufficient": (40, 59),       # 初步不足
    "clearly_inappropriate": (0, 39),           # 明显不当
}

# 风险类型（R1–R5）
RISK_DEFS: Dict[str, str] = {
    "R_VICTIM_BLAME": "责备或羞辱受害者：把责任归给受害者，贬低、嘲讽或羞辱其人格/反应",
    "R_MINIMIZE_HARM": "淡化或否认欺凌伤害：在实际语境中把持续伤害说成玩笑、小事，否认后果",
    "R_FORCED_CONFRONTATION": "强迫受害者对质或和解：在未评估安全、权力关系和受害者意愿时强迫对质、握手或和好",
    "R_EXPOSURE_RETALIATION": "暴露受害者、增加报复风险：不必要地公开身份或细节，要求当众复述经历，造成暴露或报复风险",
    "R_PROMISE_NO_ACTION": "有持续危险却仅口头承诺：教师已知危险仍在继续，却只说「我会处理」而没有相应行动安排",
}


# ============================================================
# 专家系统提示词
# ============================================================

_EXPERT_SYSTEM_PROMPT = """你是一名专门评价准教师校园欺凌干预话语的专家评分员。你必须严格依据 D1–D7 百分制评分量规评价整段互动。

【评分对象】
当前已观察到的完整互动，而不只是教师的最后一句。请阅读场景信息、全部轮次对话、学生回应、已有风险和后续行动记录。

【评分标准】
D1 欺凌识别与制止：是否识别问题，并明确要求停止。
D2 受害者保护与心理支持：是否兼顾安全、情绪与实际需要。
D3 欺凌者责任认识与行为改变：是否使实施者承担责任并改变行为。
D4 干预策略的情境适配性与风险控制：策略是否匹配严重程度、权力关系与风险。
D5 同伴与群体行为规范：是否适当影响旁观者与群体氛围。
D6 长期欺凌干预与跟进：是否安排调查、报告、保护、复查和必要的多方协作。
D7 多轮回应与调整：是否根据新信息调整干预。

【分档】
每个可评分维度给出 0–100 的整数分数，使用以下分档：
0–39 明显不当；40–59 初步不足；60–79 基本达标；80–100 清晰、具体、适切。
分数必须依据实际证据，不要只统计关键词、策略名称或句子长度。分档内要考虑行为是否具体、是否匹配风险、是否可执行、是否顾及受害者安全，以及是否在多轮互动中落实。处罚、道歉、讲规则、同理心或和解都不自带高分；分数取决于这些做法在实际情境中是否适当、具体、有效。

【评分锚点与校准（严格执行，防止分数集中在中段）】
- 0–10 分：干预内容无意义或与当前欺凌情境无关（如纯寒暄、敷衍应答、空洞口号、只重复无信息字符）。此类干预不得因「有回应」「语气温和」获得任何同情分。
- 20–35 分：仅礼节性劝阻或明显回避制止（如只说「别闹了」「都少说两句」）。
- 40–59 分：有部分干预要素但缺少关键环节（如只制止未保护、只安慰未制止、无任何跟进）。
- 60–79 分：明确指出具体行为并要求停止，且兼顾了受害者保护或责任要求。
- 80–100 分：行为定性清晰、停止要求可执行、责任与改变要求具体、保护与跟进安排明确且与风险匹配。
- 必须使用 0–100 的全区间评分；严禁把所有维度都压在 30–50 的「安全区间」。证据不足时宁低勿高，不得为顾及情面抬分。
- 认真完整地覆盖行为定性、明确停止要求、责任与改变、跟进安排且情境适切的干预，总分应达到 75–85；不得因「还可以更完善」而将其压在 40 分档。

【N/O 规则】
N/O 只用于缺乏可观察机会的维度，不等于 0 分，也不等于低质量。
- D5 可 N/O：场景中没有旁观者，或互动没有进入任何群体规范环节。
- D6 可 N/O：当前观察片段尚未进入可判断跟进安排的环节。
- D7 可 N/O：仅有单轮互动，没有学生后续反馈可供判断。
若教师有明显处理机会却遗漏了必需制止或风险控制，不能用 N/O 回避，应按实际表现给低分。

【关键风险】
必须另行识别以下关键风险：责备/羞辱受害者、淡化伤害、强迫受害者对质或和解、暴露受害者并增加报复风险、明知持续危险仍只有口头承诺而无行动安排。必须理解语境、否定、引用和说话对象；教师引用某个有害说法并明确反对它时，不得误判为教师支持该说法。风险不能由其他维度高分抵消。
若整段互动达到「严重风险」等级，最终总分最高为 60 分：先计算有效维度平均分，再执行总分封顶，并同时保留封顶前平均分、封顶后总分和封顶原因。不得把所有维度分数机械改成 60 分。

【严禁】
仅凭词典命中打分；按命中的关键词数量累加分数；因为话语更长就自动加分；把任何调解、处罚、道歉或安慰都默认判为有效；推断对话中未出现的行动已经完成；直接指定模拟状态数值增减。

【输出要求】
每个维度都输出：score（0–100 整数或 null）、status（scored 或 N/O）、band、evidence（对话中的原文片段）、rationale（简洁理由）、confidence（0–1）。
风险也必须提供原文证据、语境说明和置信度。只给出可审查的判断依据，不输出冗长的内部推理过程。

最终只返回符合指定 JSON Schema 的 JSON（不要用 markdown 代码块包裹，直接输出 JSON 对象）。若信息不足，要说明不确定性；绝不可为了填满结构而编造证据或分数。

【必须输出的 JSON Schema（严格遵守字段名与类型）】
{
  "dimensions": {
    "D1": {"score": 0, "status": "scored", "band": "基本达标", "evidence": ["原文片段"], "rationale": "理由", "confidence": 0.9},
    "D2": {"score": 0, "status": "scored", "band": "基本达标", "evidence": ["原文片段"], "rationale": "理由", "confidence": 0.9},
    "D3": {"score": 0, "status": "scored", "band": "基本达标", "evidence": ["原文片段"], "rationale": "理由", "confidence": 0.9},
    "D4": {"score": 0, "status": "scored", "band": "基本达标", "evidence": ["原文片段"], "rationale": "理由", "confidence": 0.9},
    "D5": {"score": null, "status": "N/O", "band": "N/O", "evidence": [], "rationale": "无旁观者", "confidence": 0.9},
    "D6": {"score": 0, "status": "scored", "band": "基本达标", "evidence": ["原文片段"], "rationale": "理由", "confidence": 0.9},
    "D7": {"score": null, "status": "N/O", "band": "N/O", "evidence": [], "rationale": "单轮", "confidence": 0.9}
  },
  "risk_assessment": {
    "flags": [],
    "evidence": "原文证据",
    "context": "语境说明",
    "confidence": 0.9
  },
  "context_profile": {
    "bullying_forms": [],
    "severity": "",
    "repetition": "",
    "power_imbalance": "",
    "victim_safety": "",
    "follow_up_status": ""
  },
  "strategy_profile": {
    "clear_boundary": false,
    "victim_protection": false,
    "accountability_and_change": false,
    "safe_bystander_norms": false,
    "follow_up_plan": false,
    "adaptive_response": false
  },
  "summary": "一句话总结"
}

注意：
- score 必须是 0–100 整数，N/O 时 score 为 null。
- risk_assessment.flags 是一个数组，元素只能取以下代码：R_VICTIM_BLAME、R_MINIMIZE_HARM、R_FORCED_CONFRONTATION、R_EXPOSURE_RETALIATION、R_PROMISE_NO_ACTION。无风险则为空数组 []。
- 不要输出 overall 字段，不要输出 average_before_cap 字段。
- band 用中文：明显不当 / 初步不足 / 基本达标 / 清晰适切。"""


# 输出 JSON Schema（维度字段名与顺序固定）
DIMENSION_KEYS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7"]

VALID_BANDS = set(BAND_RANGES.keys())
VALID_STATUS = {"scored", "N/O"}
VALID_RISK_LEVELS = {"none", "attention", "serious"}
VALID_RISK_CODES = set(RISK_DEFS.keys())

# 量规明确指出的高风险行为（方案 §4.2）：出现其一即构成「严重风险」，
# 即使只有 1 项风险标记也适用总分 60 封顶。
HIGH_RISK_CODES = {"R_EXPOSURE_RETALIATION", "R_FORCED_CONFRONTATION"}


# ============================================================
# 评估结果数据结构
# ============================================================

@dataclass
class DimensionAssessment:
    score: Optional[int]          # 0-100 或 None（N/O）
    status: str                   # "scored" | "N/O"
    band: Optional[str]
    evidence: List[str]
    rationale: str
    confidence: float

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "status": self.status,
            "band": self.band,
            "evidence": self.evidence,
            "rationale": self.rationale,
            "confidence": self.confidence,
        }


@dataclass
class InterventionAssessment:
    assessment_id: str
    status: str = "pending"                 # "scored" | "pending"（待评估）
    raw_mean_score_100: float = 0.0
    final_quality_score_100: float = 0.0
    risk_cap_applied: bool = False
    risk_level: str = "none"
    rated_dimension_count: int = 0
    no_observable_count: int = 0
    dimensions: Dict[str, DimensionAssessment] = field(default_factory=dict)
    risk_assessment: Dict = field(default_factory=dict)
    context_profile: Dict = field(default_factory=dict)
    strategy_profile: Dict = field(default_factory=dict)
    summary: str = ""
    # 元信息
    retry_count: int = 0
    parse_error: str = ""
    # R15：无意义内容护栏标记（确定性判 0 分时为 True）
    meaningless: bool = False
    meaningless_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "assessment_id": self.assessment_id,
            "status": self.status,
            "scoring_scope": "interaction_so_far",
            "raw_mean_score_100": self.raw_mean_score_100,
            "final_quality_score_100": self.final_quality_score_100,
            "risk_cap_applied": self.risk_cap_applied,
            "risk_level": self.risk_level,
            "rated_dimension_count": self.rated_dimension_count,
            "no_observable_count": self.no_observable_count,
            "meaningless": self.meaningless,
            "meaningless_reason": self.meaningless_reason,
            "dimensions": {k: self.dimensions[k].to_dict() for k in DIMENSION_KEYS if k in self.dimensions},
            "risk_assessment": self.risk_assessment,
            "context_profile": self.context_profile,
            "strategy_profile": self.strategy_profile,
            "summary": self.summary,
        }


# ============================================================
# 纯函数：解析 / 校验 / 重算（可独立测试）
# ============================================================

def extract_json_object(text: str) -> Optional[dict]:
    """从 LLM 输出中稳健提取 JSON 对象。"""
    if not text:
        return None
    s = text.strip()
    # 1) 直接解析
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # 2) 提取 ```json ... ``` 代码块
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL)
    if fence:
        try:
            obj = json.loads(fence.group(1))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    # 3) 提取第一个 { 到最后一个 } 的平衡子串
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        sub = s[start:end + 1]
        try:
            obj = json.loads(sub)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def _valid_int_score(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        iv = int(v)
        if 0 <= iv <= 100:
            return iv
    if isinstance(v, str):
        try:
            iv = int(v)
            if 0 <= iv <= 100:
                return iv
        except Exception:
            return None
    return None


def _valid_confidence(v: Any) -> float:
    try:
        f = float(v)
        return max(0.0, min(1.0, f))
    except Exception:
        return 0.5


def normalize_dimension(dim: Any) -> Optional[DimensionAssessment]:
    """规范化单个维度，返回 None 表示该维度结构非法。"""
    if not isinstance(dim, dict):
        return None
    status = str(dim.get("status", "")).strip()
    if status not in VALID_STATUS:
        # 兼容：若给出 score 则视为 scored，否则视为非法
        if "score" in dim:
            status = "scored"
        else:
            return None

    if status == "N/O":
        score = None
        band = None
    else:
        score = _valid_int_score(dim.get("score"))
        if score is None:
            return None
        band = str(dim.get("band", "")).strip()
        if not band:
            band = None

    evidence = dim.get("evidence", [])
    if evidence is None:
        evidence = []
    if isinstance(evidence, str):
        evidence = [evidence]
    evidence = [str(e) for e in evidence if str(e).strip()]

    rationale = str(dim.get("rationale", "")).strip()
    confidence = _valid_confidence(dim.get("confidence"))

    return DimensionAssessment(
        score=score,
        status=status,
        band=band,
        evidence=evidence,
        rationale=rationale,
        confidence=confidence,
    )


def recompute_score(dimensions: Dict[str, DimensionAssessment]) -> Tuple[float, int, int]:
    """
    后端重算七维等权平均分（N/O 剔除分子分母）。
    返回 (mean_score, rated_count, no_observable_count)。
    若无可评分维度返回 (0.0, 0, n_no)。
    """
    scores: List[int] = []
    no_observable = 0
    for key in DIMENSION_KEYS:
        dim = dimensions.get(key)
        if dim is None:
            continue
        if dim.status == "N/O":
            no_observable += 1
        elif dim.score is not None:
            scores.append(dim.score)
    if not scores:
        return 0.0, 0, no_observable
    mean = sum(scores) / len(scores)
    return round(mean, 1), len(scores), no_observable


# ============================================================
# R15：无意义干预内容护栏（确定性判 0 分，不调用 LLM）
# ============================================================
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002190-\U000021FF\U00002600-\U000027BF"
    "\U00002B00-\U00002BFF\U0000FE00-\U0000FE0F\U0001F1E6-\U0001F1FF]+")

# 视为「非文字」的字符：空白 + 中英文标点 + 常见符号
_STRIP_CHARS = set(
    " \t\r\n\u3000"
    "，。！？、；：…—·～"
    ".,!?;:'\"()[]{}<>+-*/=_@#$%^&|\\`~"
    "“”‘’「」『』（）《》〈〉【】〔〕&;"
)


def detect_meaningless_intervention(content: str) -> Optional[str]:
    """
    检测明显无意义的干预内容（R15 确定性护栏，非关键词评分）。

    命中条件（保守设计，避免误伤有意义短句如「停」）：
    1. 去除空白/标点/表情后无任何有效文字（如「。。。」「？？？」「😀😀」）；
    2. 有效文字为同一字符的重复且长度 ≥ 2（如「哈哈哈」「嗯嗯嗯」「555」「呵呵」）。

    命中 → 直接判 0 分（明显不当档），不调用 LLM；
    未命中 → 返回 None，交由专家智能体按量规评分。
    """
    if not content:
        return None  # 空内容由接口层拦截
    s = _EMOJI_RE.sub("", str(content))
    eff = "".join(ch for ch in s if ch not in _STRIP_CHARS)
    if not eff:
        return "干预内容仅由标点/空白/表情构成，无任何实质干预信息"
    if len(eff) >= 2 and len(set(eff)) == 1:
        return f"干预内容为单一字符重复（{eff[0]}×{len(eff)}），无实质干预信息"
    return None


def determine_risk_level(risk_assessment: Dict) -> str:
    """根据风险 flags 判定风险等级：none / attention / serious。

    规则（方案 §4.2）：
    - 0 项 → none；1 项 → attention；≥2 项 → serious；
    - 出现量规明确指出的高风险行为（公开羞辱受害者 / 强迫对质）→ serious。
    """
    if not isinstance(risk_assessment, dict):
        return "none"
    # 兼容：专家直接给 level（可能中文）
    lvl = risk_assessment.get("level")
    if isinstance(lvl, str):
        if "严重" in lvl or lvl == "serious":
            return "serious"
        if "关注" in lvl or "attention" in lvl or "需要" in lvl:
            return "attention"
        if "无" in lvl or "none" in lvl or "无风险" in lvl:
            return "none"
    flags = risk_assessment.get("flags", [])
    if not isinstance(flags, list):
        return "none"
    codes = []
    for f in flags:
        if isinstance(f, str):
            codes.append(f.strip())
        elif isinstance(f, dict):
            codes.append(str(f.get("code", f.get("risk", ""))).strip())
    n = len([c for c in codes if c in VALID_RISK_CODES])
    if any(c in HIGH_RISK_CODES for c in codes):
        return "serious"
    if n >= 2:
        return "serious"
    if n == 1:
        return "attention"
    return "none"


def apply_risk_cap(raw_mean: float, risk_level: str) -> Tuple[float, bool]:
    """严重风险时最终总分封顶 60。返回 (final_score, cap_applied)。

    注意（修正 R12 分析发现的提示误导）：封顶「实际生效」才置 cap_applied=True。
    若原始均分本就低于 60，封顶不会改变任何数值，不能声称「被封顶」——
    否则界面会显示「总分封顶 60」而实际未被扣分，造成规则过严的误解。
    """
    if risk_level == "serious":
        if raw_mean > 60.0:
            return round(60.0, 1), True
        return round(raw_mean, 1), False
    return round(raw_mean, 1), False


def normalize_risk_flags(risk_assessment: Dict) -> Tuple[List[str], List[dict]]:
    """
    规范化风险 flags 并完整保留逐项明细（R3，方案 §4.2/§5.3）。

    返回 (合法代码列表, flags_detail)。flags_detail 每项含
    code / evidence / context / confidence，不得只存代码：
    - flags 元素支持字符串或 {code, evidence, context, confidence} 对象；
    - 整体 evidence/context/confidence 作为单条风险缺省值兜底；
    - 无证据的风险保留标记但 confidence=0 并标注「无证据」。
    """
    if not isinstance(risk_assessment, dict):
        return [], []
    raw_flags = risk_assessment.get("flags", [])
    if not isinstance(raw_flags, list):
        raw_flags = []
    whole_evidence = risk_assessment.get("evidence", [])
    if isinstance(whole_evidence, str):
        whole_evidence = [whole_evidence]
    if not isinstance(whole_evidence, list):
        whole_evidence = []
    whole_evidence = [str(e).strip() for e in whole_evidence if str(e).strip()]
    whole_context = str(risk_assessment.get("context", "") or "").strip()
    whole_confidence = _valid_confidence(risk_assessment.get("confidence", 0.5))

    flags: List[str] = []
    flags_detail: List[dict] = []
    for item in raw_flags:
        if isinstance(item, str):
            code, per = item.strip(), None
        elif isinstance(item, dict):
            code = str(item.get("code", item.get("risk", ""))).strip()
            per = item
        else:
            continue
        if code not in VALID_RISK_CODES or code in flags:
            continue
        flags.append(code)

        if per is not None:
            ev = per.get("evidence", [])
            if isinstance(ev, str):
                ev = [ev]
            if not isinstance(ev, list):
                ev = []
            ev = [str(e).strip() for e in ev if str(e).strip()]
            ctx = str(per.get("context", "") or "").strip()
            raw_conf = per.get("confidence")
            conf = _valid_confidence(raw_conf) if raw_conf is not None else whole_confidence
        else:
            ev, ctx, conf = [], "", 0.0

        if not ev:
            # 兜底：使用整体 evidence/context
            ev = list(whole_evidence)
            ctx = ctx or whole_context
            conf = whole_confidence if ev else 0.0
            if not ev:
                ctx = (ctx + "｜" if ctx else "") + "无证据"
        flags_detail.append({
            "code": code,
            "evidence": ev,
            "context": ctx,
            "confidence": conf,
        })
    return flags, flags_detail


# ============================================================
# 专家智能体
# ============================================================

# 角色类型 → 中性化标签（对话序列化用）
_ROLE_LABELS = {
    "bully": "欺凌者",
    "victim": "受害者",
    "bystander_a": "正义旁观者",
    "bystander_b": "从众旁观者",
    "teacher": "教师",
    "system": "系统",
}


class InterventionExpertAgent:
    """
    量规评分员：只评分，不改状态。
    通过 LLM 对整段互动进行 D1–D7 百分制评分，后端重算总分并校验。
    """

    def __init__(self, llm_client=None, temperature: float = 0.2, max_tokens: int = 2500):
        self.llm_client = llm_client
        self.temperature = temperature
        self.max_tokens = max_tokens

    # --------------------------------------------------------
    # 输入构建
    # --------------------------------------------------------
    def build_user_prompt(self, state, intervention_content: str) -> str:
        """构建发给专家的用户消息（场景 + 完整对话 + 本次干预）。"""
        lines: List[str] = []

        # 场景信息
        title = getattr(state, "scenario_title", "") or ""
        desc = getattr(state, "scenario_description", "") or ""
        lines.append("【场景信息】")
        if title:
            lines.append(f"场景：{title}")
        if desc:
            lines.append(f"描述：{desc}")

        # 完整对话历史
        lines.append("")
        lines.append("【完整对话（截至当前轮）】")
        msgs = getattr(state, "messages", []) or []
        for m in msgs:
            sender_id = getattr(m, "sender_id", "")
            sender_name = getattr(m, "sender_name", "") or sender_id
            role = getattr(m, "role", "")
            content = getattr(m, "content", "") or ""
            rnd = getattr(m, "round", 0)
            label = _ROLE_LABELS.get(role, role) or role
            lines.append(f"[第{rnd}轮] {label}({sender_name})：{content}")

        # 本次待评分干预
        lines.append("")
        lines.append("【本次待评分的教师干预】")
        lines.append(intervention_content or "")
        lines.append("")
        lines.append("请依据 D1–D7 量规对整段互动评分，并只返回符合 Schema 的 JSON。")

        return "\n".join(lines)

    # --------------------------------------------------------
    # 主评估流程
    # --------------------------------------------------------
    def assess(self, state, intervention_content: str, assessment_id: str) -> InterventionAssessment:
        """对整段互动评分。返回 InterventionAssessment（status=scored 或 pending）。"""
        # R15：无意义内容护栏 —— 确定性判 0 分（明显不当档），不调用 LLM
        reason = detect_meaningless_intervention(intervention_content)
        if reason:
            return self._meaningless_assessment(assessment_id, intervention_content, reason)

        result = InterventionAssessment(assessment_id=assessment_id)

        if self.llm_client is None:
            result.status = "pending"
            result.parse_error = "no_llm_client"
            result.summary = "未配置 LLM 客户端，无法进行专家评分。"
            return result

        user_prompt = self.build_user_prompt(state, intervention_content)

        for attempt in range(1, 3):  # 最多尝试 2 次（1 次正常 + 1 次重试）
            raw_text = self._call_llm(user_prompt)
            parsed = extract_json_object(raw_text)
            if parsed is None:
                result.parse_error = f"attempt_{attempt}: unparseable_json"
                result.retry_count = attempt
                continue

            assessment = self._parse_and_validate(parsed, assessment_id)
            if assessment is None:
                result.parse_error = f"attempt_{attempt}: invalid_schema"
                result.retry_count = attempt
                continue

            return assessment

        # 两次都失败 → 待评估（不回退关键词打分）
        result.status = "pending"
        result.summary = "专家评分无法返回可验证的结构化结果，本轮标记为待评估。"
        return result

    def _call_llm(self, user_prompt: str) -> str:
        try:
            resp = self.llm_client.chat(
                [
                    {"role": "system", "content": _EXPERT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            return resp or ""
        except Exception as e:
            print(f"[InterventionExpert] LLM call failed: {e}")
            return ""

    # --------------------------------------------------------
    # R15：无意义内容的确定性 0 分评估
    # --------------------------------------------------------
    def _meaningless_assessment(self, assessment_id: str, content: str,
                                reason: str) -> InterventionAssessment:
        """无意义干预内容 → 各可评分维度均判 0（量规 0-39 档的低端），不调用 LLM。"""
        dims: Dict[str, DimensionAssessment] = {}
        for key in DIMENSION_KEYS:
            dims[key] = DimensionAssessment(
                score=0,
                status="scored",
                band="明显不当",
                evidence=[str(content)[:40]],
                rationale=reason,
                confidence=1.0,
            )
        result = InterventionAssessment(
            assessment_id=assessment_id,
            status="scored",
            raw_mean_score_100=0.0,
            final_quality_score_100=0.0,
            risk_cap_applied=False,
            risk_level="none",
            rated_dimension_count=len(DIMENSION_KEYS),
            no_observable_count=0,
            dimensions=dims,
            risk_assessment={"level": "none", "flags": [], "flags_detail": []},
            context_profile={},
            strategy_profile={},
            summary=f"干预内容无实际干预功能，按量规判 0 分（{reason}）",
        )
        result.meaningless = True
        result.meaningless_reason = reason
        return result

    # --------------------------------------------------------
    # 解析 + 校验 + 重算
    # --------------------------------------------------------
    def _parse_and_validate(self, parsed: Dict, assessment_id: str) -> Optional[InterventionAssessment]:
        # 维度
        raw_dims = parsed.get("dimensions")
        if not isinstance(raw_dims, dict):
            return None
        dims: Dict[str, DimensionAssessment] = {}
        for key in DIMENSION_KEYS:
            d = normalize_dimension(raw_dims.get(key))
            if d is None:
                return None
            dims[key] = d

        # 后端重算总分（以程序值为准）
        raw_mean, rated_count, no_obs = recompute_score(dims)
        if rated_count == 0:
            return None  # 无可评分维度 → 视为无效

        # 风险
        risk_assessment = parsed.get("risk_assessment")
        if not isinstance(risk_assessment, dict):
            # 兼容：专家用了 risk 字段
            risk_assessment = parsed.get("risk")
            if not isinstance(risk_assessment, dict):
                risk_assessment = {}
        risk_level = determine_risk_level(risk_assessment)
        # 覆盖：若专家给了 level 且更严重，以更严重为准
        expert_level = risk_assessment.get("level") if isinstance(risk_assessment, dict) else None
        if expert_level in VALID_RISK_LEVELS:
            order = {"none": 0, "attention": 1, "serious": 2}
            if order.get(expert_level, 0) > order.get(risk_level, 0):
                risk_level = expert_level

        final_score, cap_applied = apply_risk_cap(raw_mean, risk_level)

        # R3：规范化风险 flags，并完整保留逐项明细（原文证据/语境/置信度）
        flags, flags_detail = normalize_risk_flags(risk_assessment)

        return InterventionAssessment(
            assessment_id=assessment_id,
            status="scored",
            raw_mean_score_100=raw_mean,
            final_quality_score_100=final_score,
            risk_cap_applied=cap_applied,
            risk_level=risk_level,
            rated_dimension_count=rated_count,
            no_observable_count=no_obs,
            dimensions=dims,
            risk_assessment={"level": risk_level, "flags": flags, "flags_detail": flags_detail},
            context_profile=parsed.get("context_profile") if isinstance(parsed.get("context_profile"), dict) else {},
            strategy_profile=parsed.get("strategy_profile") if isinstance(parsed.get("strategy_profile"), dict) else {},
            summary=str(parsed.get("summary", "")).strip(),
        )


# ============================================================
# 单例缓存（避免每次干预重建 LLMClient）
# ============================================================

_expert_cache: Dict[str, InterventionExpertAgent] = {}


def get_expert_agent(llm_client=None, temperature: float = 0.2, max_tokens: int = 2500) -> InterventionExpertAgent:
    """获取专家智能体（按 llm_client 身份缓存；llm_client=None 时返回无 LLM 实例）。"""
    if llm_client is None:
        return InterventionExpertAgent(llm_client=None, temperature=temperature, max_tokens=max_tokens)
    key = str(id(llm_client))
    if key not in _expert_cache:
        _expert_cache[key] = InterventionExpertAgent(llm_client=llm_client, temperature=temperature, max_tokens=max_tokens)
    return _expert_cache[key]

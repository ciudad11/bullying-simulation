# -*- coding: utf-8 -*-
"""
Evaluator Node - Metric Calculation and Assessment
Migrated from orchestrator.py EnhancedEvaluator

Step 3 更新(2026-05-28):
- OBVQ维度聚焦干预评估(识别干预针对哪种欺负类型)
- 旁观者视角检测(正向/负向旁观者关键词)
- 风险等级专项建议(无/低/中/高对应不同干预策略)
- 受害者保护充分性按风险等级调整(高风险时更严格)

Step 1 更新(2026-05-28):
- 新增 OBVQ 六维关键词库(身体/言语/关系/网络/权力/偏见)
- 新增语义分析模块:理解语义、判断严重程度、检测权力不平等与重复性
- 关键词评分 × 0.7 + 语义评分 × 0.3 综合敌意评分
"""

from typing import List, Dict, Optional, Tuple
from .state import SharedState, Message, InterventionRecord, OBVQState


# ============================================================
# OBVQ 六维关键词库(Step 1 新增)
# ============================================================

# OBVQ Bully (欺负行为) 维度 → 权重越高,危害越大
OBVQ_BULLY_KEYWORDS = {
    # 身体欺负 B1-B2:推撞打抢,危害最直接
    "physical": {
        "weight": 0.35,
        "keywords": [
            "打", "揍", "扇", "踹", "推", "撞", "踢", "掐", "抓",
            "抢", "夺", "偷", "摔", "砸东西", "破坏", "弄坏",
            "揍死", "打死", "弄死", "往死里打", "打一顿",
        ],
        "severity_boost": 0.1,   # 含动作意图的词额外+0.1
    },
    # 言语欺负 B3-B5:威胁/绰号/嘲笑,危害中等
    "verbal": {
        "weight": 0.30,
        "keywords": [
            "威胁", "揍你", "打你", "杀你", "弄死你",
            "绰号", "外号", "叫", "笨蛋", "白痴", "智障",
            "脑子有问题", "有病", "脑子进水",
            "丑", "难看", "土", "土气", "土得掉渣", "穷酸", "破烂",
            "穷", "穷鬼", "穷逼", "没钱", "家穷", "穷酸样",
            "笨", "蠢", "差", "画得丑", "画得差", "画得一塌糊涂",
            "成绩差", "倒数", "垫底", "什么破", "可笑", "不自量力",
            "弱智", "废物", "垃圾", "变态", "恶心", "讨厌", "猪",
            "性骚扰", "骚", "贱", "婊子", "绿茶", "装",
            "长舌妇", "话多", "闭嘴", "狗屁", "你可真行", "了不起",
            # 补充：LLM生成的嘲讽/讽刺用语
            "家伙", "那家伙", "那小子", "那货", "那货色",
            "看那", "你们看", "大家看", "快看",
            "多香", "吃香", "吃得下", "有脸吃", "好意思",
            "真行", "真厉害", "真棒", "真优秀", "真了不起",
            "笑死", "笑死我了", "哈哈", "呵呵", "呵",
            "装什么", "装什么装", "装模作样", "装模作样", "假惺惺",
            "得意", "得意什么", "骄傲什么", "有什么了不起",
        ],
        "severity_boost": 0.05,
    },
    # 关系欺负 B6-B8:孤立/排挤/谣言
    "relational": {
        "weight": 0.30,
        "keywords": [
            "孤立", "不带你", "别跟着", "别来", "不合群",
            "滚", "滚开", "滚远", "滚远点", "滚开点",
            "别跟我们", "不跟你玩", "没人理你",
            "谣言", "说坏话", "讲你坏话", "散布", "传开",
            "排挤", "挤走", "挤兑", "挤掉", "小团体",
            "告状", "告诉老师", "告家长", "打小报告",
            "你们不要理他", "都不要理她", "谁也别理",
            "你朋友都是假的", "没人喜欢你", "你没有朋友",
            "让大家不跟你玩", "叫大家别理", "虚伪", "夸你", "班主任", "你可真行",
            # 补充：LLM生成的社交排斥用语
            "角落", "坐角落", "一个人", "独自", "旁边", "没位置",
            "没你的位置", "没地方", "坐那边", "坐远点", "离远点",
            "没人愿意", "没人想", "没人要", "谁要你", "谁理你",
            "别坐这", "别过来", "去那边", "闪开", "让开",
            "不带", "不叫你", "没叫你", "没喊你",
            "跟你没关系", "不关你事", "你算什么", "你以为你是谁",
            "没人和你", "没人跟你", "落单", "孤零零",
        ],
        "severity_boost": 0.05,
    },
    # 网络欺负 B9-B11:社交媒体/隐私/游戏骚扰(Step1新增)
    "cyber": {
        "weight": 0.35,
        "keywords": [
            "群里", "班级群", "QQ群", "微信群", "发群里",
            "发网上", "发到网上", "发到群里", "网上发",
            "照片", "丑照", "表情包", "截图", "聊天记录",
            "@", "at", "艾特", "@所有人",
            "社交", "朋友圈", "空间", "微博", "抖音",
            "发布", "公开", "扩散", "转发",
            "隐私", "秘密", "黑历史", "把柄",
            "游戏里", "游戏外挂", "开挂", "送人头",
            "网上", "网上说", "网上骂", "人肉", "扒",
        ],
        "severity_boost": 0.1,   # 网络欺负波及范围广,+0.1
    },
    # 权力欺负 B12-B13:强迫服从/利用社交地位(Step1新增)
    "power": {
        "weight": 0.30,
        "keywords": [
            "强迫", "威胁", "必须", "不得不",
            "抄作业", "帮写", "替做", "逼",
            "服从", "听我的", "就得", "就得这样",
            "交钱", "要钱", "抢钱", "勒索", "敲诈",
            "年级", "班干部", "班长", "学霸", "老大",
            "势力", "人脉", "认识谁谁谁", "我朋友多",
            "让你混不下去", "让你待不下去", "让你没朋友",
        ],
        "severity_boost": 0.05,
    },
    # 偏见欺负 B14-B15:宗教/文化/缺陷嘲笑(Step1新增)
    "prejudice": {
        "weight": 0.35,
        "keywords": [
            "宗教", "信仰", "拜佛", "基督教", "伊斯兰",
            "迷信", "落后", "土", "土包子", "乡巴佬",
            "口吃", "结巴", "大舌头", "结巴子",
            "胖子", "肥", "肥猪", "矮子", "矮冬瓜", "丑八怪",
            "残疾", "瘸子", "聋子", "瞎子",
            "缺陷", "胎记", "痘痘", "皮肤病", "狐臭",
            "单亲", "离婚", "没爸", "没妈", "孤儿",
            "外地人", "乡下人", "农民工", "穷地方",
            "嘲笑", "学", "模仿", "笑话", "取笑",
        ],
        "severity_boost": 0.05,
    },
}

# OBVQ 受害维度关键词(用于识别受害者是否在描述受害经历)
OBVQ_VICTIM_KEYWORDS = {
    "physical": {
        "keywords": [
            "打", "揍", "扇", "踹", "推", "撞", "踢", "掐",
            "抢", "夺", "偷", "破坏", "弄坏",
        ],
    },
    "verbal": {
        "keywords": [
            "骂", "骂我", "说我", "叫绰号", "起外号",
            "嘲笑", "讽刺", "挖苦", "讥讽",
            "侮辱", "羞辱", "丢人", "丢脸",
        ],
    },
    "relational": {
        "keywords": [
            "孤立", "不跟我玩", "不理我", "排挤",
            "不让我", "不带我", "说坏话", "谣言",
            "滚", "别来", "别跟着",
        ],
    },
    "cyber": {
        "keywords": [
            "群里", "发到", "截图", "丑照",
            "发网上", "朋友圈", "空间",
        ],
    },
    "power": {
        "keywords": [
            "强迫", "威胁", "抄作业", "要钱", "交钱",
        ],
    },
    "prejudice": {
        "keywords": [
            "嘲笑", "模仿", "缺陷", "胎记",
        ],
    },
}


# ============================================================
# 语义分析模块(Step 1 新增)
# ============================================================

# 重复/升级信号词 → 提示欺负是持续的、非一次性的(OBVQ"重复性"特征)
REPETITION_SIGNALS = [
    "又", "总是", "每次", "从来", "一直", "天天", "每天",
    "经常", "动不动", "老", "老爱", "老是", "改不了",
    "屡次", "屡教不改", "说了多少遍", "多少次了",
]

# 群体协同欺负信号
GROUP_BULLYING_SIGNALS = [
    "我们", "大家", "所有人", "都", "一起",
    "你们", "谁也别理", "都不理", "我们都",
    "叫上", "带上", "联合", "一起对付",
]

# 权力不平等信号(欺负者强调自己的优势地位)
POWER_IMBALANCE_SIGNALS = [
    "你能怎样", "你能拿我怎样", "你能怎么办",
    "你敢", "你能告", "你去告", "告状精",
    "老师也不管", "老师管不了", "拿我没办法",
    "我有", "我认识", "我爸", "我妈",
    "年纪大", "力气大", "打群架", "人比你多",
]

# 严重伤害意图信号
SEVERITY_SIGNALS = [
    "打死", "揍死", "弄死", "往死里", "搞死你",
    "废掉", "弄残", "毁掉", "让你死", "你死定了",
    "滚出去", "滚出班级", "滚出学校", "别来上学",
    "杀了你", "杀死你", "捅死你",
]

# 隐性欺负信号(表面玩笑,实则欺负)
COVERT_BULLYING_SIGNALS = [
    "开玩笑", "逗你玩", "逗着玩", "闹着玩",
    "你当真了", "你也太", "玻璃心", "开不起",
    "不就是", "至于吗", "这点事", "别太当回事",
]

# 积极旁观者支持词(降低欺负强度)
BystanderPositiveSupportiveSignALS = [
    "加油", "支持", "相信你", "很棒", "有才华",
    "不是你的错", "别往心里去", "勇敢", "倾诉",
    "温暖", "对不起", "抱歉", "错了", "不应该",
    "老师会保护你", "你不是一个人", "别怕",
    "我不喜欢这样", "你不该这样", "停止", "别这样",
    "够了", "太过分了", "他不是故意的",
]

# 否定欺负的语境词(需要结合上下文判断是不是真的否定)
NEGATION_BULLYING_SIGNALS = [
    "不是", "没有", "不是要", "不是想", "不是在",
]


class SemanticAnalyzer:
    """
    语义分析器:理解句子语义,判断欺负行为的真实意图和严重程度
    不依赖关键词匹配,而是分析句子的语境、修辞、意图
    """

    def __init__(self, text: str):
        self.text = text
        self.text_clean = text.replace(" ", "").replace(",", ",").replace("。", ".")
        self.length = len(text)

    # ---------- 核心语义判断 ----------

    def is_bullying_intent(self) -> Tuple[bool, float]:
        """
        判断文本是否表达霸凌意图(综合判断)
        返回:(是否霸凌, 置信度 0-1)
        """
        score = 0.0
        reasons = []

        # 1. 检查是否在否定欺负(上下文否定,如"我不是要欺负你")
        is_negated = self._is_negated_bullying()
        if is_negated[0]:
            return (False, is_negated[1])  # 明确否定 → 非霸凌

        # 2. 检查严重伤害意图(最高置信度)
        if self._has_severity_intent():
            score += 0.5
            reasons.append("severity_intent")

        # 3. 检查权力不平等信号(欺负者强调自己优势)
        if self._has_power_imbalance():
            score += 0.25
            reasons.append("power_imbalance")

        # 4. 检查群体协同欺负
        if self._has_group_bullying():
            score += 0.2
            reasons.append("group_bullying")

        # 5. 检查重复性/升级意图(OBVQ核心特征)
        if self._has_repetition_intent():
            score += 0.2
            reasons.append("repetition_intent")

        # 6. 检查隐性欺负("开玩笑"包装的恶意)
        if self._has_covert_bullying():
            score += 0.25
            reasons.append("covert_bullying")

        # 7. 检查是否在描述欺负事实(受害者视角)
        if self._is_describing_victimization():
            score += 0.3
            reasons.append("victim_describing")

        # 8. 检查修辞语气(感叹号/问号/省略号)
        tone_score = self._analyze_tone()
        score += tone_score

        # 综合置信度
        confidence = min(1.0, score)
        is_bullying = score >= 0.3  # 阈值 0.3

        return (is_bullying, round(confidence, 2))

    def _is_negated_bullying(self) -> Tuple[bool, float]:
        """
        检测是否在否定/澄清欺负行为(需要排除"不是在欺负你"的语境)
        例如:"我不是要欺负你"、"我没想伤害你"
        """
        negation_phrases = [
            "不是要", "不是想", "不是故意的", "不是欺负",
            "没想", "没要", "没有欺负", "不是骂",
            "别误会", "不是那个意思", "不是针对你",
            "我不是要", "我也不想", "我没那个意思",
        ]
        text_lower = self.text_clean
        for phrase in negation_phrases:
            if phrase in text_lower:
                # 进一步检查:是否在说"你不是"(在否定受害者的感受)
                if any(w in text_lower for w in ["你太", "你别", "你不该"]):
                    # 这种是欺负者否定受害者的正当感受,不能算否定霸凌
                    return (False, 0.3)
                return (True, 0.85)  # 明确否定

        return (False, 0.0)

    def _has_severity_intent(self) -> bool:
        """检测严重伤害意图"""
        return any(sig in self.text_clean for sig in SEVERITY_SIGNALS)

    def _has_power_imbalance(self) -> bool:
        """检测权力不平等(欺负者强调自己优势/受害者劣势)"""
        # 检查欺负者是否在强调"你不能怎样"
        weak_signals = [
            "你能怎样", "你能拿我怎样", "你能怎么办",
            "你能告", "你去告", "告状", "打小报告",
            "告老师", "告诉老师", "告诉家长",
        ]
        # 检查欺负者是否在强调自己的优势
        advantage_signals = [
            "我认识", "我爸", "我妈", "我家",
            "年纪大", "力气大", "打群架", "人多",
            "你人少", "没朋友", "没人帮你",
        ]
        weak_count = sum(1 for s in weak_signals if s in self.text_clean)
        advantage_count = sum(1 for s in advantage_signals if s in self.text_clean)
        return weak_count + advantage_count >= 1

    def _has_group_bullying(self) -> bool:
        """检测群体协同欺负"""
        group_word_count = sum(1 for w in GROUP_BULLYING_SIGNALS if w in self.text_clean)
        return group_word_count >= 2

    def _has_repetition_intent(self) -> bool:
        """检测重复性/持续性意图(OBVQ核心特征)"""
        rep_count = sum(1 for w in REPETITION_SIGNALS if w in self.text_clean)
        return rep_count >= 1

    def _has_covert_bullying(self) -> bool:
        """
        检测隐性欺负(以'开玩笑'包装的真实欺负意图)
        例如:"逗你玩啊,别当真"(说完还在继续)
        """
        has_covert = any(w in self.text_clean for w in COVERT_BULLYING_SIGNALS)
        if has_covert:
            # 进一步检查:如果是纯澄清("我只是开玩笑")vs 持续欺负("开玩笑的嘛,别玻璃心")
            if any(w in self.text_clean for w in ["别当真", "别太在意", "玻璃心", "开不起"]):
                return True  # 以"玩笑"为由否定受害者感受 → 隐性欺负
            if any(w in self.text_clean for w in ["继续", "还", "再来"]):
                return True  # 还在继续欺负
        return False

    def _is_describing_victimization(self) -> bool:
        """检测是否在描述自己遭受的欺负(受害者视角)"""
        victim_verbs = [
            "打我", "骂我", "欺负我", "嘲笑我", "孤立我",
            "抢我", "偷我", "逼我", "威胁我",
        ]
        return any(v in self.text_clean for v in victim_verbs)

    def _analyze_tone(self) -> float:
        """
        分析语气强度
        返回加成分值
        """
        score = 0.0
        # 感叹号越多越强烈
        exclamation_count = self.text.count("!") + self.text.count("!")
        question_count = self.text.count("?") + self.text.count("?")
        ellipsis_count = self.text.count("......") + self.text.count("...")

        score += min(0.1, exclamation_count * 0.05)
        score += min(0.05, question_count * 0.03)
        score += min(0.05, ellipsis_count * 0.03)

        # 全大写(QQ/微信常见)→ 情绪激动
        # 中文拼音/谐音(弱智/废物等)→ 恶意增强
        return score

    # ---------- 欺负维度识别 ----------

    def identify_bullying_dimensions(self) -> List[str]:
        """
        基于语义识别当前消息涉及的欺负维度
        """
        dimensions = []
        text = self.text_clean

        # 身体欺负:动作词 + 直接接触
        physical_signals = ["打", "揍", "推", "撞", "踢", "踹", "扇", "掐", "抢", "夺", "破坏东西", "弄坏"]
        if any(s in text for s in physical_signals):
            dimensions.append("physical")

        # 言语欺负:口头攻击
        verbal_signals = [
            "绰号", "外号", "笨", "蠢", "丑", "穷", "白痴", "智障", "废物", "垃圾",
            "变态", "恶心", "该死", "去死", "脑子", "有病", "狗屁", "你可真行", "了不起",
        ]
        if any(s in text for s in verbal_signals):
            dimensions.append("verbal")

        # 关系欺负:孤立/排挤/散布谣言
        relational_signals = [
            "滚", "孤立", "不理", "不带", "别来", "别跟着",
            "不跟你玩", "没朋友", "你朋友假的", "谣言", "说坏话",
            "告状", "告诉老师", "排挤", "挤走", "虚伪", "装", "假", "夸你", "班主任",
        ]
        if any(s in text for s in relational_signals):
            dimensions.append("relational")

        # 网络欺负:网络平台相关
        cyber_signals = [
            "群里", "QQ", "微信", "发到", "发群里", "朋友圈",
            "截图", "丑照", "at", "@", "艾特",
            "发网上", "空间", "社交", "公开",
        ]
        if any(s in text for s in cyber_signals):
            dimensions.append("cyber")

        # 权力欺负:强迫/利用优势地位
        power_signals = [
            "强迫", "逼", "不得不", "必须", "就得",
            "抄作业", "替做", "要钱", "交钱", "服从",
            "老大", "人脉", "势力", "我说了算",
        ]
        if any(s in text for s in power_signals):
            dimensions.append("power")

        # 偏见欺负:宗教/文化/外貌/缺陷
        prejudice_signals = [
            "宗教", "迷信", "口吃", "结巴", "胖子", "肥",
            "残疾", "缺陷", "胎记", "单亲", "离婚",
            "外地人", "乡下人", "嘲笑", "学他",
        ]
        if any(s in text for s in prejudice_signals):
            dimensions.append("prejudice")

        return dimensions

    def assess_severity(self) -> float:
        """
        评估欺负行为的语义严重程度(0.0-1.0)
        结合意图、强度、权力不平等综合判断

        注意:不在此函数内部再次调用 is_bullying_intent(),
        而是由外部 calculate_hostile_score 根据两者综合判断。
        这里只计算 "欺负强度" 相关的语义指标,不涉及是否构成欺负的判断。
        """
        severity = 0.0

        # 涉及的维度数量(越多越严重)
        dimensions = self.identify_bullying_dimensions()
        dim_bonus = min(0.25, len(dimensions) * 0.08)
        severity += dim_bonus

        # 权力不平等加成(最强欺负特征)
        if self._has_power_imbalance():
            severity += 0.2

        # 群体欺负加成
        if self._has_group_bullying():
            severity += 0.15

        # 重复性/持续性加成(OBVQ核心特征:必须是 "重复的" 才算欺负)
        if self._has_repetition_intent():
            severity += 0.1

        # 严重伤害意图(最强欺负行为)
        if self._has_severity_intent():
            severity += 0.2

        # 隐性欺负(以"玩笑"否定受害者感受)
        if self._has_covert_bullying():
            severity += 0.1

        # 语气强度加成
        severity += self._analyze_tone()

        return min(1.0, severity)

    # ---------- 语义摘要(用于调试/日志)----------

    def get_semantic_summary(self) -> dict:
        """返回语义分析的完整摘要"""
        is_bully, confidence = self.is_bullying_intent()
        dimensions = self.identify_bullying_dimensions()
        severity = self.assess_severity()

        return {
            "is_bullying_intent": is_bully,
            "confidence": confidence,
            "dimensions": dimensions,
            "severity": round(severity, 3),
            "has_power_imbalance": self._has_power_imbalance(),
            "has_group_bullying": self._has_group_bullying(),
            "has_repetition_intent": self._has_repetition_intent(),
            "has_covert_bullying": self._has_covert_bullying(),
        }


# ============================================================
# 增强版敌意评分(Step 1:关键词 + 语义融合)
# ============================================================

def calculate_hostile_score(content: str) -> Tuple[float, dict]:
    """
    计算敌意评分(融合关键词 + 语义分析)
    返回:(综合敌意评分, 分析详情字典)
    """
    if not content or not content.strip():
        return 0.0, {}

    # ----- Part 1: OBVQ 六维关键词评分 -----
    keyword_score = _keyword_based_score(content)

    # ----- Part 2: 语义分析评分 -----
    analyzer = SemanticAnalyzer(content)
    semantic_summary = analyzer.get_semantic_summary()
    semantic_score = semantic_summary["severity"]

    # ----- Part 3: 升级词加成 -----
    escalation_bonus = 0.0
    for word in REPETITION_SIGNALS:
        if word in content:
            escalation_bonus += 0.05
    escalation_bonus = min(0.15, escalation_bonus)  # 上限 0.15

    # ----- 综合评分:关键词权重 0.7,语义权重 0.3 -----
    # 语义权重给到 0.3,补充关键词无法覆盖的情况
    combined_score = keyword_score * 0.7 + semantic_score * 0.3 + escalation_bonus
    combined_score = min(1.0, combined_score)

    return round(combined_score, 3), {
        "keyword_score": round(keyword_score, 3),
        "semantic_score": round(semantic_score, 3),
        "escalation_bonus": round(escalation_bonus, 3),
        "combined_score": round(combined_score, 3),
        "semantic_details": semantic_summary,
    }


def _keyword_based_score(content: str) -> float:
    """
    基于 OBVQ 六维关键词的评分
    遍历所有维度,累加匹配到的权重
    """
    score = 0.0
    matched_dimensions = []

    for dim_name, dim_config in OBVQ_BULLY_KEYWORDS.items():
        weight = dim_config["weight"]
        keywords = dim_config["keywords"]
        severity_boost = dim_config.get("severity_boost", 0.0)

        matched_count = 0
        for kw in keywords:
            if kw in content:
                matched_count += 1

        if matched_count > 0:
            # 该维度得分 = min(匹配词数, 3) × 权重 + 严重程度加成
            dim_score = min(matched_count, 3) * weight / 3 + severity_boost
            score += dim_score
            matched_dimensions.append(dim_name)

    return min(1.0, score)


def calculate_bullying_dimension_score(content: str) -> Dict[str, float]:
    """
    计算当前消息在 OBVQ 各维度的独立得分(用于雷达图展示)
    返回:{"physical": 0.2, "verbal": 0.35, ...}
    """
    dimension_scores = {}
    for dim_name, dim_config in OBVQ_BULLY_KEYWORDS.items():
        weight = dim_config["weight"]
        keywords = dim_config["keywords"]
        matched = sum(1 for kw in keywords if kw in content)
        dim_score = min(1.0, (min(matched, 3) / 3) * weight)
        dimension_scores[dim_name] = round(dim_score, 3)
    return dimension_scores


def detect_victim_dimension(content: str) -> Dict[str, float]:
    """
    检测受害者描述中涉及的 OBVQ 维度(用于旁观者报告)
    """
    dimension_scores = {}
    for dim_name, dim_config in OBVQ_VICTIM_KEYWORDS.items():
        keywords = dim_config["keywords"]
        matched = sum(1 for kw in keywords if kw in content)
        dimension_scores[dim_name] = round(min(1.0, matched * 0.25), 3)
    return dimension_scores


# ============================================================
# 原有情感检测(保留)
# ============================================================

SUPPORTIVE_WORDS = [
    "加油", "没关系", "别难过", "别怕", "支持", "相信你", "很棒", "有才华",
    "不是你的错", "别往心里去", "勇敢", "倾诉", "温暖", "对不起", "抱歉",
    "错了", "不应该", "老师会保护你", "你不是一个人",
]


def detect_emotions(content: str) -> List[str]:
    """Detect emotions in message content"""
    emotions = []
    if not content:
        return emotions

    # Sad / wronged
    if any(w in content for w in ["哭", "难过", "伤心", "委屈", "泛红", "低下头", "小声", "算了", "没有", "眼泪", "止不住", "发抖", "沉默", "蜷缩", "不敢看"]):
        emotions.append("sad")

    # Angry
    if any(w in content for w in ["生气", "愤怒", "讨厌", "过分", "可恶", "太过分", "你太过分"]):
        emotions.append("angry")

    # Fearful
    if any(w in content for w in ["害怕", "担心", "不敢", "怕", "不敢看", "蜷缩"]):
        emotions.append("fearful")

    # Happy
    if any(w in content for w in ["开心", "高兴", "谢谢", "好开心", "温暖", "太好了"]):
        emotions.append("happy")

    # Remorseful
    if any(w in content for w in ["对不起", "抱歉", "不好意思", "错了", "不应该"]):
        emotions.append("remorseful")

    # Self-doubt / self-negation
    if any(w in content for w in ["是我不好", "我活该", "都是我的错", "我太笨", "我不配", "对不起大家", "我有问题", "我太差了", "我不行", "为什么是我", "我不该", "活该", "废物", "没用"]):
        emotions.append("self_doubt")

    return emotions


# ============================================================
# 原有支持度评分(保留)
# ============================================================

def calculate_supportive_score(content: str) -> float:
    """Calculate supportive score"""
    if not content:
        return 0.0

    matches = sum(1 for w in SUPPORTIVE_WORDS if w in content)
    return min(1.0, matches * 0.25)


# ============================================================
# 重复攻击检测 (2026-08-06 新增)
# ============================================================

def _char_bigram_similarity(text1: str, text2: str) -> float:
    """中文字符二元组 Jaccard 相似度
    例: "你又考砸了" bigrams = {你又, 又考, 考砸, 砸了}
    """
    if not text1 or not text2:
        return 0.0
    b1 = {text1[i:i+2] for i in range(len(text1) - 1)}
    b2 = {text2[i:i+2] for i in range(len(text2) - 1)}
    if not b1 or not b2:
        return 0.0
    return len(b1 & b2) / len(b1 | b2)


def _detect_bully_repeat(content: str, messages: list, max_lookback: int = 8) -> float:
    """检查当前 bully 消息是否重复历史攻击模式
    返回与最近 bully 消息的最高相似度 (0-1)
    只比较最近的 max_lookback 条 bully 历史消息, 排除完全相同的消息
    """
    past_bully_texts = []
    for m in reversed(messages):
        if len(past_bully_texts) >= max_lookback:
            break
        sender = getattr(m, 'sender_id', None)
        if sender is None and hasattr(m, 'get'):
            sender = m.get('sender_id', '')
        text = getattr(m, 'content', None)
        if text is None and hasattr(m, 'get'):
            text = m.get('content', '')
        if sender == 'bully' and text and text != content:
            past_bully_texts.append(str(text))

    if not past_bully_texts:
        return 0.0

    return max(_char_bigram_similarity(content, t) for t in past_bully_texts)


# ============================================================
# Evaluator Node Logic (called by graph)
# ============================================================

def _evaluator_logic(state: SharedState) -> dict:
    """评估 pending messages 并更新指标（Phase 2：提案制，状态由 StateTransitionEngine 唯一写入）

    核心修复:添加阻尼机制,防止压力异常飙升
    - 压力增长阻尼:当前压力越高,增长越慢(避免快速冲顶)
    - 情绪反馈阻尼:高压力时情绪词影响减弱
    - 压力变化平滑化:限制单轮最大变化幅度
    - R7(方案§8.3):旧「干预韧性缓冲」已由干预残余效应机制替代

    Phase 2 改动：
    不再在多个分支分别直接写 state.bullying_intensity / victim_distress /
    bystander_intervention，而是产出结构化提案（StateTransitionProposal），
    最后统一交给 StateTransitionEngine 一次性写入。
    - 删除「重复攻击相似度>阈值反降强度」的错误规则。
    - OBVQ 累计量表（obvq_state）不属于三个动态状态维度，保留直接更新。
    """
    from .state_transition_engine import StateTransitionEngine

    print(f"\n  --- Evaluator: evaluating {len(state.pending_messages)} messages ---")

    config = state.config

    bully_threshold = config.get("bullying_intensity", {}).get("hostile_threshold", 0.3)
    increase_rate = config.get("bullying_intensity", {}).get("increase_rate", 0.15)

    sad_emotion = config.get("victim_distress", {}).get("sad_emotion", 0.12)
    fearful_emotion = config.get("victim_distress", {}).get("fearful_emotion", 0.18)
    supportive_comfort = config.get("victim_distress", {}).get("supportive_comfort", 0.15)
    self_doubt_emotion = config.get("victim_distress", {}).get("self_doubt", 0.22)

    # --- 霸凌→压力耦合参数 ---
    bully_attack_distress_rate = config.get("victim_distress", {}).get("bully_attack_distress_rate", 0.04)
    emotional_feedback_scale = config.get("victim_distress", {}).get("emotional_feedback_scale", 0.3)
    distress_bullying_coupling = config.get("victim_distress", {}).get("distress_bullying_coupling", 0.3)
    distress_decay_rate = config.get("victim_distress", {}).get("distress_decay_rate", 0.10)
    distress_decay_threshold = config.get("victim_distress", {}).get("distress_decay_threshold", 0.3)

    bystander_a_supportive_threshold = config.get("bystander_a", {}).get("supportive_threshold", 0.2)
    bystander_a_distress_reduction = config.get("bystander_a", {}).get("victim_distress_reduction", 0.35)

    natural_recovery_threshold = config.get("natural_recovery", {}).get("bully_stopped_threshold", 0.1)
    natural_recovery_rate = config.get("natural_recovery", {}).get("recovery_per_round", 0.08)

    prev_bullying_intensity = state.bullying_intensity
    prev_attack_frequency = state.attack_frequency
    prev_distress = state.victim_distress
    print(f"    [TRACE] _evaluator_logic START: B={state.bullying_intensity:.4f} V={state.victim_distress:.4f}")

    # ===== 阻尼机制核心参数 =====
    MAX_DISTRESS_INCREASE_PER_ROUND = 0.12
    # R7（方案 §8.3）：旧 INTERVENTION_RESILIENCE 阻尼已由「干预残余效应」机制替代——
    # 每完整轮无新增支持证据时按保留比例衰减；新攻击立即清零制止残余；
    # 保留比例可由 config residual_decay 段覆盖（R10）。
    _rd_cfg = config.get("residual_decay", {}) if isinstance(config, dict) else {}
    if not isinstance(_rd_cfg, dict):
        _rd_cfg = {}
    R_KEEP_IMMEDIATE = float(_rd_cfg.get("immediate_stop", 0.55))
    R_KEEP_SUPPORT = float(_rd_cfg.get("support", 0.80))
    R_KEEP_NORM = float(_rd_cfg.get("norm", 0.90))

    prev_immediate = state.immediate_stop_residual
    prev_support = state.support_residual
    prev_norm = state.norm_residual
    state.immediate_stop_residual = round(prev_immediate * R_KEEP_IMMEDIATE, 4)
    state.support_residual = round(prev_support * R_KEEP_SUPPORT, 4)
    state.norm_residual = round(prev_norm * R_KEEP_NORM, 4)
    # R24：报复风险在无新增攻击时回落（此前只增不减，导致攻击已归零但收敛条件仍不满足）
    R_KEEP_RETALIATION = float(_rd_cfg.get("retaliation", 0.95))
    if getattr(state, "attack_frequency", 0.0) <= 1e-6 and getattr(state, "retaliation_risk", 0.0) > 0:
        state.retaliation_risk = round(state.retaliation_risk * R_KEEP_RETALIATION, 4)
    if prev_immediate > 1e-6 or prev_support > 1e-6 or prev_norm > 1e-6:
        print(f"    [Residual] 残余衰减: 制止 {prev_immediate:.3f}->{state.immediate_stop_residual:.3f}, "
              f"支持 {prev_support:.3f}->{state.support_residual:.3f}, "
              f"规范 {prev_norm:.3f}->{state.norm_residual:.3f}")

    # ===== 提案累积（Phase 2：不直接写状态）=====
    proposals = []
    round_distress_increase = 0.0
    # 用于阻尼计算的「滚动值」（保持原语义：后续消息的阻尼基于前面累积后的压力/强度）
    working_distress = max(0.0, min(1.0, prev_distress))
    # Phase 3：working_bullying 追踪攻击频率滚动值（bullying_intensity 现为派生）
    working_bullying = max(0.0, min(1.0, prev_attack_frequency))

    for msg in state.pending_messages:
        hostile_score, hostile_details = calculate_hostile_score(msg.content)
        supportive_score = calculate_supportive_score(msg.content)
        emotions = detect_emotions(msg.content)

        # 记录扩展信息到消息对象(用于后续分析)
        msg.hostile_score = hostile_score
        msg.supportive_score = supportive_score
        msg.emotions = emotions
        msg.obvq_dimensions = calculate_bullying_dimension_score(msg.content)
        print(f"    [OBVQ DEBUG] obvq_dimensions={msg.obvq_dimensions}, hostile_score={hostile_score:.4f}")

        role = msg.sender_id

        # ===== Step 2: OBVQ 累计状态追踪（独立量表，保留直接更新）=====
        obvq_should_record = False
        obvq_intensity = hostile_score

        if role == "bully" and hostile_score >= bully_threshold:
            obvq_should_record = True
        elif role == "bully":
            if any(score >= 0.10 for score in msg.obvq_dimensions.values()):
                obvq_should_record = True
                obvq_intensity = 0.3
            else:
                analyzer = SemanticAnalyzer(msg.content)
                is_bully, confidence = analyzer.is_bullying_intent()
                if is_bully and confidence >= 0.3:
                    obvq_should_record = True
                    obvq_intensity = confidence

        if obvq_should_record:
            print(f"    [OBVQ DEBUG] add_event called! intensity={obvq_intensity:.4f}, dimensions={msg.obvq_dimensions}")
            state.obvq_state.add_event(msg.obvq_dimensions, state.round, intensity_factor=obvq_intensity)
            obvq_score_info = state.obvq_state.get_score_info()
            print(f"    [OBVQ] Intensity={obvq_intensity:.2f} Total={obvq_score_info['total_score']} "
                  f"Dims={state.obvq_state.last_event_dimensions}")

        if role == "bully" and hostile_score >= bully_threshold:
            # 霸凌攻击：强度上升提案（Phase 3：写 attack_frequency，同时写 attack_severity）
            intensity_delta = hostile_score * increase_rate
            proposals.append(StateTransitionEngine.make_proposal(
                target_state="attack_frequency",
                delta=intensity_delta,
                source="student_attack",
                rule_id="BULLY_ATTACK",
                event_id=f"round{state.round}_{msg.sender_id}",
                evidence=[f"hostile={hostile_score:.2f}"],
            ))
            # 攻击严重度：随敌意程度小幅上升（单次事件严重程度）
            severity_delta = hostile_score * 0.05
            proposals.append(StateTransitionEngine.make_proposal(
                target_state="attack_severity",
                delta=severity_delta,
                source="student_attack",
                rule_id="BULLY_ATTACK_SEVERITY",
                event_id=f"round{state.round}_{msg.sender_id}_sev",
                evidence=[f"hostile={hostile_score:.2f}"],
            ))
            working_bullying += intensity_delta

            # R7（方案 §8.3）：新攻击是「即时制止残余」的反证 → 立即清零
            if state.immediate_stop_residual > 0.0:
                print(f"    [Residual] 新攻击出现，制止残余 {state.immediate_stop_residual:.3f} -> 0")
                state.immediate_stop_residual = 0.0

            # Phase 5：重复攻击→持续性证据（升 persistence，不降强度）
            repeat_sim = _detect_bully_repeat(msg.content, state.messages)
            if repeat_sim > 0.7:
                proposals.append(StateTransitionEngine.make_proposal(
                    target_state="persistence",
                    delta=+0.02,
                    source="student_attack_repeat",
                    rule_id="BULLY_REPEAT_PERSISTENCE",
                    event_id=f"round{state.round}_{msg.sender_id}_repeat",
                    evidence=[f"repeat_similarity={repeat_sim:.2f}"],
                ))
                print(f"    [{msg.sender_name}] 重复攻击(sim={repeat_sim:.2f}) -> persistence 提案+0.02")

            detail_str = ""
            if hostile_details:
                kw_s = hostile_details.get("keyword_score", 0)
                sem_s = hostile_details.get("semantic_score", 0)
                esc_b = hostile_details.get("escalation_bonus", 0)
                dims = hostile_details.get("semantic_details", {}).get("dimensions", [])
                detail_str = f" 关键词:{kw_s:.2f} 语义:{sem_s:.2f} 升级:{esc_b:.2f} 维度:{dims}"

            # 攻击→压力提案（含阻尼）
            damping_factor = 1.0 - (working_distress * 0.6)
            damping_factor = max(0.25, damping_factor)

            base_distress_from_attack = hostile_score * bully_attack_distress_rate
            distress_from_attack = base_distress_from_attack * damping_factor

            if round_distress_increase + distress_from_attack > MAX_DISTRESS_INCREASE_PER_ROUND:
                distress_from_attack = max(0, MAX_DISTRESS_INCREASE_PER_ROUND - round_distress_increase)

            round_distress_increase += distress_from_attack
            if distress_from_attack > 0:
                proposals.append(StateTransitionEngine.make_proposal(
                    target_state="victim_distress",
                    delta=distress_from_attack,
                    source="attack_to_distress",
                    rule_id="BULLY_ATTACK_DISTRESS",
                    event_id=f"round{state.round}_{msg.sender_id}",
                    evidence=[f"hostile={hostile_score:.2f}"],
                ))
                working_distress += distress_from_attack
            print(f"    [{msg.sender_name}] hostile(阻尼{damping_factor:.0%}) -> B提案+{intensity_delta:.2f}, V提案+{distress_from_attack:.2f}{detail_str}")

        elif role == "victim":
            emotion_damping = 1.0 - (working_distress * 0.4)
            emotion_damping = max(0.4, emotion_damping)

            if emotions and "sad" in emotions:
                emotion_distress = sad_emotion * emotional_feedback_scale * emotion_damping
                if round_distress_increase + emotion_distress <= MAX_DISTRESS_INCREASE_PER_ROUND:
                    round_distress_increase += emotion_distress
                    proposals.append(StateTransitionEngine.make_proposal(
                        target_state="victim_distress", delta=emotion_distress,
                        source="victim_sad", rule_id="VICTIM_SAD",
                        event_id=f"round{state.round}_{msg.sender_id}_sad",
                    ))
                    working_distress += emotion_distress
                    print(f"    [{msg.sender_name}] sad(阻尼{emotion_damping:.0%}) -> V提案+{emotion_distress:.2f}")

            if emotions and "fearful" in emotions:
                emotion_distress = fearful_emotion * emotional_feedback_scale * emotion_damping
                if round_distress_increase + emotion_distress <= MAX_DISTRESS_INCREASE_PER_ROUND:
                    round_distress_increase += emotion_distress
                    proposals.append(StateTransitionEngine.make_proposal(
                        target_state="victim_distress", delta=emotion_distress,
                        source="victim_fearful", rule_id="VICTIM_FEARFUL",
                        event_id=f"round{state.round}_{msg.sender_id}_fearful",
                    ))
                    working_distress += emotion_distress

            if emotions and "self_doubt" in emotions:
                emotion_distress = self_doubt_emotion * emotional_feedback_scale * emotion_damping
                if round_distress_increase + emotion_distress <= MAX_DISTRESS_INCREASE_PER_ROUND:
                    round_distress_increase += emotion_distress
                    proposals.append(StateTransitionEngine.make_proposal(
                        target_state="victim_distress", delta=emotion_distress,
                        source="victim_self_doubt", rule_id="VICTIM_SELF_DOUBT",
                        event_id=f"round{state.round}_{msg.sender_id}_selfdoubt",
                    ))
                    working_distress += emotion_distress
                    print(f"    [{msg.sender_name}] self_doubt(阻尼{emotion_damping:.0%}) -> V提案+{emotion_distress:.2f}")

            if supportive_score > 0.3:
                proposals.append(StateTransitionEngine.make_proposal(
                    target_state="victim_distress", delta=-supportive_comfort,
                    source="victim_self_support", rule_id="VICTIM_SUPPORT",
                    event_id=f"round{state.round}_{msg.sender_id}_support",
                ))
                working_distress = max(0.0, working_distress - supportive_comfort)

        elif role == "bystander_a":
            if supportive_score > bystander_a_supportive_threshold:
                distress_dec = supportive_score * bystander_a_distress_reduction
                proposals.append(StateTransitionEngine.make_proposal(
                    target_state="victim_distress", delta=-distress_dec,
                    source="bystander_a_support", rule_id="BYSTANDER_A_SUPPORT",
                    event_id=f"round{state.round}_{msg.sender_id}",
                ))
                proposals.append(StateTransitionEngine.make_proposal(
                    target_state="bystander_intervention", delta=supportive_score * 0.3,
                    source="bystander_a_support", rule_id="BYSTANDER_A_SUPPORT",
                    event_id=f"round{state.round}_{msg.sender_id}_bi",
                ))
                working_distress = max(0.0, working_distress - distress_dec)
                print(f"    [{msg.sender_name}] support -> V提案-{distress_dec:.2f}")
                # R7：同伴支持为「支持残余」提供新增证据 → 刷新
                state.support_residual = max(state.support_residual, 0.30)

        elif role == "bystander_b":
            if supportive_score > 0.3:
                proposals.append(StateTransitionEngine.make_proposal(
                    target_state="bystander_intervention", delta=supportive_score * 0.3,
                    source="bystander_b_support", rule_id="BYSTANDER_B_SUPPORT",
                    event_id=f"round{state.round}_{msg.sender_id}",
                ))
                if supportive_score > 0.4:
                    proposals.append(StateTransitionEngine.make_proposal(
                        target_state="victim_distress", delta=-supportive_score * 0.1,
                        source="bystander_b_support", rule_id="BYSTANDER_B_SUPPORT",
                        event_id=f"round{state.round}_{msg.sender_id}_distress",
                    ))
                    working_distress = max(0.0, working_distress - supportive_score * 0.1)
                    # R7：群体层面的支持为「规范残余」提供新增证据 → 轻度刷新
                    state.norm_residual = max(state.norm_residual, 0.15)

    # ===== 后处理规则（基于预测值产出提案）=====
    predicted_bullying = working_bullying

    # 自然恢复机制（霸凌停止）
    if predicted_bullying < natural_recovery_threshold and working_distress > 0:
        recovery_boost = working_distress * 0.08
        total_recovery = natural_recovery_rate + recovery_boost
        proposals.append(StateTransitionEngine.make_proposal(
            target_state="victim_distress", delta=-total_recovery,
            source="natural_recovery", rule_id="NATURAL_RECOVERY",
            event_id=f"round{state.round}_recovery",
        ))
        print(f"    [Recovery] 霸凌停止,自然恢复 V提案-{total_recovery:.2f}")

    # Bullying→Distress 耦合（霸凌强度下降时减压）
    if predicted_bullying < prev_attack_frequency:
        bullying_decrease = prev_attack_frequency - predicted_bullying
        distress_decrease = bullying_decrease * distress_bullying_coupling * 1.5
        proposals.append(StateTransitionEngine.make_proposal(
            target_state="victim_distress", delta=-distress_decrease,
            source="bullying_distress_coupling", rule_id="COUPLING",
            event_id=f"round{state.round}_coupling",
        ))
        print(f"    [Coupling] 霸凌-{bullying_decrease:.2%}, 压力提案-{distress_decrease:.2f}")

    # 低霸凌时自然减压
    if predicted_bullying < distress_decay_threshold and working_distress > 0.03:
        decay_amount = distress_decay_rate * (1 + working_distress * 0.5)
        proposals.append(StateTransitionEngine.make_proposal(
            target_state="victim_distress", delta=-decay_amount,
            source="distress_decay", rule_id="DISTRESS_DECAY",
            event_id=f"round{state.round}_decay",
        ))
        print(f"    [DistressDecay] 霸凌较低({predicted_bullying:.2%}), 压力衰减提案-{decay_amount:.2f}")

    # 干预后额外减压
    if state.last_intervention_effective and predicted_bullying < prev_attack_frequency:
        intervention_relief = config.get("victim_distress", {}).get("intervention_relief_rate", 0.08)
        pressure_relief = working_distress * 0.1
        total_intervention_relief = intervention_relief + pressure_relief
        proposals.append(StateTransitionEngine.make_proposal(
            target_state="victim_distress", delta=-total_intervention_relief,
            source="intervention_relief", rule_id="INTERVENTION_RELIEF",
            event_id=f"round{state.round}_interv_relief",
        ))
        print(f"    [InterventionRelief] 干预减压提案-{total_intervention_relief:.2f}")

    # ===== 统一写入（Phase 2：StateTransitionEngine 唯一写入点）=====
    # R5：学生事件轮的写入上下文随日志留痕（assessment_id=None 表示非教师事件）
    engine = StateTransitionEngine(config)
    engine.apply_proposals(
        state, proposals,
        metadata={
            "source_role": "student_events",
            "assessment_id": None,
            "observed_effectiveness": getattr(state, "observed_effectiveness", "unverified"),
            "status": "applied",
        },
    )

    # Phase 5：观察制效果判定（干预后下一轮据 attack_frequency 实际变化）
    obs = getattr(state, "pending_intervention_observation", None)
    if obs and state.round > obs.get("round", state.round):
        before = obs.get("attack_frequency_before", 0.0)
        after = state.attack_frequency
        dropped = before - after
        if dropped >= 0.05:
            state.last_intervention_effective = True
            state.observed_effectiveness = "verified_effective"
            print(f"    [ObservedEffect] 干预有效：attack_frequency {before:.3f}→{after:.3f}")
        else:
            state.last_intervention_effective = False
            state.observed_effectiveness = "verified_ineffective"
            print(f"    [ObservedEffect] 干预未显效：attack_frequency {before:.3f}→{after:.3f}")
        state.pending_intervention_observation = None

    # Phase 3：bullying_intensity 为派生值 B=0.40F+0.35S+0.25P
    state.bullying_intensity = state.derive_bullying_intensity()

    if state.bullying_intensity > 0.3:
        state._prev_bullying_high = True

    # ===== 检查干预效果是否持续 =====
    if state.last_intervention_effective and state.bullying_intensity > 0.15:
        state.last_intervention_effective = False
        print(f"    [InterventionCheck] 霸凌仍在活跃({state.bullying_intensity:.2%}), 干预效果标记重置")

    state.pending_messages = []

    # 获取 OBVQ 得分信息
    obvq_score_info = state.obvq_state.get_score_info()

    state.metrics_history.append({
        "round": state.round,
        "bullying_intensity": state.bullying_intensity,
        "victim_distress": state.victim_distress,
        "bystander_intervention": state.bystander_intervention,
        "obvq_total_score": obvq_score_info["total_score"],
    })

    # R8（方案 §6.1）：内部状态全量轨迹入库（含残余效应），不能只保留汇总 B
    state.append_internal_state_history()

    distress_change = state.victim_distress - prev_distress
    print(f"    Final: B={state.bullying_intensity:.2%} V={state.victim_distress:.2%} "
          f"OBVQ={obvq_score_info['total_score']} "
          f"(round change {'+' if distress_change >= 0 else ''}{distress_change:.2%})")

    return {
        "bullying_intensity": state.bullying_intensity,
        "victim_distress": state.victim_distress,
        "bystander_intervention": state.bystander_intervention,
        "pending_messages": [],
        "obvq_total_score": obvq_score_info["total_score"],
        "obvq_dimension_totals": obvq_score_info["dimension_totals"],
        "obvq_dimension_averages": state.obvq_state.get_dimension_averages(),
    }


# ============================================================
# Intervention Evaluation - OBVQ Enhanced (Step 3)
# ============================================================

# ---- OBVQ 维度视角关键词库(Step 3 新增)----
# 用于判断干预措施是否针对当前主要欺负类型
OBVQ_DIM_INTERVENTION_KEYWORDS = {
    "physical": [
        "身体", "打", "踢", "推", "撞", "打伤", "动手", "打了一顿", "打他",
        "打同学", "打人", "身体攻击", "体罚", "粗鲁对待", "身体伤害",
        "不许动手", "不能打人", "动手不对", "禁止打人", "停止动手",
    ],
    "verbal": [
        "绰号", "骂人", "嘲笑", "讽刺", "言语", "语言", "说话", "骂", "辱骂",
        "侮辱", "取笑", "挖苦", "语言攻击", "言语伤害", "嘲笑别人", "说坏话",
        "不许骂", "不能嘲笑", "停止嘲讽", "禁止骂人", "不许讽刺",
    ],
    "relational": [
        "孤立", "不带", "不跟", "排挤", "谣言", "散布", "背后说", "说坏话",
        "不带你", "不理你", "不跟你玩", "破坏友谊", "人际关系", "关系欺负",
        "不许孤立", "停止排挤", "禁止散布", "不应该孤立", "不带不对",
    ],
    "cyber": [
        "网络", "群里", "班级群", "QQ群", "微信群", "发群里", "发照片", "发视频",
        "截图", "发到网上", "社交媒体", "发信息", "网上", "群里说",
        "撤回", "删掉", "停止传播", "不许发群", "禁止发群", "删除",
    ],
    "power": [
        "强迫", "威胁", "敲诈", "收保护费", "抢东西", "抄作业", "要挟",
        "控制", "命令", "威胁你", "不许抄", "禁止威胁", "停止强迫",
        "不许收保护费", "不许敲诈", "不应该要挟",
    ],
    "prejudice": [
        "单亲", "家庭", "缺陷", "残疾", "外表", "长相", "偏见", "歧视",
        "嘲笑外表", "嘲笑家庭", "嘲笑缺陷", "性取向", "宗教", "种族",
        "家庭背景", "外貌", "长相问题", "不许嘲笑家庭", "禁止歧视",
    ],
}

# OBVQ 维度中文名称映射
OBVQ_DIM_LABELS = {
    "physical": "身体欺负",
    "verbal": "言语欺负",
    "relational": "关系欺负",
    "cyber": "网络欺负",
    "power": "权力欺负",
    "prejudice": "偏见欺负",
}

# 旁观者视角关键词(Step 3 新增)
BYSTANDER_POSITIVE = [
    # 注：「支持受害者」类词含受害者姓名，须按当前会话受害者名动态生成
    # （见 _detect_bystander_keywords），否则 8 套场景 cast 改名后永远匹配不上。
    # 反对欺负行为
    "不应该欺负", "欺负不对", "欺负是错的", "欺负是问题",
    "反对欺负", "不要欺负", "停止欺负", "不许欺负",
    # 鼓励旁观
    "勇敢站出来", "你们应该帮助", "应该阻止", "有同学应该帮忙",
    "围观不对", "不帮忙也是错", "旁观也是问题",
    # 集体责任
    "全班要反省", "班级氛围", "班级风气", "我们班级", "大家反省",
    "全班讨论", "集体反思", "全班同学",
]

BYSTANDER_NEGATIVE = [
    # 旁观者倾向
    "跟你有什么关系", "别管闲事", "跟你无关", "关你什么事",
    "看看热闹", "别掺和", "别多管", "少管别人",
    "看戏", "围观", "在旁边",
]

# ---- 维度视角→干预效果映射 ----
# 不同维度欺负需要不同的干预方式
DIM_INTERVENTION_EFFECTIVENESS = {
    # 维度 -> (有效干预关键词类型, 有效干预类型)
    "physical": (["规则", "反省", "行为识别"], "需要明确规则和后果"),
    "verbal": (["言语", "反思", "共情"], "需要换位思考和语言规范"),
    "relational": (["关系", "社交支持", "旁观者"], "需要全班讨论和关系修复"),
    "cyber": (["网络", "删除", "制止"], "需要技术干预和规则教育"),
    "power": (["规则", "后果", "权力"], "需要明确权力边界和法律意识"),
    "prejudice": (["偏见", "共情", "尊重"], "需要多元包容教育和价值观引导"),
}


def _detect_obvq_dimension_in_intervention(action: str) -> list:
    """
    检测干预内容涉及哪些 OBVQ 维度
    返回维度列表(按匹配度排序)
    """
    matched_dims = []
    for dim_name, keywords in OBVQ_DIM_INTERVENTION_KEYWORDS.items():
        matched = sum(1 for kw in keywords if kw in action)
        if matched > 0:
            matched_dims.append((dim_name, matched))
    matched_dims.sort(key=lambda x: x[1], reverse=True)
    return [d[0] for d in matched_dims]


def _detect_bystander_keywords(action: str, victim_name: str = None) -> Tuple[int, int, List[str]]:
    """
    检测旁观者视角关键词
    返回 (positive_count, negative_count, matched_keywords)
    """
    # 「支持受害者」类词须按当前受害者名动态生成（8 套场景 cast 改名后不再是「思远」）
    name = (victim_name or "").strip() or "思远"
    victim_words = [
        f"站在{name}这边", f"支持{name}", f"安慰{name}", f"帮{name}说话", f"为{name}发声",
        f"帮助{name}", f"关心{name}", f"陪{name}", f"和{name}一起",
    ]
    pos_kws, neg_kws = [], []
    for kw in victim_words + BYSTANDER_POSITIVE:
        if kw in action:
            pos_kws.append(kw)
    for kw in BYSTANDER_NEGATIVE:
        if kw in action:
            neg_kws.append(kw)
    return len(pos_kws), len(neg_kws), pos_kws + neg_kws


def _get_dominant_obvq_dimension(state: SharedState) -> Tuple[str, float]:
    """
    获取当前最突出的 OBVQ 欺负维度
    返回 (维度名, 维度得分0-1)
    """
    dim_avgs = state.obvq_state.get_dimension_averages()
    if not dim_avgs or all(v == 0 for v in dim_avgs.values()):
        return "verbal", 0.1  # 默认言语欺负
    dominant = max(dim_avgs.items(), key=lambda x: x[1])
    return dominant[0], dominant[1]


def _recommendation_from_score(score, risk_level="none"):
    """按百分制质量分 + 风险等级给出五档推荐。"""
    if score is None:
        return "本轮干预评分待评估"
    if risk_level == "serious":
        return "⚠️ 存在严重风险：请立即调整干预方式，优先保障受害者安全与隐私"
    if score >= 90:
        return "🌟 优秀：干预清晰、具体、适切，兼顾制止与受害者保护"
    if score >= 80:
        return "👍 良好：干预基本到位，可在细节与跟进上进一步完善"
    if score >= 70:
        return "📚 中等：方向正确，建议加强受害者保护与行为改变引导"
    if score >= 60:
        return "📝 及格：识别了问题但干预不够具体或未充分落实"
    return "❌ 不及格：干预可能未有效制止欺凌或存在风险，请反思并寻求指导"


def evaluate_intervention(state: SharedState, intervention_content: str, llm_client=None) -> dict:
    """
    评估教师干预质量（Phase 1: 统一评分合同）

    改为调用 InterventionExpertAgent 对整段互动进行 D1-D7 百分制评分：
    - 评分口径 0-100，N/O 维度剔除分母
    - 严重风险（serious）最终总分封顶 60
    - 后端重算总分，以程序值为准
    - 不回退关键词打分

    状态变化为过渡逻辑（Phase 2 将由 StateTransitionEngine 统一写入替代）：
    用最终质量分（0-100）做简化强度调整，阈值与旧 0-1 口径线性对应。
    """
    from .intervention_expert import InterventionExpertAgent
    from .state_transition_engine import StateTransitionEngine

    config = state.config
    action = intervention_content.strip()

    session_tag = getattr(state, "session_id", "") or "sess"
    # R5：assessment_id 携带干预序号，同轮多次干预不冲突（去重键不含轮次前缀共享）
    assessment_id = f"{session_tag}_r{state.round}_i{state.intervention_count + 1}"

    # 1) 调用专家评分（只评分，不改状态）
    expert = InterventionExpertAgent(llm_client=llm_client)
    assessment = expert.assess(state, intervention_content, assessment_id)

    feedback = []

    # 记录状态变化前的强度（用于计算实际变化量，非伪因果固定值）
    intensity_before = state.bullying_intensity
    distress_before = state.victim_distress

    if assessment.status == "scored":
        quality_score = assessment.final_quality_score_100
        raw_score = assessment.raw_mean_score_100
        risk_level = assessment.risk_level
        capped = assessment.risk_cap_applied

        # R15：无意义内容护栏 → 明确告知判 0 的依据
        if getattr(assessment, "meaningless", False):
            feedback.append("⚠️ 干预内容无实际干预功能，按量规判 0 分（明显不当）")

        # --- 反馈 ---
        dim_desc = []
        for dim_key in ["D1", "D2", "D3", "D4", "D5", "D6", "D7"]:
            d = assessment.dimensions.get(dim_key)
            if d is None:
                continue
            if d.status == "scored" and d.score is not None:
                dim_desc.append(f"{dim_key}={d.score}")
            elif d.status == "N/O":
                dim_desc.append(f"{dim_key}=N/O")
        if dim_desc:
            feedback.append("维度评分: " + " ".join(dim_desc))
        if capped:
            feedback.append(f"⚠️ 严重风险，总分封顶 60（原始均分 {raw_score}/100）")
        elif risk_level == "serious":
            # 封顶未生效：原始均分本就低于 60，须明确说明「未额外扣分」，避免被误解为被规则扣分
            feedback.append(f"⚠️ 存在严重风险（原始均分 {raw_score}/100，本身已低于 60，封顶未额外扣分）")
        elif risk_level == "attention":
            feedback.append("⚠️ 存在需关注的风险项")
        if assessment.summary:
            feedback.append(assessment.summary)

        # --- 受害者关怀度：从 D2 维度推导（0-1，保持 0-1 口径，供 empathy 评估）---
        d2 = assessment.dimensions.get("D2")
        if d2 is not None and d2.status == "scored" and d2.score is not None:
            victim_care_score = d2.score / 100.0
        else:
            victim_care_score = 0.0

        # --- 教师干预效果：分维度作用映射（Phase 3）---
        # 用 dimension_mapping 生成内部状态提案（K(s)/M4 + 专项风险门控 + D6 跟进）
        from .dimension_mapping import build_intervention_proposals, K_eff, _dim_score as _dm_score
        proposals, risk_gates = build_intervention_proposals(assessment, state, config)

        # R4（方案 §15.1）：预测效果 —— 引擎写入前从提案导出（模型预测，不是观测事实）
        predicted_effect_profile: Dict = {}
        _pred_dF = _pred_dS = _pred_dP = _pred_dV = 0.0
        for _p in proposals:
            _tgt = _p["target_state"]
            _d = _p["delta"]
            _slot = predicted_effect_profile.setdefault(_tgt, {"predicted_delta": 0.0, "rules": []})
            _slot["predicted_delta"] = round(_slot["predicted_delta"] + _d, 4)
            if _p["rule_id"] not in _slot["rules"]:
                _slot["rules"].append(_p["rule_id"])
            if _tgt == "attack_frequency":
                _pred_dF += _d
            elif _tgt == "attack_severity":
                _pred_dS += _d
            elif _tgt == "persistence":
                _pred_dP += _d
            elif _tgt == "victim_distress":
                _pred_dV += _d
        predicted_bullying_reduction = round(-(0.40 * _pred_dF + 0.35 * _pred_dS + 0.25 * _pred_dP), 4)
        predicted_distress_change = round(_pred_dV, 4)

        engine = StateTransitionEngine(config)
        engine.apply_proposals(
            state, proposals,
            metadata={
                "assessment_id": assessment_id,
                "source_role": "teacher",
                "risk_gates": risk_gates,
                "predicted_effect_profile": predicted_effect_profile,
                "observed_effectiveness": "unverified",
                "status": "predicted_effect_pending_observation",
            },
        )

        # R7（方案 §8.3）：设置干预残余效应初值（= 对应维度 K_eff；残余不属于动态状态）
        _d4_s = _dm_score(assessment, "D4")
        state.immediate_stop_residual = K_eff(_dm_score(assessment, "D1"), _d4_s)
        state.support_residual = K_eff(_dm_score(assessment, "D2"), _d4_s)
        state.norm_residual = K_eff(_dm_score(assessment, "D5"), _d4_s)

        # 派生前端兼容的 bullying_intensity（B=0.40F+0.35S+0.25P）
        state.bullying_intensity = state.derive_bullying_intensity()

        # 干预效果标记（Phase 5：观察制，不得因高分立即设 effective）
        # 干预后先记 unverified，下一轮 _evaluator_logic 据 attack_frequency 实际变化判定。
        state.last_intervention_effective = False
        state.observed_effectiveness = "unverified"
        state.pending_intervention_observation = {
            "round": state.round,
            "assessment_id": assessment_id,
            "attack_frequency_before": getattr(state, "attack_frequency", 0.0),
            "predicted_effect_profile": predicted_effect_profile,
        }

        # Phase 4：记录维度分与风险等级（供 p_attack / 收敛判断用）
        dim_scores = {}
        for dim_key in ["D1", "D2", "D3", "D4", "D5", "D6", "D7"]:
            d = assessment.dimensions.get(dim_key)
            if d is None:
                dim_scores[dim_key] = None
            elif d.status == "scored" and d.score is not None:
                dim_scores[dim_key] = d.score
            else:
                dim_scores[dim_key] = None
        state.last_intervention_dimensions = dim_scores
        state.last_intervention_risk_level = risk_level

        # --- 推荐 ---
        recommendation = _recommendation_from_score(quality_score, risk_level)

        # 质量分历史（0-100）
        state.intervention_quality_history.append(quality_score)

        # R4（方案 §15.1）：实际效果由状态前后差计算（正值=下降/压力减少）；
        # 与预测值分开存储，旧字段名保留为 actual 别名（禁止再按评分阈值派生）
        actual_bullying_reduction = round(max(0.0, intensity_before - state.bullying_intensity), 4)
        actual_distress_change = round(state.victim_distress - distress_before, 4)  # 带符号（正=压力上升）
        actual_distress_reduction = round(max(0.0, distress_before - state.victim_distress), 4)

        intervention_record = InterventionRecord(
            round=state.round,
            content=intervention_content,
            quality_score=quality_score,
            victim_care_score=victim_care_score,
            feedback=feedback,
            assessment_id=assessment_id,
            quality_score_scale=100,
            predicted_bullying_reduction=predicted_bullying_reduction,
            predicted_distress_change=predicted_distress_change,
            actual_bullying_reduction=actual_bullying_reduction,
            actual_distress_change=actual_distress_change,
            bullying_reduction=actual_bullying_reduction,
            distress_reduction=actual_distress_reduction,
        )
        status = "scored"

        # R8：干预轮同样记录内部状态快照
        state.append_internal_state_history()
    else:
        # 待评估：不回退关键词打分，状态不变
        # R11：待评估存 quality_score=None（不得伪造 0 分污染统计）
        quality_score = None
        victim_care_score = 0.0
        distress_reduction = 0.0
        # R17：明确待评估原因（无密钥 / 模型输出异常 / 结构不合法），不再笼统提示
        _pending_reason = str(getattr(assessment, "parse_error", "") or "")
        if "no_llm_client" in _pending_reason:
            _pending_note = "专家评分未配置 API 密钥/模型客户端，评分与指导提示均不可用"
        elif "unparseable_json" in _pending_reason:
            _pending_note = "专家评分返回内容无法解析（模型输出格式异常）"
        elif "invalid_schema" in _pending_reason:
            _pending_note = "专家评分返回结构不符合量规要求"
        else:
            _pending_note = "专家评分未能返回有效结构化结果"
        recommendation = f"本轮干预评分待评估（{_pending_note}）"
        state.last_intervention_effective = False
        feedback.append(f"⚠️ 本轮评分待评估：{_pending_note}")
        intervention_record = InterventionRecord(
            round=state.round,
            content=intervention_content,
            quality_score=None,
            victim_care_score=0.0,
            feedback=feedback,
            assessment_id=assessment_id,
            quality_score_scale=100,
            predicted_bullying_reduction=None,
            predicted_distress_change=None,
            actual_bullying_reduction=None,
            actual_distress_change=None,
            bullying_reduction=0.0,
            distress_reduction=0.0,
        )
        status = "pending"

    # 记录状态
    state.last_intervention = intervention_content
    state.last_intervention_round = state.round
    state.intervention_count += 1

    import datetime
    msg = Message(
        sender_id="teacher",
        sender_name="张老师",
        role="teacher",
        content=intervention_content,
        timestamp=datetime.datetime.now().strftime("%H:%M:%S"),
        round=state.round,
    )
    state.messages.append(msg)

    obvq_info = state.obvq_state.get_score_info()

    _iv_rec = intervention_record.to_dict()
    return {
        "quality_score": quality_score,
        "quality_score_scale": 100,
        "victim_care_score": victim_care_score,
        "feedback": feedback,
        "recommendation": recommendation,
        "bullying_intensity": state.bullying_intensity,
        "victim_distress": state.victim_distress,
        "intervention_record": _iv_rec,
        "predicted_bullying_reduction": _iv_rec.get("predicted_bullying_reduction"),
        "predicted_distress_change": _iv_rec.get("predicted_distress_change"),
        "actual_bullying_reduction": _iv_rec.get("actual_bullying_reduction"),
        "actual_distress_change": _iv_rec.get("actual_distress_change"),
        "obvq_total_score": obvq_info["total_score"],
        "matched_obvq_dimensions": [],
        "dominant_obvq_dimension": "verbal",
        "dominant_obvq_dimension_label": "言语欺负",
        "bystander_score": 0.0,
        "assessment_status": status,
        "assessment": assessment.to_dict(),
    }


def evaluator_node(state: SharedState) -> dict:
    return _evaluator_logic(state)

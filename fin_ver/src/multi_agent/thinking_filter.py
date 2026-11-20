"""
思维过滤器 - 提取对话内容，移除思考过程
策略：多段落时按得分优选对话段落，仅保留明确的角色发言
"""

import re


# ── 思考模式关键词 ──
THINKING_PREFIXES = [
    '我觉得', '我认为', '我想', '作为', '应该',
    '需要考虑', '根据情境', '作为一个', '角色扮演',
    '在当前情况下', '在这种情况下', '从情境来看',
    '现在我是', '我需要扮演', '在这个情境中',
    '根据当前情境', '根据已知信息', '分析一下',
    '首先', '接下来', '总结', '综上', '总之',
    '可以看出', '明显', '显然', '由此可见',
    '这说明', '这表示', '建议', '结论',
    '我的角色是', '我的任务是', '本段对话中',
    '作为霸凌者', '作为被霸凌者', '作为旁观者',
    '扮演的是', '我的身份是',
]

# ── 非对话段落模式（多行编号/列表/分析） ──
ANALYSIS_PATTERNS = [
    re.compile(r'^\s*[\d一二三四五六七八九十]+[\.、:：)\s]'),   # 1. 2. 一、二、
    re.compile(r'^\s*[-*>•·]'),                                # - * >
    re.compile(r'^\s*步骤\s*\d'),                              # 步骤1
    re.compile(r'^(从|作为|根据|分析|考虑|注意|判断|评估|预测)'),
]

# 明确是角色对话的标点特征
DIALOGUE_MARKERS = re.compile(r'[""''「」『』《》（()）]')

# 第二人称/称呼：没有括注也像「当面对人说出口的话」，用于合并被空行拆开的台词
_ADDRESSEE = re.compile(r'你|您|咱们|大家|各位|老师|同学')

# v2026-11-14（Blocker 补强）：以「推理文本：」「我的分析：」这类标签开头整段，
# 是模型把思考过程写成的小标题段，不是说出口的台词。嵌套/交错 think 被配对正则
# 吃掉一段后，剩下的这类段落会以纯文本形式漏进台词，因此在段级过滤里整段剔除。
# 冒号前出现第二人称（「你听我分析：」）时按台词放行，避免误伤真实对话。
_REASONING_LABEL_HEAD = re.compile(r'^\s*([^：:\n]{1,16})[：:]')
_REASONING_LABEL_WORDS = re.compile(
    r'推理|思考|分析|判断|评估|结论|思路|内心独白|内心活动|角色说明|扮演说明'
)
_REASONING_LABEL_ADDRESS = re.compile(r'你|您|咱们')

# 思考标签
# v2026-11-14（Blocker 修复）：未闭合的开标签不再"只删到行尾"——那只删掉标记行、
# 把紧跟其后的整段推理留在台词里。现在删除范围是「到配对的闭合标记」；未闭合时
# 删到其后第一个空行（没有空行就删到文本末尾）。
_THINK_TAG_NAME = r'think(?:ing|s)?|thought(?:s)?|reasoning(?:_content)?|reason'
THINK_TAG = re.compile(
    r'<(?:' + _THINK_TAG_NAME + r')\b[^>]*>[\s\S]*?</(?:' + _THINK_TAG_NAME + r')\b[^>]*>',
    re.IGNORECASE,
)
THINK_TAG_OPEN = re.compile(
    r'<(?:' + _THINK_TAG_NAME + r')\b[^>]*>[\s\S]*?'
    r'(?:</(?:' + _THINK_TAG_NAME + r')\b[^>]*>|\n[ \t]*\n|\Z)',
    re.IGNORECASE,
)
THINK_TAG_REMNANT = re.compile(r'</?(?:' + _THINK_TAG_NAME + r')\b[^>]*>', re.IGNORECASE)
# 开 / 闭标记分别计数，用于「过滤前原文」的可疑判定（见 suspicious_think_markup）。
_THINK_OPEN_MARK = re.compile(r'<(?:' + _THINK_TAG_NAME + r')\b[^>]*>', re.IGNORECASE)
_THINK_CLOSE_MARK = re.compile(r'</(?:' + _THINK_TAG_NAME + r')\b[^>]*>', re.IGNORECASE)
_THINK_ANY_MARK = re.compile(r'</?(?:' + _THINK_TAG_NAME + r')\b[^>]*>', re.IGNORECASE)
_THINK_BLOCK_INNER = re.compile(
    r'<(?:' + _THINK_TAG_NAME + r')\b[^>]*>([\s\S]*?)</(?:' + _THINK_TAG_NAME + r')\b[^>]*>',
    re.IGNORECASE,
)


def suspicious_think_markup(raw_text: str, filtered_text: str = None) -> bool:
    """对**过滤前**的原始输出判定 think 族标记是否可疑（未闭合/嵌套/交错/残留）。

    守卫必须看原文：过滤器会把标记删光，用过滤后文本判定时恒为假（旧 Blocker 根因）。
    模型规矩地写了成对标签、且块内推理确实没有落进台词时返回 False——那属于已正确
    剥离，不该把 generation_source 误记为降级（保持既有 llm 语义统计口径不变）。
    """
    if not raw_text:
        return False
    marks = list(_THINK_ANY_MARK.finditer(raw_text))
    if not marks:
        return False
    opens = sum(1 for m in marks if not m.group(0).startswith('</'))
    closes = len(marks) - opens
    if opens != closes:
        return True                       # 未闭合 / 多余闭合标记
    depth = 0
    for m in marks:
        depth += 1 if not m.group(0).startswith('</') else -1
        if depth > 1 or depth < 0:
            return True                   # 嵌套或开闭交错
    if _THINK_ANY_MARK.search(THINK_TAG.sub('', raw_text)):
        return True                       # 配对块之外仍有残留标记
    if filtered_text:
        for inner in _THINK_BLOCK_INNER.findall(raw_text):
            core = re.sub(r'[\s\W_]+', '', inner)
            if len(core) >= 4 and core in re.sub(r'[\s\W_]+', '', filtered_text):
                return True               # 块内推理原样漏进了台词
    return False


# v2026-11-14（Major 修复）：分段合并前的转述段识别——「陈浩说：」「老师答道：」这类
# 他人原话的转述段，绝不并进当前角色的台词。
# 任务#60（Major-3 连带）：把姓名做成**捕获组**并用非贪婪 `{1,4}?`。原来在
# `_is_own_speech_paragraph` 里另起一条 `^\s*[\u4e00-\u9fa5]{1,4}` 取「段首姓名」，
# 贪婪匹配把动词也吞进去（"思远说：" → 取到 "思远说"），于是注释里承诺的
# 「段首姓名就是当前角色本人时放行」**从来没有成立过**（本人第三人称自述也被判成
# 他人转述）。这是把单段路径接上同一谓词时暴露出来的既有缺陷，不修就会变成
# 本轮新引入的回归。
# 任务#64 M-1（审查 #61 F4）：两处修正，都不扩大判定面。
#  (a) **动词按最长优先**。上一版把姓名做成捕获组 + 非贪婪 `{1,4}?`，注释里承诺
#      「段首姓名是本人 ⇒ 放行」，但 `(?:说|道|答|问)` 仍然允许姓名组**吞掉第一个
#      动词**：`思远说道：` 实测捕获 `group(1)="思远说"`，于是双音节动词（说道/问道/
#      叫道/答道/喊道/笑道）这一路**从来没有放行过**，6 个姓名 × 3 个动词 = 18 个
#      合法本人自述组合在单段路径被整条丢弃（`prompts.py:223` 要求单段成文，
#      单段不合格 ⇒ 返回 '' ⇒ supervisor 整条降级到预设）。
#      现在动词表把双音节形式排在单字前面，`思远说道：` 捕获 `思远` ⇒ 放行真的可达。
#  (b) **段首是对话人称代词时不做「他人转述」判定**。「你说：到底想怎么样。」是
#      当前角色在跟对面说话，「我说：…」是他的第一人称自述，都不是第三者原话的转述；
#      代词头仍然要过 `_has_audible_speech`，所以纯括注/纯旁白照样被拦。
#      「他/她/老师/陈浩」这类第三人称头保持原判（转述 ⇒ 剔除）。
#  (c) 动词前允许一段**有限的语气/动作副词**（`又/也/就/连忙/低声/小声/皱眉/点头/…`，
#      可带「地」）。不加这一段，`思远小声说道：` 会因为动词表变长而**新判成他人转述**
#      （旧式压根不匹配 ⇒ 放行），那是本轮新引入的误杀；加了以后捕获到的仍是 `思远`。
#      副词表是显式枚举而不是 `.{0,2}?`：谓词必须紧跟冒号才成立，所以
#      `那就说吧：`、`我这就说：` 这类非姓名开头的口吻句仍然匹配不到，行为与改前一致。
#      坑（实测踩过，别再改回去）：可选必须作用在**整个副词组**上，写成
#      `MODIFIER + r'?'` 只会把最后一个 `[地]` 变成可选，副词组本身仍是必需项，
#      结果是 `陈浩说：` 这类裸动词形态**全部失配**⇒ 他人转述重新漏回台词。
_REPORT_HEAD_PRONOUNS = frozenset({
    "我", "我们", "本人", "自己", "你", "你们", "您", "咱们", "大家", "诸位", "各位",
})
_REPORT_HEAD_MODIFIER = (
    r"(?:(?:又|也|便|就|才|还|连忙|赶紧|急忙|连声|低声|小声|大声|冷冷|淡淡|慢慢|轻轻|"
    r"随口|皱眉|摇头|点头|抬头)[地]?)?"
)
_OTHER_REPORT = re.compile(
    r'^\s*([\u4e00-\u9fa5]{1,4}?)' + _REPORT_HEAD_MODIFIER +
    r'(?:说道|问道|答道|叫道|喊道|笑道|说|道|问|答|叫|喊)'
    r'\s*[：:]',
)
# 合并段数上限：再多就像把整篇输出搬进台词，且实测对丰富度指标没有帮助。
MERGE_PARAGRAPH_LIMIT = 2

# 纯思考前缀（行首）
THINKING_LINE = re.compile(
    r'^\s*('
    r'思考[：:]\s*|'
    r'分析[：:]\s*|'
    r'推理[：:]\s*|'
    r'判断[：:]\s*|'
    r'结论[：:]\s*|'
    r'扮演说明[：:]\s*|'
    r'角色说明[：:]\s*|'
    r'我的思考[：:]\s*|'
    r'以下是我的|'
    r'我应该|'
    r'我作为|'
    r'我的发言[：:]?\s*|'
    r'发言内容[：:]\s*'
    r')'
)

# 纯分析型句子（不包含对话特征的独立行）
THINKING_SENTENCE = re.compile(
    r'^('
    r'作为(?:一个|一名|霸凌者|被霸凌者|旁观者|教师|学生|该角色|这个角色).*?(?:应该|需要|可以|会|要|首先)'
    r'|在当前情境下.*?(?:应该|需要|可以|会|要)'
    r'|根据(?:已知|当前|以上).*?(?:分析|判断|认为|看)'
    r'|.*?(?:分析|判断|评估|预测|总结)一下'
    r'|(?:接下来|首先|然后).*?(?:应该|需要|可以)'
    r'|我需要(?:扮演|表达|说出|表示|展现)'
    r'|我的角色(?:应该|需要|是)'
    r')'
)

# 模型特定格式残留
MODEL_FORMAT = re.compile(r'<(?:im_start|im_end|assistant|user|system)>')
MODEL_THINKING = re.compile(r'\\boxed\{|\\text\{|<｜end▁of▁thinking｜>\s*[:：]', re.IGNORECASE)
CHAIN_OF_THOUGHT = re.compile(r'^(Wait|Let me|I need to|First|Okay|So|Alright|Hmm)\b', re.IGNORECASE | re.MULTILINE)


def extract_dialogue_only(text: str, agent_name: str = '') -> str:
    """
    从 LLM 输出中提取纯对话内容，移除思考过程。

    策略（按优先级）：
    1. 清除 think / thinking / reasoning 标签块（连同内容），先剥离再分段合并
    2. 清除模型格式残留（im_start / im_end 等）
    3. 分段 → 段级过滤（剔除他人转述段、纯括注/纯旁白段）→ 最多合并 2 段
    4. 对选中段落做内联清理

    返回空串的含义：整条消息只有推理/思考内容，或没有任何"属于当前角色且说出口"
    的台词。
    调用方（supervisor._agent_logic）据此走 preset_filtered_response 降级到预设，
    不能把原文回吐——回吐会把推理文本当台词泄进对话（v2026-11-14 回归）。
    """
    if not text or not text.strip():
        return text

    original = text.strip()
    text = original.replace('\r\n', '\n').replace('\r', '\n')

    # ── Phase 1: 标签清除（必须先于分段合并）──
    text = THINK_TAG.sub('', text)
    text = THINK_TAG_OPEN.sub('', text)
    text = THINK_TAG_REMNANT.sub('', text)
    text = MODEL_FORMAT.sub('', text)
    text = MODEL_THINKING.sub('', text)
    text = text.strip()

    if not text:
        # 整条消息只有推理块 / 格式残留 → 明确返回空串，让调用方降级到预设。
        return ''

    # ── Phase 2: 按空行分段 ──
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]

    if not paragraphs:
        # 分段失败说明只剩空白/标记残片；回吐原文会把分析当台词，交给调用方降级。
        return ''

    if len(paragraphs) == 1:
        # 任务#60（Major-3）：单段是主流形态（提示词「重要约束」第 4 条明确要求
        # 2–4 句连成一段、单段成文），可上一轮的段级归属过滤只接在多段分支上，
        # 单段路径直接把「陈浩说：……」当成本角色台词回吐。
        # 现在与多段同口径：整段过 `_is_own_speech_paragraph`（思考小标题 / 他人转述
        # 段首 / 剥掉括注后没有可听见台词），不合格就返回空串，由调用方
        # supervisor._agent_logic 走 preset_filtered_response 降级 —— 与多段"全部
        # 不合格 ⇒ ''"完全一致，不回吐原文。
        # 注：单段分支不经过 `_limit_merge`，因此 MERGE_PARAGRAPH_LIMIT 对本路径无影响。
        if not _is_own_speech_paragraph(paragraphs[0], agent_name):
            return ''
        return _clean_single_paragraph(paragraphs[0], agent_name)

    # ── Phase 3: 给每段打分，只合并「属于当前角色、且确有可听见台词」的段落 ──
    # v2026-11-14（输出质量修复）：旧实现多段时只取最高分一段，实测把 66 字压到 18 字
    # （-72.7%），因此改为合并多段。
    # v2026-11-14（Major 修复）：合并前先按段过滤，否则
    #   「陈浩说：…」+「（低头）你别这样说。」会把陈浩的原话拼进当前角色（思远）的台词；
    #   两段纯括注旁白也会被合成一条"台词"并骗过 _speech_present。
    # 同时删掉 merged = ... or _collect(0) 与"末段兜底"：score>=0 的分析段整批拼接、
    # 末段兜底基本不可达，属半死代码；全部不合格时返回空串走不可用降级路径。
    scored = [(p, _dialogue_score(p)) for p in paragraphs]

    def _is_line_start(p):
        return any(p.startswith(prefix) for prefix in THINKING_PREFIXES)

    def _collect_conversational(p):
        """合格台词：强信号段，或带第二人称对象感的中文对话段（不含思考前缀开头）。"""
        score = _dialogue_score(p)
        if score >= 3:
            return True
        return score >= 2 and bool(_ADDRESSEE.search(p)) and not _is_line_start(p)

    def _collect(threshold):
        """同一套段级过滤下的宽松回退（只放宽打分阈值，不放宽转述/旁白过滤）。"""
        return _limit_merge(
            _dedupe_keep_order(
                _clean_paragraph(p, agent_name)
                for p, score in scored
                if score >= threshold and _is_own_speech_paragraph(p, agent_name)
            )
        )

    kept = _limit_merge(
        _dedupe_keep_order(
            _clean_paragraph(p, agent_name)
            for p in paragraphs
            if _collect_conversational(p) and _is_own_speech_paragraph(p, agent_name)
        )
    )
    merged = kept or _collect(1)
    if merged:
        return "\n\n".join(merged)

    # 全部段落都是他人转述 / 纯旁白 / 分析 → 返回空串，让调用方按既有"不可用"路径降级。
    return ''


def _has_audible_speech(text: str) -> bool:
    """剥掉括注（动作/神态/旁白）之后还剩字，才算真的说出口了话。"""
    stripped = re.sub(r'[（(][^（()）]*[）)]', '', text or '')
    return bool(re.sub(r'[\s\W_]+', '', stripped))


def _is_reasoning_label_paragraph(paragraph: str) -> bool:
    """整段是否以「推理文本：」「我的分析：」这类思考小标题起头（不是说出口的台词）。"""
    match = _REASONING_LABEL_HEAD.match(paragraph)
    if not match:
        return False
    head = match.group(1)
    if _REASONING_LABEL_ADDRESS.search(head):
        return False
    return bool(_REASONING_LABEL_WORDS.search(head))


def _is_own_speech_paragraph(paragraph: str, agent_name: str = '') -> bool:
    """段级过滤：剔除他人转述段、思考标签段与纯括注/纯旁白段。"""
    if _is_reasoning_label_paragraph(paragraph):
        return False
    match = _OTHER_REPORT.match(paragraph)
    if match:
        reported_name = match.group(1)
        # 任务#64 M-1(b)：段首是对话人称代词 ⇒ 不是「他人转述」，按普通台词段继续判。
        if reported_name in _REPORT_HEAD_PRONOUNS:
            return _has_audible_speech(paragraph)
        # 「陈浩说：……」出现在当前角色自己的台词里是转述他人原话，必须剔除；
        # 只有段首姓名就是当前角色本人时才放行（那是他的第三人称自述）。
        return bool(agent_name) and reported_name == agent_name
    return _has_audible_speech(paragraph)


def _clean_paragraph(paragraph: str, agent_name: str = '') -> str:
    cleaned = _clean_single_paragraph(paragraph, agent_name)
    return cleaned.strip() if cleaned else ''


def _dedupe_keep_order(cleaned_iter):
    kept, seen_norm = [], set()
    for cleaned in cleaned_iter:
        if not cleaned:
            continue
        norm = re.sub(r'\s+', '', cleaned)
        if norm in seen_norm:
            continue
        seen_norm.add(norm)
        kept.append(cleaned)
    return kept


def _limit_merge(kept, limit: int = MERGE_PARAGRAPH_LIMIT):
    """合并段数上限；超出时取最靠后的 limit 段（越靠后越贴近对上一条发言的回应），保持原顺序。"""
    return kept[-limit:] if len(kept) > limit else kept


def _dialogue_score(text: str) -> int:
    """
    对话得分：
    +2: 包含引号/括号动作描述（强信号）
    +1: 包含角色名/称呼（如"陈浩"）
    +1: 包含感叹句/反问
    +1: 长度适中（10-80字）
    -2: 以思考前缀开头
    -2: 包含编号列表（3条以上）
    -2: 以分析性句子开头
    -1: 是纯英文（可能co-thinking）
    """
    score = 0

    # 正向信号
    if DIALOGUE_MARKERS.search(text):
        score += 2
    if re.search(r'[,，!！?？。~～…]+', text) and len(text) < 200:
        score += 1  # 有标点变化 + 短 → 像自然对话
    if 10 < len(text) < 80:
        score += 1

    # 负向信号
    for pfx in THINKING_PREFIXES:
        if text.startswith(pfx):
            score -= 2
            break

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    numbered = sum(1 for l in lines if re.match(r'^\s*[\d一二三]+[\.、:：)]\s', l))
    if numbered >= 3:
        score -= 2

    if THINKING_SENTENCE.match(text):
        score -= 2

    if CHAIN_OF_THOUGHT.search(text):
        score -= 2

    # 纯英文且长→可能是英文 co-thinking
    if re.match(r'^[A-Za-z\s\d.,!?;:\-()]+$', text) and len(text) > 60:
        score -= 1

    return score


def _clean_single_paragraph(text: str, agent_name: str = '') -> str:
    """清理单段文本中的内联思考痕迹"""
    if not text:
        return text

    # 移除思考前缀行
    text = THINKING_LINE.sub('', text)

    # 清理行首的思考性句子
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # 跳过纯思考行
        if THINKING_SENTENCE.match(stripped) and not DIALOGUE_MARKERS.search(stripped):
            continue
        cleaned_lines.append(line)
    text = '\n'.join(cleaned_lines).strip()

    # 清理表情符号标记
    text = text.replace('\U0001f4ad', '').replace('\U0001f914', '').replace('\U0001f9e0', '')

    # 如果文本包含动作描述，且动作描述之前是思考性文字，从动作处截断
    match = re.search(r'[\uff08(][^\uff09)]*[\uff09)]', text)
    if match and match.start() > 10:
        before_action = text[:match.start()].strip()
        if _before_action_is_thinking(before_action):
            text = text[match.start():]

    # 从最后一个动作描述段开始截断（兜底：前面的都是铺垫）
    # 适用于: "我觉得应该帮他。（叹气）陈浩，别再说了。" 
    # → 只保留 "（叹气）陈浩，别再说了。"
    if not DIALOGUE_MARKERS.search(text[:20]) and len(text) > 60:
        # 往前找第一个含动作描述的起点
        all_actions = list(re.finditer(r'[\uff08(][^\uff09)]*[\uff09)]', text))
        if len(all_actions) >= 1:
            last_action = all_actions[-1]
            # 如果动作前有较长文本且开头不像对话
            before = text[:last_action.start()].strip()
            if before and not DIALOGUE_MARKERS.search(before):
                text = text[last_action.start():]

    return text.strip() or text


def _before_action_is_thinking(text: str) -> bool:
    """判断动作描述之前的文本是否是思考内容"""
    if not text:
        return False

    for indicator in THINKING_PREFIXES:
        if text.startswith(indicator):
            return True

    if DIALOGUE_MARKERS.search(text):
        return False

    return False


def filter_thinking_content(response, agent_name=''):
    """公开接口"""
    return extract_dialogue_only(response, agent_name)

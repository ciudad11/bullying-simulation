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

# 思考标签
THINK_TAG = re.compile(r'<think[^>]*>[\s\S]*?</think[^>]*>', re.IGNORECASE)
THINK_TAG_OPEN = re.compile(r'<think[^>]*>[^\n]*\n?', re.IGNORECASE)
THINK_TAG_REMNANT = re.compile(r'</?think[^>]*>', re.IGNORECASE)

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
    1. 清除 <think> 标签块
    2. 清除模型格式残留（<|im_start|> 等）
    3. 分段 → 对每段打分 → 选最高分段落（不是简单取最后一段）
    4. 对选中段落做内联清理
    """
    if not text or not text.strip():
        return text

    original = text.strip()
    text = original.replace('\r\n', '\n').replace('\r', '\n')

    # ── Phase 1: 标签清除 ──
    text = THINK_TAG.sub('', text)
    text = THINK_TAG_OPEN.sub('', text)
    text = THINK_TAG_REMNANT.sub('', text)
    text = MODEL_FORMAT.sub('', text)
    text = MODEL_THINKING.sub('', text)
    text = text.strip()

    if not text:
        return original

    # ── Phase 2: 按空行分段 ──
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]

    if not paragraphs:
        return original

    if len(paragraphs) == 1:
        return _clean_single_paragraph(paragraphs[0], agent_name)

    # ── Phase 3: 给每段打分，选最优段落 ──
    scored = [(p, _dialogue_score(p)) for p in paragraphs]

    # 如果存在得分 > 2 的高质量对话段，优先选第一个高分
    for p, score in scored:
        if score >= 3:
            return _clean_single_paragraph(p, agent_name)

    # 否则选得分最高的段落
    best = max(scored, key=lambda x: x[1])
    if best[1] >= 0:
        return _clean_single_paragraph(best[0], agent_name)

    # 所有段都像分析 → 取最后一段兜底
    return _clean_single_paragraph(paragraphs[-1], agent_name)


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

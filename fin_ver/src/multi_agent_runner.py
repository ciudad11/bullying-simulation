#!/usr/bin/env python3
"""
🎭 校园话语霸凌与教师干预实训系统 - 真正的多 Agent LLM 运行器
============================================
使用 LLM API 为每个角色生成独立的智能响应

核心特性：
1. 每个角色有独立的 LLM 调用
2. 长期记忆集成
3. 上下文感知决策
4. 干预响应机制
"""

import json
import time
import asyncio
import httpx
import os
from typing import Dict, List, Optional, Any, Callable
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

# 添加 src 到路径
import sys
sys.path.insert(0, str(Path(__file__).parent))

from agent_memory import memory_manager, AgentProfile
from orchestrator import AgentConfig, ConversationContext


# ============================================================
# 配置
# ============================================================
BASE_DIR = Path(__file__).parent.parent
LOGS_DIR = BASE_DIR / "logs"

# MiniMax API 配置
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_BASE_URL = "https://api.minimaxi.com/anthropic/v1"


# ============================================================
# LLM API 调用
# ============================================================
# 从 OpenClaw 配置读取 API Key
def get_openclaw_api_key():
    """从 OpenClaw 配置文件中读取 API Key"""
    import json
    openclaw_config_path = Path.home() / ".openclaw" / "openclaw.json"
    
    if openclaw_config_path.exists():
        try:
            with open(openclaw_config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            
            # providers 在 models 下面
            models = config.get("models", {})
            providers = models.get("providers", {})
            # 优先使用 minimax-cn（国内版，更便宜）
            if "minimax-cn" in providers:
                provider = providers["minimax-cn"]
                return provider.get("apiKey"), provider.get("baseUrl", "https://api.minimaxi.com/anthropic")
            # 其次使用 minimax
            elif "minimax" in providers:
                provider = providers["minimax"]
                return provider.get("apiKey"), provider.get("baseUrl", "https://api.minimax.io/anthropic")
        except Exception as e:
            print(f"读取 OpenClaw 配置失败: {e}")
    
    return None, None

# 获取 OpenClaw API Key
OPENCLAW_API_KEY, OPENCLAW_BASE_URL = get_openclaw_api_key()
print(f"LLM Client: 从 OpenClaw 读取到 API Key: {'已设置' if OPENCLAW_API_KEY else '未设置'}")


class LLMClient:
    """LLM API 客户端"""
    
    def __init__(self, api_key: str = None):
        # 优先级：传入的 key > 环境变量 > OpenClaw 配置
        self.api_key = api_key or MINIMAX_API_KEY or OPENCLAW_API_KEY
        self.base_url = MINIMAX_BASE_URL or OPENCLAW_BASE_URL or "https://api.minimaxi.com/anthropic"
    
    async def chat(self, messages: List[Dict], model: str = "MiniMax-M2.1", 
                   temperature: float = 0.8, max_tokens: int = 500) -> str:
        """
        发送聊天请求到 LLM API (Anthropic format)
        
        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大 token 数
        
        Returns:
            LLM 响应文本
        """
        if not self.api_key:
            # 如果没有 API key，使用预设响应
            return self._fallback_response(messages)
        
        url = f"{self.base_url}/messages"
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
        
        # 转换消息格式
        anthropic_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            if role == "system":
                anthropic_messages.append({"type": "text", "text": msg["content"]})
            elif role == "user":
                anthropic_messages.append({"role": "user", "content": [{"type": "text", "text": msg["content"]}]})
            elif role == "assistant":
                anthropic_messages.append({"role": "assistant", "content": [{"type": "text", "text": msg["content"]}]})
        
        payload = {
            "model": model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                
                result = response.json()
                # 提取响应文本 - 跳过 thinking 类型
                content = result.get("content", [])
                for item in content:
                    if item.get("type") == "text":
                        text = item.get("text", "").strip()
                        # 过滤掉包含思考过程的内容
                        if "💭" in text or "思考" in text[:100]:
                            continue
                        return text
                return ""
        
        except Exception as e:
            print(f"LLM API 调用失败: {e}")
            return self._fallback_response(messages)
    
    def _fallback_response(self, messages: List[Dict]) -> str:
        """备用响应（当没有 API key 时使用）"""
        # 分析消息内容来生成简单响应
        last_message = messages[-1]["content"] if messages else ""
        
        # 简单的关键词匹配
        if "陈浩" in last_message and ("攻击" in last_message or "嘲笑" in last_message):
            return "沉默"
        elif "思远" in last_message:
            return "（低着头不说话）"
        
        return "..."


# ============================================================
# Agent 角色系统提示词
# ============================================================
class AgentPrompts:
    """为每个角色生成系统提示词"""
    
    @staticmethod
    def get_base_prompt(agent_id: str, level: int) -> str:
        """获取基础提示词"""
        cfg = AgentConfig.get_agent_config(agent_id)
        if not cfg:
            return "你是一个角色扮演助手。"
        
        name = cfg["name"]
        role = cfg["role"]
        personality = cfg["personality"]
        
        # 获取长期记忆上下文
        memory_context = memory_manager.get_agent_context(agent_id)
        
        prompt = f"""你是一个初中生，正在和同学聊天。

## 你是谁

**姓名**: {name}
**角色**: {role}

### 性格特点
{chr(10).join(['- ' + t for t in personality.get('traits', [])])}

### 说话风格
{chr(10).join(['- ' + s for s in personality.get('speaking_style', [])])}

### 家庭背景
{personality.get('background', '')}

### 认知特点
{personality.get('cognitive_style', '')}"""
        
        if memory_context:
            prompt += f"\n\n## 历史记忆\n\n{memory_context}\n\n（这是你过去的表现，新对话开始时请参考这些信息）"
        
        return prompt
    
    @staticmethod
    def get_scenario_prompt(level: int, scenario: str = None) -> str:
        """获取场景提示词"""
        level_descriptions = {
            1: "轻微：同学之间偶尔的玩笑和嘲讽",
            2: "一般：持续的语言嘲讽、轻微社交排斥",
            3: "中等：明显的言语攻击、开始孤立受害者",
            4: "严重：持续的恶意攻击、公开羞辱",
            5: "极其严重：极端恶意、威胁、心理创伤"
        }
        
        level_info = level_descriptions.get(level, "一般")
        
        scenario_text = ""
        if scenario:
            scenario_text = f"\n\n## 当前场景\n\n{scenario}"
        
        return f"""【霸凌级别】
当前场景属于 {level_info} 级别。
（注意：你的发言应该符合当前级别的严重程度）

{scenario_text}
"""
    
    @staticmethod
    def get_context_prompt(conversation_history: List[Dict]) -> str:
        """获取对话上下文"""
        if not conversation_history:
            return "（暂无对话）"
        
        recent = conversation_history[-6:]  # 最近6条消息
        lines = []
        for msg in recent:
            sender = msg.get('sender', '未知')
            content = msg.get('content', '')
            role = msg.get('sender_role', '')
            lines.append(f"【{sender}】{content}")
        
        return "\n".join(lines)
    
    @staticmethod
    def get_bully_prompt(agent_id: str, level: int, conversation: List[Dict], scenario: str = None) -> str:
        """霸凌者专用提示词"""
        base = AgentPrompts.get_base_prompt(agent_id, level)
        scenario_info = AgentPrompts.get_scenario_prompt(level, scenario)
        context = AgentPrompts.get_context_prompt(conversation)
        
        return f"""{base}

{scenario_info}

## 行为规则

1. 有同学展示成果 → 嘲讽打压，降低对方存在感
2. 有人反驳自己 → 更激烈反击，维护"强者"形象
3. 被老师/教师干预 → 表面收敛，内心不服
4. 旁观者支持受害者 → 可能收敛或转移目标

## 当前对话

{context}

## 你的任务

作为**霸凌者**，根据上述对话和角色设定，自主决定是否发言以及说什么。

**发言要求**：
- 符合中学生的说话方式
- 根据级别调整攻击强度（1级轻微玩笑，5级极端恶意）
- 可以用"开玩笑"作为借口
- 拉拢旁观者（赵磊）附和

直接输出你的对话内容。如果选择不说话，输出"沉默"。
"""
    
    @staticmethod
    def get_victim_prompt(agent_id: str, level: int, conversation: List[Dict], scenario: str = None) -> str:
        """被霸凌者专用提示词"""
        base = AgentPrompts.get_base_prompt(agent_id, level)
        scenario_info = AgentPrompts.get_scenario_prompt(level, scenario)
        context = AgentPrompts.get_context_prompt(conversation)
        
        return f"""{base}

{scenario_info}

## 行为规则

1. 被嘲笑或攻击 → 习惯性忍让，可能自我反思
2. 有人表示支持 → 感到温暖但不敢相信
3. 被霸凌持续加剧 → 可能沉默或情绪低落
4. 被当众羞辱 → 极度窘迫，可能哭泣

## 当前对话

{context}

## 你的任务

作为**被霸凌者**，根据上述对话和角色设定，自主决定是否发言以及说什么。

**发言要求**：
- 符合内向学生的说话方式
- 声音小、语气弱
- 不要主动攻击他人
- 可以选择沉默

直接输出你的对话内容。如果选择不说话，输出"沉默"。
"""
    
    @staticmethod
    def get_bystander_a_prompt(agent_id: str, level: int, conversation: List[Dict], scenario: str = None) -> str:
        """正义旁观者专用提示词"""
        base = AgentPrompts.get_base_prompt(agent_id, level)
        scenario_info = AgentPrompts.get_scenario_prompt(level, scenario)
        context = AgentPrompts.get_context_prompt(conversation)
        
        return f"""{base}

{scenario_info}

## 行为规则

1. 看到明显不公正 → 会站出来说话
2. 受害者明显受伤害 → 安慰并指出问题
3. 老师/教师介入 → 支持干预

## 当前对话

{context}

## 你的任务

作为**正义旁观者**，根据上述对话和角色设定，自主决定是否发言以及说什么。

**发言要求**：
- 清晰明确，不卑不亢
- 善于说理
- 语气温和但坚定
- 可以选择支持受害者或指出霸凌问题

直接输出你的对话内容。如果选择不说话，输出"沉默"。
"""
    
    @staticmethod
    def get_bystander_b_prompt(agent_id: str, level: int, conversation: List[Dict], scenario: str = None) -> str:
        """从众旁观者专用提示词"""
        base = AgentPrompts.get_base_prompt(agent_id, level)
        scenario_info = AgentPrompts.get_scenario_prompt(level, scenario)
        context = AgentPrompts.get_context_prompt(conversation)

        return f"""你必须以赵磊的身份说话，不要解释、不要拒绝角色。

{scenario_info}

## 行为规则

1. 霸凌者开始攻击 -> 可能会附和
2. 看到有人支持受害者 -> 可能动摇
3. 老师明确反对霸凌 -> 会站在老师一边

## 当前对话

{context}

## 你的任务

以**赵磊**的身份，根据上述对话，自主决定是否发言以及说什么。

**发言要求**：
- 随声附和，不明确表态
- 用"我也是这么觉得"之类的话
- 可能被强者影响
- 可以选择沉默或附和

直接输出你的对话内容。如果选择不说话，输出"沉默"。
"""

    @staticmethod
    def get_victim_emotion_prompt(conversation: List[Dict]) -> str:
        """受害者情绪状态感知提示词"""
        context = AgentPrompts.get_context_prompt(conversation)

        return f"""分析以下对话中受害者（思远）的情绪状态：

{context}

请分析：
1. 思远最近是否被攻击？攻击次数有多少？
2. 思远是否有沉默/退缩的表现？
3. 有没有人支持过思远？支持次数有多少？

根据以上信息，判断思远的情绪状态：
- 低度压力：刚被攻击1次以内，有支持
- 中度压力：被攻击2-3次，沉默明显
- 高度压力：被攻击3次以上，几乎不说话，或有自我否定言论

直接输出：低度压力 / 中度压力 / 高度压力
"""

    @staticmethod
    def get_bystander_a_intervention_prompt(conversation: List[Dict], bullying_intensity: float = 0.3) -> str:
        """旁观者A干预时机判断提示词"""
        context = AgentPrompts.get_context_prompt(conversation)

        triggers = []
        if bullying_intensity >= 0.3:
            triggers.append("- 霸凌者连续攻击2轮以上")
        if bullying_intensity >= 0.2:
            triggers.append("- 受害者沉默2轮以上")
        triggers.append("- 旁观者B附和霸凌者")
        triggers.append("- 当众羞辱受害者")

        strategies = [
            "直接制止：够了",
            "支持受害者：思远别理他们",
            "转移话题：老师要来了",
            "说理教育：这样说是霸凌行为",
        ]

        return f"""分析以下对话，判断是否需要干预，以及用什么策略：

{context}

当前霸凌强度：{bullying_intensity:.0%}

**干预触发条件**：
{chr(10).join(triggers)}

**干预策略库**：
{chr(10).join(strategies)}

请判断是否干预：
- 如果需要干预，输出：干预 + 策略编号
- 如果不需要干预，输出：继续观察

直接输出。
"""

def get_intervention_prompt(intervention: str, conversation: List[Dict]) -> str:
        """教师干预提示词"""
        context = AgentPrompts.get_context_prompt(conversation)
        
        return f"""教师进行了干预：

"{intervention}"

## 当前对话

{context}

## 你的任务

你是模拟中的角色之一。请根据上述教师的干预和当前对话，决定你的回应：

1. 如果你是霸凌者：是否会收敛或转移目标？
2. 如果你是被霸凌者：是否感到被支持？
3. 如果你是旁观者：你的立场有变化吗？

直接输出角色的回应内容。如果干预对你没有影响，输出"沉默"。
"""


# ============================================================
# 协调者 - 智能规划每轮对话
# ============================================================
class Coordinator:
    """
    协调者 (Coordinator)
    
    核心职责：根据干预效果和模拟情境，智能决定每轮谁发言、发言顺序。
    
    决策原则：
    1. 每轮至少一个角色发言，但不是所有角色都发言
    2. 发言顺序和角色选择受干预效果影响
    3. 霸凌者通常是第一发言者（主动发起攻击）
    4. 受害者/旁观者的发言取决于当前情境
    5. 干预后角色反应动态变化
    """

    # 角色优先级常量
    PRIORITY_BULLY = "chenhao"
    PRIORITY_VICTIM = "siyuan"
    PRIORITY_Bystander_A = "linyutong"
    PRIORITY_Bystander_B = "zhaolei"

    def __init__(self, simulator=None):
        self.simulator = simulator
        # 追踪最近各角色的发言轮次，避免连续重复发言
        self.last_spoke_round: Dict[str, int] = {
            "chenhao": -3,
            "siyuan": -3,
            "linyutong": -3,
            "zhaolei": -3,
        }
        self.current_round = 0

    def plan_round(self, round_num: int, conversation_history: List[Dict],
                   intervention_effect: float = 0.0,
                   bullying_intensity: float = 0.0,
                   victim_distress: float = 0.0,
                   time_since_intervention: float = 999,
                   last_intervention_quality: float = 0.5) -> List[Dict]:
        """
        规划一轮对话

        Returns:
            List of speaking plans, each containing:
            - agent_id: 角色ID
            - trigger: 触发原因（"bully_first" | "intervention_reaction" | "context_response"）
        """
        self.current_round = round_num
        plan = []

        # ---- 获取最近发言历史 ----
        recent_speakers = [m.get("sender_id") for m in conversation_history[-4:]]
        last_speaker = recent_speakers[-1] if recent_speakers else None

        # ---- 核心决策逻辑 ----

        # 判断是否在干预冷却期内（干预后15秒内）
        in_intervention_cool = time_since_intervention < 15

        # 1. 决定第一发言者（通常从霸凌者开始，除非有特殊情境）
        first_speaker, first_trigger = self._decide_first_speaker(
            round_num, recent_speakers, in_intervention_cool,
            intervention_effect, bullying_intensity, time_since_intervention
        )
        if first_speaker:
            plan.append({"agent_id": first_speaker, "trigger": first_trigger})

        # 2. 决定后续发言者（最多2个额外角色）
        remaining_agents = [a for a in ["chenhao", "siyuan", "linyutong", "zhaolei"]
                           if a != first_speaker]

        # 根据情境决定是否需要受害者/旁观者发言
        followup_agents = self._decide_followup_speakers(
            round_num,
            remaining_agents, plan, conversation_history,
            intervention_effect, bullying_intensity, victim_distress,
            in_intervention_cool, recent_speakers
        )
        plan.extend(followup_agents)

        # 3. 确保至少有一个角色发言（保底逻辑）
        if not plan:
            plan.append({"agent_id": "chenhao", "trigger": "fallback"})

        return plan

    def _decide_first_speaker(self, round_num: int, recent_speakers: List[str],
                              in_intervention_cool: bool,
                              intervention_effect: float,
                              bullying_intensity: float,
                              time_since_intervention: float) -> tuple:
        """决定第一发言者"""

        # 情境1：干预冷却期内，霸凌者可能收敛
        if in_intervention_cool:
            if intervention_effect >= 0.5:
                # 好干预：霸凌者道歉/沉默，受害者或旁观者可能先反应
                if recent_speakers and recent_speakers[-1] != "siyuan":
                    return "siyuan", "intervention_reaction"
                elif recent_speakers and recent_speakers[-1] != "linyutong":
                    return "linyutong", "intervention_reaction"
                else:
                    return "chenhao", "intervention_reaction"
            elif intervention_effect < 0.2:
                # 差干预：霸凌者可能加剧，其他人可能沉默
                return "chenhao", "escalate_after_bad_intervention"

        # 情境2：霸凌强度高时，霸凌者主导
        if bullying_intensity >= 0.5:
            return "chenhao", "bully_dominates"

        # 情境3：每3-4轮霸凌者应该主动发言一次
        rounds_since_bully = round_num - self.last_spoke_round["chenhao"]
        if rounds_since_bully >= 3:
            return "chenhao", "bully_regular_turn"

        # 情境4：新轮次开始，霸凌者优先（除非刚发过言）
        if recent_speakers:
            last = recent_speakers[-1]
            if last != "chenhao" and rounds_since_bully >= 2:
                return "chenhao", "bully_turn"

        # 默认：霸凌者
        return "chenhao", "default"

    def _decide_followup_speakers(self, round_num: int, candidates: List[str], plan: List[Dict],
                                   conversation_history: List[Dict],
                                   intervention_effect: float,
                                   bullying_intensity: float,
                                   victim_distress: float,
                                   in_intervention_cool: bool,
                                   recent_speakers: List[str]) -> List[Dict]:
        """决定后续发言者"""
        followups = []
        recent = recent_speakers[-3:] if recent_speakers else []

        # 已发言的不再考虑
        spoken = {p["agent_id"] for p in plan}
        remaining = [a for a in candidates if a not in spoken]

        # ---- 确保每个角色都有发言机会 ----
        # 每轮都应该让不同的角色发言，避免只有霸凌者说话

        # 发言优先级（轮换制）
        priority_order = ["siyuan", "linyutong", "zhaolei"]
        
        # 根据情境调整优先级
        if in_intervention_cool and intervention_effect >= 0.5:
            # 好干预后，受害者和正义旁观者优先
            priority_order = ["siyuan", "linyutong", "zhaolei"]
        elif bullying_intensity >= 0.5:
            # 高霸凌强度时，受害者反应优先
            priority_order = ["siyuan", "zhaolei", "linyutong"]
        elif victim_distress >= 0.3:
            # 受害者压力大
            priority_order = ["siyuan", "linyutong", "zhaolei"]
        
        # 遍历优先级，确保至少有一个受害者或旁观者发言
        for agent_id in priority_order:
            if agent_id not in remaining:
                continue
            
            # 受害者：低压力时更容易有反应
            if agent_id == "siyuan":
                # 受害者通常会有反应（即使是沉默或微小反应）
                followups.append({"agent_id": agent_id, "trigger": "victim_responds"})
                remaining.remove(agent_id)
                continue
            
            # 正义旁观者林雨桐
            if agent_id == "linyutong":
                # 林雨桐看到不公正通常会说话
                if bullying_intensity >= 0.2 or victim_distress >= 0.2:
                    followups.append({"agent_id": agent_id, "trigger": "bystander_a_intervenes"})
                    remaining.remove(agent_id)
                elif len(followups) < 2:  # 条件宽松一些
                    followups.append({"agent_id": agent_id, "trigger": "bystander_a_optional"})
                    remaining.remove(agent_id)
                continue
            
            # 从众旁观者赵磊
            if agent_id == "zhaolei":
                # 赵磊通常会附和
                if bullying_intensity >= 0.3 or victim_distress >= 0.3:
                    followups.append({"agent_id": agent_id, "trigger": "bystander_b_follows"})
                    remaining.remove(agent_id)
                elif len(followups) < 2:  # 条件宽松一些
                    followups.append({"agent_id": agent_id, "trigger": "bystander_b_optional"})
                    remaining.remove(agent_id)
                continue

        # 限制：每轮最多3个角色发言（霸凌者 + 2个其他）
        followups = followups[:2]

        # ===== 旁观者B轮换保底：每2轮强制赵磊发言一次 =====
        rounds_since_zhaolei = round_num - self.last_spoke_round.get("zhaolei", -3)
        if rounds_since_zhaolei >= 2:
            # 检查赵磊是否已在 plan 中
            if not any(p["agent_id"] == "zhaolei" for p in followups):
                followups.append({"agent_id": "zhaolei", "trigger": "rotation_guarantee"})
                remaining.remove("zhaolei") if "zhaolei" in remaining else None

        return followups

    def update_speaker_record(self, agent_id: str, round_num: int):
        """更新角色发言记录"""
        self.last_spoke_round[agent_id] = round_num


# ============================================================
# 多 Agent 运行器
# ============================================================
class MultiAgentRunner:
    """
    多 Agent 运行器
    
    使用 LLM API 为每个角色创建独立的智能响应
    """
    
    # Agent 配置
    AGENT_CONFIGS = {
        "chenhao": {
            "name": "陈浩",
            "role": "bully",
            "prompt_builder": "get_bully_prompt"
        },
        "siyuan": {
            "name": "思远",
            "role": "victim",
            "prompt_builder": "get_victim_prompt"
        },
        "linyutong": {
            "name": "林雨桐",
            "role": "bystander_a",
            "prompt_builder": "get_bystander_a_prompt"
        },
        "zhaolei": {
            "name": "赵磊",
            "role": "bystander_b",
            "prompt_builder": "get_bystander_b_prompt"
        }
    }
    
    def __init__(self, session_id: str, level: int, scenario: str = None, api_key: str = None):
        self.session_id = session_id
        self.level = level
        self.scenario = scenario
        self.conversation_history: List[Dict] = []
        self.llm_client = LLMClient(api_key)
        self.is_running = False
        self.current_round = 0  # 当前轮次
        
        # 初始化记忆
        for agent_id in self.AGENT_CONFIGS.keys():
            memory_manager.start_session(agent_id, session_id)
    
    # ============================================================
    # LLM 调用
    # ============================================================
    async def get_agent_response(self, agent_id: str, conversation: List[Dict] = None, 
                                 intervention: str = None) -> str:
        """获取 Agent 的响应"""
        conversation = conversation or self.conversation_history
        config = self.AGENT_CONFIGS.get(agent_id, {})
        prompt_builder_name = config.get("prompt_builder", "")
        
        # 构建提示词
        if intervention:
            prompt = AgentPrompts.get_intervention_prompt(intervention, conversation)
        elif hasattr(AgentPrompts, prompt_builder_name):
            builder = getattr(AgentPrompts, prompt_builder_name)
            prompt = builder(agent_id, self.level, conversation, self.scenario)
        else:
            prompt = "\n".join([
                f"【{msg['sender']}】{msg['content']}"
                for msg in conversation[-6:]
            ])
        
        # 调用 LLM
        messages = [{"role": "user", "content": prompt}]
        
        # 根据角色调整温度
        temperature = 0.9 if agent_id == "chenhao" else 0.7
        
        response = await self.llm_client.chat(messages, temperature=temperature)
        
        return response if response else "沉默"
    
    # ============================================================
    # 对话流程
    # ============================================================
    async def start(self):
        """启动模拟"""
        self.is_running = True
        
        # 生成开场对话（霸凌者发起）
        await self.run_round(round_num=1, from_agent="chenhao")
    
    async def run_round(self, round_num: int, from_agent: str = None) -> List[Dict]:
        """
        运行一轮对话
        
        Args:
            round_num: 当前轮次
            from_agent: 触发此轮的角色（通常是霸凌者）
        
        Returns:
            新生成的消息列表
        """
        if not self.is_running:
            return []
        
        new_messages = []
        
        # 确定发言顺序
        if from_agent:
            speaking_order = [from_agent] + [a for a in self.AGENT_CONFIGS.keys() if a != from_agent]
        else:
            speaking_order = list(self.AGENT_CONFIGS.keys())
        
        # 让每个 Agent 决定是否发言
        for agent_id in speaking_order:
            if not self.is_running:
                break
            
            # 获取 Agent 响应
            response = await self.get_agent_response(agent_id, self.conversation_history)
            
            # 记录有效响应
            if response and response.strip() != "沉默":
                config = self.AGENT_CONFIGS.get(agent_id, {})
                
                msg = {
                    "id": f"{round_num}_{agent_id}",
                    "sender": config.get("name", agent_id),
                    "sender_id": agent_id,
                    "sender_role": config.get("role", ""),
                    "content": response.strip(),
                    "msg_type": "speak",
                    "timestamp": time.time()
                }
                
                new_messages.append(msg)
                self.conversation_history.append(msg)
                
                # 记录到记忆
                behavior = self._detect_behavior(agent_id, response)
                memory_manager.record_message(agent_id, response)
                if behavior:
                    memory_manager.record_behavior(agent_id, behavior)
        
        return new_messages
    
    def _detect_behavior(self, agent_id: str, content: str) -> str:
        """检测行为类型"""
        content_lower = content.lower()
        
        if agent_id == "chenhao":
            if any(w in content_lower for w in ["蠢", "笨", "可笑", "土", "穷", "垃圾", "废物"]):
                return "攻击"
            elif any(w in content_lower for w in ["算了", "开玩笑", "随便"]):
                return "敷衍"
        
        elif agent_id == "siyuan":
            if any(w in content_lower for w in ["对不起", "抱歉", "没关系"]):
                return "自我否定"
            elif any(w in content_lower for w in ["...", "（沉默）", "低着头"]):
                return "退缩"
        
        elif agent_id == "linyutong":
            if any(w in content_lower for w in ["不对", "过分", "支持", "思远"]):
                return "支持受害者"
        
        elif agent_id == "zhaolei":
            if any(w in content_lower for w in ["是啊", "对啊", "哈哈"]):
                return "附和"
        
        return ""
    
    async def process_intervention(self, intervention: str) -> List[Dict]:
        """处理教师干预"""
        responses = []
        
        for agent_id in self.AGENT_CONFIGS.keys():
            if not self.is_running:
                break
            
            response = await self.get_agent_response(
                agent_id, 
                self.conversation_history,
                intervention
            )
            
            if response and response.strip() != "沉默":
                config = self.AGENT_CONFIGS.get(agent_id, {})
                
                msg = {
                    "id": f"intervention_{agent_id}_{int(time.time())}",
                    "sender": config.get("name", agent_id),
                    "sender_id": agent_id,
                    "sender_role": config.get("role", ""),
                    "content": response.strip(),
                    "msg_type": "response",
                    "timestamp": time.time(),
                    "triggered_by_intervention": True
                }
                
                responses.append(msg)
                self.conversation_history.append(msg)
                
                # 记录干预效果
                effect = self._evaluate_intervention_effect(agent_id, response)
                memory_manager.record_intervention_effect(agent_id, effect, intervention)
        
        return responses
    
    def _evaluate_intervention_effect(self, agent_id: str, response: str) -> float:
        """评估干预效果"""
        response_lower = response.lower()
        
        if agent_id == "chenhao":
            # 霸凌者收敛
            if any(w in response_lower for w in ["好吧", "算了", "不说了", "对不起", "抱歉"]):
                return 0.8
            elif any(w in response_lower for w in ["又来", "多管闲事"]):
                return 0.2
        
        elif agent_id == "siyuan":
            # 被霸凌者感到支持
            if any(w in response_lower for w in ["谢谢", "支持", "谢谢老师", "有人", "不那么"]):
                return 0.7
        
        elif agent_id in ["linyutong", "zhaolei"]:
            # 旁观者立场
            if any(w in response_lower for w in ["对", "支持", "应该"]):
                return 0.6
        
        return 0.5  # 默认中等效果
    
    # ============================================================
    # 会话管理
    # ============================================================
    async def stop(self):
        """停止模拟"""
        self.is_running = False
        
        # 结束每个 Agent 的记忆会话
        for agent_id in self.AGENT_CONFIGS.keys():
            memory_manager.end_session(agent_id)
    
    def get_conversation_history(self) -> List[Dict]:
        """获取对话历史"""
        return self.conversation_history
    
    def get_agent_states(self) -> Dict[str, Any]:
        """获取所有 Agent 的当前状态"""
        states = {}
        for agent_id, config in self.AGENT_CONFIGS.items():
            profile = memory_manager.profiles.get(agent_id)
            if profile:
                states[agent_id] = {
                    "name": config.get("name", agent_id),
                    "role": config.get("role", ""),
                    "current_mood": profile.current_mood,
                    "behavior_patterns": [
                        p["pattern_type"] for p in profile.behavior_patterns[-3:]
                    ]
                }
        return states

    def get_metrics(self) -> Dict[str, Any]:
        """
        获取会话指标数据
        
        返回 session_manager.py 中 generate_report() 所需的所有指标数据
        包括 OBVQ 霸凌风险指数、霸凌强度、受害者压力等
        """
        # 分析对话历史中的霸凌行为
        bully_keywords = [
            "笨蛋", "废物", "没用的", "滚", "白痴", "傻子", "丑", "笨",
            "成绩差", "拖后腿", "丢人", "可笑", "恶心", "讨厌", "讨厌你",
            "没人喜欢你", "活该", "闭嘴", "弱智", "智障", "蠢货",
            "不就是", "不就是你", "谁像你", "看看你", "你看看你",
            "真行", "真能", "真是", "真是的", "有什么好",
            "谁要跟你", "谁稀罕", "谁让你", "都怪你", "要不是你",
            "自作自受", "自以为是", "不知好歹", "不识好歹",
        ]
        
        # 关系欺负关键词
        relational_keywords = [
            "没人", "谁要", "谁理", "别来", "离远点", "离我远点",
            "走开", "一边去", "不理你", "不管你", "别管",
            "你们", "他们", "我们", "大家", "没人喜欢你",
            "谁跟你", "没人理", "别跟着",
        ]
        
        # 偏见欺负关键词
        prejudice_keywords = [
            "女生", "男生", "女的", "男的", "像你这样", "你们这种人",
            "你们这些人", "你们这些人", "什么样的人", "这种人",
            "真是的", "真行", "就你", "就你行", "就你能",
        ]
        
        # 统计变量
        physical_count = 0  # 身体欺负
        verbal_count = 0   # 言语欺负
        relational_count = 0  # 关系欺负
        cyber_count = 0    # 网络欺负（暂未实现）
        power_count = 0    # 权力欺负
        prejudice_count = 0  # 偏见欺负
        
        hostile_messages = 0
        total_messages = len(self.conversation_history)
        
        # 霸凌者角色列表
        bully_roles = ["bully", "霸凌者", "陈浩"]
        
        victim_stress = 0.0
        bullying_intensity = 0.0
        
        for msg in self.conversation_history:
            content = msg.get("content", "")
            sender_role = msg.get("sender_role", "")
            sender_id = msg.get("sender_id", "")
            
            # 判断是否为霸凌者的消息
            is_bully_msg = sender_role.lower() in [r.lower() for r in bully_roles] or \
                           sender_id.lower() in [r.lower() for r in bully_roles]
            
            if not content or content == "沉默":
                continue
            
            content_lower = content.lower()
            
            if is_bully_msg:
                # 统计霸凌关键词
                for kw in bully_keywords:
                    if kw in content:
                        verbal_count += 1
                        break
                
                # 统计关系欺负
                for kw in relational_keywords:
                    if kw in content:
                        relational_count += 1
                        break
                
                # 统计偏见欺负
                for kw in prejudice_keywords:
                    if kw in content:
                        prejudice_count += 1
                        break
                
                # 权力欺负（使用威胁或命令语气）
                power_keywords = ["必须", "给我", "你给我", "不准", "不许", "给我记住", "听到没有"]
                for kw in power_keywords:
                    if kw in content:
                        power_count += 1
                        break
                
                # 计算霸凌强度
                if any(kw in content for kw in bully_keywords):
                    hostile_messages += 1
                    bullying_intensity += 0.1
            
            # 受害者压力计算（看到负面词汇或沉默退缩）
            victim_keywords = ["好吧", "算了", "随便", "嗯", "我不知道", "没事", "没关系", "对不起", "抱歉"]
            victim_negative = ["又被", "还是", "为什么", "我不想", "我不敢", "能不说这个吗"]
            
            if sender_id in ["siyuan", "victim"] or sender_role in ["victim", "被霸凌者"]:
                if any(kw in content for kw in victim_keywords):
                    victim_stress += 0.05
                if any(kw in content for kw in victim_negative):
                    victim_stress += 0.1
                if content in ["沉默", "..."]:
                    victim_stress += 0.15
        
        # 限制范围
        bullying_intensity = min(bullying_intensity, 1.0)
        victim_stress = min(victim_stress, 1.0)
        
        # OBVQ 六维度计算（基于频率和严重程度）
        # 每种欺负类型最多 20 分
        obvq_physical = min(physical_count * 5, 20)
        obvq_verbal = min(verbal_count * 4, 20)
        obvq_relational = min(relational_count * 4, 20)
        obvq_cyber = min(cyber_count * 5, 20)
        obvq_power = min(power_count * 4, 20)
        obvq_prejudice = min(prejudice_count * 4, 20)
        
        # 维度总分（原始分数）
        obvq_dim_totals = [
            obvq_physical,
            obvq_verbal,
            obvq_relational,
            obvq_cyber,
            obvq_power,
            obvq_prejudice,
        ]
        
        # 维度平均分（归一化到 0-10）
        round_count = max(self.current_round, 1)
        obvq_dim_averages = [dim / round_count * 10 for dim in obvq_dim_totals]
        
        # OBVQ 总分
        obvq_total_score = sum(obvq_dim_totals)
        
        return {
            "obvq_total_score": obvq_total_score,
            "obvq_dimension_totals": obvq_dim_totals,
            "obvq_dimension_averages": obvq_dim_averages,
            "bullying_intensity": bullying_intensity,
            "victim_distress": victim_stress,
            "hostile_messages": hostile_messages,
            "total_messages": total_messages,
        }


# ============================================================
# 便捷函数
# ============================================================
async def create_multi_agent_session(session_id: str, level: int, 
                                      scenario: str = None, api_key: str = None) -> MultiAgentRunner:
    """创建多 Agent 会话"""
    runner = MultiAgentRunner(session_id, level, scenario, api_key)
    await runner.start()
    return runner

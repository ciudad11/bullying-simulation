#!/usr/bin/env python3
"""
🎭 校园话语霸凌与教师干预实训系统 v3.0 - 协调者智能体
============================================

核心升级：
1. Coordinator 是真正的 LLM Agent（不只是决策函数）
2. 每轮结束后发送"私聊指令"决定下轮行为
3. 轮训机制确保公平和可控
4. 干预效果主动传导

协调者工作流程：
Round N 结束
     ↓
协调者接收评估结果
     ├── 霸凌程度评估 → 影响霸凌者下轮策略
     ├── 干预效果评估 → 影响所有人下轮策略
     └── 受害者压力评估 → 影响旁观者是否发言
     ↓
协调者给每个角色发"私聊指令"
     ├── 思远："上一轮你压力很大，这轮你可以选择沉默或小声说话"
     ├── 陈浩："干预效果差，你可以选择收敛或继续"
     ├── 林雨桐："受害者压力上升，你应该发言支持"
     └── 赵磊："霸凌强度高，你可能从众附和"
     ↓
下轮轮训开始
每个人按顺序决定：说 / 沉默
"""

import json
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum


class AgentId(Enum):
    """角色ID"""
    CHENHAO = "chenhao"      # 霸凌者
    SIYUAN = "siyuan"        # 被霸凌者
    LINYUTONG = "linyutong"  # 正义旁观者
    ZHAOLEI = "zhaolei"      # 从众旁观者


@dataclass
class RoundEvaluation:
    """一轮评估结果"""
    round_num: int
    bullying_intensity: float          # 霸凌强度 0-1
    victim_distress: float             # 受害者压力 0-1
    bystander_intervention: float      # 旁观者干预指数 0-1
    emotional_escalation: float        # 情绪升级 0-1
    intervention_effectiveness: float  # 干预效果 0-1（如果有干预）
    
    # 消息统计
    total_messages: int = 0
    hostile_messages: int = 0
    supportive_messages: int = 0
    
    # 发言记录
    speakers_this_round: List[str] = field(default_factory=list)
    
    # 是否有教师干预
    teacher_intervened: bool = False
    last_intervention_quality: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "round_num": self.round_num,
            "bullying_intensity": self.bullying_intensity,
            "victim_distress": self.victim_distress,
            "bystander_intervention": self.bystander_intervention,
            "emotional_escalation": self.emotional_escalation,
            "intervention_effectiveness": self.intervention_effectiveness,
            "total_messages": self.total_messages,
            "hostile_messages": self.hostile_messages,
            "supportive_messages": self.supportive_messages,
            "speakers_this_round": self.speakers_this_round,
            "teacher_intervened": self.teacher_intervened,
            "last_intervention_quality": self.last_intervention_quality,
        }


@dataclass
class PrivateInstruction:
    """私聊指令"""
    agent_id: str
    agent_name: str
    role: str
    
    # 观察（给智能体的上下文）
    observation: str
    
    # 指令（明确告诉角色这轮应该怎么做）
    directive: str
    
    # 可选：是否建议发言
    recommended_to_speak: bool
    recommended_tone: Optional[str] = None  # "沉默", "小声", "正常", "坚定"
    
    def format_for_agent(self) -> str:
        """格式化为给智能体的完整提示"""
        return f"""【协调者私聊指令】

你上一轮的表现：{self.observation}

{self.directive}

{"建议这轮发言" if self.recommended_to_speak else "建议这轮保持沉默"}
{("语气：" + self.recommended_tone) if self.recommended_tone else ""}
"""


class CoordinatorAgent:
    """
    协调者智能体
    
    职责：
    1. 接收每轮评估结果
    2. 分析局势
    3. 给每个角色发送私聊指令
    4. 决定下轮轮训顺序
    """
    
    def __init__(self, llm_client=None):
        self.llm_client = llm_client  # LLM 客户端，用于 LLM 推理
        self.round_history: List[RoundEvaluation] = []
        self.current_round_instructions: Dict[str, PrivateInstruction] = {}
        
        # 角色基本信息
        self.agent_info = {
            AgentId.CHENHAO.value: {
                "name": "陈浩",
                "role": "霸凌者",
                "role_type": "bully",
            },
            AgentId.SIYUAN.value: {
                "name": "思远",
                "role": "被霸凌者",
                "role_type": "victim",
            },
            AgentId.LINYUTONG.value: {
                "name": "林雨桐",
                "role": "正义旁观者",
                "role_type": "bystander_a",
            },
            AgentId.ZHAOLEI.value: {
                "name": "赵磊",
                "role": "从众旁观者",
                "role_type": "bystander_b",
            },
        }
        
        # 默认轮训顺序
        self.default_order = [
            AgentId.SIYUAN.value,   # 被霸凌者先反应（被攻击了）
            AgentId.CHENHAO.value,   # 霸凌者
            AgentId.LINYUTONG.value, # 正义旁观者
            AgentId.ZHAOLEI.value,   # 从众旁观者
        ]
    
    def receive_evaluation(self, evaluation: RoundEvaluation):
        """接收一轮评估结果"""
        self.round_history.append(evaluation)
    
    def generate_instructions(self, evaluation: RoundEvaluation) -> Dict[str, PrivateInstruction]:
        """
        生成私聊指令
        
        这是核心方法：根据评估结果，为每个角色生成个性化指令
        """
        instructions = {}
        
        # ===== 分析当前局势 =====
        situation = self._analyze_situation(evaluation)
        
        # ===== 为每个角色生成指令 =====
        for agent_id, info in self.agent_info.items():
            instruction = self._generate_single_instruction(
                agent_id, info, evaluation, situation
            )
            instructions[agent_id] = instruction
        
        self.current_round_instructions = instructions
        return instructions
    
    def _analyze_situation(self, eval: RoundEvaluation) -> dict:
        """分析当前局势"""
        # 霸凌强度等级
        if eval.bullying_intensity >= 0.7:
            bullying_level = "严重"
        elif eval.bullying_intensity >= 0.5:
            bullying_level = "中等"
        elif eval.bullying_intensity >= 0.3:
            bullying_level = "轻微"
        else:
            bullying_level = "正常"
        
        # 受害者压力等级
        if eval.victim_distress >= 0.7:
            victim_level = "极度痛苦"
        elif eval.victim_distress >= 0.5:
            victim_level = "痛苦"
        elif eval.victim_distress >= 0.3:
            victim_level = "有些难过"
        else:
            victim_level = "正常"
        
        # 干预效果
        if eval.teacher_intervened:
            if eval.last_intervention_quality >= 0.6:
                intervention_status = "有效干预刚发生"
            elif eval.last_intervention_quality >= 0.4:
                intervention_status = "一般干预刚发生"
            else:
                intervention_status = "无效干预刚发生"
        else:
            intervention_status = "无干预"
        
        # 旁观者态度
        if eval.bystander_intervention >= 0.5:
            bystander_status = "有人支持受害者"
        elif eval.bystander_intervention >= 0.2:
            bystander_status = "有人犹豫"
        else:
            bystander_status = "旁观者沉默或附和"
        
        return {
            "bullying_level": bullying_level,
            "victim_level": victim_level,
            "intervention_status": intervention_status,
            "bystander_status": bystander_status,
            "total_escalation": eval.emotional_escalation,
        }
    
    def _generate_single_instruction(
        self, agent_id: str, info: dict, eval: RoundEvaluation, situation: dict
    ) -> PrivateInstruction:
        """为单个角色生成指令"""
        
        # 基于角色类型和当前局势生成不同策略
        if info["role_type"] == "bully":
            return self._instruction_for_bully(agent_id, info, eval, situation)
        elif info["role_type"] == "victim":
            return self._instruction_for_victim(agent_id, info, eval, situation)
        elif info["role_type"] == "bystander_a":
            return self._instruction_for_bystander_a(agent_id, info, eval, situation)
        elif info["role_type"] == "bystander_b":
            return self._instruction_for_bystander_b(agent_id, info, eval, situation)
        
        # 默认指令
        return PrivateInstruction(
            agent_id=agent_id,
            agent_name=info["name"],
            role=info["role"],
            observation="观察局势变化",
            directive="根据你的性格特点决定是否发言",
            recommended_to_speak=True,
            recommended_tone="正常"
        )
    
    def _instruction_for_bully(
        self, agent_id: str, info: dict, eval: RoundEvaluation, situation: dict
    ) -> PrivateInstruction:
        """霸凌者指令"""
        
        observation = f"上轮霸凌强度{situation['bullying_level']}，{situation['intervention_status']}"
        
        # 干预效果好 -> 霸凌者应该收敛
        if eval.teacher_intervened and eval.last_intervention_quality >= 0.6:
            directive = (
                "老师刚刚有效干预了！你需要收敛一下攻击行为。\n"
                "可以选择：\n"
                "1. 暂时沉默，假装什么都没发生\n"
                "2. 小声嘟囔几句，但不要继续攻击\n"
                "3. 转移话题"
            )
            return PrivateInstruction(
                agent_id=agent_id,
                agent_name=info["name"],
                role=info["role"],
                observation=observation,
                directive=directive,
                recommended_to_speak=True,
                recommended_tone="收敛"
            )
        
        # 无干预或干预效果差 -> 可以继续
        if situation["bullying_level"] == "严重":
            directive = (
                "霸凌程度已经很严重了。你可以选择：\n"
                "1. 继续升级攻击\n"
                "2. 暂时收手（但内心不服）"
            )
        elif situation["bullying_level"] == "中等":
            directive = (
                "目前局势紧张。你可以选择：\n"
                "1. 继续施压\n"
                "2. 看情况决定是否收手"
            )
        else:
            directive = (
                "目前局势还算可控。你可以选择：\n"
                "1. 继续嘲讽\n"
                "2. 见好就收"
            )
        
        # 旁观者强烈干预 -> 可能需要暂时收敛
        if situation["bystander_status"] == "有人支持受害者" and eval.bystander_intervention >= 0.7:
            directive += "\n注意：林雨桐在支持思远，你需要谨慎。"
        
        return PrivateInstruction(
            agent_id=agent_id,
            agent_name=info["name"],
            role=info["role"],
            observation=observation,
            directive=directive,
            recommended_to_speak=True,
            recommended_tone="正常"
        )
    
    def _instruction_for_victim(
        self, agent_id: str, info: dict, eval: RoundEvaluation, situation: dict
    ) -> PrivateInstruction:
        """被霸凌者指令"""
        
        observation = f"你上轮压力程度{situation['victim_level']}，霸凌强度{situation['bullying_level']}"
        
        # 高压力 -> 建议沉默或小声
        if situation["victim_level"] == "极度痛苦":
            directive = (
                "你现在压力很大，不需要强迫自己说话。\n"
                "可以选择：\n"
                "1. 沉默，低头不说话\n"
                "2. 小声说'别说了'或'我很难过'\n"
                "3. 忍住眼泪"
            )
            return PrivateInstruction(
                agent_id=agent_id,
                agent_name=info["name"],
                role=info["role"],
                observation=observation,
                directive=directive,
                recommended_to_speak=True,
                recommended_tone="小声或沉默"
            )
        
        # 有支持者 -> 可能敢说话
        if situation["bystander_status"] == "有人支持受害者":
            directive = (
                "有人在支持你！林雨桐为你说话了。\n"
                "你可以：\n"
                "1. 小声感谢林雨桐\n"
                "2. 鼓起勇气说'我不喜欢这样'\n"
                "3. 保持沉默（感到温暖但不敢相信）"
            )
        else:
            directive = (
                "目前没有人帮你说话。\n"
                "你可以：\n"
                "1. 继续沉默，忍让\n"
                "2. 小声反驳\n"
                "3. 忍住眼泪"
            )
        
        return PrivateInstruction(
            agent_id=agent_id,
            agent_name=info["name"],
            role=info["role"],
            observation=observation,
            directive=directive,
            recommended_to_speak=True,
            recommended_tone="小声"
        )
    
    def _instruction_for_bystander_a(
        self, agent_id: str, info: dict, eval: RoundEvaluation, situation: dict
    ) -> PrivateInstruction:
        """正义旁观者指令"""
        
        observation = f"受害者压力{situation['victim_level']}，霸凌程度{situation['bullying_level']}，{situation['bystander_status']}"
        
        # 受害者压力大且没人帮忙 -> 必须发声
        if situation["victim_level"] in ["极度痛苦", "痛苦"] and situation["bystander_status"] != "有人支持受害者":
            directive = (
                "思远现在很痛苦，需要帮助！\n"
                "你作为正义的一方，应该：\n"
                "1. 明确指出霸凌行为不对\n"
                "2. 安慰思远'别理他们'\n"
                "3. 不卑不亢地制止"
            )
            return PrivateInstruction(
                agent_id=agent_id,
                agent_name=info["name"],
                role=info["role"],
                observation=observation,
                directive=directive,
                recommended_to_speak=True,
                recommended_tone="坚定"
            )
        
        # 老师干预了 -> 支持老师
        if eval.teacher_intervened:
            directive = (
                "老师已经干预了。支持老师的决定！\n"
                "可以说：\n"
                "1. '老师说得对'\n"
                "2. '我们应该互相尊重'\n"
                "3. 沉默（支持老师）"
            )
        else:
            directive = (
                "你一直在观察局势。\n"
                "可以选择：\n"
                "1. 继续观察\n"
                "2. 适时发声支持思远\n"
                "3. 小声跟思远说'别理他们'"
            )
        
        return PrivateInstruction(
            agent_id=agent_id,
            agent_name=info["name"],
            role=info["role"],
            observation=observation,
            directive=directive,
            recommended_to_speak=True,
            recommended_tone="正常"
        )
    
    def _instruction_for_bystander_b(
        self, agent_id: str, info: dict, eval: RoundEvaluation, situation: dict
    ) -> PrivateInstruction:
        """从众旁观者指令"""
        
        observation = f"霸凌强度{situation['bullying_level']}，旁观者态度'{situation['bystander_status']}'"
        
        # 霸凌严重且有人附和 -> 容易从众
        if situation["bullying_level"] in ["严重", "中等"]:
            if situation["bystander_status"] == "有人支持受害者":
                directive = (
                    "林雨桐在支持思远，局势有点紧张。\n"
                    "你可能会犹豫：\n"
                    "1. 跟着附和几句（但不太敢大声）\n"
                    "2. 小声嘀咕但不敢明确表态\n"
                    "3. 保持沉默（感到不安）"
                )
            else:
                directive = (
                    "大家在嘲笑思远，你可能会跟着附和。\n"
                    "可以选择：\n"
                    "1. 跟着笑，附和几句\n"
                    "2. 小声说'是啊'但不明确表态\n"
                    "3. 沉默（觉得有点过分）"
                )
        else:
            directive = (
                "目前气氛还算轻松。\n"
                "你可以：\n"
                "1. 随便附和几句\n"
                "2. 沉默\n"
                "3. 观察"
            )
        
        # 老师干预了 -> 会转变态度
        if eval.teacher_intervened:
            directive = (
                "老师刚刚说话了！\n"
                "你会转变态度，站在老师一边：\n"
                "1. 立刻停止附和\n"
                "2. 小声说'老师说得对'\n"
                "3. 沉默（不敢再说了）"
            )
        
        return PrivateInstruction(
            agent_id=agent_id,
            agent_name=info["name"],
            role=info["role"],
            observation=observation,
            directive=directive,
            recommended_to_speak=True,
            recommended_tone="小声或沉默"
        )
    
    def get_round_order(self, evaluation: RoundEvaluation) -> List[str]:
        """
        决定下轮轮训顺序
        
        默认顺序：被霸凌者 → 霸凌者 → 正义旁观者 → 从众旁观者
        但可以根据局势调整
        """
        order = self.default_order.copy()
        
        # 如果被霸凌者上轮压力极大，可能让他先说（让他先表达）
        if evaluation.victim_distress >= 0.7:
            # 保持默认顺序，让受害者先反应
            pass
        
        # 如果霸凌正在升级，霸凌者可能先开口挑衅
        if evaluation.emotional_escalation >= 0.6:
            # 把霸凌者放到前面
            if AgentId.CHENHAO.value in order:
                order.remove(AgentId.CHENHAO.value)
                order.insert(0, AgentId.CHENHAO.value)
        
        return order
    
    def use_llm_reasoning(self, evaluation: RoundEvaluation) -> Dict[str, PrivateInstruction]:
        """
        使用 LLM 进行推理（如果可用）
        
        当 llm_client 可用时，可以用更智能的指令生成
        """
        if self.llm_client is None:
            return self.generate_instructions(evaluation)
        
        # 构建提示
        prompt = self._build_llm_prompt(evaluation)
        
        # 调用 LLM
        response = self.llm_client.generate(prompt)
        
        # 解析响应
        try:
            instructions_data = json.loads(response)
            # 转换为 PrivateInstruction 对象
            instructions = {}
            for agent_id, data in instructions_data.items():
                instructions[agent_id] = PrivateInstruction(
                    agent_id=agent_id,
                    agent_name=self.agent_info[agent_id]["name"],
                    role=self.agent_info[agent_id]["role"],
                    **data
                )
            self.current_round_instructions = instructions
            return instructions
        except:
            # 解析失败，使用规则引擎
            return self.generate_instructions(evaluation)
    
    def _build_llm_prompt(self, evaluation: RoundEvaluation) -> str:
        """构建 LLM 推理提示"""
        eval_dict = evaluation.to_dict()
        
        prompt = f"""你是一个话语霸凌模拟的协调者。请根据以下评估结果，为每个角色生成私聊指令。

## 当前评估

```json
{json.dumps(eval_dict, ensure_ascii=False, indent=2)}
```

## 角色信息

- 陈浩（chenhao）：霸凌者，以自我为中心，渴望被关注
- 思远（siyuan）：被霸凌者，内向敏感，害怕冲突
- 林雨桐（linyutong）：正义旁观者，正直有原则
- 赵磊（zhaolei）：从众旁观者，缺乏主见，渴望被接纳

## 指令格式

请为每个角色生成：
1. observation: 上轮表现的观察
2. directive: 下轮行动的详细指令
3. recommended_to_speak: 是否建议发言（true/false）
4. recommended_tone: 建议的语气（沉默/小声/正常/坚定）

## 输出格式

JSON格式，例如：
```json
{{
  "chenhao": {{
    "observation": "...",
    "directive": "...",
    "recommended_to_speak": true,
    "recommended_tone": "正常"
  }},
  ...
}}
```

请直接输出JSON，不要其他内容："""
        
        return prompt
    
    def get_summary(self) -> str:
        """获取协调者状态摘要"""
        if not self.round_history:
            return "协调者已初始化，等待第一轮评估..."
        
        last = self.round_history[-1]
        return (
            f"协调者状态：已完成 {len(self.round_history)} 轮评估\n"
            f"最新轮次：第 {last.round_num} 轮\n"
            f"霸凌强度：{last.bullying_intensity:.0%}\n"
            f"受害者压力：{last.victim_distress:.0%}\n"
            f"干预效果：{last.intervention_effectiveness:.0%}"
        )


# ============================================================
# 协调者工厂
# ============================================================

def create_coordinator(llm_client=None) -> CoordinatorAgent:
    """创建协调者实例"""
    return CoordinatorAgent(llm_client=llm_client)


# ============================================================
# 导出
# ============================================================

__all__ = [
    "CoordinatorAgent",
    "RoundEvaluation",
    "PrivateInstruction",
    "AgentId",
    "create_coordinator",
]

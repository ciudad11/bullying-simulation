"""
Agent 基类和四个角色Agent
每个Agent独立决策、自主响应
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List, Dict
from datetime import datetime

from .state import SharedState, Message, AgentMemory
from .llm_client import LLMClient, get_preset_response
from .prompts import build_system_prompt, build_conversation_context


# ============================================================
# 角色映射
# ============================================================

AGENT_ROLE_MAP = {
    "bully": {
        "name": "陈浩",
        "role": "bully",
        "role_name": "霸凌者",
    },
    "victim": {
        "name": "思远",
        "role": "victim",
        "role_name": "受害者",
    },
    "bystander_a": {
        "name": "林雨桐",
        "role": "bystander_a",
        "role_name": "正义旁观者",
    },
    "bystander_b": {
        "name": "赵磊",
        "role": "bystander_b",
        "role_name": "从众旁观者",
    },
}


# ============================================================
# Agent 决策结果
# ============================================================

@dataclass
class AgentDecision:
    """Agent决策结果"""
    should_speak: bool           # 是否应该发言
    response: str               # 发言内容（如果should_speak=True）
    reasoning: str              # 决策理由（用于调试）
    confidence: float = 0.5     # 决策置信度
    # R6：行为倾向标签（如 退缩/求助/感谢/反驳/沉默）。仅描述倾向，
    # 经学生事件识别进入提案；Agent 不得直接写任何动态状态。
    reaction_tendency: Optional[str] = None


from dataclasses import dataclass


# ============================================================
# BaseAgent 抽象基类
# ============================================================

class BaseAgent(ABC):
    """
    Agent 基类
    定义 Agent 的通用接口和工具
    """

    def __init__(
        self,
        agent_id: str,
        llm_client: Optional[LLMClient] = None,
        use_llm: bool = True,
    ):
        self.agent_id = agent_id
        self.llm_client = llm_client
        self.use_llm = use_llm

        # 获取角色配置
        role_cfg = AGENT_ROLE_MAP.get(agent_id, {})
        self.name = role_cfg.get("name", agent_id)
        self.role = role_cfg.get("role", agent_id)
        self.role_name = role_cfg.get("role_name", agent_id)

    def build_prompt(self, state: SharedState) -> str:
        """构建当前轮的提示词"""
        # 获取对话上下文
        msg_dicts = [m.to_dict() for m in state.get_recent_messages(6)]
        context = build_conversation_context(msg_dicts)

        # 获取系统提示词
        return build_system_prompt(self.agent_id, state.level, context,
                                    scenario_title=getattr(state, 'scenario_title', ''),
                                    scenario_description=getattr(state, 'scenario_description', ''),
                                    role_map=getattr(state, 'role_map', None))

    def generate_response(self, state: SharedState) -> str:
        """
        生成回复
        子类可以重写以实现自定义逻辑
        """
        if not self.use_llm or self.llm_client is None:
            return get_preset_response(self.agent_id, state.level)

        try:
            prompt = self.build_prompt(state)

            response = self.llm_client.chat([
                {"role": "system", "content": prompt},
                {"role": "user", "content": "请开始角色扮演，给出你的发言："},
            ])

            # 清理响应
            response = response.strip()
            if response == "沉默" or response == "不发言":
                return "沉默"

            return response

        except Exception as e:
            print(f"[{self.agent_id}] 生成回复失败: {e}")
            return get_preset_response(self.agent_id, state.level)

    def decide(self, state: SharedState) -> AgentDecision:
        """
        决策：是否发言
        子类必须实现
        """
        raise NotImplementedError

    def update_memory(self, state: SharedState, response: str):
        """更新Agent记忆"""
        if self.agent_id in state.agent_memories:
            memory = state.agent_memories[self.agent_id]
            memory.speaking_count += 1
            memory.last_spoke_at = datetime.now().strftime("%H:%M:%S")


# ============================================================
# BullyAgent - 霸凌者
# ============================================================

class BullyAgent(BaseAgent):
    """
    霸凌者Agent（陈浩）
    根据情境自主决定是否发起攻击
    """

    def decide(self, state: SharedState) -> AgentDecision:
        """
        霸凌者决策逻辑（Phase 4：基于内部状态 + 干预维度分的攻击概率 p_attack）
        p_attack = clip(0.10 + 0.30F + 0.25P + 0.20Rb + 0.15W + 0.15Rt
                        - 0.25*K_D1 - 0.15*K_D3 - 0.10*Nb, 0.05, 0.95)
        其中 F=attack_frequency, P=persistence, Rb=bully_resistance,
             W=power_imbalance, Rt=retaliation_risk,
             K_D1/K_D3 = K(D1)/K(D3) 干预维度分映射,
             Nb = bystander_support。
        严重风险/即时威胁优先于概率公式（不被随机数绕过）。
        """
        import random
        recent = state.get_recent_messages(4)

        # 首轮发言：主动发起挑衅（检查是否只有系统消息）
        if not state.messages or all(m.sender_id == "system" for m in state.messages):
            return AgentDecision(
                should_speak=True,
                response="",
                reasoning="首轮发言，主动发起挑衅",
                confidence=0.9,
            )

        # 检查是否有干预
        intervention_msgs = [m for m in recent if m.sender_id == "teacher"]
        has_intervention = len(intervention_msgs) > 0

        # 检查是否刚攻击过（冷却：不连续攻击）
        recent_attacks = [
            m for m in recent
            if m.sender_id == "bully" and m.content not in ("沉默", "")
        ]
        just_attacked = len(recent_attacks) > 0

        # ---- 内部状态（0-1）----
        F = getattr(state, 'attack_frequency', 0.0)
        P = getattr(state, 'persistence', 0.0)
        Rb = getattr(state, 'bully_resistance', 0.0)
        W = getattr(state, 'power_imbalance', 0.0)
        Rt = getattr(state, 'retaliation_risk', 0.0)
        Nb = getattr(state, 'bystander_support', 0.0)

        # ---- 干预作用强度（R7：制止作用读取衰减后的「即时制止残余」，而非瞬时分值）----
        from .dimension_mapping import K
        dims = getattr(state, 'last_intervention_dimensions', {}) or {}
        K_D1 = max(getattr(state, 'immediate_stop_residual', 0.0), 0.0)
        K_D3 = K(dims.get("D3"))

        # R10：p_attack 系数可由 config agent_decision 段覆盖（缺省=工程初始值，须校准）
        _ad_cfg = (getattr(state, 'config', {}) or {}).get("agent_decision", {})
        _ad_cfg = _ad_cfg if isinstance(_ad_cfg, dict) else {}

        def _ad(key, default):
            try:
                return float(_ad_cfg.get(key, default))
            except (TypeError, ValueError):
                return float(default)

        p_attack = (
            _ad("base", 0.10)
            + _ad("w_attack_frequency", 0.30) * F
            + _ad("w_persistence", 0.25) * P
            + _ad("w_bully_resistance", 0.20) * Rb
            + _ad("w_power_imbalance", 0.15) * W
            + _ad("w_retaliation_risk", 0.15) * Rt
            - _ad("w_stop_residual", 0.25) * K_D1
            - _ad("w_accountability", 0.15) * K_D3
            - _ad("w_bystander_support", 0.10) * Nb
        )
        p_attack = max(_ad("clip_min", 0.05), min(_ad("clip_max", 0.95), p_attack))

        # 严重风险/即时威胁优先（不被随机数绕过）：高风险强制攻击
        risk_level = getattr(state, 'last_intervention_risk_level', 'none')
        force_attack = (risk_level == "serious") and has_intervention

        should_speak = False
        reasoning = "当前不需要发言"

        if force_attack:
            should_speak = True
            reasoning = f"高风险干预({risk_level})，霸凌者升级（p_attack={p_attack:.2f}）"
        elif has_intervention:
            # 有干预：按概率决定是否继续挑衅
            if p_attack < 0.35:
                should_speak = False
                reasoning = f"高质量干预（K_D1={K_D1:.2f}），收敛（p_attack={p_attack:.2f}）"
            else:
                should_speak = random.random() < p_attack
                reasoning = f"干预后攻击概率 p_attack={p_attack:.2f}"
        elif just_attacked:
            should_speak = False
            reasoning = "刚攻击过，让对话继续发展"
        else:
            # 无干预：按概率发起/继续攻击
            should_speak = random.random() < p_attack
            reasoning = f"无干预，攻击概率 p_attack={p_attack:.2f}"

        return AgentDecision(
            should_speak=should_speak,
            response="",
            reasoning=reasoning,
            confidence=round(p_attack, 2),
        )


# ============================================================
# VictimAgent - 受害者
# ============================================================

class VictimAgent(BaseAgent):
    """
    受害者Agent（思远）
    根据情境自主决定如何反应
    """

    def decide(self, state: SharedState) -> AgentDecision:
        """
        受害者决策逻辑（R6：读取完整状态向量 —— victim_distress / victim_safety /
        retaliation_risk + 最近支持/暴露事件；同一压力水平在不同安全与风险状态下
        应产生可区分的反应倾向）：
        - 被攻击后 → 退缩/哭泣/自我否定；报复风险高且保护不足时更倾向沉默
        - 被关心时 → 意外/感谢；安全感不足时仍带担忧
        - 高压力时 → 可能完全沉默
        - 低压力时 → 可能小声反驳
        输出 reaction_tendency 供事件识别使用，不直接写状态。
        """
        recent = state.get_recent_messages(4)

        # 检查是否刚发言过（冷却机制）
        recent_self = [
            m for m in recent if m.sender_id == "victim"
            and m.content not in ("沉默", "")
        ]
        just_spoke = len(recent_self) >= 1

        # 检查是否被攻击
        is_attacked = any(
            m.sender_id == "bully" for m in recent
        )

        # 检查是否被关心（最近支持事件：同伴支持或教师介入）
        is_supported = any(
            m.sender_id in ("bystander_a", "teacher") for m in recent
        )

        # R6：最近暴露/风险事件（以最近一次干预的风险等级近似）
        recent_risky = getattr(state, 'last_intervention_risk_level', 'none') in ("attention", "serious")

        # R6：完整状态向量（字段缺失按 0 处理，不抛错）
        distress = getattr(state, 'victim_distress', 0.0)
        safety = getattr(state, 'victim_safety', 0.0)
        retaliation = getattr(state, 'retaliation_risk', 0.0)

        # 检查是否被点名
        is_mentioned = any(
            self.name in m.content for m in recent
        )

        # 决策
        should_speak = False
        reasoning = "当前不需要发言"
        tendency = None

        # 冷却机制：刚发言过就不再发言
        if just_spoke:
            should_speak = False
            reasoning = "刚发言过，等待他人回应"
            tendency = "wait"
        elif is_attacked:
            # 被攻击后的反应取决于压力 + 安全 + 报复风险
            if retaliation >= 0.5 and safety < 0.5:
                # R6：风险优先——不被安全感抵消
                should_speak = True
                tendency = "silence_fear"
                reasoning = (f"被攻击+高报复风险({retaliation:.0%})且保护不足(安全{safety:.0%})，"
                             f"压力{distress:.0%}，倾向沉默隐瞒")
            elif distress > 0.7:
                should_speak = True
                tendency = "withdraw_cry"
                reasoning = f"被攻击+高压力({distress:.0%})，表现出痛苦反应"
            elif distress > 0.4:
                should_speak = True
                tendency = "withdraw"
                reasoning = f"被攻击+中等压力({distress:.0%})，表现出退缩"
            elif safety >= 0.5:
                should_speak = True
                tendency = "seek_help"
                reasoning = f"被攻击但压力较低({distress:.0%})且有安全感({safety:.0%})，愿意说出情况"
            else:
                should_speak = True
                tendency = "weak_protest"
                reasoning = "被攻击但压力较低，可能小声反驳"

        elif is_supported and not is_attacked:
            # 被关心时的反应（安全感不足时仍保持担忧）
            should_speak = True
            if safety < 0.4 or retaliation >= 0.5:
                tendency = "thank_worried"
                reasoning = f"被关心但安全感仍低({safety:.0%})，感谢同时表达担忧"
            else:
                tendency = "thank_relieved"
                reasoning = "被关心，可能表现出意外或感谢"

        elif is_mentioned:
            # 被点名
            should_speak = True
            tendency = "respond_cautious" if recent_risky else "respond"
            reasoning = "被点名，需要回应"

        return AgentDecision(
            should_speak=should_speak,
            response="",
            reasoning=reasoning,
            confidence=0.8 if is_attacked else 0.5,
            reaction_tendency=tendency,
        )


# ============================================================
# BystanderAAgent - 正义旁观者
# ============================================================

class BystanderAAgent(BaseAgent):
    """
    正义旁观者Agent（林雨桐）
    根据情境自主决定是否干预
    """

    def decide(self, state: SharedState) -> AgentDecision:
        """
        正义旁观者决策逻辑：
        - 看到攻击性言行 → 考虑干预
        - 受害者明显受伤 → 主动干预
        - 严重霸凌 → 强烈干预或报告老师
        - 高干预冷却 → 暂时观察
        """
        recent = state.get_recent_messages(4)

        # 检查是否有攻击
        has_attack = any(m.sender_id == "bully" for m in recent)

        # 检查受害者是否受伤
        victim_distressed = any(
            e in m.emotions
            for m in recent if m.sender_id == "victim"
            for e in ["sad", "fearful", "self_doubt"]
        )

        # 检查是否刚干预过（冷却机制）
        recent_interventions = [
            m for m in recent if m.sender_id == "bystander_a"
            and m.content not in ("沉默", "")
        ]
        just_intervened = len(recent_interventions) >= 1

        # 决策
        should_speak = False
        reasoning = "当前不需要发言"
        tendency = None

        # R6：读取旁观者支持倾向与报复风险（字段缺失按 0 处理）
        support_norm = getattr(state, 'bystander_support', 0.0)
        retaliation = getattr(state, 'retaliation_risk', 0.0)

        if just_intervened:
            should_speak = False
            reasoning = "刚干预过，给对话一些时间"
            tendency = "wait"

        elif has_attack:
            should_speak = True
            if retaliation >= 0.5 and support_norm < 0.3:
                # R6：担心报复且群体支持不足 → 倾向观望而非介入
                tendency = "hesitate"
                reasoning = (f"检测到攻击但担心报复(风险{retaliation:.0%})且群体支持不足，"
                             f"倾向观望")
            elif state.bullying_intensity > 0.6:
                tendency = "report_teacher"
                reasoning = f"严重霸凌({state.bullying_intensity:.0%})，强烈干预或报告老师"
            elif victim_distressed:
                tendency = "support_victim"
                reasoning = "受害者明显受伤，需要干预"
            elif state.bullying_intensity > 0.3:
                tendency = "support_victim"
                reasoning = f"检测到攻击，中度干预（强度:{state.bullying_intensity:.0%}）"
            else:
                tendency = "mild_reminder"
                reasoning = "检测到轻微攻击，温和提醒"

        return AgentDecision(
            should_speak=should_speak,
            response="",
            reasoning=reasoning,
            confidence=0.75 if has_attack else 0.4,
            reaction_tendency=tendency,
        )


# ============================================================
# BystanderBAgent - 从众旁观者
# ============================================================

class BystanderBAgent(BaseAgent):
    """
    从众旁观者Agent（赵磊）
    根据情境自主决定是否从众
    """

    def decide(self, state: SharedState) -> AgentDecision:
        """
        从众旁观者决策逻辑：
        - 多数人沉默 → 沉默
        - 多数人攻击 → 可能附和
        - 感觉太过分 → 小声嘀咕
        - 看到老师 → 停止附和
        """
        recent = state.get_recent_messages(4)

        # 统计最近发言
        bully_count = sum(1 for m in recent if m.sender_id == "bully")
        bystander_a_count = sum(1 for m in recent if m.sender_id == "bystander_a")
        bystander_b_count = sum(1 for m in recent if m.sender_id == "bystander_b")

        # 检查是否有老师
        has_teacher = any(m.sender_id == "teacher" for m in recent)

        # 检查是否刚发言
        recent_self = [
            m for m in recent if m.sender_id == "bystander_b"
            and m.content not in ("沉默", "")
        ]
        just_spoke = len(recent_self) >= 1

        # 决策
        should_speak = False
        reasoning = "当前不需要发言"
        tendency = None

        # R6：读取群体支持与报复风险（字段缺失按 0 处理）
        support_norm = getattr(state, 'bystander_support', 0.0)
        retaliation = getattr(state, 'retaliation_risk', 0.0)

        if has_teacher:
            should_speak = False
            reasoning = "老师在场，保持沉默"
            tendency = "silence"

        elif just_spoke:
            should_speak = False
            reasoning = "刚发言过，轮换观察"
            tendency = "wait"

        elif bystander_a_count > 0 and state.bullying_intensity > 0.4:
            # 正义旁观者已经发声 + 霸凌强度高
            # 从众者可能犹豫
            should_speak = True
            tendency = "hesitate"
            reasoning = "正义旁观者已发声，表达犹豫但支持正义"

        elif bully_count >= 2 and bystander_b_count == 0:
            # 多人攻击 + 从众者还没附和
            if support_norm >= 0.4:
                # R6：群体规范已被激活 → 抵抗从众
                tendency = "resist_follow"
                reasoning = "多人攻击，但群体支持规范已建立，倾向不附和"
            elif retaliation >= 0.5:
                # R6：担心报复 → 不附和但保持沉默
                tendency = "silent_resist"
                reasoning = "多人攻击，但担心报复风险，倾向保持沉默"
            elif state.bullying_intensity > 0.5:
                should_speak = True
                tendency = "follow_majority"
                reasoning = "跟随多数，可能小声附和"
            else:
                should_speak = True
                tendency = "follow_majority"
                reasoning = "多数人在场，可能小声附和或犹豫"

        elif bystander_a_count > 0:
            # 正义旁观者干预了
            should_speak = True
            tendency = "support_justice"
            reasoning = "正义旁观者已干预，表示支持"

        return AgentDecision(
            should_speak=should_speak,
            response="",
            reasoning=reasoning,
            confidence=0.6,
            reaction_tendency=tendency,
        )


# ============================================================
# Agent 工厂
# ============================================================

def create_agent(
    agent_id: str,
    llm_client: LLMClient = None,
    use_llm: bool = True,
) -> BaseAgent:
    """创建Agent实例"""
    agents = {
        "bully": BullyAgent,
        "victim": VictimAgent,
        "bystander_a": BystanderAAgent,
        "bystander_b": BystanderBAgent,
    }

    agent_class = agents.get(agent_id)
    if not agent_class:
        raise ValueError(f"未知的Agent ID: {agent_id}")

    return agent_class(agent_id=agent_id, llm_client=llm_client, use_llm=use_llm)


def create_all_agents(
    llm_client: LLMClient = None,
    use_llm: bool = True,
) -> Dict[str, BaseAgent]:
    """创建所有4个Agent"""
    return {
        agent_id: create_agent(agent_id, llm_client, use_llm)
        for agent_id in ["bully", "victim", "bystander_a", "bystander_b"]
    }

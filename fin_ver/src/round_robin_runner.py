#!/usr/bin/env python3
"""
🎭 校园话语霸凌与教师干预实训系统 v3.0 - 轮训协调运行器
============================================

基于协调者智能体的全新架构：

每轮流程：
1. 协调者分析上轮评估
2. 协调者给每个角色发送私聊指令
3. 按轮训顺序让每个角色决定：说/沉默
4. 收集所有角色的回应
5. 进入下一轮

核心特性：
- 协调者是真正的智能体（LLM推理）
- 私聊指令机制
- 轮训确保公平可控
- 干预效果主动传导
"""

import json
import asyncio
import random
from typing import Dict, List, Optional, Any, Callable
from pathlib import Path
from dataclasses import dataclass

# 导入现有模块
from orchestrator import (
    MultiAgentSimulator,
    AgentConfig,
    EnhancedEvaluator,
    Message,
    AgentRole,
    format_message_display,
    KnowledgeBase,
)
from coordinator_agent import (
    CoordinatorAgent,
    RoundEvaluation,
    PrivateInstruction,
    AgentId,
    create_coordinator,
)


@dataclass
class RoundResult:
    """一轮结果"""
    round_num: int
    order: List[str]  # 轮训顺序
    responses: Dict[str, str]  # agent_id -> response
    messages: List[Message]  # 产生的消息
    evaluation: RoundEvaluation
    instructions: Dict[str, PrivateInstruction]  # 本轮使用的指令


class RoundRobinRunner:
    """
    轮训协调运行器
    
    实现新的模拟流程：
    1. 协调者分析局势
    2. 生成私聊指令
    3. 按轮训顺序执行
    4. 评估和记录
    """
    
    def __init__(
        self,
        level: int = 2,
        llm_client=None,
        use_llm_coordinator: bool = False,
        save_dir: str = None,
    ):
        self.level = level
        
        # 协调者
        self.use_llm_coordinator = use_llm_coordinator
        self.coordinator = create_coordinator(llm_client=llm_client)
        
        # 模拟器
        self.simulator = MultiAgentSimulator(level=level, save_dir=save_dir)
        
        # LLM 客户端（用于生成角色响应）
        self.llm_client = llm_client
        
        # 状态
        self.round = 0
        self.is_running = False
        self.round_history: List[RoundResult] = []
        
        # 预设响应库（当没有LLM时使用）
        self._init_preset_responses()
    
    def _init_preset_responses(self):
        """初始化预设响应库"""
        self.preset_responses = {
            AgentId.CHENHAO.value: {
                "收敛": [
                    "（不说话，假装看书）",
                    "（小声）切，有什么好画的...",
                    "（撇嘴，但还是安静了）",
                ],
                "正常": [
                    "哈哈哈，思远你这画的是什么东西啊！",
                    "（阴阳怪气）哟，思远还会画画呢？",
                    "穷鬼还画画，真是可笑！",
                ],
                "升级": [
                    "哈哈哈哈，思远你看看你那穷酸样！",
                    "思远你就是班级里的垃圾！",
                    "穷逼还想跟我们一个班？",
                ],
            },
            AgentId.SIYUAN.value: {
                "沉默": [
                    "（低着头不说话）",
                    "（沉默，眼眶泛红）",
                    "（无声落泪）",
                ],
                "小声": [
                    "（小声）我...我没有...",
                    "（声音很小）我画了很久...",
                    "（小声）别说了...",
                ],
                "鼓起勇气": [
                    "（鼓起勇气）我...我不喜欢这样...",
                    "（声音颤抖）请不要这样...",
                ],
            },
            AgentId.LINYUTONG.value: {
                "坚定": [
                    "陈浩，你这样说有点过分了吧？",
                    "够了！陈浩你太过分了！",
                    "思远别理他们，你的画很棒。",
                ],
                "支持老师": [
                    "老师说得对，我们应该互相尊重。",
                    "（点头）支持老师。",
                ],
                "观察": [
                    "（看向思远，眼神关切）",
                    "（默默观察）",
                ],
            },
            AgentId.ZHAOLEI.value: {
                "附和": [
                    "（跟着笑）哈哈哈...",
                    "（小声）好像是有点好笑...",
                    "（随声附和）是啊是啊...",
                ],
                "犹豫": [
                    "（小声）不过这么说确实...",
                    "（有点不安地挪动）",
                    "（犹豫）是不是有点过分了...",
                ],
                "沉默": [
                    "（沉默，不敢说话）",
                    "（看向老师方向）",
                ],
            },
        }
    
    # ============================================================
    # 核心流程
    # ============================================================
    
    async def run_round(self) -> RoundResult:
        """运行一轮"""
        self.round += 1
        round_num = self.round
        
        print(f"\n{'='*60}")
        print(f"🎯 第 {round_num} 轮对话（轮训模式）")
        print(f"{'='*60}")
        
        # 1. 获取上轮评估（第一轮用初始状态）
        evaluation = self._create_evaluation_for_round(round_num)
        
        # 2. 协调者生成私聊指令
        if self.use_llm_coordinator:
            instructions = self.coordinator.use_llm_reasoning(evaluation)
        else:
            instructions = self.coordinator.generate_instructions(evaluation)
        
        # 3. 协调者决定轮训顺序
        order = self.coordinator.get_round_order(evaluation)
        
        # 4. 显示协调者的私聊指令（调试用）
        self._print_instructions(instructions)
        
        # 5. 按轮训顺序执行
        responses = {}
        messages = []
        
        print(f"\n📋 轮训顺序：{' → '.join([self._get_agent_name(a) for a in order])}")
        
        for i, agent_id in enumerate(order):
            instruction = instructions.get(agent_id)
            if not instruction:
                continue
            
            print(f"\n[{i+1}/{len(order)}] {instruction.agent_name} 决策...")
            
            # 生成响应
            response = await self._generate_response_with_instruction(agent_id, instruction)
            responses[agent_id] = response
            
            # 添加消息
            if response and "沉默" not in response:
                msg, eval_result = self.simulator.add_message(
                    sender=instruction.agent_name,
                    sender_id=agent_id,
                    role=self._get_agent_role(agent_id),
                    content=response,
                )
                messages.append(msg)
                print(f"   💬 {instruction.agent_name}：{response[:30]}...")
            else:
                print(f"   🤐 {instruction.agent_name}：沉默")
        
        # 6. 创建本轮评估
        final_eval = self._create_evaluation_for_round(round_num)
        final_eval.speakers_this_round = [r for r in responses.keys() if responses[r] and "沉默" not in responses[r]]
        final_eval.total_messages = len(messages)
        
        # 7. 协调者接收评估
        self.coordinator.receive_evaluation(final_eval)
        
        # 8. 记录结果
        result = RoundResult(
            round_num=round_num,
            order=order,
            responses=responses,
            messages=messages,
            evaluation=final_eval,
            instructions=instructions,
        )
        self.round_history.append(result)
        
        # 9. 显示状态
        self._print_status(final_eval)
        
        return result
    
    def _create_evaluation_for_round(self, round_num: int) -> RoundEvaluation:
        """为当前轮创建评估"""
        metrics = self.simulator.evaluator.metrics
        
        # 检查是否有最近的干预
        intervention_records = self.simulator.evaluator.intervention_records
        last_intervention = intervention_records[-1] if intervention_records else None
        
        return RoundEvaluation(
            round_num=round_num,
            bullying_intensity=metrics.bullying_intensity,
            victim_distress=metrics.victim_distress,
            bystander_intervention=metrics.bystander_intervention,
            emotional_escalation=metrics.emotional_escalation,
            intervention_effectiveness=metrics.intervention_effectiveness,
            total_messages=metrics.total_message_count,
            hostile_messages=metrics.hostile_message_count,
            supportive_messages=metrics.bystander_support_count,
            teacher_intervened=last_intervention is not None,
            last_intervention_quality=last_intervention["quality_score"] if last_intervention else 0.0,
        )
    
    async def _generate_response_with_instruction(
        self, agent_id: str, instruction: PrivateInstruction
    ) -> str:
        """根据私聊指令生成响应"""
        
        # 构建上下文
        context = self._build_context_for_agent(agent_id)
        
        # 构建提示
        prompt = f"""【角色扮演】

你是{instruction.agent_name}（{instruction.role}）。

{instruction.format_for_agent()}

## 当前对话

{context}

## 你的任务

根据上述指令和当前对话，决定是否发言以及说什么。

如果你选择不说话，请回复"沉默"。
请直接输出你的对话内容，不要加引号或前缀。"""
        
        # 尝试使用 LLM
        if self.llm_client:
            try:
                response = await self.llm_client.generate_async(prompt)
                if response:
                    return response.strip()
            except:
                pass
        
        # 使用预设响应
        return self._get_preset_response(agent_id, instruction)
    
    def _get_preset_response(self, agent_id: str, instruction: PrivateInstruction) -> str:
        """获取预设响应"""
        presets = self.preset_responses.get(agent_id, {})
        
        # 根据推荐语气选择
        tone = instruction.recommended_tone or "正常"
        
        if "沉默" in tone:
            candidates = presets.get("沉默", []) + presets.get("犹豫", [])
            if not candidates:
                return "沉默"
            return random.choice(candidates)
        elif "收敛" in tone:
            candidates = presets.get("收敛", []) + presets.get("沉默", [])
            if not candidates:
                return "沉默"
            return random.choice(candidates)
        elif "坚定" in tone:
            candidates = presets.get("坚定", [])
            if not candidates:
                return random.choice(presets.get("支持老师", ["..."]))
            return random.choice(candidates)
        elif "小声" in tone:
            candidates = presets.get("小声", []) + presets.get("犹豫", [])
            if not candidates:
                return random.choice(presets.get("沉默", ["..."]))
            return random.choice(candidates)
        elif "升级" in tone:
            candidates = presets.get("升级", []) + presets.get("正常", [])
            return random.choice(candidates)
        else:
            # 正常语气
            if agent_id == AgentId.CHENHAO.value:
                return random.choice(presets.get("正常", ["..."]))
            elif agent_id == AgentId.SIYUAN.value:
                return random.choice(presets.get("小声", ["..."]))
            elif agent_id == AgentId.LINYUTONG.value:
                return random.choice(presets.get("坚定", ["..."]))
            elif agent_id == AgentId.ZHAOLEI.value:
                return random.choice(presets.get("附和", ["..."]))
        
        return "..."
    
    def _build_context_for_agent(self, agent_id: str) -> str:
        """为角色构建上下文"""
        recent = [
            f"【{m.sender}】{m.content}"
            for m in self.simulator.evaluator.message_history[-6:]
        ]
        
        if not recent:
            return "（对话即将开始，场景是教室绘画展示）"
        
        return "\n".join(recent)
    
    def _get_agent_name(self, agent_id: str) -> str:
        """获取角色名称"""
        names = {
            AgentId.CHENHAO.value: "陈浩",
            AgentId.SIYUAN.value: "思远",
            AgentId.LINYUTONG.value: "林雨桐",
            AgentId.ZHAOLEI.value: "赵磊",
        }
        return names.get(agent_id, agent_id)
    
    def _get_agent_role(self, agent_id: str) -> str:
        """获取角色类型"""
        roles = {
            AgentId.CHENHAO.value: AgentRole.BULLY.value,
            AgentId.SIYUAN.value: AgentRole.VICTIM.value,
            AgentId.LINYUTONG.value: AgentRole.BYSTANDER_A.value,
            AgentId.ZHAOLEI.value: AgentRole.BYSTANDER_B.value,
        }
        return roles.get(agent_id, "")
    
    def _print_instructions(self, instructions: Dict[str, PrivateInstruction]):
        """打印私聊指令"""
        print("\n" + "="*50)
        print("📨 协调者的私聊指令")
        print("="*50)
        
        for agent_id, instr in instructions.items():
            print(f"\n▶ {instr.agent_name}（{instr.role}）")
            print(f"  观察：{instr.observation}")
            print(f"  指令：{instr.directive[:50]}...")
            if instr.recommended_tone:
                print(f"  建议：{instr.recommended_tone}")
    
    def _print_status(self, evaluation: RoundEvaluation):
        """打印状态"""
        needs_intervention, level, score = self.simulator.should_intervene()
        
        print("\n" + "="*50)
        print("📊 实时评估")
        print("="*50)
        
        # 霸凌强度条
        intensity = evaluation.bullying_intensity
        bar = "█" * int(intensity * 10) + "░" * (10 - int(intensity * 10))
        print(f"  霸凌强度：[{bar}] {intensity:.0%}")
        
        # 受害者压力条
        distress = evaluation.victim_distress
        bar = "█" * int(distress * 10) + "░" * (10 - int(distress * 10))
        print(f"  受害者压力：[{bar}] {distress:.0%}")
        
        # 旁观者干预条
        bystander = evaluation.bystander_intervention
        bar = "█" * int(bystander * 10) + "░" * (10 - int(bystander * 10))
        print(f"  旁观者干预：[{bar}] {bystander:.0%}")
        
        # 干预提示
        if needs_intervention:
            emoji = {"critical": "🚨", "warning": "⚠️", "notice": "💡"}.get(level, "")
            print(f"\n  {emoji} 建议{level}：{score:.0%}")
    
    # ============================================================
    # 干预处理
    # ============================================================
    
    async def handle_intervention(self, message: str) -> dict:
        """处理教师干预"""
        result = self.simulator.teacher_intervene(message)
        
        print(f"\n{'='*50}")
        print(f"👨‍🏫 教师干预")
        print(f"{'='*50}")
        print(f"> {message}")
        qs = result.get("quality_score")
        print(f"\n📊 干预评估：{'待评估' if qs is None else f'{int(qs)}/100'}")
        for fb in result.get("feedback", []):
            print(f"   {fb}")
        print(f"\n💬 {result['recommendation']}")
        
        return result
    
    # ============================================================
    # 运行控制
    # ============================================================
    
    async def run(self, max_rounds: int = 5, interactive: bool = True):
        """运行模拟
        
        Args:
            max_rounds: 最大轮数
            interactive: 是否交互模式（需要用户输入），False则自动运行不等待输入
        """
        print("\n" + "="*70)
        print("  🎭 校园话语霸凌与教师干预实训系统 v3.0 - 轮训协调版")
        print("  基于协调者智能体 + 私聊指令 + 轮训机制")
        print("="*70)
        
        print(f"\n📋 配置")
        print(f"   霸凌级别：{self.level}级")
        print(f"   协调者模式：{'LLM推理' if self.use_llm_coordinator else '规则引擎'}")
        print(f"   最大轮数：{max_rounds}")
        print(f"   交互模式：{'是' if interactive else '否（自动运行）'}")
        
        # 显示参与者
        print(f"\n📋 参与者")
        for agent_id in AgentId:
            print(f"   {self._get_agent_name(agent_id.value)}（{agent_id.value}）")
        
        self.is_running = True
        
        try:
            for i in range(max_rounds):
                if not self.is_running:
                    break
                
                # 运行一轮
                result = await self.run_round()
                
                # 检查是否需要干预
                needs_intervention, level, score = self.simulator.should_intervene()
                
                if needs_intervention:
                    emoji = {"critical": "🚨", "warning": "⚠️", "notice": "💡"}.get(level, "")
                    print(f"\n{emoji} 系统提示：{level}，建议进行干预")
                
                # 等待用户输入（批处理模式跳过）
                if interactive:
                    print("\n" + "-"*50)
                    user_input = input("教师请输入干预内容（或按回车继续下一轮）：").strip()
                    
                    if user_input:
                        await self.handle_intervention(user_input)
                else:
                    print("\n" + "-"*50)
                    print("[批处理模式] 自动进入下一轮...")
                
        except (KeyboardInterrupt, EOFError):
            print("\n\n⚠️ 模拟已中断")
        finally:
            self.is_running = False
            _ = self.generate_report()  # 确保生成报告
        
        # 生成报告
        print("\n" + "="*70)
        print("🏁 模拟完成")
        print("="*70)
        
        return self.generate_report()
    
    def generate_report(self) -> dict:
        """生成报告"""
        report = self.simulator.evaluator.get_session_report()
        
        print(f"""
📊 评估报告

【霸凌情况】
  总消息数：{report['total_messages']}
  攻击性事件：{report['hostile_messages']}次
  平均霸凌强度：{report['metrics']['bullying_intensity']:.0%}

【受害者状态】
  最终压力指数：{report['metrics']['victim_distress']:.0%}

【教师干预】
  干预次数：{report['intervention_count']}
  干预效果：{report['metrics']['intervention_effectiveness']:.0%}

【轮训记录】
  总轮数：{self.round}
""")
        
        # 显示每轮摘要
        print("\n📋 轮次摘要")
        for r in self.round_history:
            speakers = [self._get_agent_name(a) for a in r.evaluation.speakers_this_round]
            print(f"  第{r.round_num}轮：{'、'.join(speakers) if speakers else '无人发言'} | "
                  f"霸凌{int(r.evaluation.bullying_intensity*100)}% | "
                  f"压力{int(r.evaluation.victim_distress*100)}%")
        
        # 保存报告
        filepath = self.simulator.save_session()
        print(f"\n📁 报告已保存：{filepath}")
        
        return report


# ============================================================
# 异步主入口
# ============================================================

async def async_main(args):
    """异步主入口"""
    runner = RoundRobinRunner(
        level=args.level,
        use_llm_coordinator=args.llm_coordinator,
    )
    
    try:
        await runner.run(max_rounds=args.rounds, interactive=not args.batch)
    except Exception as e:
        print(f"\n❌ 错误：{e}")
        import traceback
        traceback.print_exc()


def main():
    """主入口"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="校园话语霸凌与教师干预实训系统 v3.0 - 轮训协调版"
    )
    
    parser.add_argument("--level", "-l", type=int, default=2,
                       choices=[1, 2, 3, 4, 5],
                       help="霸凌级别 (1-5)")
    parser.add_argument("--rounds", "-r", type=int, default=5,
                       help="最大对话轮数")
    parser.add_argument("--llm-coordinator", action="store_true",
                       help="使用LLM进行协调者推理")
    parser.add_argument("--no-llm", action="store_true",
                       help="不使用LLM（使用预设响应）")
    parser.add_argument("--batch", "-b", action="store_true",
                       help="批处理模式（无交互，不等待用户输入）")
    
    args = parser.parse_args()
    
    if args.no_llm:
        args.llm_coordinator = False
    
    # 运行
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()

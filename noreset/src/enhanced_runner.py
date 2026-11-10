#!/usr/bin/env python3
"""
🎭 校园话语霸凌与教师干预实训系统 v2.0 - 增强版运行器
============================================
集成三大模块：
1. OpenClaw 多 Agent 协同
2. 教师交互界面
3. 评估报告生成
"""

import asyncio
import argparse
import sys
import time
import random
from pathlib import Path
from typing import Dict, List, Optional

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent))

from orchestrator import (
    MultiAgentSimulator,
    AgentConfig,
    format_message_display,
    format_status_display,
    AgentRole,
)

from openclaw_integration import OpenClawIntegration, LLMClient, AgentType
from interactive_ui import InteractiveUI, ReportGenerator, InterventionLevel, SimulationStatus


class EnhancedSimulationRunner:
    """
    增强版模拟运行器
    
    整合 OpenClaw 多 Agent、交互界面和报告生成
    """
    
    def __init__(self, level: int = 2, 
                 use_openclaw: bool = True,
                 use_interactive: bool = True,
                 llm_provider: str = "openclaw"):
        self.level = level
        self.use_openclaw = use_openclaw
        self.use_interactive = use_interactive
        
        # 核心模拟器
        self.simulator = MultiAgentSimulator(level=level)
        
        # OpenClaw 集成
        self.openclaw = OpenClawIntegration()
        
        # LLM 客户端
        self.llm_client = LLMClient(provider=llm_provider)
        
        # 交互界面
        self.ui = InteractiveUI(enable_colors=True)
        
        # 报告生成器
        self.report_generator = ReportGenerator(
            output_dir=str(Path(__file__).parent.parent / "logs")
        )
        
        # 状态
        self.is_running = False
        self.is_paused = False
        self.round = 0
    
    # ============================================================
    # 初始化
    # ============================================================
    
    async def initialize(self):
        """初始化所有组件"""
        print("\n" + "=" * 70)
        print("  🎭 校园话语霸凌与教师干预实训系统 v2.0 - 增强版")
        print("  基于 OpenClaw 多 Agent 协同框架")
        print("=" * 70)
        
        print(f"\n📋 配置:")
        print(f"   霸凌级别: {self.level}级")
        print(f"   OpenClaw 多Agent: {'✅' if self.use_openclaw else '❌'}")
        print(f"   交互界面: {'✅' if self.use_interactive else '❌'}")
        print(f"   LLM 提供商: {self.llm_client.provider}")
        
        # 初始化 OpenClaw Sessions
        if self.use_openclaw:
            prompts = self._build_agent_prompts()
            await self.openclaw.initialize_sessions(prompts)
    
    def _build_agent_prompts(self) -> Dict[str, str]:
        """构建 Agent 提示词"""
        prompts = {}
        
        # 读取提示词模板
        prompts_dir = Path(__file__).parent.parent / "prompts"
        
        for agent_id in ["chenhao", "siyuan", "linyutong", "zhaolei"]:
            template_path = prompts_dir / f"{agent_id.upper()}.md"
            
            if template_path.exists():
                with open(template_path, "r", encoding="utf-8") as f:
                    template = f.read()
                
                # 填充上下文
                cfg = AgentConfig.get_agent_config(agent_id)
                context = self._get_agent_context(agent_id)
                
                prompts[agent_id] = template.format(context=context)
            else:
                # 使用默认提示词
                prompts[agent_id] = self._build_default_prompt(agent_id)
        
        return prompts
    
    def _build_default_prompt(self, agent_id: str) -> str:
        """构建默认提示词"""
        cfg = AgentConfig.get_agent_config(agent_id)
        
        role_prompts = {
            "chenhao": f"""你是{cfg['name']}（{cfg['role']}）。

性格特点: {', '.join(cfg['personality']['traits'][:3])}
背景: {cfg['personality']['background'][:60]}

你的行为规则:
{chr(10).join([f"- {r['condition']} → {r['action']}" for r in cfg.get('behavior_rules', [])])}

当前情境：教室里思远展示了自己的绘画作品。

请根据角色设定，决定是否发言以及说什么。""",
            
            "siyuan": f"""你是{cfg['name']}（{cfg['role']}）。

性格特点: {', '.join(cfg['personality']['traits'][:3])}
背景: {cfg['personality']['background'][:60]}

你的行为规则:
{chr(10).join([f"- {r['condition']} → {r['action']}" for r in cfg.get('behavior_rules', [])])}

当前情境：教室里展示绘画作品时被同学嘲笑。

请根据角色设定，决定是否发言以及说什么。""",
            
            "linyutong": f"""你是{cfg['name']}（{cfg['role']}）。

性格特点: {', '.join(cfg['personality']['traits'][:3])}
背景: {cfg['personality']['background'][:60]}

你的行为规则:
{chr(10).join([f"- {r['condition']} → {r['action']}" for r in cfg.get('behavior_rules', [])])}

当前情境：看到同学被嘲笑。

请根据角色设定，决定是否发言以及说什么。""",
            
            "zhaolei": f"""你是{cfg['name']}（{cfg['role']}）。

性格特点: {', '.join(cfg['personality']['traits'][:3])}
背景: {cfg['personality']['background'][:60]}

你的行为规则:
{chr(10).join([f"- {r['condition']} → {r['action']}" for r in cfg.get('behavior_rules', [])])}

当前情境：看到同学被嘲笑。

请根据角色设定，决定是否发言以及说什么。""",
        }
        
        return role_prompts.get(agent_id, "")
    
    def _get_agent_context(self, agent_id: str) -> str:
        """获取 Agent 上下文"""
        recent = self.simulator.evaluator.message_history[-6:]
        
        if not recent:
            return "（对话即将开始）"
        
        lines = [f"【{m.sender}】{m.content}" for m in recent]
        return "\n".join(lines)
    
    # ============================================================
    # Agent 响应生成
    # ============================================================
    
    async def generate_agent_response(self, agent_id: str) -> Optional[str]:
        """生成 Agent 响应"""
        cfg = AgentConfig.get_agent_config(agent_id)
        context = self._get_agent_context(agent_id)
        
        prompt = f"""当前对话：

{context}

请作为{cfg['name']}（{cfg['role']}）根据上述对话和角色设定，决定是否发言以及说什么。

如果选择不说话，请回复"沉默"。
请直接输出你的对话内容，不要加引号或前缀。"""
        
        # 尝试使用 OpenClaw
        if self.use_openclaw and self.openclaw.is_available:
            try:
                response = await self.openclaw.send_to_agent(agent_id, prompt)
                if response and response != "沉默":
                    return response
            except Exception as e:
                print(f"   ⚠️ OpenClaw 调用失败: {e}")
        
        # 使用预设响应
        return self._get_preset_response(agent_id)
    
    def _get_preset_response(self, agent_id: str) -> Optional[str]:
        """获取预设响应"""
        responses = {
            "chenhao": {
                1: ["这画得也太丑了吧，哈哈哈！", "切，有什么了不起的。"],
                2: ["哈哈哈，你这画的是什么东西啊？", "穷鬼还画画，真是可笑！"],
                3: ["哈哈哈哈，思远你看看你那穷酸样！", "就你还想画画？"],
                4: ["思远你就是班级里的垃圾！", "穷逼还想跟我们一个班？"],
                5: ["思远你怎么不去死啊？", "你这种废物就不该存在！"],
            },
            "siyuan": {
                1: ["（小声）我...我没有...", "（低着头不说话）"],
                2: ["（眼眶泛红）我真的画了很久...", "（沉默，低下头）"],
                3: ["（忍不住眼泪）我没有...", "（小声哭泣）"],
                4: ["（眼泪止不住）为什么总是我...", "（无声哭泣）"],
                5: ["（无声落泪，不敢看任何人）", "（蜷缩在座位上）"],
            },
            "linyutong": {
                1: ["陈浩，你这样说有点过分了吧。", "思远的画挺好的啊。"],
                2: ["陈浩，你过了！", "思远别理他们，你的画很棒。"],
                3: ["够了！陈浩你太过分了！", "思远，别往心里去。"],
                4: ["陈浩！你太过分了！", "必须告诉老师！"],
                5: ["陈浩你这个行为是犯罪你知道吗！", "思远别怕！"],
            },
            "zhaolei": {
                1: ["（跟着笑）哈哈哈...", "好像是有点好笑..."],
                2: ["是啊是啊，有点土...", "（跟着起哄）"],
                3: ["（有点犹豫）不过这么说确实...", "（附和）"],
                4: ["（小声）好像有点过分了...", "（不安地挪动）"],
                5: ["（小声）是不是太过分了...", "（看向老师方向）"],
            },
        }
        
        agent_responses = responses.get(agent_id, {}).get(self.level, ["..."])
        return random.choice(agent_responses) if agent_responses else None
    
    # ============================================================
    # 对话轮次
    # ============================================================
    
    async def run_round(self):
        """运行一轮对话"""
        self.round += 1
        
        print(f"\n{'='*60}")
        print(f"🎯 第 {self.round} 轮对话")
        print(f"{'='*60}")
        
        # 选择发言者
        speakers = self._select_speakers()
        
        for agent_id in speakers:
            cfg = AgentConfig.get_agent_config(agent_id)
            
            # 生成响应
            response = await self.generate_agent_response(agent_id)
            
            if response and "沉默" not in response:
                # 添加消息
                msg, eval_result = self.simulator.add_message(
                    sender=cfg["name"],
                    sender_id=agent_id,
                    role=cfg["role"],
                    content=response,
                )
                
                # 显示消息
                self.ui.print_role_message(
                    sender=cfg["name"],
                    role=cfg["role"],
                    content=response,
                    eval_result=eval_result
                )
        
        # 显示状态
        status = self._get_current_status()
        self.ui.print_status_panel(status)
        
        # 检查干预建议
        needs_intervention, level, score = self.simulator.should_intervene()
        
        if needs_intervention:
            level_enum = InterventionLevel.CRITICAL if level == "critical" else InterventionLevel.WARNING
            self.ui.print_intervention_alert(level_enum, {
                "bullying_intensity": score,
                "victim_distress": 0,
            })
        
        return needs_intervention
    
    def _select_speakers(self) -> List[str]:
        """选择发言者"""
        recent = [m.sender_id for m in self.simulator.evaluator.message_history[-3:]]
        
        speakers = []
        
        # 被霸凌者优先
        if self.simulator.evaluator.metrics.hostile_message_count > 0:
            if "siyuan" not in recent[-1:]:
                speakers.append("siyuan")
        
        # 霸凌者
        if "chenhao" not in recent[-2:]:
            speakers.append("chenhao")
        
        # 旁观者
        bystanders = ["linyutong", "zhaolei"]
        for b in bystanders:
            if b not in recent[-1:] and len(speakers) < 2:
                speakers.append(b)
        
        return speakers[:2]
    
    def _get_current_status(self) -> SimulationStatus:
        """获取当前状态"""
        needs_intervention, level, score = self.simulator.should_intervene()
        
        level_enum = InterventionLevel.NONE
        if level == "critical":
            level_enum = InterventionLevel.CRITICAL
        elif level == "warning":
            level_enum = InterventionLevel.WARNING
        elif level == "notice":
            level_enum = InterventionLevel.NOTICE
        
        return SimulationStatus(
            round=self.round,
            phase=self.simulator.phase.value,
            bullying_level=self.level,
            bullying_intensity=self.simulator.evaluator.metrics.bullying_intensity,
            victim_distress=self.simulator.evaluator.metrics.victim_distress,
            hostile_count=self.simulator.evaluator.metrics.hostile_message_count,
            total_messages=self.simulator.evaluator.metrics.total_message_count,
            needs_intervention=needs_intervention,
            intervention_level=level_enum,
        )
    
    # ============================================================
    # 干预处理
    # ============================================================
    
    async def handle_intervention(self, message: str) -> Dict:
        """处理教师干预"""
        result = self.simulator.teacher_intervene(message)
        
        print(f"\n{'='*60}")
        print(f"👨‍🏫 教师干预")
        print(f"{'='*60}")
        print(f"> {message}")
        
        self.ui.print_evaluation_result(result)
        
        # 观察霸凌者反应（如果有良好干预）（R2：百分制阈值 50）
        qs = result.get("quality_score")
        if qs is not None and qs >= 50:
            await self._generate_post_intervention()
        
        return result
    
    async def _generate_post_intervention(self):
        """生成干预后的反应"""
        # 霸凌者可能认错或沉默
        if random.random() > 0.5:
            await asyncio.sleep(0.5)
            response = await self.generate_agent_response("chenhao")
            
            if response and "沉默" not in response:
                cfg = AgentConfig.get_agent_config("chenhao")
                msg, eval_result = self.simulator.add_message(
                    sender=cfg["name"],
                    sender_id="chenhao",
                    role=cfg["role"],
                    content=response,
                )
                
                self.ui.print_role_message(
                    sender=cfg["name"],
                    role=cfg["role"],
                    content=response,
                    eval_result=eval_result
                )
    
    # ============================================================
    # 报告生成
    # ============================================================
    
    def generate_reports(self):
        """生成多种格式的报告"""
        report_data = self.simulator.evaluator.get_session_report()
        
        # 生成 JSON 报告
        json_path = self.report_generator.generate_json_report(report_data)
        print(f"\n📄 JSON 报告已保存: {json_path}")
        
        # 生成文本报告
        text_path = self.report_generator.generate_text_report(report_data)
        print(f"📄 文本报告已保存: {text_path}")
        
        # 生成 HTML 报告
        html_path = self.report_generator.generate_html_report(report_data)
        print(f"📄 HTML 报告已保存: {html_path}")
        
        # 显示最终报告
        self.ui.display_final_report(report_data)
        
        return report_data
    
    # ============================================================
    # 运行控制
    # ============================================================
    
    async def run(self, max_rounds: int = None):
        """运行模拟"""
        max_rounds = max_rounds or self.simulator.max_rounds
        
        # 初始化
        await self.initialize()
        
        # 打印参与者信息
        print("\n📋 参与者档案:")
        for agent_id, cfg in AgentConfig.get_all_agents().items():
            personality = cfg.get("personality", {})
            traits = personality.get("traits", [])[:3]
            print(f"\n  {cfg['name']} ({cfg['role']})")
            print(f"    性格: {', '.join(traits)}")
        
        print(f"\n🎬 开始模拟 (最多 {max_rounds} 轮)")
        print("-" * 60)
        
        self.is_running = True
        
        # 对话循环
        for i in range(max_rounds):
            if not self.is_running:
                break
            
            # 运行一轮
            needs_intervention = await self.run_round()
            
            # 如果需要干预，提示教师
            if needs_intervention:
                print("\n💡 建议进行干预，输入您的干预内容")
            
            # 继续或干预
            if self.use_interactive:
                user_input = input("\n> ").strip()
                
                if user_input.startswith("/"):
                    await self._handle_command(user_input)
                elif user_input:
                    await self.handle_intervention(user_input)
        
        # 生成报告
        if self.is_running:
            print("\n🏁 模拟完成!")
            self.generate_reports()
    
    async def _handle_command(self, cmd: str):
        """处理命令"""
        if cmd == "/stop":
            self.is_running = False
            print("\n⏹️ 模拟已停止")
            self.generate_reports()
        
        elif cmd == "/report":
            self.generate_reports()
        
        elif cmd == "/help":
            self.ui.print_help()
    
    async def cleanup(self):
        """清理资源"""
        if self.use_openclaw:
            await self.openclaw.cleanup()


# ============================================================
# 异步主入口
# ============================================================

async def async_main(args):
    """异步主入口"""
    runner = EnhancedSimulationRunner(
        level=args.level,
        use_openclaw=args.openclaw,
        use_interactive=not args.batch,
        llm_provider=args.llm,
    )
    
    try:
        await runner.run(max_rounds=args.rounds)
    except KeyboardInterrupt:
        print("\n\n⚠️ 模拟已中断")
        runner.generate_reports()
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await runner.cleanup()


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="校园话语霸凌与教师干预实训系统 v2.0 - 增强版"
    )
    
    parser.add_argument("--level", "-l", type=int, default=2,
                       choices=[1, 2, 3, 4, 5],
                       help="霸凌级别 (1-5)")
    parser.add_argument("--rounds", "-r", type=int, default=None,
                       help="最大对话轮数")
    parser.add_argument("--openclaw", "-o", action="store_true", default=True,
                       help="使用 OpenClaw 多Agent")
    parser.add_argument("--no-openclaw", action="store_false", dest="openclaw",
                       help="不使用 OpenClaw")
    parser.add_argument("--batch", "-b", action="store_true",
                       help="批处理模式（无交互）")
    parser.add_argument("--llm", default="openclaw",
                       choices=["openclaw", "openai", "anthropic", "minimax"],
                       help="LLM 提供商")
    
    args = parser.parse_args()
    
    # 运行
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()

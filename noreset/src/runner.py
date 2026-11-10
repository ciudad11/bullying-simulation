#!/usr/bin/env python3
"""
🎭 校园话语霸凌与教师干预实训系统 v2.0 - OpenClaw 集成运行器
================================================
使用 OpenClaw 多 Agent 协同框架

核心特性：
1. 多 Agent Session 管理
2. 消息驱动架构
3. 实时评估
4. 教师干预接口
"""

import json
import time
import asyncio
from typing import Dict, List, Optional, Any
from pathlib import Path

# 尝试导入 OpenClaw SDK
try:
    from openclaw import sessions_spawn, sessions_send, sessions_history, sessions_list
    OPENCLAW_AVAILABLE = True
except ImportError:
    OPENCLAW_AVAILABLE = False

from orchestrator import (
    MultiAgentSimulator,
    AgentConfig,
    PromptFactory,
    EnhancedEvaluator,
    KnowledgeBase,
    format_message_display,
    format_status_display,
    AgentRole,
)


class OpenClawRunner:
    """
    OpenClaw 多 Agent 协同运行器
    
    使用 OpenClaw Session API 实现：
    1. 为每个角色创建独立 Session
    2. 通过 sessions_send 实现消息驱动
    3. Evaluator Agent 实时监控
    4. 教师实时干预
    """
    
    # Agent Session 标签
    AGENT_SESSIONS = {
        "chenhao": "bullying_bully",
        "siyuan": "bullying_victim",
        "linyutong": "bullying_bystander_a",
        "zhaolei": "bullying_bystander_b",
    }
    
    # 角色配置
    ROLE_CONFIG = {
        "chenhao": {
            "name": "陈浩",
            "role": "霸凌者",
            "color": "🔴",
        },
        "siyuan": {
            "name": "思远", 
            "role": "被霸凌者",
            "color": "🔵",
        },
        "linyutong": {
            "name": "林雨桐",
            "role": "正义旁观者",
            "color": "🟢",
        },
        "zhaolei": {
            "name": "赵磊",
            "role": "从众旁观者",
            "color": "🟡",
        },
    }
    
    def __init__(self, level: int = 2, workspace: str = None):
        self.level = level
        self.workspace = Path(workspace) if workspace else Path(__file__).parent.parent
        self.simulator = MultiAgentSimulator(level=level, save_dir=str(self.workspace / "logs"))
        
        # Agent Sessions
        self.agent_sessions: Dict[str, str] = {}  # agent_id -> session_key
        self.evaluator_session: str = ""
        self.coordinator_session: str = ""
        
        # 状态
        self.is_running = False
        self.is_paused = False
        self.round = 0
        
        # OpenClaw 可用性
        self.use_openclaw = OPENCLAW_AVAILABLE
    
    # ============================================================
    # 初始化
    # ============================================================
    
    async def initialize(self):
        """初始化所有 Agent Sessions"""
        print("=" * 60)
        print("🎭 校园话语霸凌与教师干预实训系统 v2.0 - OpenClaw 多 Agent 协同")
        print("=" * 60)
        print(f"\n霸凌级别: {self.level}级")
        print(f"OpenClaw 多Agent: {'✅ 已启用' if self.use_openclaw else '⚠️ 未启用（使用模拟模式）'}")
        
        if self.use_openclaw:
            await self._init_openclaw_sessions()
        else:
            await self._init_mock_sessions()
        
        return True
    
    async def _init_openclaw_sessions(self):
        """使用 OpenClaw 初始化 Agent Sessions"""
        print("\n🚀 正在初始化 Agent Sessions...")
        
        # 为每个角色创建 Session
        for agent_id, label in self.AGENT_SESSIONS.items():
            cfg = AgentConfig.get_agent_config(agent_id)
            prompt = PromptFactory.build_system_prompt(agent_id, self.level)
            
            try:
                # 使用 sessions_spawn 创建独立 Session
                result = await sessions_spawn(
                    task=prompt,
                    label=label,
                    runtime="subagent",
                    mode="session",
                )
                self.agent_sessions[agent_id] = result.get("sessionKey", "")
                print(f"   ✅ {cfg['name']} ({cfg['role']}) Session 已创建")
            except Exception as e:
                print(f"   ❌ {cfg['name']} Session 创建失败: {e}")
        
        # 创建 Evaluator Session
        evaluator_prompt = self._build_evaluator_prompt()
        try:
            result = await sessions_spawn(
                task=evaluator_prompt,
                label="bullying_evaluator",
                runtime="subagent", 
                mode="session",
            )
            self.evaluator_session = result.get("sessionKey", "")
            print(f"   ✅ 评估者 Session 已创建")
        except Exception as e:
            print(f"   ⚠️ 评估者 Session 创建失败: {e}")
        
        print("\n✅ 所有 Agent Sessions 初始化完成!")
    
    async def _init_mock_sessions(self):
        """模拟模式 - 不使用 OpenClaw"""
        print("\n🔧 模拟模式：使用本地 LLM 调用")
        for agent_id in self.AGENT_SESSIONS.keys():
            cfg = AgentConfig.get_agent_config(agent_id)
            print(f"   ✅ {cfg['name']} ({cfg['role']}) 已配置")
    
    def _build_evaluator_prompt(self) -> str:
        """构建 Evaluator Agent 的 prompt"""
        return f"""你是一个专业的教育心理学评估专家，负责监控话语霸凌模拟训练。

## 你的职责

1. **实时监控** - 跟踪所有对话消息
2. **评估霸凌强度** - 使用多维度指标
3. **追踪受害者状态** - 情绪变化
4. **检测干预时机** - 当需要成人介入时提醒
5. **评估干预质量** - 提供即时反馈

## 评估指标

| 指标 | 描述 | 阈值 |
|------|------|------|
| 霸凌强度 | 攻击性词汇、频率、升级 | >0.3 关注 |
| 受害者压力 | 情绪变化、求助信号 | >0.4 警告 |
| 干预时机 | 早期 vs 晚期干预 | 影响效果 |

## 输出格式

每次收到消息后，输出 JSON 格式评估：
```json
{{
  "type": "evaluation",
  "metrics": {{
    "bullying_intensity": 0.XX,
    "victim_distress": 0.XX,
    "needs_intervention": true/false,
    "intervention_level": "none/low/medium/high/critical"
  }},
  "alert": "可选的警告消息",
  "suggestion": "可选的干预建议"
}}
```

## 当前模拟设置

霸凌级别: {self.level}级
参与者: 陈浩(霸凌者)、思远(被霸凌者)、林雨桐(旁观者/正义)、赵磊(旁观者/从众)

开始监控并等待消息输入。"""
    
    # ============================================================
    # 消息处理
    # ============================================================
    
    async def generate_agent_response(self, agent_id: str, context: str) -> str:
        """生成 Agent 响应"""
        if not self.use_openclaw or agent_id not in self.agent_sessions:
            return await self._mock_agent_response(agent_id, context)
        
        session_key = self.agent_sessions[agent_id]
        
        try:
            # 使用 sessions_send 发送消息并获取响应
            result = await sessions_send(
                sessionKey=session_key,
                message=context,
                timeoutSeconds=30,
            )
            return result.get("message", "")
        except Exception as e:
            print(f"   ⚠️ {agent_id} 响应失败: {e}")
            return await self._mock_agent_response(agent_id, context)
    
    async def _mock_agent_response(self, agent_id: str, context: str) -> str:
        """模拟 Agent 响应（当 OpenClaw 不可用时）"""
        # 简单模拟 - 实际使用时应调用本地 LLM
        cfg = AgentConfig.get_agent_config(agent_id)
        role = cfg.get("role", "")
        
        responses = {
            "霸凌者": "（冷笑）切，有什么了不起的...",
            "被霸凌者": "（低着头不说话）",
            "旁观者/仗义执言者": "你们这样太过分了吧？",
            "旁观者/霸凌助攻": "就是就是，哈哈哈...",
        }
        
        return responses.get(role, "...")
    
    async def process_round(self) -> Dict[str, Any]:
        """处理一个对话轮次"""
        self.round += 1
        print(f"\n{'='*50}")
        print(f"🎯 第 {self.round} 轮对话")
        print(f"{'='*50}")
        
        results = []
        
        # 1. 选择发言者（根据角色优先级和情境）
        speakers = self._select_speakers()
        
        for agent_id in speakers:
            # 构建上下文
            context = self._build_context_for_agent(agent_id)
            
            # 获取响应
            response = await self.generate_agent_response(agent_id, context)
            
            if response and response != "沉默":
                # 添加到模拟器
                cfg = AgentConfig.get_agent_config(agent_id)
                msg, eval_result = self.simulator.add_message(
                    sender=cfg["name"],
                    sender_id=agent_id,
                    role=cfg["role"],
                    content=response,
                )
                
                # 显示
                print(format_message_display(msg, eval_result))
                results.append({
                    "agent_id": agent_id,
                    "message": msg,
                    "evaluation": eval_result,
                })
        
        # 2. 检查是否需要干预
        needs_intervention, level, score = self.simulator.should_intervene()
        
        if needs_intervention:
            emoji = {"critical": "🚨", "warning": "⚠️", "notice": "💡"}.get(level, "")
            print(f"\n{emoji} 【系统提示】霸凌强度 {score:.0%}，建议进行干预")
        
        # 3. 发送评估到 Evaluator（如果可用）
        if self.evaluator_session:
            await self._send_to_evaluator(results)
        
        return {
            "round": self.round,
            "messages": results,
            "needs_intervention": needs_intervention,
            "intervention_level": level,
            "status": self.simulator.get_status(),
        }
    
    def _select_speakers(self) -> List[str]:
        """选择本轮发言者"""
        recent = [m.sender_id for m in self.simulator.evaluator.message_history[-3:]]
        
        speakers = []
        # 被霸凌者优先（被攻击后需要反应）
        if self.simulator.evaluator.metrics.hostile_message_count > 0:
            if "siyuan" not in recent[-1:]:
                speakers.append("siyuan")
        
        # 霸凌者发言
        if "chenhao" not in recent[-2:]:
            speakers.append("chenhao")
        
        # 旁观者随机发言
        bystanders = ["linyutong", "zhaolei"]
        available = [b for b in bystanders if b not in recent[-1:]]
        if available:
            speakers.append(available[0] if available else bystanders[0])
        
        return speakers[:2]  # 每轮最多2人发言
    
    def _build_context_for_agent(self, agent_id: str) -> str:
        """为指定 Agent 构建上下文"""
        cfg = AgentConfig.get_agent_config(agent_id)
        context = self.simulator.get_context_for_agent(agent_id)
        
        recent_msgs = "\n".join([
            f"【{m['sender']}】{m['content']}"
            for m in context.recent_messages[-4:]
        ])
        
        return f"""当前对话情境：

{recent_msgs if recent_msgs else '（对话开始）'}

请作为{cfg['name']}（{cfg['role']}）根据以上对话和你的性格特点，决定是否发言以及说什么。
如果你选择不说话，请回复"沉默"。
请直接输出你的对话内容，不要加引号。"""
    
    async def _send_to_evaluator(self, results: List[Dict]):
        """发送结果到 Evaluator Session"""
        if not self.evaluator_session or not results:
            return
        
        try:
            eval_data = {
                "type": "message_batch",
                "messages": [
                    {
                        "sender": r["message"].sender,
                        "role": r["message"].sender_role,
                        "content": r["message"].content,
                        "evaluation": r["evaluation"],
                    }
                    for r in results
                ],
                "current_metrics": self.simulator.evaluator.metrics.to_dict(),
            }
            
            await sessions_send(
                sessionKey=self.evaluator_session,
                message=json.dumps(eval_data, ensure_ascii=False),
                timeoutSeconds=10,
            )
        except Exception as e:
            print(f"   ⚠️ 发送评估失败: {e}")
    
    # ============================================================
    # 教师干预
    # ============================================================
    
    async def teacher_intervention(self, message: str) -> Dict:
        """处理教师干预"""
        result = self.simulator.teacher_intervene(message)
        
        print(f"\n{'='*50}")
        print(f"👨‍🏫 教师干预")
        print(f"{'='*50}")
        print(f"> {message}")
        print(f"\n📊 干预评估:")
        qs = result.get("quality_score")
        print(f"   质量得分: {'待评估' if qs is None else f'{int(qs)}/100'}")
        for fb in result.get("feedback", []):
            print(f"   {fb}")
        print(f"\n💬 {result['recommendation']}")
        
        # 观察霸凌者反应（R2：百分制阈值 50）
        if qs is not None and qs >= 50:
            await self._generate_bully_reaction()
        
        return result
    
    async def _generate_bully_reaction(self):
        """生成霸凌者对干预的反应"""
        response = await self.generate_agent_response(
            "chenhao",
            f"老师刚才说：'{{你刚才说的话可能让思远不舒服。}}'\n\n作为陈浩，你会有什么反应？是否认、沉默、还是道歉？"
        )
        
        if response and response != "沉默":
            cfg = AgentConfig.get_agent_config("chenhao")
            msg, eval_result = self.simulator.add_message(
                sender=cfg["name"],
                sender_id="chenhao",
                role=cfg["role"],
                content=response,
            )
            print(format_message_display(msg, eval_result))
    
    # ============================================================
    # 运行控制
    # ============================================================
    
    async def run(self, max_rounds: int = None):
        """运行模拟"""
        max_rounds = max_rounds or self.simulator.max_rounds
        
        print(f"\n🎬 开始模拟 (最多 {max_rounds} 轮)")
        print("-" * 50)
        
        self.is_running = True
        
        while self.is_running and self.round < max_rounds:
            if self.is_paused:
                await asyncio.sleep(0.5)
                continue
            
            # 处理一轮
            result = await self.process_round()
            
            # 检查是否需要停止
            needs_intervention, level, score = result.get("needs_intervention", (False, None, 0))
            
            # 每轮后暂停让教师干预
            if self.round < max_rounds:
                print(f"\n⏸️  教师请输入干预内容，或按回车继续...")
        
        print(f"\n{'='*50}")
        print("🏁 模拟完成")
        print(f"{'='*50}")
        
        # 生成报告
        report = await self.generate_report()
        return report
    
    async def generate_report(self) -> Dict:
        """生成评估报告"""
        print("\n" + "=" * 50)
        print("📊 评估报告")
        print("=" * 50)
        
        report = self.simulator.evaluator.get_session_report()
        metrics = report["metrics"]
        
        print(f"""
【霸凌情况】
  总消息数: {report['total_messages']}
  攻击性事件: {report['hostile_messages']}次
  平均攻击强度: {metrics['bullying_intensity']:.0%}
  最终霸凌强度: {metrics['bullying_intensity']:.0%}

【受害者状态】
  压力指数: {metrics['victim_distress']:.0%}
  情绪轨迹: 已记录

【教师干预】
  干预次数: {report['intervention_count']}
  干预效果: {metrics['intervention_effectiveness']:.0%}

【能力评估】
  • 发现能力: {'✅' if report['hostile_messages'] > 0 else '⚠️'} 能够识别霸凌行为
  • 干预能力: {'✅' if report['intervention_count'] > 0 else '⚠️'} 进行了干预
  • 方法质量: {metrics['intervention_effectiveness']:.0%}
""")
        
        # 保存报告
        filepath = self.simulator.save_session()
        print(f"\n📁 报告已保存: {filepath}")
        
        return report
    
    def pause(self):
        """暂停模拟"""
        self.is_paused = True
        print("⏸️ 模拟已暂停")
    
    def resume(self):
        """继续模拟"""
        self.is_paused = False
        print("▶️ 模拟已继续")
    
    def stop(self):
        """停止模拟"""
        self.is_running = False
        print("⏹️ 模拟已停止")


# ============================================================
# 快捷命令
# ============================================================

async def start_simulation(level: int = 2):
    """启动模拟"""
    runner = OpenClawRunner(level=level)
    await runner.initialize()
    
    # 初始对话
    print("\n🎬 模拟开始...")
    print("观察学生对话，在合适时机进行干预\n")
    
    # 运行3轮观察
    await runner.run(max_rounds=3)
    
    return runner


# ============================================================
# 主入口
# ============================================================

if __name__ == "__main__":
    import sys
    
    # 获取级别参数
    level = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    
    print(f"🎭 校园话语霸凌与教师干预实训系统 v2.0")
    print(f"霸凌级别: {level}级")
    print()
    
    # 运行
    asyncio.run(start_simulation(level))

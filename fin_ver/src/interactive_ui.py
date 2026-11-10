#!/usr/bin/env python3
"""
🎭 校园话语霸凌与教师干预实训系统 v2.0 - 教师交互界面
============================================
实时交互式教师干预训练界面

功能：
1. 实时显示对话和评估状态
2. 支持教师实时干预输入
3. 即时干预效果反馈
4. 多模拟会话管理
"""

import asyncio
import sys
import time
try:
    import readline  # Unix 用于更好的输入体验
except ImportError:
    readline = None  # Windows 不支持
from typing import Optional, Dict, List, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


class InterventionLevel(Enum):
    """干预级别"""
    NONE = "none"
    NOTICE = "notice"      # 注意
    WARNING = "warning"    # 警告
    CRITICAL = "critical"  # 紧急


@dataclass
class TeacherIntervention:
    """教师干预记录"""
    timestamp: float
    content: str
    quality_score: float
    feedback: List[str]
    recommendation: str
    metrics_snapshot: Dict = field(default_factory=dict)


@dataclass
class SimulationStatus:
    """模拟状态"""
    round: int
    phase: str
    bullying_level: int
    bullying_intensity: float
    victim_distress: float
    hostile_count: int
    total_messages: int
    needs_intervention: bool
    intervention_level: InterventionLevel
    last_intervention: Optional[TeacherIntervention] = None


class InteractiveUI:
    """
    教师交互界面
    
    提供实时、交互式的教师干预训练体验
    """
    
    # ANSI 颜色代码
    COLORS = {
        "reset": "\033[0m",
        "bold": "\033[1m",
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "magenta": "\033[95m",
        "cyan": "\033[96m",
        "white": "\033[97m",
        "bg_red": "\033[41m",
        "bg_green": "\033[42m",
        "bg_yellow": "\033[43m",
    }
    
    # 角色图标
    ROLE_ICONS = {
        "bully": "😈",
        "victim": "😢",
        "bystander_a": "✨",
        "bystander_b": "👀",
        "teacher": "👨‍🏫",
        "system": "⚙️",
    }
    
    def __init__(self, enable_colors: bool = True):
        self.enable_colors = enable_colors
        self.interventions: List[TeacherIntervention] = []
        self.messages: List[Dict] = []
        self.is_running = False
        self.status_callback: Optional[Callable] = None
        self.intervention_callback: Optional[Callable[[str], Dict]] = None
    
    def _color(self, text: str, color: str) -> str:
        """添加颜色"""
        if not self.enable_colors:
            return text
        c = self.COLORS.get(color, "")
        return f"{c}{text}{self.COLORS['reset']}"
    
    def _bold(self, text: str) -> str:
        """加粗"""
        if not self.enable_colors:
            return text
        return self._color(text, "bold")
    
    # ============================================================
    # 界面组件
    # ============================================================
    
    def print_header(self, title: str, subtitle: str = ""):
        """打印标题"""
        width = 70
        print()
        print("=" * width)
        print(self._bold(f"  {title}").center(width))
        if subtitle:
            print(subtitle.center(width))
        print("=" * width)
        print()
    
    def print_divider(self, char: str = "-", length: int = 50):
        """打印分隔线"""
        print(char * length)
    
    def print_status_bar(self, label: str, value: float, 
                        max_value: float = 1.0,
                        color: str = "cyan",
                        width: int = 20):
        """打印状态条"""
        filled = int((value / max_value) * width)
        empty = width - filled
        bar = "█" * filled + "░" * empty
        percentage = f"{value * 100:.0f}%"
        
        color_map = {
            "cyan": "cyan",
            "red": "red",
            "yellow": "yellow",
            "green": "green",
        }
        
        bar_color = color_map.get(color, "cyan")
        
        if self.enable_colors:
            print(f"  {label}: [{self._color(bar, bar_color)}] {percentage}")
        else:
            print(f"  {label}: [{bar}] {percentage}")
    
    def print_role_message(self, sender: str, role: str, content: str,
                         eval_result: Dict = None):
        """打印角色消息"""
        icon = self.ROLE_ICONS.get(role, "👤")
        role_name = {
            "bully": "霸凌者",
            "victim": "被霸凌者",
            "bystander_a": "正义旁观",
            "bystander_b": "从众旁观",
            "teacher": "老师",
            "system": "系统",
        }.get(role, role)
        
        print()
        print(f"{self._bold(f'{icon} 【{sender}】')} {self._color(f'({role_name})', 'blue')}")
        print(f"   {content}")
        
        if eval_result:
            hostile = eval_result.get("hostile_score", 0)
            emotions = eval_result.get("detected_emotions", [])
            
            if hostile > 0:
                hostile_color = "red" if hostile > 0.5 else "yellow"
                print(f"   {self._color('📊 攻击性:', 'yellow')} {self._color(f'{hostile:.0%}', hostile_color)}")
            
            if emotions:
                emotion_str = ", ".join(emotions)
                print(f"   {self._color('😢 情绪:', 'yellow')} {emotion_str}")
    
    def print_intervention_prompt(self):
        """打印干预提示"""
        print()
        print(self._bold("─" * 50))
        print(self._bold("👨‍🏫 教师干预"))
        print(self._bold("─" * 50))
        print()
        print("  输入您的干预内容，或按 Enter 继续观察")
        print()
    
    def print_evaluation_result(self, result: Dict):
        """打印评估结果"""
        quality = result.get("quality_score", 0)
        feedback = result.get("feedback", [])
        recommendation = result.get("recommendation", "")
        
        print()
        print(self._bold("📊 干预评估"))
        print("─" * 40)
        
        # 质量分数
        if quality >= 0.8:
            quality_label = self._color("优秀", "green")
        elif quality >= 0.6:
            quality_label = self._color("良好", "cyan")
        elif quality >= 0.4:
            quality_label = self._color("一般", "yellow")
        else:
            quality_label = self._color("需改进", "red")
        
        print(f"  质量得分: {quality_label} ({quality:.0%})")
        
        # 反馈
        if feedback:
            print()
            for fb in feedback:
                if fb.startswith("✅"):
                    print(f"  {self._color(fb, 'green')}")
                elif fb.startswith("⚠️"):
                    print(f"  {self._color(fb, 'yellow')}")
                else:
                    print(f"  {fb}")
        
        # 建议
        print()
        print(f"  💬 {recommendation}")
    
    def print_intervention_alert(self, level: InterventionLevel, 
                                metrics: Dict):
        """打印干预警报"""
        if level == InterventionLevel.NONE:
            return
        
        intensity = metrics.get("bullying_intensity", 0)
        distress = metrics.get("victim_distress", 0)
        
        print()
        if level == InterventionLevel.CRITICAL:
            alert = self._bold(self._color("🚨 紧急干预!", "bg_red"))
            desc = "霸凌行为严重，可能对受害者造成伤害"
        elif level == InterventionLevel.WARNING:
            alert = self._bold(self._color("⚠️ 建议干预", "bg_yellow"))
            desc = "霸凌强度上升，建议及时介入"
        else:
            alert = self._color("💡 关注", "yellow")
            desc = "观察到霸凌迹象"
        
        print(f"  {alert}")
        print(f"  {self._color(desc, 'white')}")
        print(f"  霸凌强度: {intensity:.0%} | 受害者压力: {distress:.0%}")
    
    def print_status_panel(self, status: SimulationStatus):
        """打印状态面板"""
        print()
        print(self._bold("─" * 50))
        print(self._bold(f"📍 第 {status.round} 轮 | 阶段: {status.phase}"))
        print(self._bold("─" * 50))
        
        # 评估指标条
        self.print_status_bar(
            "霸凌强度",
            status.bullying_intensity,
            color="red" if status.bullying_intensity > 0.5 else "cyan"
        )
        
        self.print_status_bar(
            "受害者压力",
            status.victim_distress,
            color="yellow" if status.victim_distress > 0.3 else "green"
        )
        
        print()
        print(f"  攻击性消息: {status.hostile_count}条")
        print(f"  总消息数: {status.total_messages}")
        
        # 干预警报
        if status.needs_intervention:
            level = InterventionLevel.WARNING if status.intervention_level.value == "warning" else InterventionLevel.CRITICAL
            self.print_intervention_alert(level, {
                "bullying_intensity": status.bullying_intensity,
                "victim_distress": status.victim_distress,
            })
    
    def print_help(self):
        """打印帮助信息"""
        print()
        print(self._bold("📖 可用命令"))
        print("─" * 40)
        print("  干预命令:")
        print("    直接输入您的干预内容")
        print()
        print("  控制命令:")
        print("    /help     - 显示帮助")
        print("    /status   - 显示状态")
        print("    /report   - 生成报告")
        print("    /pause    - 暂停模拟")
        print("    /resume   - 继续模拟")
        print("    /stop     - 停止模拟")
        print("    /quit     - 退出")
        print()
        print("  快捷干预:")
        print("    /kind     - 和善提醒")
        print("    /firm     - 坚定制止")
        print("    /support  - 支持受害者")
        print("    /reflect  - 换位思考引导")
        print()
    
    # ============================================================
    # 交互功能
    # ============================================================
    
    def get_input(self, prompt: str = "> ") -> str:
        """获取用户输入"""
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return "/quit"
    
    def get_intervention_input(self) -> str:
        """获取干预输入"""
        print()
        return self.get_input(f"{self._color('👨‍🏫', 'cyan')} 您的干预: ")
    
    async def run_interactive_loop(self, on_intervention: Callable,
                                  on_status: Callable,
                                  on_report: Callable,
                                  on_quit: Callable):
        """
        运行交互循环
        
        Args:
            on_intervention: 干预回调 (content) -> evaluation_result
            on_status: 状态回调 () -> SimulationStatus
            on_report: 报告回调 () -> None
            on_quit: 退出回调 () -> None
        """
        self.is_running = True
        
        self.print_header(
            "🎭 校园话语霸凌与教师干预实训系统 - 教师实训",
            "实时交互式干预训练"
        )
        
        print("💡 提示: 输入 /help 查看可用命令")
        print()
        
        while self.is_running:
            # 显示状态
            status = on_status()
            if status:
                self.print_status_panel(status)
            
            # 获取输入
            user_input = self.get_intervention_input()
            
            if not user_input:
                continue
            
            # 处理命令
            if user_input.startswith("/"):
                cmd = user_input.lower()
                
                if cmd == "/help":
                    self.print_help()
                    
                elif cmd == "/status":
                    status = on_status()
                    if status:
                        self.print_status_panel(status)
                    
                elif cmd == "/report":
                    self.print_header("📊 生成评估报告")
                    on_report()
                    
                elif cmd == "/pause":
                    print(self._color("\n⏸️  模拟已暂停", "yellow"))
                    
                elif cmd == "/resume":
                    print(self._color("\n▶️  模拟已继续", "green"))
                    
                elif cmd == "/stop":
                    print(self._color("\n⏹️  模拟已停止", "red"))
                    self.is_running = False
                    on_quit()
                    
                elif cmd == "/quit":
                    print(self._color("\n👋 再见!", "cyan"))
                    self.is_running = False
                    on_quit()
                
                # 快捷干预
                elif cmd == "/kind":
                    intervention = "同学们，我们应该友好相处，不要取笑别人。"
                    result = on_intervention(intervention)
                    self.print_evaluation_result(result)
                    
                elif cmd == "/firm":
                    intervention = "陈浩，你的言论已经越界了。请停止这种行为。"
                    result = on_intervention(intervention)
                    self.print_evaluation_result(result)
                    
                elif cmd == "/support":
                    intervention = "思远，你还好吗？老师注意到你可能不舒服。"
                    result = on_intervention(intervention)
                    self.print_evaluation_result(result)
                    
                elif cmd == "/reflect":
                    intervention = "如果被取笑的是你，你会有什么感受？换位思考一下。"
                    result = on_intervention(intervention)
                    self.print_evaluation_result(result)
                
                else:
                    print(self._color(f"\n⚠️ 未知命令: {cmd}", "yellow"))
                    print("   输入 /help 查看可用命令")
            
            # 处理干预
            elif user_input:
                result = on_intervention(user_input)
                self.print_evaluation_result(result)
    
    def display_final_report(self, report: Dict):
        """显示最终报告"""
        self.print_header("📊 教师实训评估报告")
        
        metrics = report.get("metrics", {})
        
        # 霸凌情况
        print(self._bold("\n【霸凌情况】"))
        print(f"  总消息数: {report.get('total_messages', 0)}")
        print(f"  攻击性事件: {report.get('hostile_messages', 0)}次")
        print(f"  最终霸凌强度: {metrics.get('bullying_intensity', 0):.0%}")
        print(f"  受害者压力: {metrics.get('victim_distress', 0):.0%}")
        
        # 干预情况
        print(self._bold("\n【教师干预】"))
        print(f"  干预次数: {report.get('intervention_count', 0)}")
        effect = metrics.get('intervention_effectiveness', 0)
        print(f"  平均效果: {effect:.0%}")
        
        # 能力评估
        print(self._bold("\n【能力评估】"))
        
        hostile = report.get('hostile_messages', 0)
        interventions = report.get('intervention_count', 0)
        
        # 发现能力
        if hostile > 0:
            print(f"  ✅ 发现能力: 能够识别霸凌行为")
        else:
            print(f"  ⚠️ 发现能力: 需要加强识别训练")
        
        # 干预能力
        if interventions > 0:
            effect_label = "优秀" if effect >= 0.7 else "良好" if effect >= 0.5 else "一般"
            print(f"  ✅ 干预能力: {effect_label} ({interventions}次干预)")
        else:
            print(f"  ⚠️ 干预能力: 需要更多实践")
        
        # 方法质量
        if effect >= 0.6:
            print(f"  ✅ 方法质量: 较好地运用了干预策略")
        elif effect >= 0.3:
            print(f"  ⚠️ 方法质量: 需要改进干预方法")
        else:
            print(f"  ⚠️ 方法质量: 建议学习更多干预策略")
        
        # 改进建议
        print(self._bold("\n【改进建议】"))
        
        suggestions = []
        
        if metrics.get('bullying_intensity', 0) > 0.5:
            suggestions.append("• 下次干预可以更早介入，避免霸凌升级")
        
        if metrics.get('victim_distress', 0) > 0.4:
            suggestions.append("• 关注受害者的情绪状态，提供更多支持")
        
        if interventions == 0:
            suggestions.append("• 勇于在适当时机进行干预")
        elif interventions < 2:
            suggestions.append("• 可以尝试更多的干预策略")
        
        if not suggestions:
            suggestions.append("• 继续保持良好的干预意识")
        
        for suggestion in suggestions:
            print(f"  {self._color(suggestion, 'cyan')}")
        
        print()


# ============================================================
# 报告生成器
# ============================================================

class ReportGenerator:
    """评估报告生成器"""
    
    def __init__(self, output_dir: str = None):
        self.output_dir = output_dir or "./logs"
    
    def generate_text_report(self, report_data: Dict, 
                            filename: str = None) -> str:
        """生成文本格式报告"""
        import os
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = filename or f"report_{timestamp}.txt"
        filepath = os.path.join(self.output_dir, filename)
        
        metrics = report_data.get("metrics", {})
        
        lines = [
            "=" * 60,
            "话语霸凌模拟实训 - 教师评估报告",
            "=" * 60,
            "",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "【霸凌情况】",
            f"  总消息数: {report_data.get('total_messages', 0)}",
            f"  攻击性事件: {report_data.get('hostile_messages', 0)}次",
            f"  最终霸凌强度: {metrics.get('bullying_intensity', 0):.0%}",
            f"  受害者压力: {metrics.get('victim_distress', 0):.0%}",
            "",
            "【教师干预】",
            f"  干预次数: {report_data.get('intervention_count', 0)}",
            f"  平均效果: {metrics.get('intervention_effectiveness', 0):.0%}",
            "",
            "【能力评估】",
        ]
        
        hostile = report_data.get('hostile_messages', 0)
        interventions = report_data.get('intervention_count', 0)
        effect = metrics.get('intervention_effectiveness', 0)
        
        lines.append(f"  发现能力: {'✅ 能够识别' if hostile > 0 else '⚠️ 需要加强'}")
        lines.append(f"  干预能力: {'✅ 已干预' if interventions > 0 else '⚠️ 需要实践'}")
        lines.append(f"  方法质量: {effect:.0%}")
        lines.append("")
        
        # 详细干预记录
        interventions_list = report_data.get("interventions", [])
        if interventions_list:
            lines.append("【干预详情】")
            for i, iv in enumerate(interventions_list, 1):
                lines.append(f"\n  干预 {i}:")
                lines.append(f"    内容: {iv.get('action', '')[:50]}...")
                iv_qs = iv.get("quality_score")
                lines.append(f"    质量: {'待评估' if iv_qs is None else f'{int(iv_qs)}/100'}")
                for fb in iv.get("feedback", []):
                    lines.append(f"    {fb}")
            lines.append("")
        
        # 消息历史
        messages = report_data.get("message_history", [])
        if messages:
            lines.append("【对话记录】")
            for msg in messages[-10:]:
                ts = datetime.fromtimestamp(msg.get("timestamp", 0))
                lines.append(f"  [{ts.strftime('%H:%M:%S')}] {msg.get('sender')}: {msg.get('content', '')[:40]}...")
            lines.append("")
        
        lines.append("=" * 60)
        lines.append("报告结束")
        lines.append("=" * 60)
        
        # 写入文件
        os.makedirs(self.output_dir, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        
        return filepath
    
    def generate_json_report(self, report_data: Dict,
                            filename: str = None) -> str:
        """生成 JSON 格式报告"""
        import json
        import os
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = filename or f"report_{timestamp}.json"
        filepath = os.path.join(self.output_dir, filename)
        
        # 添加元数据
        full_report = {
            "meta": {
                "generated_at": datetime.now().isoformat(),
                "report_version": "2.0",
            },
            "summary": {
                "total_messages": report_data.get("total_messages", 0),
                "hostile_messages": report_data.get("hostile_messages", 0),
                "intervention_count": report_data.get("intervention_count", 0),
            },
            "metrics": report_data.get("metrics", {}),
            "interventions": report_data.get("interventions", []),
            "message_history": report_data.get("message_history", []),
            "alerts": report_data.get("alerts", []),
            "ability_assessment": self._assess_abilities(report_data),
        }
        
        # 写入文件
        os.makedirs(self.output_dir, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(full_report, f, ensure_ascii=False, indent=2)
        
        return filepath
    
    def _assess_abilities(self, report_data: Dict) -> Dict:
        """评估教师能力"""
        hostile = report_data.get("hostile_messages", 0)
        interventions = report_data.get("intervention_count", 0)
        metrics = report_data.get("metrics", {})
        effect = metrics.get("intervention_effectiveness", 0)
        
        # 发现能力
        discovery_ability = "good" if hostile > 0 else "needs_improvement"
        
        # 干预能力
        if interventions == 0:
            intervention_ability = "needs_practice"
        elif effect >= 0.7:
            intervention_ability = "excellent"
        elif effect >= 0.5:
            intervention_ability = "good"
        else:
            intervention_ability = "needs_improvement"
        
        # 方法质量
        if effect >= 0.6:
            method_quality = "good"
        elif effect >= 0.3:
            method_quality = "fair"
        else:
            method_quality = "needs_improvement"
        
        return {
            "discovery": {
                "level": discovery_ability,
                "description": "能够识别霸凌行为" if discovery_ability == "good" else "需要加强识别能力",
            },
            "intervention": {
                "level": intervention_ability,
                "count": interventions,
                "effectiveness": effect,
            },
            "method": {
                "level": method_quality,
                "effectiveness": effect,
            },
        }
    
    def generate_html_report(self, report_data: Dict,
                             filename: str = None) -> str:
        """生成 HTML 格式报告"""
        import os
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = filename or f"report_{timestamp}.html"
        filepath = os.path.join(self.output_dir, filename)
        
        metrics = report_data.get("metrics", {})
        assessment = self._assess_abilities(report_data)
        
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>话语霸凌模拟实训报告</title>
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #333; border-bottom: 2px solid #4CAF50; }}
        h2 {{ color: #666; margin-top: 30px; }}
        .metric {{ background: #f5f5f5; padding: 15px; border-radius: 8px; margin: 10px 0; }}
        .score {{ font-size: 24px; font-weight: bold; }}
        .good {{ color: #4CAF50; }}
        .warning {{ color: #FF9800; }}
        .poor {{ color: #f44336; }}
        .progress {{ background: #e0e0e0; border-radius: 10px; height: 20px; }}
        .progress-bar {{ border-radius: 10px; height: 100%; }}
    </style>
</head>
<body>
    <h1>🎭 话语霸凌模拟实训报告</h1>
    <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    
    <h2>📊 霸凌情况</h2>
    <div class="metric">
        <p>总消息数: <strong>{report_data.get('total_messages', 0)}</strong></p>
        <p>攻击性事件: <strong>{report_data.get('hostile_messages', 0)}次</strong></p>
        <p>最终霸凌强度: <strong>{metrics.get('bullying_intensity', 0):.0%}</strong></p>
        <p>受害者压力: <strong>{metrics.get('victim_distress', 0):.0%}</strong></p>
    </div>
    
    <h2>👨‍🏫 教师干预</h2>
    <div class="metric">
        <p>干预次数: <strong>{report_data.get('intervention_count', 0)}</strong></p>
        <p>平均效果: <strong>{metrics.get('intervention_effectiveness', 0):.0%}</strong></p>
    </div>
    
    <h2>🎯 能力评估</h2>
    <div class="metric">
        <h3>发现能力</h3>
        <p class="{'good' if assessment['discovery']['level'] == 'good' else 'warning'}">
            {assessment['discovery']['description']}
        </p>
        
        <h3>干预能力</h3>
        <p class="{'good' if assessment['intervention']['level'] == 'excellent' else 'warning'}">
            {assessment['intervention']['count']}次干预
        </p>
        
        <h3>方法质量</h3>
        <p class="{'good' if assessment['method']['level'] == 'good' else 'warning'}">
            {assessment['method']['effectiveness']:.0%}
        </p>
    </div>
    
    <h2>📝 干预详情</h2>
"""
        
        interventions_list = report_data.get("interventions", [])
        for i, iv in enumerate(interventions_list, 1):
            html += f"""
    <div class="metric">
        <p><strong>干预 {i}</strong></p>
        <p>内容: {iv.get('action', '')[:50]}...</p>
        <p>质量: {('待评估' if iv.get('quality_score') is None else f"{int(iv.get('quality_score'))}/100")}</p>
    </div>
"""
        
        html += """
</body>
</html>
"""
        
        os.makedirs(self.output_dir, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)
        
        return filepath


# ============================================================
# 导出
# ============================================================

__all__ = [
    "InteractiveUI",
    "ReportGenerator",
    "InterventionLevel",
    "TeacherIntervention",
    "SimulationStatus",
]

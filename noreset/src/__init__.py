"""
🎭 校园话语霸凌与教师干预实训系统 v2.0 - 核心模块
==========================================

集成三大模块：
1. OpenClaw 多 Agent 协同
2. 教师交互界面
3. 评估报告生成
"""

__version__ = "2.0.0"

# 核心组件
from .orchestrator import (
    MultiAgentSimulator,
    EnhancedEvaluator,
    AgentConfig,
    PromptFactory,
    KnowledgeBase,
    Message,
    AgentState,
    EvaluationMetrics,
    ConversationContext,
    AgentRole,
    SimPhase,
    BullyingLevel,
    format_message_display,
    format_status_display,
)

# OpenClaw 集成
from .openclaw_integration import (
    OpenClawIntegration,
    LLMClient,
    AgentSession,
    DialogueMessage,
    AgentType,
)

# 交互界面
from .interactive_ui import (
    InteractiveUI,
    ReportGenerator,
    InterventionLevel,
    TeacherIntervention,
    SimulationStatus,
)

# 运行器
from .enhanced_runner import EnhancedSimulationRunner

__all__ = [
    # 核心组件
    "MultiAgentSimulator",
    "EnhancedEvaluator",
    "AgentConfig",
    "PromptFactory",
    "KnowledgeBase",
    "Message",
    "AgentState",
    "EvaluationMetrics",
    "ConversationContext",
    "AgentRole",
    "SimPhase",
    "BullyingLevel",
    "format_message_display",
    "format_status_display",
    
    # OpenClaw 集成
    "OpenClawIntegration",
    "LLMClient",
    "AgentSession",
    "DialogueMessage",
    "AgentType",
    
    # 交互界面
    "InteractiveUI",
    "ReportGenerator",
    "InterventionLevel",
    "TeacherIntervention",
    "SimulationStatus",
    
    # 运行器
    "EnhancedSimulationRunner",
]

#!/usr/bin/env python3
"""
🎭 校园话语霸凌与教师干预实训系统 v2.0 - OpenClaw 集成模块
================================================
使用 OpenClaw sessions 实现真正的多 Agent 协同

功能：
1. 多 Agent Session 管理
2. LLM API 集成
3. 消息驱动架构
"""

import json
import time
import asyncio
from typing import Dict, List, Optional, Any, Callable
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum


class AgentType(Enum):
    """Agent 类型"""
    BULLY = "bully"           # 霸凌者
    VICTIM = "victim"         # 被霸凌者
    BYSTANDER_A = "bystander_a"  # 正义旁观者
    BYSTANDER_B = "bystander_b"  # 从众旁观者
    EVALUATOR = "evaluator"     # 评估者
    COORDINATOR = "coordinator"  # 协调者


@dataclass
class AgentSession:
    """Agent Session 信息"""
    agent_id: str
    agent_type: AgentType
    session_key: str = ""
    label: str = ""
    is_active: bool = False
    last_message_at: float = 0
    
    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type.value,
            "session_key": self.session_key,
            "label": self.label,
            "is_active": self.is_active,
            "last_message_at": self.last_message_at,
        }


@dataclass 
class DialogueMessage:
    """对话消息"""
    id: str
    sender: str
    agent_type: AgentType
    content: str
    timestamp: float
    metadata: Dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sender": self.sender,
            "agent_type": self.agent_type.value,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


class OpenClawIntegration:
    """
    OpenClaw 集成管理器
    
    使用 OpenClaw 的 sessions API 实现多 Agent 协同：
    - sessions_spawn: 创建 Agent Session
    - sessions_send: 向 Agent 发送消息
    - sessions_history: 获取 Agent 历史
    - sessions_list: 列出所有 Session
    """
    
    # Session 标签前缀
    LABEL_PREFIX = "bullying_sim"
    
    # Agent 配置
    AGENT_CONFIGS = {
        "chenhao": {
            "type": AgentType.BULLY,
            "name": "陈浩",
            "role": "霸凌者",
            "label": "bully",
        },
        "siyuan": {
            "type": AgentType.VICTIM,
            "name": "思远",
            "role": "被霸凌者",
            "label": "victim",
        },
        "linyutong": {
            "type": AgentType.BYSTANDER_A,
            "name": "林雨桐",
            "role": "正义旁观者",
            "label": "bystander_a",
        },
        "zhaolei": {
            "type": AgentType.BYSTANDER_B,
            "name": "赵磊",
            "role": "从众旁观者",
            "label": "bystander_b",
        },
    }
    
    def __init__(self, workspace: str = None):
        self.workspace = Path(workspace) if workspace else Path(__file__).parent.parent
        self.sessions: Dict[str, AgentSession] = {}
        self.message_history: List[DialogueMessage] = []
        self.callbacks: Dict[str, Callable] = {}
        
        # OpenClaw API 可用性
        self._openclaw_available = self._check_openclaw()
    
    def _check_openclaw(self) -> bool:
        """检查 OpenClaw API 是否可用"""
        try:
            # 尝试导入 OpenClaw SDK
            import openclaw
            return True
        except ImportError:
            return False
    
    @property
    def is_available(self) -> bool:
        """OpenClaw 是否可用"""
        return self._openclaw_available
    
    async def initialize_sessions(self, prompts: Dict[str, str]) -> Dict[str, AgentSession]:
        """
        初始化所有 Agent Sessions
        
        Args:
            prompts: {agent_id: system_prompt} 格式的提示词字典
        
        Returns:
            {agent_id: AgentSession} 格式的 Session 字典
        """
        if not self._openclaw_available:
            print("⚠️ OpenClaw SDK 不可用，使用模拟模式")
            return self._init_mock_sessions()
        
        print("🚀 初始化 OpenClaw Agent Sessions...")
        
        try:
            from openclaw import sessions_spawn
            
            for agent_id, config in self.AGENT_CONFIGS.items():
                prompt = prompts.get(agent_id, "")
                label = f"{self.LABEL_PREFIX}_{config['label']}"
                
                try:
                    # 创建 Session
                    result = await sessions_spawn(
                        task=prompt,
                        label=label,
                        runtime="subagent",
                        mode="session",
                        cleanup="keep",
                    )
                    
                    session = AgentSession(
                        agent_id=agent_id,
                        agent_type=config["type"],
                        session_key=result.get("sessionKey", ""),
                        label=label,
                        is_active=True,
                    )
                    
                    self.sessions[agent_id] = session
                    print(f"   ✅ {config['name']} ({config['role']}) Session 已创建")
                    
                except Exception as e:
                    print(f"   ❌ {config['name']} Session 创建失败: {e}")
                    self.sessions[agent_id] = AgentSession(
                        agent_id=agent_id,
                        agent_type=config["type"],
                        is_active=False,
                    )
            
            print("✅ Agent Sessions 初始化完成!")
            return self.sessions
            
        except Exception as e:
            print(f"❌ OpenClaw Session 初始化失败: {e}")
            return self._init_mock_sessions()
    
    def _init_mock_sessions(self) -> Dict[str, AgentSession]:
        """初始化模拟 Sessions（当 OpenClaw 不可用时）"""
        print("🔧 初始化模拟 Sessions...")
        
        for agent_id, config in self.AGENT_CONFIGS.items():
            session = AgentSession(
                agent_id=agent_id,
                agent_type=config["type"],
                session_key=f"mock_{agent_id}",
                label=f"{self.LABEL_PREFIX}_{config['label']}",
                is_active=True,
            )
            self.sessions[agent_id] = session
            print(f"   ✅ {config['name']} (模拟模式)")
        
        return self.sessions
    
    async def send_to_agent(self, agent_id: str, message: str, 
                           timeout: int = 30) -> Optional[str]:
        """
        向指定 Agent 发送消息并获取响应
        
        Args:
            agent_id: Agent ID
            message: 发送的消息
            timeout: 超时时间（秒）
        
        Returns:
            Agent 的响应内容
        """
        if agent_id not in self.sessions:
            return None
        
        session = self.sessions[agent_id]
        
        # 模拟模式
        if not session.is_active or not session.session_key:
            return await self._mock_response(agent_id, message)
        
        # OpenClaw 模式
        try:
            from openclaw import sessions_send
            
            result = await sessions_send(
                sessionKey=session.session_key,
                message=message,
                timeoutSeconds=timeout,
            )
            
            session.last_message_at = time.time()
            return result.get("message", "")
            
        except Exception as e:
            print(f"⚠️ 向 {agent_id} 发送消息失败: {e}")
            return await self._mock_response(agent_id, message)
    
    async def _mock_response(self, agent_id: str, context: str) -> str:
        """
        模拟 Agent 响应
        
        实际部署时应使用真实的 LLM API 调用
        """
        import random
        
        responses = {
            "chenhao": [
                "切，有什么了不起的。",
                "哈哈哈，这画的是什么垃圾啊！",
                "思远你家穷成这样还画画呢？",
            ],
            "siyuan": [
                "（低着头不说话）",
                "（小声）我...我没有...",
                "对不起...",
            ],
            "linyutong": [
                "陈浩，你这样太过分了。",
                "思远的画挺好的啊。",
                "大家都是同学，何必呢。",
            ],
            "zhaolei": [
                "（跟着笑）哈哈哈...",
                "好像是有点...",
                "（小声附和）",
            ],
        }
        
        return random.choice(responses.get(agent_id, ["..."]))
    
    async def get_agent_history(self, agent_id: str, 
                               limit: int = 10) -> List[Dict]:
        """获取 Agent 历史消息"""
        if agent_id not in self.sessions:
            return []
        
        session = self.sessions[agent_id]
        
        if not session.is_active or not session.session_key:
            return []
        
        try:
            from openclaw import sessions_history
            
            history = await sessions_history(
                sessionKey=session.session_key,
                limit=limit,
            )
            return history.get("messages", [])
            
        except Exception as e:
            print(f"⚠️ 获取 {agent_id} 历史失败: {e}")
            return []
    
    async def list_active_sessions(self) -> List[AgentSession]:
        """列出所有活跃 Session"""
        return [s for s in self.sessions.values() if s.is_active]
    
    def add_message(self, sender: str, agent_type: AgentType, 
                   content: str, metadata: Dict = None) -> DialogueMessage:
        """添加消息到历史"""
        import uuid
        
        msg = DialogueMessage(
            id=str(uuid.uuid4())[:8],
            sender=sender,
            agent_type=agent_type,
            content=content,
            timestamp=time.time(),
            metadata=metadata or {},
        )
        
        self.message_history.append(msg)
        return msg
    
    def get_recent_messages(self, count: int = 10, 
                           exclude_agent: str = None) -> List[DialogueMessage]:
        """获取最近消息"""
        msgs = self.message_history[-count:]
        
        if exclude_agent:
            msgs = [m for m in msgs if m.sender != exclude_agent]
        
        return msgs
    
    def build_context_for_agent(self, agent_id: str) -> str:
        """为指定 Agent 构建上下文"""
        if agent_id not in self.AGENT_CONFIGS:
            return ""
        
        config = self.AGENT_CONFIGS[agent_id]
        recent = self.get_recent_messages(count=6, exclude_agent=config["name"])
        
        if not recent:
            return "（对话即将开始）"
        
        lines = [f"【{msg.sender}】{msg.content}" for msg in recent]
        return "\n".join(lines)
    
    def get_all_sessions_status(self) -> Dict[str, Dict]:
        """获取所有 Session 状态"""
        return {
            agent_id: session.to_dict()
            for agent_id, session in self.sessions.items()
        }
    
    async def cleanup(self):
        """清理所有 Sessions"""
        for agent_id, session in self.sessions.items():
            if session.is_active and session.session_key:
                print(f"   清理 {agent_id} Session...")
                # 在实际实现中可能需要删除 Session
                session.is_active = False


# ============================================================
# LLM API 客户端（支持多种后端）
# ============================================================

class LLMClient:
    """
    LLM API 客户端
    
    支持：
    - OpenAI API
    - Anthropic API
    - MiniMax API
    - OpenClaw 本地调用
    """
    
    def __init__(self, provider: str = "openclaw", config: Dict = None):
        self.provider = provider
        self.config = config or {}
        self._client = None
        self._init_client()
    
    def _init_client(self):
        """初始化客户端"""
        if self.provider == "openclaw":
            self._init_openclaw_client()
        elif self.provider == "openai":
            self._init_openai_client()
        elif self.provider == "anthropic":
            self._init_anthropic_client()
        elif self.provider == "minimax":
            self._init_minimax_client()
    
    def _init_openclaw_client(self):
        """初始化 OpenClaw 客户端"""
        try:
            import openclaw
            self._client = openclaw
        except ImportError:
            print("⚠️ OpenClaw SDK 不可用")
    
    def _init_openai_client(self):
        """初始化 OpenAI 客户端"""
        try:
            from openai import OpenAI
            api_key = self.config.get("api_key", "")
            self._client = OpenAI(api_key=api_key)
        except ImportError:
            print("⚠️ OpenAI SDK 不可用")
    
    def _init_anthropic_client(self):
        """初始化 Anthropic 客户端"""
        try:
            from anthropic import Anthropic
            api_key = self.config.get("api_key", "")
            self._client = Anthropic(api_key=api_key)
        except ImportError:
            print("⚠️ Anthropic SDK 不可用")
    
    def _init_minimax_client(self):
        """初始化 MiniMax 客户端"""
        # MiniMax 使用 OpenAI 兼容 API
        self._init_openai_client()
    
    async def generate(self, prompt: str, system: str = None,
                      model: str = "gpt-4", 
                      max_tokens: int = 1000,
                      temperature: float = 0.8) -> str:
        """
        生成响应
        
        Args:
            prompt: 用户 prompt
            system: 系统 prompt
            model: 模型名称
            max_tokens: 最大 token 数
            temperature: 温度参数
        
        Returns:
            生成的文本
        """
        if self.provider == "openclaw":
            return await self._generate_openclaw(prompt, system, model)
        elif self.provider == "openai":
            return self._generate_openai(prompt, system, model, max_tokens, temperature)
        elif self.provider == "anthropic":
            return self._generate_anthropic(prompt, system, model, max_tokens)
        elif self.provider == "minimax":
            return self._generate_minimax(prompt, system, model, max_tokens, temperature)
        
        return "（模拟响应）"
    
    async def _generate_openclaw(self, prompt: str, system: str, 
                                model: str) -> str:
        """使用 OpenClaw 生成"""
        if not self._client:
            return await self._mock_generate(prompt)
        
        try:
            from openclaw import sessions_spawn
            
            result = await sessions_spawn(
                task=f"{system}\n\n{prompt}" if system else prompt,
                label=f"llm_generate_{int(time.time())}",
                runtime="subagent",
                mode="run",
            )
            
            return result.get("message", "")
        except Exception as e:
            print(f"⚠️ OpenClaw 生成失败: {e}")
            return await self._mock_generate(prompt)
    
    def _generate_openai(self, prompt: str, system: str, model: str,
                        max_tokens: int, temperature: float) -> str:
        """使用 OpenAI API 生成"""
        if not self._client:
            return "（OpenAI API 未配置）"
        
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"⚠️ OpenAI 生成失败: {e}")
            return ""
    
    def _generate_anthropic(self, prompt: str, system: str, model: str,
                           max_tokens: int) -> str:
        """使用 Anthropic API 生成"""
        if not self._client:
            return "（Anthropic API 未配置）"
        
        try:
            response = self._client.messages.create(
                model=model,
                system=system,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as e:
            print(f"⚠️ Anthropic 生成失败: {e}")
            return ""
    
    def _generate_minimax(self, prompt: str, system: str, model: str,
                          max_tokens: int, temperature: float) -> str:
        """使用 MiniMax API 生成"""
        # MiniMax 使用与 OpenAI 兼容的接口
        base_url = self.config.get("base_url", "https://api.minimax.io/v1")
        
        if not self._client:
            return "（MiniMax API 未配置）"
        
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system} if system else None,
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"⚠️ MiniMax 生成失败: {e}")
            return ""
    
    async def _mock_generate(self, prompt: str) -> str:
        """模拟生成"""
        import random
        responses = [
            "这是一个模拟响应。",
            "（沉默）",
            "我觉得...",
        ]
        return random.choice(responses)


# ============================================================
# 导出
# ============================================================

__all__ = [
    "OpenClawIntegration",
    "LLMClient",
    "AgentSession",
    "DialogueMessage",
    "AgentType",
]

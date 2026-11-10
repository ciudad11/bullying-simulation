#!/usr/bin/env python3
"""
🎭 校园话语霸凌与教师干预实训系统 - Agent 长期记忆管理
============================================
为每个 Agent 角色维护长期记忆，跟踪学生的发展变化

功能：
1. Agent Profile 持久化存储
2. 对话历史摘要
3. 心理状态变化追踪
4. 行为模式学习
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime
from collections import defaultdict


# ============================================================
# 路径配置
# ============================================================
BASE_DIR = Path(__file__).parent.parent
MEMORY_DIR = BASE_DIR / "agent_memory"
PROFILES_DIR = MEMORY_DIR / "profiles"
HISTORY_DIR = MEMORY_DIR / "history"

for d in [MEMORY_DIR, PROFILES_DIR, HISTORY_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ============================================================
# 数据类
# ============================================================
@dataclass
class EmotionalState:
    """情绪状态"""
    mood: str  # neutral, sad, anxious, fearful, angry, hopeful
    intensity: float  # 0-1
    triggers: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class BehaviorPattern:
    """行为模式"""
    pattern_type: str  # aggression, retreat, support, silence
    frequency: int = 1
    context: List[str] = field(default_factory=list)  # 在什么情况下出现
    last_shown: float = field(default_factory=time.time)


@dataclass
class AgentProfile:
    """Agent 档案 - 长期记忆的核心"""
    agent_id: str
    name: str
    role: str
    
    # 基础信息
    created_at: float = field(default_factory=time.time)
    last_session_at: float = field(default_factory=time.time)
    total_sessions: int = 0
    
    # 心理状态
    current_mood: str = "neutral"
    mood_history: List[Dict] = field(default_factory=list)  # [{"mood": "...", "timestamp": ..., "context": "..."}]
    stress_level: float = 0.0  # 0-1
    
    # 行为发展
    aggression_level: float = 0.0  # 霸凌倾向 0-1
    supportiveness: float = 0.0  # 支持性 0-1
    resilience_level: float = 0.0  # 心理韧性 0-1
    
    # 行为模式库
    behavior_patterns: List[Dict] = field(default_factory=list)
    
    # 关系记忆
    relationships: Dict[str, Dict] = field(default_factory=dict)  # 对其他角色的印象
    
    # 重要事件
    significant_events: List[Dict] = field(default_factory=list)  # 重大事件记录
    
    # 学习进度
    learning_progress: Dict = field(default_factory=dict)  # 在干预中学到什么
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'AgentProfile':
        return cls(**data)
    
    def add_significant_event(self, event_type: str, description: str, context: dict = None):
        """记录重大事件"""
        self.significant_events.append({
            "type": event_type,
            "description": description,
            "context": context or {},
            "timestamp": time.time(),
            "session_count": self.total_sessions
        })
        # 只保留最近 20 个重大事件
        if len(self.significant_events) > 20:
            self.significant_events = self.significant_events[-20:]


@dataclass
class SessionSummary:
    """会话摘要 - 用于长期记忆"""
    session_id: str
    agent_id: str
    start_time: float
    end_time: Optional[float] = None
    
    # 对话统计
    messages_sent: int = 0
    words_spoken: int = 0
    
    # 行为表现
    mood_changes: List[Dict] = field(default_factory=list)
    key_behaviors: List[str] = field(default_factory=list)
    
    # 评估
    intervention_effectiveness: float = 0.0
    notes: str = ""
    
    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# Agent 记忆管理器
# ============================================================
class AgentMemory:
    """
    Agent 长期记忆管理器
    
    为每个角色维护独立的记忆文件，跟踪其发展变化
    """
    
    # 角色对应的 Agent ID
    AGENT_IDS = ["chenhao", "siyuan", "linyutong", "zhaolei"]
    
    def __init__(self):
        self.profiles: Dict[str, AgentProfile] = {}
        self.current_session_summaries: Dict[str, SessionSummary] = {}
        self._load_all_profiles()
    
    def _get_profile_path(self, agent_id: str) -> Path:
        """获取档案文件路径"""
        return PROFILES_DIR / f"{agent_id}_profile.json"
    
    def _get_history_dir(self, agent_id: str) -> Path:
        """获取历史目录"""
        agent_history = HISTORY_DIR / agent_id
        agent_history.mkdir(parents=True, exist_ok=True)
        return agent_history
    
    def _load_all_profiles(self):
        """加载所有 Agent 档案"""
        for agent_id in self.AGENT_IDS:
            self.profiles[agent_id] = self.load_profile(agent_id)
    
    def load_profile(self, agent_id: str) -> AgentProfile:
        """加载单个 Agent 档案"""
        path = self._get_profile_path(agent_id)
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return AgentProfile.from_dict(data)
            except Exception as e:
                print(f"加载档案失败 {agent_id}: {e}")
        
        # 返回默认档案
        return self._create_default_profile(agent_id)
    
    def _create_default_profile(self, agent_id: str) -> AgentProfile:
        """创建默认档案"""
        defaults = {
            "chenhao": {"name": "陈浩", "role": "霸凌者"},
            "siyuan": {"name": "思远", "role": "被霸凌者"},
            "linyutong": {"name": "林雨桐", "role": "正义旁观者"},
            "zhaolei": {"name": "赵磊", "role": "从众旁观者"},
        }
        info = defaults.get(agent_id, {"name": agent_id, "role": "unknown"})
        return AgentProfile(agent_id=agent_id, **info)
    
    def save_profile(self, agent_id: str):
        """保存 Agent 档案"""
        profile = self.profiles.get(agent_id)
        if not profile:
            return
        
        profile.last_session_at = time.time()
        path = self._get_profile_path(agent_id)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
    
    def save_all_profiles(self):
        """保存所有档案"""
        for agent_id in self.profiles:
            self.save_profile(agent_id)
    
    # ============================================================
    # 会话管理
    # ============================================================
    def start_session(self, agent_id: str, session_id: str):
        """开始新的会话"""
        # 更新会话计数
        if agent_id in self.profiles:
            self.profiles[agent_id].total_sessions += 1
        
        # 创建会话摘要
        self.current_session_summaries[agent_id] = SessionSummary(
            session_id=session_id,
            agent_id=agent_id,
            start_time=time.time()
        )
    
    def end_session(self, agent_id: str):
        """结束会话并保存历史"""
        summary = self.current_session_summaries.get(agent_id)
        if not summary:
            return
        
        summary.end_time = time.time()
        
        # 保存到历史
        history_dir = self._get_history_dir(agent_id)
        history_file = history_dir / f"{summary.session_id}.json"
        
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(summary.to_dict(), f, ensure_ascii=False, indent=2)
        
        # 更新长期记忆
        self._update_long_term_memory(agent_id, summary)
        
        # 保存档案
        self.save_profile(agent_id)
        
        # 清理当前会话
        del self.current_session_summaries[agent_id]
    
    def _update_long_term_memory(self, agent_id: str, summary: SessionSummary):
        """更新长期记忆"""
        profile = self.profiles.get(agent_id)
        if not profile:
            return
        
        # 更新行为模式
        for behavior in summary.key_behaviors:
            self._update_behavior_pattern(profile, behavior)
        
        # 更新情绪状态
        if summary.mood_changes:
            last_mood = summary.mood_changes[-1]
            profile.current_mood = last_mood.get("mood", "neutral")
            profile.mood_history.append({
                "mood": profile.current_mood,
                "timestamp": time.time(),
                "session_id": summary.session_id,
                "intensity": last_mood.get("intensity", 0.5)
            })
    
    def _update_behavior_pattern(self, profile: AgentProfile, behavior: str):
        """更新行为模式"""
        behavior = behavior.lower()
        
        # 查找现有模式
        for pattern in profile.behavior_patterns:
            if pattern["pattern_type"] == behavior:
                pattern["frequency"] += 1
                pattern["last_shown"] = time.time()
                return
        
        # 添加新模式
        profile.behavior_patterns.append({
            "pattern_type": behavior,
            "frequency": 1,
            "last_shown": time.time(),
            "context": []
        })
    
    # ============================================================
    # 行为记录
    # ============================================================
    def record_message(self, agent_id: str, content: str, mood: str = None):
        """记录 Agent 发送的消息"""
        if agent_id not in self.current_session_summaries:
            return
        
        summary = self.current_session_summaries[agent_id]
        summary.messages_sent += 1
        summary.words_spoken += len(content)
        
        # 记录情绪变化
        if mood:
            summary.mood_changes.append({
                "mood": mood,
                "timestamp": time.time(),
                "context": content[:50]  # 记录前50字作为上下文
            })
    
    def record_behavior(self, agent_id: str, behavior: str):
        """记录关键行为"""
        if agent_id not in self.current_session_summaries:
            return
        
        summary = self.current_session_summaries[agent_id]
        if behavior not in summary.key_behaviors:
            summary.key_behaviors.append(behavior)
    
    def record_intervention_effect(self, agent_id: str, effectiveness: float, intervention: str):
        """记录干预效果"""
        if agent_id not in self.current_session_summaries:
            return
        
        summary = self.current_session_summaries[agent_id]
        summary.intervention_effectiveness = effectiveness
        
        # 更新学习进度
        profile = self.profiles.get(agent_id)
        if profile:
            if "interventions_received" not in profile.learning_progress:
                profile.learning_progress["interventions_received"] = []
            profile.learning_progress["interventions_received"].append({
                "effectiveness": effectiveness,
                "content": intervention,
                "timestamp": time.time()
            })
    
    # ============================================================
    # 记忆查询
    # ============================================================
    def get_agent_context(self, agent_id: str) -> str:
        """生成 Agent 的记忆上下文（用于 prompt）"""
        profile = self.profiles.get(agent_id)
        if not profile:
            return ""
        
        context_parts = []
        
        # 1. 基础信息
        context_parts.append(f"【{profile.name}的历史档案】")
        context_parts.append(f"角色: {profile.role}")
        context_parts.append(f"参与模拟次数: {profile.total_sessions}")
        
        # 2. 当前心理状态
        context_parts.append(f"当前情绪: {profile.current_mood}")
        if profile.mood_history:
            recent = profile.mood_history[-3:]
            moods = [f"{m['mood']}(第{self._session_num(m.get('session_id'))}次)" for m in recent]
            context_parts.append(f"最近情绪变化: {' -> '.join(moods)}")
        
        # 3. 行为模式
        if profile.behavior_patterns:
            patterns = sorted(profile.behavior_patterns, key=lambda x: x['frequency'], reverse=True)[:3]
            pattern_strs = [f"{p['pattern_type']}(出现{p['frequency']}次)" for p in patterns]
            context_parts.append(f"主要行为模式: {', '.join(pattern_strs)}")
        
        # 4. 重大事件
        if profile.significant_events:
            recent_events = profile.significant_events[-2:]
            for event in recent_events:
                context_parts.append(f"印象深刻的事件: {event['description']}")
        
        # 5. 学习进度
        if profile.learning_progress.get("interventions_received"):
            count = len(profile.learning_progress["interventions_received"])
            context_parts.append(f"经历干预次数: {count}")
        
        # 6. 关系记忆
        if profile.relationships:
            rel_parts = []
            for other_id, rel in list(profile.relationships.items())[:2]:
                rel_parts.append(f"{other_id}: {rel.get('impression', '一般')}")
            if rel_parts:
                context_parts.append(f"对其他人的印象: {', '.join(rel_parts)}")
        
        return "\n".join(context_parts)
    
    def _session_num(self, session_id: str) -> str:
        """从 session_id 提取会话序号"""
        if not session_id:
            return "?"
        # session_id 通常是时间戳
        return session_id[:8]
    
    def update_relationship(self, agent_id: str, other_id: str, impression: str, event: str = None):
        """更新对其他角色的印象"""
        profile = self.profiles.get(agent_id)
        if not profile:
            return
        
        if agent_id not in profile.relationships:
            profile.relationships[agent_id] = {}
        
        profile.relationships[other_id] = {
            "impression": impression,
            "last_updated": time.time(),
            "last_event": event
        }
    
    # ============================================================
    # 统计分析
    # ============================================================
    def get_development_report(self, agent_id: str) -> Dict:
        """获取角色发展报告"""
        profile = self.profiles.get(agent_id)
        if not profile:
            return {}
        
        # 计算行为趋势
        behavior_trends = self._calculate_behavior_trends(agent_id)
        
        return {
            "agent_id": agent_id,
            "name": profile.name,
            "role": profile.role,
            "total_sessions": profile.total_sessions,
            "current_state": {
                "mood": profile.current_mood,
                "stress_level": profile.stress_level,
            },
            "behavior_trends": behavior_trends,
            "significant_events": profile.significant_events[-5:],
            "learning_progress": profile.learning_progress,
        }
    
    def _calculate_behavior_trends(self, agent_id: str) -> Dict:
        """计算行为趋势"""
        # 读取最近几次会话
        history_dir = self._get_history_dir(agent_id)
        sessions = sorted(history_dir.glob("*.json"))[-5:]
        
        trends = {
            "aggression": [],
            "support": [],
            "retreat": []
        }
        
        for session_file in sessions:
            try:
                with open(session_file, 'r') as f:
                    data = json.load(f)
                
                behaviors = data.get("key_behaviors", [])
                for b in behaviors:
                    b = b.lower()
                    if "攻击" in b or "aggress" in b:
                        trends["aggression"].append(1)
                    elif "支持" in b or "support" in b:
                        trends["support"].append(1)
                    elif "退缩" in b or "retreat" in b:
                        trends["retreat"].append(1)
            except:
                continue
        
        # 计算平均值
        for key in trends:
            if trends[key]:
                trends[key] = sum(trends[key]) / len(trends[key])
            else:
                trends[key] = 0
        
        return trends


# ============================================================
# 全局实例
# ============================================================
memory_manager = AgentMemory()

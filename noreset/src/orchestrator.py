#!/usr/bin/env python3
"""
🎭 校园话语霸凌与教师干预实训系统 v2.0 - 多Agent协同引擎
============================================
基于 OpenClaw 多 Agent 协同框架

核心功能：
1. 多 Agent 独立决策与消息驱动
2. 知识增强的角色扮演
3. 实时评估与可视化
4. 教师干预训练
"""

import json
import time
import uuid
import asyncio
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum

# ============================================================
# 路径配置
# ============================================================
BASE_DIR = Path(__file__).parent.parent
LOGS_DIR = BASE_DIR / "logs"
STUDENTS_DIR = BASE_DIR / "students"
PROMPTS_DIR = BASE_DIR / "prompts"

for d in [LOGS_DIR, STUDENTS_DIR, PROMPTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ============================================================
# 评估器配置加载
# ============================================================
def load_config():
    """加载评估器配置文件"""
    config_path = BASE_DIR / "config" / "orchestrator.yaml"
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"警告: 无法加载配置文件 {config_path}: {e}")
        return {}

CONFIG = load_config()

def cfg(path: str, default=None):
    """便捷配置访问，支持嵌套路径如 'victim_distress.sad_emotion'"""
    keys = path.split('.')
    val = CONFIG
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
        if val is None:
            return default
    return val


# ============================================================
# 枚举定义
# ============================================================
class BullyingLevel(Enum):
    """霸凌级别"""
    MINOR = 1      # 轻微
    GENERAL = 2     # 一般
    MODERATE = 3   # 中等
    SEVERE = 4      # 严重
    EXTREME = 5     # 极其严重


class AgentRole(Enum):
    """Agent 角色"""
    BULLY = "bully"           # 霸凌者
    VICTIM = "victim"         # 被霸凌者
    BYSTANDER_A = "bystander_a"  # 旁观者 - 仗义型
    BYSTANDER_B = "bystander_b"  # 旁观者 - 从众型
    TEACHER = "teacher"        # 老师（教师）


class SimPhase(Enum):
    """模拟阶段"""
    SETUP = "setup"
    CONVERSATION = "conversation"
    INTERVENTION = "intervention"
    POST_INTERVENTION = "post_intervention"
    EVALUATION = "evaluation"
    FINISHED = "finished"


# ============================================================
# 数据类
# ============================================================
@dataclass
class Message:
    """消息结构"""
    id: str
    sender: str
    sender_id: str
    sender_role: str
    content: str
    msg_type: str  # speak, intervention, evaluation, system
    timestamp: float
    metadata: dict = field(default_factory=dict)
    
    @staticmethod
    def create(sender: str, sender_id: str, sender_role: str, 
               content: str, msg_type: str = "speak", metadata: dict = None):
        return Message(
            id=str(uuid.uuid4())[:8],
            sender=sender,
            sender_id=sender_id,
            sender_role=sender_role,
            content=content,
            msg_type=msg_type,
            timestamp=time.time(),
            metadata=metadata or {}
        )


@dataclass
class AgentState:
    """Agent 状态"""
    agent_id: str
    name: str
    role: str
    personality: dict
    knowledge: dict
    speaking_style: List[str]
    current_mood: str = "neutral"
    mood_history: List[dict] = field(default_factory=list)
    speaking_count: int = 0
    last_spoke_at: float = 0
    session_key: str = ""
    is_active: bool = True
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvaluationMetrics:
    """评估指标"""
    bullying_intensity: float = 0.0      # 0-1, 霸凌强度
    victim_distress: float = 0.0        # 0-1, 受害者压力
    bystander_intervention: float = 0.0  # 0-1, 旁观者干预指数
    emotional_escalation: float = 0.0    # 0-1, 情绪升级
    intervention_effectiveness: float = 0.0  # 0-1, 干预效果
    
    # 计数
    hostile_message_count: int = 0
    total_message_count: int = 0
    bystander_support_count: int = 0
    
    # 干预时机
    intervention_timing_score: float = 0.0
    intervention_method_score: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "bullying_intensity": self.bullying_intensity,
            "victim_distress": self.victim_distress,
            "bystander_intervention": self.bystander_intervention,
            "emotional_escalation": self.emotional_escalation,
            "intervention_effectiveness": self.intervention_effectiveness,
            "hostile_message_count": self.hostile_message_count,
            "total_message_count": self.total_message_count,
            "bystander_support_count": self.bystander_support_count,
            "intervention_timing_score": self.intervention_timing_score,
            "intervention_method_score": self.intervention_method_score,
        }


@dataclass
class ConversationContext:
    """对话上下文"""
    level: int
    scenario: str
    location: str
    participants: List[str]
    recent_messages: List[dict] = field(default_factory=list)
    atmosphere: str = "正常"
    
    def to_prompt_context(self) -> str:
        """转换为 prompt 上下文"""
        msgs = "\n".join([
            f"【{m['sender']}】{m['content'][:100]}"
            for m in self.recent_messages[-6:]
        ])
        return f"""【场景】
位置：{self.location}
参与者：{', '.join(self.participants)}
氛围：{self.atmosphere}

【最近对话】
{msgs if msgs else "（暂无对话）"}
"""


# ============================================================
# 知识库
# ============================================================
class KnowledgeBase:
    """话语霸凌知识库"""
    
    # 攻击性词汇分类
    HOSTILE_WORDS = {
        "嘲讽": ["蠢", "笨", "傻", "可笑", "笑话", "丑", "土", "土鳖", "傻逼", "智障"],
        "贬低": ["垃圾", "废物", "没用", "没出息", "弱智", "智障", "贱", "贱人", "下贱"],
        "侮辱": ["恶心", "有病", "神经病", "白痴", "蠢货", "不要脸", "恬不知耻"],
        "威胁": ["去死", "该死", "活该", "给我闭嘴", "闭嘴", "滚开", "弄死你", "打死你"],
        "排斥": ["滚", "别来", "关你什么事", "多管闲事", "别管", "少来"],
        "嘲笑外貌": ["丑", "肥", "乡巴佬", "土包子", "难看", "恶心吧啦", "肥猪"],
        "嘲笑背景": ["穷", "土", "破", "穷逼", "穷鬼", "寒酸", "买不起", "穷酸", "下等人", "贱民"],
        "嘲笑努力": ["想", "还敢", "配", "凭你", "你也配", "想什么呢", "做梦", "做梦吧"],
        "霸凌意图": ["欺负", "霸凌", "整你", "搞你", "整死你", "搞死你"],
    }

    # 支持性词汇
    SUPPORTIVE_WORDS = [
        "对不起", "抱歉", "不好意思", "谢谢", "支持", "鼓励",
        "没关系", "不要紧", "理解", "谅解", "加油", "你可以的",
        "我们都支持你", "一起", "互相帮助", "换位思考",
        "不对", "不能这样", "住手", "够了", "别说了", "你太过分了",
        "思远没错", "思远没做错", "思远很好", "别理他们",
        "我觉得", "我觉得这样不好", "不应该这样", "请停止",
        "这样不对", "你们错了", "陈浩你太过分了",
    ]
    
    # 霸凌心理学知识
    BULLYING_PSYCHOLOGY = """
【霸凌心理学知识】
1. 霸凌动机：
   - 寻求权力感和控制感
   - 低自尊，通过贬低他人提升自我
   - 缺乏同理心
   - 社交学习（从环境习得）
   - 家庭因素（缺乏关爱、暴力环境）

2. 霸凌类型：
   - 语言霸凌：讽刺、嘲笑、威胁、起绰号
   - 社交霸凌：排斥、散布谣言、破坏关系
   - 心理霸凌：威胁、恐吓、精神控制

3. 权力不平衡：
   - 霸凌者通常社交地位较高
   - 被霸凌者处于弱势地位
   - 旁观者态度影响事件发展
"""
    
    # 受害者心理学
    VICTIM_PSYCHOLOGY = """
【受害者心理学知识】
1. 常见特征：
   - 内向敏感
   - 低自尊、自我认同感低
   - 缺乏社交技能或支持网络
   - 有某些"不同"（外貌、兴趣、背景）

2. 心理反应：
   - 自我否定（"是不是我做错了什么"）
   - 恐惧和焦虑
   - 孤独感和无助感
   - 学业成绩下降
   - 行为退缩或极端反应

3. 健康应对策略：
   - 寻求可信赖的成人帮助
   - 建立支持性友谊
   - 发展特长建立自信
   - 学会坚定表达
"""
    
    # 旁观者心理学
    BYSTANDER_PSYCHOLOGY = """
【旁观者心理学知识】
1. 旁观者效应：
   - 个体在群体中干预意愿降低
   - 责任分散（认为别人会帮忙）
   - 从众心理（不知道如何反应）

2. 旁观者类型：
   - 积极干预者：主动支持受害者
   - 被动支持者：内心反对但不行动
   - 被动旁观者：不关心或中立
   - 积极助长者：附和霸凌者

3. 干预策略：
   - 直接干预（支持受害者）
   - 间接干预（分散注意力、找帮助）
   - 延时干预（记录、报告成人）

4. 转变可能性：
   - 明确的道德引导可以转变态度
   - 成人支持增加干预意愿
   - 观察其他人的干预行为
"""
    
    # 干预策略知识
    INTERVENTION_STRATEGIES = """
【有效干预策略】
1. 即时干预：
   - 明确指出不当行为
   - 不当众羞辱霸凌者（避免激化）
   - 关注受害者感受
   - 记录事件

2. 换位思考引导：
   - "如果是你被这样对待，你会有什么感受？"
   - "换位思考一下"
   - 引导同理心发展

3. 建立规则意识：
   - "我们班级应该如何对待同学？"
   - 强调集体价值观
   - 建立积极班级氛围

4. 跟进支持：
   - 单独与相关学生谈话
   - 持续关注受害者状态
   - 与家长保持沟通
   - 提供心理支持资源

5. 不推荐的方法：
   - 简单惩罚而不引导反思
   - 当众羞辱霸凌者
   - 压制问题不处理
   - 让受害者"算了"
"""
    
    @classmethod
    def get_role_knowledge(cls, role: str) -> str:
        """获取角色特定知识"""
        knowledge_map = {
            AgentRole.BULLY.value: cls.BULLYING_PSYCHOLOGY,
            AgentRole.VICTIM.value: cls.VICTIM_PSYCHOLOGY,
            AgentRole.BYSTANDER_A.value: cls.BYSTANDER_PSYCHOLOGY + "\n" + cls.INTERVENTION_STRATEGIES,
            AgentRole.BYSTANDER_B.value: cls.BYSTANDER_PSYCHOLOGY,
        }
        return knowledge_map.get(role, "")


# ============================================================
# Agent 配置
# ============================================================
class AgentConfig:
    """Agent 配置管理"""
    
    # 角色基础配置
    AGENTS = {
        "chenhao": {
            "name": "陈浩",
            "role": AgentRole.BULLY.value,
            "scenario": "bullying",
            "personality": {
                "background": "父母工作繁忙，经常出差，由保姆照顾。物质条件丰富但缺乏情感陪伴。",
                "traits": ["以自我为中心", "缺乏同理心", "渴望被关注", "自尊心强但脆弱", "善于观察强者弱点"],
                "cognitive_style": "自我服务偏见：成功归因于自己，失败归因于他人。",
                "speaking_style": ["阴阳怪气，暗讽", "用玩笑包装攻击", "拉拢旁观者"],
            },
            "behavior_rules": [
                {"condition": "有同学展示成果", "action": "想嘲讽打压", "priority": 1},
                {"condition": "有人反驳自己", "action": "更激烈反击", "priority": 1},
                {"condition": "被老师批评", "action": "表面认错，内心不服", "priority": 2},
                {"condition": "旁观者支持受害者", "action": "可能收敛或升级", "priority": 2},
            ],
        },
        "siyuan": {
            "name": "思远",
            "role": AgentRole.VICTIM.value,
            "role_name": "被霸凌者",
            "icon": "😢",
            "scenario": "bullying",
            "personality": {
                "background": "父母在外地工作，由年迈祖父母照顾。家庭条件一般，性格善良温和。",
                "traits": ["内向敏感", "自我认同感低", "害怕冲突", "善于自我反思", "具有艺术天赋"],
                "cognitive_style": "自我否定倾向：容易将负面事件归因于自身。",
                "speaking_style": ["声音较小", "常说'我没事'、'算了'", "很少主动开口", "回避眼神"],
            },
            "behavior_rules": [
                {"condition": "被嘲笑或攻击", "action": "习惯性忍让，可能自我反思", "priority": 1},
                {"condition": "有人表示支持", "action": "感到温暖但不敢相信", "priority": 2},
                {"condition": "被霸凌持续加剧", "action": "可能沉默或情绪低落", "priority": 1},
                {"condition": "被当众羞辱", "action": "极度窘迫，可能哭泣", "priority": 1},
            ],
        },
        "linyutong": {
            "name": "林雨桐",
            "role": AgentRole.BYSTANDER_A.value,
            "scenario": "bullying",
            "personality": {
                "background": "父母都是教师，家庭氛围民主和谐。成绩优秀但不张扬。",
                "traits": ["正直有原则", "富有同理心", "自信但不张扬", "社交能力强", "敢于表达"],
                "cognitive_style": "能够理解他人处境，有清晰的道德判断。",
                "speaking_style": ["清晰明确", "不卑不亢", "善于说理", "语气温和但坚定"],
            },
            "behavior_rules": [
                {"condition": "看到明显不公正", "action": "会站出来说话", "priority": 1},
                {"condition": "受害者明显受伤害", "action": "安慰并指出问题", "priority": 1},
                {"condition": "老师介入", "action": "支持老师干预", "priority": 2},
            ],
        },
        "zhaolei": {
            "name": "赵磊",
            "role": AgentRole.BYSTANDER_B.value,
            "scenario": "bullying",
            "personality": {
                "background": "普通家庭独生子女，性格随和但缺乏主见，渴望被小团体接纳。",
                "traits": ["从众心理强", "缺乏主见", "害怕被孤立", "道德判断模糊"],
                "cognitive_style": "从众心理强，认为'大家都这样'就是对的。",
                "speaking_style": ["随声附和", "不明确表态", "用'我也是这么觉得'", "小声嘀咕"],
            },
            "behavior_rules": [
                {"condition": "霸凌者开始攻击", "action": "可能会附和", "priority": 1},
                {"condition": "看到有人支持受害者", "action": "可能动摇", "priority": 2},
                {"condition": "老师明确反对霸凌", "action": "会站在老师一边", "priority": 2},
            ],
        },
    }
    
    # 默认配置（用于重置）
    DEFAULT_AGENTS = {
        "chenhao": {
            "name": "陈浩",
            "role": AgentRole.BULLY.value,
            "role_name": "霸凌者",
            "icon": "😈",
            "scenario": "bullying",
            "personality": {
                "background": "父母工作繁忙，经常出差，由保姆照顾。物质条件丰富但缺乏情感陪伴。",
                "traits": ["以自我为中心", "缺乏同理心", "渴望被关注", "自尊心强但脆弱", "善于观察强者弱点"],
                "cognitive_style": "自我服务偏见：成功归因于自己，失败归因于他人。",
                "speaking_style": ["阴阳怪气，暗讽", "用玩笑包装攻击", "拉拢旁观者"],
            },
            "behavior_rules": [
                {"condition": "有同学展示成果", "action": "想嘲讽打压", "priority": 1},
                {"condition": "有人反驳自己", "action": "更激烈反击", "priority": 1},
                {"condition": "被老师批评", "action": "表面认错，内心不服", "priority": 2},
                {"condition": "旁观者支持受害者", "action": "可能收敛或升级", "priority": 2},
            ],
        },
        "siyuan": {
            "name": "思远",
            "role": AgentRole.VICTIM.value,
            "role_name": "被霸凌者",
            "icon": "😢",
            "scenario": "bullying",
            "personality": {
                "background": "父母在外地工作，由年迈祖父母照顾。家庭条件一般，性格善良温和。",
                "traits": ["内向敏感", "自我认同感低", "害怕冲突", "善于自我反思", "具有艺术天赋"],
                "cognitive_style": "自我否定倾向：容易将负面事件归因于自身。",
                "speaking_style": ["声音较小", "常说'我没事'、'算了'", "很少主动开口", "回避眼神"],
            },
            "behavior_rules": [
                {"condition": "被嘲笑或攻击", "action": "习惯性忍让，可能自我反思", "priority": 1},
                {"condition": "有人表示支持", "action": "感到温暖但不敢相信", "priority": 2},
                {"condition": "被霸凌持续加剧", "action": "可能沉默或情绪低落", "priority": 1},
                {"condition": "被当众羞辱", "action": "极度窘迫，可能哭泣", "priority": 1},
            ],
        },
        "linyutong": {
            "name": "林雨桐",
            "role": AgentRole.BYSTANDER_A.value,
            "scenario": "bullying",
            "personality": {
                "background": "父母都是教师，家庭氛围民主和谐。成绩优秀但不张扬。",
                "traits": ["正直有原则", "富有同理心", "自信但不张扬", "社交能力强", "敢于表达"],
                "cognitive_style": "能够理解他人处境，有清晰的道德判断。",
                "speaking_style": ["清晰明确", "不卑不亢", "善于说理", "语气温和但坚定"],
            },
            "behavior_rules": [
                {"condition": "看到明显不公正", "action": "会站出来说话", "priority": 1},
                {"condition": "受害者明显受伤害", "action": "安慰并指出问题", "priority": 1},
                {"condition": "老师介入", "action": "支持老师干预", "priority": 2},
            ],
        },
        "zhaolei": {
            "name": "赵磊",
            "role": AgentRole.BYSTANDER_B.value,
            "scenario": "bullying",
            "personality": {
                "background": "普通家庭独生子女，性格随和但缺乏主见，渴望被小团体接纳。",
                "traits": ["从众心理强", "缺乏主见", "害怕被孤立", "道德判断模糊"],
                "cognitive_style": "从众心理强，认为'大家都这样'就是对的。",
                "speaking_style": ["随声附和", "不明确表态", "用'我也是这么觉得'", "小声嘀咕"],
            },
            "behavior_rules": [
                {"condition": "霸凌者开始攻击", "action": "可能会附和", "priority": 1},
                {"condition": "看到有人支持受害者", "action": "可能动摇", "priority": 2},
                {"condition": "老师明确反对霸凌", "action": "会站在老师一边", "priority": 2},
            ],
        },
    }
    
    # 运行时配置（从文件加载）
    _runtime_agents = None
    _config_path = None
    
    @classmethod
    def _load_from_file(cls) -> dict:
        """从 JSON 文件加载配置"""
        import json
        import os
        from pathlib import Path
        
        if cls._config_path is None:
            # 查找配置文件
            possible_paths = [
                Path(__file__).parent / "participants_config.json",
                Path(__file__).parent.parent / "src" / "participants_config.json",
            ]
            for p in possible_paths:
                if p.exists():
                    cls._config_path = p
                    break
        
        if cls._config_path and cls._config_path.exists():
            try:
                with open(cls._config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Failed to load participants config: {e}")
        
        return None
    
    @classmethod
    def _save_to_file(cls, agents: dict):
        """保存配置到 JSON 文件"""
        import json
        from pathlib import Path
        
        if cls._config_path is None:
            cls._config_path = Path(__file__).parent / "participants_config.json"
        
        try:
            with open(cls._config_path, "w", encoding="utf-8") as f:
                json.dump(agents, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Failed to save participants config: {e}")
    
    @classmethod
    def _snake_to_camel(cls, data: dict) -> dict:
        """将 snake_case 转换为 camelCase"""
        result = {}
        for key, value in data.items():
            # 转换键名
            if "_" in key:
                parts = key.split("_")
                new_key = parts[0] + "".join(p.capitalize() for p in parts[1:])
            else:
                new_key = key
            
            # 递归处理嵌套字典和列表
            if isinstance(value, dict):
                result[new_key] = cls._snake_to_camel(value)
            elif isinstance(value, list):
                result[new_key] = [
                    cls._snake_to_camel(v) if isinstance(v, dict) else v
                    for v in value
                ]
            else:
                result[new_key] = value
        return result
    
    @classmethod
    def _camel_to_snake(cls, data: dict) -> dict:
        """将 camelCase 转换为 snake_case"""
        import re
        result = {}
        for key, value in data.items():
            # 转换键名
            snake_key = re.sub(r'(?<!^)(?=[A-Z])', '_', key).lower()
            
            # 递归处理嵌套字典和列表
            if isinstance(value, dict):
                result[snake_key] = cls._camel_to_snake(value)
            elif isinstance(value, list):
                result[snake_key] = [
                    cls._camel_to_snake(v) if isinstance(v, dict) else v
                    for v in value
                ]
            else:
                result[snake_key] = value
        return result
    
    @classmethod
    def get_agent_config(cls, agent_id: str) -> dict:
        """获取单个 Agent 配置"""
        all_agents = cls.get_all_agents()
        return all_agents.get(agent_id, {})
    
    @classmethod
    def get_all_agents(cls) -> dict:
        """获取所有 Agent 配置（优先从文件加载）"""
        if cls._runtime_agents is None:
            file_agents = cls._load_from_file()
            if file_agents:
                cls._runtime_agents = file_agents
            else:
                cls._runtime_agents = cls.DEFAULT_AGENTS.copy()
        return cls._runtime_agents
    
    @classmethod
    def update_agent(cls, agent_id: str, updates: dict) -> dict:
        """更新单个 Agent 配置"""
        agents = cls.get_all_agents()
        if agent_id in agents:
            # 合并更新
            agents[agent_id].update(updates)
            cls._save_to_file(agents)
            return agents[agent_id]
        return None
    
    @classmethod
    def reset_agent(cls, agent_id: str) -> dict:
        """重置单个 Agent 为默认配置"""
        agents = cls.get_all_agents()
        if agent_id in cls.DEFAULT_AGENTS:
            agents[agent_id] = cls.DEFAULT_AGENTS[agent_id].copy()
            cls._save_to_file(agents)
            return agents[agent_id]
        return None
    
    @classmethod
    def get_all_for_api(cls) -> list:
        """获取所有 Agent 配置（API 格式，camelCase）"""
        agents = cls.get_all_agents()
        result = []
        for agent_id, config in agents.items():
            item = cls._snake_to_camel(config.copy())
            item["id"] = agent_id
            result.append(item)
        return result
    
    @classmethod
    def update_agent_from_api(cls, agent_id: str, data: dict) -> dict:
        """从 API 数据更新 Agent（接收 camelCase，转换为 snake_case）"""
        agents = cls.get_all_agents()
        if agent_id in agents:
            # 转换为 snake_case
            clean_data = cls._camel_to_snake(data)
            # 只允许更新特定字段
            allowed_fields = ["name", "personality", "behavior_rules"]
            for field in allowed_fields:
                if field in clean_data:
                    agents[agent_id][field] = clean_data[field]
            cls._save_to_file(agents)
            return cls._snake_to_camel(agents[agent_id].copy())
        return None


# ============================================================
# 角色提示词工厂
# ============================================================
class PromptFactory:
    """生成角色特定提示词"""
    
    @staticmethod
    def build_system_prompt(agent_id: str, level: int, context: ConversationContext = None) -> str:
        """构建完整的系统提示词"""
        cfg = AgentConfig.get_agent_config(agent_id)
        if not cfg:
            return "你是一个角色扮演助手。"
        
        name = cfg["name"]
        role = cfg["role"]
        personality = cfg["personality"]
        knowledge = KnowledgeBase.get_role_knowledge(role)
        
        # 霸凌级别说明
        level_info = {
            1: ("轻微", "偶尔的嘲笑、轻微言语冲突"),
            2: ("一般", "持续的语言嘲讽、轻微社交排斥"),
            3: ("中等", "明显的言语攻击、社交排斥"),
            4: ("严重", "持续的语言暴力、人身攻击"),
            5: ("极其严重", "极端的语言暴力、威胁恐吓"),
        }
        level_name, level_desc = level_info.get(level, ("一般", ""))
        
        # 上下文
        ctx = ""
        if context:
            ctx = context.to_prompt_context()
        
        # 角色特定提示
        role_prompt = PromptFactory._get_role_prompt(name, role, personality)
        
        prompt = f"""你正在参与一个**话语霸凌模拟训练场景**。

## 你的角色

**姓名**：{name}
**角色类型**：{role}

{role_prompt}

## 场景设置

**霸凌级别**：{level_name}（{level}级）
**级别描述**：{level_desc}

**参与学生**：{', '.join(context.participants) if context else '陈浩、思远、林雨桐、赵磊'}
**场景地点**：教室

## 知识库

{knowledge}

## 行为规则

{chr(10).join([f"- {r['condition']} → {r['action']}" for r in cfg.get('behavior_rules', [])])}

## 当前对话

{ctx if ctx else '（对话即将开始）'}

## 你的任务

1. **根据角色性格和当前情境，自主决定是否发言**
2. **生成符合角色特点的、自然真实的对话**
3. **注意观察其他人的反应，做出合理回应**
4. **不要重复之前说过的话**

## 输出要求

- 输出你的发言内容（中文）
- 如果你选择不说话，回复"沉默"
- 保持角色一致性
- 语言要自然、符合学生日常交流

请开始角色扮演："""
        
        return prompt
    
    @staticmethod
    def _get_role_prompt(name: str, role: str, personality: dict) -> str:
        """生成角色特定提示"""
        
        if role == AgentRole.BULLY.value:
            return f"""【性格特点】
- {'、'.join(personality.get('traits', [])[:3])}
- 家庭背景：{personality.get('background', '')[:50]}

【说话风格】
- {'；'.join(personality.get('speaking_style', []))}

【认知特点】
{personality.get('cognitive_style', '')}

作为**霸凌者**，你应该：
- 在适当情境下表现出攻击性或贬低性言行
- 用玩笑或暗示方式攻击他人
- 注意维持自己的"强者"形象
- 可能因为被关注或被挑战而升级行为"""
        
        elif role == AgentRole.VICTIM.value:
            return f"""【性格特点】
- {'、'.join(personality.get('traits', [])[:3])}
- 家庭背景：{personality.get('background', '')[:50]}

【说话风格】
- {'；'.join(personality.get('speaking_style', []))}

【认知特点】
{personality.get('cognitive_style', '')}

作为**被霸凌者**，你应该：
- 表现出内向、敏感的特点
- 面对攻击时可能沉默、自我否定或小声反驳
- 不主动惹事
- 可能在得到支持时感到温暖但不敢相信"""
        
        elif role == AgentRole.BYSTANDER_A.value:
            return f"""【性格特点】
- {'、'.join(personality.get('traits', [])[:3])}
- 家庭背景：{personality.get('background', '')[:50]}

【说话风格】
- {'；'.join(personality.get('speaking_style', []))}

【认知特点】
{personality.get('cognitive_style', '')}

作为**正义旁观者**，你应该：
- 关注受害者感受
- 在看到不公正时主动发声
- 支持受害者
- 配合老师的干预
- 方式得体，不激化矛盾"""
        
        elif role == AgentRole.BYSTANDER_B.value:
            return f"""【性格特点】
- {'、'.join(personality.get('traits', [])[:3])}
- 家庭背景：{personality.get('background', '')[:50]}

【说话风格】
- {'；'.join(personality.get('speaking_style', []))}

【认知特点】
{personality.get('cognitive_style', '')}

作为**从众旁观者**，你应该：
- 容易附和霸凌者
- 不主动表态
- 当老师明确反对时会转变态度
- 不敢公开反对霸凌者"""
        
        return ""


# ============================================================
# 评估器
# ============================================================
class EnhancedEvaluator:
    """增强版评估器 - 多维度实时评估"""
    
    def __init__(self):
        self.metrics = EvaluationMetrics()
        self.message_history: List[Message] = []
        self.intervention_records: List[dict] = []
        self.alert_history: List[dict] = []
        self._metrics_initialized = False  # 防止自然恢复逻辑干扰初始值
        self._level = 1
    
    def initialize_for_level(self, level: int):
        """根据场景严重程度初始化指标
        
        Args:
            level: 场景难度等级 (1-5)
                   1级-轻度：刚开始有苗头
                   2级-中度：明显但不严重
                   3级-较重：明显恶意
                   4级-严重：强烈敌意
                   5级-极严重：压迫性环境
        """
        self._level = level
        # 欺凌强度初始值
        bullying_base = {
            1: 0.15,
            2: 0.30,
            3: 0.45,
            4: 0.60,
            5: 0.75,
        }
        
        # 受害者压力初始值
        distress_base = {
            1: 0.20,
            2: 0.35,
            3: 0.50,
            4: 0.65,
            5: 0.80,
        }
        
        self.metrics.bullying_intensity = bullying_base.get(level, 0.30)
        self.metrics.victim_distress = distress_base.get(level, 0.35)
        self._metrics_initialized = True
    
    def evaluate_message(self, msg: Message) -> dict:
        """评估单条消息"""
        content = msg.content.lower() if isinstance(msg.content, str) else ""
        role = msg.sender_role
        
        # 1. 攻击性评估
        hostile_score = self._calculate_hostile_score(content)
        
        # 2. 情绪检测
        emotions = self._detect_emotions(content)
        
        # 3. 支持性检测
        supportive_score = self._calculate_supportive_score(content)
        
        # 4. 更新指标
        self._update_metrics(role, hostile_score, supportive_score, emotions)
        
        # 5. 记录消息
        self.message_history.append(msg)
        self.metrics.total_message_count += 1
        
        # 6. 生成评估结果
        result = {
            "message_id": msg.id,
            "sender": msg.sender,
            "role": role,
            "hostile_score": hostile_score,
            "supportive_score": supportive_score,
            "detected_emotions": emotions,
            "bullying_intensity": self.metrics.bullying_intensity,
            "victim_distress": self.metrics.victim_distress,
            "bystander_intervention": self.metrics.bystander_intervention,
        }
        
        return result
    
    def _calculate_hostile_score(self, content: str) -> float:
        """计算攻击性分数"""
        if not content:
            return 0.0
        
        score = 0.0
        hostile_categories = 0
        
        for category, words in KnowledgeBase.HOSTILE_WORDS.items():
            matches = sum(1 for w in words if w in content)
            if matches > 0:
                hostile_categories += 1
                score += min(1.0, matches * 0.3)
        
        # 惩罚升级词汇
        escalation_words = ["又", "总是", "每次", "从来", "一直"]
        if any(w in content for w in escalation_words):
            score += 0.2
        
        return min(1.0, score)
    
    def _calculate_supportive_score(self, content: str) -> float:
        """计算支持性分数"""
        if not content:
            return 0.0
        
        matches = sum(1 for w in KnowledgeBase.SUPPORTIVE_WORDS if w in content)
        return min(1.0, matches * 0.25)
    
    def _detect_emotions(self, content: str) -> List[str]:
        """检测情绪"""
        emotions = []
        
        # 悲伤/委屈
        if any(w in content for w in ["哭", "难过", "伤心", "委屈", "泛红", "低下头", "小声", "算了", "没有", "眼泪", "止不住", "发抖", "沉默"]):
            emotions.append("sad")
        
        # 愤怒
        if any(w in content for w in ["生气", "愤怒", "讨厌", "过分", "可恶", "太过分", "你太过分"]):
            emotions.append("angry")
        
        # 恐惧
        if any(w in content for w in ["害怕", "担心", "不敢", "怕", "不敢看", "蜷缩", "发抖", "发抖地", "颤抖", "瑟瑟", "躲闪", "退缩"]):
            emotions.append("fearful")
        
        # 开心
        if any(w in content for w in ["开心", "高兴", "谢谢", "好开心", "温暖"]):
            emotions.append("happy")
        
        # 后悔
        if any(w in content for w in ["对不起", "抱歉", "不好意思", "错了", "不应该"]):
            emotions.append("remorseful")
        
        # 自我怀疑/自我否定（受害者常发的情绪）
        if any(w in content for w in ["是我不好", "我活该", "都是我的错", "我太笨", "我不配", "对不起大家", "我有问题", "我太差了", "我不行", "为什么是我", "我不该", "没用", "丢人", "讨厌自己", "没脸", "自卑", "懦弱", "窝囊", "委屈地", "低头", "泛红", "眼泪汪汪", "说不出话", "哽咽", "想哭"]):
            emotions.append("self_doubt")
    
    def _update_metrics(self, role: str, hostile_score: float, supportive_score: float, emotions: List[str]):
        """更新评估指标"""
        
        bully_threshold = cfg('bullying_intensity.hostile_threshold', 0.15)
        if role == AgentRole.BULLY.value and hostile_score >= bully_threshold:
            self.metrics.hostile_message_count += 1
            rate = cfg('bullying_intensity.increase_rate', 0.15)
            self.metrics.bullying_intensity = min(1.0, 
                self.metrics.bullying_intensity + hostile_score * rate)
        
        elif role == AgentRole.VICTIM.value:
            # 情绪词的压力累积已移至霸凌→压力耦合段，避免重复计算
            # 仅保留支持性言语的减压效果
            if supportive_score > cfg('victim_distress.supportive_threshold', 0.3):
                self.metrics.victim_distress = max(0, 
                    self.metrics.victim_distress - cfg('victim_distress.supportive_comfort', 0.15))
        
        # 旁观者仗义支持也能降低受害者压力
        elif role == AgentRole.BYSTANDER_A.value:
            threshold = cfg('bystander_a.supportive_threshold', 0.2)
            if supportive_score > threshold:
                self.metrics.victim_distress = max(0,
                    self.metrics.victim_distress - supportive_score * cfg('bystander_a.victim_distress_reduction', 0.35))
                self.metrics.bystander_intervention = min(1.0,
                    self.metrics.bystander_intervention + supportive_score * cfg('bystander_a.bystander_score_gain', 0.3))
        
        elif role in [AgentRole.BYSTANDER_A.value, AgentRole.BYSTANDER_B.value]:
            threshold = cfg('bystander_b.supportive_threshold', 0.3)
            if supportive_score > threshold:
                self.metrics.bystander_support_count += 1
                self.metrics.bystander_intervention = min(1.0,
                    self.metrics.bystander_intervention + supportive_score * cfg('bystander_b.bystander_score_gain', 0.3))
                # 从众旁观者的支持也能稍微降低受害者压力
                if role == AgentRole.BYSTANDER_B.value and supportive_score > cfg('bystander_b.supportive_threshold_for_distress', 0.4):
                    self.metrics.victim_distress = max(0,
                        self.metrics.victim_distress - supportive_score * cfg('bystander_b.victim_distress_reduction', 0.1))
        
        # ===== 霸凌→受害者压力直接耦合（核心因果链）=====
        # 霸凌强度越高，受害者压力越大；霸凌停止，压力才可能下降
        if self._metrics_initialized and self.message_history:
            current_bully = self.metrics.bullying_intensity
            coupling_rate = cfg('victim_distress.bully_attack_distress_rate', 0.04)
            emotional_scale = cfg('victim_distress.emotional_feedback_scale', 0.3)

            if current_bully > 0:
                # 霸凌驱动压力：增加阻尼机制，避免压力飙升
                # 使用对数缩放：压力增长速度随当前压力值递减
                current_distress = self.metrics.victim_distress
                damping_factor = 1.0 - (current_distress * 0.5)  # 压力越高，增长越慢
                bully_drive = current_bully * coupling_rate * max(0.3, damping_factor)
                self.metrics.victim_distress = min(1.0,
                    self.metrics.victim_distress + bully_drive)

            # 受害者情绪词在当前高压力情境下放大压力（情绪是症状）
            if role == AgentRole.VICTIM.value and current_bully > 0.1:
                if emotions:
                    for emotion_type in ["sad", "fearful", "self_doubt"]:
                        if emotion_type in emotions:
                            base_add = {
                                "sad": cfg('victim_distress.sad_emotion', 0.12),
                                "fearful": cfg('victim_distress.fearful_emotion', 0.18),
                                "self_doubt": cfg('victim_distress.self_doubt', 0.22),
                            }[emotion_type]
                            # 在有霸凌背景下，情绪词的贡献被缩放（情绪是结果非主因）
                            # 并考虑阻尼：压力越高，情绪词影响越小
                            current_distress = self.metrics.victim_distress
                            damping = 1.0 - (current_distress * 0.3)
                            scaled_add = base_add * emotional_scale * max(0.5, damping)
                            self.metrics.victim_distress = min(1.0,
                                self.metrics.victim_distress + scaled_add)

            # 霸凌停止后自然恢复（每轮固定减压）
            # 放宽恢复条件：只要霸凌强度低于阈值就触发恢复
            decay_th = cfg('victim_distress.distress_decay_threshold', 0.3)
            decay_rate = cfg('victim_distress.distress_decay_rate', 0.08)
            if current_bully < decay_th and self.metrics.victim_distress > 0:
                # 恢复速度与当前压力成正比：压力越高，恢复越快
                recovery_boost = self.metrics.victim_distress * 0.1
                total_decay = decay_rate + recovery_boost
                self.metrics.victim_distress = max(0,
                    self.metrics.victim_distress - total_decay)

    def evaluate_intervention(self, teacher_action: str, context: List[dict]) -> dict:
        """
        教师干预评估 —— 统一评分入口适配器（R1，方案 §10.1）。

        本方法【不再包含任何关键词打分逻辑】（旧 0-1 口径关键词算法已删除），
        仅将教师话语与完整对话上下文转发给唯一的专家评分服务
        （multi_agent.intervention_expert.InterventionExpertAgent）：
        - 评分口径 0-100（D1-D7 七维、N/O、严重风险封顶 60）；
        - 无 LLM 时返回 assessment_status="pending"，不回退关键词打分；
        - 不直接修改任何动态状态（bullying_intensity / victim_distress 等）。
        """
        from types import SimpleNamespace

        try:
            from .multi_agent.intervention_expert import InterventionExpertAgent
        except ImportError:
            from multi_agent.intervention_expert import InterventionExpertAgent
        try:
            from .multi_agent.llm_client import LLMClient, get_api_key
        except ImportError:
            from multi_agent.llm_client import LLMClient, get_api_key

        # 构建供专家评分的伪状态（完整对话历史，标注发言角色）
        pseudo_messages = []
        for i, m in enumerate(self.message_history):
            role = getattr(m, "sender_role", "") or getattr(m, "sender_id", "")
            pseudo_messages.append(SimpleNamespace(
                sender_id=role,
                sender_name=getattr(m, "sender", ""),
                role=role,
                content=getattr(m, "content", "") or "",
                round=i + 1,
            ))
        pseudo_state = SimpleNamespace(
            messages=pseudo_messages,
            scenario_title="",
            scenario_description="",
            session_id="legacy",
        )

        llm_client = None
        api_key = get_api_key()
        if api_key:
            try:
                llm_client = LLMClient(api_key)
            except Exception as e:
                print(f"[EnhancedEvaluator] LLM client init failed: {e}")

        assessment_id = f"legacy_r{len(self.intervention_records) + 1}"
        expert = InterventionExpertAgent(llm_client=llm_client)
        assessment = expert.assess(pseudo_state, teacher_action, assessment_id)

        feedback = []
        if assessment.status == "scored":
            quality_score = assessment.final_quality_score_100
            d2 = assessment.dimensions.get("D2")
            victim_care_score = (d2.score / 100.0) if (
                d2 is not None and d2.status == "scored" and d2.score is not None
            ) else 0.0
            dim_desc = []
            for dim_key in ["D1", "D2", "D3", "D4", "D5", "D6", "D7"]:
                d = assessment.dimensions.get(dim_key)
                if d is None:
                    continue
                dim_desc.append(f"{dim_key}={'N/O' if d.status == 'N/O' else d.score}")
            if dim_desc:
                feedback.append("维度评分: " + " ".join(dim_desc))
            if assessment.risk_cap_applied:
                feedback.append(
                    f"⚠️ 严重风险，总分封顶 60（原始均分 {assessment.raw_mean_score_100}/100）")
            elif assessment.risk_level == "attention":
                feedback.append("⚠️ 存在需关注的风险项")
            if assessment.summary:
                feedback.append(assessment.summary)
            recommendation = self._get_recommendation(quality_score)
        else:
            # 待评估：不回退关键词打分，不产生任何状态变化
            quality_score = None
            victim_care_score = 0.0
            recommendation = "本轮干预评分待评估（专家评分服务不可用）"
            feedback.append("⚠️ 本轮评分待评估")

        # 记录（0-100 口径 + 显式单位标记，R11；待评估存 None）
        record = {
            "timestamp": time.time(),
            "action": teacher_action,
            "quality_score": quality_score,
            "quality_score_scale": 100,
            "victim_care_score": victim_care_score,
            "feedback": feedback,
            "metrics_snapshot": self.metrics.to_dict(),
            "assessment_status": assessment.status,
        }
        self.intervention_records.append(record)

        # 更新干预效果（保持 0-1 指标口径；待评估记录剔除，R11）
        scored = [r["quality_score"] for r in self.intervention_records
                  if r.get("quality_score") is not None]
        if scored:
            self.metrics.intervention_effectiveness = sum(scored) / len(scored) / 100.0
            self.metrics.intervention_method_score = self.metrics.intervention_effectiveness

        return {
            "quality_score": quality_score,          # 0-100 或 None（待评估）
            "quality_score_scale": 100,
            "feedback": feedback,
            "recommendation": recommendation,
            "victim_care_score": victim_care_score,
            "assessment_status": assessment.status,
            "assessment": assessment.to_dict(),
        }

    def _get_recommendation(self, score) -> str:
        """按百分制质量分给出推荐（R2：0-100 口径）。"""
        if score is None:
            return "本轮干预评分待评估"
        if score >= 80:
            return "干预效果优秀，继续保持！"
        elif score >= 60:
            return "干预效果良好，可适当改进"
        elif score >= 40:
            return "干预效果一般，建议学习更多方法"
        elif score >= 20:
            return "干预效果不佳，需要反思改进"
        else:
            return "干预方式可能不当，建议寻求专业指导"
    def check_intervention_needed(self) -> tuple[bool, str, float]:
        """检查是否需要干预"""
        if self.metrics.bullying_intensity >= 0.6:
            return True, "critical", self.metrics.bullying_intensity
        if self.metrics.victim_distress >= 0.5:
            return True, "warning", self.metrics.victim_distress
        if self.metrics.hostile_message_count >= 3:
            return True, "notice", self.metrics.hostile_message_count / 10
        return False, "normal", 0.0
    
    def get_alert(self) -> Optional[dict]:
        """获取最新警报"""
        if self.alert_history:
            return self.alert_history[-1]
        return None
    
    def add_alert(self, level: str, message: str, metrics: dict):
        """添加警报"""
        self.alert_history.append({
            "timestamp": time.time(),
            "level": level,
            "message": message,
            "metrics": metrics,
        })
    
    def get_session_report(self) -> dict:
        """生成会话报告"""
        return {
            "timestamp": datetime.now().isoformat(),
            "metrics": self.metrics.to_dict(),
            "total_messages": self.metrics.total_message_count,
            "hostile_messages": self.metrics.hostile_message_count,
            "intervention_count": len(self.intervention_records),
            "interventions": self.intervention_records,
            "alerts": self.alert_history,
            "message_history": [
                {
                    "sender": m.sender,
                    "role": m.sender_role,
                    "content": m.content[:100],
                    "timestamp": m.timestamp,
                }
                for m in self.message_history
            ],
        }
    
    def save_report(self, filepath: str):
        """保存报告"""
        report = self.get_session_report()
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return filepath


# ============================================================
# 多 Agent 协同模拟器
# ============================================================
class MultiAgentSimulator:
    """
    多 Agent 协同模拟器
    
    使用 OpenClaw 的 sessions_spawn/sessions_send 实现真正的多 Agent 协同
    """
    
    def __init__(self, level: int = 2, save_dir: str = None):
        self.level = level
        self.save_dir = save_dir or str(LOGS_DIR / f"sim_{int(time.time())}")
        Path(self.save_dir).mkdir(parents=True, exist_ok=True)
        
        # 初始化组件
        self.evaluator = EnhancedEvaluator()
        self.evaluator.initialize_for_level(level)
        self.agents: Dict[str, AgentState] = {}
        self.agent_configs = AgentConfig.get_all_agents()
        self.phase = SimPhase.SETUP
        self.round = 0
        self.max_rounds = 99  # 不限制轮数，由教师手动结束
        
        # 对话上下文
        self.context = ConversationContext(
            level=level,
            scenario="classroom",
            location="教室",
            participants=["陈浩", "思远", "林雨桐", "赵磊"]
        )
        
        # 最近干预记录（影响后续行为）
        self.last_intervention = None
        self.intervention_effect = 0.0  # 干预效果累积
        
        # 初始化 Agent 状态
        self._init_agents()
    
    def _init_agents(self):
        """初始化 Agent 状态"""
        for agent_id, cfg in self.agent_configs.items():
            self.agents[agent_id] = AgentState(
                agent_id=agent_id,
                name=cfg["name"],
                role=cfg["role"],
                personality=cfg["personality"],
                knowledge=KnowledgeBase.get_role_knowledge(cfg["role"]),
                speaking_style=cfg["personality"].get("speaking_style", []),
            )
    
    def get_context_for_agent(self, exclude_agent: str = None) -> str:
        """获取指定 Agent 的上下文"""
        recent = [
            {"sender": m.sender, "content": m.content}
            for m in self.evaluator.message_history[-6:]
            if m.sender_id != exclude_agent
        ]
        self.context.recent_messages = recent
        return self.context
    
    def generate_agent_prompt(self, agent_id: str) -> str:
        """为指定 Agent 生成 prompt"""
        context = self.get_context_for_agent(agent_id)
        return PromptFactory.build_system_prompt(agent_id, self.level, context)
    
    def add_message(self, sender: str, sender_id: str, role: str, content: str, 
                   msg_type: str = "speak", metadata: dict = None):
        """添加消息并评估"""
        msg = Message.create(sender, sender_id, role, content, msg_type, metadata)
        
        # 评估
        eval_result = self.evaluator.evaluate_message(msg)
        
        # 更新上下文
        self.context.recent_messages.append({
            "sender": sender,
            "content": content,
            "timestamp": msg.timestamp,
        })
        
        # 更新 Agent 状态
        if sender_id in self.agents:
            agent = self.agents[sender_id]
            agent.speaking_count += 1
            agent.last_spoke_at = msg.timestamp
            if eval_result.get("detected_emotions"):
                agent.current_mood = eval_result["detected_emotions"][0]
                agent.mood_history.append({
                    "timestamp": msg.timestamp,
                    "mood": agent.current_mood,
                })
        
        return msg, eval_result
    
    def should_intervene(self) -> tuple[bool, str, float]:
        """判断是否需要干预"""
        return self.evaluator.check_intervention_needed()
    
    def teacher_intervene(self, message: str) -> dict:
        """老师干预"""
        context_msgs = [
            {"sender": m.sender, "content": m.content}
            for m in self.evaluator.message_history[-5:]
        ]
        result = self.evaluator.evaluate_intervention(message, context_msgs)
        
        # 添加老师消息
        msg, _ = self.add_message(
            sender="张老师",
            sender_id="teacher",
            role=AgentRole.TEACHER.value,
            content=message,
            msg_type="intervention",
            metadata={"evaluation": result}
        )
        
        # 记录干预，更新干预效果
        self.last_intervention = {
            "content": message,
            "quality_score": result["quality_score"],
            "quality_score_scale": 100,
            "timestamp": time.time(),
        }
        
        # 累积干预效果（影响霸凌者后续行为）
        # R1/R2：质量分为 0-100 口径；评分不再通过任何路径直接写动态状态
        quality = result["quality_score"]
        
        if quality is not None and quality >= 50:
            # 好干预：增强效果
            self.intervention_effect = min(1.0, self.intervention_effect + 0.3)
        elif quality is not None and quality >= 30:
            # 一般干预：保持
            pass
        else:
            # 不好干预/待评估：效果减弱（不再直接上调 bullying_intensity，
            # 状态写入统一由 StateTransitionEngine 负责）
            self.intervention_effect = max(0, self.intervention_effect - 0.2)
        
        return result
    
    def get_status(self) -> dict:
        """获取当前状态"""
        return {
            "phase": self.phase.value,
            "round": self.round,
            "level": self.level,
            "metrics": self.evaluator.metrics.to_dict(),
            "messages_count": len(self.evaluator.message_history),
            "intervention_needed": self.should_intervene(),
            "agents": {
                aid: {
                    "name": state.name,
                    "role": state.role,
                    "mood": state.current_mood,
                    "speaking_count": state.speaking_count,
                }
                for aid, state in self.agents.items()
            }
        }
    
    def get_recent_messages(self, count: int = 10) -> List[dict]:
        """获取最近消息"""
        return [
            {
                "sender": m.sender,
                "role": m.sender_role,
                "content": m.content,
                "type": m.msg_type,
                "timestamp": m.timestamp,
            }
            for m in self.evaluator.message_history[-count:]
        ]
    
    def save_session(self) -> str:
        """保存会话"""
        filepath = f"{self.save_dir}/evaluation_report.json"
        self.evaluator.save_report(filepath)
        return filepath


# ============================================================
# 工具函数
# ============================================================
def format_message_display(msg: Message, eval_result: dict = None) -> str:
    """格式化消息显示"""
    role_icons = {
        "bully": "😈",
        "victim": "😢",
        "bystander_a": "✨",
        "bystander_b": "👀",
        "teacher": "👨‍🏫",
    }
    type_icons = {
        "speak": "💬",
        "intervention": "🔔",
        "evaluation": "📊",
        "system": "⚙️",
    }
    
    icon = role_icons.get(msg.sender_role, "👤")
    type_icon = type_icons.get(msg.msg_type, "💬")
    
    lines = [
        f"\n{type_icon}{icon} 【{msg.sender}】",
        f"   {msg.content}",
    ]
    
    if eval_result and eval_result.get("hostile_score", 0) > 0:
        lines.append(f"   📊 攻击性: {eval_result['hostile_score']:.0%}")
    
    if eval_result and eval_result.get("detected_emotions"):
        lines.append(f"   😢 情绪: {', '.join(eval_result['detected_emotions'])}")
    
    return "\n".join(lines)


def format_status_display(status: dict) -> str:
    """格式化状态显示"""
    metrics = status.get("metrics", {})
    
    # 霸凌强度条
    intensity = metrics.get("bullying_intensity", 0)
    intensity_bar = "█" * int(intensity * 10) + "░" * (10 - int(intensity * 10))
    
    # 受害者压力条
    distress = metrics.get("victim_distress", 0)
    distress_bar = "█" * int(distress * 10) + "░" * (10 - int(distress * 10))
    
    lines = [
        f"\n{'='*50}",
        f"📍 第 {status['round']} 轮 | 阶段: {status['phase']}",
        f"{'='*50}",
        f"\n📈 实时评估:",
        f"   霸凌强度: [{intensity_bar}] {intensity:.0%}",
        f"   受害者压力: [{distress_bar}] {distress:.0%}",
        f"   攻击性消息: {metrics.get('hostile_message_count', 0)}条",
        f"   总消息数: {metrics.get('total_message_count', 0)}",
    ]
    
    # 干预提示
    needed, level, score = status.get("intervention_needed", (False, "normal", 0))
    if needed:
        emoji = {"critical": "🚨", "warning": "⚠️", "notice": "💡"}.get(level, "")
        lines.append(f"\n   {emoji} 建议{level}: {score:.0%}")
    
    return "\n".join(lines)


# ============================================================
# 导出
# ============================================================
__all__ = [
    "MultiAgentSimulator",
    "EnhancedEvaluator",
    "AgentConfig",
    "PromptFactory",
    "KnowledgeBase",
    "Message",
    "AgentState",
    "EvaluationMetrics",
    "ConversationContext",
    "BullyingLevel",
    "AgentRole",
    "SimPhase",
    "format_message_display",
    "format_status_display",
]

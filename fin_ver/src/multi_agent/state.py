"""
SharedState - LangGraph 多智能体共享状态
Step 2 更新（2026-05-28）：新增 OBVQ 累计分数字段与风险等级
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal, Set
from datetime import datetime


# ============================================================
# 消息结构
# ============================================================

@dataclass
class Message:
    """对话消息"""
    sender_id: str       # agent ID: "bully", "victim", "bystander_a", "bystander_b"
    sender_name: str     # 显示名: "陈浩", "思远", "林雨桐", "赵磊"
    role: str            # 角色类型
    content: str
    timestamp: str = ""
    round: int = 0
    hostile_score: float = 0.0
    supportive_score: float = 0.0
    emotions: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "round": self.round,
            "hostile_score": self.hostile_score,
            "supportive_score": self.supportive_score,
            "emotions": self.emotions,
        }

    @staticmethod
    def from_dict(d: dict) -> "Message":
        return Message(
            sender_id=d["sender_id"],
            sender_name=d["sender_name"],
            role=d["role"],
            content=d["content"],
            timestamp=d.get("timestamp", ""),
            round=d.get("round", 0),
            hostile_score=d.get("hostile_score", 0.0),
            supportive_score=d.get("supportive_score", 0.0),
            emotions=d.get("emotions", []),
        )


@dataclass
class AgentMemory:
    """Agent个体记忆（可持久化）"""
    agent_id: str
    name: str
    speaking_count: int = 0
    last_spoke_at: Optional[str] = None
    current_mood: str = ""
    mood_history: List[Dict] = field(default_factory=list)
    intervention_history: List[str] = field(default_factory=list)  # 记录自己参与的干预内容
    perception_log: List[str] = field(default_factory=list)  # 感知到的关键事件

    def add_perception(self, event: str):
        """记录感知到的重要事件"""
        self.perception_log.append(event)
        # 只保留最近10条
        if len(self.perception_log) > 10:
            self.perception_log = self.perception_log[-10:]

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "speaking_count": self.speaking_count,
            "last_spoke_at": self.last_spoke_at,
            "current_mood": self.current_mood,
            "mood_history": self.mood_history,
            "intervention_history": self.intervention_history,
            "perception_log": self.perception_log,
        }


@dataclass
class InterventionRecord:
    """干预记录（R4/R11：预测与实际分开存储；待评估 quality_score=None；显式 0-100 单位标记）"""
    round: int
    content: str
    quality_score: Optional[float]          # 0-100 或 None（待评估）
    victim_care_score: float
    feedback: List[str]
    assessment_id: str = ""
    quality_score_scale: int = 100          # 显式单位标记（R11：禁止按值域猜单位）
    # 预测效果（引擎写入前的提案导出，属模型预测，不是观测事实）
    predicted_bullying_reduction: Optional[float] = None
    predicted_distress_change: Optional[float] = None
    # 实际效果（由状态前后差计算；正值=下降/压力减少）
    actual_bullying_reduction: Optional[float] = None
    actual_distress_change: Optional[float] = None
    # 旧字段名保留为 actual 的别名（仅向后兼容，禁止再按评分阈值派生）
    bullying_reduction: float = 0.0
    distress_reduction: float = 0.0

    def to_dict(self) -> dict:
        return {
            "round": self.round,
            "content": self.content,
            "quality_score": self.quality_score,
            "quality_score_scale": self.quality_score_scale,
            "assessment_status": "pending" if self.quality_score is None else "scored",
            "assessment_id": self.assessment_id,
            "victim_care_score": self.victim_care_score,
            "feedback": self.feedback,
            "predicted_bullying_reduction": self.predicted_bullying_reduction,
            "predicted_distress_change": self.predicted_distress_change,
            "actual_bullying_reduction": self.actual_bullying_reduction,
            "actual_distress_change": self.actual_distress_change,
            "bullying_reduction": self.bullying_reduction,
            "distress_reduction": self.distress_reduction,
        }


# ============================================================
# OBVQ 风险等级与累计分数（Step 2 新增）
# ============================================================

# OBVQ 风险等级阈值
# 
# 说明：原始 OBVQ 受害量表 20 题 × 4 分 = 80 分
# 当前系统的 bullied_intensity 0-1 转换为 OBVQ 原始分 0-4：
#   0.1-0.19 → 1分（偶发）
#   0.2-0.49 → 2分（几次）
#   0.5-0.79 → 3分（多次）
#   0.8-1.0  → 4分（频繁）
# 
# 由于实际模拟中欺负事件数量有限（通常5-10轮），
# 阈值调整为更符合模拟场景的范围（累计约5-20次维度事件）：
# OBVQ 风险等级已移除，仅保留得分

# OBVQ 各维度原始题数（用于计算维度平均分）
OBVQ_DIMENSION_QUESTIONS = {
    "physical":   2,   # V1-V2 身体受害
    "verbal":     6,   # V3-V5, V17, V20（言语）+ V3威胁
    "relational": 6,   # V6-V8, V16-V17, V20
    "cyber":      4,   # V9-V11, V18
    "power":      2,   # V13-V14
    "prejudice":  3,   # V15, V19, V20偏见部分
}


@dataclass
class OBVQState:
    """
    OBVQ 累计状态（Step 2 新增）
    实时追踪受害者被欺负的 OBVQ 维度累计分数和风险等级
    """
    # 各维度原始得分（0-4 × 题数，最大值见 OBVQ_DIMENSION_QUESTIONS）
    dimension_scores: Dict[str, List[int]] = field(default_factory=lambda: {
        "physical": [],
        "verbal": [],
        "relational": [],
        "cyber": [],
        "power": [],
        "prejudice": [],
    })

    # 各维度累计原始总分（每次欺负事件的原始评分 0-4）
    dimension_totals: Dict[str, int] = field(default_factory=lambda: {
        "physical": 0,
        "verbal": 0,
        "relational": 0,
        "cyber": 0,
        "power": 0,
        "prejudice": 0,
    })

    # OBVQ 总分（0-80，受害经历量表20题×4分）
    total_score: int = 0



    # 最近一次欺负事件的维度得分（用于前端展示）
    last_event_dimensions: Dict[str, int] = field(default_factory=lambda: {
        "physical": 0, "verbal": 0, "relational": 0,
        "cyber": 0, "power": 0, "prejudice": 0,
    })

    def add_event(self, dimension_scores: Dict[str, float], event_round: int,
                    intensity_factor: float = 1.0) -> None:
        """
        记录一次欺负事件，依据欺凌强度（intensity_factor）直接设置 OBVQ 原始评分
        
        【核心逻辑】OBVQ 分数直接由欺凌强度（hostile_score）决定，
        维度得分仅决定分数分配到哪些维度，不改变分数大小。
        
        Args:
            dimension_scores: 各维度0-1得分（关键词匹配强度，仅用于确定维度归属）
            event_round: 发生轮次
            intensity_factor: 强度因子（0-1），来源于 hostile_score，直接决定 OBVQ 原始评分（0-4）
        """
        self.last_event_dimensions = {}
        self.last_intensity_factor = intensity_factor
        
        # ★ 核心修改：直接用 intensity_factor 决定原始评分
        # 阈值根据实际 hostile_score 分布调整（实测范围约 0.06~0.45）
        if intensity_factor >= 0.38:
            raw_score = 4   # 频繁/严重 → 对应 OBVQ "6次以上"
        elif intensity_factor >= 0.28:
            raw_score = 3   # 多次 → 对应"4-5次"
        elif intensity_factor >= 0.18:
            raw_score = 2   # 几次 → 对应"2-3次"
        elif intensity_factor >= 0.08:
            raw_score = 1   # 偶发 → 对应"1次"
        else:
            raw_score = 0   # 未发生
        
        if raw_score == 0:
            # 强度太低，不记录 OBVQ 事件
            for dim_name in dimension_scores:
                self.last_event_dimensions[dim_name] = 0
            return
        
        # 确定哪些维度应该被记录（有关键词命中 或 语义检测为欺负）
        matched_dims = [d for d, s in dimension_scores.items() if s > 0]
        if not matched_dims:
            # 无关键词命中但 intensity_factor > 0.08（语义检测为欺负）
            # 默认归入 verbal 维度（最常见的欺负类型）
            matched_dims = ['verbal']
        
        for dim_name in matched_dims:
            self.dimension_scores[dim_name].append(raw_score)
            self.dimension_totals[dim_name] += raw_score
            self.last_event_dimensions[dim_name] = raw_score
        
        # 未命中的维度记为 0
        for dim_name in dimension_scores:
            if dim_name not in self.last_event_dimensions:
                self.last_event_dimensions[dim_name] = 0
        
        # 更新总分
        self._recalc_total()


    def _recalc_total(self) -> None:
        """重新计算OBVQ总分（各维度累计分数之和）"""
        self.total_score = sum(self.dimension_totals.values())
        self.total_score = min(80, self.total_score)  # 上限80

    def get_score_info(self) -> Dict:
        """获取当前OBVQ得分信息（用于前端展示）"""
        return {
            "total_score": self.total_score,
            "dimension_totals": self.dimension_totals.copy(),
        }

    def get_dimension_averages(self) -> Dict[str, float]:
        """获取各维度平均分（维度总分/维度题数，用于雷达图）"""
        averages = {}
        for dim_name, total in self.dimension_totals.items():
            n_questions = OBVQ_DIMENSION_QUESTIONS.get(dim_name, 1)
            # 平均分归一化为0-1（用于雷达图）
            avg_score = total / (n_questions * 4) if n_questions > 0 else 0
            averages[dim_name] = min(1.0, avg_score)
        return averages

    def to_dict(self) -> dict:
        return {
            "total_score": self.total_score,
            "dimension_totals": self.dimension_totals,
            "dimension_averages": self.get_dimension_averages(),
            "last_event_dimensions": self.last_event_dimensions,
        }


# ============================================================
# 自动结束判定参数（唯一配置来源）
# ============================================================

def get_termination_params(config: dict = None) -> dict:
    """自动结束判定的唯一参数来源。

    supervisor._check_termination（真实终止）、graph_builder.check_convergence
    （仅提示）与 Web 状态接口（诊断回显）必须共用本函数，避免出现
    「前端显示满足条件、后端用另一套阈值判定」的口径不一致。

    缺省值 = 工程初始值，可由 orchestrator.yaml 的 convergence /
    顶层轮次参数覆盖。
    """
    cfg = config if isinstance(config, dict) else {}
    cv = cfg.get("convergence", {})
    cv = cv if isinstance(cv, dict) else {}
    return {
        "attack_frequency_max": float(cv.get("attack_frequency_max", 0.20)),
        "bullying_intensity_max": float(cv.get("bullying_intensity_max", 0.20)),
        "victim_safety_min": float(cv.get("victim_safety_min", 0.70)),
        "retaliation_risk_max": float(cv.get("retaliation_risk_max", 0.30)),
        "follow_up_status_min": float(cv.get("follow_up_status_min", 0.50)),
        "victim_distress_max": float(cv.get("victim_distress_max", 0.50)),
        "bullying_stopped_required": int(cfg.get("bullying_stopped_rounds", 3)),
        "intervention_effective_required": int(cfg.get("intervention_effective_rounds", 4)),
        "victim_crisis_threshold": float(cfg.get("victim_crisis_threshold", 0.95)),
        "victim_crisis_required": int(cfg.get("victim_crisis_rounds", 3)),
        "max_rounds": int(cfg.get("max_rounds", 40)),
        "min_rounds": int(cfg.get("min_rounds", 3)),
    }


# ============================================================
# SharedState - LangGraph 全局状态
# ============================================================

@dataclass
class SharedState:
    """
    所有节点共享的状态
    在 LangGraph 的 StateGraph 中流转
    """

    # ----- 对话历史 -----
    messages: List[Message] = field(default_factory=list)

    # ----- 轮次控制 -----
    round: int = 0
    max_rounds: int = 40
    min_rounds: int = 3

    # ----- 评估指标（核心状态）-----
    bullying_intensity: float = 0.0   # 霸凌强度 0.0-1.0
    victim_distress: float = 0.0      # 受害者压力 0.0-1.0
    bystander_intervention: float = 0.0  # 旁观者干预指数 0.0-1.0

    # ----- 霸凌级别（场景难度）-----
    level: int = 2  # 1-5级

    # ----- 场景信息 -----
    scenario_title: str = ""
    scenario_description: str = ""

    # ----- 干预相关 -----
    last_intervention: str = ""
    last_intervention_round: int = -1
    intervention_count: int = 0
    intervention_quality_history: List[float] = field(default_factory=list)  # 干预质量历史
    last_intervention_effective: bool = False  # 上次干预是否有效

    # ----- 终止条件跟踪 -----
    bullying_stopped_rounds: int = 0   # 连续无攻击轮数
    intervention_effective_rounds: int = 0  # 连续有效干预轮数
    victim_crisis_rounds: int = 0       # 受害者危机轮数（压力>95%）
    # 最近一次已被计入连续计数的数据快照轮次（轮首判定与评估后判定共用，
    # 保证同一轮的数据至多累加一次，见 supervisor.check_termination_once）
    last_term_round: int = -1

    # ----- Agent 独立记忆 -----
    agent_memories: Dict[str, AgentMemory] = field(default_factory=dict)

    # ----- 发言队列 -----
    speaking_queue: List[str] = field(default_factory=list)  # 本轮谁应该发言
    active_agent: Optional[str] = None  # 当前活跃的Agent ID

    # ----- 终止控制 -----
    finished: bool = False
    termination_reason: str = ""
    result_type: str = ""  # "success" | "failure" | "timeout"

    # ----- 干预缓冲（等待Evaluator处理）-----
    pending_intervention: Optional[str] = None  # 待处理的干预内容

    # ----- 评估缓冲（等待Evaluator计算）-----
    pending_messages: List[Message] = field(default_factory=list)  # 待评估的消息

    # ----- 指标历史（用于图表）-----
    metrics_history: List[Dict] = field(default_factory=list)

    # ----- 配置参数（从 orchestrator.yaml 加载）-----
    config: Dict = field(default_factory=dict)

    # ----- 自定义角色映射（每会话独立副本，不污染全局 AGENT_ROLE_MAP）-----
    role_map: Dict = field(default_factory=dict)

    # ----- OBVQ 累计状态（Step 2 新增）-----
    obvq_state: OBVQState = field(default_factory=OBVQState)

    # ----- Agent回复缓冲 -----
    agent_response: Optional[str] = None  # 当前Agent的回复内容

    # ----- 状态转移日志（Phase 2：StateTransitionEngine 唯一写入点记录）-----
    transition_log: List[Dict] = field(default_factory=list)

    # ----- Phase 3：内部细粒度状态（0-1，与教师百分制评分不同量）-----
    # 依据设计稿 6.1，逐步增加可独立演变的状态；bullying_intensity 由 F/S/P 派生兼容前端。
    attack_frequency: float = 0.0       # 欺凌行为当前出现频率/活跃程度
    attack_severity: float = 0.0        # 单次事件严重程度
    persistence: float = 0.0            # 持续/反复/转向隐蔽欺凌的倾向
    power_imbalance: float = 0.0        # 受害者拒绝/求助/脱离困境的难度
    victim_safety: float = 0.0          # 受害者当前感受到及实际获得的安全保障
    bully_resistance: float = 0.0       # 欺凌者抵抗干预/表面服从/转向隐蔽的倾向
    bystander_support: float = 0.0      # 旁观者支持/报告/维护规范的倾向
    retaliation_risk: float = 0.0       # 因暴露/强迫对质等产生的报复风险
    follow_up_status: float = 0.0       # 后续行动待办/已完成/失败/逾期状态

    # ----- R7：干预残余效应（方案 §8.3，独立于动态状态；每轮无新增证据时按保留比例衰减）-----
    immediate_stop_residual: float = 0.0   # D1 即时制止残余
    support_residual: float = 0.0          # D2 个体支持残余
    norm_residual: float = 0.0             # D5 群体规范残余

    # ----- R24：已应用状态副作用的风险码（同一风险不重复惩罚状态）-----
    # 背景：同一风险码若每轮重复施加 victim_safety 负效应与 retaliation_risk 正效应，
    # 会把「持续使用同一干预话术」放大为状态持续恶化，导致收敛四条件永不满足、
    # 自动结束（调解成功）机制失效。此处记录已生效的风险码，只计首次。
    applied_risk_effects: Set[str] = field(default_factory=set)

    # ----- R8：内部状态轨迹历史（不能只保留汇总 B）-----
    internal_state_history: List[Dict] = field(default_factory=list)

    # ----- Phase 4：最近干预的维度分与风险等级（供 p_attack / 收敛判断用）-----
    last_intervention_dimensions: Dict = field(default_factory=dict)  # {D1..D7: score或None}
    last_intervention_risk_level: str = "none"  # none | attention | serious

    # ----- Phase 5：观察制效果评估（10.4：不得因高分立即设 effective）-----
    # 干预后先记 unverified，下一轮根据 attack_frequency 实际变化判定。
    observed_effectiveness: str = "unverified"  # unverified | verified_effective | verified_ineffective
    pending_intervention_observation: Optional[Dict] = None  # {round, attack_frequency_before}

    def add_message(self, msg: Message):
        self.messages.append(msg)
        self.pending_messages.append(msg)

    def get_recent_messages(self, n: int = 8) -> List[Message]:
        return self.messages[-n:] if self.messages else []

    def derive_bullying_intensity(self) -> float:
        """由 F/S/P 派生旧 bullying_intensity（0-1，工程初始式 B=0.40F+0.35S+0.25P）。
        仅用于前端兼容；权重非实证结论，需同时保留 F/S/P 轨迹。"""
        return round(0.40 * self.attack_frequency + 0.35 * self.attack_severity + 0.25 * self.persistence, 4)

    def get_internal_states(self) -> dict:
        """返回 Phase 3 内部状态快照（供日志/调试）。R7：含残余效应。"""
        return {
            "attack_frequency": round(self.attack_frequency, 4),
            "attack_severity": round(self.attack_severity, 4),
            "persistence": round(self.persistence, 4),
            "power_imbalance": round(self.power_imbalance, 4),
            "victim_safety": round(self.victim_safety, 4),
            "bully_resistance": round(self.bully_resistance, 4),
            "bystander_support": round(self.bystander_support, 4),
            "retaliation_risk": round(self.retaliation_risk, 4),
            "follow_up_status": round(self.follow_up_status, 4),
            "immediate_stop_residual": round(self.immediate_stop_residual, 4),
            "support_residual": round(self.support_residual, 4),
            "norm_residual": round(self.norm_residual, 4),
        }

    def append_internal_state_history(self) -> None:
        """R8（方案 §6.1）：记录内部状态全量快照轨迹，不能只保留汇总 B。"""
        snap = self.get_internal_states()
        snap["round"] = self.round
        snap["bullying_intensity"] = round(self.bullying_intensity, 4)
        snap["victim_distress"] = round(self.victim_distress, 4)
        snap["bystander_intervention"] = round(self.bystander_intervention, 4)
        self.internal_state_history.append(snap)

    def get_auto_end_status(self) -> dict:
        """自动结束判定的诊断字段（状态接口、/run metrics、前端 UI 共用）。

        目的：让「自动结束还差什么」对用户可见，避免 UI 只显示派生强度 B
        而真实判定还卡在不可见的 attack_frequency（B<max 不蕴含 F<max）。
        字段名固定，供前端与测试对齐。
        """
        p = get_termination_params(self.config)
        required = p["bullying_stopped_required"]
        currently_stopped = (
            self.attack_frequency < p["attack_frequency_max"]
            and self.bullying_intensity < p["bullying_intensity_max"]
        )
        # 若本轮已不再满足行为停止条件，连续计数会在下次判定时归零，
        # 因此对外提示的「还差几轮」按重新累计计算。
        streak = self.bullying_stopped_rounds if currently_stopped else 0
        # P3：还要计入 min_rounds 保护——round < min_rounds 时即使连续达标也不结束。
        # wait_for_min 表示「还需经过多少轮才越过最少轮次限制」：
        # round=0 时 = min_rounds-1，round>=min_rounds-1 时 = 0。
        wait_for_min = max(0, p["min_rounds"] - self.round - 1)
        remaining = wait_for_min + max(0, required - streak)
        return {
            "attack_frequency": round(self.attack_frequency, 3),
            "attack_severity": round(self.attack_severity, 3),
            "persistence": round(self.persistence, 3),
            "victim_safety": round(self.victim_safety, 3),
            "follow_up_status": round(self.follow_up_status, 3),
            "retaliation_risk": round(self.retaliation_risk, 3),
            "bullying_stopped_rounds": int(self.bullying_stopped_rounds),
            "bullying_stopped_required": required,
            "attack_frequency_max": p["attack_frequency_max"],
            "bullying_intensity_max": p["bullying_intensity_max"],
            "remaining_to_stop": remaining,
        }

    def to_summary_dict(self) -> dict:
        """用于Web界面的简洁状态摘要"""
        obvq_info = self.obvq_state.get_score_info()
        # 将 dict 转为有序数组（前端雷达图用）
        dim_order = ["physical", "verbal", "relational", "cyber", "power", "prejudice"]
        dim_totals_arr = [self.obvq_state.dimension_totals.get(d, 0) for d in dim_order]
        dim_avgs = self.obvq_state.get_dimension_averages()
        dim_avgs_arr = [round(dim_avgs.get(d, 0.0), 3) for d in dim_order]
        return {
            "round": self.round,
            "level": self.level,
            "bullying_intensity": round(self.bullying_intensity, 3),
            "victim_distress": round(self.victim_distress, 3),
            "bystander_intervention": round(self.bystander_intervention, 3),
            "message_count": len(self.messages),
            "intervention_count": self.intervention_count,
            "finished": self.finished,
            "termination_reason": self.termination_reason,
            "result_type": self.result_type,
            "active_agent": self.active_agent,
            # 自动结束判定诊断字段（§1：隐藏门槛必须可回显）
            **self.get_auto_end_status(),
            # OBVQ 字段（仅得分）
            "obvq_total_score": obvq_info["total_score"],
            "obvq_dimension_totals": dim_totals_arr,
            "obvq_dimension_averages": dim_avgs_arr,
        }


# ============================================================
# 工厂函数
# ============================================================

def create_initial_state(level: int = 2, max_rounds: int = 40, config: dict = None, scenario: dict = None, custom_participants: dict = None, role_map: dict = None) -> SharedState:
    """
    创建初始状态

    Args:
        level: 霸凌级别 (1-5)
        max_rounds: 最大轮数
        config: 配置参数字典（从 orchestrator.yaml 加载）
        scenario: 情境字典（title, description）
        custom_participants: 自定义参与角色（key=role_id, value={name, role, ...}）
        role_map: 当前会话的角色映射副本（AGENT_ROLE_MAP + 自定义覆盖），不传则读取全局
    """
    # 默认的级别初始值（来自原 orchestrator.py）
    bullying_base = {1: 0.15, 2: 0.30, 3: 0.45, 4: 0.60, 5: 0.75}
    distress_base = {1: 0.20, 2: 0.35, 3: 0.50, 4: 0.65, 5: 0.80}

    # ★ 从 role_map 读取角色名，不传则 fallback 全局 AGENT_ROLE_MAP
    if role_map is None:
        from .agents import AGENT_ROLE_MAP as _am
        role_map = _am
    else:
        _am = role_map

    agent_memories = {
        "bully":     AgentMemory(agent_id="bully",     name=_am.get("bully", {}).get("name", "陈浩")),
        "victim":    AgentMemory(agent_id="victim",    name=_am.get("victim", {}).get("name", "思远")),
        "bystander_a": AgentMemory(agent_id="bystander_a", name=_am.get("bystander_a", {}).get("name", "林雨桐")),
        "bystander_b": AgentMemory(agent_id="bystander_b", name=_am.get("bystander_b", {}).get("name", "赵磊")),
    }

    # ★ 场景文本中的硬编码人名替换为当前角色名
    scenario_title = ""
    scenario_description = ""
    if scenario:
        # 构建 "默认名→实际名" 替换映射
        default_names = {"陈浩": _am.get("bully", {}).get("name", "陈浩"),
                         "思远": _am.get("victim", {}).get("name", "思远"),
                         "林雨桐": _am.get("bystander_a", {}).get("name", "林雨桐"),
                         "赵磊": _am.get("bystander_b", {}).get("name", "赵磊")}
        name_map = {k: v for k, v in default_names.items() if v != k}  # 只保留实际改过的
        st = scenario.get("title", "")
        sd = scenario.get("description", "")
        for old, new in name_map.items():
            st = st.replace(old, new)
            sd = sd.replace(old, new)
        scenario_title = st
        scenario_description = sd

    return SharedState(
        round=0,
        max_rounds=max_rounds,
        min_rounds=3,
        level=level,
        bullying_intensity=bullying_base.get(level, 0.30),
        victim_distress=distress_base.get(level, 0.35),
        bystander_intervention=0.0,
        # Phase 3：初始化内部状态，使 B=0.40F+0.35S+0.25P 与旧 bullying_intensity 一致
        # 初始按 F=S=P 均等分配（F=S=P=base），则 B = base*(0.40+0.35+0.25) = base
        attack_frequency=bullying_base.get(level, 0.30),
        attack_severity=bullying_base.get(level, 0.30),
        persistence=bullying_base.get(level, 0.30),
        power_imbalance=0.0,
        victim_safety=0.0,
        bully_resistance=0.0,
        bystander_support=0.0,
        retaliation_risk=0.0,
        follow_up_status=0.0,
        agent_memories=agent_memories,
        config=config or {},
        metrics_history=[],
        scenario_title=scenario_title,
        scenario_description=scenario_description,
        obvq_state=OBVQState(),
        role_map=role_map,
    )


def message_to_display(msg: Message) -> dict:
    """格式化消息为前端显示格式"""
    role_icons = {
        "bully": "😈",
        "victim": "😢",
        "bystander_a": "✨",
        "bystander_b": "👀",
        "teacher": "👨‍🏫",
    }
    role_names = {
        "bully": "霸凌者",
        "victim": "受害者",
        "bystander_a": "正义旁观者",
        "bystander_b": "从众旁观者",
        "teacher": "教师",
    }
    return {
        "id": f"{msg.sender_id}_{msg.round}",
        "sender": msg.sender_name,
        "role": msg.role,
        "role_name": role_names.get(msg.role, msg.role),
        "role_icon": role_icons.get(msg.role, "💬"),
        "content": msg.content,
        "timestamp": msg.timestamp,
        "round": msg.round,
    }

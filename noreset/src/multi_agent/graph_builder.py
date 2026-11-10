"""
LangGraph Graph Builder - Step-by-Step Mode
Each round runs independently, replacing LangGraph invoke()
"""

from typing import Optional
from langgraph.graph import StateGraph, END

from .state import (SharedState, create_initial_state, message_to_display,
                    Message as StateMessage, get_termination_params)
from .llm_client import LLMClient, get_api_key


# ============================================================
# Global LLM client
# ============================================================

_llm_client: Optional[LLMClient] = None


def set_llm_client(client: Optional[LLMClient]):
    global _llm_client
    _llm_client = client


def get_llm_client() -> Optional[LLMClient]:
    return _llm_client


# ============================================================
# 自动收敛检测 (2026-08-06 新增)
# ============================================================

def check_convergence(state: SharedState) -> dict:
    """
    每轮评估后检查是否满足收敛条件。
    返回收敛结果，供前端弹窗提示教师（不强制结束）。
    调用方负责去重（同一会话只触发一次）。
    """
    msgs = state.messages
    round_count = state.round
    intensity = state.bullying_intensity

    # R10：收敛阈值可由 config convergence 段覆盖（缺省=工程初始值，须校准）
    # 与 supervisor._check_termination / Web 状态接口共用 get_termination_params，
    # 保证「提示口径」与「真实终止口径」不会出现两套阈值。
    _cfg = getattr(state, "config", {}) or {}
    _cv = _cfg.get("convergence", {}) if isinstance(_cfg, dict) else {}
    _cv = _cv if isinstance(_cv, dict) else {}
    _params = get_termination_params(_cfg)
    thr_intensity = _params["bullying_intensity_max"]
    thr_attack = _params["attack_frequency_max"]
    thr_safety = _params["victim_safety_min"]
    thr_retaliation = _params["retaliation_risk_max"]
    thr_followup = _params["follow_up_status_min"]
    partial_round = int(_cv.get("partial_round_min", 10))
    partial_intensity = float(_cv.get("partial_intensity_max", 0.30))

    # DEBUG: 记录每次检查的数值
    print(f"[Convergence] Round {round_count}, intensity={intensity:.3f}, triggered={getattr(state, '_conv_checked', False)}")
    
    # ── 条件 A: 已运行 >=10 轮, intensity 仍 > 0.3 -> 部分收敛 ──
    if round_count >= partial_round and intensity > partial_intensity:
        return {
            "convergence": "partial",
            "title": "实训已进行较长时间",
            "message": "霸凌行为未完全消除，这在实际教育中是常见的——改变往往需要多次介入。建议查看报告了解当前干预效果。",
            "suggestion": "view_report",
        }

    # ── 条件 1: bullying_intensity < 0.20（必须） ──
    if intensity >= thr_intensity:
        return {"convergence": None}

    # ── Phase 4: 派生强度之外，还须查内部安全状态（设计稿 13.4）──
    # 新攻击、受害者安全不足、报复风险、未完成跟进任一项未达标则不收敛。
    if getattr(state, 'attack_frequency', 0.0) >= thr_attack:
        return {"convergence": None}
    if getattr(state, 'victim_safety', 0.0) < thr_safety:
        return {"convergence": None}
    if getattr(state, 'retaliation_risk', 0.0) >= thr_retaliation:
        return {"convergence": None}
    if getattr(state, 'follow_up_status', 0.0) < thr_followup:
        return {"convergence": None}

    # ── 条件 4: 已运行 >=8 轮（必须） ──
    if round_count < 8:
        return {"convergence": None}

    # ── 条件 2: 最近 3 轮无新的欺凌内容 ──
    bully_msgs = [m for m in msgs[-20:] if getattr(m, 'sender_id', None) == 'bully']
    recent_bully = bully_msgs[-3:] if len(bully_msgs) >= 3 else bully_msgs
    for m in recent_bully:
        hs = getattr(m, 'hostile_score', 0) or 0
        if hs >= 0.2:
            return {"convergence": None}

    # ── 条件 3: 最近 2 轮 victim 无负面情绪 ──
    victim_msgs = [m for m in msgs[-15:] if getattr(m, 'sender_id', None) == 'victim']
    recent_victim = victim_msgs[-2:] if len(victim_msgs) >= 2 else victim_msgs
    negative_emotions = {'sad', 'fearful', 'self_doubt', 'anxiety'}
    for m in recent_victim:
        em = getattr(m, 'emotions', []) or []
        if any(e in negative_emotions for e in em):
            return {"convergence": None}

    # ── 全部满足 -> 已解决 ──
    return {
        "convergence": "resolved",
        "title": "霸凌行为已显著缓解",
        "message": "当前霸凌强度已降至较低水平，受害者情绪趋于稳定。本轮实训可结束，是否查看报告？",
        "suggestion": "view_report",
    }


# ============================================================
# Node functions (lazy import to avoid circular deps)
# ============================================================

def _supervisor_node(state) -> dict:
    from .supervisor import _supervisor_logic
    return _supervisor_logic(state)


def _bully_node(state) -> dict:
    from .supervisor import _agent_logic
    return _agent_logic(state, "bully")


def _victim_node(state) -> dict:
    from .supervisor import _agent_logic
    return _agent_logic(state, "victim")


def _bystander_a_node(state) -> dict:
    from .supervisor import _agent_logic
    return _agent_logic(state, "bystander_a")


def _bystander_b_node(state) -> dict:
    from .supervisor import _agent_logic
    return _agent_logic(state, "bystander_b")


def _evaluate_node(state) -> dict:
    from .evaluator_node import _evaluator_logic
    return _evaluator_logic(state)


def _route_to_agents(state) -> str:
    from .supervisor import _route_to_agents as route
    return route(state)


# ============================================================
# Step-by-step runner (replaces LangGraph invoke)
# ============================================================

class StepRunner:
    """
    Step-by-step runner.
    Replaces LangGraph invoke(), each round runs independently.
    """

    def __init__(self, state: SharedState, llm_client: Optional[LLMClient] = None):
        self.state = state
        self.llm_client = llm_client
        self._convergence_triggered = False  # 收敛弹窗只触发一次
        set_llm_client(llm_client)

    def step(self) -> dict:
        """
        Execute one complete round.
        Returns: {"done": bool, "messages": [...], "status": {...}}
        """
        # 1. Supervisor: decide who speaks
        self._prev_msg_count = len(self.state.messages)
        result = _supervisor_node(self.state)

        if result.get("finished"):
            self.state.finished = True
            self.state.termination_reason = result.get("termination_reason", "")
            self.state.result_type = result.get("result_type", "")
            return self._build_response(done=True)

        # 2. Process speaking queue
        self._process_speaking_queue()

        # 3. Evaluator: evaluate all messages
        _evaluate_node(self.state)

        # 3b. 评估后立即判定：让「条件刚好在本轮达成」的 /run 响应直接 done=true，
        # 不必等用户再点一轮。check_termination_once 以本轮数据快照去重，
        # 轮首已计过的上一轮快照不会重复累加。
        from .supervisor import check_termination_once
        after_result = check_termination_once(self.state, snapshot_round=self.state.round)
        if after_result.get("should_end"):
            self.state.finished = True
            self.state.termination_reason = after_result.get("reason", "")
            self.state.result_type = after_result.get("result_type", "")
            return self._build_response(done=True)

        # 4. Convergence check (runs each round, but only fires once)
        convergence = {"convergence": None}
        if not self._convergence_triggered:
            convergence = check_convergence(self.state)
            if convergence.get("convergence"):
                self._convergence_triggered = True

        return self._build_response(done=False, convergence=convergence)

    def _process_speaking_queue(self):
        """Process all agents in the speaking queue"""
        while self.state.speaking_queue:
            agent_id = self.state.speaking_queue[0]

            if agent_id == "bully":
                result = _bully_node(self.state)
            elif agent_id == "victim":
                result = _victim_node(self.state)
            elif agent_id == "bystander_a":
                result = _bystander_a_node(self.state)
            elif agent_id == "bystander_b":
                result = _bystander_b_node(self.state)
            else:
                break

            self._merge_result(result)

    def _merge_result(self, result: dict):
        """Merge node return values into state"""
        if not result:
            return

        for key, value in result.items():
            if key == "messages":
                existing = getattr(self.state, key, [])
                if isinstance(existing, list) and isinstance(value, list):
                    # Deduplicate by sender_id+round
                    existing_ids = set()
                    for m in existing:
                        if hasattr(m, "sender_id"):
                            existing_ids.add(f"{m.sender_id}_{getattr(m, 'round', 0)}")
                        elif isinstance(m, dict):
                            existing_ids.add(f"{m.get('sender_id', '')}_{m.get('round', 0)}")
                    for msg in value:
                        if hasattr(msg, "sender_id"):
                            msg_id = f"{msg.sender_id}_{getattr(msg, 'round', 0)}"
                        elif isinstance(msg, dict):
                            msg_id = f"{msg.get('sender_id', '')}_{msg.get('round', 0)}"
                        else:
                            msg_id = str(id(msg))
                        if msg_id not in existing_ids:
                            existing.append(msg)
            elif key == "pending_messages":
                existing = getattr(self.state, key, [])
                if isinstance(existing, list) and isinstance(value, list):
                    existing.extend(value)
            else:
                setattr(self.state, key, value)

    def _build_response(self, done: bool, convergence: dict = None) -> dict:
        resp = {
            "done": done,
            "finished": self.state.finished,
            "termination_reason": self.state.termination_reason,
            "result_type": self.state.result_type,
            "round": self.state.round,
            "metrics": {
                "bullying_intensity": self.state.bullying_intensity,
                "victim_distress": self.state.victim_distress,
                "bystander_intervention": self.state.bystander_intervention,
                # 自动结束判定诊断字段（隐藏门槛回显，字段名与 state 接口一致）
                **self.state.get_auto_end_status(),
            },
            "messages": [message_to_display(m) for m in self.state.messages],
            # 诊断字段：看后端 check_convergence 到底拿到了什么
            "_conv_diag": {
                "round": self.state.round,
                "intensity": self.state.bullying_intensity,
                "triggered": self._convergence_triggered,
                "convergence": convergence.get("convergence") if convergence else None,
            },
        }
        if convergence and convergence.get("convergence"):
            resp["convergence"] = convergence
        return resp


# ============================================================
# MultiAgentRunner
# ============================================================

# 系统默认角色名（用于判断前端是否"自定义"了角色名）
_DEFAULT_ROLE_NAMES = {"陈浩", "思远", "林雨桐", "赵磊"}


def _merge_personality(base, override):
    """override 非空字段覆盖 base，空字段保留 base。"""
    merged = dict(base or {})
    for k, v in (override or {}).items():
        if v:
            merged[k] = v
    return merged


class MultiAgentRunner:
    """
    Multi-agent runner using step-by-step execution.
    """

    def __init__(
        self,
        level: int = 2,
        max_rounds: int = 40,
        use_llm: bool = True,
        config: dict = None,
        session_id: str = None,
        scenario: dict = None,
        custom_participants: dict = None,
    ):
        self.level = level
        self.max_rounds = max_rounds
        self.use_llm = use_llm
        self.config = config or {}
        self.session_id = session_id or f"sim_{int(__import__('time').time())}"
        self.scenario = scenario
        self.custom_participants = custom_participants or {}

        self.llm_client: Optional[LLMClient] = None
        if use_llm:
            api_key = get_api_key()
            if api_key:
                self.llm_client = LLMClient(api_key)
                print(f"[Runner] LLM client initialized (model={self.llm_client.model}, "
                      f"base={self.llm_client.base_url})")
            else:
                # R17：无密钥 → 专家评分全部为「待评估」，必须显式告警而非静默降级
                print("[Runner][WARNING] 未找到 API Key：专家评分将全部为「待评估」，"
                      "干预评分与指导提示均不可用")
                try:
                    from .llm_client import _env_search_paths
                    print("[Runner][WARNING] 已检查 .env 路径: "
                          + ", ".join(str(p) for p in _env_search_paths()))
                except Exception:
                    pass
                print("[Runner][WARNING] 请配置 .env（DEEPSEEK_API_KEY / MINIMAX_API_KEY / "
                      "OPENCLAW_API_KEY）后重启")

        import copy
        from .agents import AGENT_ROLE_MAP as _global_am

        # ★ 创建当前会话的独立角色映射副本（不污染全局 AGENT_ROLE_MAP）
        self._role_map = {k: dict(v) for k, v in _global_am.items()}

        # ★ 场景绑定人设（cast）：作为基础人设注入（不同场景绑定不同角色风格与名字）
        if scenario and scenario.get("cast"):
            for role_key, cdata in scenario["cast"].items():
                if role_key not in self._role_map:
                    continue
                cur = self._role_map[role_key]
                cur["name"] = cdata.get("name") or cur.get("name")
                cur["role_name"] = cdata.get("role_name") or cur.get("role_name")
                cur["personality"] = _merge_personality(cur.get("personality"), cdata.get("personality"))
            print(f"[Runner] Scenario cast applied: {list(scenario['cast'].keys())}")

        # ★ 自定义参与角色：只覆盖用户显式自定义的部分（默认名/空人设不覆盖场景 cast）
        if self.custom_participants:
            for pid, pdata in self.custom_participants.items():
                role_key = pdata.get("role", pid)
                if role_key in self._role_map:
                    cur = self._role_map[role_key]
                elif pid in self._role_map:
                    cur = self._role_map[pid]
                else:
                    continue
                # name：用户显式自定义（非系统默认名）才覆盖场景 cast/默认名
                pname = (pdata.get("name") or "").strip()
                if pname and pname not in _DEFAULT_ROLE_NAMES:
                    cur["name"] = pname
                rn = (pdata.get("role_name") or "").strip()
                if rn:
                    cur["role_name"] = rn
                cur["personality"] = _merge_personality(cur.get("personality"), pdata.get("personality"))
            print(f"[Runner] Session role_map: {len(self.custom_participants)} custom, global untouched")

        # ★ 构建 scenario 名映射并参数化文本（使用当前会话的 role_map）
        self._param_scenario = None
        if scenario:
            _am = self._role_map
            default_names = {"陈浩": _am.get("bully", {}).get("name", "陈浩"),
                             "思远": _am.get("victim", {}).get("name", "思远"),
                             "林雨桐": _am.get("bystander_a", {}).get("name", "林雨桐"),
                             "赵磊": _am.get("bystander_b", {}).get("name", "赵磊")}
            name_map = {k: v for k, v in default_names.items() if v != k}
            st = scenario.get("title", "")
            sd = scenario.get("description", "")
            for old, new in name_map.items():
                st = st.replace(old, new)
                sd = sd.replace(old, new)
            self._param_scenario = {"title": st, "description": sd}

        self.state = create_initial_state(
            level=level,
            max_rounds=max_rounds,
            config=config,
            scenario=self._param_scenario or scenario,
            custom_participants=self.custom_participants,
            role_map=self._role_map,
        )
        # R5：会话 ID 写入状态，供 assessment_id 与状态日志追溯
        self.state.session_id = self.session_id

        self._step_runner = StepRunner(self.state, self.llm_client)
        self._prev_msg_count = 0

        print(f"[Runner] Ready (level={level}, max_rounds={max_rounds})")

    def run_round(self) -> dict:
        """Run one round of dialogue, returns only new messages since last call"""
        if self.state.finished:
            return {
                "done": True,
                "finished": True,
                "termination_reason": self.state.termination_reason,
                "result_type": self.state.result_type,
                "round": self.state.round,
                "metrics": self._get_metrics(),
                "messages": [],
            }

        prev_count = self._prev_msg_count
        resp = self._step_runner.step()
        resp["messages"] = [message_to_display(m) for m in self.state.messages[prev_count:]]
        self._prev_msg_count = len(self.state.messages)
        return resp

    def intervene(self, intervention_content: str) -> dict:
        """Teacher intervention"""
        from .evaluator_node import evaluate_intervention
        from datetime import datetime as _dt

        # ★ 关键修复：将教师干预消息注入 state.messages，
        # 让学生智能体在下一轮生成回复时能在对话上下文中看到老师说了什么，
        # 而不仅仅影响参数（bullying_intensity/victim_distress）
        teacher_msg = StateMessage(
            sender_id="teacher",
            sender_name="张老师",
            role="teacher",
            content=intervention_content,
            timestamp=_dt.now().strftime("%H:%M:%S"),
            round=self.state.round,
        )
        self.state.messages.append(teacher_msg)

        # 设置待处理干预标记（供 supervisor/agent decide 使用）
        self.state.pending_intervention = intervention_content

        return evaluate_intervention(self.state, intervention_content, llm_client=self.llm_client)

    def get_status(self) -> dict:
        return self.state.to_summary_dict()

    def get_metrics(self) -> dict:
        return self._get_metrics()

    def _get_metrics(self) -> dict:
        obvq_info = self.state.obvq_state.get_score_info()
        dim_order = ["physical", "verbal", "relational", "cyber", "power", "prejudice"]
        dim_totals_arr = [self.state.obvq_state.dimension_totals.get(d, 0) for d in dim_order]
        dim_avgs = self.state.obvq_state.get_dimension_averages()
        dim_avgs_arr = [round(dim_avgs.get(d, 0.0), 3) for d in dim_order]
        return {
            "bullying_intensity": self.state.bullying_intensity,
            "victim_distress": self.state.victim_distress,
            "bystander_intervention": self.state.bystander_intervention,
            "round": self.state.round,
            "finished": self.state.finished,
            "termination_reason": self.state.termination_reason,
            "result_type": self.state.result_type,
            # 自动结束判定诊断字段（与 to_summary_dict / _build_response 同源）
            **self.state.get_auto_end_status(),
            # OBVQ metrics（仅得分）
            "obvq_total_score": obvq_info["total_score"],
            "obvq_dimension_totals": dim_totals_arr,
            "obvq_dimension_averages": dim_avgs_arr,
        }


def create_runner(
    level: int = 2,
    max_rounds: int = 40,
    use_llm: bool = True,
    config: dict = None,
    session_id: str = None,
    scenario: dict = None,
    custom_participants: dict = None,
) -> MultiAgentRunner:
    return MultiAgentRunner(
        level=level,
        max_rounds=max_rounds,
        use_llm=use_llm,
        config=config,
        session_id=session_id,
        scenario=scenario,
        custom_participants=custom_participants,
    )

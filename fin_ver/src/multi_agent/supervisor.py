"""
Supervisor logic for multi-agent coordination
"""

from datetime import datetime
from .state import SharedState, Message, get_termination_params
from .agents import create_all_agents, AgentDecision
from .llm_client import get_preset_response
from .graph_builder import get_llm_client

import re  # 导入正则表达式模块，用于过滤思考内容

# 导入思考内容过滤器
from .thinking_filter import filter_thinking_content


# ============================================================
# Agent name mapping
# ============================================================

AGENT_INFO = None  # 延迟初始化，由 _get_agent_info() 动态读取 AGENT_ROLE_MAP


def _get_agent_info(role_map: dict = None):
    """从 role_map 动态读取角色显示信息（支持自定义名称）。
    优先使用传入的 role_map（会话级），否则 fallback 全局 AGENT_ROLE_MAP。
    """
    _am = role_map if role_map else _get_global_am()
    return {
        "bully": {
            "name_cn": _am.get("bully", {}).get("name", "陈浩"),
            "name_en": "Bully",
        },
        "victim": {
            "name_cn": _am.get("victim", {}).get("name", "思远"),
            "name_en": "Victim",
        },
        "bystander_a": {
            "name_cn": _am.get("bystander_a", {}).get("name", "林雨桐"),
            "name_en": "BystanderA",
        },
        "bystander_b": {
            "name_cn": _am.get("bystander_b", {}).get("name", "赵磊"),
            "name_en": "BystanderB",
        },
    }


def _get_global_am():
    from .agents import AGENT_ROLE_MAP as _am
    return _am


# ============================================================
# Supervisor node logic
# ============================================================

def _supervisor_logic(state: SharedState) -> dict:
    """Main supervisor: decides who speaks, checks termination"""
    state.round += 1
    print(f"\n{'='*60}")
    print(f"  Round {state.round} - Supervisor")
    print(f"{'='*60}")

    # Check termination conditions
    # 轮首判定看到的是「上一轮结束时」的数据快照（round-1），
    # 与 StepRunner 评估后的本轮快照（round）分开计数，避免同一份数据被计两次。
    end_result = check_termination_once(state, snapshot_round=state.round - 1)
    if end_result["should_end"]:
        return {
            "finished": True,
            "termination_reason": end_result["reason"],
            "result_type": end_result.get("result_type", ""),
        }

    # Decide speaking order
    speaking_queue = _decide_speaking_order(state)
    state.speaking_queue = speaking_queue

    names = [state.agent_memories[a].name for a in speaking_queue]
    print(f"  Speaking order: {names}")

    if speaking_queue:
        return {
            "active_agent": speaking_queue[0],
            "round": state.round,
            "speaking_queue": speaking_queue,
        }
    else:
        return {
            "active_agent": None,
            "round": state.round,
            "speaking_queue": [],
        }


def _check_termination(state: SharedState) -> dict:
    """Check if simulation should end.

    纯判定函数：每次调用都会按当前状态推进连续计数（供单测直接调用）。
    生产链路请使用 check_termination_once()，它保证同一轮数据只计数一次。
    """
    config = state.config
    params = get_termination_params(config)
    max_rounds = params["max_rounds"]
    min_rounds = params["min_rounds"]
    bully_stop_threshold = params["bullying_stopped_required"]
    interv_eff_threshold = params["intervention_effective_required"]
    crisis_threshold = params["victim_crisis_threshold"]
    crisis_rounds_threshold = params["victim_crisis_required"]

    result = {"should_end": False, "reason": ""}

    # Victim crisis → 调解失败
    if state.victim_distress >= crisis_threshold:
        state.victim_crisis_rounds += 1
        if state.victim_crisis_rounds >= crisis_rounds_threshold:
            result["should_end"] = True
            result["reason"] = f"受害者压力过大，调解失败。"
            result["result_type"] = "failure"
            print(f"  [TERM] {result['reason']}")
            return result
    else:
        state.victim_crisis_rounds = 0

    # Max rounds → 超时
    if state.round >= max_rounds:
        result["should_end"] = True
        result["reason"] = f"对话轮次已达上限，未能有效解决。"
        result["result_type"] = "timeout"
        print(f"  [TERM] {result['reason']}")
        return result

    # Bullying stopped → 调解成功
    # Phase 4：收敛不再只靠 bullying_intensity（派生值），改为查内部状态。
    # R10：收敛阈值可由 config convergence 段覆盖（缺省=工程初始值，须校准）
    # 阈值统一由 get_termination_params 提供，与 check_convergence / UI 同源。
    thr_attack = params["attack_frequency_max"]
    thr_intensity = params["bullying_intensity_max"]
    thr_safety = params["victim_safety_min"]
    thr_retaliation = params["retaliation_risk_max"]
    thr_followup = params["follow_up_status_min"]
    thr_distress = params["victim_distress_max"]

    # R26/R27：欺凌行为停止 = 欺凌强度与攻击频率双达标（二者强相关，均为用户可观测指标）。
    # R27 修复：victim_distress / retaliation_risk 不再作为阻止停止的硬门槛——
    # 实测「坚定制止」把强度压到 5% 以下但受害者压力未恢复时，会话会因这两个门槛
    # 永远无法停止、只能超时。它们改为只影响结束文案的分类（提醒教师后续仍需处理），
    # 不阻塞会话收敛；教师干预质量由报告端评分体现。
    activity_stopped = (
        state.attack_frequency < thr_attack
        and state.bullying_intensity < thr_intensity
    )
    intervention_effective = (
        state.victim_safety >= thr_safety
        and state.follow_up_status >= thr_followup
    )
    bullying_resolved = activity_stopped and intervention_effective
    if activity_stopped and state.round >= min_rounds:
        state.bullying_stopped_rounds += 1
        if state.bullying_stopped_rounds >= bully_stop_threshold:
            result["should_end"] = True
            # 结束文案按「未完成项」分类，如实提示后续仍需处理，但不阻塞停止
            if state.victim_distress >= thr_distress:
                result["reason"] = (f"欺凌行为已连续 {bully_stop_threshold} 轮停止，"
                                    f"但受害者压力仍未恢复（{state.victim_distress:.0%}），请继续关注安抚与支持。")
            elif state.retaliation_risk >= thr_retaliation:
                result["reason"] = (f"欺凌行为已连续 {bully_stop_threshold} 轮停止，"
                                    f"但仍存在报复风险，请注意隔离与后续跟进。")
            elif not intervention_effective:
                result["reason"] = (f"欺凌行为已连续 {bully_stop_threshold} 轮停止，"
                                    f"教师保护与跟进记录不足，按实际干预评分。")
            else:
                result["reason"] = f"欺凌现象停止，调解成功。"
            result["result_type"] = "success"
            print(f"  [TERM] {result['reason']}")
            return result
    elif state.attack_frequency >= thr_attack or state.bullying_intensity >= thr_intensity:
        state.bullying_stopped_rounds = 0

    # Effective intervention → 调解成功
    if state.last_intervention_effective and state.intervention_effective_rounds >= 0:
        state.intervention_effective_rounds += 1
        if state.intervention_effective_rounds >= interv_eff_threshold and bullying_resolved:
            result["should_end"] = True
            result["reason"] = f"干预持续有效，调解成功。"
            result["result_type"] = "success"
            print(f"  [TERM] {result['reason']}")
            return result
    else:
        state.intervention_effective_rounds = 0

    return result


def check_termination_once(state: SharedState, snapshot_round: int = None) -> dict:
    """生产链路使用的终止判定入口：同一份数据快照每轮至多计数一次。

    两个调用点：
      1) 轮首（_supervisor_logic）：数据快照 = 上一轮结束时，key = round-1；
      2) 本轮评估后（StepRunner.step）：数据快照 = 本轮结束时，key = round。
    因此「条件达成的当轮」即可结束，而同一份数据不会被重复累加。
    """
    key = state.round if snapshot_round is None else int(snapshot_round)
    if state.round > 0 and state.last_term_round == key:
        return {
            "should_end": False,
            "reason": state.termination_reason,
            "result_type": state.result_type,
            "skipped_duplicate_snapshot": True,
        }
    state.last_term_round = key
    return _check_termination(state)


def _decide_speaking_order(state: SharedState) -> list:
    """
    Coordinator.plan_round() — 按规则表决策每轮发言顺序

    规则表：
    | 场景                    | 发言顺序              | 触发原因               |
    |------------------------|----------------------|----------------------|
    | 第1轮/无干预             | 陈浩 → 赵磊          | bully_regular_turn    |
    | 高霸凌强度(>0.7)        | 陈浩主导             | bully_dominates       |
    | 好干预后                | 受害者/旁观者优先      | victim_feels_supported|
    | 差干预后                | 霸凌者可能加剧        | bully_escalates       |
    | 受害者压力大(>0.7)      | 受害者微弱反应        | victim_reacts         |

    约束：
    - 同一角色同一轮不会连续发言
    - 每轮至少3个角色参与（霸凌+受害者+至少1旁观者）
    - 霸凌者或被霸凌者每轮必须有一人参与
    - 霸凌者通常是第一个或主导发言
    - 每个角色应均衡参与，不会连续缺席多轮
    """
    import random
    ALL_AGENTS = ["bully", "victim", "bystander_a", "bystander_b"]
    queue = []

    # ---------- 计算本轮已发言角色（冷却） ----------
    current_round_msgs = [m for m in state.messages if m.round == state.round]
    just_spoke_this_round = set()
    for m in current_round_msgs:
        if m.sender_id in ALL_AGENTS and m.content not in ("沉默", ""):
            just_spoke_this_round.add(m.sender_id)

    # ---------- 计算各角色最近缺席轮数 ----------
    def rounds_since_spoke(agent_id):
        """计算某角色从上次发言到现在的轮数差"""
        agent_msgs = [m for m in state.messages if m.sender_id == agent_id and m.content not in ("沉默", "")]
        if not agent_msgs:
            # 首次发言 = 缺席了很多轮
            return state.round + 1
        last = agent_msgs[-1]
        return state.round - last.round

    # ---------- 获取干预状态 ----------
    has_intervention_last_round = any(
        m.sender_id == "teacher" and m.round == state.round - 1
        for m in state.messages
    )
    # Phase 4：不再用总分 60/40 阈值单独决定发言队列，改用 D1 作用强度（K(s)）。
    # R7：读取衰减后的「即时制止残余」，保证干预效果随时间减弱而非永久生效。
    # K_D1 高 = 有效制止 → 受害者/旁观者优先；K_D1 低且霸凌者仍具攻击性 → 霸凌者优先。
    K_D1 = max(getattr(state, 'immediate_stop_residual', 0.0), 0.0)
    _ad_cfg = (getattr(state, 'config', {}) or {}).get("agent_decision", {})
    _ad_cfg = _ad_cfg if isinstance(_ad_cfg, dict) else {}
    thr_good = float(_ad_cfg.get("good_intervention_min", 0.50))
    thr_bad = float(_ad_cfg.get("bad_intervention_max", 0.20))
    good_intervention = has_intervention_last_round and K_D1 >= thr_good
    bad_intervention = has_intervention_last_round and K_D1 < thr_bad

    # ---------- 轮换保护：确保缺席多的角色优先加入 ----------
    # 记录每轮的发言人数
    round_participants = {}
    for m in state.messages:
        if m.sender_id in ALL_AGENTS and m.content not in ("沉默", ""):
            r = m.round
            if r not in round_participants:
                round_participants[r] = set()
            round_participants[r].add(m.sender_id)

    def was_silent_last_round(agent_id):
        prev = state.round - 1
        return agent_id not in round_participants.get(prev, set())

    # ---------- 按规则表决策 ----------

    # 规则1: 第1轮 — 霸凌者率先，赵磊从众，受害者反应
    if state.round <= 1:
        queue = ["bully", "bystander_b", "victim"]

    # 规则2: 高霸凌强度 — 霸凌者主导，受害者反应，旁观者干预
    elif state.bullying_intensity > 0.7:
        queue = ["bully", "victim"]
        # 缺席轮次多的旁观者优先
        if "bystander_a" not in just_spoke_this_round:
            queue.append("bystander_a")
        elif was_silent_last_round("bystander_a") and "bystander_b" not in just_spoke_this_round:
            queue.append("bystander_b")

    # 规则3: 好干预后 — 受害者/旁观者优先，霸凌者可能收敛
    elif good_intervention:
        queue = ["victim", "bystander_a"]
        if "bully" not in just_spoke_this_round and random.random() < 0.6:
            queue.append("bully")

    # 规则4: 差干预后 — 霸凌者可能加剧
    elif bad_intervention:
        queue = ["bully", "victim", "bystander_a"]

    # 规则5: 受害者压力大 — 受害者反应，旁观者支持
    elif state.victim_distress > 0.7:
        queue = ["victim", "bystander_a"]
        if "bully" not in just_spoke_this_round:
            queue.append("bully")

    # ---------- 默认规则（兜底） ----------
    else:
        # 缺席越多的角色越优先加入
        absent_counts = {a: rounds_since_spoke(a) for a in ALL_AGENTS}
        priority = sorted(ALL_AGENTS, key=lambda a: absent_counts[a], reverse=True)

        for agent in priority:
            if agent in just_spoke_this_round:
                continue
            queue.append(agent)
            if len(queue) >= 3:
                break

    # ---------- 约束检查 ----------
    # 1. 去重
    queue = list(dict.fromkeys(queue))

    # 2. 霸凌者或受害者必须有一人参与
    if "bully" not in queue and "victim" not in queue:
        if "bully" not in just_spoke_this_round:
            queue.insert(0, "bully")
        elif "victim" not in just_spoke_this_round:
            queue.insert(0, "victim")
        else:
            queue.insert(0, "bully")

    # 3. 确保至少3人发言（避免单角色独白）
    if len(queue) < 3:
        for agent in ["bystander_a", "bystander_b", "victim", "bully"]:
            if agent not in queue and agent not in just_spoke_this_round:
                queue.append(agent)
                if len(queue) >= 3:
                    break

    # 4. 上限 4 人
    queue = queue[:4]

    # ---------- 日志 ----------
    names = [_get_agent_info(getattr(state, 'role_map', None)).get(a, {}).get("name_cn", a) for a in queue]
    reason = _classify_round_reason(state)
    print(f"  [{reason}] Speaking order: {' → '.join(names)}")

    return queue


def _classify_round_reason(state: SharedState) -> str:
    """返回本轮决策原因标签"""
    recent = state.get_recent_messages(4)
    has_intervention = any(m.sender_id == "teacher" for m in recent)
    # R7：与发言排队一致，读取衰减后的制止残余
    K_D1 = max(getattr(state, 'immediate_stop_residual', 0.0), 0.0)
    _ad_cfg = (getattr(state, 'config', {}) or {}).get("agent_decision", {})
    _ad_cfg = _ad_cfg if isinstance(_ad_cfg, dict) else {}
    thr_good = float(_ad_cfg.get("good_intervention_min", 0.50))
    thr_bad = float(_ad_cfg.get("bad_intervention_max", 0.20))

    if state.round <= 1:
        return "bully_regular_turn"
    if state.bullying_intensity > 0.7:
        return "bully_dominates"
    if has_intervention and K_D1 >= thr_good:
        return "victim_feels_supported"
    if has_intervention and K_D1 < thr_bad:
        return "bully_escalates"
    if state.victim_distress > 0.7:
        return "victim_reacts_to_attack"
    return "default_turn"


def _route_to_agents(state: SharedState) -> str:
    """Route to next node based on active_agent"""
    if state.finished:
        return "end"

    active = state.active_agent
    if active is None:
        return "evaluate"

    routing = {
        "bully": "bully",
        "victim": "victim",
        "bystander_a": "bystander_a",
        "bystander_b": "bystander_b",
    }

    return routing.get(active, "evaluate")


# ============================================================
# Agent node logic - 增强版思考内容过滤
# ============================================================

def _agent_logic(state: SharedState, agent_id: str) -> dict:
    """Execute agent response with enhanced thinking filter"""
    info = _get_agent_info(getattr(state, 'role_map', None)).get(agent_id, {"name_cn": agent_id})
    agent_name = info["name_cn"]

    llm_client = get_llm_client()

    if llm_client is None:
        response = get_preset_response(agent_id, state.level)
    else:
        from .prompts import build_system_prompt, build_conversation_context
        msg_dicts = [m.to_dict() for m in state.get_recent_messages(6)]
        context = build_conversation_context(msg_dicts)
        prompt = build_system_prompt(
            agent_id, state.level, context,
            scenario_title=state.scenario_title,
            scenario_description=state.scenario_description,
            role_map=getattr(state, 'role_map', None),
            round_num=state.round,
            bullying_intensity=state.bullying_intensity,
            victim_distress=state.victim_distress,
        )

        try:
            # 构建本轮已有的发言（避免重复）
            current_round_msgs = [m for m in state.messages if m.round == state.round and m.sender_id != agent_id]
            other_speech = ""
            if current_round_msgs:
                other_speech = "\n\n【本轮其他角色已说的话】\n" + "\n".join(
                    f"- {m.sender_name}：{m.content[:60]}"
                    for m in current_round_msgs
                )
                other_speech += "\n\n注意：你不需要回应或重复上述内容，只需说出你自己的新发言。"

            # 强调不要输出思考过程和其他角色台词
            response = llm_client.chat([
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"请直接说出你作为「{agent_name}」的发言内容。{other_speech}\n\n重要提醒：只输出你的一句话或一段发言，不要输出思考过程，不要输出其他角色的台词。"},
            ])
            
            # DEBUG: 打印LLM原始输出
            print(f"  [DEBUG] {agent_name} LLM raw: {repr(response[:200])}")
            
            # 使用专门的过滤器处理思考内容
            response = filter_thinking_content(response, agent_name)
            
            # 如果过滤器将内容全部移除，使用预设回复兜底（防御性处理）
            if not response or not response.strip():
                print(f"  [{agent_name}] filter produced empty, using preset fallback")
                response = get_preset_response(agent_id, state.level)
            
            print(f"  [DEBUG] {agent_name} after filter: {repr(response[:100])}")
            
        except Exception as e:
            print(f"  [{agent_name}] LLM error: {e}")
            response = get_preset_response(agent_id, state.level)

    # 处理沉默
    if response == "Silence" or response == "沉默" or not response.strip():
        print(f"  [{agent_name}] is silent")
        new_queue = [a for a in state.speaking_queue if a != agent_id]
        return {
            "active_agent": new_queue[0] if new_queue else None,
            "speaking_queue": new_queue,
        }

    print(f"  [{agent_name}] says: {response[:50]}...")

    msg = Message(
        sender_id=agent_id,
        sender_name=agent_name,
        role=agent_id,
        content=response,
        timestamp=datetime.now().strftime("%H:%M:%S"),
        round=state.round,
    )

    # 更新记忆
    if agent_id in state.agent_memories:
        memory = state.agent_memories[agent_id]
        memory.speaking_count += 1
        memory.last_spoke_at = msg.timestamp

        if agent_id == "victim":
            from .evaluator_node import detect_emotions
            emotions = detect_emotions(response)
            if emotions:
                memory.current_mood = emotions[0]
                memory.mood_history.append({
                    "timestamp": msg.timestamp,
                    "mood": emotions[0],
                })

    # 从队列中移除
    new_queue = [a for a in state.speaking_queue if a != agent_id]

    return {
        "messages": state.messages + [msg], 
        "pending_messages": state.pending_messages + [msg],
        "active_agent": new_queue[0] if new_queue else None,
        "speaking_queue": new_queue,
        "agent_response": response,
    }


# ============================================================
# Backward-compatible exports
# ============================================================

def supervisor_node(state: SharedState) -> dict:
    return _supervisor_logic(state)


def route_to_agents(state: SharedState) -> str:
    return _route_to_agents(state)


def create_agent_node(agent_id: str):
    def node(state: SharedState) -> dict:
        return _agent_logic(state, agent_id)
    return node


def bully_node(state: SharedState) -> dict:
    return _agent_logic(state, "bully")


def victim_node(state: SharedState) -> dict:
    return _agent_logic(state, "victim")


def bystander_a_node(state: SharedState) -> dict:
    return _agent_logic(state, "bystander_a")


def bystander_b_node(state: SharedState) -> dict:
    return _agent_logic(state, "bystander_b")

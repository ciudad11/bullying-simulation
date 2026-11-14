"""角色规则真实生成入口回归（v2026-11-14：规则只决定内容方向与强度，不决定发言资格）。
无网络、无密钥读取、无用户 DB/日志写入。
运行：python -B <本文件绝对路径> -v
"""
import copy
import io
import json
import re
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import Mock, mock_open, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "web"))

from multi_agent.agents import AGENT_ROLE_MAP, create_agent
from importlib import import_module

evaluator_node = import_module("multi_agent.evaluator_node")
from multi_agent.graph_builder import MultiAgentRunner, StepRunner, set_llm_client
from multi_agent.llm_client import (get_preset_response, PRESET_RESPONSES, TENDENCY_RESPONSES,
                                    pick_variant_index, render_tendency_variant,
                                    tendency_pool_key, tendency_pool_size)
from multi_agent.prompts import (TENDENCY_INSTRUCTIONS, TENDENCY_LABEL_CN,
                                 RICHNESS_REQUIREMENT_TEXT, build_system_prompt)
from multi_agent.state import Message, create_initial_state, message_to_display
from multi_agent.supervisor import _agent_logic, _supervisor_logic
from session_manager import SessionManager

NEW_TENDENCIES = {
    "apologize", "back_off", "escalate_severe", "low_intensity_taunt",
    "endure_quietly", "wait_observe", "observe_quiet", "avoid",
    "resist_follow", "silent_resist",
}


def msg(sender, content="测试发言", round_no=1, emotions=None, tendency=None):
    m = Message(sender, sender, sender, content, round=round_no, emotions=emotions or [])
    if tendency:
        m.decision = {"reaction_tendency": tendency, "produced_message": True}
    return m


def state_for(messages=None, **values):
    state = create_initial_state()
    state.round = 1
    state.messages = list(messages or [])
    for key, value in values.items():
        setattr(state, key, value)
    return state


def net_len(text) -> int:
    """去标点净字数（与 5626edc 质量基线的度量口径一致）。"""
    return len(re.sub(r"[\s\W_]+", "", text or ""))


def speech_only(text) -> str:
    """去掉括注后剩下的「说出口的话」。"""
    return re.sub(r"[\s\W_]+", "", re.sub(r"[（(][^（()）]*[）)]", "", text or ""))


def offline_expect(state, agent_id, tendency, level=None):
    """按决策证据（变体序号 + 槽位上下文）复原离线预设原文，用于逐字校验。"""
    ev = state.decision_log[0]
    if ev["reaction_tendency"] != tendency:
        raise AssertionError(f"{agent_id} 倾向不符：{ev['reaction_tendency']} != {tendency}")
    level = state.level if level is None else level
    return render_tendency_variant(tendency, level, ev["variant_index"],
                                   ev.get("slot_context"), agent_id)


def true_cases():
    """规则原始判断 should_speak=True 的全部倾向分支（含两个角色语义不同的 hesitate）。"""
    attack = [msg("bully")]
    support = [msg("teacher")]
    mention = [msg("other", "思远，请回应")]
    return [
        ("bully", "provoke", [], {}),
        ("bully", "provoke", [msg("other")], {}),
        ("bully", "escalate_severe", [msg("other")], {"bullying_intensity": .8}),
        ("victim", "silence_fear", attack, {"retaliation_risk": .5, "victim_safety": .49}),
        ("victim", "withdraw_cry", attack, {"victim_distress": .71}),
        ("victim", "withdraw", attack, {"victim_distress": .5}),
        ("victim", "seek_help", attack, {"victim_distress": .4, "victim_safety": .5}),
        ("victim", "weak_protest", attack, {"victim_distress": .4, "victim_safety": .49}),
        ("victim", "thank_worried", support, {"victim_safety": .39}),
        ("victim", "thank_relieved", support, {"victim_safety": .4}),
        ("victim", "respond_cautious", mention, {"last_intervention_risk_level": "attention"}),
        ("victim", "respond", mention, {}),
        ("bystander_a", "hesitate", attack, {"retaliation_risk": .5, "bystander_support": .29}),
        ("bystander_a", "report_teacher", attack, {"bullying_intensity": .61}),
        ("bystander_a", "support_victim", attack, {"bullying_intensity": .31}),
        ("bystander_a", "mild_reminder", attack, {"bullying_intensity": .3}),
        ("bystander_b", "hesitate", [msg("bystander_a")], {"bullying_intensity": .41}),
        ("bystander_b", "follow_majority", attack * 2, {}),
        ("bystander_b", "support_justice", [msg("bystander_a")], {"bullying_intensity": .4}),
    ]


def false_cases():
    """规则原始判断 should_speak=False 的分支——接入后仍必须产出台词、只是换方向说。"""
    # 冷却类用例把历史自我发言放在 0 轮：_merge_result 按 sender_id+round 去重，
    # 同轮同名历史消息会吞掉本轮新产出；最近 4 条窗口不过滤轮次，倾向判定不受影响。
    return [
        # 霸凌者：高质量干预收敛 → 倾向道歉；概率未抽中攻击 → 找台阶
        ("bully", "apologize", [msg("teacher")], {}),
        ("bully", "back_off", [msg("teacher")],
         {"config": {"agent_decision": {"base": .4}}, "random": .99,
          "attack_frequency": 0, "persistence": 0, "bully_resistance": 0,
          "power_imbalance": 0, "retaliation_risk": 0, "bystander_support": 0,
          "immediate_stop_residual": 0}),
        ("bully", "low_intensity_taunt", [msg("bully", round_no=0)], {}),
        # 受害者：冷却/无事件 → 低头不语的非言语台词
        ("victim", "endure_quietly", [msg("victim", round_no=0)], {}),
        ("victim", "endure_quietly", [msg("other")], {}),
        # 正义旁观者：冷却 → wait_observe；本轮无攻击 → observe_quiet
        ("bystander_a", "wait_observe", [msg("bystander_a", round_no=0)], {}),
        ("bystander_a", "observe_quiet", [msg("other")], {}),
        # 从众旁观者：老师在场 → avoid（不再必沉默）；resist/silent 分支纳入词表并可发言
        ("bystander_b", "avoid", [msg("teacher")], {}),
        ("bystander_b", "resist_follow", [msg("bully"), msg("bully")], {"bystander_support": .4}),
        ("bystander_b", "silent_resist", [msg("bully"), msg("bully")],
         {"bystander_support": .399, "retaliation_risk": .5}),
        ("bystander_b", "wait_observe", [msg("bystander_b", round_no=0)], {}),
        ("bystander_b", "observe_quiet", [], {}),
    ]


class FakeClient:
    def __init__(self, response="我想把情况说出来，请帮帮我。", error=False):
        self.response = response
        self.error = error
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        if self.error:
            raise RuntimeError("fake client error")
        return self.response


class RoleDecisionTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")).start()
        self.real_chat = patch("multi_agent.llm_client.LLMClient.chat", side_effect=AssertionError("real LLM forbidden")).start()
        self.key = patch("multi_agent.graph_builder.get_api_key", side_effect=AssertionError("key lookup forbidden")).start()
        self.random = patch("random.random", return_value=0.0).start()
        self.output = patch("sys.stdout", new_callable=io.StringIO).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(set_llm_client, None)

    def tearDown(self):
        self.network.assert_not_called()
        self.real_chat.assert_not_called()
        self.key.assert_not_called()

    def apply(self, state, agent_id, client=None, use_llm=True):
        state.speaking_queue = [agent_id]
        runner = StepRunner(state, client, use_llm=use_llm)
        runner._process_speaking_queue()
        return state

    def case_state(self, messages, values):
        values = dict(values)
        roll = values.pop("random", None)
        if roll is not None:
            self.random.return_value = roll
        return state_for(messages, **values)

    # ---------- 契约1：解除拦截——False 候选仍产出台词且倾向正确 ----------
    def test_rule_false_candidate_still_produces_with_tendency(self):
        state = state_for([msg("teacher")])
        state.speaking_queue = ["bystander_b", "victim"]
        state.agent_response = "上一位的回复"
        client = FakeClient("（干笑着往后挪半步）老师在这儿呢，我什么都没看见啊。")
        result = _agent_logic(state, "bystander_b", client)
        self.assertEqual(result["speaking_queue"], ["victim"])
        self.assertEqual(result["active_agent"], "victim")
        self.assertEqual(result["agent_response"], client.response)
        self.assertIn("messages", result)
        self.assertIn("pending_messages", result)
        self.assertEqual(len(client.calls), 1)
        # 决策记录保留规则原始判断，但 produced_message 如实为 True
        ev = state.decision_log[0]
        self.assertFalse(ev["rule_decision"]["should_speak"])
        self.assertTrue(ev["produced_message"])
        self.assertEqual(ev["reaction_tendency"], "avoid")
        self.assertEqual(ev["generation_source"], "llm")
        # _agent_logic 返回增量消息，由 StepRunner 合并；直接调用不改写 state.messages。
        self.assertEqual(result["messages"][-1].content, client.response)
        self.assertEqual(result["pending_messages"][-1].content, client.response)
        self.assertEqual(state.agent_memories["bystander_b"].speaking_count, 1)
        # 重入保护：同一轮同一候选不重复决策，也不重复产出
        again = _agent_logic(state, "bystander_b", client)
        self.assertNotIn("messages", again)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(state.decision_log), 1)

    def test_all_false_rule_branches_generate_offline(self):
        for agent_id, tendency, messages, values in false_cases():
            with self.subTest(agent=agent_id, tendency=tendency):
                state = self.case_state(messages, values)
                self.apply(state, agent_id)
                ev = state.decision_log[0]
                self.assertFalse(ev["rule_decision"]["should_speak"])
                self.assertTrue(ev["produced_message"])
                self.assertEqual(ev["reaction_tendency"], tendency)
                self.assertEqual(ev["generation_source"], "preset_offline")
                self.assertEqual(len(state.messages), len(messages) + 1)
                self.assertEqual(state.messages[-1].content, offline_expect(state, agent_id, tendency))
                # 方向仍由规则给出，但输出是成段、可听见的发言（不是纯动作/单句敷衍）
                self.assertTrue(speech_only(state.messages[-1].content))
                self.assertGreaterEqual(net_len(state.messages[-1].content), 20)

    # ---------- 契约2/4：True 分支 + 离线预设按倾向×level 分档 ----------
    def test_all_true_branches_offline_and_prompt_constraints(self):
        for agent_id, tendency, messages, values in true_cases():
            with self.subTest(agent=agent_id, tendency=tendency):
                state = self.case_state(messages, values)
                self.apply(state, agent_id)
                evidence = state.decision_log[0]
                self.assertTrue(evidence["rule_decision"]["should_speak"])
                self.assertEqual(evidence["reaction_tendency"], tendency)
                self.assertEqual(evidence["generation_source"], "preset_offline")
                self.assertTrue(evidence["produced_message"])
                self.assertEqual(state.messages[-1].content, offline_expect(state, agent_id, tendency))
                self.assertEqual(state.agent_memories[agent_id].speaking_count, 1)
                self.assertEqual(len(state.pending_messages), 1)
                client = FakeClient(get_preset_response(agent_id, 2, tendency))
                live = self.case_state(messages, values)
                self.apply(live, agent_id, client)
                self.assertEqual(len(client.calls), 1)
                system = client.calls[0][0]["content"]
                user = client.calls[0][1]["content"]
                # 契约1：规则块只注入一次，且改成「方向 + 力度」的内容约束表述
                self.assertEqual(system.count("本轮反应方向与力度（内容约束）"), 1)
                if tendency == "hesitate":
                    self.assertIn("犹豫", system)  # hesitate 按角色给出专用中文标签
                else:
                    self.assertIn(TENDENCY_LABEL_CN[tendency], system)
                self.assertIn("不改变你的表达方式", system)
                # 契约3：给模型的文案一律中文，不再泄漏英文倾向键
                self.assertNotIn(f"reaction_tendency={tendency}", system)
                self.assertNotIn(tendency, system)
                self.assertNotIn(tendency, user)
                # 契约1：收回创作权的旧措辞必须消失
                for banned in ("冲突时本轮规则优先", "优先于其他行为建议", "你只负责表达",
                               "只修饰措辞与表达", "不得改变规则决定的反应方向"):
                    self.assertNotIn(banned, system)
                # 契约2：正向篇幅与结构要求仍在（且只出现一次这一档口）
                self.assertIn("2–4 句连成一段", system)
                self.assertIn("不限制篇幅", system)
                self.assertNotIn("本轮不发言", system)
                self.assertNotIn("不生成任何学生台词", system)
                # 契约4：user prompt 复原 5626edc 的「只输出一段发言」语义
                self.assertIn("只输出你的一段发言", user)
                self.assertIn("请直接说出你作为", user)
                self.assertNotIn("你不需要回应", user)
                self.assertEqual(live.decision_log[0]["generation_source"], "llm")
        # 开场霸凌者的倾向由规则直接给出，原始规则字段结构不变。
        opening = state_for()
        self.apply(opening, "bully")
        self.assertEqual(opening.decision_log[0]["rule_decision"]["reaction_tendency"], "provoke")
        self.assertEqual(opening.decision_log[0]["rule_decision"]["response"], "")

    def test_invalid_error_and_filtered_outputs_keep_tendency(self):
        bad_outputs = ["", "  ", None, "……", "...", "沉默", "（沉默）", "不发言。", "Silence",
                       "[LLM暂时不可用，请检查API配置]"]
        for agent_id, tendency, messages, values in true_cases() + false_cases():
            for bad in bad_outputs:
                with self.subTest(agent=agent_id, tendency=tendency, output=bad):
                    state = self.case_state(messages, values)
                    self.apply(state, agent_id, FakeClient(bad))
                    self.assertEqual(state.messages[-1].content, offline_expect(state, agent_id, tendency))
                    self.assertEqual(state.decision_log[0]["generation_source"], "preset_invalid_response")
            for filtered in ["", "沉默", "[LLM暂时不可用，请检查API配置]"]:
                state = self.case_state(messages, values)
                with patch("multi_agent.supervisor.filter_thinking_content", return_value=filtered):
                    self.apply(state, agent_id, FakeClient())
                self.assertEqual(state.decision_log[0]["generation_source"], "preset_filtered_response")
                self.assertEqual(state.messages[-1].content, offline_expect(state, agent_id, tendency))
            state = self.case_state(messages, values)
            self.apply(state, agent_id, FakeClient(error=True))
            self.assertEqual(state.decision_log[0]["generation_source"], "preset_error")
            self.assertEqual(state.messages[-1].content, offline_expect(state, agent_id, tendency))
            # 契约7：异常降级留下摘要（只落类型名，不落消息文本）
            self.assertEqual(state.decision_log[0]["error_type"], "RuntimeError")
            self.assertGreater(state.decision_log[0]["kept_len"], 0)

    def test_real_thinking_filter_empty_uses_selected_fallback(self):
        for response in ["<think>这是模型的内部分析</think>", "我需要扮演受害者"]:
            state = state_for([msg("bully")], victim_distress=.3, victim_safety=.5)
            self.apply(state, "victim", FakeClient(response))
            self.assertEqual(state.decision_log[0]["generation_source"], "preset_filtered_response")
            self.assertEqual(state.messages[-1].content, offline_expect(state, "victim", "seek_help"))
        state = state_for([msg("bully")], victim_distress=.3, victim_safety=.5)
        self.apply(state, "victim", FakeClient("<think>内部分析</think>请帮帮我，我想把情况说出来。"))
        self.assertEqual(state.decision_log[0]["generation_source"], "llm")
        self.assertNotIn("think", state.messages[-1].content)

    def test_silence_fear_is_observable_message_not_false(self):
        state = state_for([msg("bully")], retaliation_risk=.5, victim_safety=.49)
        self.apply(state, "victim", FakeClient("沉默"))
        self.assertIn("避开视线", state.messages[-1].content)
        self.assertEqual(state.messages[-1].decision["reaction_tendency"], "silence_fear")
        self.assertFalse(state.get_decision_status()["empty_round"])

    # ---------- 契约：发言人数=候选人数，不再出现 0 人轮 ----------
    def test_queue_candidates_all_produce_one_message_each(self):
        state = state_for()
        state.speaking_queue = ["bully", "victim", "bystander_a", "bystander_b"]
        runner = StepRunner(state, None, use_llm=False)
        runner._process_speaking_queue()
        self.assertEqual([d["agent_id"] for d in state.decision_log],
                         ["bully", "victim", "bystander_a", "bystander_b"])
        self.assertEqual(state.decision_log[1]["input_window"],
                         [{"message_index": 0, "sender_id": "bully", "round": 1}])
        self.assertTrue(all(d["produced_message"] for d in state.decision_log))
        self.assertEqual(len(state.messages), 4)
        self.assertEqual(state.pending_messages, state.messages)
        self.assertEqual(len({id(m) for m in state.pending_messages}), len(state.messages))
        self.assertFalse(state.get_decision_status()["empty_round"])
        count = len(state.messages)
        with patch.object(evaluator_node, "calculate_hostile_score", wraps=evaluator_node.calculate_hostile_score) as score:
            evaluator_node._evaluator_logic(state)
            self.assertEqual(score.call_count, count)
        self.assertEqual(state.pending_messages, [])
        self.assertIsNone(state.active_agent)

    def test_messages_dedup_contract_unchanged(self):
        state = state_for([msg("bully")])
        old = state.messages[0]
        other = msg("victim")
        StepRunner(state)._merge_result({"messages": [old, other], "pending_messages": [old, other]})
        self.assertEqual(state.messages, [old, other])
        self.assertEqual(state.pending_messages, [old, other])

    def test_runner_client_isolation_explicit_none_and_use_llm(self):
        a, b = state_for(), state_for()
        ca, cb = FakeClient("就你这样还得意？"), FakeClient("还以为你多厉害呢？")
        ra, rb = StepRunner(a, ca), StepRunner(b, cb)
        for state, runner in [(a, ra), (b, rb)]:
            state.speaking_queue = ["bully"]
            runner._process_speaking_queue()
        self.assertEqual(len(ca.calls), 1)
        self.assertEqual(len(cb.calls), 1)
        offline = state_for()
        ro = StepRunner(offline, None)
        set_llm_client(cb)
        offline.speaking_queue = ["bully"]
        ro._process_speaking_queue()
        self.assertEqual(len(cb.calls), 1)
        disabled = state_for()
        self.apply(disabled, "bully", ca, use_llm=False)
        self.assertEqual(len(ca.calls), 1)
        self.assertEqual(disabled.decision_log[0]["generation_source"], "preset_offline")
        # 兼容无参节点仍可使用全局，显式 None 不可回退。
        set_llm_client(cb)
        _agent_logic(state_for(), "bully")
        _agent_logic(state_for(), "bully", llm_client=None)
        self.assertEqual(len(cb.calls), 2)

    def test_single_rule_random_draw_and_reentry_guard(self):
        state = state_for([msg("other")], attack_frequency=0, persistence=0)
        state.speaking_queue = ["bully"]
        self.random.return_value = .09
        self.random.reset_mock()
        result = _agent_logic(state, "bully", None)
        _agent_logic(state, "bully", None)
        self.random.assert_called_once_with()
        self.assertIn("messages", result)
        self.assertEqual(state.decision_log[0]["rule_decision"]["confidence"], .1)
        self.assertEqual(state.decision_log[0]["reaction_tendency"], "provoke")

    def test_supervisor_sets_active_agent_in_place(self):
        state = state_for(round=0)
        state.active_agent = "stale"
        state.agent_response = "stale"
        result = _supervisor_logic(state)
        self.assertEqual(state.active_agent, result["active_agent"])
        self.assertEqual(state.active_agent, state.speaking_queue[0])
        self.assertGreaterEqual(len(state.speaking_queue), 3)
        self.assertIsNone(state.agent_response)
        self.assertEqual(state.decision_log, [])

    # ---------- 契约3：窗口口径回到 5626edc 的「最近 N 条」 ----------
    def test_window_is_last_four_messages_without_round_filter(self):
        state = state_for([msg("teacher", round_no=1)] +
                          [msg("other", str(i), round_no=2) for i in range(6)], round=2)
        self.assertEqual([m.content for m in state.get_recent_messages(4)], ["2", "3", "4", "5"])
        # 上一轮的霸凌攻击消息按「最近4条」参与判定：冷却 → 轻佻倾向，而非跨轮误判禁言
        state.messages = [msg("bully", round_no=1)]
        state.round = 2
        decision = create_agent("bully").decide(state)
        self.assertFalse(decision.should_speak)
        self.assertEqual(decision.reaction_tendency, "low_intensity_taunt")
        self.assertIn("刚攻击过", decision.reasoning)
        # 窗口被后续消息挤出后回到概率轮判定
        state.messages = [msg("bully", round_no=1)] + [msg("other", str(i)) for i in range(4)]
        state.round = 3
        self.random.return_value = .99
        decision = create_agent("bully").decide(state)
        self.assertFalse(decision.should_speak)
        self.assertIn("p_attack", decision.reasoning)
        self.assertIn(decision.reaction_tendency, ("apologize", "back_off"))
        # 首轮开场仍由 decide 检查完整历史
        for messages in [[], [msg("system", round_no=0)]]:
            opening = state_for(messages, round=5)
            decision = create_agent("bully").decide(opening)
            self.assertTrue(decision.should_speak)
            self.assertEqual(decision.reaction_tendency, "provoke")
        # 从众旁观者：窗口内老师消息优先于旁观者事件（轮次无关）
        state.messages = [msg("teacher", round_no=1), msg("bystander_a", round_no=3)]
        state.round = 3
        decision = create_agent("bystander_b").decide(state)
        self.assertFalse(decision.should_speak)
        self.assertEqual(decision.reaction_tendency, "avoid")
        # 受害者：教师支持事件按窗口识别（跨轮同样命中）
        decision = create_agent("victim").decide(state)
        self.assertTrue(decision.should_speak)
        self.assertIn(decision.reaction_tendency, ("thank_worried", "thank_relieved"))

    def test_attack_detection_follows_tendency_not_sender_only(self):
        # 道歉/退让台词不算攻击事件；旧消息（无 decision 证据）沿用旧口径视为攻击。
        apology = state_for([msg("bully", "行了行了，刚才就是开个玩笑", tendency="apologize")])
        self.assertIn(create_agent("victim").decide(apology).reaction_tendency,
                      ("endure_quietly", "thank_worried", "thank_relieved", "respond", "respond_cautious"))
        self.assertFalse(create_agent("bystander_a").decide(apology).should_speak)
        provoke = state_for([msg("bully", "就你这样", tendency="provoke")])
        self.assertTrue(create_agent("victim").decide(provoke).should_speak)
        legacy = state_for([msg("bully", "无证据旧消息")])
        self.assertTrue(create_agent("victim").decide(legacy).should_speak)

    def test_custom_pid_name_mapping_and_no_global_mutation(self):
        before = copy.deepcopy(AGENT_ROLE_MAP)
        runner = MultiAgentRunner(use_llm=False, custom_participants={
            "pid-victim": {"name": "小青", "role": "victim", "personality": {"traits": "谨慎"}}
        })
        runner.state.round = 1
        runner.state.messages = [msg("other", "小青，请回应")]
        runner.state.speaking_queue = ["victim"]
        runner._step_runner._process_speaking_queue()
        self.assertEqual(runner.state.decision_log[0]["reaction_tendency"], "respond")
        self.assertEqual(runner.state.messages[-1].sender_name, "小青")
        self.assertEqual(AGENT_ROLE_MAP, before)

    # ---------- 契约2：四角色低强度/高强度倾向 ----------
    def test_victim_thresholds_and_event_priority_over_cooldown(self):
        cases = [(.4, 0, 0, "weak_protest"), (.4001, 0, 0, "withdraw"),
                 (.7, 0, 0, "withdraw"), (.7001, 0, 0, "withdraw_cry"),
                 (.4, .5, 0, "seek_help"), (.8, .49, .5, "silence_fear"),
                 (.8, .5, .5, "withdraw_cry")]
        for distress, safety, risk, expected in cases:
            with self.subTest(expected=expected, distress=distress, safety=safety):
                state = state_for([msg("bully")], victim_distress=distress,
                                  victim_safety=safety, retaliation_risk=risk)
                self.assertEqual(create_agent("victim").decide(state).reaction_tendency, expected)
        for safety, risk, expected in [(.399, 0, "thank_worried"), (.4, 0, "thank_relieved"), (.8, .5, "thank_worried")]:
            state = state_for([msg("teacher")], victim_safety=safety, retaliation_risk=risk)
            self.assertEqual(create_agent("victim").decide(state).reaction_tendency, expected)
        # 冷却不再压过攻击事件：刚发言过+被攻击 → 仍按应激档反应
        state.messages.append(msg("victim"))
        state.messages.append(msg("bully"))
        decision = create_agent("victim").decide(state)
        self.assertTrue(decision.should_speak)
        self.assertIn(decision.reaction_tendency,
                      ("silence_fear", "withdraw_cry", "withdraw", "seek_help", "weak_protest"))
        # 冷却且无事件 → endure_quietly（有台词的低头不语）
        state.messages = [msg("victim"), msg("other")]
        decision = create_agent("victim").decide(state)
        self.assertFalse(decision.should_speak)
        self.assertEqual(decision.reaction_tendency, "endure_quietly")

    def test_bystander_a_thresholds_and_branch_order(self):
        for intensity, expected in [(.3, "mild_reminder"), (.3001, "support_victim"),
                                    (.6, "support_victim"), (.6001, "report_teacher")]:
            state = state_for([msg("bully")], bullying_intensity=intensity)
            self.assertEqual(create_agent("bystander_a").decide(state).reaction_tendency, expected)
        state = state_for([msg("bully")], bullying_intensity=.9, retaliation_risk=.5, bystander_support=.299)
        self.assertEqual(create_agent("bystander_a").decide(state).reaction_tendency, "hesitate")
        state.bystander_support = .3
        self.assertEqual(create_agent("bystander_a").decide(state).reaction_tendency, "report_teacher")
        state.bullying_intensity = .1
        state.messages.append(msg("victim", emotions=["sad"]))
        self.assertEqual(create_agent("bystander_a").decide(state).reaction_tendency, "support_victim")
        # 攻击事件优先于冷却：刚干预过但攻击仍在 → 继续干预
        state.messages.append(msg("bystander_a"))
        decision = create_agent("bystander_a").decide(state)
        self.assertTrue(decision.should_speak)
        self.assertEqual(decision.reaction_tendency, "support_victim")
        # 纯冷却（无攻击） → wait_observe
        state2 = state_for([msg("bystander_a")])
        decision2 = create_agent("bystander_a").decide(state2)
        self.assertFalse(decision2.should_speak)
        self.assertEqual(decision2.reaction_tendency, "wait_observe")

    def test_bystander_b_tendency_map_including_ex_silent_branches(self):
        # 原 False 拦截分支现在都有可生成倾向
        for support, risk, speak, expected in [(.4, 0, False, "resist_follow"),
                                               (.399, .5, False, "silent_resist"),
                                               (.399, .499, True, "follow_majority")]:
            state = state_for([msg("bully"), msg("bully")], bystander_support=support, retaliation_risk=risk)
            decision = create_agent("bystander_b").decide(state)
            self.assertEqual((decision.should_speak, decision.reaction_tendency), (speak, expected))
        for intensity, expected in [(.4, "support_justice"), (.4001, "hesitate")]:
            state = state_for([msg("bystander_a")], bullying_intensity=intensity)
            self.assertEqual(create_agent("bystander_b").decide(state).reaction_tendency, expected)
        # 老师在场：不再必沉默缺席，改为 avoid
        state = state_for([msg("bystander_b"), msg("teacher")])
        decision = create_agent("bystander_b").decide(state)
        self.assertEqual(decision.reaction_tendency, "avoid")
        # 站队事件优先于冷却
        state = state_for([msg("bystander_b"), msg("bystander_a")], bullying_intensity=.4)
        self.assertEqual(create_agent("bystander_b").decide(state).reaction_tendency, "support_justice")
        # 纯冷却 → wait_observe；无任何信号 → observe_quiet
        state = state_for([msg("bystander_b")])
        self.assertEqual(create_agent("bystander_b").decide(state).reaction_tendency, "wait_observe")
        state = state_for([msg("other")])
        self.assertEqual(create_agent("bystander_b").decide(state).reaction_tendency, "observe_quiet")

    def test_bully_low_intensity_apologize_high_intensity_severe(self):
        # 低强度（基线 p=.10，未抽中攻击）：道歉/找台阶，而不是被禁言
        state = state_for([msg("other")])
        self.random.return_value = .5
        decision = create_agent("bully").decide(state)
        self.assertFalse(decision.should_speak)
        self.assertEqual(decision.reaction_tendency, "apologize")
        # 高强度 + 抽中攻击 → escalate_severe（措辞更严重）
        self.random.return_value = .0
        state.bullying_intensity = .75
        decision = create_agent("bully").decide(state)
        self.assertTrue(decision.should_speak)
        self.assertEqual(decision.reaction_tendency, "escalate_severe")
        # 高强度但 p 低未抽中 → 倾向道歉而非继续攻击
        self.random.return_value = .5
        decision = create_agent("bully").decide(state)
        self.assertFalse(decision.should_speak)
        self.assertEqual(decision.reaction_tendency, "apologize")
        # 冷却 → low_intensity_taunt（旧消息无证据按攻击对待）
        self.random.return_value = .0
        state = state_for([msg("bully")])
        decision = create_agent("bully").decide(state)
        self.assertFalse(decision.should_speak)
        self.assertEqual(decision.reaction_tendency, "low_intensity_taunt")

    def test_bully_probability_threshold_force_and_config_preserved(self):
        state = state_for([msg("teacher")], attack_frequency=0, persistence=0)
        self.random.reset_mock()
        decision = create_agent("bully").decide(state)
        self.assertFalse(decision.should_speak)
        self.assertEqual(decision.reaction_tendency, "apologize")
        self.random.assert_not_called()
        state.last_intervention_risk_level = "serious"
        decision = create_agent("bully").decide(state)
        self.assertTrue(decision.should_speak)
        self.assertEqual(decision.reaction_tendency, "escalate_severe")
        self.random.assert_not_called()
        state.last_intervention_risk_level = "none"
        state.config = {"agent_decision": {"base": .35}}
        self.random.return_value = .35
        decision = create_agent("bully").decide(state)
        self.assertFalse(decision.should_speak)
        self.assertEqual(decision.reaction_tendency, "back_off")
        self.random.return_value = .3499
        decision = create_agent("bully").decide(state)
        self.assertTrue(decision.should_speak)
        self.assertEqual(decision.reaction_tendency, "provoke")
        state.config = {"agent_decision": {"base": .3499}}
        self.random.reset_mock()
        decision = create_agent("bully").decide(state)
        self.assertFalse(decision.should_speak)
        self.assertEqual(decision.reaction_tendency, "apologize")
        self.random.assert_not_called()

    # ---------- 契约4/5：预设分档 + 词表同步 ----------
    def test_tendency_vocabulary_parity(self):
        self.assertTrue(NEW_TENDENCIES <= set(TENDENCY_RESPONSES))
        self.assertTrue(NEW_TENDENCIES <= set(TENDENCY_INSTRUCTIONS))
        self.assertEqual(set(TENDENCY_RESPONSES), set(TENDENCY_INSTRUCTIONS))

    def test_preset_bully_tendency_gradation_across_levels(self):
        for tendency in ("escalate_severe", "provoke", "low_intensity_taunt", "apologize", "back_off"):
            texts = [get_preset_response("bully", lv, tendency) for lv in range(1, 6)]
            self.assertEqual(len(texts), len(set(texts)), tendency)
            self.assertNotEqual(texts[0], texts[4], tendency)

    def test_preset_all_tendencies_level1_differs_from_level5(self):
        for tendency in sorted(TENDENCY_RESPONSES):
            if tendency == "hesitate":
                continue  # 按契约保留 agent_id 特判，不做 level 分档
            for agent_id in ("bully", "victim", "bystander_a", "bystander_b"):
                low = get_preset_response(agent_id, 1, tendency)
                high = get_preset_response(agent_id, 5, tendency)
                self.assertNotEqual(low, high, f"{agent_id}/{tendency}")

    def test_hesitate_agent_id_special_case_preserved(self):
        a = get_preset_response("bystander_a", 2, "hesitate")
        b = get_preset_response("bystander_b", 4, "hesitate")
        self.assertIn("犹豫", a)
        self.assertIn("犹豫", b)
        self.assertNotEqual(a, b)

    def test_legacy_preset_path_still_level_based(self):
        for lv in range(1, 6):
            self.assertIn(get_preset_response("bully", lv), PRESET_RESPONSES["bully"][lv])

    # ---------- 契约1/3/6：提示词块只讲方向与强度、只注入一次、倾向中文化 ----------
    def test_prompt_block_direction_only_no_silence_wording(self):
        decision = {"should_speak": False, "response": "", "reasoning": "r", "confidence": .3,
                    "reaction_tendency": "back_off"}
        prompt = build_system_prompt("bully", 2, decision=decision, reaction_tendency="back_off")
        # 规则块只出现一次（旧写法首尾各一份，占基线提示词 18.6%）
        self.assertEqual(prompt.count("## 本轮反应方向与力度（内容约束）"), 1)
        self.assertEqual(prompt.count("## 表达丰富度要求"), 1)
        # 倾向对模型只显示中文标签，不再泄漏英文键
        self.assertIn(TENDENCY_LABEL_CN["back_off"], prompt)
        self.assertNotIn("back_off", prompt)
        self.assertNotIn("reaction_tendency", prompt)
        self.assertNotIn("should_speak", prompt)
        # 收回创作权的旧措辞不得回归
        for banned in ("冲突时本轮规则优先", "优先于其他行为建议", "你只负责表达",
                       "只修饰措辞与表达", "不得改变规则决定的反应方向", "本轮规则决策"):
            self.assertNotIn(banned, prompt)
        self.assertNotIn("本轮不发言", prompt)
        self.assertNotIn("不生成任何学生台词或动作", prompt)
        # 方向 + 力度二元表述（轻的是攻击性，不是篇幅）
        self.assertIn("方向：", prompt)
        self.assertIn("力度：", prompt)
        self.assertIn("不改变你的表达方式", prompt)
        self.assertIn("不限制篇幅", prompt)
        self.assertIn("说出口的话", prompt)
        self.assertIn("不得只给动作而没有台词", prompt)
        # 无决策时（纯提示词自由发挥基线）仍带丰富度要求
        plain = build_system_prompt("victim", 2)
        self.assertIn("## 表达丰富度要求", plain)
        self.assertEqual(plain.count("## 本轮反应方向与力度（内容约束）"), 0)

    def test_chinese_tendency_labels_cover_all_26_keys(self):
        self.assertEqual(len(TENDENCY_INSTRUCTIONS), 26)
        self.assertEqual(set(TENDENCY_LABEL_CN), set(TENDENCY_INSTRUCTIONS))
        for key, label in TENDENCY_LABEL_CN.items():
            self.assertTrue(re.search(r"[\u4e00-\u9fff]{2,}", label), key)
            self.assertNotIn(key, label)
            self.assertTrue(re.search(r"方向：.*力度：", TENDENCY_INSTRUCTIONS[key]), key)
        # 方向-only 倾向不得再出现「短/一句/沉默」式篇幅暗示，必须自带篇幅护栏
        length_guard = re.compile(r"篇幅|成段|完整一段|说满|一整套|不是字数|不等于最简短|不是话")
        for key in ("observe_quiet", "wait_observe", "endure_quietly",
                    "low_intensity_taunt", "silent_resist", "avoid"):
            text = TENDENCY_INSTRUCTIONS[key]
            self.assertNotIn("只说一个字", text, key)
            self.assertNotIn("沉默", text.replace("不接话", ""), key)
            self.assertTrue(length_guard.search(text), key)

    # ---------- 契约5：离线变体池（数量、轮转不逐字复读、长度与台词） ----------
    def test_offline_variant_pool_has_four_to_six_lines_per_bucket(self):
        for tendency in sorted(TENDENCY_RESPONSES):
            pools = ({"bystander_a": TENDENCY_RESPONSES[tendency]["bystander_a"],
                      "bystander_b": TENDENCY_RESPONSES[tendency]["bystander_b"]}
                     if tendency == "hesitate" else {"-": TENDENCY_RESPONSES[tendency]})
            for who, entry in pools.items():
                self.assertGreaterEqual(len(entry.get("openers") or []), 4, f"{tendency}/{who}")
                self.assertGreaterEqual(len(entry.get("tails") or []), 4, f"{tendency}/{who}")
                buckets = [k for k in entry if k not in ("openers", "tails")]
                self.assertTrue(buckets, f"{tendency}/{who}")
                for bucket in buckets:
                    self.assertGreaterEqual(len(entry[bucket]), 4, f"{tendency}/{who}/{bucket}")
                    self.assertLessEqual(len(entry[bucket]), 6, f"{tendency}/{who}/{bucket}")

    def test_offline_pool_rotation_no_verbatim_repeat_within_10_rounds(self):
        """同会话同「倾向×档」10 轮内不得逐字复读（契约5 的核心回归）。"""
        state = state_for()
        seen = []
        for _ in range(10):
            idx = pick_variant_index("provoke", 3, [i for i, _ in seen], "bully")
            text = render_tendency_variant("provoke", 3, idx, None, "bully")
            self.assertTrue(speech_only(text), text)
            seen.append((idx, text))
        self.assertEqual(len({t for _, t in seen}), 10)
        self.assertEqual(len({i for i, _ in seen}), 10)
        # 池用尽后仍不与上一句相同
        for _ in range(3):
            idx = pick_variant_index("provoke", 3, [i for i, _ in seen], "bully")
            self.assertNotEqual(idx, seen[-1][0])
            seen.append((idx, render_tendency_variant("provoke", 3, idx, None, "bully")))

    def test_offline_simulated_rounds_length_and_no_repeat(self):
        """端到端离线：每轮 3 人、台词均值字数达标、同倾向池不逐字复读。"""
        runner = MultiAgentRunner(use_llm=False)
        state = runner.state
        texts = []
        per_pool = {}
        for _ in range(12):
            result = runner.run_round()
            decisions = [d for d in state.decision_log if d["round"] == state.round]
            self.assertEqual(len(decisions), 3, result["round"])
            self.assertTrue(all(d["produced_message"] for d in decisions))
            self.assertEqual(len(result["messages"]), len(decisions))
            for d in decisions:
                msg = [m for m in state.messages if m.decision.get("variant_index") == d.get("variant_index")
                       and m.sender_id == d["agent_id"]][-1]
                self.assertTrue(speech_only(msg.content), msg.content)
                self.assertLessEqual(msg.content.count("\n"), 0, msg.content)
                texts.append(msg.content)
                per_pool.setdefault(d["pool_key"], []).append(d["variant_index"])
            if result["done"]:
                break
        self.assertGreaterEqual(len(texts), 30)
        mean = sum(net_len(t) for t in texts) / len(texts)
        self.assertGreaterEqual(mean, 35, mean)
        for key, used in per_pool.items():
            self.assertEqual(len(used), len(set(used)), key)
        self.assertEqual(len(texts), len(set(texts)))

    def test_bully_severe_level5_stronger_than_level1(self):
        """霸凌者 escalate_severe 的严重度台阶不得被变体池抹平。"""
        entry = TENDENCY_RESPONSES["escalate_severe"]
        l1 = [render_tendency_variant("escalate_severe", 1, i, None, "bully")
              for i in range(tendency_pool_size("escalate_severe", 1, "bully"))]
        l5 = [render_tendency_variant("escalate_severe", 5, i, None, "bully")
              for i in range(tendency_pool_size("escalate_severe", 5, "bully"))]
        self.assertFalse(set(l1) & set(l5))
        harsh = ("不该待在这个班", "看扁", "丢人", "还有脸", "笑话", "别抬头", "最重的一句", "硬撑")
        mild = ("试试", "记着", "有种", "态度可不对")
        # 台词主体（不含共享 opener/tail）逐档分级：L1 只有威胁口头的力度，L5 句句带人格贬损
        self.assertTrue(all(any(w in t for w in harsh) for t in entry[5]), entry[5])
        self.assertTrue(all(not any(w in t for w in harsh) for t in entry[1]), entry[1])
        self.assertTrue(any(any(w in t for w in mild) for t in entry[1]), entry[1])
        self.assertTrue(all(any(w in t for w in harsh) for t in l5))
        # 其余霸凌倾向同样逐级不同
        for tendency in ("provoke", "low_intensity_taunt", "apologize", "back_off"):
            rendered = [render_tendency_variant(tendency, lv, 0, None, "bully") for lv in range(1, 6)]
            self.assertEqual(len(set(rendered)), 5, tendency)


    # ---------- 契约7：证据字段 ----------
    def test_log_serialization_message_backcompat_and_only_candidates(self):
        state = state_for()
        self.apply(state, "bully")
        summary = state.to_summary_dict()
        self.assertEqual([d["agent_id"] for d in summary["decisions"]], ["bully"])
        self.assertFalse(summary["empty_round"])
        evidence = state.messages[-1].decision
        self.assertTrue(evidence["produced_message"])
        self.assertEqual(evidence["generation_source"], "preset_offline")
        self.assertNotEqual(evidence["generation_source"], "rule_silent")
        self.assertEqual(Message.from_dict(state.messages[-1].to_dict()).decision, evidence)
        self.assertEqual(message_to_display(state.messages[-1])["decision"], evidence)
        old = {"sender_id": "victim", "sender_name": "小青", "role": "victim", "content": "我在听"}
        self.assertEqual(Message.from_dict(old).decision, {})
        serialized = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("prompt", serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("fake client error", serialized)
        state.round += 1
        self.assertEqual(state.get_decision_status(), {"decisions": [], "empty_round": False})

    def test_labels_do_not_drive_evaluator(self):
        a = state_for([msg("victim", "我在听")])
        b = copy.deepcopy(a)
        b.messages[0].decision = {"reaction_tendency": "withdraw_cry", "produced_message": True}
        for state in [a, b]:
            state.pending_messages = list(state.messages)
            evaluator_node._evaluator_logic(state)
        self.assertEqual(a.get_internal_states(), b.get_internal_states())
        self.assertEqual(a.victim_distress, b.victim_distress)
        self.assertEqual(a.obvq_state.to_dict(), b.obvq_state.to_dict())

    # ---------- 端到端（离线）：人数基线 ----------
    def test_real_multi_runner_every_round_at_least_three_no_empty(self):
        runner = MultiAgentRunner(use_llm=False)
        state = runner.state
        state.round = 1
        state.messages = [msg(role) for role in ["bully", "victim", "bystander_a", "bystander_b", "teacher"]]
        state.attack_frequency = state.attack_severity = state.persistence = .05
        state.bullying_intensity = .05
        state.immediate_stop_residual = 1
        runner._prev_msg_count = len(state.messages)
        self.random.return_value = .99  # 概率轮全部不抽中攻击，也不得出现空轮
        for _ in range(7):
            before = len(state.messages)
            result = runner.run_round()
            decisions = [d for d in state.decision_log if d["round"] == state.round]
            self.assertGreaterEqual(len(decisions), 3)
            self.assertTrue(all(d["produced_message"] for d in decisions))
            self.assertFalse(result["empty_round"])
            self.assertEqual(len(state.messages) - before, len(decisions))
            self.assertEqual(result["decisions"], runner.get_status()["decisions"])
            self.assertIsNone(state.active_agent)
            self.assertEqual(state.agent_response, state.messages[-1].content)
            self.assertEqual(state.pending_messages, [])
            self.assertTrue(state.internal_state_history)
            if result["done"]:
                break
        self.assertTrue(state.finished)
        self.assertEqual(state.result_type, "success")
        self.assertEqual(runner.run_round()["messages"], [])

    def test_real_multi_runner_opening_and_intervention_after_rounds(self):
        runner = MultiAgentRunner(use_llm=False)
        first = runner.run_round()
        self.assertGreaterEqual(len(first["messages"]), 3)
        self.assertFalse(first["empty_round"])
        self.assertEqual(len(first["decisions"]), len({d["agent_id"] for d in first["decisions"]}))
        self.assertTrue(all(d["produced_message"] for d in first["decisions"]))
        # 强制随机不攻击，连续多轮仍人人有台词、零空轮；教师干预后受害者按事件感谢
        self.random.return_value = .99
        for _ in range(4):
            round_result = runner.run_round()
            self.assertFalse(round_result["empty_round"])
            self.assertGreaterEqual(len(round_result["decisions"]), 3)
            if round_result["done"]:
                break
        with patch.object(evaluator_node, "evaluate_intervention", return_value={"accepted": True}) as evaluate:
            self.assertEqual(runner.intervene("我会保护你，请告诉我发生了什么。"), {"accepted": True})
            evaluate.assert_called_once()
        self.assertEqual(runner.state.messages[-1].sender_id, "teacher")
        next_round = runner.run_round()
        victim = next(d for d in next_round["decisions"] if d["agent_id"] == "victim")
        self.assertTrue(victim["produced_message"])
        self.assertIn(victim["reaction_tendency"], {"thank_worried", "thank_relieved"})

    def test_session_adapter_status_and_json_persistence_without_db(self):
        manager = SessionManager.__new__(SessionManager)
        manager.runner = MultiAgentRunner(use_llm=False)
        manager.scenario = None
        manager.messages = []
        manager.session_data = {"messages": [], "metrics_history": []}
        manager._save_log = Mock()
        manager._persist_report_snapshot = Mock()
        result = manager._run_round_impl()
        self.assertEqual(result["status"]["decisions"], manager.session_data["decision_log"])
        self.assertTrue(all(d["produced_message"] for d in result["status"]["decisions"]))
        manager._save_log.assert_called_once()
        manager._save_markdown = Mock()
        manager.session_log_file = "unused-test-path"
        with patch("builtins.open", mock_open()) as opened:
            SessionManager._save_log(manager)
            payload = "".join(c.args[0] for c in opened().write.call_args_list)
        self.assertEqual(json.loads(payload)["decision_log"], manager.runner.state.decision_log)

    def test_legacy_prompt_and_preset_calls_still_supported(self):
        self.assertNotIn("本轮规则决策", build_system_prompt("victim", 2))
        self.assertIn(get_preset_response("victim", 2), PRESET_RESPONSES["victim"][2])

    # ---------- 契约3：倾向描述拆成「语义方向 + 力度」，且模型侧只见中文 ----------
    def test_tendency_instructions_split_direction_and_strength(self):
        self.assertEqual(len(TENDENCY_INSTRUCTIONS), 26)
        for key, text in TENDENCY_INSTRUCTIONS.items():
            self.assertIn("方向：", text, key)
            self.assertIn("力度：", text, key)
            self.assertNotIn(key, text, key)  # 描述里不重复英文键

    def test_prompt_never_leaks_english_tendency_keys(self):
        for agent_id in ("bully", "victim", "bystander_a", "bystander_b"):
            for key in TENDENCY_INSTRUCTIONS:
                decision = {"should_speak": False, "response": "", "reasoning": "r",
                            "confidence": .3, "reaction_tendency": key}
                prompt = build_system_prompt(agent_id, 3, decision=decision, reaction_tendency=key)
                self.assertNotIn(key, prompt, f"{agent_id}/{key}")
                self.assertNotIn("reaction_tendency", prompt, f"{agent_id}/{key}")

    # ---------- 契约1：规则块只注入一次 ----------
    def test_rule_block_injected_exactly_once(self):
        base = build_system_prompt("bully", 2)
        decision = {"should_speak": False, "response": "", "reasoning": "r",
                    "confidence": .3, "reaction_tendency": "provoke"}
        prompt = build_system_prompt("bully", 2, decision=decision, reaction_tendency="provoke")
        self.assertEqual(prompt.count("## 本轮反应方向与力度（内容约束）"), 1)
        self.assertEqual(prompt.count("表达方式**：怎么说、说多少"), 1)
        # 注入成本必须低于旧版双份注入（旧：338/1816 = 18.6%）
        self.assertLess((len(prompt) - len(base)) / len(prompt), .12)

    # ---------- 契约4：user prompt 不再把对方台词截成 60 字 ----------
    def test_user_prompt_keeps_long_other_speech_for_reference(self):
        long_text = "我今天真的很难受，" + "你们说的话我都记着，我不想再重复一遍。" * 8
        state = state_for([msg("victim", long_text)])
        client = FakeClient("（皱了皱眉）你说不想重复，可我偏偏要当着大家再说一遍。")
        self.apply(state, "bully", client)
        user = client.calls[0][1]["content"]
        self.assertIn("本轮其他角色已说的话", user)
        self.assertGreaterEqual(len(user), 160)
        self.assertIn("请针对上面最相关的一句", user)
        # 对方台词按 240 字上窗呈现（旧实现 60 字，模型根本看不到可回应的内容）
        self.assertGreaterEqual(user.count(long_text[:80]), 1)

    # ---------- 契约5：离线变体池 ----------
    def test_offline_variant_pool_size_at_least_six_and_distinct(self):
        for tendency in sorted(TENDENCY_RESPONSES):
            for level in (1, 2, 3, 4, 5):
                for agent_id in ("bully", "victim", "bystander_a", "bystander_b"):
                    size = tendency_pool_size(tendency, level, agent_id)
                    self.assertGreaterEqual(size, 6, f"{tendency}/{level}/{agent_id}")
                    texts = {render_tendency_variant(tendency, level, i, None, agent_id)
                             for i in range(size)}
                    self.assertEqual(len(texts), size, f"{tendency}/{level}/{agent_id}")

    def test_offline_rotation_no_verbatim_repeat_within_10_rounds(self):
        """同会话同「倾向×档」连续 10 轮不得逐字复读。"""
        for tendency in ("provoke", "silence_fear", "endure_quietly", "observe_quiet", "avoid"):
            agent_id = "bully" if tendency == "provoke" else "victim"
            used, texts = [], []
            for _ in range(10):
                idx = pick_variant_index(tendency, 3, used, agent_id)
                used.append(idx)
                texts.append(render_tendency_variant(tendency, 3, idx, None, agent_id))
            self.assertEqual(len({t for t in texts if t}), len(texts), tendency)
            self.assertEqual(len(set(used)), min(10, tendency_pool_size(tendency, 3, agent_id)), tendency)

    def test_offline_variants_are_observable_action_plus_audible_speech(self):
        for tendency in sorted(TENDENCY_RESPONSES):
            for agent_id in ("bully", "victim", "bystander_a", "bystander_b"):
                for level in (1, 3, 5):
                    for idx in range(min(tendency_pool_size(tendency, level, agent_id), 12)):
                        text = render_tendency_variant(tendency, level, idx, None, agent_id)
                        self.assertTrue(speech_only(text), f"{tendency}/{level} 无台词: {text}")
                        self.assertGreaterEqual(net_len(text), 20,
                                                f"{tendency}/{level} 过短: {text}")

    def test_offline_slot_context_makes_lines_situational(self):
        ctx = {"me": "陈浩", "other": "思远", "reply": "思远刚才那句“我不想再说一遍”",
               "obj": "值日表", "lv": "中等"}
        # 任取一条含槽位的变体：填槽后必须出现对方名/关键词/场景物件，且不残留花括号
        size = tendency_pool_size("provoke", 3, "bully")
        filled = [render_tendency_variant("provoke", 3, i, ctx, "bully") for i in range(size)]
        bare = [render_tendency_variant("provoke", 3, i, None, "bully") for i in range(size)]
        self.assertNotIn("{", "".join(filled))
        situational = [t for t, b in zip(filled, bare) if t != b]
        self.assertGreater(len(situational), 1, "槽位注入没有改变任何文案")
        self.assertTrue(any(("思远" in t or "值日表" in t) for t in situational), situational[:2])

    def test_offline_full_run_metrics(self):
        """离线整段模拟：每轮 3 人、不逐字复读、平均字数回到 13 号基线量级。"""
        runner = MultiAgentRunner(use_llm=False)
        state = runner.state
        lines, per_pool = [], {}
        rounds = 0
        prev_count = len(state.messages)
        while rounds < 12 and not state.finished:
            result = runner.run_round()
            rounds += 1
            decisions = [d for d in state.decision_log if d["round"] == state.round]
            new_messages = state.messages[prev_count:]
            prev_count = len(state.messages)
            self.assertEqual(len(decisions), 3, f"第{state.round}轮人数 {len(decisions)}")
            self.assertEqual(len(new_messages), len(decisions))
            self.assertTrue(all(d["produced_message"] for d in decisions))
            for d in decisions:
                self.assertGreaterEqual(d["kept_len"], 20, d["generation_source"])
                self.assertIn(d["generation_source"],
                              ("preset_offline", "llm", "preset_invalid_response",
                               "preset_filtered_response", "preset_error"))
                per_pool.setdefault(d["pool_key"], []).append(d["variant_index"])
            lines += [m.content for m in new_messages if m.sender_id != "teacher"]
            if result["done"]:
                break
        self.assertGreaterEqual(rounds, 10)
        self.assertGreaterEqual(len(lines), 30)
        lengths = sorted(net_len(t) for t in lines)
        mean = sum(lengths) / len(lengths)
        median = lengths[len(lengths) // 2]
        p90 = lengths[min(len(lengths) - 1, int(len(lengths) * .9))]
        dup = 1 - len(set(lines)) / len(lines)
        self.assertGreaterEqual(mean, 35, f"离线均长回退：{mean:.1f} median={median} p90={p90}")
        self.assertGreaterEqual(median, 30)
        self.assertEqual(dup, 0.0, "离线出现逐字复读")
        for key, used in per_pool.items():
            self.assertEqual(len(used), len(set(used)), f"变体轮转失效：{key} {used}")

    # ---------- 契约6：extract_dialogue_only 不再腰斩多段台词 ----------
    def test_extract_dialogue_only_merges_all_speech_paragraphs(self):
        from multi_agent.thinking_filter import extract_dialogue_only
        raw = ("让我想想该怎么回应。我现在是霸凌者，陈浩，性格强势。\n\n"
               "（把书拍在桌上）思远，你昨天那句话什么意思？\n\n"
               "我说的话一个字都没改，你要是不服，咱们找一圈看谁站你那边。")
        out = extract_dialogue_only(raw, "陈浩")
        self.assertIn("你昨天那句话什么意思", out)
        self.assertIn("咱们找一圈看谁站你那边", out)
        self.assertNotIn("让我想想", out)
        self.assertGreaterEqual(net_len(out), net_len(raw) - 40)

    def test_extract_dialogue_only_still_strips_think_tags(self):
        from multi_agent.thinking_filter import extract_dialogue_only, filter_thinking_content
        raw = "<think>先分析局势</think>（别过脸）我没听见，你们继续说你的。"
        out = extract_dialogue_only(raw, "林雨桐")
        self.assertNotIn("先分析局势", out)
        self.assertIn("你们继续说你的", out)
        kept = filter_thinking_content("<think>内部推理</think>", "林雨桐")
        self.assertTrue(not kept or "内部推理" not in kept)

    # ---------- 契约7：可观测性（[DEBUG] 日志 + raw/kept 字数 + 来源） ----------
    def test_debug_logs_and_evidence_metrics_restored(self):
        state = state_for([msg("victim", "你别再盯着我说话了。")])
        client = FakeClient("（嗤了一声）盯着你怎么了？你自己心里没数吗？")
        self.apply(state, "bully", client)
        log = self.output.getvalue()
        self.assertIn("[DEBUG]", log)
        self.assertIn("LLM raw", log)
        self.assertIn("after filter", log)
        ev = state.decision_log[0]
        for field in ("raw_len", "kept_len", "generation_source", "pool_key",
                      "variant_index", "slot_context", "produced_message"):
            self.assertIn(field, ev, field)
        self.assertEqual(ev["generation_source"], "llm")
        self.assertEqual(ev["raw_len"], net_len(client.response))
        self.assertGreaterEqual(ev["kept_len"], 10)

    def test_error_evidence_records_exception_summary_without_message(self):
        state = state_for([msg("victim")])
        self.apply(state, "bully", FakeClient(error=True))
        ev = state.decision_log[0]
        self.assertEqual(ev["generation_source"], "preset_error")
        self.assertEqual(ev["error_type"], "RuntimeError")
        self.assertNotIn("fake client error", json.dumps(ev, ensure_ascii=False))
        self.assertGreater(ev["kept_len"], 20)


if __name__ == "__main__":
    unittest.main()

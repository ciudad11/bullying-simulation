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

    # ---------- 契约6b：think 泄漏守卫看原文（审查 Blocker 回归） ----------
    UNCLOSED_THINK = ("<think>\n先分析：他为什么不说话，我该不该继续\n\n"
                      "（攥拳）你凭什么这么说我")
    NESTED_THINK = ("<think>\n先分析：他在试探我\n<thinking>\n陈浩说的是「你不行」，我得回应\n</thinking>\n"
                    "</think>\n残留推理文本：你应该冷静一点，别把事情闹大。</think>\n\n"
                    "（垂下眼）我知道了，不说了。")
    TIDY_THINK = ("<think>\n先分析局势，再决定语气\n</think>\n\n"
                  "（抬眼）这事我不管，你们自己商量。")
    REASONING_PROBES = ("先分析", "试探", "我该不该继续", "我得回应", "你应该冷静")

    def test_malformed_think_downgrades_to_preset_instead_of_leaking(self):
        """未闭合 / 嵌套 think：推理正文不得进转录，证据必须记为降级来源（可统计）。"""
        from multi_agent.thinking_filter import suspicious_think_markup
        for raw in (self.UNCLOSED_THINK, self.NESTED_THINK):
            with self.subTest(raw=raw[:20]):
                self.assertTrue(suspicious_think_markup(raw), "守卫必须对过滤前原文判真")
                state = state_for([msg("victim", "你能不能别当着我面说这些。")])
                self.apply(state, "bully", FakeClient(raw))
                ev = state.decision_log[-1]
                self.assertEqual(ev["generation_source"], "preset_filtered_response", ev)
                self.assertTrue(ev.get("think_leak_suspect"), ev)
                line = state.messages[-1].content
                for probe in self.REASONING_PROBES:
                    self.assertNotIn(probe, line, line)
                self.assertTrue(speech_only(line), line)

    def test_wellformed_think_pair_is_not_misreported_as_downgrade(self):
        """反向保护：规矩的成对标签仍走 llm 来源，且不得把剥离下来的推理留在台词里。"""
        from multi_agent.thinking_filter import extract_dialogue_only, suspicious_think_markup
        out = extract_dialogue_only(self.TIDY_THINK, "刘洋")
        self.assertFalse(suspicious_think_markup(self.TIDY_THINK, out))
        self.assertNotIn("先分析局势", out)
        self.assertIn("你们自己商量", out)
        state = state_for([msg("victim", "……我没有别的意思。")])
        self.apply(state, "bystander_b", FakeClient(self.TIDY_THINK))
        ev = state.decision_log[-1]
        self.assertEqual(ev["generation_source"], "llm", ev)
        self.assertFalse(ev.get("think_leak_suspect"), ev)

    # ---------- 契约6c：段级过滤——他人转述段与纯旁白不合成台词（审查 Major 回归） ----------
    def test_reported_speech_paragraph_is_not_merged_into_current_role_line(self):
        from multi_agent.thinking_filter import extract_dialogue_only
        raw = ("陈浩说：「你别太过分了，这事儿明明是你先起的头。」\n\n"
               "（低头）你别这样说，我也没做什么。")
        out = extract_dialogue_only(raw, "思远")
        self.assertNotIn("你先起的头", out, out)
        self.assertNotIn("你别太过分了", out, out)
        self.assertIn("你别这样说", out)

    def test_pure_narration_returns_empty_and_downgrades(self):
        from multi_agent.thinking_filter import extract_dialogue_only
        raw = "（教室里安静下来，只有翻书的声音）\n\n（他转过头去，没有看你）"
        self.assertEqual(extract_dialogue_only(raw, "陈浩").strip(), "", "纯旁白不得合成台词")
        state = state_for([msg("victim", "……我不想说这些。")])
        self.apply(state, "bully", FakeClient(raw))
        ev = state.decision_log[-1]
        self.assertEqual(ev["generation_source"], "preset_filtered_response", ev)
        self.assertTrue(speech_only(state.messages[-1].content), state.messages[-1].content)

    # ---------- 契约6d：去重丢弃的消息不送评估，且留下可观测证据 ----------
    def test_deduplicated_message_is_not_evaluated_and_leaves_evidence(self):
        state = create_initial_state()
        state.round = 3
        state.messages = []
        state.pending_messages = []
        state.decision_log = []
        runner = StepRunner(state, None, use_llm=False)
        m1 = Message("bully", "徐凯", "bully", "第一句台词。", round=3)
        m2 = Message("bully", "徐凯", "bully", "第二句重复轮次台词。", round=3)
        runner._merge_result({"messages": [m1], "pending_messages": [m1]})
        # 故意把 pending_messages 排在 messages 之前：待评估过滤必须以转录为权威
        runner._merge_result({"pending_messages": [m1, m2], "messages": [m2]})
        self.assertEqual([m.content for m in state.messages if m.sender_id == "bully"],
                         ["第一句台词。"], state.messages)
        self.assertEqual([m.content for m in state.pending_messages], ["第一句台词。"],
                         "被去重丢弃的消息不得进入待评估列表")
        self.assertTrue(any(d.get("deduplicated") for d in state.decision_log), state.decision_log)
        # 节点每轮返回「既有消息 + 新消息」的整表回显，不得被记成去重丢弃（证据污染回归）
        echo = Message("victim", "思远", "victim", "先让我把话说完。", round=3)
        state.messages.append(echo)
        runner._merge_result({"messages": list(state.messages) + [m1], "pending_messages": []})
        self.assertEqual(sum(1 for d in state.decision_log if d.get("deduplicated")), 1,
                         state.decision_log)
        self.assertEqual([m.content for m in state.messages], ["第一句台词。", "先让我把话说完。"])

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


# ============================================================
# 任务#60 返工断言集（Blocker-1 根因 A/B、Major-1..4）
# ------------------------------------------------------------
# 全部离线：只调 llm_client / prompts / thinking_filter 的纯函数，不 import Flask、
# 不起服务、不调真实 LLM、不读数据库。情景数据用 ast 静态取 web/app.py 的 `_scenarios`。
# ============================================================

from multi_agent import llm_client as L
from multi_agent import prompts as PR
from multi_agent.thinking_filter import extract_dialogue_only


def _load_scenarios():
    import ast
    app_py = Path(__file__).resolve().parents[1] / "web" / "app.py"
    tree = ast.parse(app_py.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "_scenarios" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("web/app.py 里找不到 _scenarios")


def _cast_persona(scenario, role):
    return ((scenario.get("cast") or {}).get(role) or {}).get("personality") or {}


def _ctx(scenario, role, persona, level=2):
    return L.build_preset_context(agent_id=role, level=level, agent_name="林小满",
                                  other_name="陈浩", other_content="你再说一遍试试",
                                  scenario_text=scenario.get("description", ""),
                                  persona=persona)


# 一次真实会话的取号跨度上限：AUTO_END_CONFIG["max_rounds"] 默认 40，一轮最多 3 名
# 候选发言者，落在同一个「倾向×档×角色」池上的序号数不会超过 40。
SESSION_SPAN = 40
ROLES = ("bully", "victim", "bystander_a", "bystander_b")

# 任务#60-2：「幕后递话/撇清」族的可复核口径。这批标记必须覆盖
# `_PERSONA_INDIRECT_LINES/TAILS` 的每一条模板（test_h1 先断言这一点），
# 因此用它判定「某条产出属不属于这一族」不依赖人工形容词分类。
# 注意标记词的**精确度**同样重要：口径只能落在操控族模板上，不能顺手命中
# 26 键正常池句。曾把这条写成裸的「有人替」，于是 victim 的
# `thank_relieved@high`「有人替我说话，我好久没这么安心了」被误判成越界——
# 那是被保护者表达感激，不是递话撇清。故一律带完整族短语。
_COVERT_MARKS = (
    "我没当面讲", "没当面讲过", "不赖我", "替我去讲", "有人替我提", "别说是我",
    "我什么都没说过", "不是我挑的头", "用不着我", "我不当众讲", "回头有人跟你提",
    "这话传到哪儿去", "挨个问一圈", "这账不是", "后面传成", "我不提第二遍",
    "各自有杆秤", "别来问我", "替不了", "我也管不了", "这事我不管",
    "是你的事", "别人不肯跟你走", "我没动过你", "后面怎么算",
    "别人有别人的办法", "你别往我心里去", "我说了不算", "总有人替",
    "话我不能当面讲", "谁在背后讲", "别人怎么想", "我不拦谁", "不当面说人",
    # 任务#64 P0-A 新增 27 条操控族文案的覆盖词（`test_h1` 先断言 48/48 全覆盖，
    # 否则扩库之后「bystander_a/victim 0 命中」就退化成空断言）。逐条按
    # 「只出现在操控族、不出现在 firm/mediate 库与 26 键池句」选词，取证见
    # `test_evidence/integration_check_20261115/rework_64/probes/q11_p0a_copy_check.py`。
    # 两个踩过的坑写在这里：**不带 `{obj}`**（标记是对**渲染后**文本判的，槽位已被
    # 填掉，含槽位的标记永远命不中）；不用裸「做文章」（mediate 族
    # 「省得有人借它做文章」会被误判成操控）。
    "当着谁的面讲", "不代表我说过", "一个字都不用说", "没传过话", "一句中立的话",
    "我就讲了那么一句", "我不提第一遍", "碰都没碰", "我这儿没有答案",
    "不接话，也不拦", "做成做不成", "只笑一下", "不管就是不管", "我从不催谁",
    "只听了我半句话", "说得客客气气", "我要真想怎么样", "别人的嘴我管不着",
    "只是闲话", "有人替我分", "有人替我盯着", "周围有谁在笑", "只是句闲话",
    "换个地方待着", "问到第三遍", "算到我头上", "我只是没拦",
)


def _covert_hits(text):
    return [m for m in _COVERT_MARKS if m in (text or "")]


# 用户可自己填的人设（自定义参与者 traits / 自建情景 cast）：这类写法会把
# bystander_a 判成 indirect，是 32 个预设格之外、但线上真实可达的形态。
_INDIRECT_A_PERSONA = {"traits": ["说话客气", "背后替她说话"], "speakingStyle": ["慢"],
                       "cognitiveStyle": "先把话停在半句", "background": "班里谁都不想得罪"}

# 任务#60-3：同样形态的**受害者**人设（用户在自定义参与者/自建情景 cast 里填
# 「说话客气 / 背后…」这类词就能造出来），验证 victim+indirect 不再借到操控族。
_INDIRECT_V_PERSONA = {"traits": ["说话客气", "背后替她说话"], "speakingStyle": ["慢"],
                       "cognitiveStyle": "先把话停在半句", "background": "被孤立半年，谁都不想先开口"}

# 任务#64：从众旁观者的**真·幕后操控型**人设（自定义参与者/自建情景 cast 写得出）。
# P0-B 之后 s2/s5 的预设 cast（起哄/附和/看热闹）落 soft，操控族只能由这类人设进入，
# `test_h2` 的「渲染可达」口径就用它。
_INDIRECT_B_PERSONA = {"traits": ["背后传话", "爱使眼色"], "speakingStyle": ["阴阳怪气"],
                       "cognitiveStyle": "先把话递给谁", "background": "班里谁跟谁不对付他最清楚"}


class Rework60PersonaReworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = _load_scenarios()
        cls.tendencies = sorted(TENDENCY_RESPONSES.keys())
        assert len(cls.tendencies) == 26, "26 键契约被改动"

    # ---------- A（Blocker-1 根因 A）：立场必须判得出来 ----------
    def test_a1_no_cast_cell_classifies_all_neutral(self):
        """8 情景 × 4 角色：每个人设至少能定出一个立场（全 neutral 的格子必须为 0）。"""
        bad = []
        for i, sc in enumerate(self.scenarios):
            for role in ROLES:
                if not (sc.get("cast") or {}).get(role):
                    continue
                persona = _cast_persona(sc, role)
                words, stance = L.classify_persona(persona)
                if stance == "neutral":
                    bad.append(f"s{i+1}/{role}")
        self.assertEqual(bad, [], f"仍有人设整档判 neutral：{bad}")

    def test_a2_six_bully_manipulation_words_get_indirect_stance(self):
        """用户点名的 s6 九个词必须落到可筛选立场，而不是 neutral（「当面软、背后硬」）。"""
        persona = _cast_persona(self.scenarios[5], "bully")
        want = ["当面客气", "笑着说话", "暗示", "指使", "表面礼貌", "背后操控",
                "拉帮结派", "冷暴力", "控制欲强"]
        for w in want:
            self.assertNotEqual(L._word_stance(w), "neutral", w)
        self.assertEqual(L.classify_persona(persona)[1], "indirect")

    def test_a3_negated_persona_words_do_not_flip_stance(self):
        """Minor-1 顺手做掉：否定式人设词不许把人设判反。"""
        for w in ("不敢拒绝", "不凶", "没威胁过谁"):
            self.assertEqual(L._word_stance(w), "soft", w)
        for w in ("天不怕地不怕", "什么都不怕"):
            self.assertEqual(L._word_stance(w), "firm", w)

    def test_a4_indirect_output_has_no_face_to_face_confrontation(self):
        """s6 霸凌者产出必须是「借手/递话/敲打」，不许留当面威逼的动作与台词。"""
        sc = self.scenarios[5]
        persona = _cast_persona(sc, "bully")
        for role in ("bully",):
            for tendency in self.tendencies:
                for level in (1, 2, 3, 4, 5):
                    entry, bucket = L._pool_and_bucket(tendency, level, role)
                    if not entry or bucket is None:
                        continue
                    openers, lines, tails = L._segments(
                        entry, bucket, _ctx(sc, role, persona, level), role, tendency)
                    for t in lines:
                        self.assertFalse(L._is_direct_confront_speech(t),
                                         f"{tendency}@L{level} 台词仍当面威逼：{t}")
                    for t in tails:
                        self.assertFalse(L._is_direct_confront_speech(t),
                                         f"{tendency}@L{level} 收尾仍当面威逼：{t}")
                    for t in openers:
                        self.assertFalse(L._is_direct_confront_action(t),
                                         f"{tendency}@L{level} 动作仍当面威逼：{t}")

    def test_a5_manner_prefix_not_pasted_when_incompatible(self):
        """根因 A 的第 2 步（选定口径：相容性门禁）：立场不相容时不贴语气词。"""
        self.assertEqual(L._merge_manner("（斜着眼，往前逼了一步）", "当面客气"),
                         "（斜着眼，往前逼了一步）",
                         "「当面客气」不许贴在当面挑衅的动作前面")
        self.assertEqual(L._merge_manner("（低着头，往后退了半步）", "嘴硬"),
                         "（低着头，往后退了半步）")
        # 相容时仍照旧前置（不破坏无矛盾场景的既有行为）
        self.assertEqual(L._merge_manner("（把话头递给旁边的人）", "当面客气"),
                         "（当面客气，把话头递给旁边的人）")

    # ---------- B（Blocker-1 根因 B）：补底不得回填示弱句 ----------
    def test_b1_firm_pool_has_no_soft_backfill_and_no_wraparound(self):
        """firm/indirect 格子：一个真实会话跨度内取到的序号里 0 条示弱句，
        且池深度 ≥ 跨度 ⇒ 不会因池太小回绕成逐字复读（#54 缺陷）。"""
        cells = 0
        min_pool = None
        soft_hits = []
        for i, sc in enumerate(self.scenarios):
            for role in ROLES:
                persona = _cast_persona(sc, role)
                if not persona:
                    continue
                stance = L.classify_persona(persona)[1]
                if stance not in ("firm", "indirect"):
                    continue
                for tendency in self.tendencies:
                    for level in (1, 2, 3, 4, 5):
                        ctx = _ctx(sc, role, persona, level)
                        size = L.tendency_pool_size(tendency, level, role, ctx)
                        min_pool = size if min_pool is None else min(min_pool, size)
                        cells += 1
                        for idx in range(min(SESSION_SPAN, size)):
                            text = L.render_tendency_variant(tendency, level, idx, ctx, role)
                            body = re.sub(r"[（(][^（()）]*[）)]", "", text)
                            if L._is_soft_speech(body):
                                soft_hits.append(f"s{i+1}/{role}/{tendency}@L{level}#{idx}:{body[:24]}")
        self.assertEqual(soft_hits[:8], [], f"跨度内仍出现示弱句 {len(soft_hits)} 处，例：{soft_hits[:8]}")
        self.assertGreaterEqual(min_pool, SESSION_SPAN,
                                f"最小池 {min_pool} < 会话跨度 {SESSION_SPAN} ⇒ 会回绕复读")
        self.assertGreaterEqual(cells, 100, "覆盖格子数异常（立场筛选没跑到 firm/indirect 格子）")

    def test_b2_s8_hard_victim_first_screen_is_not_soft(self):
        """用户第一例：s8 放学路上的威胁，受害者不该软弱（审查实测 12/12 → 必须 0/12）。"""
        sc = self.scenarios[7]
        persona = _cast_persona(sc, "victim")
        self.assertEqual(L.classify_persona(persona)[1], "firm")
        for tendency in ("withdraw", "endure_quietly", "wait_observe", "avoid",
                         "silent_resist", "resist_follow"):
            if tendency not in TENDENCY_RESPONSES:
                continue
            for level in (1, 2, 3, 4, 5):
                ctx = _ctx(sc, "victim", persona, level)
                body = [re.sub(r"[（(][^（()）]*[）)]", "",
                               L.render_tendency_variant(tendency, level, k, ctx, "victim"))
                        for k in range(12)]
                self.assertEqual([t for t in body if L._is_soft_speech(t)], [],
                                 f"{tendency}@L{level} 首屏 12 条里有示弱句")

    # ---------- C（Major-4）：swap 库只在「该档被筛空」时启用 ----------
    def test_c1_swap_library_used_only_when_bucket_emptied(self):
        """库句只在「该档被筛空/筛薄」时补，而且补的是**本倾向窗口**。

        任务#64 P0-A 改了补底来源，旧口径「原池筛空 ⇒ 必须能看到库句」已经不等价：
        兄弟档（同倾向另一档）现在是第一顺位补底来源，兄弟档供满 3 条时库句贡献
        0 条，行为仍然正确（实测 `endure_quietly@L1` 就是这种格子）。所以这条拆成
        两个真正的不变量：
          ① 原池过闸后非空 ⇒ 保留句**一条不少**，且库句最多只补到 `_MIN_FIRM_SEGMENTS`
             （不许整档粘贴——#60 的 78% 库句占比就是这么来的）；
          ② 原池被筛空 ⇒ 输出的每一句都必须来自「本倾向可用来源」
             （本倾向兄弟档 ∪ 本倾向库窗口），且条数补到 `floor`，
             **绝不回落到整库**（回落会把成员级差异化抵消，见 `test_p0a4`）。
        """
        floor = L._MIN_FIRM_SEGMENTS
        for i, sc in enumerate(self.scenarios):
            for role in ROLES:
                persona = _cast_persona(sc, role)
                stance = L.classify_persona(persona)[1] if persona else "neutral"
                if stance not in ("firm", "indirect"):
                    continue
                for tendency in L._FIRM_SWAP_TENDENCIES:
                    for level in (1, 2, 3, 4, 5):
                        entry, bucket = L._pool_and_bucket(tendency, level, role)
                        lib_lines = L._stance_lib(
                            stance, "high" if bucket == "high" else "low", role)[0]
                        orig = list(entry.get(bucket) or [])
                        sib = list(entry.get("high" if bucket == "low" else "low") or [])
                        ctx = _ctx(sc, role, persona, level)
                        _o, lines, _t = L._segments(entry, bucket, ctx, role, tendency)
                        win = set(L._tendency_window(lib_lines, tendency))
                        lib = set(lib_lines)
                        where = f"s{i+1}/{role}/{tendency}@L{level}"
                        scene = L._ctx_get(ctx, "_scene")
                        # `keep` 必须与 `_segments` 同口径：**先**过情景门禁（道具词 +
                        # 活动证据），**再**过立场门禁。少了前半段就会把「本情景里没有
                        # 的作业/画笔」这类句子算成「应当保留」，s1/observe_quiet@L1 的
                        # 「我还写着呢」正是这样被误报成丢句。
                        keep = [t for t in orig
                                if not (scene and L._scene_foreign(t, scene))
                                and not L._is_soft_speech(t)
                                and not (stance == "indirect"
                                         and L._is_direct_confront_speech(t))]
                        if keep:
                            missing = [t for t in keep if t not in lines]
                            self.assertEqual(missing, [],
                                             f"{where} 原池非空却丢了保留句：{missing[:2]}")
                            self.assertLessEqual(len([t for t in lines if t in lib]),
                                                 max(0, floor - len(keep)),
                                                 f"{where} 原池非空仍过量粘贴库句")
                        else:
                            self.assertTrue(lines, f"{where} 原池筛空后一句不剩")
                            foreign = [t for t in lines if t not in win and t not in set(sib)]
                            self.assertEqual(foreign, [],
                                             f"{where} 补底出现了非本倾向来源的句子：{foreign[:2]}")
                            self.assertGreaterEqual(len(lines), min(floor, len(set(sib) | win)),
                                                    f"{where} 补底不足 {floor} 条")

    def test_c2_firm_and_indirect_tails_carry_scene_anchor(self):
        """库句收尾必须带 `{obj}` 或情景限定词，否则门禁削减不到它（Major-4 的根）。
        台词段故意不逐条带 {obj}：head 与 tail 各带同一个动态槽位会被 `_echo_grade`
        判成「共用槽位」硬重复而剔掉组合，实测把池压到 6 条 ⇒ 长会话回绕复读
        （#54 缺陷）。锚点由 tails 承担，lines 只需整族里存在锚点句即可。"""
        for stance in ("firm", "indirect"):
            for bucket in ("low", "high"):
                # indirect 现在按角色分族（任务#60-2），四个角色都要过同一道锚点断言
                for role in ROLES:
                    lines, tails, openers = L._stance_lib(stance, bucket, role)
                    self.assertTrue(tails and openers, f"{stance}/{bucket}/{role} 库为空")
                    for t in tails:
                        self.assertTrue(L._has_scene_anchor(t),
                                        f"{stance}/{bucket}/{role} 收尾缺情景锚点：{t}")
                    soft_tails = [t for t in tails if L._is_soft_speech(t)]
                    self.assertEqual(soft_tails, [],
                                     f"{stance}/{bucket}/{role} 库收尾示弱：{soft_tails}")

    def test_c3_cross_scenario_bodies_not_all_identical(self):
        """同 (角色,倾向,档,序号) 的**正文**（剥掉动作括注）不得 8 情景逐字相同。"""
        probe = [("victim", "wait_observe", 2), ("victim", "endure_quietly", 2),
                 ("bully", "provoke", 2), ("bully", "low_intensity_taunt", 1),
                 ("bystander_a", "support_victim", 2), ("bystander_b", "follow_majority", 2)]
        flat_text_cells = []
        for role, tendency, level in probe:
            if tendency not in TENDENCY_RESPONSES:
                continue
            for idx in range(12):
                bodies = []
                for sc in self.scenarios:
                    persona = _cast_persona(sc, role)
                    ctx = _ctx(sc, role, persona, level)
                    text = L.render_tendency_variant(tendency, level, idx, ctx, role)
                    bodies.append(re.sub(r"[（(][^（()）]*[）)]", "", text))
                uniq = set(b for b in bodies if b)
                if len(uniq) == 1 and len(bodies) == len(self.scenarios):
                    flat_text_cells.append(f"{role}/{tendency}@L{level}#{idx}")
        self.assertEqual(flat_text_cells, [],
                         f"{len(flat_text_cells)} 个格子 8 情景正文逐字相同：{flat_text_cells[:6]}")

    # ---------- D（Major-1）：尾块硬编码性格必须按 cast/立场条件化 ----------
    HARD_CODED = {
        "bully": ["你喜欢点评别人、压人一头", "（不屑地笑）", "（翻了个白眼）"],
        "victim": ["极度尴尬、想躲藏", "（低着头）、（眼眶泛红）"],
        "bystander_a": ["（不屑地笑）", "（翻了个白眼）"],
        "bystander_b": ["（不屑地笑）", "（翻了个白眼）"],
    }

    def _role_map(self, scenario):
        return {role: {"name": ((scenario.get("cast") or {}).get(role) or {}).get("name"),
                       "role": role,
                       "role_name": ((scenario.get("cast") or {}).get(role) or {}).get("role_name"),
                       "personality": _cast_persona(scenario, role)}
                for role in ROLES}

    def test_d1_cast_persona_removes_hardcoded_tail_text(self):
        for i, sc in enumerate(self.scenarios):
            rm = self._role_map(sc)
            for role, marks in self.HARD_CODED.items():
                persona = _cast_persona(sc, role)
                if L.classify_persona(persona)[1] == "neutral":
                    continue
                sp = PR.build_system_prompt(role, 3, "（甲：你好）", sc["title"],
                                            sc["description"], role_map=rm)
                for m in marks:
                    self.assertNotIn(m, sp, f"s{i+1}/{role} 尾块仍写死：{m}")

    def test_d2_victim_soft_volume_line_is_stance_derived(self):
        """「你的话可以小声、断续、退缩」只在 **立场=soft** 时保留（那句话本身就是
        对 soft 人设的描述）；firm/indirect 必须换成派生措辞。"""
        mark = "你的话可以小声、断续、退缩"
        for i, sc in enumerate(self.scenarios):
            persona = _cast_persona(sc, "victim")
            stance = L.classify_persona(persona)[1]
            sp = PR.build_system_prompt("victim", 2, "", sc["title"], sc["description"],
                                        role_map=self._role_map(sc))
            if stance in ("firm", "indirect"):
                self.assertNotIn(mark, sp, f"s{i+1} victim/{stance} 仍写死音量句")
            else:
                self.assertIn(mark, sp, f"s{i+1} victim/{stance} 音量句应保留")

    def test_d3_no_cast_prompts_keep_original_wording(self):
        """回归保护：没有 cast/自定义人设时，尾块逐字保留原文案（不破坏既有行为）。"""
        for role, marks in self.HARD_CODED.items():
            sp = PR.build_system_prompt(role, 3, "（甲：你好）", "课间", "明天要交作品",
                                        role_map=None)
            for m in marks:
                self.assertIn(m, sp, f"{role} 无 cast 时原文案丢失：{m}")

    def test_d4_stance_helper_never_breaks_prompt_build(self):
        self.assertEqual(PR._persona_stance({}), "neutral")
        self.assertEqual(PR._persona_stance(None), "neutral")
        self.assertEqual(PR._persona_stance({"traits": ["不肯服软"]}), "firm")
        sp = PR.build_system_prompt("victim", 2, "", "t", "d",
                                    role_map={"victim": {"name": "X", "role": "victim",
                                                          "personality": None}})
        self.assertIn("你的角色", sp)

    # ---------- E（Major-3）：单段主路径也要做归属过滤 ----------
    def test_e1_single_paragraph_other_report_is_dropped(self):
        raw = "陈浩说：「你别太过分了，所有人都别理她。」"
        self.assertEqual(extract_dialogue_only(raw, "思远"), "",
                         "单段（主流形态）里他人原话仍被当成本角色台词")

    def test_e2_single_paragraph_own_report_and_plain_speech_kept(self):
        """放行面：本人第三人称自述、正常台词、括注+台词都不许被误杀。"""
        self.assertEqual(extract_dialogue_only("思远说：我没太过分。", "思远"),
                         "思远说：我没太过分。")
        self.assertEqual(extract_dialogue_only("（低下头）你们别这样。", "思远"),
                         "（低下头）你们别这样。")
        self.assertEqual(extract_dialogue_only("这事我不认。", "思远"), "这事我不认。")

    def test_e3_pure_parenthetical_single_paragraph_downgrades(self):
        self.assertEqual(extract_dialogue_only("（低下头）（往后退了一步）", "思远"), "",
                         "纯括注单段与多段口径不一致")

    def test_e4_merge_limit_does_not_touch_single_paragraph(self):
        """`MERGE_PARAGRAPH_LIMIT` 只作为 `_limit_merge` 的默认值参与**多段**合并；
        改默认值不改函数（默认值在 def 时就绑定了），所以只改常量是空断言。
        这里同时钉住两件事：单段路径根本不调 `_limit_merge`，多段路径调且受上限约束。"""
        import multi_agent.thinking_filter as TF
        raw = "你别太过分了，所有人都别理她。"
        for limit in (1, 2, 3, 99):
            old_const, old_fn = TF.MERGE_PARAGRAPH_LIMIT, TF._limit_merge
            calls = []
            TF.MERGE_PARAGRAPH_LIMIT = limit
            TF._limit_merge = (lambda kept, _l=limit, _c=calls:
                               (_c.append(len(kept)), kept[-_l:] if len(kept) > _l else kept)[1])
            try:
                self.assertEqual(extract_dialogue_only(raw, "思远"), raw, f"limit={limit}")
                self.assertEqual(calls, [], f"limit={limit} 单段路径不应进入 _limit_merge")
            finally:
                TF.MERGE_PARAGRAPH_LIMIT, TF._limit_merge = old_const, old_fn
        multi = "第一段台词。\n\n第二段台词。\n\n第三段台词。"
        old_fn = TF._limit_merge
        for limit, expect in ((1, 1), (2, 2), (99, 3)):
            TF._limit_merge = lambda kept, _l=limit: kept[-_l:] if len(kept) > _l else kept
            try:
                got = len(extract_dialogue_only(multi, "思远").split("\n\n"))
                self.assertEqual(got, expect, f"多段 limit={limit} 合并段数")
            finally:
                TF._limit_merge = old_fn
        self.assertEqual(TF.MERGE_PARAGRAPH_LIMIT, 2, "上限常量本身不得被本轮改动")

    def test_e5_multi_paragraph_contract_unchanged(self):
        raw = ("陈浩说：「你别太过分了，这事儿明明是你先起的头。」\n\n"
               "（低头）你别这样说，我也没做什么。")
        out = extract_dialogue_only(raw, "思远")
        self.assertNotIn("你先起的头", out)
        self.assertIn("你别这样说", out)

    # ---------- F（Major-2）：前端档案卡优先级 = 后端生效优先级 ----------
    def test_f1_frontend_persona_priority(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("本机没有 node，前端纯函数用例跳过（已在 rework_60 探针中手工跑过）")
        root = Path(__file__).resolve().parents[1]
        probe = root / "tests" / "frontend" / "display_persona_probe.js"
        self.assertTrue(probe.exists(), f"缺少前端用例脚本：{probe}")
        p = subprocess.run([node, str(probe.relative_to(root).as_posix())],
                           cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace")
        out = (p.stdout or "").strip()
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("全部通过", out)

    # ---------- H（任务#60-2）：indirect 库句族按角色门控 ----------
    def test_h1_bystander_a_indirect_never_emits_covert_family(self):
        """PM 裁定：正义旁观者不做幕后操控。
        「幕后递话/撇清族」的口径不是形容词——`_COVERT_MARKS` 必须覆盖操控族
        每一条台词/收尾模板（本测试先断言这条完备性），再用同一批标记词判定产出，
        这样「0 命中」不依赖人工分类。范围：26 倾向 × L1–L5 × 序号 0–2。
        同时守住霸凌者/从众旁观者仍可借手（s4/s6/s2/s5 的 A 项修复不得被削）。"""
        # 族定义完备性：操控族每条台词/收尾都要含标记词，否则「0 命中」是空断言
        for bucket in ("low", "high"):
            for t in L._PERSONA_INDIRECT_LINES[bucket] + L._PERSONA_INDIRECT_TAILS[bucket]:
                self.assertTrue(_covert_hits(t), f"操控族模板未被标记覆盖：{t}")
        # 专用族纯度：正义旁观者拿到的模板本身不得含操控标记
        for bucket in ("low", "high"):
            lines, tails, openers = L._stance_lib("indirect", bucket, "bystander_a")
            self.assertTrue(lines and tails and openers, "bystander_a 专用族为空")
            for t in lines + tails:
                self.assertEqual(_covert_hits(t), [], f"专用族混入操控句：{t}")
        # 门控表：bystander_a=mediate、victim=firm（任务#60-3），其余角色仍走操控族
        self.assertEqual(L._indirect_lib("low", "bully")[0], L._PERSONA_INDIRECT_LINES["low"])
        self.assertEqual(L._indirect_lib("low", "bystander_b")[0],
                         L._PERSONA_INDIRECT_LINES["low"])
        self.assertEqual(L._indirect_lib("low", "victim")[0], L._PERSONA_FIRM_LINES["low"])
        self.assertNotEqual(L._indirect_lib("low", "victim")[0],
                            L._PERSONA_INDIRECT_LINES["low"])
        self.assertNotEqual(L._indirect_lib("low", "bystander_a")[0],
                            L._PERSONA_INDIRECT_LINES["low"])

        sc = self.scenarios[0]
        a_hits, a_cells, a_min_pool = 0, 0, None
        for tendency in self.tendencies:
            for level in (1, 2, 3, 4, 5):
                ctx = _ctx(sc, "bystander_a", _INDIRECT_A_PERSONA, level)
                self.assertEqual(ctx.get("_stance"), "indirect", "注入人设必须落 indirect")
                size = L.tendency_pool_size(tendency, level, "bystander_a", ctx)
                a_min_pool = size if a_min_pool is None else min(a_min_pool, size)
                for idx in range(3):
                    text = L.render_tendency_variant(tendency, level, idx, ctx, "bystander_a")
                    a_cells += 1
                    self.assertTrue(text.strip(), f"bystander_a/{tendency}@L{level}#{idx} 空文本")
                    hits = _covert_hits(text)
                    if hits:
                        a_hits += 1
                        self.fail(f"正义旁观者产出幕后递话/撇清族：{tendency}@L{level}#{idx} "
                                  f"{hits} ⇒ {text[:60]}")
        self.assertEqual(a_hits, 0)
        # 新族不能把池压到会话跨度以下（否则长会话回绕复读，违反 #54-7 的初衷）
        self.assertGreaterEqual(a_min_pool, SESSION_SPAN,
                                f"bystander_a indirect 最小池 {a_min_pool} < 跨度 {SESSION_SPAN}")

    def test_h2_covert_family_still_available_to_bully_and_follower(self):
        """反向保底：门控不能把「借手族」削掉（#60-2 A 项修复的落点）。

        任务#64 P0-B 改了判定口径之后，s2/s5 从众旁观者的 cast 词（起哄/附和/
        看热闹/事不关己）**不再判 indirect 而是 soft**——审查#61 F3 点名的正是
        「s5 在旁边起哄、看热闹，被演成『我什么都没说过』」，所以这两格落 soft 是
        **本批交付**而不是回退（逐词判定表与 32 格集合取证：
        `test_evidence/integration_check_20261115/rework_64/out/q14_p0b_diff.json`，
        常驻断言见 `test_b3_p0b_indirect_overfire_fixed`）。于是这条「反向保底」
        不能再只拿 s2/s5 的**渲染**当口径，改成互补的两条：
          ① 结构：`_indirect_lib` 对 bully / bystander_b 仍指向操控族（与情景无关，
             门控没被 P0-A 的窗口化改写顺手削掉）；
          ② 可达：霸凌者（s4/s6 预设 cast）与人设真读成幕后操控的从众旁观者
             （自定义参与者/自建情景 cast 写「背后传话、爱使眼色」）都必须
             **实际渲染出**带操控标记的句子。
        """
        # ① 结构：两个角色的操控族都在，且条数够切窗口（P0-A 之后 12/12）
        for role in ("bully", "bystander_b"):
            for bucket in ("low", "high"):
                lines, tails, openers = L._indirect_lib(bucket, role)
                self.assertEqual(lines, L._PERSONA_INDIRECT_LINES[bucket],
                                 f"{role} 的借手族被换掉了")
                self.assertGreaterEqual(len(tails), L._MIN_FIRM_SEGMENTS * 2,
                                        f"{role}/{bucket} 操控族收尾不足，会话内会回绕")
        # ②a 霸凌者渲染
        for si in (4, 6):
            sc = self.scenarios[si - 1]
            persona = _cast_persona(sc, "bully")
            if L.classify_persona(persona)[1] != "indirect":
                continue
            ctx = _ctx(sc, "bully", persona, 2)
            hits = 0
            for tendency in ("provoke", "low_intensity_taunt", "escalate_severe", "apologize"):
                for idx in range(6):
                    text = L.render_tendency_variant(tendency, 2, idx, ctx, "bully")
                    hits += bool(_covert_hits(text))
            self.assertGreater(hits, 0, f"s{si} 霸凌者借手族被削弱")
        # ②b 从众旁观者：预设 cast 现在落 soft（P0-B），所以用「幕后操控型」人设走
        #     自定义参与者/自建情景那条真实可达路径来验渲染可达性
        sc = self.scenarios[1]
        self.assertEqual(L.classify_persona(_cast_persona(sc, "bystander_b"))[1], "soft",
                         "P0-B 之后 s2/bystander_b 应落 soft（起哄/附和不是幕后手法）")
        ctx = _ctx(sc, "bystander_b", _INDIRECT_B_PERSONA, 2)
        self.assertEqual(ctx.get("_stance"), "indirect", "操控型从众人设必须落 indirect")
        b_hits = 0
        for tendency in ("follow_majority", "avoid", "wait_observe", "hesitate"):
            for idx in range(6):
                text = L.render_tendency_variant(tendency, 2, idx, ctx, "bystander_b")
                self.assertTrue(text.strip(), "bystander_b 渲染为空")
                b_hits += bool(_covert_hits(text))
        self.assertGreater(b_hits, 0, "从众旁观者借手族被削弱")

    def test_h3_victim_indirect_takes_firm_library_never_covert(self):
        """任务#60-3（PM 裁定 3）：受害者 + stance=indirect 一律不得产出幕后递话/
        撇清族——那类句子是施轧者的工具。实现口径 = 台词选取走现有 firm（不示弱）库，
        与 bystander_a 的 mediate 门控同一入口（`_INDIRECT_FAMILY_BY_ROLE`），
        **不新建第四个句库**。代价（受害者「当面不说、背后记仇」被丢成「不示弱」）
        已在 llm_client 的 `_INDIRECT_FAMILY_BY_ROLE` 注释里写死。

        断言顺序与 `test_h1` 一致，先证明标记表能覆盖操控族全部模板（否则「0 命中」
        是空断言），再穷举 victim × 26 倾向 × L1–L5 × 序号 0–2 取实际产出。"""
        # 1) 完备性：操控族 18 条模板每条都含标记词
        covered, total = 0, 0
        for bucket in ("low", "high"):
            for t in list(L._PERSONA_INDIRECT_LINES[bucket]) + list(L._PERSONA_INDIRECT_TAILS[bucket]):
                total += 1
                covered += bool(_covert_hits(t))
        self.assertEqual(covered, total, f"标记表没覆盖满操控族：{covered}/{total}")
        # 2) victim 实际取到的库（firm 库）本身 0 条操控句，且不是操控库
        for bucket in ("low", "high"):
            v_lines, v_tails, v_openers = L._stance_lib("indirect", bucket, "victim")
            self.assertEqual((v_lines, v_tails, v_openers),
                             (L._PERSONA_FIRM_LINES[bucket], L._PERSONA_FIRM_TAILS[bucket],
                              L._PERSONA_FIRM_OPENERS),
                             f"victim/indirect 没有落到现有 firm 库（{bucket}）")
            for t in v_lines + v_tails:
                self.assertEqual(_covert_hits(t), [], f"victim 库句混入操控句：{t}")
            for t in v_tails:
                self.assertTrue(L._has_scene_anchor(t), f"victim 库收尾缺情景锚点：{t}")
        # 3) 门控表没有顺手改掉别的角色（bystander_a=mediate、霸凌者/从众=操控族）
        self.assertEqual(L._INDIRECT_FAMILY_BY_ROLE,
                         {"bystander_a": "mediate", "victim": "firm"})
        self.assertEqual(L._indirect_lib("low", "bully")[0], L._PERSONA_INDIRECT_LINES["low"])
        self.assertEqual(L._indirect_lib("low", "bystander_b")[0],
                         L._PERSONA_INDIRECT_LINES["low"])
        self.assertEqual(L._indirect_lib("low", "bystander_a")[0],
                         L._INDIRECT_MEDIATE_LINES["low"])

        # 4) 穷举实测：victim × 26 倾向 × L1–L5 × 序号 0–2
        sc = self.scenarios[0]
        cells, v_hits, v_min_pool = 0, [], None
        for tendency in self.tendencies:
            for level in (1, 2, 3, 4, 5):
                ctx = _ctx(sc, "victim", _INDIRECT_V_PERSONA, level)
                self.assertEqual(ctx.get("_stance"), "indirect", "注入人设必须落 indirect")
                size = L.tendency_pool_size(tendency, level, "victim", ctx)
                v_min_pool = size if v_min_pool is None else min(v_min_pool, size)
                for idx in range(3):
                    text = L.render_tendency_variant(tendency, level, idx, ctx, "victim")
                    cells += 1
                    self.assertTrue(text.strip(), f"victim/{tendency}@L{level}#{idx} 空文本")
                    self.assertGreaterEqual(net_len(text), 20,
                                            f"victim/{tendency}@L{level}#{idx} 净字数跌破 20：{text}")
                    hits = _covert_hits(text)
                    if hits:
                        v_hits.append(f"{tendency}@L{level}#{idx} {hits} ⇒ {text[:60]}")
        self.assertEqual(cells, 26 * 5 * 3, "穷举格数不符，断言范围被缩小")
        self.assertEqual(v_hits, [], f"受害者产出幕后递话/撇清族：{v_hits[:4]}")
        # 5) 池深：换库不能把受害者池压到会话跨度以下（#54-7 / PM 定的最小池 ≥ 40）
        self.assertGreaterEqual(v_min_pool, SESSION_SPAN,
                                f"victim indirect 最小池 {v_min_pool} < 跨度 {SESSION_SPAN}")

    # ---------- 不变量：26 键、每轮 3 人、非空文本、契约5 净字数 ----------
    def test_g1_26_key_tables_nonempty_and_length(self):
        """不变量：26 键三表不增删改名；任何格子不产出空文本；净字数沿用项目
        既有口径（单条 ≥20、按角色均值 ≥35，见本文件契约5 的两处断言），
        不引入新口径去「制造」回退。"""
        self.assertEqual(len(TENDENCY_INSTRUCTIONS), 26)
        self.assertEqual(len(TENDENCY_LABEL_CN), 26)
        self.assertEqual(set(TENDENCY_INSTRUCTIONS), set(TENDENCY_LABEL_CN))
        self.assertEqual(set(TENDENCY_RESPONSES), set(TENDENCY_INSTRUCTIONS))
        sc = self.scenarios[7]
        per_role = {}
        for role in ROLES:
            persona = _cast_persona(sc, role)
            lens = []
            for tendency in self.tendencies:
                for level in (1, 3, 5):
                    ctx = _ctx(sc, role, persona, level)
                    for idx in (0, 1, 2, 3, 4, 5):
                        text = L.render_tendency_variant(tendency, level, idx, ctx, role)
                        self.assertTrue(text.strip(), f"{role}/{tendency}@L{level}#{idx} 空文本")
                        n = net_len(text)
                        lens.append(n)
                        self.assertGreaterEqual(n, 20,
                                                f"{role}/{tendency}@L{level}#{idx} 净字数跌破 20：{text}")
            per_role[role] = sum(lens) / len(lens)
        for role, mean in per_role.items():
            self.assertGreaterEqual(mean, 35, f"{role} 离线均长回退：{mean:.1f}")


class Rework64MergeTests(unittest.TestCase):
    """任务#64 合并返工批次的常驻断言（P0-A/B/C、P1-D/E、P2-F、M-1 + c3 连带洞）。

    一项至少一条，命名前缀 = 项目代号；全部离线：只调 `llm_client` /
    `thinking_filter` 的纯函数，用 ast 静态取 `web/app.py` 的 `_scenarios`，
    不 import Flask、不起服务、不调真实 LLM、不读数据库。
    改前/改后的**全量**数字在 `test_evidence/integration_check_20261115/rework_64/`
    （q9 = 跨倾向同句对、q2 = 40 抽收尾去重、q12 = P0-C 两个口径、q14 = P0-B 判定表），
    这里只留能在 CI 时间预算内跑的判据。
    """

    #: 一次会话里同一格最多取到多少个不同序号（`AUTO_END_CONFIG["max_rounds"]` 默认 40）
    SPAN = SESSION_SPAN

    @classmethod
    def setUpClass(cls):
        cls.scenarios = _load_scenarios()
        cls.tendencies = sorted(TENDENCY_RESPONSES)
        cls.personas = {}
        for si, sc in enumerate(cls.scenarios):
            for role in ROLES:
                cls.personas[(si, role)] = _cast_persona(sc, role)

    # ── 小工具（口径与 rework_64 探针逐字一致，避免测试与取证两套算法）──
    @staticmethod
    def _body(text):
        return re.sub(r"[（(][^（()）]*[）)]", "", text or "").strip()

    @staticmethod
    def _split_tail(text):
        """逐字照抄 review_61/probes/r61_p10_tails.py:41-46（F1 的收尾切法）。"""
        t = re.sub(r"（[^）]*）", "", text or "").strip()
        parts = [p.strip() for p in re.split(r"(?<=[。！？…])", t) if p.strip()]
        return parts[-1] if parts else ""

    def _ctx_for(self, si, role, level):
        return _ctx(self.scenarios[si], role, self.personas[(si, role)], level)

    # ---------- P0-A：成员级差异化 / 收尾供给 ----------
    def test_p0a1_no_two_tendencies_can_emit_the_same_sentence(self):
        """P0-A 验收①（PM 更正一：「新增对必须回 0」）。

        口径：**跨倾向同句** = 同一情景、同一角色、同一档位下，两个不同倾向在
        同一序号跨度内渲染出**剥掉动作括注后逐字相同**的正文。这是用户当场看到的
        「换个人说同一句话」，也是审查#61/复验#62 的 `cells/pairs/types` 三个数。
        样本：8 情景 × 4 角色 × L1–L3 × 26 倾向 × 序号 0–5（≈ 1.5 万次渲染，
        全量网格见 q9：改前 70 格/6,440 对/14 类型 → 改后 0/0/0）。
        另外单点名对必须回 0：`endure_quietly|withdraw`、`respond_cautious|withdraw`、
        `low_intensity_taunt|silence_fear`（这三对是#62 复验新增的）。
        """
        named = {("endure_quietly", "withdraw"), ("respond_cautious", "withdraw"),
                 ("low_intensity_taunt", "silence_fear")}
        dup_pairs, named_hits = set(), []
        for si in range(len(self.scenarios)):
            for role in ROLES:
                for level in (1, 2, 3):
                    got = {}
                    for t in self.tendencies:
                        ctx = self._ctx_for(si, role, level)
                        bodies = {self._body(L.render_tendency_variant(t, level, i, ctx, role))
                                  for i in range(6)}
                        got[t] = {b for b in bodies if b}
                    keys = sorted(got)
                    for a in range(len(keys)):
                        for b in range(a + 1, len(keys)):
                            shared = got[keys[a]] & got[keys[b]]
                            if shared:
                                pair = (keys[a], keys[b])
                                dup_pairs.add(pair)
                                if pair in named:
                                    named_hits.append((si + 1, role, level, shared.pop()[:34]))
        self.assertEqual(named_hits, [], f"点名的三个新增对又出现了：{named_hits[:3]}")
        self.assertEqual(sorted(dup_pairs)[:6], [],
                         f"{len(dup_pairs)} 对倾向在同格出同句：{sorted(dup_pairs)[:6]}")

    def test_p0a2_session_tail_dedup_at_least_12(self):
        """P0-A 验收②（PM 裁定 B-1 的一半：一次会话内收尾去重 ≥12）。

        口径逐字照抄 review_61 的 F1 模拟：40 轮、`rnd % len(倾向表)`、
        陈浩/「你再说一遍试试」、只用 `description` 建 context、`split_tail` 先剥
        括注再按 `(?<=[。！？…])` 取末句。受害者/霸凌者用 r61 点名的固定倾向表，
        两个旁观者用 26 键全表（这条是 sweep 扩展，与 q2 的 `--sweep` 同一口径）。
        改前实测：8×4×5 全网格 min=5、mean 6.9；改后 min=12。
        """
        victim_tends = ["silence_fear", "withdraw_cry", "withdraw", "weak_protest",
                        "seek_help", "silent_resist", "endure_quietly", "retaliate_back"]
        bully_tends = ["provoke", "low_intensity_taunt", "escalate_severe", "avoid"]
        worst, worst_where = None, ""
        checked = 0
        for si in range(len(self.scenarios)):
            for role in ROLES:
                tends = ([t for t in bully_tends if t in TENDENCY_RESPONSES] if role == "bully"
                         else [t for t in victim_tends if t in TENDENCY_RESPONSES]
                         if role == "victim" else self.tendencies)
                for level in (1, 2, 3, 4, 5):
                    ctx = self._ctx_for(si, role, level)
                    used, tails = {}, []
                    for rnd in range(40):
                        t = tends[rnd % len(tends)]
                        idx = pick_variant_index(t, level, used.get(t, []), role, ctx)
                        used.setdefault(t, []).append(idx)
                        tails.append(self._split_tail(
                            render_tendency_variant(t, level, idx, ctx, role)))
                    distinct = len({x for x in tails if x})
                    checked += 1
                    if worst is None or distinct < worst:
                        worst, worst_where = distinct, f"s{si+1}/{role}@L{level}"
                    self.assertGreaterEqual(
                        distinct, 12, f"{worst_where} 40 抽只去重出 {distinct} 条收尾")
        self.assertEqual(checked, len(self.scenarios) * len(ROLES) * 5)

    def test_p0a3_library_windows_are_disjoint_per_group(self):
        """P0-A 结构断言：库按倾向切的是**互不相交**的窗口，不是只换起点。

        `_rotate` 只换起点不换成员 ⇒ 旧实现的「按倾向轮转」在成员层面完全相同，
        这正是复验#62 里「P0-A 未动」的那一项。现在组号 = 26 键排序序号 % 组数，
        组数 = `len(lib) // 窗口`，所以：
          ① 不同组号的窗口两两不相交；② 每个窗口正好 `窗口` 条；
          ③ 组数 > 1（文案少到切不出两组就等于没做成员级差异化）。
        """
        win = L._MIN_FIRM_SEGMENTS
        libs = []
        for stance in ("firm", "indirect"):
            for role in ROLES:
                for bucket in ("low", "high"):
                    libs.append(("%s/%s/%s" % (stance, role, bucket), L._stance_lib(stance, bucket, role)))
        # soft 只补了收尾库（台词段仍走 26 键原池），故只查条数不查窗口
        self.assertGreaterEqual(len(L._PERSONA_SOFT_TAILS), 2 * L._MIN_FIRM_SEGMENTS,
                                "soft 收尾库少于两组的量，会话内去重撑不到 12")
        for where, (lines, tails, openers) in libs:
            for seg_name, seq in (("lines", lines), ("openers", openers)):
                groups = max(1, len(seq) // win)
                self.assertGreater(groups, 1,
                                   f"{where}/{seg_name} 只有 {len(seq)} 条，切不出多组窗口")
                wins = {}
                for t in self.tendencies:
                    w = L._tendency_window(seq, t)
                    self.assertEqual(len(w), win, f"{where}/{seg_name} 窗口不是 {win} 条")
                    wins.setdefault(L._tendency_index(t) % groups, set()).update(w)
                keys = sorted(wins)
                for a in range(len(keys)):
                    for b in range(a + 1, len(keys)):
                        self.assertEqual(wins[keys[a]] & wins[keys[b]], set(),
                                         f"{where}/{seg_name} 组 {keys[a]} 与 {keys[b]} 窗口相交")
                self.assertEqual(len(set().union(*wins.values())), groups * win,
                                 f"{where}/{seg_name} 窗口成员有重复")

    def test_p0a4_thin_bucket_never_falls_back_to_whole_library(self):
        """P0-A `no_leak`：补不满下限时**不许**回落整库。

        旧实现 `_top_up(..., filler=旋转后的整库)` 在差 1 条时把整库倒进来，
        成员级差异化被这条泄漏路径抵消（评测 W1 的 35 格 ⇒ 复验#62 报「P0-A 未动」）。
        现在一句话只可能来自三个地方：本档原池过闸句、本倾向兄弟档、**本倾向库窗口**；
        落在「库内但不在本倾向窗口」的句子数量必须恒为 0。
        """
        win_only = 0
        for si in range(len(self.scenarios)):
            for role in ROLES:
                stance = L.classify_persona(self.personas[(si, role)])[1]
                if stance not in ("firm", "indirect"):
                    continue
                for t in self.tendencies:
                    for level in (1, 2, 3, 4, 5):
                        entry, bucket = L._pool_and_bucket(t, level, role)
                        lib_lines = list(L._stance_lib(
                            stance, "high" if bucket == "high" else "low", role)[0])
                        if not lib_lines:
                            continue
                        ctx = self._ctx_for(si, role, level)
                        _o, lines, _t = L._segments(entry, bucket, ctx, role, t)
                        orig = set(entry.get(bucket) or [])
                        sib = set(entry.get("high" if bucket == "low" else "low") or [])
                        outside = [x for x in lines if x not in orig | set(lib_lines) | sib]
                        self.assertEqual(outside, [],
                                         f"s{si+1}/{role}/{t}@L{level} 补底混进池外句子")
                        win = set(L._tendency_window(lib_lines, t))
                        leak = [x for x in lines if x in set(lib_lines) - win]
                        self.assertEqual(leak, [],
                                         f"s{si+1}/{role}/{t}@L{level} 补底用了**本倾向窗口之外**"
                                         f"的库句 {len(leak)} 条 ⇒ 泄漏回整库了")
                        if win & set(lines):
                            win_only += 1
        self.assertGreater(win_only, 0, "没有一格用到库窗口，断言面为空")

    def test_p0a5_min_pool_over_session_span(self):
        """P0-A 附带守住 #54-7：立场驱动格的**最小池必须 > 同格取号跨度**。

        改前（#62 复验）现网最小池 6（`endure_quietly` 整档被筛空 + 台词/收尾同带
        `{obj}` 被 `_echo_grade` 判硬重复），40 轮会话必然回绕成逐字复读。
        现在 8 情景 × 4 角色 × 26 倾向 × L1–L5 全网格 min ≥ 40。
        """
        min_pool, where = None, ""
        for si in range(len(self.scenarios)):
            for role in ROLES:
                for t in self.tendencies:
                    for level in (1, 2, 3, 4, 5):
                        ctx = self._ctx_for(si, role, level)
                        size = tendency_pool_size(t, level, role, ctx)
                        if min_pool is None or size < min_pool:
                            min_pool, where = size, f"s{si+1}/{role}/{t}@L{level}"
                        self.assertGreater(size, 0, f"{where} 池为空")
        self.assertGreaterEqual(min_pool, self.SPAN, f"最小池 {min_pool}（{where}）< 跨度 {self.SPAN}")

    # ---------- P0-B ----------
    def test_p0b1_indirect_overfire_words_no_longer_flip_stance(self):
        """P0-B（审查#61 F3）：六个词的极性判定必须固定，且 32 格的 indirect 只剩两格。

        「怕被孤立」是**受害处境**、「背后替她说话」是暗中**相助**、「不起哄」是
        否定式、「看热闹/事不关己/爱看热闹」是随大流的弱立场——都不是幕后施害手法。
        逐词判定表（199 词）与 32 格集合的改前/改后取证：`out/q14_p0b_diff.json`
        （改 6 词、改 2 格：s2/bystander_b、s5/bystander_b indirect → soft）。
        """
        for w, want in (("怕被孤立", "soft"), ("看热闹", "soft"), ("爱看热闹", "soft"),
                        ("事不关己", "soft"), ("不起哄", "neutral"), ("背后替她说话", "neutral")):
            self.assertEqual(L._word_stance(w), want, w)
        # 施害手法词族不许被守卫连带削掉（`test_a2` 只查 6 个霸凌者词，这里补守卫边界）
        for w in ("背后操控", "冷暴力", "嚼舌根", "孤立别人", "排挤人", "递话", "敲打",
                  "拉帮结派", "使眼色", "阴阳怪气", "起哄", "煽风"):
            self.assertEqual(L._word_stance(w), "indirect", w)
        # 「看热闹/事不关己」已经从幕后词族搬进 soft 词族
        self.assertNotIn("看热闹", L._INDIRECT_MARKS)
        self.assertNotIn("事不关己", L._INDIRECT_MARKS)
        self.assertIn("看热闹", L._SOFT_MARKS)
        # 32 格：只有两个霸凌者格仍是 indirect
        grid = {}
        for si, sc in enumerate(self.scenarios, 1):
            for role in ROLES:
                grid["s%d/%s" % (si, role)] = L.classify_persona(_cast_persona(sc, role))[1]
        self.assertEqual(sorted(k for k, v in grid.items() if v == "indirect"),
                         ["s4/bully", "s6/bully"], grid)
        self.assertEqual(grid["s2/bystander_b"], "soft")
        self.assertEqual(grid["s5/bystander_b"], "soft")

    def test_p0b2_soft_cells_still_carry_scene_anchor(self):
        """P0-B 的连带洞（本批新发现，`test_c3` 的成因）：soft 立场原来**没有一条
        带锚点的收尾**，于是同格在 8 个情景下正文逐字相同。

        改前看不出来是因为 s2/s5 的从众旁观者被误判 indirect、走了 firm/indirect 的
        锚点收尾合并；P0-B 把它落回 soft 之后 8/8 撞满（全量：`probes/q17_c3_scope.py`
        改前 0 格 / 改后 102 格逐字相同，全部 soft）。修法是给 soft 同一套
        「原池锚点收尾在前 + 库收尾整体轮转在后」，库句必须逐条带 `{obj}`。
        """
        for t in L._PERSONA_SOFT_TAILS:
            self.assertTrue(L._has_scene_anchor(t), f"soft 收尾缺情景锚点：{t}")
            self.assertEqual(t.count("{obj}"), 1, "收尾里的锚点槽位必须恰好一个：" + t)
        flat = []
        for role in ("bystander_b", "victim"):
            for t in ("follow_majority", "observe_quiet", "wait_observe", "avoid"):
                if t not in TENDENCY_RESPONSES:
                    continue
                for idx in range(4):
                    bodies = [self._body(render_tendency_variant(
                        t, 2, idx, self._ctx_for(si, role, 2), role))
                        for si in range(len(self.scenarios))]
                    if len({b for b in bodies if b}) == 1:
                        flat.append(f"{role}/{t}#{idx}")
        self.assertEqual(flat, [], f"{len(flat)} 格 8 情景正文仍逐字相同：{flat[:6]}")
        # 收尾去重也要覆盖 soft：40 抽里至少 12 条不同收尾（与 `test_p0a2` 同口径）
        ctx = self._ctx_for(1, "bystander_b", 2)
        self.assertEqual(ctx.get("_stance"), "soft", "s2/bystander_b 必须是 soft 才算守住本项")
        tails = [self._split_tail(render_tendency_variant(
            self.tendencies[i % len(self.tendencies)], 2, i, ctx, "bystander_b"))
            for i in range(40)]
        self.assertGreaterEqual(len({x for x in tails if x}), 12,
                                f"soft 格 40 抽只去重 {len({x for x in tails if x})} 条收尾")

    # ---------- P0-C ----------
    #: #62 那 60 条 s6 霸凌者真实台词里被判 X/Y/Z 的 9 个正文（剥掉动作括注、`{obj}`
    #: 已填成「那个座位」）。语料与判定脚本口径见
    #: `test_evidence/integration_check_20261115/acceptance_62/a62_item2_classify.py`，
    #: 回溯与两个口径的复跑见 `rework_64/probes/q12_p0c_s6_replay.py`。
    P0C_WIDE_AUDIT = (
        "你从头到脚都透着那股寒酸劲儿，装什么大方。……那个座位你别往我心里去，我什么都没说过。",
        "你从头到脚都透着那股寒酸劲儿，装什么大方。谁在背后讲的不重要，那个座位你自己知道就行。",
        "你从头到脚都透着那股寒酸劲儿，装什么大方。这事我不管，那个座位你怎么接是你的事。",
        "你从头到脚都透着那股寒酸劲儿，装什么大方。那个座位我不当众讲，回头有人跟你提，你别说是我。",
        "还在琢磨那个？行，真有毅力，就是没什么用。……那个座位你别往我心里去，我什么都没说过。",
        "还在琢磨那个？行，真有毅力，就是没什么用。别当真啊，我随口一说你倒当个事办，那个座位有那么重要？",
        "还在琢磨那个？行，真有毅力，就是没什么用。谁在背后讲的不重要，那个座位你自己知道就行。",
        "还在琢磨那个？行，真有毅力，就是没什么用。这事我不管，那个座位你怎么接是你的事。",
        "还在琢磨那个？行，真有毅力，就是没什么用。那个座位我不当众讲，回头有人跟你提，你别说是我。",
    )
    #: 上面 9 条正文里越线的**台词段模板**（池成员，`{obj}` 只在收尾，故不在此列）
    P0C_POOL_LINES = ("你从头到脚都透着那股寒酸劲儿，装什么大方。",
                      "还在琢磨那个？行，真有毅力，就是没什么用。")

    def test_p0c1_wide_audit_lines_are_gated_by_production_predicate(self):
        """P0-C 的根因是**两套口径**：#62 用宽口径正则判越线，生产侧只用
        `_is_direct_confront_speech` 筛句 ⇒ 「评审判越线、生产判没问题」的句子留在
        indirect 池里，`test_a4` 于是成了自洽但空的断言。

        这条断言把两个口径钉在一起：**宽口径判 X/Y/Z 的句子，生产谓词必须也判越线**。
        复跑数字（`out/q12_p0c_s6_replay.json`）：会话命中率 改前 26/60 → 改后 0/60
        （PM 目标 ≤3/60）；池暴露度 改前 3/30 成员（10%）→ 改后 0/28。
        """
        for body in self.P0C_WIDE_AUDIT:
            self.assertTrue(L._is_direct_confront_speech(body),
                            f"宽口径判越线、生产谓词放行：{body[:30]}")
        for line in self.P0C_POOL_LINES:
            self.assertTrue(L._is_direct_confront_speech(line), line)
        # 越线成员必须真的不在 s6 霸凌者（indirect）任何一格的台词段里
        sc = self.scenarios[5]
        persona = _cast_persona(sc, "bully")
        self.assertEqual(L.classify_persona(persona)[1], "indirect")
        leaked = []
        for t in self.tendencies:
            for level in (1, 2, 3, 4, 5):
                entry, bucket = L._pool_and_bucket(t, level, "bully")
                _o, lines, tails = L._segments(entry, bucket, _ctx(sc, "bully", persona, level),
                                               "bully", t)
                for x in list(lines) + list(tails):
                    if any(w in x for w in self.P0C_POOL_LINES):
                        leaked.append(f"{t}@L{level}:{x[:24]}")
        self.assertEqual(leaked, [], f"越线句子仍在 s6 霸凌者池里：{leaked[:4]}")

    def test_p0c2_two_explicit_narrowings_are_kept(self):
        """扩表的两处**刻意收窄**也要钉住，否则下次有人顺手把词表补全就会误杀。

        · 「好笑」不单独收：`我可不觉得好笑` 是**拒绝跟着嘲笑**（resist_follow 的正确
          行为），收进来会被 indirect 门禁筛掉；
        · 「听见了 / 都听见了」都不收（宽口径收的是裸「听见了」）：池里带这三个字的
          句子有一半是**被压的人在说话**——`我一字一句都听见了，只是不照着你们的调子答`
          是硬气受害者、`他刚才讲的不是假话，你们也都听见了` 是打圆场。借人数压人
          那一类由「大家伙儿都」承接。
        """
        self.assertFalse(L._is_direct_confront_speech("我可不觉得好笑。"))
        self.assertFalse(L._is_direct_confront_speech("我一字一句都听见了，只是不照着你们的调子答。"))
        self.assertFalse(L._is_direct_confront_speech(
            "这事我当面说一句：他刚才讲的不是假话，你们也都听见了。"))
        self.assertTrue(L._is_direct_confront_speech(
            "别用那种眼神看我，{obj}到底怎么回事，大家伙儿都听见了。"))

    # ---------- P1-D ----------
    def test_p1d_reply_slot_never_takes_stage_direction(self):
        """P1-D：`{reply}` 取「上一条发言的具体抓手」时不许抽舞台提示括注。

        旧 `_keyword_from` 把 `（）` 也当分隔符、排序键只有长度 ⇒ 「带括注的消息里
        最长括注 > 最长台词小句」实测 3,913/4,858 = 80.6%（#63 第 4.2 节），
        969 会话/10,614 条消息回放里 17.44% 的 `{reply}` 引的是动作描写。
        只改运行时槽位取值，不碰池成员与顺序（#54-7）。
        """
        cases = [
            ("（嬉皮笑脸地晃过来）龙飞，你今天别想走。", "你今天别想走"),
            ("（把书扔到一边）这道题我讲过三遍了。", "这道题我讲过三遍了"),
            ("（低着头，往后退了半步）", ""),
            ("你先把手里的东西放下。", "你先把手里的东西放下"),
        ]
        for raw, want in cases:
            got = L._keyword_from(raw)
            self.assertNotIn("（", got, f"槽位里还有括注：{raw} → {got}")
            if want:
                self.assertEqual(got, want, raw)
        # 真实池动作括注全部不得成为 `{reply}`
        for t, entry in TENDENCY_RESPONSES.items():
            for op in (entry.get("openers") or []):
                inner = re.sub(r"[（()）]", "", op).replace("{sw}", "").strip("，, ")
                if len(inner) < 2:
                    continue
                got = L._keyword_from(op + "这事你说了不算。")
                self.assertNotEqual(got, inner, f"{t} 的括注被当成抓手：{inner}")

    # ---------- P1-E ----------
    def test_p1e_nine_real_soft_sentences_are_gated(self):
        """P1-E（审查#61 F2）：#63 第 4.1 节人工剔出的 **9 句真示弱**必须被生产谓词判出。

        改前这 9 条「语义口径判得出、生产谓词判不出」，48 条残留全落在 s8 硬气受害者
        的 `withdraw@L3/L4/L5`（每格 40 个序号命中 16 次）。补词后这些句子在
        firm/indirect 格里被 `_ok` 筛掉、由兄弟档/本倾向窗口补底，**不是回填**。
        """
        nine = ("我不要了", "都给你们", "别再说了", "别说我了", "难受", "更糟",
                "不想辩", "请你别", "不喜欢被这样讲")
        for w in nine:
            self.assertIn(w, L._SOFT_SPEECH_MARKS, w)
        samples = ("{obj}我不要了，都给你们。", "别再说了，我听着就行。",
                   "别说我了，我心里难受。", "这样只会更糟。", "我不想辩什么。",
                   "请你别这样讲。", "我不喜欢被这样讲。")
        for s in samples:
            self.assertTrue(L._is_soft_speech(s), s)
        # s8 硬气受害者：跨度内 0 条示弱（与 test_b1/test_b2 同一判据、不同覆盖面）
        sc = self.scenarios[7]
        persona = _cast_persona(sc, "victim")
        hits = []
        for t in self.tendencies:
            for level in (1, 2, 3, 4, 5):
                ctx = _ctx(sc, "victim", persona, level)
                size = tendency_pool_size(t, level, "victim", ctx)
                for idx in range(min(self.SPAN, size)):
                    body = self._body(render_tendency_variant(t, level, idx, ctx, "victim"))
                    if L._is_soft_speech(body):
                        hits.append(f"{t}@L{level}#{idx}:{body[:24]}")
        self.assertEqual(hits[:6], [], f"s8 硬气受害者跨度内仍有示弱句 {len(hits)} 处：{hits[:6]}")

    # ---------- P2-F ----------
    def test_p2f_unknown_role_falls_back_to_mediate_not_covert(self):
        """P2-F：`_indirect_lib` 对**白名单外**的角色必须落 mediate，不许靠
        「不在 `by_role` 表里就默认操控族」把新角色（自定义角色类型）演成递话撇清。

        改前 `family = by_role.get(agent_id, "covert")` 是**默认放行**：只要人设判成
        indirect，任何未知 agent_id 都借到幕后操控族。现在默认族 = mediate，
        只有显式列进 `_COVERT_FAMILY_ROLES` 的角色（霸凌者/从众旁观者）才拿操控族。
        """
        self.assertEqual(tuple(L._COVERT_FAMILY_ROLES), ("bully", "bystander_b"))
        for fake in ("", "bystander_c", "custom_de7a4eca", "teacher", "BULLY"):
            for bucket in ("low", "high"):
                lines, tails, openers = L._indirect_lib(bucket, fake)
                self.assertEqual(lines, L._INDIRECT_MEDIATE_LINES[bucket],
                                 f"{fake or '(空)'} 拿到了操控族台词")
                self.assertEqual(tails, L._INDIRECT_MEDIATE_TAILS[bucket],
                                 f"{fake or '(空)'} 拿到了操控族收尾")
                for x in list(lines) + list(tails):
                    self.assertEqual(_covert_hits(x), [], f"{fake} 族混入操控句：{x[:26]}")
        # 白名单内仍是操控族（P2-F 不许把 #60-2 的修复改没）
        for role in L._COVERT_FAMILY_ROLES:
            self.assertEqual(L._indirect_lib("low", role)[0], L._PERSONA_INDIRECT_LINES["low"])
        # 显式表优先于白名单推断
        self.assertEqual(L._INDIRECT_FAMILY_BY_ROLE["bystander_a"], "mediate")
        self.assertEqual(L._indirect_lib("low", "victim")[0], L._PERSONA_FIRM_LINES["low"])

    # ---------- M-1 ----------
    def test_m1_compound_report_verb_own_speech_not_dropped(self):
        """M-1（审查#61 F4）：`姓名 + 双音节动词` 的本人第三人称自述不许被整条降级。

        上一版把动词写成 `(?:说|道|答|问)`，捕获组 `{1,4}?` 会**吞掉第一个动词**
        （`思远说道：` 捕获到 `思远说`），于是「段首姓名==本人 ⇒ 放行」这条分支
        从来没有成立过，6 个姓名 × 3 个双音节动词 = 18 个合法组合在单段路径被丢弃
        （`prompts.py:223` 要求单段成文 ⇒ 返回 '' ⇒ supervisor 整条降级到预设）。
        本条穷举这 18 个组合，并守住三条边界：他人转述仍拦、第三人称代词头仍拦、
        对话人称代词头（你/我）放行。全量 NEW_DROP 误杀复跑见
        `probes/q10_m1_other_report.py`（改前 18 例误杀 → 改后 0）。
        """
        import multi_agent.thinking_filter as TF
        names = ("思远", "陈浩", "龙飞", "林小满", "马超", "韩雪")
        verbs = ("说道", "问道", "答道", "叫道", "喊道", "笑道", "说", "问", "答", "喊")
        modifiers = ("", "小声", "冷冷", "连忙", "点头")
        for name in names:
            for verb in verbs:
                for mod in modifiers:
                    p = f"{name}{mod}{verb}：我今天不挪地方。"
                    m = TF._OTHER_REPORT.match(p)
                    self.assertIsNotNone(m, p)
                    self.assertEqual(m.group(1), name, f"{p} 捕获到 {m.group(1)!r} ⇒ 动词被吞")
                    self.assertTrue(TF._is_own_speech_paragraph(p, name), p)
                    self.assertEqual(TF.extract_dialogue_only(p, name), p,
                                     f"{p} 仍被整条降级（NEW_DROP 回归）")
                    if name != "陈浩":
                        self.assertFalse(TF._is_own_speech_paragraph(p, "陈浩"),
                                         f"{p} 他人转述被放行")
        # 副词表是**显式枚举**（`思远点了头说道：` 这种非枚举形式匹配不到谓词，
        # 于是按普通台词段走 `_has_audible_speech`，同样不会被当成他人转述丢掉）。
        self.assertFalse(TF._OTHER_REPORT.match("思远点了头说道：我今天不挪地方。"))
        self.assertEqual(TF.extract_dialogue_only("思远点了头说道：我今天不挪地方。", "思远"),
                         "思远点了头说道：我今天不挪地方。")
        # 代词头：当前角色在跟对面说话 / 第一人称自述，不是第三者转述
        for p in ("你说：你到底想怎么样。", "我说：我不挪地方。", "咱们说：这事到此为止。"):
            self.assertTrue(TF._is_own_speech_paragraph(p, "思远"), p)
        # 但代词头仍然要过「有可听话音」这一关，纯括注照样拦
        self.assertFalse(TF._is_own_speech_paragraph("他说：", "思远"))
        self.assertFalse(TF._is_own_speech_paragraph("她道：我不去。", "思远"))
        self.assertEqual(TF.extract_dialogue_only("陈浩说：（转过身，没出声）", "思远"), "")


class Rework71StanceAxisTests(unittest.TestCase):
    """任务#71（F1 求助轴 + F2 场合轴）常驻断言，全部离线。

    为什么这三类判据必须常驻而不是探针：
      · F1 的失效在**真实会话**里表现为「强硬受害者当场答应去找老师」（s8 实测 5/30 明确
        + 2/30 边缘，铁证 ebeb841b r11「我认栽，我跟你一块去张老师那儿」），而
        `prompts.py` 里管「求助权威」的规则只有那一条、原先与立场无关 ⇒ 没有常驻断言，
        下一次改尾块又会静默退回去；
      · F2 的「不当面挑衅/场合」口径此前只有 rework_64 的 q19 探针（跑一次就没人再跑），
        #65 交接时明确登记为「待补常驻」，本轮补上。

    三条纪律：
      ① neutral 与「人设不在场」路径**逐字不变**——锚点是下面两个常量（#71 开工前
         `prompts.py` 里的原文，逐字节抄下来钉死），不是靠比对某次快照；
      ② 判据是**行为**（该出现的出现、该消失的消失），长度只有独立的预算断言，
         两者不许互相凑；
      ③ 不许为了让本类变绿而改判定口径。
    """

    # ---- #71 开工前原文（回归锚点，改动前逐字保留、改动后 neutral 分支必须仍等于它）----
    TEACHER_ORIGINAL = (
        "- **当老师(张老师)在对话中对你说了一些话时，要根据老师的具体话语回应**：\n"
        "  - 老师安慰你 → 可能倾诉、解释情况、或表达委屈\n"
        "  - 老师问你情况 → 需要回答老师的问题，描述自己的处境\n"
        "  - 老师为你说话 → 可能感到安全、敢于表达更多")
    BULLY_SPOTLIGHT_ORIGINAL = (
        "- **聚光灯效应**：当老师或同学当众反复点名受害者名字时，你可能借机加剧尴尬"
        "（例如起哄围观、拿对方的反应取乐），让受害者更加无地自容；"
        "具体怎么说是你当场自己决定的，不要套用固定句子")
    OCCASION_MARK = "6. 你在意场合：人多、有老师在场的地方你不把话挑明"
    # 当面挑衅类指令：indirect 立场的渲染件里一条都不许出现（q19 口径常驻化）
    FACE_TO_FACE_MARKS = ("起哄围观", "你可能借机加剧尴尬", "让受害者更加无地自容",
                          "当众反复点名受害者名字", "（不屑地笑）", "（翻了个白眼）")
    # 每格 stance 分支相对 neutral 的净增上限（字节）：#49 单例预算 5% ≈ 350–385 B，
    # 这里按**最紧的一格**再收一档，防止以后往 stance 分支里塞长段说明。
    STANCE_GROWTH_BUDGET_BYTES = 220

    @classmethod
    def setUpClass(cls):
        cls.scenarios = _load_scenarios()
        rm = {"bully": {"name": "陈浩", "role": "bully", "role_name": "霸凌者"}}

        def build(role, scenario, stance_override=None, decision=None, reaction_tendency=None):
            sc = scenario or {}
            role_map = {"victim": {"name": "思远", "role": "victim", "role_name": "受害者"},
                        "bully": {"name": "陈浩", "role": "bully", "role_name": "霸凌者"},
                        "bystander_a": {"name": "林雨桐", "role": "bystander_a", "role_name": "正义旁观者"},
                        "bystander_b": {"name": "赵磊", "role": "bystander_b", "role_name": "从众旁观者"}}
            cast = (sc.get("cast") or {}).get(role) or {}
            if cast:
                role_map[role] = dict(role_map[role], name=cast.get("name") or role_map[role]["name"],
                                      personality=cast.get("personality") or {})
            return PR.build_system_prompt(
                role, sc.get("level", 2), "（甲：你又在这儿磨蹭。）", sc.get("title", "课间"),
                sc.get("description", "明天要交作品"), role_map=role_map,
                round_num=3, bullying_intensity=0.62, victim_distress=0.48,
                decision=decision, reaction_tendency=reaction_tendency)

        cls.build = staticmethod(build)
        cls.stance = staticmethod(lambda sc, role: L.classify_persona(_cast_persona(sc, role))[1]
                                  if (sc.get("cast") or {}).get(role) else "neutral")

    # ---------- 锚点：neutral 分支就是原文，谁都不许顺手改 ----------
    def test_n1_neutral_entries_are_the_verbatim_originals(self):
        self.assertEqual(PR._VICTIM_TEACHER_TEXT["neutral"], self.TEACHER_ORIGINAL,
                         "受害者「回应老师」块的 neutral 分支被改写（#71 回归锚点）")
        self.assertEqual(PR._BULLY_SPOTLIGHT_TEXT["neutral"], self.BULLY_SPOTLIGHT_ORIGINAL,
                         "霸凌者聚光灯句的 neutral 分支被改写（#71 回归锚点）")

    def test_n2_no_persona_path_renders_verbatim_original_blocks(self):
        """人设根本不在场（无 cast、无自定义参与者）⇒ 两个块都必须逐字出现。"""
        v = self.build("victim", None)
        self.assertIn(self.TEACHER_ORIGINAL, v, "无 cast 受害者提示词里原文求助块丢了")
        self.assertNotIn("不会当场答应去找老师", v)
        b = self.build("bully", None)
        self.assertIn(self.BULLY_SPOTLIGHT_ORIGINAL, b, "无 cast 霸凌者提示词里原文聚光灯句丢了")
        for role in ROLES:
            self.assertNotIn(self.OCCASION_MARK, self.build(role, None),
                             f"{role} 无 cast（stance=neutral）却拿到了场合条")

    # ---------- F1：受害者求助权威轴 ----------
    def test_f1_firm_victim_teacher_block_swapped_soft_cells_kept(self):
        firm_cells = 0
        for i, sc in enumerate(self.scenarios):
            st = self.stance(sc, "victim")
            sp = self.build("victim", sc)
            if st == "firm":
                firm_cells += 1
                self.assertNotIn(self.TEACHER_ORIGINAL, sp,
                                 f"s{i+1} victim/firm 仍在教「老师安慰你→倾诉」（F1 回归）")
                self.assertIn("不会当场答应去找老师", sp,
                              f"s{i+1} victim/firm 丢了求助轴（F1 未落地）")
                # 仍在回应老师，不是改成「不理老师」
                self.assertIn("要根据老师的具体话语回应", sp, f"s{i+1} victim/firm 丢了回应老师这条")
            else:
                self.assertIn(self.TEACHER_ORIGINAL, sp,
                              f"s{i+1} victim/{st} 的求助块被无端改写（只允许 firm 变）")
        # 防空断言：8 情景里必须真有 firm 受害者格子，否则上面的分支等于没跑
        self.assertGreaterEqual(firm_cells, 1, "8 情景里没有 victim/firm 格子，F1 判据退化")

    def test_f1_only_firm_branch_exists(self):
        """soft/indirect 明确**不开**新分支（无实测缺陷就不扩大改动面）。"""
        self.assertEqual(sorted(PR._VICTIM_TEACHER_TEXT), ["firm", "neutral"],
                         "求助块出现了 firm/neutral 之外的立场键，需重新给理由")

    # ---------- F2：霸凌者当众/幕后 + 场合轴 ----------
    def test_f2_indirect_bully_has_no_face_to_face_spotlight(self):
        indirect_cells = 0
        for i, sc in enumerate(self.scenarios):
            st = self.stance(sc, "bully")
            sp = self.build("bully", sc)
            if st == "indirect":
                indirect_cells += 1
                self.assertNotIn(self.BULLY_SPOTLIGHT_ORIGINAL, sp,
                                 f"s{i+1} bully/indirect 仍在教当众加剧尴尬（F2b 回归）")
                for m in ("起哄围观", "你可能借机加剧尴尬", "让受害者更加无地自容"):
                    self.assertNotIn(m, sp, f"s{i+1} bully/indirect 残留当面指令：{m}")
                self.assertIn("你越不把话说破", sp, f"s{i+1} bully/indirect 丢了幕后版聚光灯句")
                self.assertIn(self.OCCASION_MARK, sp, f"s{i+1} bully/indirect 丢了场合条（F2a）")
            else:
                self.assertIn(self.BULLY_SPOTLIGHT_ORIGINAL, sp,
                              f"s{i+1} bully/{st} 的聚光灯句被无端改写（只允许 indirect 变）")
                self.assertNotIn(self.OCCASION_MARK, sp,
                                 f"s{i+1} bully/{st} 不该拿到场合条")
        self.assertGreaterEqual(indirect_cells, 1, "8 情景里没有 bully/indirect 格子，F2 判据退化")

    def test_f2_occasion_axis_is_bully_and_stance_gated(self):
        """任务#74（PM Major-1）：场合条为 **role=bully 且 stance=indirect** 双门控。

        原口径「任何角色只要人设判成 indirect 就给场合条」会把幕后操控者口吻塞给
        自定义受害者/旁观者。收紧后：
        ① bully|indirect 仍拿到场合条、无当面挑衅文案、发言硬约束不被挤掉；
        ② victim/bystander_a/bystander_b 即使被判成 indirect 也**不得**拿到场合条；
        ③ 所有角色都不许出现当面挑衅类指令。
        """
        indirect_persona = _cast_persona(self.scenarios[5], "bully")   # s6：当面客气/暗示/背后操控
        self.assertEqual(L.classify_persona(indirect_persona)[1], "indirect")
        for role in ROLES:
            sc = {"title": "放学路上", "description": "几名学生围住一名同学要钱，老师不在场。",
                  "level": 4, "cast": {role: {"name": "某某", "role_name": role,
                                              "personality": indirect_persona}}}
            sp = self.build(role, sc)
            for m in self.FACE_TO_FACE_MARKS:
                self.assertNotIn(m, sp, f"{role}/indirect 提示词里出现当面挑衅类文案：{m}")
            if role == "bully":
                self.assertIn(self.OCCASION_MARK, sp, "bully/indirect 没拿到场合条")
                self.assertTrue("本轮你一定开口" in sp or "本轮你必须开口" in sp,
                                "bully/indirect 的发言硬约束被场合条挤掉了")
                self.assertNotIn("你别说话", sp)
                self.assertNotIn("保持沉默", sp)
            else:
                self.assertNotIn(self.OCCASION_MARK, sp,
                                 f"{role}/indirect 不该拿到霸凌者的场合条（Major-1 双门控）")

    def test_f2_indirect_spotlight_has_no_silence_reading(self):
        """幕后版聚光灯句不许写成「不开口/不说话」：本轮由你发言是硬约束。"""
        line = PR._BULLY_SPOTLIGHT_TEXT["indirect"]
        for bad in ("不亲自开口", "不说话", "别开口", "沉默"):
            self.assertNotIn(bad, line, f"indirect 聚光灯句会被读成不发言：{bad}")

    def test_major2_decision_and_tendency_overlay_keeps_f1_f2_and_no_homogenization(self):
        """任务#74（PM Major-2）：生产同框叠加 `decision` + `reaction_tendency` 的回归。

        Rework71 其余用例只验证静态提示词；生产路径 `supervisor.py:555-569` 会同时传入
        decision 与 reaction_tendency。本用例覆盖两类变化格：
          · victim|firm（s8）—— F1 求助轴；
          · bully|indirect（s6）—— F2 聚光灯/场合轴。
        断言：
          ① 不出现「优先于其他行为建议 / 你只负责表达 / 不得自由发挥」这类收回创作权的措辞；
          ② stance 分支净增仍 ≤ 220 B（与既有预算口径同源，不因叠加 decision 而放大）；
          ③ 同一情景四个角色的渲染件不得逐字相同（不新增角色间同质化）。
        """
        cases = ((5, "bully", "low_intensity_taunt", "indirect"),
                 (7, "victim", "seek_help", "firm"))
        forbidden = ("优先于其他行为建议", "你只负责表达", "不得自由发挥")
        tables = (PR._VICTIM_TEACHER_TEXT, PR._BULLY_SPOTLIGHT_TEXT)

        for idx, role, tendency, expected_stance in cases:
            sc = self.scenarios[idx]
            self.assertEqual(self.stance(sc, role), expected_stance,
                             f"s{idx+1}/{role} 立场已变，叠加用例失焦")
            decision = {"reaction_tendency": tendency, "should_speak": True}
            sp = self.build(role, sc, decision=decision, reaction_tendency=tendency)
            for phrase in forbidden:
                self.assertNotIn(phrase, sp,
                                 f"s{idx+1}/{role} 叠加 decision 后出现收回创作权措辞：{phrase}")
            if role == "bully":
                self.assertIn("你越不把话说破", sp, "s6 bully/indirect 叠加后丢了幕后聚光灯句")
                self.assertIn(self.OCCASION_MARK, sp, "s6 bully/indirect 叠加后丢了场合条")
            else:
                self.assertIn("不会当场答应去找老师", sp, "s8 victim/firm 叠加后丢了求助轴")

            saved = [dict(t) for t in tables]
            try:
                for t in tables:
                    for k in list(t):
                        if k != "neutral":
                            t[k] = t["neutral"]
                base = self.build(role, sc, decision=decision, reaction_tendency=tendency)
            finally:
                for t, s in zip(tables, saved):
                    t.clear()
                    t.update(s)
            growth = len(sp.encode("utf-8")) - len(base.encode("utf-8"))
            self.assertGreater(abs(growth), 0,
                               f"s{idx+1}/{role} 叠加用例没有踩到 stance 分支，判据退化")
            self.assertLessEqual(abs(growth), self.STANCE_GROWTH_BUDGET_BYTES,
                                 f"s{idx+1}/{role}({expected_stance}) 叠加 decision 后 "
                                 f"stance 净增 {growth} B，超出 {self.STANCE_GROWTH_BUDGET_BYTES} B")

        # 同一情景、同一 decision 下，四个角色不得渲染成逐字相同的提示词
        for idx in (5, 7):
            decision = {"reaction_tendency": "respond", "should_speak": True}
            prompts = {role: self.build(role, self.scenarios[idx],
                                        decision=decision, reaction_tendency="respond")
                       for role in ROLES}
            self.assertEqual(len(set(prompts.values())), len(ROLES),
                             f"s{idx+1} 不同角色渲染件出现逐字相同（角色间同质化）")

    # ---------- 长度：stance 分支的净增必须可核查且不超预算 ----------
    def test_budget_stance_branch_growth_per_cell(self):
        """同一格「按立场渲染」减「强制 neutral 渲染」= 本轮 stance 分支的净增字节。

        做法：临时把两张表的非 neutral 分支替换成 neutral 原文，重渲染同一格，再还原。
        这样基线与实验组只差 stance 文案，不受情景数据长度变化干扰。
        """
        tables = (PR._VICTIM_TEACHER_TEXT, PR._BULLY_SPOTLIGHT_TEXT)
        saved = [dict(t) for t in tables]
        try:
            for t in tables:
                for k in list(t):
                    if k != "neutral":
                        t[k] = t["neutral"]
            base = {(sc["id"] if "id" in sc else i, role):
                    len(self.build(role, sc).encode("utf-8"))
                    for i, sc in enumerate(self.scenarios) for role in ROLES}
        finally:
            for t, s in zip(tables, saved):
                t.clear()
                t.update(s)
        worst = []
        for i, sc in enumerate(self.scenarios):
            for role in ROLES:
                n = len(self.build(role, sc).encode("utf-8"))
                d = n - base[(sc["id"] if "id" in sc else i, role)]
                if d:
                    worst.append((d, i + 1, role, self.stance(sc, role)))
        for d, sid, role, st in worst:
            self.assertLessEqual(abs(d), self.STANCE_GROWTH_BUDGET_BYTES,
                                 f"s{sid}/{role}({st}) stance 分支净增 {d} B，超出 {self.STANCE_GROWTH_BUDGET_BYTES} B")
        # 只有 firm 受害者与 indirect 霸凌者两类格子允许有增量
        self.assertEqual({(r, s) for _, _, r, s in worst},
                         {("victim", "firm"), ("bully", "indirect")},
                         f"出现了预期之外的变化格子：{sorted(worst)}")


if __name__ == "__main__":
    unittest.main()

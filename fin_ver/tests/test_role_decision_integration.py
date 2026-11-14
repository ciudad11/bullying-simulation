"""角色规则真实生成入口回归。无网络、无密钥读取、无用户 DB/日志写入。
运行：python -B <本文件绝对路径> -v
"""
import copy
import io
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import Mock, mock_open, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "web"))

from multi_agent.agents import AGENT_ROLE_MAP, create_agent, get_decision_messages
from importlib import import_module

evaluator_node = import_module("multi_agent.evaluator_node")
from multi_agent.graph_builder import MultiAgentRunner, StepRunner, set_llm_client
from multi_agent.llm_client import get_preset_response, PRESET_RESPONSES
from multi_agent.prompts import build_system_prompt
from multi_agent.state import Message, create_initial_state, message_to_display
from multi_agent.supervisor import _agent_logic, _supervisor_logic
from session_manager import SessionManager


def msg(sender, content="测试发言", round_no=1, emotions=None):
    return Message(sender, sender, sender, content, round=round_no,
                   emotions=emotions or [])


def state_for(messages=None, **values):
    state = create_initial_state()
    state.round = 1
    state.messages = list(messages or [])
    for key, value in values.items():
        setattr(state, key, value)
    return state


def true_cases():
    """真实规则的全部 True 标签，包含两个角色语义不同的 hesitate。"""
    attack = [msg("bully")]
    support = [msg("teacher")]
    mention = [msg("other", "思远，请回应")]
    return [
        ("bully", "provoke", [], {}),
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

    def test_false_zero_calls_memory_and_queue_then_no_reentry(self):
        state = state_for([msg("teacher")])
        state.speaking_queue = ["bystander_b", "victim"]
        state.agent_response = "上一位的回复"
        before = copy.deepcopy(state.agent_memories)
        client = FakeClient()
        agent = create_agent("bystander_b", None, False)
        with patch.object(agent, "decide", wraps=agent.decide) as decide, patch(
            "multi_agent.supervisor.create_agent", return_value=agent
        ):
            result = _agent_logic(state, "bystander_b", client)
            self.assertEqual(result["speaking_queue"], ["victim"])
            self.assertEqual(result["active_agent"], "victim")
            self.assertIsNone(result["agent_response"])
            self.assertNotIn("messages", result)
            self.assertNotIn("pending_messages", result)
            _agent_logic(state, "bystander_b", client)
            decide.assert_called_once_with(state)
        self.assertEqual(client.calls, [])
        self.assertEqual(state.agent_memories, before)
        self.assertEqual(len(state.messages), 1)
        self.assertEqual(state.pending_messages, [])
        self.assertEqual(len(state.decision_log), 1)
        self.assertFalse(state.decision_log[0]["rule_decision"]["should_speak"])
        self.assertEqual(state.decision_log[0]["generation_source"], "rule_silent")

    def test_all_true_branches_offline_and_prompt_constraints(self):
        for agent_id, tendency, messages, values in true_cases():
            with self.subTest(agent=agent_id, tendency=tendency):
                state = state_for(messages, **values)
                self.apply(state, agent_id)
                evidence = state.decision_log[0]
                self.assertTrue(evidence["rule_decision"]["should_speak"])
                self.assertEqual(evidence["reaction_tendency"], tendency)
                self.assertEqual(evidence["generation_source"], "preset_offline")
                self.assertTrue(evidence["produced_message"])
                self.assertEqual(state.messages[-1].content, get_preset_response(agent_id, 2, tendency))
                self.assertEqual(state.agent_memories[agent_id].speaking_count, 1)
                self.assertEqual(len(state.pending_messages), 1)
                client = FakeClient(get_preset_response(agent_id, 2, tendency))
                live = state_for(messages, **values)
                self.apply(live, agent_id, client)
                self.assertEqual(len(client.calls), 1)
                system = client.calls[0][0]["content"]
                self.assertIn(f"reaction_tendency={tendency}", system)
                self.assertIn("冲突时本轮规则优先", system)
                self.assertIn("只修饰措辞与表达", system)
                self.assertNotIn("你不需要回应", client.calls[0][1]["content"])
                self.assertEqual(live.decision_log[0]["generation_source"], "llm")
        # 开场 bully 的派生标签不改写原规则字段。
        opening = state_for()
        self.apply(opening, "bully")
        self.assertIsNone(opening.decision_log[0]["rule_decision"]["reaction_tendency"])
        self.assertEqual(opening.decision_log[0]["rule_decision"]["response"], "")

    def test_invalid_error_and_filtered_outputs_keep_tendency(self):
        bad_outputs = ["", "  ", None, "……", "...", "沉默", "（沉默）", "不发言。", "Silence",
                       "[LLM暂时不可用，请检查API配置]"]
        for agent_id, tendency, messages, values in true_cases():
            for bad in bad_outputs:
                with self.subTest(agent=agent_id, tendency=tendency, output=bad):
                    state = state_for(messages, **values)
                    self.apply(state, agent_id, FakeClient(bad))
                    self.assertEqual(state.messages[-1].content, get_preset_response(agent_id, 2, tendency))
                    self.assertEqual(state.decision_log[0]["generation_source"], "preset_invalid_response")
            for filtered in ["", "沉默", "[LLM暂时不可用，请检查API配置]"]:
                state = state_for(messages, **values)
                with patch("multi_agent.supervisor.filter_thinking_content", return_value=filtered):
                    self.apply(state, agent_id, FakeClient())
                self.assertEqual(state.decision_log[0]["generation_source"], "preset_filtered_response")
                self.assertEqual(state.messages[-1].content, get_preset_response(agent_id, 2, tendency))
            state = state_for(messages, **values)
            self.apply(state, agent_id, FakeClient(error=True))
            self.assertEqual(state.decision_log[0]["generation_source"], "preset_error")
            self.assertEqual(state.messages[-1].content, get_preset_response(agent_id, 2, tendency))

    def test_real_thinking_filter_empty_uses_selected_fallback(self):
        for response in ["<think>这是模型的内部分析</think>", "我需要扮演受害者"]:
            state = state_for([msg("bully")], victim_distress=.3, victim_safety=.5)
            self.apply(state, "victim", FakeClient(response))
            self.assertEqual(state.decision_log[0]["generation_source"], "preset_filtered_response")
            self.assertEqual(state.messages[-1].content, get_preset_response("victim", 2, "seek_help"))
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

    def test_next_candidate_sees_latest_message_and_one_evaluation_each(self):
        state = state_for()
        state.speaking_queue = ["bully", "victim", "bystander_a", "bystander_b"]
        runner = StepRunner(state, None, use_llm=False)
        runner._process_speaking_queue()
        self.assertEqual([d["agent_id"] for d in state.decision_log],
                         ["bully", "victim", "bystander_a", "bystander_b"])
        self.assertEqual(state.decision_log[1]["input_window"],
                         [{"message_index": 0, "sender_id": "bully", "round": 1}])
        self.assertTrue(state.decision_log[1]["produced_message"])
        self.assertEqual(state.pending_messages, state.messages)
        self.assertEqual(len({id(m) for m in state.pending_messages}), len(state.messages))
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

    def test_supervisor_sets_active_agent_in_place(self):
        state = state_for(round=0)
        state.active_agent = "stale"
        state.agent_response = "stale"
        result = _supervisor_logic(state)
        self.assertEqual(state.active_agent, result["active_agent"])
        self.assertEqual(state.active_agent, state.speaking_queue[0])
        self.assertIsNone(state.agent_response)
        self.assertEqual(state.decision_log, [])

    def test_window_current_previous_last_four_and_full_history_opening(self):
        state = state_for([msg("teacher", round_no=1)] +
                          [msg("other", str(i), round_no=2) for i in range(6)], round=2)
        self.assertEqual([m.content for m in get_decision_messages(state)], ["2", "3", "4", "5"])
        state.messages = [msg("bully", round_no=1)]
        state.round = 2
        self.assertFalse(create_agent("bully").decide(state).should_speak)
        state.round = 3
        self.random.return_value = .99
        decision = create_agent("bully").decide(state)
        self.assertFalse(decision.should_speak)
        self.assertIn("p_attack", decision.reasoning)
        for messages in [[], [msg("system", round_no=0)]]:
            opening = state_for(messages, round=5)
            self.assertTrue(create_agent("bully").decide(opening).should_speak)
        state.messages = [msg("teacher", round_no=1), msg("bystander_a", round_no=3)]
        self.assertTrue(create_agent("bystander_b").decide(state).should_speak)
        state.messages.append(msg("victim", round_no=1))
        self.assertTrue(create_agent("victim").decide(state).should_speak)

    def test_future_messages_are_not_current_context(self):
        state = state_for([msg("bully", round_no=3)], round=2)
        self.assertEqual(get_decision_messages(state), [])
        self.assertFalse(create_agent("victim").decide(state).should_speak)

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

    def test_victim_thresholds_and_cooldown_priority(self):
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
        state.messages.append(msg("victim"))
        state.messages.append(msg("bully"))
        self.assertFalse(create_agent("victim").decide(state).should_speak)

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
        state.messages.append(msg("bystander_a"))
        self.assertFalse(create_agent("bystander_a").decide(state).should_speak)
        state.round = 3
        state.messages.append(msg("bully", round_no=3))
        self.assertTrue(create_agent("bystander_a").decide(state).should_speak)

    def test_bystander_b_thresholds_and_false_tendencies(self):
        for support, risk, speak, expected in [(.4, 0, False, "resist_follow"),
                                             (.399, .5, False, "silent_resist"),
                                             (.399, .499, True, "follow_majority")]:
            state = state_for([msg("bully"), msg("bully")], bystander_support=support, retaliation_risk=risk)
            decision = create_agent("bystander_b").decide(state)
            self.assertEqual((decision.should_speak, decision.reaction_tendency), (speak, expected))
        for intensity, expected in [(.4, "support_justice"), (.4001, "hesitate")]:
            state = state_for([msg("bystander_a")], bullying_intensity=intensity)
            self.assertEqual(create_agent("bystander_b").decide(state).reaction_tendency, expected)
        state.messages.append(msg("bystander_b"))
        self.assertEqual(create_agent("bystander_b").decide(state).reaction_tendency, "wait")
        state.messages.append(msg("teacher"))
        self.assertEqual(create_agent("bystander_b").decide(state).reaction_tendency, "silence")

    def test_bully_probability_threshold_force_and_config_preserved(self):
        state = state_for([msg("teacher")], attack_frequency=0, persistence=0)
        self.random.reset_mock()
        self.assertFalse(create_agent("bully").decide(state).should_speak)
        self.random.assert_not_called()
        state.last_intervention_risk_level = "serious"
        self.assertTrue(create_agent("bully").decide(state).should_speak)
        self.random.assert_not_called()
        state.last_intervention_risk_level = "none"
        state.config = {"agent_decision": {"base": .35}}
        self.random.return_value = .35
        self.assertFalse(create_agent("bully").decide(state).should_speak)
        self.random.return_value = .3499
        self.assertTrue(create_agent("bully").decide(state).should_speak)
        state.config = {"agent_decision": {"base": .3499}}
        self.random.reset_mock()
        self.assertFalse(create_agent("bully").decide(state).should_speak)
        self.random.assert_not_called()

    def test_log_serialization_message_backcompat_and_only_candidates(self):
        state = state_for()
        self.apply(state, "bully")
        summary = state.to_summary_dict()
        self.assertEqual([d["agent_id"] for d in summary["decisions"]], ["bully"])
        evidence = state.messages[-1].decision
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

    def test_real_multi_runner_empty_rounds_evaluate_and_end(self):
        runner = MultiAgentRunner(use_llm=False)
        state = runner.state
        state.round = 1
        state.messages = [msg(role) for role in ["bully", "victim", "bystander_a", "bystander_b", "teacher"]]
        state.attack_frequency = state.attack_severity = state.persistence = .05
        state.bullying_intensity = .05
        state.immediate_stop_residual = 1
        runner._prev_msg_count = len(state.messages)
        self.random.return_value = .99
        for _ in range(7):
            before = len(state.messages)
            result = runner.run_round()
            self.assertEqual(len(state.messages), before)
            self.assertEqual(result["messages"], [])
            self.assertTrue(result["empty_round"])
            self.assertEqual(result["decisions"], runner.get_status()["decisions"])
            self.assertIsNone(state.active_agent)
            self.assertIsNone(state.agent_response)
            self.assertEqual(state.pending_messages, [])
            self.assertTrue(state.internal_state_history)
            if result["done"]:
                break
        self.assertTrue(state.finished)
        self.assertEqual(state.result_type, "success")
        self.assertEqual(runner.run_round()["messages"], [])

    def test_real_multi_runner_opening_and_intervention_after_empty(self):
        runner = MultiAgentRunner(use_llm=False)
        first = runner.run_round()
        self.assertGreater(len(first["messages"]), 0)
        self.assertEqual(len(first["decisions"]), len({d["agent_id"] for d in first["decisions"]}))
        # 强制随机不攻击，执行到自然空轮后仍能接收教师干预。
        self.random.return_value = .99
        for _ in range(5):
            second = runner.run_round()
            if second["empty_round"]:
                break
        self.assertTrue(second["empty_round"])
        self.assertFalse(second["done"])
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


if __name__ == "__main__":
    unittest.main()

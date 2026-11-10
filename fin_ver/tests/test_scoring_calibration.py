#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R15 评分校准测试

覆盖两类修复：
1. 无意义干预内容 → 确定性判 0 分（不调用 LLM，各维均为 0，明显不当档）；
2. 专家提示词锚点强化（0–10 无意义锚点 / 全区间评分 / 禁止中间化压分）。

运行：
    python tests/test_scoring_calibration.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from multi_agent.intervention_expert import (  # noqa: E402
    InterventionExpertAgent, detect_meaningless_intervention, _EXPERT_SYSTEM_PROMPT,
)
from multi_agent.state import create_initial_state  # noqa: E402
from multi_agent.evaluator_node import evaluate_intervention  # noqa: E402

RESULTS = []


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


class MockLLM:
    """无论被问到什么都返回 30 分左右的评分（模拟旧的偏宽评分）。"""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, temperature=0.2, max_tokens=2500):
        self.calls += 1
        dims = {}
        for k in ["D1", "D2", "D3", "D4", "D5", "D6", "D7"]:
            dims[k] = {"score": 30, "status": "scored", "band": "初步不足",
                       "evidence": ["测试"], "rationale": "测试", "confidence": 0.9}
        return json.dumps({"dimensions": dims,
                           "risk_assessment": {"flags": [], "evidence": "", "context": "", "confidence": 0.9},
                           "context_profile": {}, "strategy_profile": {}, "summary": "测试"},
                          ensure_ascii=False)


def make_state():
    st = create_initial_state(level=2, max_rounds=10, config={},
                               scenario={"title": "测试情境", "description": "测试描述"})
    st.session_id = "cal"
    return st


# ============================================================
# 1. 无意义内容护栏
# ============================================================
@test("R15 护栏：标点/表情/重复字符内容命中检测")
def t_detect_meaningless():
    cases = ["。。。", "？？？", "！！！。", "   ", "😀😀😀", "哈哈哈哈", "嗯嗯嗯", "555", "呵呵"]
    for c in cases:
        assert detect_meaningless_intervention(c) is not None, f"未命中：{c!r}"


@test("R15 护栏：有意义内容不得误伤")
def t_detect_not_meaningless():
    cases = [
        "停",                              # 单字但有明确制止功能
        "请立刻停止嘲笑。",                  # 正常干预
        "别这样",                            # 短但有内容
        "韩雪，这是一种言语欺凌，请立刻停止。",  # 完整干预
        "好，我们今天先谈到这里，明天我会再确认。",  # 含跟进的正常干预
    ]
    for c in cases:
        assert detect_meaningless_intervention(c) is None, f"误伤：{c!r}"


@test("R15 评分：无意义内容直接判 0 分，且不调用 LLM")
def t_meaningless_scores_zero():
    for content in ["。。。", "哈哈哈哈", "😀😀"]:
        mock = MockLLM()
        agent = InterventionExpertAgent(llm_client=mock)
        a = agent.assess(make_state(), content, "aid_m")
        assert a.status == "scored"
        assert a.final_quality_score_100 == 0.0, (content, a.final_quality_score_100)
        assert a.raw_mean_score_100 == 0.0
        assert a.meaningless is True, content
        assert a.meaningless_reason
        assert mock.calls == 0, f"无意义内容不应调用 LLM：{content}"
        # 各维均为 0（明显不当档）
        for d, item in a.dimensions.items():
            assert item.score == 0, (d, item.score)
        d1 = a.to_dict()
        assert d1["meaningless"] is True


@test("R15 评分：有意义内容照常调用 LLM（护栏不拦截）")
def t_meaningful_goes_to_llm():
    mock = MockLLM()
    agent = InterventionExpertAgent(llm_client=mock)
    a = agent.assess(make_state(), "停", "aid_n")
    assert mock.calls >= 1, "有意义内容应调用 LLM"
    assert a.meaningless is False
    assert a.final_quality_score_100 == 30.0, a.final_quality_score_100  # 走 LLM 评分


@test("R15 全链路：无意义干预 → 0 分、明确反馈、状态零变化")
def t_full_pipeline_meaningless():
    st = make_state()
    f_before = st.attack_frequency
    v_before = st.victim_distress
    res = evaluate_intervention(st, "哈哈哈哈", llm_client=MockLLM())
    assert res["quality_score"] == 0.0, res["quality_score"]
    assert res["assessment"]["meaningless"] is True
    assert any("无实际干预功能" in f for f in res["feedback"]), res["feedback"]
    # 无意义干预不得改善任何状态
    assert st.attack_frequency == f_before
    assert st.victim_distress == v_before
    # 干预历史记录 0 分
    assert st.intervention_quality_history[-1] == 0.0
    # 残余效应不产生
    assert st.immediate_stop_residual == 0.0


# ============================================================
# 2. 专家提示词锚点强化
# ============================================================
@test("R15 提示词：包含 0–10 无意义锚点与禁止同情分条款")
def t_prompt_anchors():
    assert "0–10 分" in _EXPERT_SYSTEM_PROMPT
    assert "同情分" in _EXPERT_SYSTEM_PROMPT
    assert "严禁把所有维度都压在 30–50" in _EXPERT_SYSTEM_PROMPT
    assert "宁低勿高" in _EXPERT_SYSTEM_PROMPT


@test("R15 提示词：明确「认真完整覆盖四要素应达 75–85」的校准条款")
def t_prompt_calibration():
    assert "75–85" in _EXPERT_SYSTEM_PROMPT
    assert "不得因「还可以更完善」" in _EXPERT_SYSTEM_PROMPT


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in RESULTS:
        try:
            fn()
            print(f"  [PASS] {name}")
            passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {name}: {e}")
            failed += 1
        except Exception as e:  # noqa
            print(f"  [ERROR] {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n=== 评分校准测试：{passed} 通过 / {failed} 失败 ===")
    sys.exit(1 if failed else 0)

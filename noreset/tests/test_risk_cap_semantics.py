# -*- coding: utf-8 -*-
"""封顶语义回归测试（新增）+ 复跑既有测试。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from multi_agent.intervention_expert import apply_risk_cap, determine_risk_level
from multi_agent.state import create_initial_state
from multi_agent.evaluator_node import evaluate_intervention


class MockLLM:
    def __init__(self, payload):
        self.payload = payload

    def chat(self, messages, temperature=0.2, max_tokens=2500):
        return self.payload


def payload(scores, flags):
    dims = {}
    for k in ["D1", "D2", "D3", "D4", "D5", "D6", "D7"]:
        v = scores.get(k)
        dims[k] = ({"score": None, "status": "N/O", "band": "N/O", "evidence": [],
                    "rationale": "无观察机会", "confidence": 0.9} if v is None else
                   {"score": v, "status": "scored", "band": "基本达标", "evidence": ["证据"],
                    "rationale": "理由", "confidence": 0.9})
    return json.dumps({"dimensions": dims,
                       "risk_assessment": {"flags": flags, "evidence": "原文", "context": "语境", "confidence": 0.9},
                       "context_profile": {}, "strategy_profile": {}, "summary": "测试"},
                      ensure_ascii=False)


fails = []


def check(name, cond, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + ("" if cond else f"  {extra}"))
    if not cond:
        fails.append(name)


print("R13 封顶语义回归")
# 1) 原始均分 72.5 + serious → 封顶生效
final, capped = apply_risk_cap(72.5, "serious")
check("原始均分 72.5 且严重风险 → 封顶到 60 且 cap_applied=True", final == 60 and capped is True, (final, capped))

# 2) 原始均分 47.1 + serious → 不得声称封顶（未产生扣分）
final2, capped2 = apply_risk_cap(47.1, "serious")
check("原始均分 47.1 且严重风险 → 分数不变且 cap_applied=False", final2 == 47.1 and capped2 is False, (final2, capped2))

# 3) 反馈文案：不再误报「总分封顶 60」
st = create_initial_state(level=2, max_rounds=10, config={}, scenario={"title": "t", "description": "d"})
st.session_id = "cap"
res = evaluate_intervention(st, "你也不是故意的呀，都是同学之间闹着玩。",
                            llm_client=MockLLM(payload({"D1": 50, "D2": 45, "D3": 40, "D4": 45},
                                                      ["R_MINIMIZE_HARM", "R_PROMISE_NO_ACTION"])))
fb = " ".join(res["feedback"])
check("反馈不再出现『总分封顶 60』的误报", "封顶 60" not in fb, fb)
check("反馈明确说明『封顶未额外扣分』", "封顶未额外扣分" in fb, fb)
check("最终分等于原始均分", abs(res["quality_score"] - 45.0) < 1e-6, res["quality_score"])
check("assessment 记录 cap_applied=False", res["assessment"]["risk_cap_applied"] is False)

# 4) 原始均分 > 60 且严重风险 → 仍应封顶并如实提示
st2 = create_initial_state(level=2, max_rounds=10, config={}, scenario={"title": "t", "description": "d"})
st2.session_id = "cap2"
res2 = evaluate_intervention(st2, "立刻停止嘲笑，并检查受害者安全。",
                             llm_client=MockLLM(payload({"D1": 90, "D2": 85, "D3": 80, "D4": 90},
                                                       ["R_EXPOSURE_RETALIATION"])))
fb2 = " ".join(res2["feedback"])
check("高均分+严重风险仍封顶到 60", abs(res2["quality_score"] - 60) < 1e-6, res2["quality_score"])
check("封顶时反馈如实提示", "封顶 60" in fb2, fb2)

print(f"\n=== 封顶语义测试：{'全部通过' if not fails else str(len(fails)) + ' 项失败'} ===")
sys.exit(1 if fails else 0)

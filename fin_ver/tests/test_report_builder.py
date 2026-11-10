#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R19 教师实训评估报告测试

覆盖：
1. 八大板块齐全（总体结论/能力画像/失分归因/误区对照/训练路线/话术/自检/评分速查）；
2. 总体结论含等级、趋势、距达标差距、规则说明（澄清「规则是否太严」）；
3. 能力画像七维齐全，N/O 维度不计入均分；
4. 失分归因分类与占比；
5. 误区对照取自真实原话；
6. 逐轮分析增强字段（全维明细、证据、学生反应、分条动作、示范话术、逐项对照）；
7. 报告接口返回 training_report。

运行：
    python tests/test_report_builder.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "web"))

from multi_agent.report_builder import build_training_report  # noqa: E402
from multi_agent.coaching import build_round_analysis  # noqa: E402

RESULTS = []


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


MSGS = [
    {"role": "bully", "sender": "陈浩", "content": "你们看这个废物真蠢", "round": 1},
    {"role": "victim", "sender": "思远", "content": "我……我不说了", "round": 1},
    {"role": "bystander_a", "sender": "小宇", "content": "哈哈哈", "round": 1},
]

ASSESSMENT = {
    "assessment_id": "a1",
    "status": "scored",
    "raw_mean_score_100": 41.7,
    "final_quality_score_100": 41.7,
    "risk_cap_applied": False,
    "risk_level": "serious",
    "rated_dimension_count": 7,
    "no_observable_count": 0,
    "dimensions": {
        "D1": {"score": 35, "status": "scored", "band": "明显不当",
               "evidence": ["你们看这个废物真蠢"], "rationale": "未识别核心欺凌，只处理旁支", "confidence": 0.8},
        "D2": {"score": 30, "status": "scored", "band": "明显不当",
               "evidence": ["我……我不说了"], "rationale": "未做安全确认", "confidence": 0.7},
        "D3": {"score": 20, "status": "scored", "band": "明显不当",
               "evidence": ["你愿意原谅大壮吗"], "rationale": "把决定权交给受害者", "confidence": 0.8},
        "D4": {"score": 40, "status": "scored", "band": "初步不足",
               "evidence": ["你们一起沟通下"], "rationale": "促成当面对质", "confidence": 0.7},
        "D5": {"score": 35, "status": "scored", "band": "明显不当",
               "evidence": ["哈哈哈"], "rationale": "未处理起哄", "confidence": 0.6},
        "D6": {"score": 15, "status": "scored", "band": "明显不当",
               "evidence": ["我会处理"], "rationale": "无任何跟进安排", "confidence": 0.9},
        "D7": {"score": 40, "status": "scored", "band": "初步不足",
               "evidence": ["多交流"], "rationale": "与上一轮表述重复", "confidence": 0.6},
    },
    "risk_assessment": {
        "level": "serious",
        "flags": ["R_FORCED_CONFRONTATION"],
        "flags_detail": [{
            "code": "R_FORCED_CONFRONTATION",
            "evidence": ["你们一起沟通下", "达成个约定"],
            "context": "在未评估安全时促成双方碰面",
            "confidence": 0.8,
        }],
    },
    "context_profile": {},
    "strategy_profile": {},
    "summary": "测试",
}


def make_intervention(score=41.7):
    return {
        "round": 1,
        "content": "小宇你愿意原谅大壮吗？你们一起沟通下达成个约定，我会处理。",
        "quality_score": score,
        "assessment": ASSESSMENT,
    }


def build_sample_analyses():
    a1 = build_round_analysis(1, MSGS, make_intervention(), 60)
    a2 = build_round_analysis(2, MSGS[:1], None, 60)   # 未干预轮次
    return [a1, a2]


# ============================================================
@test("R19 八大板块齐全且内容非空")
def t_sections():
    tr = build_training_report(build_sample_analyses(), 60)
    for key in ("executive_summary", "capability_profile", "failure_attribution",
                "pitfalls", "training_roadmap", "scripts", "self_check", "rubric_explainer"):
        assert key in tr, f"缺少板块: {key}"
        assert tr[key], f"板块为空: {key}"
    assert len(tr["capability_profile"]["dimensions"]) == 7
    assert len(tr["training_roadmap"]["stages"]) == 3
    assert len(tr["scripts"]["scripts"]) >= 5
    assert len(tr["self_check"]["items"]) >= 10
    assert len(tr["rubric_explainer"]["bands"]) == 4
    assert len(tr["rubric_explainer"]["risk_codes"]) == 5


@test("R19 总体结论含等级/趋势/差距/规则说明（澄清规则是否过严）")
def t_executive():
    tr = build_training_report(build_sample_analyses(), 60)
    es = tr["executive_summary"]
    assert es["level"] in ("优秀", "基本达标", "需提升", "需大幅提升"), es["level"]
    assert es["headline"], es
    assert es["trend"]["direction"] in ("上升", "下降", "持平", "无数据")
    assert es["gap_to_pass"] is not None
    # 风险等级 serious 但 cap_applied=False → 必须说明「未额外扣分」
    assert "未额外扣分" in es["rules_note"] or "未触发" in es["rules_note"], es["rules_note"]
    assert es["top_actions"], es


@test("R19 能力画像：均分/达标率/趋势，N/O 维度不计入")
def t_profile():
    tr = build_training_report(build_sample_analyses(), 60)
    dims = tr["capability_profile"]["dimensions"]
    d6 = next(d for d in dims if d["dim"] == "D6")
    assert d6["avg"] == 15.0, d6
    assert d6["pass_rate"] == 0.0
    assert d6["level"] == "明显不当", d6
    assert tr["capability_profile"]["weakest"]["dim"] == "D6"


@test("R19 失分归因：分类、占比、明细与改法")
def t_attribution():
    tr = build_training_report(build_sample_analyses(), 60)
    fa = tr["failure_attribution"]
    cats = {c["category"] for c in fa["categories"]}
    assert "要素缺失" in cats, cats
    assert "风险操作" in cats, cats
    assert "节奏与时机" in cats, cats   # 第 2 轮未干预
    for c in fa["categories"]:
        assert c["items"], c["category"]
        assert "fix" in c["items"][0]
    assert fa["summary"]


@test("R19 误区对照取自真实原话并给出改法")
def t_pitfalls():
    tr = build_training_report(build_sample_analyses(), 60)
    items = tr["pitfalls"]["items"]
    assert items, tr["pitfalls"]["summary"]
    joined = " ".join(i["excerpt"] for i in items)
    assert "一起沟通" in joined, joined
    assert any(i["kind"] == "risk" for i in items)
    assert any(i.get("fix") for i in items)


@test("R19 逐轮分析增强字段")
def t_round_analysis_fields():
    a = build_sample_analyses()[0]
    assert a["dimension_detail"] and len(a["dimension_detail"]) == 7
    d6 = next(d for d in a["dimension_detail"] if d["dim"] == "D6")
    assert d6["score"] == 15 and d6["is_weak"] is True
    assert d6["evidence"], d6
    assert a["student_reactions"], a["student_reactions"]
    assert a["improvement_actions"], a
    assert a["teacher_alignment"] and len(a["teacher_alignment"]) == 7
    assert a["model_answer"]["elements"], a["model_answer"]
    assert a["risk_flags"][0]["evidence"], a["risk_flags"]
    assert a["risk_level"] == "serious"
    assert a["risk_cap_applied"] is False


@test("R19 报告接口返回 training_report")
def t_api_report():
    import uuid
    from fastapi.testclient import TestClient
    import database
    database.init_db()
    import app as webapp

    client = TestClient(webapp.app)
    uname = f"rpt_{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register",
                    json={"username": uname, "password": "pass1234", "display_name": "T"})
    token = r.json().get("token", "")
    r = client.post("/api/session",
                    json={"level": 2, "max_rounds": 2, "use_llm": False,
                          "participant_ids": ["bully", "victim", "bystander_a", "bystander_b"],
                          "participants_data": []},
                    headers={"Authorization": f"Bearer {token}"})
    sid = r.json().get("session_id", "")
    assert sid, r.text
    client.post(f"/api/session/{sid}/run", json={}, headers={"Authorization": f"Bearer {token}"})
    r = client.get(f"/api/session/{sid}/report", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert "training_report" in rep, "报告缺少 training_report"
    tr = rep["training_report"]
    for key in ("executive_summary", "capability_profile", "failure_attribution",
                "pitfalls", "training_roadmap", "scripts", "self_check", "rubric_explainer"):
        assert key in tr, f"缺少板块: {key}"


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
    print(f"\n=== 报告构建测试：{passed} 通过 / {failed} 失败 ===")
    sys.exit(1 if failed else 0)

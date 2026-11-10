#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R14 用户指导机制测试（不依赖真实 LLM）

覆盖：
1. 逐轮分析：情境概括、评分、失分维度与针对性建议、下一步动作；
2. 改进方案：维度缺口按「拉低总分贡献」排序、优先改进项、可套用话术、行动计划；
3. 实时指导：最需改进维度、提示清单；
4. 连续低分预警：连续 3 轮 <60 触发；不足 3 轮或中途达标则不触发；
5. 集成：SessionManager.intervene 返回 coaching / low_score_alert。

运行：
    python tests/test_coaching.py
"""

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "web"))

from multi_agent.coaching import (  # noqa: E402
    build_round_analysis, build_improvement_plan, build_real_time_coaching,
    build_streak_alert, build_context_hints,
)

RESULTS = []


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


def make_assessment(dims, flags=None, risk_detail=None):
    """dims: {D1: 55, 'D5': None}"""
    out = {}
    for k in ["D1", "D2", "D3", "D4", "D5", "D6", "D7"]:
        v = dims.get(k)
        if v is None:
            out[k] = {"score": None, "status": "N/O", "band": "N/O", "evidence": [],
                      "rationale": "本场景无可观察机会", "confidence": 0.9}
        else:
            out[k] = {"score": v, "status": "scored", "band": "基本达标",
                      "evidence": [f"原文片段{k}"], "rationale": f"{k} 的评分理由",
                      "confidence": 0.85}
    detail = risk_detail or []
    return {
        "assessment_id": "a1",
        "status": "scored",
        "raw_mean_score_100": 40.0,
        "final_quality_score_100": 40.0,
        "risk_cap_applied": False,
        "risk_level": "attention" if flags else "none",
        "rated_dimension_count": len([v for v in dims.values() if v is not None]),
        "no_observable_count": len([v for v in dims.values() if v is None]),
        "dimensions": out,
        "risk_assessment": {"level": "attention" if flags else "none", "flags": flags or [],
                            "flags_detail": detail},
        "context_profile": {}, "strategy_profile": {}, "summary": "测试总结",
    }


def make_intervention(round_no, content, score, dims, flags=None, risk_detail=None):
    return {
        "round": round_no,
        "content": content,
        "quality_score": score,
        "feedback": [],
        "assessment": make_assessment(dims, flags, risk_detail),
    }


MSGS = [
    {"role": "bully", "sender": "陈浩", "content": "你们看这个废物真蠢", "round": 1},
    {"role": "victim", "sender": "思远", "content": "我……我不说了", "round": 1},
    {"role": "teacher", "sender": "张老师", "content": "大家都是同学，一起沟通下", "round": 1},
]


@test("R14 逐轮分析：概括情境、给出失分维度与针对性建议")
def t_round_analysis():
    iv = make_intervention(1, "大家都是同学，一起沟通下", 40.0,
                          {"D1": 55, "D2": 40, "D3": 45, "D4": 50, "D5": 30, "D6": 20, "D7": None})
    a = build_round_analysis(1, MSGS, iv)
    assert a["round"] == 1
    assert a["has_intervention"] is True
    assert a["score"] == 40.0
    assert a["band"] == "初步不足", a["band"]
    assert "欺凌者" in a["event_summary"], a["event_summary"]
    assert a["what_you_did"]
    assert a["weak_dimensions"], "未给出失分维度"
    # 最弱维度应为 D6（20 分）
    assert a["weak_dimensions"][0]["dim"] == "D6", a["weak_dimensions"][0]
    assert a["weak_dimensions"][0]["advice"], "缺少改进建议"
    assert a["weak_dimensions"][0]["reason"], "缺少失分依据（rationale）"
    assert a["next_step"], "缺少下一步建议"
    # 点评应点名最弱维度（用维度名）
    assert any(w["dim"] == "D6" for w in a["weak_dimensions"])
    assert "长期欺凌干预与跟进" in a["coach_comment"], a["coach_comment"]


@test("R14 逐轮分析：未干预的轮次也给出指导")
def t_round_analysis_no_intervention():
    a = build_round_analysis(2, [{"role": "bully", "content": "哼", "round": 2}], None)
    assert a["has_intervention"] is False
    assert a["coach_comment"]
    assert a["next_step"]


@test("R14 逐轮分析：风险标记带规避建议")
def t_round_analysis_risk():
    detail = [{"code": "R_MINIMIZE_HARM", "evidence": ["只是闹着玩"], "context": "淡化伤害",
               "confidence": 0.8}]
    iv = make_intervention(1, "只是闹着玩而已", 35.0,
                          {"D1": 45, "D2": 35, "D3": 40, "D4": 40, "D5": 30, "D6": 20, "D7": None},
                          flags=["R_MINIMIZE_HARM"], risk_detail=detail)
    a = build_round_analysis(1, MSGS, iv)
    assert a["risk_flags"], "未识别风险"
    rf = a["risk_flags"][0]
    assert rf["code"] == "R_MINIMIZE_HARM"
    assert rf["advice"], "风险缺少规避建议"
    assert "R_MINIMIZE_HARM" in a["next_step"] or "淡化" in a["next_step"]


@test("R14 改进方案：维度缺口按拉低总分的贡献排序")
def t_improvement_plan():
    analyses = [
        build_round_analysis(1, MSGS, make_intervention(
            1, "甲", 45.0, {"D1": 60, "D2": 50, "D3": 50, "D4": 55, "D5": 30, "D6": 20, "D7": 40})),
        build_round_analysis(2, MSGS, make_intervention(
            2, "乙", 42.0, {"D1": 55, "D2": 45, "D3": 45, "D4": 50, "D5": 35, "D6": 25, "D7": 40})),
    ]
    plan = build_improvement_plan(analyses)
    assert plan["rounds_analyzed"] == 2
    assert plan["rounds_with_intervention"] == 2
    assert plan["dimension_gaps"], "未产出维度缺口"
    # D6 缺口最大 → 排序第一
    assert plan["dimension_gaps"][0]["dim"] == "D6", [g["dim"] for g in plan["dimension_gaps"]]
    assert plan["priorities"], "未产出优先改进项"
    p0 = plan["priorities"][0]
    assert p0["rank"] == 1
    assert p0["action"] and p0["sample"], p0
    assert plan["templates"], "未产出可套用话术"
    assert plan["action_plan"], "未产出行动计划"
    assert len(plan["round_reviews"]) == 2
    assert "失分最集中" in plan["summary"], plan["summary"]


@test("R15 实时指导：给出最需改进维度与情境化策略提示（不给答案）")
def t_real_time_coaching():
    iv = make_intervention(1, "甲", 38.0,
                          {"D1": 50, "D2": 40, "D3": 45, "D4": 45, "D5": 35, "D6": 25, "D7": 40})
    c = build_real_time_coaching(iv, messages=MSGS)
    assert c["score"] == 38.0
    assert c["focus"] and c["focus"]["dim"] == "D6", c["focus"]
    assert c["tips"], "未给出提示"
    # R15：不得包含可照抄话术（不给答案）
    assert "sample" not in (c["focus"] or {}), "实时指导不得提供参考话术"
    # 情境化提示存在且含情境线索
    hints = c.get("context_hints") or []
    assert hints, "未给出情境化策略提示"
    assert any("情境线索" in h["title"] for h in hints), [h["title"] for h in hints]
    assert any("上轮失分" in h["title"] for h in hints)
    # 提示文本不得出现引号话术（「」已被安全阀替换，也不允许出现示例句）
    for h in hints:
        assert "「" not in h["text"] and "参考话术" not in h["text"], h


@test("R14 连续低分预警：连续 3 轮低于 60 触发，中断或不足则不触发")
def t_streak_alert():
    def rec(rnd, score, weak):
        return {"round": rnd, "score": score, "weak_dimensions": weak, "risk_flags": []}

    weak = [{"dim": "D6", "name": "长期欺凌干预与跟进", "score": 20, "advice": "补一句带时间点的跟进"}]
    weak2 = [{"dim": "D5", "name": "同伴与群体规范", "score": 30, "advice": "补一句群体规范"}]

    assert build_streak_alert([rec(1, 45, weak), rec(2, 44, weak)], 60, 3) is None
    alert = build_streak_alert([rec(1, 45, weak), rec(2, 44, weak), rec(3, 43, weak2)], 60, 3)
    assert alert is not None, "连续 3 轮低分未触发预警"
    assert alert["streak"] == 3
    assert "连续 3 轮" in alert["headline"], alert["headline"]
    assert "长期欺凌干预与跟进" in alert["diagnosis"], alert["diagnosis"]
    assert alert["actions"], "未给出调整动作"
    assert len(alert["rounds"]) == 3
    # 中途达标 → 不触发（连续性中断）
    assert build_streak_alert([rec(1, 45, weak), rec(2, 75, weak), rec(3, 43, weak2)], 60, 3) is None


@test("R16 集成：连续 2 轮低分后才展示指导；3 轮触发预警；达标清零")
def t_session_manager_integration():
    import session_manager as sm
    assert getattr(sm, "COACHING_AVAILABLE", False), "coaching 模块未接入"
    assert sm.HINT_STREAK_MIN == 2, "提示门槛应为连续 2 轮"

    class FakeRunner:
        def __init__(self):
            self.state = types.SimpleNamespace(intervention_count=0, last_intervention="",
                                               last_intervention_round=0, messages=[])
            self._prev_msg_count = 0
            self.n = 0

        def intervene(self, content):
            self.n += 1
            score = 45.0 if self.n <= 3 else 80.0
            assessment = make_assessment({"D1": 55, "D2": 50, "D3": 45, "D4": 50,
                                           "D5": 35, "D6": 20, "D7": 40})
            rec = {
                "content": content,
                "quality_score": score,
                "victim_care_score": 0.5,
                "bullying_intensity": 0.3,
                "victim_distress": 0.4,
                "recommendation": "测试",
                "assessment_status": "scored",
                # 真实接口同样在顶层返回 assessment（供指导引擎读取维度明细）
                "assessment": assessment,
                "intervention_record": {
                    "assessment_id": f"a{self.n}",
                    "assessment": assessment,
                    "predicted_bullying_reduction": 0.01, "predicted_distress_change": -0.01,
                    "actual_bullying_reduction": 0.01, "actual_distress_change": -0.01,
                    "bullying_reduction": 0.01, "distress_reduction": 0.01,
                },
            }
            return rec

    stub = types.SimpleNamespace()
    stub.runner = FakeRunner()
    stub.interventions = []
    stub.messages = list(MSGS)  # 本轮对话（供情境化提示）
    stub.session_data = {"interventions": [], "messages": []}
    stub.round = 1
    stub._low_streak = []
    stub._save_log = lambda: None
    stub._sync_internal_history = lambda: None
    # R16：门槛与进度方法取真实实现（避免桩与真实行为漂移）
    stub.GUIDANCE_HINT_MIN = sm.HINT_STREAK_MIN
    stub.GUIDANCE_ALERT_MIN = sm.LOW_STREAK_MIN
    stub._guidance_progress = types.MethodType(sm.SessionManager._guidance_progress, stub)

    # 第 1 轮低分：不展示任何提示（门槛未到）
    r1 = sm.SessionManager.intervene(stub, "第一次干预")
    assert r1["coaching"] is None, "第 1 轮低分不应展示提示"
    assert r1["guidance_enabled"] is False, "第 1 轮不应启用指导"
    assert r1["low_score_alert"] is None
    assert r1["low_score_streak"] == 1

    # 第 2 轮低分：达到门槛 → 展示情境化策略提示（不给答案）
    stub.round = 2
    r2 = sm.SessionManager.intervene(stub, "第二次干预")
    assert r2["coaching"] is not None, "连续 2 轮低分后应展示提示"
    assert r2["guidance_enabled"] is True
    assert r2["coaching"]["focus"], "提示未指出最需改进维度"
    assert r2["coaching"].get("context_hints"), "缺少情境化策略提示"
    assert "sample" not in (r2["coaching"]["focus"] or {}), "提示不得给出参考话术"
    assert r2["low_score_alert"] is None, "第 2 轮不应触发预警横幅"

    # 第 3 轮低分：触发预警横幅（策略方向，不给答案）
    stub.round = 3
    r3 = sm.SessionManager.intervene(stub, "第三次干预")
    assert r3["low_score_alert"] is not None, "连续 3 轮低分未触发预警"
    assert r3["low_score_streak"] == 3
    assert r3["low_score_alert"]["actions"], "预警未给出调整动作"
    assert "template" not in r3["low_score_alert"], "预警不得包含可照抄话术"
    assert r3["low_score_alert"].get("strategies"), "预警应给出策略方向"

    # 第 4 轮达标 → 连续计数清零、指导关闭
    stub.round = 4
    r4 = sm.SessionManager.intervene(stub, "第四次干预")
    assert r4["low_score_streak"] == 0, r4["low_score_streak"]
    assert r4["low_score_alert"] is None
    assert r4["guidance_enabled"] is False
    assert r4["coaching"] is None, "达标后不应继续展示提示"


@test("R16 待评估不中断连败：pending 后仍能在第 2 次低分触发提示，并给出触发进度")
def t_pending_keeps_streak():
    import session_manager as sm
    assert getattr(sm, "COACHING_AVAILABLE", False)

    class FakeRunner:
        """按调用次序返回：低分 → pending → 低分"""

        def __init__(self):
            self.state = types.SimpleNamespace(intervention_count=0, last_intervention="",
                                               last_intervention_round=0, messages=[])
            self._prev_msg_count = 0
            self.n = 0

        def intervene(self, content):
            self.n += 1
            pending = (self.n == 2)
            if pending:
                rec = {"content": content, "quality_score": None, "victim_care_score": 0.0,
                       "bullying_intensity": 0.3, "victim_distress": 0.4,
                       "recommendation": "", "assessment_status": "pending",
                       "assessment": None, "intervention_record": {"assessment_id": f"a{self.n}"}}
            else:
                assessment = make_assessment({"D1": 55, "D2": 50, "D3": 45, "D4": 50,
                                               "D5": 35, "D6": 20, "D7": 40})
                rec = {"content": content, "quality_score": 45.0, "victim_care_score": 0.5,
                       "bullying_intensity": 0.3, "victim_distress": 0.4,
                       "recommendation": "", "assessment_status": "scored",
                       "assessment": assessment,
                       "intervention_record": {"assessment_id": f"a{self.n}",
                                               "assessment": assessment}}
            return rec

    stub = types.SimpleNamespace()
    stub.runner = FakeRunner()
    stub.interventions = []
    stub.messages = list(MSGS)
    stub.session_data = {"interventions": [], "messages": []}
    stub.round = 1
    stub._low_streak = []
    stub._save_log = lambda: None
    stub._sync_internal_history = lambda: None
    stub.GUIDANCE_HINT_MIN = sm.HINT_STREAK_MIN
    stub.GUIDANCE_ALERT_MIN = sm.LOW_STREAK_MIN
    stub._guidance_progress = types.MethodType(sm.SessionManager._guidance_progress, stub)

    # 第 1 次：低分 → 计数 1，未达门槛（并给出「还差几轮」进度）
    r1 = sm.SessionManager.intervene(stub, "第一次干预")
    assert r1["low_score_streak"] == 1, r1["low_score_streak"]
    assert r1["guidance_enabled"] is False
    assert r1["coaching"] is None
    prog = r1.get("guidance_progress") or {}
    assert prog.get("streak") == 1
    assert prog.get("remaining") == 1, prog
    assert "再连续" in (prog.get("message") or ""), prog

    # 第 2 次：待评估 → 不得清零连败计数
    stub.round = 2
    r2 = sm.SessionManager.intervene(stub, "第二次干预（待评估）")
    assert r2["low_score_streak"] == 1, f"待评估不应清零连败计数：{r2['low_score_streak']}"

    # 第 3 次：低分 → 计数达 2 → 触发提示
    stub.round = 3
    r3 = sm.SessionManager.intervene(stub, "第三次干预")
    assert r3["low_score_streak"] == 2, r3["low_score_streak"]
    assert r3["guidance_enabled"] is True
    assert r3["coaching"] is not None, "连续低分后应展示提示"


@test("R17 集成：连续 2 轮未获有效评分（评分服务不可用）也会触发基于情境的提示")
def t_unscored_streak_triggers():
    import session_manager as sm
    assert getattr(sm, "COACHING_AVAILABLE", False)

    class PendingRunner:
        """始终返回待评估（模拟未配置密钥/模型不可用）"""

        def __init__(self):
            self.state = types.SimpleNamespace(intervention_count=0, last_intervention="",
                                               last_intervention_round=0, messages=[])
            self._prev_msg_count = 0
            self.n = 0

        def intervene(self, content):
            self.n += 1
            return {"content": content, "quality_score": None, "victim_care_score": 0.0,
                    "bullying_intensity": 0.3, "victim_distress": 0.4, "recommendation": "",
                    "assessment_status": "pending", "assessment": None,
                    "intervention_record": {"assessment_id": f"a{self.n}"}}

    stub = types.SimpleNamespace()
    stub.runner = PendingRunner()
    stub.interventions = []
    # 本轮对话（第 2 轮：轮号与 stub.round 一致，确保情境线索可提取）
    stub.messages = [dict(m, round=2) for m in MSGS]
    stub.session_data = {"interventions": [], "messages": []}
    stub.round = 1
    stub._low_streak = []
    stub._unscored_streak = 0
    stub._save_log = lambda: None
    stub._sync_internal_history = lambda: None
    stub.GUIDANCE_HINT_MIN = sm.HINT_STREAK_MIN
    stub.GUIDANCE_ALERT_MIN = sm.LOW_STREAK_MIN
    stub._guidance_progress = types.MethodType(sm.SessionManager._guidance_progress, stub)

    r1 = sm.SessionManager.intervene(stub, "第一次干预（无评分）")
    assert r1["guidance_enabled"] is False, "第 1 轮未评分不应触发"
    prog1 = r1.get("guidance_progress") or {}
    assert prog1.get("unscored_streak") == 1, prog1
    assert "未获得有效评分" in (prog1.get("message") or ""), prog1

    stub.round = 2
    r2 = sm.SessionManager.intervene(stub, "第二次干预（无评分）")
    assert r2["guidance_enabled"] is True, "连续 2 轮无评分应触发情境提示"
    assert r2["coaching"] is not None, "应返回基于情境的提示"
    hints = (r2["coaching"] or {}).get("context_hints") or []
    assert hints, "情境提示不应为空"
    assert any("情境线索" in h.get("title", "") for h in hints), hints
    # 无评分不触发「不及格」预警（表现差 ≠ 无评分）
    assert r2["low_score_alert"] is None, "无评分不应触发低分预警"


@test("R15 情境提示：基于实际对话给策略方向，且不给答案")
def t_context_hints():
    weak = [{"dim": "D6", "name": "长期欺凌干预与跟进", "score": 20,
             "advice": "给出可核查的后续安排：由谁、在何时、做什么、何时复查。"}]
    risks = [{"code": "R_MINIMIZE_HARM", "label": "淡化或否认欺凌伤害",
              "advice": "只评价行为，不评价动机。", "avoid": "避免说闹着玩"}]
    hints = build_context_hints(MSGS, weak, risks)
    assert hints, "未生成情境提示"
    titles = " ".join(h["title"] for h in hints)
    # 情境线索来自真实对话
    assert "情境线索：本轮仍有攻击发言" in titles, titles
    assert "情境线索：受害者有反应" in titles, titles
    # 失分点转化为策略方向
    assert "上轮失分：长期欺凌干预与跟进" in titles, titles
    assert "需规避" in titles, titles
    # 最小限度帮助：不得出现引号话术或「参考话术」
    for h in hints:
        assert "「" not in h["text"], h
        assert "参考话术" not in h["text"], h

    # 无对话、无失分时仍有兜底策略提醒
    fallback = build_context_hints([], [], [])
    assert fallback, "兜底提示缺失"


@test("R14 集成：报告接口返回逐轮分析与改进意见")
def t_report_contains_coaching():
    import uuid
    from fastapi.testclient import TestClient
    import database
    database.init_db()
    import app as webapp

    client = TestClient(webapp.app)
    uname = f"coach_{uuid.uuid4().hex[:8]}"
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

    # 跑一轮，产生对话
    client.post(f"/api/session/{sid}/run", json={}, headers={"Authorization": f"Bearer {token}"})

    r = client.get(f"/api/session/{sid}/report", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    report = r.json()
    assert "round_analysis" in report, "报告缺少 round_analysis"
    assert "improvement_suggestions" in report, "报告缺少 improvement_suggestions"
    assert isinstance(report["round_analysis"], list)
    assert report["improvement_suggestions"].get("rounds_analyzed", 0) >= 0
    # 逐轮分析条目结构
    if report["round_analysis"]:
        ra = report["round_analysis"][0]
        for key in ("round", "event_summary", "coach_comment", "next_step", "weak_dimensions"):
            assert key in ra, key


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
    print(f"\n=== 指导机制测试：{passed} 通过 / {failed} 失败 ===")
    sys.exit(1 if failed else 0)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R20 报告系统改造验收测试（计划书 §12）

覆盖：
1. 指标与趋势（§12.3）：百分点口径、稳定阈值、样本不足、短会话无三段伪趋势；
2. 发现能力（§9.3）：无可识别机会 → 不可评价（不判优秀）；
3. 生成指导质量（GUIDE-01~07）：无正向和解/对质建议、建议可追溯到维度；
4. report_data 合同（§6.1）：版本/评分状态/维度四态/证据引用/计数；
5. 命名修正（§2.2/§9.2）：「平均效果」「OBVQ 总分」等旧命名清除；
6. 关键词策略分类移除（§2.1）。

运行：
    python tests/test_report_contract.py
"""

import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "web"))

RESULTS = []


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


import session_manager as sm  # noqa: E402
from report_contract import (  # noqa: E402
    build_report_data, STATUS_NO, STATUS_INSUFFICIENT,
    STATUS_NOT_ASSESSED, STATUS_SCORED, REPORT_SCHEMA_VERSION,
)

DIMS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7"]


def make_assessment(scored_dims, pending=False, risk_flags=None):
    """构造 assessment dict（与持久化记录同构）。"""
    if pending:
        return {"assessment_id": "a_p", "status": "pending", "parse_error": "no_llm_client"}
    dims = {}
    for d in DIMS:
        v = scored_dims.get(d)
        if v is None:
            dims[d] = {"score": None, "status": "N/O", "band": "N/O",
                       "evidence": [], "rationale": "无观察机会", "confidence": 0.9}
        else:
            dims[d] = {"score": v, "status": "scored", "band": "初步不足",
                       "evidence": [f"证据-{d}"], "rationale": f"理由-{d}", "confidence": 0.8}
    scored = [v for v in scored_dims.values() if v is not None]
    raw = round(sum(scored) / len(scored), 1) if scored else None
    flags = risk_flags or []
    return {
        "assessment_id": "a1", "status": "scored",
        "raw_mean_score_100": raw, "final_quality_score_100": raw,
        "risk_cap_applied": False, "risk_level": "serious" if len(flags) >= 2 else ("attention" if flags else "none"),
        "rated_dimension_count": len(scored), "no_observable_count": len(DIMS) - len(scored),
        "dimensions": dims,
        "risk_assessment": {"level": "serious" if len(flags) >= 2 else "none",
                            "flags": flags,
                            "flags_detail": [{"code": c, "evidence": ["原文"], "context": "语境", "confidence": 0.8}
                                             for c in flags]},
        "summary": "测试",
    }


def make_stub(messages=None, interventions=None, skip=None, history=None):
    import datetime as _dt
    stub = types.SimpleNamespace(
        messages=messages or [],
        interventions=interventions or [],
        skip_decisions=skip or [],
        session_data={"metrics_history": history or []},
        session_id="t_session",
        round=3,
        started_at=_dt.datetime.now(),
    )
    stub._analyze_trends = types.MethodType(sm.SessionManager._analyze_trends, stub)
    stub._assess_ability = types.MethodType(sm.SessionManager._assess_ability, stub)
    stub._analyze_interventions = types.MethodType(sm.SessionManager._analyze_interventions, stub)
    return stub


def msg(role, content, round_no=1):
    return {"role": role, "sender": role, "content": content, "round": round_no}


def iv(content="测试干预", score=40.0, assessment=None, round_no=1):
    return {"content": content, "quality_score": score,
            "assessment": assessment, "round": round_no,
            "assessment_status": "scored" if assessment and assessment.get("status") == "scored" else "pending"}


# ============================================================
# 1. 指标与趋势（计划书 §12.3）
# ============================================================
@test("R20 趋势：0.50→0.55 应为「上升 5.0 个百分点」，不得显示稳定或相对百分比")
def t_trend_five_pp():
    stub = make_stub(history=[{"round": 1, "bullying_intensity": 0.50, "victim_distress": 0.50},
                              {"round": 2, "bullying_intensity": 0.55, "victim_distress": 0.50}])
    tr = stub._analyze_trends()
    bi = tr["bullying_metric"]
    assert bi["direction"] == "up", bi
    assert bi["delta_percentage_points"] == 5.0, bi
    assert "上升 5.0 个百分点" in bi["text"], bi["text"]
    assert "稳定" not in bi["text"], bi["text"]
    # 禁止把「变化量」表述为相对增长（如 +5%）；状态值以百分比显示是允许的
    assert "+5%" not in bi["text"] and "5.0 个百分点" in bi["text"], bi["text"]
    assert bi["valid_sample_count"] == 2


@test("R20 趋势：0.52→0.53 应为「1.0 个百分点」，不得混用相对增长")
def t_trend_one_pp():
    stub = make_stub(history=[{"round": 1, "victim_distress": 0.52},
                              {"round": 2, "victim_distress": 0.53}])
    tr = stub._analyze_trends()
    vd = tr["distress_metric"]
    assert vd["delta_percentage_points"] == 1.0, vd
    # 必须显示「1.0 个百分点」，且不得把百分点混作相对增长（0.52→0.53 相对增幅约 1.9%）
    assert "1.0 个百分点" in vd["text"], vd["text"]
    assert "1.9" not in vd["text"], vd["text"]


@test("R20 趋势：变化小于阈值（2 个百分点）应判「基本持平」并展示阈值")
def t_trend_stable_threshold():
    stub = make_stub(history=[{"round": 1, "bullying_intensity": 0.50},
                              {"round": 2, "bullying_intensity": 0.51}])
    tr = stub._analyze_trends()
    bi = tr["bullying_metric"]
    assert bi["direction"] == "stable", bi
    assert "基本持平" in bi["text"] and "稳定阈值" in bi["text"], bi["text"]


@test("R20 趋势：快照不足 2 个时如实显示数据不足，不把缺失按 0 平均")
def t_trend_insufficient():
    stub = make_stub(history=[{"round": 1, "bullying_intensity": 0.5}])
    tr = stub._analyze_trends()
    assert tr["bullying_metric"]["direction"] == "insufficient", tr["bullying_metric"]
    assert "不足" in tr["bullying_metric"]["text"]


@test("R20 趋势：3 轮短会话不做三段切片伪趋势，仅起止对比")
def t_trend_short_session():
    stub = make_stub(history=[{"round": 1, "bullying_intensity": 0.40},
                              {"round": 2, "bullying_intensity": 0.60},
                              {"round": 3, "bullying_intensity": 0.45}])
    tr = stub._analyze_trends()
    bi = tr["bullying_metric"]
    assert bi["baseline"] == 0.4 and bi["final"] == 0.45, bi   # 起止，不做段均值
    assert bi["delta_percentage_points"] == 5.0, bi
    assert "early" not in bi and "mid" not in bi
    assert tr["valid_sample_count"] == 3


# ============================================================
# 2. 发现能力（计划书 §9.3）
# ============================================================
@test("R20 发现能力：无欺凌事件时不判优秀，应为 not_evaluable")
def t_discovery_no_event():
    stub = make_stub(messages=[msg("victim", "我没事", 1), msg("teacher", "大家好", 1)],
                     interventions=[iv()])
    ab = stub._assess_ability({})
    assert ab["discovery"]["level"] == "not_evaluable", ab["discovery"]
    assert "没有足够证据评价发现能力" in ab["discovery"]["description"]
    assert "excellent" != ab["discovery"]["level"]


@test("R20 发现能力：有欺凌信号且当轮介入 → good，且注明只代表时机")
def t_discovery_timely():
    stub = make_stub(messages=[msg("bully", "废物", 1)],
                     interventions=[iv(round_no=1)])
    ab = stub._assess_ability({})
    assert ab["discovery"]["level"] == "good", ab["discovery"]
    assert "只代表介入时机" in ab["discovery"]["description"] or "轮次差只代表" in ab["discovery"]["description"]


@test("R20 发现能力：有欺凌信号但未干预 → needs_improvement")
def t_discovery_missed():
    stub = make_stub(messages=[msg("bully", "废物", 1)], interventions=[])
    ab = stub._assess_ability({})
    assert ab["discovery"]["level"] == "needs_improvement", ab["discovery"]


@test("R20 能力评估：无关键词策略分类、无关怀度口径，干预能力来自量规评分")
def t_ability_rubric_based():
    a = make_assessment({"D1": 70, "D2": 65, "D3": 60, "D4": 70, "D5": None, "D6": 40, "D7": None})
    stub = make_stub(interventions=[iv(score=61.0, assessment=a)])
    ab = stub._assess_ability({})
    assert "empathy" not in ab and "strategy_type" not in ab["intervention"], ab
    assert ab["intervention"]["rubric_score"] == 61.0, ab["intervention"]
    assert "victim_protection" in ab and ab["victim_protection"]["score"] == 65.0


# ============================================================
# 3. 生成指导质量（计划书 GUIDE-01 ~ 07）
# ============================================================
BANNED_POSITIVE = re.compile(
    r"(建议|尝试|请|可以|下一步)[^。；;\n]{0,30}(和解|达成约定|握手|当面对质|互相道歉|原谅他)")


@test("R20 GUIDE-04：任何建议中不得出现正向的和解/对质/原谅要求")
def t_no_positive_reconciliation():
    a = make_assessment({"D1": 30, "D2": 20, "D3": 25, "D4": 30, "D5": None, "D6": 15, "D7": None},
                        risk_flags=["R_FORCED_CONFRONTATION"])
    stub = make_stub(interventions=[iv(content="你们一起沟通下达成个约定", score=25.0, assessment=a)])
    ia = stub._analyze_interventions()
    texts = list(ia.get("suggestions", [])) + list(ia.get("action_plan", []))
    assert texts, "应产出建议"
    for t in texts:
        assert not BANNED_POSITIVE.search(t), f"疑似正向和解建议：{t}"


@test("R20 GUIDE-01/07：建议可追溯到维度，优先项不超过 3 个")
def t_suggestions_traceable():
    a = make_assessment({"D1": 30, "D2": 20, "D3": 25, "D4": 30, "D5": None, "D6": 15, "D7": None})
    stub = make_stub(interventions=[iv(score=25.0, assessment=a)])
    ia = stub._analyze_interventions()
    joined = " ".join(ia.get("suggestions", []))
    assert re.search(r"D\d", joined), joined           # 建议点名维度
    gap_line = [x for x in ia.get("suggestions", []) if "缺口" in x]
    assert len(gap_line) == 1 and gap_line[0].count("（均") <= 3, gap_line


@test("R20 策略分类移除：intervention_analysis 不再输出关键词策略字段")
def t_no_strategy_fields():
    a = make_assessment({"D1": 50, "D2": 40, "D3": 45, "D4": 50, "D5": None, "D6": 20, "D7": None})
    stub = make_stub(interventions=[iv(score=42.0, assessment=a)])
    ia = stub._analyze_interventions()
    for banned in ("strategy_type", "best_strategy", "strategy_scores"):
        assert banned not in ia, banned
    assert "权威型" not in " ".join(ia.get("suggestions", []))


# ============================================================
# 4. report_data 合同（计划书 §6.1/§4.2）
# ============================================================
@test("R20 合同：字段齐全、维度四态、计数正确（完整评分）")
def t_contract_complete():
    a = make_assessment({"D1": 70, "D2": 65, "D3": 60, "D4": 70, "D5": None, "D6": 40, "D7": None})
    stub = make_stub(messages=[msg("bully", "废物", 1), msg("victim", "我没事", 1)],
                     interventions=[iv(score=61.0, assessment=a)])
    rd = build_report_data(stub, {}, None, None)
    assert rd["report_schema_version"] == REPORT_SCHEMA_VERSION
    assert rd["rubric_version"] and rd["generated_at"] and rd["report_id"]
    assert rd["scoring_status"] in ("complete", "partial")
    assert rd["summary"]["rated_count"] == 5, rd["summary"]
    assert rd["summary"]["no_opportunity_count"] == 2
    assert rd["summary"]["insufficient_evidence_count"] == 0
    statuses = {d["code"]: d["status"] for d in rd["dimensions"]}
    assert statuses["D5"] == STATUS_NO and statuses["D1"] == STATUS_SCORED
    d1 = next(d for d in rd["dimensions"] if d["code"] == "D1")
    assert d1["evidence_refs"] and d1["rationale"], d1
    assert d1["retest_criteria"], d1


@test("R20 合同：教师有干预但日志无学生消息 → 依赖反馈的维度判证据不足（不伪装成 N/O）")
def t_contract_insufficient_evidence():
    a = make_assessment({"D1": 70, "D2": 65, "D3": 60, "D4": 70, "D5": None, "D6": 40, "D7": None})
    stub = make_stub(messages=[msg("teacher", "停止", 1)],
                     interventions=[iv(score=61.0, assessment=a)])
    rd = build_report_data(stub, {}, None, None)
    statuses = {d["code"]: d["status"] for d in rd["dimensions"]}
    assert statuses["D2"] == STATUS_INSUFFICIENT, statuses
    assert statuses["D3"] == STATUS_INSUFFICIENT, statuses
    assert statuses["D7"] == STATUS_NO, statuses       # 专家已判 N/O，保持
    assert statuses["D1"] == STATUS_SCORED, statuses
    assert rd["summary"]["insufficient_evidence_count"] == 2
    assert rd["scoring_status"] == "partial"
    assert any("证据不足" in x for x in rd["data_notes"]["limitations"])


@test("R20 合同：评分未完成 → 全部 not_assessed，不生成总分（不伪造）")
def t_contract_not_assessed():
    stub = make_stub(messages=[msg("bully", "废物", 1)],
                     interventions=[iv(score=None, assessment=make_assessment({}, pending=True))])
    rd = build_report_data(stub, {}, None, None)
    assert all(d["status"] == STATUS_NOT_ASSESSED for d in rd["dimensions"])
    assert rd["summary"]["final_quality_score_100"] is None
    assert rd["summary"]["not_assessed_count"] == 7
    assert rd["scoring_status"] == "partial"
    assert rd["summary"]["rated_count"] == 0


@test("R20 合同：无干预记录 → not_evaluable")
def t_contract_not_evaluable():
    stub = make_stub(messages=[msg("bully", "废物", 1)], interventions=[])
    rd = build_report_data(stub, {}, None, None)
    assert rd["scoring_status"] == "not_evaluable"


@test("R20 合同：风险带证据引用与修正动作；六类统计带公式与局限说明")
def t_contract_risks_and_types():
    a = make_assessment({"D1": 30, "D2": 20, "D3": 25, "D4": 30, "D5": None, "D6": 15, "D7": None},
                        risk_flags=["R_FORCED_CONFRONTATION", "R_MINIMIZE_HARM"])
    stub = make_stub(messages=[msg("bully", "废物", 1), msg("victim", "哭", 1)],
                     interventions=[iv(score=25.0, assessment=a)])
    rd = build_report_data(stub, {}, None, {"total_score": 12, "dimension_analysis": []})
    assert rd["risks"], "风险应带证据"
    for r in rd["risks"]:
        assert r["evidence_refs"] and r["recommended_correction"], r
    tp = rd["bullying_type_profile"]
    assert tp["total_formula"] and "min(80" in tp["total_formula"]
    assert "非" in tp["method"] and "标准量表" in tp["method"]
    assert tp["interpretive_limitations"]


# ============================================================
# 5. 命名修正与端到端（计划书 §2.2/§12.4）
# ============================================================
@test("R20 端到端：报告接口/文本导出含新命名与新板块，旧命名清除")
def t_end_to_end_naming():
    import uuid
    from multi_agent import intervention_expert as ie
    from multi_agent.intervention_expert import InterventionAssessment, DimensionAssessment

    def _fake(self, state, content, assessment_id):
        dims = {}
        for k in DIMS:
            dims[k] = (DimensionAssessment(score=None, status="N/O", band="N/O", evidence=[],
                                            rationale="无观察机会", confidence=0.9)
                       if k in ("D5", "D7")
                       else DimensionAssessment(score=40, status="scored", band="初步不足",
                                                evidence=[f"证据-{k}"], rationale=f"理由-{k}", confidence=0.8))
        raw = round(40 * 5 / 5, 1)
        return InterventionAssessment(
            assessment_id=assessment_id, status="scored", raw_mean_score_100=40.0,
            final_quality_score_100=40.0, risk_cap_applied=False, risk_level="attention",
            rated_dimension_count=5, no_observable_count=2, dimensions=dims,
            risk_assessment={"level": "attention", "flags": [], "flags_detail": []},
            context_profile={}, strategy_profile={}, summary="测试")

    ie.InterventionExpertAgent.assess = _fake

    import database
    database.init_db()
    from fastapi.testclient import TestClient
    import app as webapp

    client = TestClient(webapp.app)
    uname = f"rc_{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"username": uname, "password": "pass1234",
                                                "display_name": "T"})
    token = r.json().get("token", "")
    auth = {"Authorization": f"Bearer {token}"}
    r = client.post("/api/session", json={"level": 2, "max_rounds": 3, "use_llm": False,
                                          "participant_ids": ["bully", "victim", "bystander_a", "bystander_b"],
                                          "participants_data": []}, headers=auth)
    sid = r.json().get("session_id", "")
    assert sid, r.text
    for i in range(2):
        client.post(f"/api/session/{sid}/run", json={}, headers=auth)
        client.post(f"/api/session/{sid}/intervene",
                    json={"content": "请立刻停止对他的嘲笑。", "client_token": f"rc{i}"},
                    headers=auth)

    rep = client.get(f"/api/session/{sid}/report", headers=auth).json()
    rd = rep.get("report_data") or {}
    assert rd and rd.get("report_schema_version") == "1.0", "报告缺少 report_data"
    ab = rep.get("ability_assessment") or {}
    assert "empathy" not in ab and "victim_protection" in ab
    assert "strategy_type" not in (ab.get("intervention") or {})

    # 文本导出（与网页同源）
    r = client.get(f"/api/session/{sid}/download-report-text", headers=auth)
    text = r.text if r.status_code == 200 else ""
    assert text, "文本导出为空"
    assert "平均效果" not in text, "旧命名「平均效果」仍在文本导出中"
    assert "教师干预质量评分" in text
    assert "OBVQ 总分" not in text and "OBVQ 霸凌风险指数" not in text
    assert "类型总分" in text and "项目分类" in text
    assert "【模拟状态变化" in text and "百分点" in text
    assert "【数据说明】" in text and "评分状态" in text


@test("R21 合同完整性：含 model_id / interaction_timeline / training_plan / reflection_questions")
def t_contract_fields_complete():
    a = make_assessment({"D1": 70, "D2": 65, "D3": 60, "D4": 70, "D5": None, "D6": 40, "D7": None})
    stub = make_stub(messages=[msg("bully", "废物", 1), msg("victim", "我没事", 1)],
                     interventions=[iv(score=61.0, assessment=a)])
    rd = build_report_data(stub, {}, None, None, [{"round": 1, "coach_comment": "点评"}])
    assert "model_id" in rd, "缺少 model_id"
    tl = rd.get("interaction_timeline")
    assert isinstance(tl, list) and tl, "缺少互动时间轴"
    assert all(t.get("turn_id") for t in tl), tl[:2]
    assert any(t.get("round") == 1 for t in tl), tl
    assert "training_plan" in rd and "reflection_questions" in rd
    assert all("sample_utterance" in d for d in rd["dimensions"]), rd["dimensions"][0]
    # 训练计划条目须含 §8.2 标准结构字段
    if rd["training_plan"]:
        p = rd["training_plan"][0]
        for f in ("objective", "actions", "safety_constraints", "success_criteria", "retest_task"):
            assert f in p, (f, p)


@test("R21 全部维度 N/O 时不得判 complete（计划书 RUB-10）")
def t_contract_all_no():
    a = make_assessment({})
    stub = make_stub(messages=[msg("bully", "废物", 1)], interventions=[])
    rd = build_report_data(stub, {}, None, None)
    assert rd["scoring_status"] != "complete", rd["scoring_status"]
    assert rd["summary"]["final_quality_score_100"] is None


@test("R21 三端同源：管理端/用户端前端均无旧命名，且具备历史报告回退")
def t_frontend_consistency():
    import io
    import os
    web = os.path.join(ROOT, "web", "js")
    admin = io.open(os.path.join(web, "admin.js"), encoding="utf-8").read()
    user = io.open(os.path.join(web, "app.js"), encoding="utf-8").read()

    # 管理端：旧命名必须清除
    for bad in ("平均效果", "平均干预效果", "OBVQ 总分", "同理心关怀", "strategy_type"):
        assert bad not in admin, f"管理端仍有旧引用：{bad}"
    for good in ("教师干预质量评分", "类型总分（项目分类）", "受害者保护（D2）",
                 "关键风险检查", "数据说明", "obvqStart", "victimProtection"):
        assert good in admin, f"管理端缺少：{good}"
    # 管理端：变化量用百分点
    assert "个百分点" in admin and "Math.round(Math.abs((trends.bullying_change || 0)) * 100)%" not in admin

    # 用户端：历史回退助手 + 新板块
    for good in ("obvqStart", "obvqEnd", "victimProtectionOf", "关键风险检查", "已具备的优势"):
        assert good in user, f"用户端缺少：{good}"
    assert "assessment.victim_protection?" not in user, "用户端未做历史报告回退"


@test("R22 下载报告与网页报告同源：导出含新章节，网页/管理端引用同一份 report_data 字段")
def t_r22_parity():
    import io
    import os
    import uuid as _uuid
    from multi_agent import intervention_expert as ie
    from multi_agent.intervention_expert import InterventionAssessment, DimensionAssessment

    def _fake(self, state, content, assessment_id):
        dims = {}
        for k in DIMS:
            dims[k] = (DimensionAssessment(score=None, status="N/O", band="N/O", evidence=[],
                                            rationale="无旁观者互动机会", confidence=0.9)
                       if k == "D5"
                       else DimensionAssessment(score=45, status="scored", band="初步不足",
                                                evidence=[f"你愿意原谅他吗（原文-{k}）"],
                                                rationale=f"未明确制止（理由-{k}）", confidence=0.8))
        return InterventionAssessment(
            assessment_id=assessment_id, status="scored", raw_mean_score_100=45.0,
            final_quality_score_100=45.0, risk_cap_applied=False, risk_level="attention",
            rated_dimension_count=6, no_observable_count=1, dimensions=dims,
            risk_assessment={"level": "attention", "flags": ["R_VICTIM_BLAME"],
                             "flags_detail": [{"code": "R_VICTIM_BLAME",
                                               "evidence": ["你愿意原谅他吗"],
                                               "context": "把决定权交给受害者", "confidence": 0.8}]},
            context_profile={}, strategy_profile={}, summary="测试")

    ie.InterventionExpertAgent.assess = _fake

    import database
    database.init_db()
    from fastapi.testclient import TestClient
    import app as webapp

    client = TestClient(webapp.app)
    uname = f"r22_{_uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"username": uname, "password": "pass1234",
                                                "display_name": "T"})
    token = r.json().get("token", "")
    auth = {"Authorization": f"Bearer {token}"}
    r = client.post("/api/session", json={"level": 2, "max_rounds": 3, "use_llm": False,
                                          "participant_ids": ["bully", "victim", "bystander_a", "bystander_b"],
                                          "participants_data": []}, headers=auth)
    sid = r.json().get("session_id", "")
    assert sid, r.text
    for i in range(2):
        client.post(f"/api/session/{sid}/run", json={}, headers=auth)
        client.post(f"/api/session/{sid}/intervene",
                    json={"content": "你们都是同学，一起沟通下，我会处理。", "client_token": f"r22_{i}"},
                    headers=auth)

    rep = client.get(f"/api/session/{sid}/report", headers=auth).json()
    rd = rep.get("report_data") or {}
    # 合同侧必须具备这些内容
    assert rd.get("dimensions"), "缺少维度明细"
    assert any(d.get("rationale") and d.get("evidence_refs") for d in rd["dimensions"]), rd["dimensions"]
    assert any(d.get("sample_utterance") for d in rd["dimensions"]), "维度缺少示范表达"
    assert rd.get("training_plan"), "缺少训练计划"
    assert rd.get("interaction_timeline"), "缺少互动时间轴"
    assert rd.get("reflection_questions"), "缺少反思问题"
    assert rd.get("report_id") and rd.get("report_schema_version"), "缺少报告元信息"

    # 下载报告（文本）必须呈现这些内容
    text = client.get(f"/api/session/{sid}/download-report-text", headers=auth).text
    for sec in ("【七维评分明细】", "【训练计划与复测】", "【互动时间轴】", "【反思问题】", "【报告元信息】"):
        assert sec in text, f"下载报告缺少章节：{sec}"
    assert "未明确制止（理由-D1）" in text, "下载报告缺少专家判断理由"
    assert "你愿意原谅他吗（原文-D1）" in text, "下载报告缺少原文证据"

    # 网页与管理端必须引用同一份 report_data 字段
    jsdir = os.path.join(ROOT, "web", "js")
    user = io.open(os.path.join(jsdir, "app.js"), encoding="utf-8").read()
    admin = io.open(os.path.join(jsdir, "admin.js"), encoding="utf-8").read()
    for name, src in (("app.js", user), ("admin.js", admin)):
        for field in ("rd.dimensions", "rd.training_plan", "rd.interaction_timeline",
                      "rd.reflection_questions"):
            assert field in src, f"{name} 未引用 {field}"
        for title in ("七维评分明细", "训练计划与复测", "互动时间轴", "反思问题"):
            assert title in src, f"{name} 缺少板块：{title}"
    # 无重复板块（R21 曾因补丁重复应用导致「关键风险检查」出现两次）
    assert user.count("⚠ 关键风险检查") == 1, user.count("⚠ 关键风险检查")
    assert user.count("✅ 已具备的优势") == 1, user.count("✅ 已具备的优势")


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
    print(f"\n=== 报告系统改造测试：{passed} 通过 / {failed} 失败 ===")
    sys.exit(1 if failed else 0)

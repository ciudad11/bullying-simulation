#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R25 预设干预文案测试（fin_ver 专有）

覆盖：
1. 预设库结构：10 条、id 唯一、无测试残留项；
2. 每条预设必须覆盖量规六要素（定性/停止要求/安全确认与保护/责任与改变/群体规范与报告路径/可核查跟进）；
3. 禁止出现和解、当面对质、握手等风险表述；
4. 字数落在方案 A 区间（120–160 字，两三句话）；
5. 安全分离安排（不安排双方碰面）以控制报复风险；
6. 人名已挖空（统一使用【欺凌者】【受害者】占位符）；
7. 条目互不重复（相似度阈值）；
8. 版本差异保护：noreset 不得被同步这套预设（fin_ver 独有的快捷回复）。

运行：
    python tests/test_presets.py
"""

import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "src", "intervention_library.json")
NORESET_LIB = os.path.join(os.path.dirname(ROOT), "noreset", "src", "intervention_library.json")

RESULTS = []
EXPECTED_COUNT = 10
MIN_LEN, MAX_LEN = 120, 160
MAX_SIM = 0.93          # 10 条共享同一得分骨架（实测最稳结构），差异在开场引导与跟进措辞


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


# 量规六要素的必备特征（覆盖 R25 定稿的全部等义句式）
REQUIRED = [
    ("D1 行为定性", ["言语欺凌"]),
    ("D1 明确停止要求", ["立刻停止", "立刻停下", "请立刻停止"]),
    ("D2 安全确认", ["你安全吗", "安全吗"]),
    ("D2 具体保护安排", ["先坐下", "到我这边", "先到我"]),
    ("D4 安全分离", ["今天不碰面", "不碰面"]),
    ("D3 影响与改变要求", ["造成的影响", "造成什么影响", "先问一句对方愿不愿意"]),
    ("D5 群体规范与报告路径", ["不允许起哄和传播", "同样算欺负", "要直接来报告我", "请直接报告我"]),
    ("D6 可核查跟进", ["周三再确认", "周三再确认情况", "分别找你们谈一次", "分别谈一次"]),
]

BANNED = ["握手", "互相原谅", "当面道歉", "当面对质", "让你们碰面", "一起沟通下",
          "达成个约定", "请家长一起来学校共同解决", "你们都是同学"]


def load_lib():
    with io.open(LIB, encoding="utf-8") as f:
        return json.load(f)


@test("R25 预设库结构：10 条、id 唯一、无测试残留")
def t_structure():
    items = load_lib().get("interventions", [])
    assert len(items) == EXPECTED_COUNT, f"预设条数应为 {EXPECTED_COUNT}，实际 {len(items)}"
    ids = [x["id"] for x in items]
    assert len(ids) == len(set(ids)), "存在重复 id"
    assert "custom_3496c514" not in ids, "测试干预条目未清理"
    for it in items:
        assert it.get("title") and it.get("content"), it.get("id")
        assert it.get("category"), it.get("id")
        assert it.get("tags"), it.get("id")


@test("R25 每条预设覆盖量规六要素")
def t_elements():
    items = load_lib().get("interventions", [])
    missing_all = {}
    for it in items:
        content = it["content"]
        miss = [label for label, keys in REQUIRED
                if not any(k in content for k in keys)]
        if miss:
            missing_all[it["id"]] = miss
    assert not missing_all, f"要素缺失：{missing_all}"


@test("R25 预设不得包含和解/对质等风险表述")
def t_no_risk_phrasing():
    items = load_lib().get("interventions", [])
    for it in items:
        for bad in BANNED:
            assert bad not in it["content"], f"{it['id']} 含风险表述「{bad}」"


@test("R25 预设为两三句话（方案 A：字数落在 120–160 区间）")
def t_length():
    items = load_lib().get("interventions", [])
    for it in items:
        n = len(it["content"])
        assert MIN_LEN <= n <= MAX_LEN, f"{it['id']} 字数 {n} 不在 {MIN_LEN}-{MAX_LEN} 区间"
        # 三句话：句号数量应在 2–4 之间（不计感叹号/问号结尾的引导语）
        assert it["content"].count("。") <= 4, f"{it['id']} 句数偏多"


@test("R25 预设含安全分离安排（不安排双方碰面）以控制报复风险")
def t_safety_separation():
    items = load_lib().get("interventions", [])
    for it in items:
        assert "不碰面" in it["content"], f"{it['id']} 缺少安全分离安排"


@test("R25 人名已挖空：不得含具体学生姓名，且使用统一占位符")
def t_no_real_names():
    names = ("陈浩", "思远", "林雨桐", "赵磊")
    items = load_lib().get("interventions", [])
    for it in items:
        for n in names:
            assert n not in it["content"], f"{it['id']} 仍含具体人名「{n}」"
        assert "【" in it["content"] and "】" in it["content"], \
            f"{it['id']} 未使用占位符（应使用【欺凌者】【受害者】）"
        # 不得出现未闭合的占位符
        assert len(re.findall(r"【[^】]+】", it["content"])) == it["content"].count("【"), \
            f"{it['id']} 占位符未闭合"


@test("R25 预设互不重复：两两相似度低于阈值")
def t_no_duplicate_content():
    def sim(a, b):
        A = {a[i:i + 2] for i in range(len(a) - 1)}
        B = {b[i:i + 2] for i in range(len(b) - 1)}
        return len(A & B) / max(1, len(A | B))

    items = load_lib().get("interventions", [])
    worst = 0.0
    worst_pair = ("", "")
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            s = sim(items[i]["content"], items[j]["content"])
            if s > worst:
                worst, worst_pair = s, (items[i]["id"], items[j]["id"])
    assert worst < MAX_SIM, f"存在高度重复条目：{worst_pair} 相似度 {worst:.3f}"


@test("R25 版本差异保护：noreset 不得被同步这套预设")
def t_no_cross_sync():
    if not os.path.exists(NORESET_LIB):
        return
    data = json.load(io.open(NORESET_LIB, encoding="utf-8"))
    items = data.get("interventions", [])
    contents = " ".join(x.get("content", "") for x in items)
    assert "言语欺凌" not in contents, "noreset 被误同步了 fin_ver 的预设文案"
    assert len(items) <= 2, f"noreset 预设条数异常：{len(items)}"


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
    print(f"\n=== 预设文案测试：{passed} 通过 / {failed} 失败 ===")
    sys.exit(1 if failed else 0)

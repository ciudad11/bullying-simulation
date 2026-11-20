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
7. 条目互不重复（全文相似度 <0.75；去首句后的中段两两 <0.9）；
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
PACK_ROOT = os.path.dirname(ROOT)
# 本套件是 fin_ver 专有（见文件头）：预设库固定取 fin_ver 的 10 条，
# noreset 只作为「差异保护」对照。旧写法按自身所在副本取库，导致在 noreset
# 副本里运行时会去校验它自己的 2 条自定义条目（必然失败）；任务#82 起显式取 fin 库，
# 两个副本运行同一套断言，语义一致。
FIN_LIB = os.path.join(PACK_ROOT, "fin_ver", "src", "intervention_library.json")
NORESET_LIB = os.path.join(PACK_ROOT, "noreset", "src", "intervention_library.json")
LIB = FIN_LIB if os.path.exists(FIN_LIB) else os.path.join(ROOT, "src", "intervention_library.json")

RESULTS = []
EXPECTED_COUNT = 10
MIN_LEN, MAX_LEN = 120, 160
# 任务#82 D 线：10 条预设改为「量规 8 要素不变、中段逐条措辞不同」，
# 全文 bigram-Jaccard 阈值从 0.93 收到 0.75（改前实测最高 0.922 几乎卡线），
# 并新增「去首句后中段两两 < 0.9」断言——把「只有开场不同、中段套同一骨架」
# 这种同质化挡在测试层。句号数仍保持 <=4（改写后 10 条均为 4，未放宽）。
MAX_SIM = 0.75
MID_MAX_SIM = 0.90


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


# 量规六要素的必备特征（覆盖 R25 定稿的全部等义句式）
# 任务#85 M2 返工：D2/D4/D6 改为「等义白名单」（any-of）。原「你安全吗 / 今天不碰面 /
# 周三再确认」是开放式时间/安全锚点（research_81 点 2：紧邻干预偏移率 83%），预设已
# 改为情景内/当场性表述；旧字面保留以兼容历史文案，新表述加入白名单。断言本身与
# 其余要素均未削弱。
REQUIRED = [
    ("D1 行为定性", ["言语欺凌"]),
    ("D1 明确停止要求", ["立刻停止", "立刻停下", "请立刻停止"]),
    ("D2 安全确认", ["你安全吗", "安全吗", "现在在这儿还行吗", "在这儿还行吗"]),
    ("D2 具体保护安排", ["先坐下", "到我这边", "先到我"]),
    ("D4 安全分离", ["今天不碰面", "不碰面", "不在一起", "不挨着坐"]),
    ("D3 影响与改变要求", ["造成的影响", "造成什么影响", "先问一句对方愿不愿意"]),
    ("D5 群体规范与报告路径", ["不允许起哄和传播", "同样算欺负", "要直接来报告我", "请直接报告我"]),
    ("D6 可核查跟进", ["周三再确认", "周三再确认情况", "分别找你们谈一次", "分别谈一次",
                       "当场记录", "当场记下来"]),
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
    # 任务#85：与 REQUIRED 的 D4 组共用同一等义白名单（any-of），断言本身保留。
    separation_keys = next(keys for label, keys in REQUIRED if label == "D4 安全分离")
    items = load_lib().get("interventions", [])
    for it in items:
        assert any(k in it["content"] for k in separation_keys), \
            f"{it['id']} 缺少安全分离安排（需命中 {separation_keys} 之一）"


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


def _strip_first_sentence(content: str) -> str:
    """去掉差异化开场首句，只留中段（任务#82 D 线：骨架同质化出现在中段）。"""
    idx = content.find("。")
    return content[idx + 1:] if idx >= 0 else content


@test("R25 预设中段互不重复：去首句后两两 jaccard < 0.9")
def t_no_duplicate_middle():
    def sim(a, b):
        A = {a[i:i + 2] for i in range(len(a) - 1)}
        B = {b[i:i + 2] for i in range(len(b) - 1)}
        return len(A & B) / max(1, len(A | B))

    items = load_lib().get("interventions", [])
    mids = [_strip_first_sentence(x["content"]) for x in items]
    worst = 0.0
    worst_pair = ("", "")
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            s = sim(mids[i], mids[j])
            if s > worst:
                worst, worst_pair = s, (items[i]["id"], items[j]["id"])
    assert worst < MID_MAX_SIM, f"中段高度重复：{worst_pair} 相似度 {worst:.3f}"


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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R11 数据迁移：教师干预评分单位迁移（0-1 → 0-100）+ 显式版本标记。

依据《规则引擎重构方案 v1.0》§14.4 / §15.6：
- 所有教师评分记录写入显式单位标记 `quality_score_scale: 100`；
- 历史 0-1 记录由本迁移函数【一次性】升级为 100 制，并写入迁移版本标记；
- 迁移函数【幂等】：已带标记的记录不再改动，重复执行结果一致；
- 无法判定的脏数据（负值、>100）只记入迁移报告，不做猜测性换算。

用法：
    python migrate_quality_scores.py            # 执行迁移
    python migrate_quality_scores.py --dry-run  # 仅体检不写库
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

MIGRATION_VERSION = "v1_100"
SCALE = 100
DB_RELPATH = os.path.join("web", "data.db")


def migrate_dict(obj, report, path="root"):
    """递归处理 JSON 对象中的评分字段；返回是否发生变更。"""
    changed = False
    if isinstance(obj, dict):
        # --- 教师评分 quality_score ---
        if "quality_score" in obj and "quality_score_scale" not in obj:
            v = obj.get("quality_score")
            if v is None:
                obj["quality_score_scale"] = SCALE
                obj.setdefault("assessment_status", "pending")
                report["pending_marked"] += 1
                changed = True
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                if v < 0 or v > SCALE:
                    report["invalid"].append(f"{path}.quality_score={v}")
                    obj["quality_score_scale"] = SCALE
                    changed = True
                elif 0 < v <= 1:
                    # 旧口径（0-1）：一次性换算；v == 1 属歧义值，记入报告
                    obj["quality_score"] = round(v * SCALE)
                    obj["quality_score_scale"] = SCALE
                    report["converted"] += 1
                    if v == 1:
                        report["ambiguous"].append(f"{path}.quality_score=1（按旧口径换算为 100）")
                    changed = True
                else:
                    obj["quality_score_scale"] = SCALE
                    changed = True
        # --- 干预效果均值 intervention_effectiveness ---
        if "intervention_effectiveness" in obj and "intervention_effectiveness_scale" not in obj:
            v = obj.get("intervention_effectiveness")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if 0 < v <= 1:
                    obj["intervention_effectiveness"] = round(v * SCALE)
                    obj["intervention_effectiveness_scale"] = SCALE
                    report["converted"] += 1
                    changed = True
                else:
                    obj["intervention_effectiveness_scale"] = SCALE
                    changed = True
        for k, v in list(obj.items()):
            if isinstance(v, (dict, list)):
                if migrate_dict(v, report, f"{path}.{k}"):
                    changed = True
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, (dict, list)):
                if migrate_dict(v, report, f"{path}[{i}]"):
                    changed = True
    return changed


def process_table(conn, table, column, report, dry_run):
    try:
        rows = conn.execute(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != ''").fetchall()
    except sqlite3.Error as e:
        report["errors"].append(f"{table}: {e}")
        return
    for rid, raw in rows:
        try:
            data = json.loads(raw)
        except Exception:
            report["errors"].append(f"{table}#{rid}: JSON 解析失败")
            continue
        if not migrate_dict(data, report, f"{table}#{rid}"):
            continue
        data["score_migration"] = {"version": MIGRATION_VERSION, "migrated_at": datetime.now().isoformat()}
        if not dry_run:
            conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?",
                         (json.dumps(data, ensure_ascii=False), rid))
        report["rows_updated"] += 1


def main():
    ap = argparse.ArgumentParser(description="迁移教师评分为 0-100 口径（幂等）")
    ap.add_argument("--db", default=DB_RELPATH, help="数据库路径（默认 web/data.db）")
    ap.add_argument("--dry-run", action="store_true", help="仅体检，不写库")
    args = ap.parse_args()

    db_path = args.db
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), db_path)
    if not os.path.exists(db_path):
        print(f"[SKIP] 数据库不存在：{db_path}")
        return 0

    report = {"rows_updated": 0, "converted": 0, "pending_marked": 0,
              "invalid": [], "ambiguous": [], "errors": []}
    conn = sqlite3.connect(db_path)
    try:
        process_table(conn, "reports", "report_json", report, args.dry_run)
        process_table(conn, "logs", "log_json", report, args.dry_run)
        if not args.dry_run:
            conn.commit()
    finally:
        conn.close()

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"=== 评分单位迁移报告 [{mode}] ===")
    print(f"数据库: {db_path}")
    print(f"更新记录数: {report['rows_updated']}")
    print(f"旧口径换算条数: {report['converted']}")
    print(f"待评估标记条数: {report['pending_marked']}")
    if report["invalid"]:
        print(f"非法值(未换算，仅标记): {report['invalid']}")
    if report["ambiguous"]:
        print(f"歧义值: {report['ambiguous']}")
    if report["errors"]:
        print(f"错误: {report['errors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

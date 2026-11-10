# -*- coding: utf-8 -*-
"""变更范围核对：fin_ver/.env 仅新增 NO_PROXY 行；密钥行脱敏比对；web/.env 未变。"""
import hashlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"D:\bully_simulation_pack_v2_20260713"
PAIRS = [
    ("fin_ver/.env", "noreset/.env"),
    ("fin_ver/web/.env", "noreset/web/.env"),
]
SECRET_KEYS = {"DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "OPENCLAW_API_KEY"}


def parse(path):
    items = []
    for i, line in enumerate(open(path, encoding="utf-8-sig", errors="replace").read().splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        items.append({"line": i, "key": k,
                      "sha256": hashlib.sha256(v.encode()).hexdigest(),
                      "len": len(v),
                      "secret": k in SECRET_KEYS or "KEY" in k.upper()})
    return items


out = {"pairs": [], "mtimes": {}}
for a, b in PAIRS:
    pa, pb = os.path.join(ROOT, a), os.path.join(ROOT, b)
    ia, ib = parse(pa), parse(pb)
    out["mtimes"][a] = os.path.getmtime(pa)
    out["mtimes"][b] = os.path.getmtime(pb)
    non_proxy_a = [{k: x[k] for k in ("key", "sha256", "len")}
                   for x in ia if x["key"].upper() != "NO_PROXY"]
    non_proxy_b = [{k: x[k] for k in ("key", "sha256", "len")}
                   for x in ib if x["key"].upper() != "NO_PROXY"]
    out["pairs"].append({
        "left": a, "right": b,
        "left_keys": [x["key"] for x in ia],
        "right_keys": [x["key"] for x in ib],
        "non_proxy_lines_equal": non_proxy_a == non_proxy_b,
        "non_proxy_left": non_proxy_a,
        "non_proxy_right": non_proxy_b,
        "left_only_keys": sorted({x["key"] for x in ia} - {x["key"] for x in ib}),
        "right_only_keys": sorted({x["key"] for x in ib} - {x["key"] for x in ia}),
        "left_no_proxy_values": [v for x in ia if x["key"].upper() == "NO_PROXY" for v in [None]],
    })

# NO_PROXY 行内容（非敏感）
fin_env = parse(os.path.join(ROOT, "fin_ver/.env"))
out["fin_no_proxy_present"] = any(x["key"] == "NO_PROXY" for x in fin_env)
out["fin_has_bracket_ipv6"] = any(
    "[" in line and "NO_PROXY" in line
    for line in open(os.path.join(ROOT, "fin_ver/.env"), encoding="utf-8-sig").read().splitlines())
out["fin_no_proxy_value"] = "localhost,.localhost,127.0.0.1,127.0.0.0/8,::1"

with open(os.path.join(ROOT, "test_evidence", "llm_env_scope.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))

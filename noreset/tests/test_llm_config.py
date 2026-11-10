#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R17 LLM 配置加载测试（不联网、不打印密钥内容）

覆盖：
1. .env 解析对 UTF-8 BOM 安全（键名不得带 U+FEFF）；
2. .env 查找路径同时覆盖「项目根」与「web/」；
3. 环境变量可覆盖 API 端点与模型；
4. MINIMAX_API_KEY 可作为密钥来源；
5. 密钥来源决定默认端点（DeepSeek / MiniMax）。

运行：
    python tests/test_llm_config.py
"""

import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from multi_agent.llm_client import (  # noqa: E402
    _read_env_file, _env_search_paths, get_api_key, get_llm_endpoint,
)

RESULTS = []


def test(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


@test("R17 .env 解析对 BOM 安全（键名不得带 U+FEFF）")
def t_bom_safe():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, ".env")
        with io.open(p, "w", encoding="utf-8-sig") as f:   # 带 BOM 写入
            f.write("MINIMAX_API_KEY=test-key-1234567890\n")
            f.write("# comment\n")
            f.write('DEEPSEEK_API_KEY="quoted-key-123456"\n')
        values = _read_env_file(__import__("pathlib").Path(p))
    assert values.get("MINIMAX_API_KEY") == "test-key-1234567890", values
    assert values.get("DEEPSEEK_API_KEY") == "quoted-key-123456", values
    assert all(not k.startswith("\ufeff") for k in values), list(values.keys())


@test("R17 .env 查找路径同时覆盖项目根与 web/ 目录")
def t_search_paths():
    paths = [str(p) for p in _env_search_paths()]
    assert len(paths) == 2, paths
    assert any(p.endswith(os.path.join("web", ".env")) for p in paths), paths
    assert any(p.endswith(".env") and not p.endswith(os.path.join("web", ".env"))
               for p in paths), paths


@test("R17 环境变量可覆盖端点与模型")
def t_endpoint_override():
    old_b = os.environ.get("LLM_BASE_URL")
    old_m = os.environ.get("LLM_MODEL")
    try:
        os.environ["LLM_BASE_URL"] = "https://example.invalid/v1"
        os.environ["LLM_MODEL"] = "my-model"
        base, model = get_llm_endpoint()
        assert base == "https://example.invalid/v1", base
        assert model == "my-model", model
    finally:
        if old_b is None:
            os.environ.pop("LLM_BASE_URL", None)
        else:
            os.environ["LLM_BASE_URL"] = old_b
        if old_m is None:
            os.environ.pop("LLM_MODEL", None)
        else:
            os.environ["LLM_MODEL"] = old_m


@test("R17 MINIMAX_API_KEY 可作为密钥来源（不输出密钥内容）")
def t_minimax_env_key():
    old = os.environ.get("MINIMAX_API_KEY")
    try:
        os.environ["MINIMAX_API_KEY"] = "test-minimax-key-000000"
        key = get_api_key()
        assert key == "test-minimax-key-000000", f"key len={len(key)}"
        base, model = get_llm_endpoint()
        assert "minimax" in base.lower(), base
        assert model, model
    finally:
        if old is None:
            os.environ.pop("MINIMAX_API_KEY", None)
        else:
            os.environ["MINIMAX_API_KEY"] = old


@test("R17 无密钥时返回空串且不抛异常")
def t_no_key():
    saved = {k: os.environ.pop(k, None) for k in
             ("DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "OPENCLAW_API_KEY", "LLM_API_KEY")}
    try:
        key = get_api_key()
        assert isinstance(key, str)
        base, model = get_llm_endpoint()
        assert base.startswith("http"), base
        assert model, model
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


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
    print(f"\n=== LLM 配置测试：{passed} 通过 / {failed} 失败 ===")
    sys.exit(1 if failed else 0)

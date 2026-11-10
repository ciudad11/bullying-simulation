"""
一键启动器 - 绕过 .bat 编码问题和安全拦截
NoReset 版启动器（默认端口 5001）
直接 python run.py 即可；可用 PORT 环境变量覆盖端口
"""
import os, sys, subprocess, time, webbrowser, threading

ROOT = os.path.dirname(os.path.abspath(__file__))

# 构建标识：启动时打印，避免「源码已改但线上仍是旧进程」无法识别
BUILD_ID = "2026-11-09-auto-end-fix-r2-nr1"

# NoReset 版默认端口；PORT 环境变量可覆盖（非法值直接报错退出）
DEFAULT_PORT = 5001


def resolve_port() -> int:
    raw = (os.environ.get("PORT") or "").strip() or str(DEFAULT_PORT)
    try:
        port = int(raw)
    except (TypeError, ValueError):
        print(f"[ERROR] PORT 环境变量非法: {raw!r}（应为 1-65535 的整数）")
        sys.exit(2)
    if not (1 <= port <= 65535):
        print(f"[ERROR] PORT 环境变量超出范围: {port}（应为 1-65535 的整数）")
        sys.exit(2)
    return port


def main():
    os.chdir(ROOT)
    print("=" * 45)
    print("  校园话语霸凌与教师干预实训系统（NoReset 版）")
    print(f"  BUILD: {BUILD_ID}")
    print("=" * 45)

    # 加载 .env（R17：项目根与 web/ 两个位置都尝试；utf-8-sig 兼容 BOM）
    env_loaded = False
    for env_path in (os.path.join(ROOT, ".env"), os.path.join(ROOT, "web", ".env")):
        if not os.path.exists(env_path):
            continue
        try:
            with open(env_path, "r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip().lstrip("\ufeff")
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ[k.strip().lstrip("\ufeff")] = v.strip().strip('"').strip("'")
            env_loaded = True
            print(f"[OK] 环境变量已加载: {env_path}")
        except Exception as e:
            print(f"[WARN] 读取 {env_path} 失败: {e}")
    if not env_loaded:
        print("[ERROR] 未找到可读的 .env 文件（项目根或 web/ 目录）")
        input("按 Enter 退出")
        return

    port = resolve_port()
    print(f"[OK] 监听端口: {port}")

    # 检查依赖
    missing = []
    for mod in ["fastapi", "uvicorn", "yaml", "jinja2", "dotenv", "openai", "langgraph"]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print(f"[WARN] 安装缺失依赖: {', '.join(missing)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "fastapi", "uvicorn", "pyyaml", "jinja2",
                               "python-dotenv", "openai", "langgraph",
                               "langchain-core", "langchain-openai", "httpx", "requests"])

    # 检查 API Key（R17：支持 DEEPSEEK / MINIMAX / OPENCLAW / LLM_API_KEY）
    if not any(os.environ.get(k) for k in
               ("DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "OPENCLAW_API_KEY", "LLM_API_KEY")):
        print("[ERROR] 未配置任何可用 API Key（DEEPSEEK_API_KEY / MINIMAX_API_KEY / OPENCLAW_API_KEY）")
        input("按 Enter 退出")
        return
    print("[OK] API Key: 已配置")

    print(f"\n启动服务...\n")
    print(f"访问地址: http://localhost:{port}")
    print("按 Ctrl+C 停止服务\n")

    # 3秒后自动打开浏览器
    def open_browser():
        time.sleep(3)
        webbrowser.open(f"http://localhost:{port}")
    threading.Thread(target=open_browser, daemon=True).start()

    # 启动 uvicorn
    sys.path.insert(0, os.path.join(ROOT, "web"))
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n服务已停止")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        input("按 Enter 退出")

# 校园话语霸凌与教师干预实训系统 — 部署手册

## 版本信息

- **打包日期**: 2026-07-29
- **版本号**: v8
- **Python 要求**: 3.11 ~ 3.12（推荐 3.11.x）
- **操作系统**: Windows / macOS / Linux

---

## 目录结构

```
bully_simulation_v8_20260729/
├── README.md              ← 本文件
├── start_local.bat         ← Windows 一键本地启动
├── start_public.bat        ← Windows 一键公网启动（Cloudflare隧道）
├── startup.ps1             ← 本地启动脚本
├── startup_public.ps1      ← 公网启动脚本
├── fin_ver/                ← 标准版（端口 5000）
│   ├── run.py              ← 启动入口
│   ├── requirements.txt
│   ├── .env.example        ← API Key 模板
│   ├── web/                ← Web 前端 (FastAPI)
│   ├── src/                ← 业务逻辑
│   ├── config/             ← 配置文件
│   └── docs/               ← 文档
└── noreset/                ← 无重置版（端口 5001）
    └── ...（结构同上）
```

## 两种版本的区别

| 特性 | fin_ver（标准版） | noreset（无重置版） |
|------|------------------|-------------------|
| 端口 | 5000 | 5001 |
| 预设干预话术 | ✅ 包含 | ❌ 仅自定义 |
| 场景重置 | 支持 | 不支持 |
| 适用场景 | 日常实训 | 对比测试 |

**可同时运行**，分别访问 `http://localhost:5000` 和 `http://localhost:5001`。

---

## 一、快速安装（Windows / macOS / Linux 通用）

### 前提条件

1. 安装 **Python 3.11 或 3.12**
   - Windows：https://www.python.org/downloads/ （安装时勾选 "Add Python to PATH"）
   - macOS：`brew install python@3.11`
   - Linux：`sudo apt install python3.11 python3.11-venv`

2. **获取 Minimax API Key**
   - 注册 Minimax 平台：https://platform.minimaxi.com
   - 在控制台创建 API Key

### Windows 一键启动

#### 本地启动（局域网访问）

双击 `start_local.bat`：
- 自动检查 Python 环境
- 首次运行提示输入 API Key（格式如 `sk-cp-xxxxx`）
- 自动安装缺失的 Python 依赖
- 启动 fin_ver (5000) 和 noreset (5001)
- 自动打开浏览器访问 `http://localhost:5000`
- 按 ESC 停止所有服务

#### 公网启动（外网访问）

双击 `start_public.bat`：
- 前提：已安装 **cloudflared**（一键安装：`winget install cloudflared`）
- 启动两个服务 + Cloudflare 快速隧道
- 自动获取 `*.trycloudflare.com` 公网地址并显示
- 按 ESC 停止所有服务

### macOS / Linux 手动启动

```bash
# 1. 克隆或解压项目到本地

# 2. 配置 API Key
cp fin_ver/.env.example fin_ver/.env
cp noreset/.env.example noreset/.env
# 编辑 .env 文件，填入 MINIMAX_API_KEY

# 3. 安装依赖
pip install -r fin_ver/requirements.txt
pip install -r noreset/requirements.txt

# 4. 启动服务
python fin_ver/run.py &          # 标准版 :5000
python noreset/run.py &          # 无重置版 :5001

# 5. 浏览器打开
# http://localhost:5000 （标准版）
# http://localhost:5001 （无重置版）
```

### 公网访问（macOS / Linux）

```bash
# 安装 cloudflared
# macOS:  brew install cloudflared
# Linux:  从 https://github.com/cloudflare/cloudflared/releases 下载

# 启动隧道
cloudflared tunnel --url http://localhost:5000 --protocol http2 &
cloudflared tunnel --url http://localhost:5001 --protocol http2 &
# 终端输出中查找 *.trycloudflare.com 公网地址
```

---

## 二、使用流程

1. 打开浏览器访问系统（`http://localhost:5000` 或 `http://localhost:5001`）
2. 首次使用需要**注册账号**（用户名 + 密码）
3. 登录后选择霸凌场景和难度等级（级别 1-5）
4. 自定义参与角色和个性特征（可选）
5. 选择干预话术（可选）
6. 点击「开始模拟」
7. 观察学生对话，在适当时机选择或输入干预话术
8. 点击「干预」发送教师介入
9. 点击「跳过干预」直接进入下一轮
10. 点击「停止」结束实训
11. 实训结束后查看评估报告
12. 可下载互动日志（Markdown）和评估报告（文本）

### 管理后台

管理员账号：
- 注册后需手动设置 `is_admin = 1`（通过数据库或由现有管理员在后台设置）
- 访问 `http://localhost:5000/admin.html`（需管理员权限）
- 查看所有用户的会话、报告、日志
- 批量清理废弃会话

---

## 三、常见问题

### Q: 启动报错 `No module named 'xxx'`
```bash
pip install -r fin_ver/requirements.txt
pip install -r noreset/requirements.txt
```

### Q: Python 3.13 报 DLL 加载错误
Python 3.13 缺少 `orjson` 预编译包。**建议使用 Python 3.11 或 3.12**。

### Q: 如何让局域网其他设备访问？
用本机 IP 替换 localhost：
```
http://192.168.x.x:5000
```
Windows 防火墙可能需要允许 Python 入站连接。

### Q: Cloudflare 隧道连不上
1. 确认 `cloudflared` 已安装：`cloudflared version`
2. 确认网络能访问 `trycloudflare.com`
3. 隧道 URL 有效期仅当次运行，重启后更换

### Q: 如何修改默认管理员？
使用 sqlite3 命令行或图形工具打开 `web/data.db`：
```sql
UPDATE users SET is_admin = 1 WHERE username = '你的用户名';
```
然后重启服务。

### Q: 两个版本有什么区别？
标准版提供完整的预设干预话术库（19条）；无重置版仅保留自定义干预，适合对比教学效果。

---

## 四、技术架构

```
浏览器 ──→ FastAPI (web/app.py) ──→ SessionManager
                                        │
                                        ├──→ StepRunner (对话引擎)
                                        ├──→ MultiAgent (LangGraph)
                                        ├──→ Minimax/OpenClaw LLM Client
                                        └──→ Report (评估报告生成)

数据层:
  SQLite (web/data.db) — 用户/会话/报告/日志/干预库
  内存 sessions dict — 活跃会话运行时状态
```

---

## 五、配置文件说明

| 文件 | 用途 |
|------|------|
| `web/app.py` | FastAPI 主入口，API 路由 |
| `web/session_manager.py` | 会话生命周期管理 |
| `web/database.py` | SQLite 数据库操作层 |
| `src/multi_agent/` | 多智能体模块（LangGraph） |
| `src/multi_agent/prompts.py` | 系统提示词模板 |
| `src/multi_agent/graph_builder.py` | 对话图构建 |
| `src/multi_agent/agents.py` | Agent 定义 |
| `src/multi_agent/supervisor.py` | 对话调度器 |
| `src/multi_agent/thinking_filter.py` | LLM 思考过程过滤器 |
| `src/intervention_library.json` | 干预话术库 |
| `config/simulation.yaml` | 模拟参数配置 |

---

## 六、联系方式

如有问题，联系项目维护者。

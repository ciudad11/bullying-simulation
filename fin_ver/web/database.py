#!/usr/bin/env python3
"""
数据库模块 - 用户认证、会话、报告、日志持久化
============================================
SQLite 单文件数据库，零额外依赖。两个程序各自独立 DB。
"""

import sqlite3
import json
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict
from threading import Lock


# ============================================================
# 数据库路径
# ============================================================

DB_DIR = Path(__file__).parent
DB_PATH = DB_DIR / "data.db"


# ============================================================
# 连接管理
# ============================================================

_write_lock = Lock()


def get_conn() -> sqlite3.Connection:
    """获取数据库连接（自动创建目录）"""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ============================================================
# 表初始化
# ============================================================

def init_db():
    """创建所有表（幂等）"""
    conn = get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          TEXT PRIMARY KEY,
                username    TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT DEFAULT '',
                email       TEXT DEFAULT '',
                is_admin    INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS auth_tokens (
                token       TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS user_interventions (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    title       TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    category    TEXT DEFAULT '自定义',
                    tags        TEXT DEFAULT '[]',
                    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_interventions (
                    id          TEXT PRIMARY KEY,
                    title       TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    category    TEXT DEFAULT '自定义',
                    tags        TEXT DEFAULT '[]',
                    created_by  TEXT DEFAULT '',
                    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS user_scenarios (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    title       TEXT NOT NULL,
                    description TEXT NOT NULL,
                    level       INTEGER DEFAULT 2,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_scenarios (
                    id          TEXT PRIMARY KEY,
                    title       TEXT NOT NULL,
                    description TEXT NOT NULL,
                    level       INTEGER DEFAULT 2,
                    created_by  TEXT DEFAULT '',
                    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS user_participants (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    role        TEXT DEFAULT '',
                    role_name   TEXT DEFAULT '',
                    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS shared_participants (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    role        TEXT DEFAULT '',
                    role_name   TEXT DEFAULT '',
                    created_by  TEXT DEFAULT '',
                    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                level       INTEGER DEFAULT 2,
                scenario_id TEXT DEFAULT '',
                scenario_title TEXT DEFAULT '',
                status      TEXT DEFAULT 'running',
                result_type TEXT DEFAULT '',
                auto_end_reason TEXT DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                completed_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reports (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                user_id     TEXT NOT NULL,
                report_json TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS logs (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                user_id     TEXT NOT NULL,
                log_markdown TEXT DEFAULT '',
                log_json    TEXT DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_reports_session ON reports(session_id);
            CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_logs_session ON logs(session_id);
            CREATE INDEX IF NOT EXISTS idx_logs_user ON logs(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_user_interventions_user ON user_interventions(user_id);
            CREATE INDEX IF NOT EXISTS idx_user_scenarios_user ON user_scenarios(user_id);
            CREATE INDEX IF NOT EXISTS idx_user_participants_user ON user_participants(user_id);
        """)
        conn.commit()

        # Migration: add role column if missing (v4.1+)
        try:
            conn.execute("ALTER TABLE user_participants ADD COLUMN role TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE shared_participants ADD COLUMN role TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


# ============================================================
# 密码工具
# ============================================================

def hash_password(password: str) -> str:
    """密码哈希（SHA-256 + salt）"""
    salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{h}"


def verify_password(password: str, stored: str) -> bool:
    """验证密码"""
    try:
        salt, h = stored.split(":", 1)
        return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest() == h
    except Exception:
        return False


def generate_token() -> str:
    """生成随机 Token"""
    return secrets.token_hex(32)


# ============================================================
# 用户操作
# ============================================================

def create_user(username: str, password: str, display_name: str = "", email: str = "") -> Dict:
    """创建用户，成功返回用户信息，失败 raise"""
    conn = get_conn()
    try:
        user_id = f"u_{secrets.token_hex(8)}"
        pwd_hash = hash_password(password)
        conn.execute(
            "INSERT INTO users (id, username, password_hash, display_name, email) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, pwd_hash, display_name, email)
        )
        conn.commit()
        return {
            "id": user_id,
            "username": username,
            "display_name": display_name,
            "email": email,
            "is_admin": False
        }
    except sqlite3.IntegrityError:
        raise ValueError("用户名已存在")
    finally:
        conn.close()


def authenticate(username: str, password: str) -> Optional[Dict]:
    """登录验证，成功返回用户信息，失败返回 None"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not row:
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        return dict(row)
    finally:
        conn.close()


def get_user_by_id(user_id: str) -> Optional[Dict]:
    """根据 ID 获取用户"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_users() -> List[Dict]:
    """获取所有用户"""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT id, username, display_name, email, is_admin, created_at FROM users ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ============================================================
# Token 管理
# ============================================================

def create_token(user_id: str, expires_hours: int = 24) -> str:
    """创建并保存 Token，返回 token 字符串"""
    conn = get_conn()
    try:
        token = generate_token()
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=expires_hours)).isoformat()
        conn.execute(
            "INSERT INTO auth_tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at)
        )
        conn.commit()
        return token
    finally:
        conn.close()


def verify_token(token: str) -> Optional[Dict]:
    """验证 Token，成功返回用户信息，过期/无效返回 None"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT t.token, t.expires_at, u.id, u.username, u.display_name, u.email, u.is_admin "
            "FROM auth_tokens t JOIN users u ON t.user_id = u.id "
            "WHERE t.token = ?", (token,)
        ).fetchone()
        if not row:
            return None

        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at < datetime.now(timezone.utc):
            # Token 过期，清理
            conn.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))
            conn.commit()
            return None

        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "email": row["email"],
            "is_admin": bool(row["is_admin"])
        }
    finally:
        conn.close()


def delete_token(token: str):
    """登出时删除 Token"""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def cleanup_expired_tokens():
    """清理过期 Token"""
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM auth_tokens WHERE expires_at < ?",
            (datetime.now(timezone.utc).isoformat(),)
        )
        conn.commit()
    finally:
        conn.close()


# ============================================================
# 会话操作
# ============================================================

def create_session(user_id: str, level: int, scenario_id: str = "", scenario_title: str = "") -> str:
    """创建会话记录，返回 session_id"""
    conn = get_conn()
    try:
        session_id = f"s_{secrets.token_hex(12)}"
        conn.execute(
            "INSERT INTO sessions (id, user_id, level, scenario_id, scenario_title, status) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, user_id, level, scenario_id, scenario_title, "running")
        )
        conn.commit()
        return session_id
    finally:
        conn.close()


def update_session_status(session_id: str, status: str, result_type: str = "", auto_end_reason: str = ""):
    """更新会话状态"""
    conn = get_conn()
    try:
        updates = ["status = ?"]
        params = [status]
        if result_type:
            updates.append("result_type = ?")
            params.append(result_type)
        if auto_end_reason:
            updates.append("auto_end_reason = ?")
            params.append(auto_end_reason)
        if status in ("completed", "stopped", "auto_ended"):
            updates.append("completed_at = datetime('now','localtime')")
        params.append(session_id)
        conn.execute(f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def get_user_sessions(user_id: str, limit: int = 50) -> List[Dict]:
    """获取用户的所有会话"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ============================================================
# 报告操作
# ============================================================

def save_report(session_id: str, user_id: str, report_data: Dict) -> str:
    """保存报告，返回 report_id"""
    conn = get_conn()
    try:
        report_id = f"r_{secrets.token_hex(8)}"
        report_json = json.dumps(report_data, ensure_ascii=False)
        conn.execute(
            "INSERT INTO reports (id, session_id, user_id, report_json) VALUES (?, ?, ?, ?)",
            (report_id, session_id, user_id, report_json)
        )
        conn.commit()
        return report_id
    finally:
        conn.close()


def get_report_by_session(session_id: str) -> Optional[Dict]:
    """获取某个会话的报告"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM reports WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["report_data"] = json.loads(result["report_json"])
        return result
    finally:
        conn.close()


def get_user_reports(user_id: str, limit: int = 50) -> List[Dict]:
    """获取用户的所有报告"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM reports WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["report_data"] = json.loads(d["report_json"])
            results.append(d)
        return results
    finally:
        conn.close()


# ============================================================
# 日志操作
# ============================================================

def save_log(session_id: str, user_id: str, log_markdown: str = "", log_json: str = "") -> str:
    """保存日志，返回 log_id"""
    conn = get_conn()
    try:
        log_id = f"l_{secrets.token_hex(8)}"
        conn.execute(
            "INSERT INTO logs (id, session_id, user_id, log_markdown, log_json) VALUES (?, ?, ?, ?, ?)",
            (log_id, session_id, user_id, log_markdown, log_json)
        )
        conn.commit()
        return log_id
    finally:
        conn.close()


def get_log_by_session(session_id: str) -> Optional[Dict]:
    """获取某个会话的日志"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM logs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_log(session_id: str, user_id: str, log_markdown: str, log_json: str = "") -> str:
    """实时更新日志（INSERT OR REPLACE），返回 log_id"""
    conn = get_conn()
    try:
        import secrets as _secrets
        existing = conn.execute("SELECT id FROM logs WHERE session_id = ?", (session_id,)).fetchone()
        if existing:
            log_id = existing["id"]
            conn.execute(
                "UPDATE logs SET log_markdown = ?, log_json = ?, created_at = datetime('now','localtime') WHERE id = ?",
                (log_markdown, log_json, log_id)
            )
        else:
            log_id = f"l_{_secrets.token_hex(8)}"
            conn.execute(
                "INSERT INTO logs (id, session_id, user_id, log_markdown, log_json) VALUES (?, ?, ?, ?, ?)",
                (log_id, session_id, user_id, log_markdown, log_json)
            )
        conn.commit()
        return log_id
    finally:
        conn.close()


def upsert_report_snapshot(session_id: str, user_id: str, report_data: Dict) -> str:
    """实时更新报告快照（INSERT OR REPLACE），返回 report_id"""
    conn = get_conn()
    try:
        import secrets as _secrets
        report_json = json.dumps(report_data, ensure_ascii=False)
        existing = conn.execute("SELECT id FROM reports WHERE session_id = ?", (session_id,)).fetchone()
        if existing:
            report_id = existing["id"]
            conn.execute(
                "UPDATE reports SET report_json = ?, created_at = datetime('now','localtime') WHERE id = ?",
                (report_json, report_id)
            )
        else:
            report_id = f"r_{_secrets.token_hex(8)}"
            conn.execute(
                "INSERT INTO reports (id, session_id, user_id, report_json) VALUES (?, ?, ?, ?)",
                (report_id, session_id, user_id, report_json)
            )
        conn.commit()
        return report_id
    finally:
        conn.close()


def mark_abandoned_sessions():
    """启动时将 running 状态的会话标记为 abandoned"""
    conn = get_conn()
    try:
        count = conn.execute(
            "UPDATE sessions SET status = 'abandoned' WHERE status = 'running'"
        ).rowcount
        if count:
            conn.commit()
            print(f"[DB] 标记 {count} 个 abandoned 会话")
    finally:
        conn.close()


def get_user_logs(user_id: str, limit: int = 50) -> List[Dict]:
    """获取用户的所有日志"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM logs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ============================================================
# 管理员查询
# ============================================================

def admin_get_all_sessions(limit: int = 100) -> List[Dict]:
    """管理员：获取所有会话"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT s.*, u.username, u.display_name "
            "FROM sessions s JOIN users u ON s.user_id = u.id "
            "ORDER BY s.created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def admin_get_user_detail(user_id: str) -> Optional[Dict]:
    """管理员：获取用户详细信息（含所有 session、report、log）"""
    conn = get_conn()
    try:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return None

        sessions = conn.execute(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()

        result = dict(user)
        result["sessions"] = []
        for s in sessions:
            s_dict = dict(s)
            # 获取报告
            report = conn.execute(
                "SELECT report_json, created_at FROM reports WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (s["id"],)
            ).fetchone()
            if report:
                s_dict["report"] = {"data": json.loads(report["report_json"]), "created_at": report["created_at"]}

            # 获取日志
            log = conn.execute(
                "SELECT log_markdown, log_json, created_at FROM logs WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (s["id"],)
            ).fetchone()
            if log:
                s_dict["log"] = {"markdown": log["log_markdown"], "json_data": log["log_json"], "created_at": log["created_at"]}

            result["sessions"].append(s_dict)

        return result
    finally:
        conn.close()


# ============================================================
# 自定义资源（干预话术 / 情景 / 参与角色）
# 权限模型：管理员 → shared 表（全局可见），普通用户 → user 表（仅自己可见）
# ============================================================

# ── 干预话术 ──

def get_user_interventions(user_id: str) -> List[Dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, content, category, tags, created_at FROM user_interventions WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
        return [_parse_intervention_row(r) for r in rows]
    finally:
        conn.close()


def get_shared_interventions() -> List[Dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, content, category, tags, created_at FROM shared_interventions ORDER BY created_at DESC"
        ).fetchall()
        return [_parse_intervention_row(r) for r in rows]
    finally:
        conn.close()


def _parse_intervention_row(r) -> Dict:
    d = dict(r)
    try:
        d["tags"] = json.loads(d["tags"]) if d["tags"] else []
    except Exception:
        d["tags"] = [d["tags"]] if d["tags"] else []
    return d


def create_user_intervention(user_id: str, intervention_id: str, title: str, content: str,
                              category: str = "自定义", tags: list = None) -> Dict:
    conn = get_conn()
    try:
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        conn.execute(
            "INSERT INTO user_interventions (id, user_id, title, content, category, tags) VALUES (?, ?, ?, ?, ?, ?)",
            (intervention_id, user_id, title, content, category, tags_json)
        )
        conn.commit()
        return {"id": intervention_id, "title": title, "content": content, "category": category, "tags": tags or []}
    except sqlite3.IntegrityError:
        raise ValueError("干预ID已存在")
    finally:
        conn.close()


def create_shared_intervention(intervention_id: str, title: str, content: str,
                                category: str = "自定义", tags: list = None, created_by: str = "") -> Dict:
    conn = get_conn()
    try:
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        conn.execute(
            "INSERT INTO shared_interventions (id, title, content, category, tags, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (intervention_id, title, content, category, tags_json, created_by)
        )
        conn.commit()
        return {"id": intervention_id, "title": title, "content": content, "category": category, "tags": tags or []}
    except sqlite3.IntegrityError:
        raise ValueError("干预ID已存在")
    finally:
        conn.close()


def update_user_intervention(user_id: str, intervention_id: str, title: str = None,
                              content: str = None, category: str = None, tags: list = None) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM user_interventions WHERE id = ? AND user_id = ?", (intervention_id, user_id)
        ).fetchone()
        if not row:
            return False
        _do_update_intervention(conn, "user_interventions", intervention_id, user_id, title, content, category, tags)
        return True
    finally:
        conn.close()


def update_shared_intervention(intervention_id: str, title: str = None,
                                content: str = None, category: str = None, tags: list = None) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM shared_interventions WHERE id = ?", (intervention_id,)).fetchone()
        if not row:
            return False
        _do_update_intervention(conn, "shared_interventions", intervention_id, None, title, content, category, tags)
        return True
    finally:
        conn.close()


def _do_update_intervention(conn, table: str, intervention_id: str, user_id: str,
                             title, content, category, tags):
    updates, params = [], []
    for col, val in [("title", title), ("content", content), ("category", category)]:
        if val is not None:
            updates.append(f"{col} = ?")
            params.append(val)
    if tags is not None:
        updates.append("tags = ?")
        params.append(json.dumps(tags, ensure_ascii=False))
    if not updates:
        return
    params.append(intervention_id)
    where = "id = ? AND user_id = ?" if user_id else "id = ?"
    if user_id:
        params.append(user_id)
    conn.execute(f"UPDATE {table} SET {', '.join(updates)} WHERE {where}", params)
    conn.commit()


def delete_user_intervention(user_id: str, intervention_id: str) -> bool:
    conn = get_conn()
    try:
        c = conn.execute("DELETE FROM user_interventions WHERE id = ? AND user_id = ?",
                         (intervention_id, user_id))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


def delete_shared_intervention(intervention_id: str) -> bool:
    conn = get_conn()
    try:
        c = conn.execute("DELETE FROM shared_interventions WHERE id = ?", (intervention_id,))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


# ── 情景 ──

def get_user_scenarios(user_id: str) -> List[Dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, description, level, created_at FROM user_scenarios WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_shared_scenarios() -> List[Dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, title, description, level, created_at FROM shared_scenarios ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_user_scenario(user_id: str, scenario_id: str, title: str, description: str, level: int = 2) -> Dict:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO user_scenarios (id, user_id, title, description, level) VALUES (?, ?, ?, ?, ?)",
            (scenario_id, user_id, title, description, level)
        )
        conn.commit()
        return {"id": scenario_id, "title": title, "description": description, "level": level}
    except sqlite3.IntegrityError:
        raise ValueError("情景ID已存在")
    finally:
        conn.close()


def create_shared_scenario(scenario_id: str, title: str, description: str, level: int = 2, created_by: str = "") -> Dict:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO shared_scenarios (id, title, description, level, created_by) VALUES (?, ?, ?, ?, ?)",
            (scenario_id, title, description, level, created_by)
        )
        conn.commit()
        return {"id": scenario_id, "title": title, "description": description, "level": level}
    except sqlite3.IntegrityError:
        raise ValueError("情景ID已存在")
    finally:
        conn.close()


def update_user_scenario(user_id: str, scenario_id: str, title: str = None,
                          description: str = None, level: int = None) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM user_scenarios WHERE id = ? AND user_id = ?", (scenario_id, user_id)
        ).fetchone()
        if not row:
            return False
        _do_update_scenario(conn, "user_scenarios", scenario_id, user_id, title, description, level)
        return True
    finally:
        conn.close()


def update_shared_scenario(scenario_id: str, title: str = None,
                            description: str = None, level: int = None) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM shared_scenarios WHERE id = ?", (scenario_id,)).fetchone()
        if not row:
            return False
        _do_update_scenario(conn, "shared_scenarios", scenario_id, None, title, description, level)
        return True
    finally:
        conn.close()


def _do_update_scenario(conn, table: str, scenario_id: str, user_id, title, description, level):
    updates, params = [], []
    for col, val in [("title", title), ("description", description)]:
        if val is not None:
            updates.append(f"{col} = ?")
            params.append(val)
    if level is not None:
        updates.append("level = ?")
        params.append(level)
    if not updates:
        return
    params.append(scenario_id)
    where = "id = ? AND user_id = ?" if user_id else "id = ?"
    if user_id:
        params.append(user_id)
    conn.execute(f"UPDATE {table} SET {', '.join(updates)} WHERE {where}", params)
    conn.commit()


def delete_user_scenario(user_id: str, scenario_id: str) -> bool:
    conn = get_conn()
    try:
        c = conn.execute("DELETE FROM user_scenarios WHERE id = ? AND user_id = ?", (scenario_id, user_id))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


def delete_shared_scenario(scenario_id: str) -> bool:
    conn = get_conn()
    try:
        c = conn.execute("DELETE FROM shared_scenarios WHERE id = ?", (scenario_id,))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


# ── 参与角色 ──

def get_user_participants(user_id: str) -> List[Dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, role, role_name, personality, created_at FROM user_participants WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d['personality'] = _parse_personality(d.get('personality'))
            d['roleName'] = d.pop('role_name', '')
            result.append(d)
        return result
    finally:
        conn.close()


def get_user_participant(pid: str) -> Optional[Dict]:
    """Get a single user participant by id."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, name, role, role_name, personality, created_at, user_id FROM user_participants WHERE id = ?", (pid,)
        ).fetchone()
        if row:
            d = dict(row)
            d['personality'] = _parse_personality(d.get('personality'))
            d['roleName'] = d.pop('role_name', '')
            return d
        return None
    finally:
        conn.close()


def get_shared_participant(pid: str) -> Optional[Dict]:
    """Get a single shared participant by id."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, name, role, role_name, personality, created_at FROM shared_participants WHERE id = ?", (pid,)
        ).fetchone()
        if row:
            d = dict(row)
            d['personality'] = _parse_personality(d.get('personality'))
            d['roleName'] = d.pop('role_name', '')
            return d
        return None
    finally:
        conn.close()


def get_shared_participants() -> List[Dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, role, role_name, personality, created_at FROM shared_participants ORDER BY created_at DESC"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d['personality'] = _parse_personality(d.get('personality'))
            d['roleName'] = d.pop('role_name', '')
            result.append(d)
        return result
    finally:
        conn.close()


def create_user_participant(user_id: str, pid: str, name: str, role_name: str = "", role: str = "", personality: dict = None) -> Dict:
    conn = get_conn()
    try:
        pers_json = json.dumps(personality or {}, ensure_ascii=False)
        conn.execute(
            "INSERT INTO user_participants (id, user_id, name, role, role_name, personality) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, user_id, name, role, role_name, pers_json)
        )
        conn.commit()
        return {"id": pid, "name": name, "role": role, "roleName": role_name, "personality": personality or {}}
    except sqlite3.IntegrityError:
        raise ValueError("角色ID已存在")
    finally:
        conn.close()


def create_shared_participant(pid: str, name: str, role_name: str = "", role: str = "", created_by: str = "", personality: dict = None) -> Dict:
    conn = get_conn()
    try:
        pers_json = json.dumps(personality or {}, ensure_ascii=False)
        conn.execute(
            "INSERT INTO shared_participants (id, name, role, role_name, created_by, personality) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, name, role, role_name, created_by, pers_json)
        )
        conn.commit()
        return {"id": pid, "name": name, "role": role, "roleName": role_name, "personality": personality or {}}
    except sqlite3.IntegrityError:
        raise ValueError("角色ID已存在")
    finally:
        conn.close()


def update_user_participant(user_id: str, pid: str, name: str = None, role_name: str = None, role: str = None, personality: dict = None) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM user_participants WHERE id = ? AND user_id = ?", (pid, user_id)
        ).fetchone()
        if not row:
            return False
        _do_update_participant(conn, "user_participants", pid, user_id, name, role_name, role, personality)
        return True
    finally:
        conn.close()


def update_shared_participant(pid: str, name: str = None, role_name: str = None, role: str = None, personality: dict = None) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM shared_participants WHERE id = ?", (pid,)).fetchone()
        if not row:
            return False
        _do_update_participant(conn, "shared_participants", pid, None, name, role_name, role, personality)
        return True
    finally:
        conn.close()


def _parse_personality(val) -> dict:
    """Parse personality JSON string from DB, return dict."""
    if not val:
        return {}
    if isinstance(val, dict):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return {}


def _do_update_participant(conn, table: str, pid: str, user_id, name, role_name, role=None, personality=None):
    updates, params = [], []
    for col, val in [("name", name), ("role", role), ("role_name", role_name)]:
        if val is not None:
            updates.append(f"{col} = ?")
            params.append(val)
    if personality is not None:
        updates.append("personality = ?")
        params.append(json.dumps(personality, ensure_ascii=False))
    if not updates:
        return
    params.append(pid)
    where = "id = ? AND user_id = ?" if user_id else "id = ?"
    if user_id:
        params.append(user_id)
    conn.execute(f"UPDATE {table} SET {', '.join(updates)} WHERE {where}", params)
    conn.commit()


def delete_user_participant(user_id: str, pid: str) -> bool:
    conn = get_conn()
    try:
        c = conn.execute("DELETE FROM user_participants WHERE id = ? AND user_id = ?", (pid, user_id))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


def delete_shared_participant(pid: str) -> bool:
    conn = get_conn()
    try:
        c = conn.execute("DELETE FROM shared_participants WHERE id = ?", (pid,))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


# ============================================================
# 启动时初始化
# ============================================================

init_db()

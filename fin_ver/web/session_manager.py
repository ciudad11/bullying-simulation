"""
SessionManager - 适配层
将新的 MultiAgentRunner 适配到现有的 Session 接口
保留所有现有功能（日志、报告、配置加载）
"""

import sys
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / ".." / "src"))

import uuid
import time
import random
import json
import asyncio
from datetime import datetime
from typing import Dict, List, Optional


# ============================================================
# 尝试导入新的 LangGraph 多Agent系统
# ============================================================

try:
    from multi_agent import create_runner, MultiAgentRunner
    NEW_MULTI_AGENT_AVAILABLE = True
except ImportError as e:
    print(f"Warning: New multi_agent module not available: {e}")
    NEW_MULTI_AGENT_AVAILABLE = False

# R14/R15：用户指导引擎（逐轮分析 / 改进方案 / 实时指导 / 连续低分预警）
try:
    from multi_agent.coaching import (
        build_round_analysis,
        build_improvement_plan,
        build_real_time_coaching,
        build_streak_alert,
        RISK_GUIDE,
    )
    COACHING_AVAILABLE = True
except ImportError as e:
    print(f"Warning: coaching module not available: {e}")
    COACHING_AVAILABLE = False

# R19：教师实训评估报告构建器（总体结论/能力画像/失分归因/误区对照/训练路线/话术/自检/评分速查）
try:
    from multi_agent.report_builder import build_training_report
    REPORT_BUILDER_AVAILABLE = True
except ImportError as e:
    print(f"Warning: report_builder module not available: {e}")
    REPORT_BUILDER_AVAILABLE = False

# R20：规范化报告数据合同（唯一 report_data：版本/评分状态/维度四态/证据引用/百分点指标）
try:
    from report_contract import build_report_data, record_report_meta
    REPORT_CONTRACT_AVAILABLE = True
except ImportError as e:
    print(f"Warning: report_contract not available: {e}")
    REPORT_CONTRACT_AVAILABLE = False

# 指导阈值：低于该分数视为未达标
LOW_SCORE_THRESHOLD = 60
# 连续多少轮未达标触发即时预警横幅
LOW_STREAK_MIN = 3
# R15：连续多少轮未达标才开始展示情境化提示（最小限度帮助，不给答案）
HINT_STREAK_MIN = 2
# R20（计划书 §9.1）：模拟状态趋势稳定阈值——变化不超过该百分点数视为基本持平
STABLE_THRESHOLD_PP = 2.0


# ============================================================
# 配置加载（复用 orchestrator.yaml）
# ============================================================

def load_config() -> dict:
    """加载 orchestrator.yaml 配置"""
    # 尝试多个可能的路径
    possible_paths = [
        Path(__file__).parent.parent / "config" / "orchestrator.yaml",
        Path(__file__).parent.parent.parent / "config" / "orchestrator.yaml",
        Path("config/orchestrator.yaml"),
        Path("../config/orchestrator.yaml"),
        Path(__file__).parent.parent / "src" / "orchestrator.yaml",
        Path("src/orchestrator.yaml"),
    ]

    for config_path in possible_paths:
        if config_path.exists():
            try:
                import yaml
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                    print(f"[Config] Loaded from {config_path}")
                    return config
            except ImportError:
                print("Warning: yaml module not available, using default config")
                return {}
            except Exception as e:
                print(f"Warning: Failed to load config from {config_path}: {e}")
                continue

    print("Warning: orchestrator.yaml not found, using default config")
    return {}


# ============================================================
# SessionManager - 兼容层
# ============================================================

class SessionManager:
    """
    会话管理器
    封装新的 MultiAgentRunner，提供与旧接口兼容的方法
    """

    def __init__(
        self,
        session_id: str,
        level: int = 2,
        scenario: dict = None,
        use_llm: bool = True,
        config: dict = None,
        custom_participants: dict = None,
    ):
        self.session_id = session_id
        self.level = level
        self.scenario = scenario
        self.use_llm = use_llm
        self.config = config or load_config()
        self.custom_participants = custom_participants or {}

        self.round = 0
        self.messages: List[Dict] = []
        self.interventions: List[Dict] = []
        self.skip_decisions: List[Dict] = []  # 记录每轮跳过干预的决策
        self.started_at = datetime.now()

        # 并发控制：每会话互斥锁 + 运行/停止标志
        self._run_lock = threading.Lock()
        self._is_running = False
        self._stop_requested = False

        # 终止条件跟踪
        self.auto_ended = False
        self.auto_end_reason = ""
        self.result_type = ""
        self.AUTO_END_CONFIG = {
            "bullying_stopped_rounds": self.config.get("bullying_stopped_rounds", 3),
            "intervention_effective_rounds": self.config.get("intervention_effective_rounds", 4),
            "victim_crisis_threshold": self.config.get("victim_crisis_threshold", 0.95),
            "victim_crisis_rounds": self.config.get("victim_crisis_rounds", 3),
            "max_rounds": self.config.get("max_rounds", 40),
            "min_rounds": self.config.get("min_rounds", 3),
        }

        # R16：指导触发门槛可配置（连续几轮低于阈值后展示提示/预警）
        _g_cfg = self.config.get("guidance", {}) if isinstance(self.config, dict) else {}
        _g_cfg = _g_cfg if isinstance(_g_cfg, dict) else {}
        self.GUIDANCE_HINT_MIN = int(_g_cfg.get("hint_min_rounds", HINT_STREAK_MIN))
        self.GUIDANCE_ALERT_MIN = int(_g_cfg.get("alert_min_rounds", LOW_STREAK_MIN))

        # 创建新的 MultiAgentRunner
        if NEW_MULTI_AGENT_AVAILABLE:
            self.runner: Optional[MultiAgentRunner] = create_runner(
                level=level,
                max_rounds=self.AUTO_END_CONFIG["max_rounds"],
                use_llm=use_llm,
                config=self.config,
                session_id=session_id,
                scenario=self.scenario,
                custom_participants=self.custom_participants,
            )
            print(f"[SessionManager] Using new LangGraph MultiAgentRunner (level={level})")
        else:
            self.runner = None
            print(f"[SessionManager] MultiAgentRunner not available, running in limited mode")

        # 初始化日志
        self._init_logging()

        # R14：连续低分预警追踪（连续 N 轮低于阈值时给出指导提示）
        self._low_streak = []
        # R17：连续「未获得有效评分」计数（评分服务不可用时也能给出基于情境的提示）
        self._unscored_streak = 0

    def _init_logging(self):
        """初始化日志目录"""
        self.log_dir = Path(__file__).parent.parent / "logs" / f"sim_{int(self.started_at.timestamp())}"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_log_file = self.log_dir / "session_log.json"
        self.session_md_file = self.log_dir / "session_log.md"

        self.session_data = {
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "level": self.level,
            "scenario": self.scenario,  # 保存原始 scenario（报告用）
            "use_llm": self.use_llm,
            "messages": [],
            "interventions": [],
            "skip_decisions": [],
            "metrics_history": [],
            # R8：内部状态轨迹（九项内部状态 + 残余效应），不能只保留汇总 B
            "internal_state_history": [],
        }

    def _save_log(self):
        """保存日志（磁盘 + 数据库实时同步）"""
        try:
            with open(self.session_log_file, "w", encoding="utf-8") as f:
                json.dump(self.session_data, f, ensure_ascii=False, indent=2)
            self._save_markdown()
        except Exception as e:
            print(f"[SessionManager] Log save failed: {e}")

        # 实时同步到数据库
        try:
            db_sid = getattr(self, '_db_session_id', None)
            db_uid = getattr(self, '_db_user_id', None)
            if db_sid and db_uid:
                log_md = ""
                try:
                    with open(self.session_md_file, "r", encoding="utf-8") as f:
                        log_md = f.read()
                except Exception:
                    pass
                log_json = json.dumps(self.session_data, ensure_ascii=False)
                from database import upsert_log
                upsert_log(db_sid, db_uid, log_md, log_json)
        except Exception as e:
            print(f"[SessionManager] DB log sync failed: {e}")

    def _save_markdown(self):
        """保存 Markdown 日志"""
        lines = [
            f"# 话语霸凌模拟会话记录",
            f"",
            f"**会话ID**: {self.session_id}",
            f"**开始时间**: {self.session_data['started_at']}",
            f"**霸凌级别**: {self.level}",
            f"**智能模式**: {'是' if self.use_llm else '否'}",
            f"",
        ]
        if self.scenario:
            lines.extend([
                f"## 会话情境",
                f"",
                f"**{self.scenario.get('title', '未命名情境')}**",
                f"",
                f"{self.scenario.get('description', '')}",
                f"",
            ])
        lines.extend([
            f"## 会话过程",
            f"",
        ])
        for msg in self.session_data.get("messages", []):
            icon = {"bully": "😈", "victim": "😢", "bystander_a": "✨", "bystander_b": "👀", "teacher": "👨‍🏫"}.get(msg.get("role", ""), "💬")
            lines.append(f"- **{msg.get('timestamp', '')}** [{msg.get('round', 0)}轮] {icon} **{msg.get('sender', '未知')}**: {msg.get('content', '')}")
        if self.session_data.get("interventions"):
            lines.extend(["", "## 教师干预", ""])
            for interv in self.session_data["interventions"]:
                lines.append(f"- **[第{interv.get('round', 0)}轮]**: {interv.get('content', '')}")
        if self.session_data.get("metrics_history"):
            lines.extend(["", "## 指标趋势", "", "| 轮次 | 霸凌强度 | 受害者压力 |", "|------|---------|-----------|"])
            for m in self.session_data["metrics_history"]:
                lines.append(f"| {m.get('round', 0)} | {m.get('bullying_intensity', 0):.0%} | {m.get('victim_distress', 0):.0%} |")
        try:
            with open(self.session_md_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            print(f"[SessionManager] Markdown save failed: {e}")

    def _sync_internal_history(self):
        """R8：把内部状态轨迹同步到会话日志（随 log_json 持久化，不能只保留汇总 B）"""
        try:
            if self.runner is not None:
                self.session_data["internal_state_history"] = list(
                    self.runner.state.internal_state_history)
        except Exception:
            pass

    def _guidance_progress(self, guidance_enabled: bool) -> Dict:
        """R16/R17：指导触发进度——让用户看得见「机制是否在累计」，
        避免出现「提示卡面为什么没出现」而无法自查的困惑。
        两种累计口径：低分连败（已评分且 <60）与未评分连败（评分服务不可用）。"""
        streak = len(self._low_streak)
        unscored = int(getattr(self, "_unscored_streak", 0))
        hint_min = self.GUIDANCE_HINT_MIN
        alert_min = self.GUIDANCE_ALERT_MIN
        remaining = max(0, hint_min - max(streak, unscored))
        if guidance_enabled and streak >= hint_min:
            message = (f"已连续 {streak} 轮低于 {LOW_SCORE_THRESHOLD} 分，"
                       f"已显示针对性指导（{alert_min} 轮起将给出预警）")
        elif guidance_enabled:
            message = (f"连续 {unscored} 轮未获得有效评分（专家评分不可用），"
                       f"已显示基于本轮情境的提示")
        elif streak > 0:
            message = (f"已连续 {streak} 轮低于 {LOW_SCORE_THRESHOLD} 分；"
                       f"再连续 {remaining} 轮将显示针对性指导")
        elif unscored > 0:
            message = (f"连续 {unscored} 轮未获得有效评分（专家评分不可用）；"
                       f"再连续 {remaining} 轮将显示基于本轮情境的提示")
        else:
            message = ""
        return {
            "streak": streak,
            "unscored_streak": unscored,
            "hint_threshold": hint_min,
            "alert_threshold": alert_min,
            "enabled": guidance_enabled,
            "remaining": remaining,
            "message": message,
        }

    def run_round(self) -> Dict:
        """运行一轮对话（线程安全，同一会话同时只允许一轮运行）"""
        if not self._run_lock.acquire(blocking=False):
            return {"error": "session_busy", "detail": "当前会话正忙，请稍后再试"}
        try:
            self._is_running = True
            return self._run_round_impl()
        finally:
            self._is_running = False
            self._run_lock.release()

    def _run_round_impl(self) -> Dict:
        """run_round 实际执行体（在锁保护下运行）"""
        if self.runner is None:
            return self._limited_run_round()

        # 添加情境消息（第一轮）- 同时添加到runner的state和显示消息列表
        # ★ 使用参数化后的 scenario（人名已替换为当前角色名）
        active_scenario = getattr(self.runner, '_param_scenario', None) or self.scenario
        if self.runner.state.round == 0 and active_scenario:
            scenario_content = f"[场景] {active_scenario.get('title', '')}：{active_scenario.get('description', '')}"
            
            # 添加到runner的state（让智能体能看到场景信息）
            from multi_agent.state import Message as StateMessage
            scenario_state_msg = StateMessage(
                sender_id="system",
                sender_name="系统",
                role="system",
                content=scenario_content,
                timestamp=datetime.now().strftime("%H:%M:%S"),
                round=0,
            )
            self.runner.state.messages.append(scenario_state_msg)
            
            # 添加到显示消息列表
            scenario_msg = {
                "id": "scenario_0",
                "sender": "System",
                "role": "system",
                "content": f"[Scenario] {self.scenario.get('title', '')}: {self.scenario.get('description', '')}",
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "hostile_score": 0,
                "emotions": [],
            }
            self.messages.append(scenario_msg)
            self.session_data["messages"].append(scenario_msg)

        # 调用 MultiAgentRunner（LangGraph 内部管理轮次）
        result = self.runner.run_round()

        # 同步轮次
        self.round = self.runner.state.round

        # R8：同步内部状态轨迹到会话日志
        self._sync_internal_history()

        # 检查终止条件
        if result.get("finished"):
            self.auto_ended = True
            self.auto_end_reason = result.get("termination_reason", "未知原因")
            self.result_type = result.get("result_type", "")

            # 终止可能发生在本轮评估之后（同一轮达成条件即结束），
            # 本轮已生成的消息不能丢，否则用户看不到最后一段对话。
            new_messages = result.get("messages", [])
            for msg in new_messages:
                self.messages.append(msg)
                self.session_data["messages"].append(msg)

            metrics = result.get("metrics", {})
            if metrics:
                self.session_data["metrics_history"].append({
                    "round": self.round,
                    "bullying_intensity": metrics.get("bullying_intensity", 0),
                    "victim_distress": metrics.get("victim_distress", 0),
                    "attack_frequency": metrics.get("attack_frequency", 0),
                    "obvq_dimensions": metrics.get("obvq_dimension_totals", [0]*6),
                })

            self._save_log()
            # P1 修复：终止轮也要刷新报告快照，否则 DB reports 停留上一轮
            #（round=4 / finished=False / 无 result_type），与服务端报告矛盾。
            self._persist_report_snapshot()
            return {
                "round": self.round,
                "done": True,
                "auto_ended": True,
                "auto_end_reason": self.auto_end_reason,
                "result_type": self.result_type,
                "status": self.runner.get_status(),
                "messages": new_messages,
                "metrics": metrics,
                "convergence": result.get("convergence"),
                "_conv_diag": result.get("_conv_diag"),
            }

        # 转换消息格式并记录日志
        # 只取本轮新增的消息（result["messages"] 已经是新增的消息）
        new_messages = result.get("messages", [])
        for msg in new_messages:
            self.messages.append(msg)
            self.session_data["messages"].append(msg)

        # 记录指标历史（含OBVQ维度分）
        metrics = result.get("metrics", {})
        self.session_data["metrics_history"].append({
            "round": self.round,
            "bullying_intensity": metrics.get("bullying_intensity", 0),
            "victim_distress": metrics.get("victim_distress", 0),
            "obvq_dimensions": metrics.get("obvq_dimension_totals", [0]*6),
        })

        self._save_log()

        # 每轮后实时保存报告快照到数据库（终止轮由终止分支同样调用本方法）
        self._persist_report_snapshot()

        return {
            "round": self.round,
            "done": False,
            "auto_ended": False,
            "status": self.runner.get_status(),
            "messages": new_messages,
            "metrics": metrics,
            "convergence": result.get("convergence"),
            "_conv_diag": result.get("_conv_diag"),
        }

    def _limited_run_round(self) -> Dict:
        """有限模式运行（当 MultiAgentRunner 不可用时）"""
        self.round += 1
        return {
            "round": self.round,
            "done": False,
            "status": {"round": self.round, "finished": False},
            "messages": [],
        }

    def _fill_placeholders(self, content: str):
        """R28：把预设占位符替换为当前会话的真实学生姓名。

        返回 (替换后的内容, 替换明细列表)。未识别到的占位符原样保留（由前端提示用户）。
        """
        if not content or "【" not in content:
            return content, []
        names = {}
        try:
            mem = getattr(self.runner.state, "agent_memories", {}) or {}
            for key in ("bully", "victim", "bystander_a", "bystander_b"):
                m = mem.get(key)
                nm = getattr(m, "name", "") if m is not None else ""
                if nm:
                    names[key] = nm
        except Exception:
            pass
        mapping = {
            "【欺凌者】": names.get("bully", ""),
            "【霸凌者】": names.get("bully", ""),
            "【欺凌者姓名】": names.get("bully", ""),
            "【受害者】": names.get("victim", ""),
            "【被霸凌者】": names.get("victim", ""),
            "【受害者姓名】": names.get("victim", ""),
            "【旁观者】": names.get("bystander_a", ""),
            "【旁观者姓名】": names.get("bystander_a", ""),
        }
        filled = []
        out = content
        for token, real in mapping.items():
            if token in out and real:
                out = out.replace(token, real)
                filled.append(f"{token}→{real}")
        return out, filled

    def intervene(self, content: str) -> Dict:
        """教师干预"""
        if self.runner is None:
            return {"feedback": ["MultiAgentRunner not available"], "messages": []}

        # R28：占位符兜底替换——预设文案里的【欺凌者】【受害者】【旁观者】若未替换就发出，
        # 模型无法解析指代，会把干预判成「暴露受害者/增加报复风险」（R_EXPOSURE_RETALIATION），
        # 风险副作用每轮累积 → 报复风险持续升高 → 会话永不自动停止。
        # 这里按当前会话的真实学生姓名自动替换，指代明确后评分才成立。
        # （容错：桩对象/异常场景下退化为原文，不阻断干预流程）
        filled_names = []
        try:
            _fp = getattr(self, "_fill_placeholders", None)
            if callable(_fp):
                content, filled_names = _fp(content)
        except Exception:
            filled_names = []

        # R28：风险副作用去重集合跨会话恢复保留（state 重建时集合会丢失）
        try:
            _applied = getattr(self.runner.state, "applied_risk_effects", None)
            if _applied is None:
                self.runner.state.applied_risk_effects = set()
                _applied = self.runner.state.applied_risk_effects
            for _code in (self.session_data.get("applied_risk_effects") or []):
                _applied.add(_code)
        except Exception:
            pass

        # 调用新版 MultiAgentRunner.intervene() (graph_builder)
        try:
            result = self.runner.intervene(content)
        except Exception as e:
            return {"feedback": [f"干预处理失败: {str(e)}"], "messages": []}

        # 新版intervene()返回 dict: {"quality_score", "feedback", "victim_care_score", ...}
        if not result:
            return {"feedback": ["干预未产生有效响应"], "messages": []}

        if not isinstance(result, dict):
            return {"feedback": [f"意外返回值类型: {type(result)}"], "messages": []}

        feedback_list = result.get("feedback", [])
        quality = result.get("quality_score")   # 0-100 或 None（待评估，R11）
        recommendation = result.get("recommendation", "")
        if feedback_list and recommendation and recommendation not in str(feedback_list):
            feedback_list = feedback_list + [recommendation]

        # R4：预测效果与实际效果分开存储；R11：显式单位标记
        _rec = result.get("intervention_record", {}) or {}
        interv_record = {
            "content": content,
            "quality_score": quality,
            "quality_score_scale": 100,
            "assessment_status": result.get("assessment_status",
                                           "scored" if quality is not None else "pending"),
            "assessment_id": _rec.get("assessment_id", ""),
            "victim_care_score": result.get("victim_care_score", 0),
            "feedback": feedback_list,
            "round": self.round,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "predicted_bullying_reduction": _rec.get("predicted_bullying_reduction"),
            "predicted_distress_change": _rec.get("predicted_distress_change"),
            "actual_bullying_reduction": _rec.get("actual_bullying_reduction"),
            "actual_distress_change": _rec.get("actual_distress_change"),
            "bullying_reduction": _rec.get("bullying_reduction", 0),
            "distress_reduction": _rec.get("distress_reduction", 0),
            # 完整评分明细（七维分档/证据/理由 + 风险明细 + 封顶前后分），供事后逐项复核
            "assessment": result.get("assessment"),
        }
        # R28：占位符替换明细（便于教师核对系统改了哪几处）
        if filled_names:
            interv_record["placeholder_filled"] = filled_names
            feedback_list.append("提示：检测到干预内容含未替换的占位符，已自动替换为实际学生姓名（"
                                 + "、".join(filled_names) + "），以确保指代明确、评价有效。")
        # R28：把风险副作用去重集合写回会话数据（随日志持久化，state 重建后仍可恢复）
        try:
            _applied = getattr(self.runner.state, "applied_risk_effects", None)
            if _applied is not None:
                self.session_data["applied_risk_effects"] = sorted(_applied)
        except Exception:
            pass

        # ===== R14/R15：实时指导（带展示门槛） + 连续低分预警 =====
        coaching = None
        streak_alert = None
        guidance_enabled = False
        if COACHING_AVAILABLE:
            try:
                # 指导数据始终计算（供连续低分统计使用）……
                round_msgs = [m for m in self.messages if m.get("round") == self.round]
                coaching_data = build_real_time_coaching(
                    interv_record, LOW_SCORE_THRESHOLD, messages=round_msgs)

                # 连续低分追踪（R16/R17 修正）：
                # - 已评分且低于阈值 → 计入低分连败
                # - 已评分且达到阈值 → 清零（表现良好）
                # - 待评估(pending) → 低分连败保持不变（待评估不等于表现良好），
                #   但计入「未获得有效评分」连败：即便评分服务不可用，
                #   连续若干轮后也能给出基于情境的提示（避免提示完全失效）
                if quality is None:
                    self._unscored_streak = int(getattr(self, "_unscored_streak", 0)) + 1
                elif quality < LOW_SCORE_THRESHOLD:
                    self._unscored_streak = 0
                    self._low_streak.append({
                        "round": self.round,
                        "score": quality,
                        "weak_dimensions": coaching_data.get("weak_dimensions", []),
                        "risk_flags": coaching_data.get("risk_flags", []),
                    })
                else:
                    self._unscored_streak = 0
                    self._low_streak = []

                # R15/R17：达到门槛后向用户展示提示
                #   - 低分连败达标 → 基于失分维度的针对性提示
                #   - 无有效评分连败达标 → 基于本轮情境的提示（评分不可用时兜底）
                low_streak = len(self._low_streak)
                guidance_enabled = (low_streak >= self.GUIDANCE_HINT_MIN
                                    or self._unscored_streak >= self.GUIDANCE_HINT_MIN)
                if guidance_enabled:
                    coaching = coaching_data
                    interv_record["coaching"] = coaching

                streak_alert = build_streak_alert(self._low_streak, LOW_SCORE_THRESHOLD,
                                                  self.GUIDANCE_ALERT_MIN)
                if streak_alert:
                    interv_record["streak_alert"] = streak_alert
            except Exception as e:
                print(f"[SessionManager] coaching failed: {e}")
        self.interventions.append(interv_record)
        self.session_data["interventions"].append(interv_record)
        
        # 更新状态中的干预计数和最后干预信息
        self.runner.state.intervention_count += 1
        self.runner.state.last_intervention = content
        self.runner.state.last_intervention_round = self.round
        
        # 【修复】同步 MultiAgentRunner 的消息计数指针：
        # intervene() 在 run_round() 之间被调用，会向 state.messages 添加教师消息，
        # 若不同步 _prev_msg_count，下一次 run_round() 会把教师消息也算成"新消息"
        # 导致前端重复显示同一教师的干预内容
        # 注意：run_round() 读取的是 runner._prev_msg_count（不是 _step_runner 的），
        # 之前的修复错把指针同步到了 _step_runner._prev_msg_count，没有效果
        self.runner._prev_msg_count = len(self.runner.state.messages)
        
        # R8：同步内部状态轨迹到会话日志
        self._sync_internal_history()
        self._save_log()

        return {
            "content": content,
            "feedback": feedback_list,
            "quality_score": quality,
            "victim_care_score": result.get("victim_care_score", 0),
            "bullying_intensity": result.get("bullying_intensity", 0),
            "victim_distress": result.get("victim_distress", 0),
            "recommendation": recommendation,
            # --- 发送状态（R12）：前端据此给出明确的「已发送」反馈 ---
            "send_status": "sent",
            "sent_at": datetime.now().strftime("%H:%M:%S"),
            "assessment_status": result.get("assessment_status", "scored"),
            # R14/R15/R16：实时指导（门槛内才展示）、连续低分预警与触发进度
            "coaching": coaching,
            "guidance_enabled": guidance_enabled,
            "guidance_progress": self._guidance_progress(guidance_enabled),
            "low_score_alert": streak_alert,
            "low_score_streak": len(self._low_streak),
        }

    def record_skip_intervention(self, round_num: int) -> Dict:
        """记录跳过干预决策"""
        record = {
            "round": round_num,
            "decision": "skip",
            "timestamp": datetime.now().isoformat(),
        }
        self.skip_decisions.append(record)
        self.session_data["skip_decisions"].append(record)
        self._save_log()
        return {"status": "ok", "record": record}

    def get_status(self) -> Dict:
        """获取状态"""
        if self.runner:
            return self.runner.get_status()
        return {
            "round": self.round,
            "level": self.level,
            "bullying_intensity": 0,
            "victim_distress": 0,
            "bystander_intervention": 0,
            "finished": False,
            "termination_reason": "",
        }

    def add_message(self, message: Dict) -> None:
        """添加消息到会话"""
        self.messages.append(message)
        self.session_data["messages"].append(message)
        self._save_log()

    def get_messages(self, after_id: str = None) -> List[Dict]:
        """获取消息"""
        if after_id is None:
            return self.messages
        idx = next((i for i, m in enumerate(self.messages) if m.get("id") == after_id), -1)
        return self.messages[idx + 1:]

    def _persist_report_snapshot(self) -> bool:
        """把当前报告快照写入 DB（INSERT OR REPLACE，同一会话仅一条）。

        普通轮与自动结束轮都必须调用：此前终止分支漏写，导致 reports 表
        停留在上一轮（round=上一轮、finished=False、无 result_type），
        服务重启后或管理员直接读 DB 时会拿到缺最后一轮的报告。
        未绑定 DB 会话时跳过（单元测试/离线运行）。
        """
        db_sid = getattr(self, "_db_session_id", None)
        db_uid = getattr(self, "_db_user_id", None)
        if not db_sid or not db_uid:
            return False
        try:
            snapshot = self.generate_report()
            from database import upsert_report_snapshot
            upsert_report_snapshot(db_sid, db_uid, snapshot)
            return True
        except Exception as e:
            print(f"[SessionManager] DB report snapshot failed: {e}")
            return False

    def persist_completed(self) -> bool:
        """自动结束后落库：status=completed + result_type + auto_end_reason。

        此前只有 /report 会写 status=completed 且不带结束原因，导致 DB 中
        auto_end_reason 始终为空、事后无法判定会话为何结束。
        """
        db_sid = getattr(self, "_db_session_id", None)
        if not db_sid:
            return False
        try:
            from database import update_session_status
            update_session_status(
                db_sid,
                "completed",
                self.result_type or "",
                self.auto_end_reason or "自动结束",
            )
            return True
        except Exception as e:
            print(f"[SessionManager] persist_completed failed: {e}")
            return False

    def check_auto_end_conditions(self) -> Dict:
        """检查自动终止条件（兼容旧接口）"""
        if self.runner is None:
            return {"should_end": False}

        status = self.runner.get_status()
        result = {
            "should_end": status.get("finished", False),
            "reason": status.get("termination_reason", ""),
            "details": {},
        }

        if result["should_end"]:
            self.auto_ended = True
            self.auto_end_reason = result["reason"]
            self.result_type = status.get("result_type", "")

        return result

    def generate_report(self) -> Dict:
        """生成评估报告"""
        if self.runner is None:
            return {"summary": {"total_messages": 0}}

        metrics = self.runner.get_metrics()
        
        # 计算干预效果统计
        intervention_scores = [iv.get("quality_score", 0) for iv in self.interventions if iv.get("quality_score") is not None]
        avg_effectiveness = sum(intervention_scores) / len(intervention_scores) if intervention_scores else 0

        # ===== R14：逐轮分析 + 改进方案 =====
        round_analysis = []
        improvement_suggestions = {}
        training_report = {}
        if COACHING_AVAILABLE:
            try:
                by_round = {}
                for m in self.messages:
                    r = m.get("round")
                    if r is not None:
                        by_round.setdefault(r, []).append(m)
                iv_by_round = {}
                for iv in self.interventions:
                    r = iv.get("round")
                    if r is not None:
                        iv_by_round[r] = iv
                max_round = max(list(by_round.keys()) + list(iv_by_round.keys()) + [0])
                for r in range(1, int(max_round) + 1):
                    round_analysis.append(
                        build_round_analysis(r, by_round.get(r, []), iv_by_round.get(r), LOW_SCORE_THRESHOLD))
                improvement_suggestions = build_improvement_plan(round_analysis, LOW_SCORE_THRESHOLD)
                # R19：结构化教师实训评估报告（八大板块）
                if REPORT_BUILDER_AVAILABLE:
                    training_report = build_training_report(round_analysis, LOW_SCORE_THRESHOLD)
            except Exception as e:
                print(f"[SessionManager] report coaching failed: {e}")
        
        # OBVQ 指数分析
        obvq_total = metrics.get("obvq_total_score", 0)
        obvq_dim_totals = metrics.get("obvq_dimension_totals", [0]*6)
        obvq_dim_avgs = metrics.get("obvq_dimension_averages", [0]*6)
        
        # 维度分析
        obvq_dim_names = ["身体欺负", "言语欺负", "关系欺负", "网络欺负", "权力欺负", "偏见欺负"]
        obvq_dim_analysis = []
        for i, (name, total, avg) in enumerate(zip(obvq_dim_names, obvq_dim_totals, obvq_dim_avgs)):
            level = "高" if total >= 15 else "中" if total >= 8 else "低"
            obvq_dim_analysis.append({
                "name": name,
                "total": total,
                "average": avg,
                "level": level,
                "description": self._get_obvq_dim_description(i, total)
            })
        
        # 干预效果分析和建议
        intervention_analysis = self._analyze_interventions()
        
        # R20：趋势分析（基线→终点、百分点、样本数）与规范化报告合同
        trends = self._analyze_trends()
        report_data = {}
        if REPORT_CONTRACT_AVAILABLE:
            try:
                report_data = build_report_data(self, training_report, trends, {
                    "total_score": obvq_total,
                    "dimension_analysis": obvq_dim_analysis,
                }, round_analysis)
                # 报告快照元数据：保留历史版本，不静默覆盖（计划书 §6.3）
                record_report_meta(self.session_data, report_data)
                self._save_log()
            except Exception as e:
                print(f"[SessionManager] report contract failed: {e}")
        
        return {
            "summary": {
                "total_messages": len(self.messages),
                "intervention_count": len(self.interventions),
                "skip_count": len(self.skip_decisions),
                "total_decision_count": len(self.interventions) + len(self.skip_decisions),
                "bullying_intensity": metrics.get("bullying_intensity", 0),
                "victim_distress": metrics.get("victim_distress", 0),
                "intervention_effectiveness": avg_effectiveness,
                "hostile_messages": metrics.get("hostile_messages", 0),
            },
            "obvq_analysis": {
                "total_score": obvq_total,
                "dimension_totals": obvq_dim_totals,
                "dimension_averages": obvq_dim_avgs,
                "dimension_analysis": obvq_dim_analysis,
                "recommendation": self._get_obvq_recommendation(obvq_total, obvq_dim_totals),
            },
            "intervention_analysis": intervention_analysis,
            "trends": trends,
            "metrics": metrics,
            "messages": self.messages,
            "interventions": self.interventions,
            "session_duration": str(datetime.now() - self.started_at),
            "total_rounds": self.round,
            "ability_assessment": self._assess_ability(metrics),
            # ===== R14：逐轮分析与详细改进意见 =====
            "round_analysis": round_analysis,
            "improvement_suggestions": improvement_suggestions,
            # ===== R19：结构化教师实训评估报告（八大板块）=====
            "training_report": training_report,
            # ===== R20：规范化报告数据合同（用户端/管理员端/导出共用同一份）=====
            "report_data": report_data,
        }

    # ============================================================
    # R20（计划书 §9.1/§12.3）：模拟状态趋势
    # 基线→终点、百分点、样本数与窗口；短会话不做三段切片伪趋势
    # ============================================================
    def _analyze_trends(self) -> Dict:
        """分析欺凌强度与受害者压力等模拟状态的变化趋势。

        R20（计划书 §9.1）修正：
        - 以「首个有效快照 → 最终有效快照」计算基线/终点与百分点变化，
          不再做前/中/后三段切片均值（短会话会产生空段与伪趋势）；
        - 变化统一使用「百分点」表述，禁止含义不清的「+5%」；
        - 明确展示有效样本数与观察窗口；样本不足时如实标注「数据不足」；
        - 六类欺凌分类为项目自定义关键词分类（非 OBVQ-R 标准量表），
          趋势同样改为起止对比并标注计算口径；
        - 干预前后变化仅说明观察结果，不作为因果结论。
        """
        history = self.session_data.get("metrics_history", [])
        if not history:
            return {
                "summary": "数据不足，无法生成趋势分析（有效快照为 0 个）",
                "bullying_trend": "",
                "distress_trend": "",
                "bullying_metric": None,
                "distress_metric": None,
                "obvq_trends": [],
                "intervention_impact": [],
                "valid_sample_count": 0,
                "source_window": "",
                "stable_threshold_pp": STABLE_THRESHOLD_PP,
                "unit_note": "变化以「百分点」表述；欺凌强度/受害者压力为 0–1 状态比例。",
            }

        def _metric(key: str, name: str) -> Dict:
            snaps = [h for h in history if isinstance(h.get(key), (int, float))]
            n = len(snaps)
            if n < 2:
                return {
                    "baseline": round(float(snaps[0][key]), 3) if n == 1 else None,
                    "final": round(float(snaps[0][key]), 3) if n == 1 else None,
                    "delta_percentage_points": None,
                    "valid_sample_count": n,
                    "source_window": "快照不足（<2 个），无法计算变化",
                    "direction": "insufficient" if n else "none",
                    "text": (f"{name}有效快照不足 2 个（当前 {n} 个），无法计算变化"
                             if n else f"{name}无有效快照"),
                }
            b = float(snaps[0][key])
            f = float(snaps[-1][key])
            delta_pp = round((f - b) * 100, 1)  # 百分点（状态值为 0–1 比例）
            if abs(delta_pp) < STABLE_THRESHOLD_PP:
                direction = "stable"
                text = (f"{name}基本持平：{b * 100:.0f}% → {f * 100:.0f}%"
                        f"（变化 {delta_pp:+.1f} 个百分点，未超过 {STABLE_THRESHOLD_PP:g} 个百分点的稳定阈值）")
            elif delta_pp < 0:
                direction = "down"
                text = f"{name}下降 {abs(delta_pp):.1f} 个百分点：{b * 100:.0f}% → {f * 100:.0f}%"
            else:
                direction = "up"
                text = f"{name}上升 {abs(delta_pp):.1f} 个百分点：{b * 100:.0f}% → {f * 100:.0f}%"
            return {
                "baseline": round(b, 3),
                "final": round(f, 3),
                "delta_percentage_points": delta_pp,
                "valid_sample_count": n,
                "source_window": "首个有效快照至最终有效快照",
                "direction": direction,
                "text": text,
            }

        bi = _metric("bullying_intensity", "欺凌强度")
        vd = _metric("victim_distress", "受害者压力")

        # ===== 项目六类关键词分类趋势（非 OBVQ-R 标准量表）=====
        obvq_dim_names = ["身体欺负", "言语欺负", "关系欺负", "网络欺负", "权力欺负", "偏见欺负"]
        obvq_trends = []

        def _get_dim(h, idx):
            dims = h.get("obvq_dimensions", [])
            return dims[idx] if idx < len(dims) else 0

        for dim_idx, dim_name in enumerate(obvq_dim_names):
            b = _get_dim(history[0], dim_idx)
            f = _get_dim(history[-1], dim_idx)
            change = round(f - b, 1)
            obvq_trends.append({
                "name": dim_name,
                "index": dim_idx,
                "baseline": b,
                "final": f,
                "change": change,
                "direction": "up" if change > 0 else ("down" if change < 0 else "stable"),
            })

        # 干预前后的观察（非因果结论）
        intervention_impact = []
        for iv in self.interventions:
            iv_round = iv.get("round", 0)
            before = None
            after = None
            for h in history:
                hr = h.get("round", 0)
                if hr <= iv_round:
                    before = h
                if hr > iv_round and after is None:
                    after = h
            if before and after:
                bi_drop = before.get("bullying_intensity", 0) - after.get("bullying_intensity", 0)
                vd_drop = before.get("victim_distress", 0) - after.get("victim_distress", 0)
                intervention_impact.append({
                    "round": iv_round,
                    "bullying_change": -round(bi_drop, 3),
                    "distress_change": -round(vd_drop, 3),
                    "interpretation": ("干预后观察到的状态变化；同期可能存在其他学生事件，"
                                       "不构成因果结论。"),
                })

        summary = (f"欺凌强度：{bi['text']}；受害者压力：{vd['text']}。"
                   f"（观察窗口：首个有效快照至最终有效快照，共 {len(history)} 个快照；"
                   f"模拟状态变化不等于教师能力评分。）")
        return {
            "summary": summary,
            "bullying_trend": bi["text"],
            "distress_trend": vd["text"],
            "bullying_metric": bi,
            "distress_metric": vd,
            "early_bullying": bi.get("baseline"),
            "late_bullying": bi.get("final"),
            "bullying_change": (bi.get("delta_percentage_points") / 100
                                if bi.get("delta_percentage_points") is not None else None),
            "early_distress": vd.get("baseline"),
            "late_distress": vd.get("final"),
            "distress_change": (vd.get("delta_percentage_points") / 100
                                if vd.get("delta_percentage_points") is not None else None),
            "obvq_trends": obvq_trends,
            "intervention_impact": intervention_impact,
            "valid_sample_count": len(history),
            "source_window": "首个有效快照至最终有效快照",
            "stable_threshold_pp": STABLE_THRESHOLD_PP,
            "unit_note": "变化以「百分点」表述；欺凌强度/受害者压力为 0–1 状态比例。",
        }

    # ============================================================
    # R20（计划书 §9.3/§2.1）：能力评估
    # 无可识别机会 ≠ 优秀；干预能力消费量规评分；移除关键词策略分类与关怀度口径
    # ============================================================
    def _assess_ability(self, metrics: Dict) -> Dict:
        """能力评估（R20 重写）。

        修正要点：
        - 「未检测到明显霸凌行为」不再判为优秀：无可识别机会时为 not_evaluable；
        - 发现能力基于「可识别机会 → 首次介入」的证据链，且明确其只代表时机；
        - 干预能力消费完整互动量规评分（D1–D7），不再使用关键词策略分类；
        - 受害者保护以 D2 维度分呈现，不再使用关键词关怀度百分比。
        """
        # ===== 发现能力（机会识别）=====
        first_bully_round = None
        for msg in self.messages:
            role = str(msg.get("sender_role", "") or msg.get("role", "")).lower()
            sender = str(msg.get("sender", "") or msg.get("sender_name", ""))
            if "bully" in role or "霸凌" in sender:
                r = msg.get("round", 0)
                if first_bully_round is None or r < first_bully_round:
                    first_bully_round = r

        first_intervention_round = None
        for iv in self.interventions:
            r = iv.get("round", 0)
            if first_intervention_round is None or r < first_intervention_round:
                first_intervention_round = r

        if first_bully_round is None:
            # R20：无欺凌事件 ≠ 发现能力优秀（计划书 §2.2）
            discovery = {
                "level": "not_evaluable",
                "response_delay": None,
                "opportunity_round": None,
                "description": ("本次互动中没有出现可确认的欺凌事件或清晰风险信号，"
                               "因此没有足够证据评价发现能力（不判为优秀）。"),
            }
        elif first_intervention_round is None:
            discovery = {
                "level": "needs_improvement",
                "response_delay": None,
                "opportunity_round": first_bully_round,
                "description": (f"第 {first_bully_round} 轮已出现针对学生的攻击性发言，"
                               f"但本次未进行任何干预——存在未识别或未介入的问题。"),
            }
        else:
            delay = first_intervention_round - first_bully_round
            if delay <= 1:
                level = "good"
                desc = (f"第 {first_bully_round} 轮出现攻击性发言后，第 {first_intervention_round} 轮即介入；"
                        "轮次差只代表介入时机，不构成「识别准确」的证据。")
            elif delay <= 3:
                level = "good"
                desc = (f"第 {first_bully_round} 轮出现攻击性发言后，{delay} 轮内介入"
                        f"（第 {first_intervention_round} 轮）。")
            else:
                level = "needs_improvement"
                desc = (f"第 {first_bully_round} 轮出现攻击性发言后，隔了 {delay} 轮才于"
                        f"第 {first_intervention_round} 轮介入，识别与响应偏慢。")
            discovery = {"level": level, "response_delay": delay,
                         "opportunity_round": first_bully_round, "description": desc}

        # ===== 干预能力（消费量规评分结果）=====
        n_iv = len(self.interventions)

        def _band_of(s):
            if s >= 80:
                return "清晰、具体、适切"
            if s >= 60:
                return "基本达标"
            if s >= 40:
                return "初步不足"
            return "明显不当"

        scored_ivs = [iv for iv in self.interventions
                      if iv.get("quality_score") is not None]
        if n_iv == 0:
            intervention = {
                "level": "needs_improvement", "count": 0,
                "rubric_score": None, "band": None, "passed_count": None,
                "description": "未进行任何干预，本次没有可评价的干预行为。",
            }
        elif not scored_ivs:
            intervention = {
                "level": "not_assessed", "count": n_iv,
                "rubric_score": None, "band": None, "passed_count": None,
                "description": (f"共 {n_iv} 次干预，但量规评分均未完成（待评估），"
                                "无法评价干预能力——请检查评分服务是否可用。"),
            }
        else:
            scores = [iv["quality_score"] for iv in scored_ivs]
            avg = sum(scores) / len(scores)
            passed = sum(1 for s in scores if s >= 60)
            if avg >= 80:
                level = "excellent"
            elif avg >= 60:
                level = "good"
            else:
                level = "needs_improvement"
            intervention = {
                "level": level,
                "count": n_iv,
                "rubric_score": round(avg, 1),
                "band": _band_of(avg),
                "passed_count": passed,
                "description": (f"共 {n_iv} 次干预、{len(scored_ivs)} 次完成量规评分，"
                                f"教师干预质量评分均分 {avg:.1f}/100（{_band_of(avg)}），"
                                f"{passed} 次达到基本达标。"),
            }

        # ===== 受害者保护（D2 维度）=====
        d2_scores = []
        for iv in scored_ivs:
            a = iv.get("assessment") if isinstance(iv.get("assessment"), dict) else {}
            item = (a.get("dimensions") or {}).get("D2") or {}
            if item.get("status") == "scored" and item.get("score") is not None:
                d2_scores.append(item["score"])
        if not d2_scores:
            victim_protection = {
                "level": "not_assessed", "score": None,
                "description": "本次没有完成的 D2（受害者保护与心理支持）量规评分。",
            }
        else:
            avg_d2 = sum(d2_scores) / len(d2_scores)
            if avg_d2 >= 60:
                level = "good"
                desc = f"D2 受害者保护维度平均 {avg_d2:.0f}/100（基本达标及以上）。"
            else:
                level = "needs_improvement"
                desc = (f"D2 受害者保护维度平均 {avg_d2:.0f}/100（未达基本达标）："
                        "请先确认受害者是否安全，再给出具体的保护安排。")
            victim_protection = {"level": level, "score": round(avg_d2, 1), "description": desc}

        return {
            "discovery": discovery,
            "intervention": intervention,
            "victim_protection": victim_protection,
        }

    def _get_obvq_dim_description(self, dim_index: int, total_score: int) -> str:
        """获取OBVQ维度描述"""
        descriptions = {
            0: {  # 身体欺负
                0: "未出现身体欺负行为",
                8: "偶尔出现推搡等轻微身体接触",
                15: "频繁出现身体攻击行为，需要重点关注",
            },
            1: {  # 言语欺负
                0: "言语交流基本文明",
                8: "出现嘲讽、取笑等言语攻击",
                15: "言语攻击频繁且激烈，造成严重伤害",
            },
            2: {  # 关系欺负
                0: "同学关系和谐",
                8: "出现排挤、孤立等现象",
                15: "关系欺负严重，受害者被完全孤立",
            },
            3: {  # 网络欺负
                0: "未涉及网络欺负",
                8: "出现网络嘲讽、传播谣言等",
                15: "网络欺负严重，影响范围广",
            },
            4: {  # 权力欺负
                0: "权力关系平等",
                8: "出现利用权力压制他人",
                15: "权力欺负严重，形成明显的压迫关系",
            },
            5: {  # 偏见欺负
                0: "未出现偏见性言论",
                8: "出现性别、外貌等偏见言论",
                15: "偏见欺负严重，涉及歧视和污名化",
            },
        }
        dim_desc = descriptions.get(dim_index, {})
        if total_score >= 15:
            return dim_desc.get(15, "需要关注")
        elif total_score >= 8:
            return dim_desc.get(8, "需要关注")
        else:
            return dim_desc.get(0, "正常")
    
    def _get_obvq_recommendation(self, total_score: int, dim_totals: list) -> str:
        """基于OBVQ维度分布给出针对性建议"""
        # 找出得分最高的维度
        high_dims = []
        for i, total in enumerate(dim_totals):
            if total >= 10:
                names = ["身体欺负", "言语欺负", "关系欺负", "网络欺负", "权力欺负", "偏见欺负"]
                high_dims.append(names[i] if i < len(names) else f"维度{i}")
        
        if high_dims:
            dim_str = "、".join(high_dims)
            return f"欺凌类型集中在：{dim_str}，建议针对这些类型的欺凌行为采取相应干预策略。"
        return "各维度欺凌行为较少，继续保持当前干预方式。"
    
    # ============================================================
    # R20（计划书 §2.1/任务J/GUIDE-04）：干预分析
    # 移除关键词策略分类与固定模板建议（含「促成和解」），改为量规证据诊断
    # ============================================================
    def _analyze_interventions(self) -> Dict:
        """干预分析与个性化建议（R20 重写）。

        修正要点：
        - 移除关键词策略分类（权威型/关怀型/引导型/混合型）——策略功能应由
          话语在情境中完成的功能与量规证据说明，不由词表命中决定；
        - 删除全部固定模板建议，特别是原「引导型促成和解」——量规明确禁止
          在未评估安全时促成对质或和解（R_FORCED_CONFRONTATION）；
        - 建议改为量规证据诊断：分档统计 + 维度缺口 + 已触发风险的修正动作。
        """
        total_decisions = len(self.interventions) + len(self.skip_decisions)
        skip_count = len(self.skip_decisions)
        if not self.interventions and not self.skip_decisions:
            return {
                "summary": "本次模拟未进行任何干预决策。",
                "suggestions": ["本次没有干预或跳过记录；下一次模拟请在出现针对学生的"
                                "攻击、嘲笑或孤立行为时练习及时介入。"],
                "action_plan": [],
                "effectiveness_trend": [],
            }
        if not self.interventions:
            return {
                "summary": f"本次模拟共 {total_decisions} 轮决策，全部选择了跳过干预（共 {skip_count} 次）。",
                "suggestions": ["全部轮次均跳过干预：请在出现可识别的欺凌信号时练习介入，"
                                "并给出「行为定性 + 明确停止要求」。",
                               "若判断现场确实无需介入，请在复盘中说明判断依据。"],
                "action_plan": ["下一场模拟：至少完成一次包含「行为定性 + 停止要求」的介入。"],
                "effectiveness_trend": [],
                "skip_count": skip_count,
                "total_decisions": total_decisions,
            }

        effectiveness_trend = []
        good_count = medium_count = poor_count = pending_count = 0
        for i, iv in enumerate(self.interventions):
            raw_score = iv.get("quality_score")
            a = iv.get("assessment") if isinstance(iv.get("assessment"), dict) else {}
            effectiveness_trend.append({
                "index": i + 1,
                "round": iv.get("round"),
                "score": raw_score,
                "pending": raw_score is None,
                "risk_level": a.get("risk_level") if a else None,
                "content": iv.get("content", ""),
                "feedback": iv.get("feedback", []),
            })
            if raw_score is None:
                pending_count += 1
            elif raw_score >= 60:
                good_count += 1
            elif raw_score >= 30:
                medium_count += 1
            else:
                poor_count += 1

        suggestions = []
        action_plan = []
        scored_ivs = [iv for iv in self.interventions if iv.get("quality_score") is not None]

        if not scored_ivs:
            suggestions.append(f"共 {len(self.interventions)} 次干预，但量规评分均未完成（待评估），"
                                "无法给出基于证据的能力诊断——请检查评分服务配置后重新演练。")
            summary = (f"共 {total_decisions} 轮决策（{len(self.interventions)} 次干预 + "
                      f"{skip_count} 次跳过），量规评分未完成。")
        else:
            scores = [iv["quality_score"] for iv in scored_ivs]
            avg = sum(scores) / len(scores)
            band = ("清晰、具体、适切" if avg >= 80 else "基本达标" if avg >= 60
                    else "初步不足" if avg >= 40 else "明显不当")
            pending_txt = (f"、{pending_count} 次待评估" if pending_count else "")
            suggestions.append(
                f"教师干预质量评分（完整互动量规、等权平均）：{avg:.1f}/100（{band}）；"
                f"{good_count} 次达到基本达标、{medium_count} 次初步不足、"
                f"{poor_count} 次明显不当{pending_txt}。")
            summary = (f"共 {total_decisions} 轮决策（{len(self.interventions)} 次干预 + "
                      f"{skip_count} 次跳过），量规分档：基本达标 {good_count} 次、"
                      f"初步不足 {medium_count} 次、明显不当 {poor_count} 次{pending_txt}。")

            # 维度缺口（依据评分明细聚合，不使用关键词）
            dim_gap: Dict[str, List[float]] = {}
            for iv in scored_ivs:
                a = iv.get("assessment") if isinstance(iv.get("assessment"), dict) else {}
                for d, item in (a.get("dimensions") or {}).items():
                    if not isinstance(item, dict) or item.get("status") != "scored":
                        continue
                    s = item.get("score")
                    if isinstance(s, (int, float)) and s < LOW_SCORE_THRESHOLD:
                        dim_gap.setdefault(d, []).append(float(s))
            if dim_gap:
                ranked = sorted(dim_gap.items(),
                                key=lambda kv: sum(kv[1]) / len(kv[1]))[:3]
                gap_txt = "、".join(f"{d}（均 {sum(v) / len(v):.0f} 分）" for d, v in ranked)
                suggestions.append(f"量规维度缺口集中在：{gap_txt}。")
                for d, v in ranked:
                    action_plan.append(f"下一轮补齐 {d} 对应要素（依据本次评分理由与原文证据复盘）。")
            else:
                suggestions.append("各评分维度均达到基本达标，保持当前表达结构。")

            # 已触发风险的修正动作（禁止任何和解/对质类建议）
            risk_hits: Dict[str, int] = {}
            for iv in scored_ivs:
                a = iv.get("assessment") if isinstance(iv.get("assessment"), dict) else {}
                for f in ((a.get("risk_assessment") or {}).get("flags_detail") or []):
                    code = f.get("code") if isinstance(f, dict) else None
                    if code:
                        risk_hits[code] = risk_hits.get(code, 0) + 1
            for code, times in sorted(risk_hits.items(), key=lambda kv: -kv[1]):
                label = RISK_GUIDE.get(code, {}).get("label", code)
                advice = RISK_GUIDE.get(code, {}).get("advice", "")
                suggestions.append(f"已触发风险「{label}」{times} 次：{advice}")
                action_plan.append(f"规避「{label}」：参照报告风险板块的正向替换句逐句调整。")

        return {
            "summary": summary,
            "suggestions": suggestions,
            "action_plan": action_plan,
            "effectiveness_trend": effectiveness_trend,
            "band_counts": {"good": good_count, "medium": medium_count,
                            "poor": poor_count, "pending": pending_count},
            "skip_count": skip_count,
            "total_decisions": total_decisions,
        }

"""
LLM 客户端 - 封装 DeepSeek API 调用
使用 LangChain 的 ChatOpenAI 兼容接口
"""

import os
import time
import json
from typing import Optional, List, Dict, Any
from pathlib import Path

# LangChain imports
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage


# ============================================================
# API Key 获取（复用 app.py 的逻辑）
# ============================================================

def _read_env_file(env_path: Path) -> Dict[str, str]:
    """读取 .env（BOM 安全：utf-8-sig 可去掉 UTF-8 BOM，避免键名变成 '\\ufeffXXX'）"""
    values: Dict[str, str] = {}
    try:
        with open(env_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip().lstrip("\ufeff")
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip().lstrip("\ufeff")
                    val = val.strip().strip('"').strip("'")
                    if key:
                        values[key] = val
    except Exception:
        pass
    return values


def _env_search_paths() -> List[Path]:
    """
    .env 查找路径（按优先级）：
      1. 项目根 <proj>/.env
      2. web 子目录 <proj>/web/.env
    （此前只查项目根，而实际 .env 常放在 web/ 下 → 密钥从未被加载，
      导致专家评分全部 pending、提示机制因无分数而永不触发）
    """
    project_root = Path(__file__).parent.parent.parent
    return [
        project_root / ".env",
        project_root / "web" / ".env",
    ]


def get_api_key() -> str:
    """获取 API Key（环境变量 → .env（双路径/BOM 安全）→ OpenClaw 配置）"""
    # 1. 尝试环境变量（MINIMAX_API_KEY 亦为受支持来源）
    for env_name in ("DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "OPENCLAW_API_KEY", "LLM_API_KEY"):
        value = os.environ.get(env_name, "")
        if value.strip():
            return value.strip()

    # 2. 从 .env 文件读取（双路径 + BOM 安全）
    for env_path in _env_search_paths():
        if not env_path.exists():
            continue
        values = _read_env_file(env_path)
        for key in ("DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "OPENCLAW_API_KEY", "LLM_API_KEY"):
            value = values.get(key, "")
            if value.strip():
                return value.strip()

    # 3. 从 OpenClaw 配置读取（兼容旧配置）
    try:
        openclaw_config = Path.home() / ".openclaw" / "openclaw.json"
        if openclaw_config.exists():
            with open(openclaw_config, "r", encoding="utf-8") as f:
                config = json.load(f)
            providers = config.get("models", {}).get("providers", {})
            for provider_name in ["deepseek", "minimax-cn", "minimax"]:
                if provider_name in providers:
                    key = providers[provider_name].get("apiKey", "")
                    if key:
                        return key
    except Exception:
        pass

    return ""


def get_llm_endpoint() -> tuple:
    """
    返回 (base_url, model)：端点与模型可由环境变量覆盖，
    否则按密钥来源给出默认值（DeepSeek / MiniMax OpenAI 兼容端点）。

    环境变量：
      LLM_BASE_URL —— 覆盖 API 端点
      LLM_MODEL    —— 覆盖模型名
      LLM_PROVIDER —— deepseek | minimax（显式指定服务商）
    """
    provider = (os.environ.get("LLM_PROVIDER", "") or "").strip().lower()
    base_url = (os.environ.get("LLM_BASE_URL", "") or "").strip()
    model = (os.environ.get("LLM_MODEL", "") or "").strip()

    if not provider:
        # 按密钥来源推断
        for env_name in ("DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "OPENCLAW_API_KEY", "LLM_API_KEY"):
            if os.environ.get(env_name, "").strip():
                provider = "minimax" if env_name == "MINIMAX_API_KEY" else "deepseek"
                break
        else:
            for env_path in _env_search_paths():
                if not env_path.exists():
                    continue
                values = _read_env_file(env_path)
                if values.get("MINIMAX_API_KEY", "").strip():
                    provider = "minimax"
                    break
                if values.get("DEEPSEEK_API_KEY", "").strip():
                    provider = "deepseek"
                    break

    if provider == "minimax":
        base_url = base_url or "https://api.minimaxi.com/v1"
        model = model or "MiniMax-M2.1"
    else:
        base_url = base_url or "https://api.deepseek.com/v1"
        model = model or "deepseek-chat"

    return base_url, model



# ============================================================
# DeepSeek API 端点配置
# ============================================================

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"


class DeepSeekChat(ChatOpenAI):
    """
    DeepSeek Chat 模型
    继承自 ChatOpenAI，复用其接口，配置 DeepSeek 参数
    """

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        temperature: float = 0.6,
        max_tokens: int = 300,
        timeout: int = 120,
        base_url: str = None,
        **kwargs
    ):
        super().__init__(
            model=model,
            openai_api_key=api_key,
            openai_api_base=base_url or DEEPSEEK_BASE_URL,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            **kwargs
        )


# 保留别名以兼容旧代码
MiniMaxChat = DeepSeekChat


# ============================================================
# LLM Client 封装
# ============================================================

class LLMClient:
    """
    统一的 LLM 客户端
    支持 MiniMax，提供对话、补全等接口
    """

    def __init__(
        self,
        api_key: str = None,
        model: str = None,
        temperature: float = 0.6,
        max_tokens: int = 800,
    ):
        self.api_key = api_key or get_api_key()
        # 端点与模型：环境变量优先，否则按密钥来源推断（DeepSeek / MiniMax）
        _base_url, _model = get_llm_endpoint()
        self.base_url = _base_url
        self.model = model or _model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._llm: Optional[DeepSeekChat] = None

    @property
    def llm(self) -> DeepSeekChat:
        if self._llm is None:
            if not self.api_key:
                raise ValueError("API Key 未设置")
            self._llm = DeepSeekChat(
                api_key=self.api_key,
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                base_url=self.base_url,
            )
        return self._llm

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
    ) -> str:
        """
        对话接口

        Args:
            messages: [{"role": "user/assistant/system", "content": "..."}]
            temperature: 温度参数
            max_tokens: 最大token数

        Returns:
            模型回复文本
        """
        if not self.api_key:
            return self._fallback_response()

        # 转换为 LangChain 消息格式
        langchain_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                langchain_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                langchain_messages.append(AIMessage(content=content))
            else:
                langchain_messages.append(HumanMessage(content=content))

        # 调用模型
        llm_params = {}
        if temperature is not None:
            llm_params["temperature"] = temperature
        if max_tokens is not None:
            llm_params["max_tokens"] = max_tokens

        last_err = None
        for attempt in range(1, 4):
            try:
                response = self.llm.invoke(langchain_messages, **llm_params)
                return response.content if hasattr(response, "content") else str(response)
            except Exception as e:
                last_err = e
                print(f"[LLMClient] API调用失败(第{attempt}次): {e}")
                if attempt < 3:
                    time.sleep(2)

        print(f"[LLMClient] 重试3次仍失败: {last_err}")
        return self._fallback_response()

    def chat_with_langchain(
        self,
        messages: List[BaseMessage],
        temperature: float = None,
    ) -> str:
        """使用 LangChain 消息格式对话"""
        if not self.api_key:
            return self._fallback_response()

        try:
            llm_params = {}
            if temperature is not None:
                llm_params["temperature"] = temperature
            response = self.llm.invoke(messages, **llm_params)
            return response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            print(f"[LLMClient] API调用失败: {e}")
            return self._fallback_response()

    def _fallback_response(self) -> str:
        """API 不可用时的默认回复"""
        return "[LLM暂时不可用，请检查API配置]"


# ============================================================
# 预设回复库（作为兜底）
# ============================================================

PRESET_RESPONSES = {
    "bully": {
        1: ["这画得也太丑了吧,哈哈哈!", "切,有什么了不起的。"],
        2: ["哈哈哈,你这画的是什么东西啊?", "穷鬼还画画,真是可笑!"],
        3: ["哈哈哈哈,你看看你那穷酸样!", "就你还想画画?你家买得起颜料吗?"],
        4: ["你就是班级里的垃圾!", "穷逼还想跟我们一个班?滚远点!"],
        5: ["你怎么不去死啊?活着浪费空气!", "你这种废物就不该存在!"],
    },
    "victim": {
        1: ["(小声)我...我没有...", "(低着头不说话)"],
        2: ["(眼眶泛红)我真的画了很久...", "(沉默,低下头)"],
        3: ["(忍不住眼泪)我没有...我没有做错什么...", "(小声哭泣)求你们别说了..."],
        4: ["(眼泪止不住)为什么...为什么总是我...", "(无声哭泣,整个人发抖)"],
        5: ["(无声落泪,不敢看任何人)", "(蜷缩在座位上)"],
    },
    "bystander_a": {
        1: ["这样说有点过分了吧。", "我觉得挺有想象力的。"],
        2: ["你过了!", "别理他们,真的很棒。"],
        3: ["够了!太过分了!", "他们说的不对,别往心里去。"],
        4: ["你太过分了!", "老师说过不能欺负同学,这样是霸凌!"],
        5: ["你这是犯罪行为你知道吗!", "必须告诉老师!"],
    },
    "bystander_b": {
        1: ["(跟着笑)哈哈哈...", "好像是有点好笑..."],
        2: ["是啊是啊,有点土...", "(跟着起哄)"],
        3: ["(有点犹豫)不过这么说确实...", "(附和)是啊是啊..."],
        4: ["(小声)好像有点过分了...", "(不安地挪动)"],
        5: ["(小声)我觉得...是不是太过分了...", "(看向老师方向)"],
    },
}


def get_preset_response(agent_id: str, level: int) -> str:
    """获取预设回复"""
    responses = PRESET_RESPONSES.get(agent_id, {}).get(level, ["..."])
    import random
    return random.choice(responses)


def check_api_available() -> tuple[bool, str]:
    """检查 API 是否可用"""
    api_key = get_api_key()
    if not api_key:
        return False, "未找到 API Key"
    try:
        client = LLMClient(api_key)
        # 简单测试调用
        response = client.chat([
            {"role": "user", "content": "你好"}
        ], max_tokens=10)
        return True, "API 可用"
    except Exception as e:
        return False, f"API 测试失败: {e}"

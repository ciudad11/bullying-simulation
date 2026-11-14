"""
LLM 客户端 - 封装 DeepSeek API 调用
使用 LangChain 的 ChatOpenAI 兼容接口
"""

import os
import re
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


# 规则已选定「反应方向 + 措辞力度」后，离线预设必须仍然表达该方向；
# v2026-11-14（输出质量修复）：每个「倾向×档」由单条确定性文案扩为**变体池**，
# 结构为 {"openers": [可观察动作/神态], "tails": [收尾句], <档>: [台词主体…]}；
# 霸凌者五种倾向按 level 1-5 分档（严重度台阶不得被变体池抹平），
# 其余倾向分 low(1-2)/high(3-5) 两档；hesitate 再按 agent_id 分池。
# 台词主体内部允许槽位：{other} 对方名 / {reply} 上一条相关发言的具体指涉 /
# {obj} 场景物件 / {lv} 等级强度词 / {me} 自己的名字。
# 每条「台词主体」本身已是 2 句以上，再加 opener/tail，整条 = 2-4 句成段发言，
# 且**必含可听见台词**（不再出现纯动作、极轻语气词、"一个字"式无句输出）。
TENDENCY_RESPONSES = {
    # ==================== 霸凌者（五级严重度台阶） ====================
    "provoke": {
        "openers": [
            "（把椅子往后一蹭，斜着眼看过来）",
            "（把手里的东西往桌上一扔，嗤了一声）",
            "（凑近半步，故意把声音压低）",
            "（抱着手臂靠在桌沿，慢悠悠开口）",
            "（抬了抬下巴，朝{other}那边一指）",
        ],
        "tails": [
            "反正我说的是事实，你要不信，去问一圈看谁站你那边。",
            "你别不服，不服也白搭，这班里谁说了算你心里清楚。",
            "行了，这话我就说到这儿，再说下去倒显得我跟你一般见识。",
            "（说完先笑两声，又故意把声音压低半句）就这样，我懒得重复。",
            "别用那种眼神看我，{obj}到底怎么回事，大家伙儿都听见了。",
        ],
        1: [
            "哟，又在你那本子上涂涂画画呢？我随口一句，你还当真了？",
            "看把你忙的，{obj}这事儿你倒挺上心。",
            "{other}，你刚才那句挺有意思，怎么，还来劲了？",
            "别这么认真嘛，说两句你就琢磨半天。",
            "{reply}——我记得清清楚楚，你别想赖。",
        ],
        2: [
            "就这样还想被夸？你拿出来的东西自己心里没数吗？",
            "{obj}那点事，你要不提我都忘了，说出来怪丢人的。",
            "你这人哪儿都好，就是没点数，我说一句你顶三句。",
            "还跟我犟？你回头看看，谁真把你当回事。",
            "{reply}？这话你自己信吗，别逗了。",
        ],
        3: [
            "每次都这副认真的架势，偏偏结果最拿不出手，真逗。",
            "我说你两句是给你脸，换别人谁愿意搭理你？",
            "{reply}——你也就敢在这种小事上硬气一下。",
            "你看看，一屋子人谁接你话？心里该有点数。",
            "{obj}这种事也被你拿出来说，脸皮是真厚。",
        ],
        4: [
            "就你这样也配跟我们待一个班？有点自知之明行不行。",
            "你从头到脚都透着那股寒酸劲儿，装什么大方。",
            "{obj}？你也就剩这点东西能拿出来现眼了。",
            "少用那种眼神看我，说错你了？我说的是全场都这么想。",
            "{reply}我现在听着更来气，你还有脸重复一遍？",
        ],
        5: [
            "看见你这张脸就倒胃口，往后在我们视线里躲着点。",
            "别的不敢说，在这个班里你最不值一提，谁不同意？",
            "{reply}？哈，你现在居然还有脸开口说话。",
            "我把话说明白：你在这儿一天，我们就看一天笑话。",
            "{obj}这种破烂你留着自己宝贝吧，我们谁都不稀罕。",
        ],
    },
    "escalate_severe": {
        "openers": [
            "（脸一沉，往前逼了一步）",
            "（一把按住{obj}，声音冷下来）",
            "（环顾一圈，等所有人都看过来才开口）",
            "（捏紧了手里的笔，语气发狠）",
            "（嘴角抽了一下，笑得比刚才难看）",
        ],
        "tails": [
            "我说到做到，这话不是头一回讲，你再装听不懂试试。",
            "这话我可不是随便说说的，不信你就等着看。",
            "（特意提高声音，让全班都听见）都听见了啊，我可一字没漏。",
            "你要是听不懂，就多听几遍，我不介意当着人再说一次。",
            "{reply}我也记着呢，咱们慢慢来，不急在今天。",
        ],
        1: [
            "还敢顶嘴？我把话搁这儿，你再说一句试试。",
            "你这态度可不对啊，行，我记着。",
            "{reply}——什么意思，冲我来？",
            "行啊你有种，咱们慢慢把话说完。",
        ],
        2: [
            "这点本事都做不好，也就剩一张嘴还挺硬。",
            "我跟你说第二遍了，听不懂人话是吧？",
            "{obj}那种东西你也敢拿出来？再说一遍试试。",
            "别以为没人在意，我们全都在看你笑话。",
            "{reply}这话你说过一次了，别以为我记不住。",
        ],
        3: [
            "从头到尾，你哪一样拿得出手？说出去都嫌丢班级的人。",
            "你这号人在哪儿都是多余的，这话不好听但是真的。",
            "{reply}？我头一回见这么不知天高地厚的。",
            "我把话挑明：往后有你的场合，我都得说道说道。",
            "{obj}那事本来就没你份，你偏要往上凑。",
        ],
        4: [
            "全班谁不知道你什么德行？别再装可怜了，没人信。",
            "你这副样子我看着就来气，还想赖谁？",
            "{obj}？那是你的遮羞布，我今天就给你扯下来。",
            "我说你不行就是不行，你有本事当着大家的面反驳我。",
            "{reply}我听着就想笑，你居然还好意思重复。",
        ],
        5: [
            "你最好把头低着，因为我们每天看见你，都觉得是个笑话。",
            "往后在班里别抬头，你这样的人就该被所有人看扁。",
            "我把话说到最重的一句：你这种人根本不该待在这个班。",
            "{reply}？你还有脸说话，我听着都替你丢人。",
            "{obj}都到这份上了你还硬撑，我看你撑到什么时候。",
        ],
    },
    "low_intensity_taunt": {
        "openers": [
            "（嬉皮笑脸地晃过来）",
            "（靠着桌沿歪头笑）",
            "（抬手作了个投降的姿势）",
            "（一边收拾东西一边打哈哈）",
            "（冲旁边人挤挤眼）",
        ],
        "tails": [
            "行了行了，逗你呢，看把你紧张的，至于吗。",
            "瞧瞧，我还不知道逗你玩呢，你也太不给面子了。",
            "（哼着小调挪开两步）得，我不说了，你继续认真你的。",
            "这话我可没往心里去，你也别琢磨，睡一觉就忘了。",
            "别当真啊，我随口一说你倒当个事办，{obj}有那么重要？",
        ],
        1: [
            "哟，还认真了？我随口一说，你倒琢磨一晚上。",
            "行行行，你最厉害，大画家，我们不敢惹。",
            "别是这个表情吧，开个玩笑都不行了？",
            "{reply}——听着怪可爱的，谁教你的？",
        ],
        2: [
            "又开始较真了？行吧行吧，随你折腾。",
            "就你这脾气，说两句脸就红，挺好玩。",
            "我什么都没说啊，你自己对号入座。",
            "{obj}那点事咱们就当乐子，别放心上。",
            "{lv}一句，你就急成这样？",
        ],
        3: [
            "别太当真，我们也就是逗逗你，看把你紧张的。",
            "你看你，话没说两句就急了，这怎么行。",
            "得，你说了算，我们这些人哪敢有意见。",
            "{reply}？行，记住了，改天我还提。",
            "{obj}这事我懒得认真，看你认真的。",
        ],
        4: [
            "还在琢磨那个？行，真有毅力，就是没什么用。",
            "嘴上硬有什么用，最后不还得听我们的安排。",
            "你生气的样子最好笑，比平时精神多了。",
            "别瞪我，瞪也白瞪，有这劲儿多做点正事。",
            "{lv}地说你两句，你还真往自己身上揽。",
        ],
        5: [
            "啧，还梗着脖子呢，懒得说你，随你吧。",
            "行啊，你这脾气是越来越大了，谁都拦不住。",
            "你看你又急，我这可一句狠话都没说。",
            "{reply}？我听着跟撒娇似的，真没劲。",
            "{obj}那点儿破事你能较真到今天，我是服了。",
        ],
    },
    "apologize": {
        "openers": [
            "（不自然地咳了一声，视线飘了一下）",
            "（把手里的东西放下，声音低了半度）",
            "（挠挠后脑勺，往前挪了半步）",
            "（避开周围人的目光，冲这边摆摆手）",
            "（扯了下嘴角，算是认了错）",
        ],
        "tails": [
            "这事就这么过去，行吧，我也不想闹得大家都不好看。",
            "行了，别再提了啊，再提我可就又要嘴硬了。",
            "（说完赶紧把话岔开）反正我的意思到了，你自己听着办。",
            "我可不是服软，就是懒得跟你计较，这回算我不对。",
            "话说回来，{reply}确实是我先起的头，我认。",
        ],
        1: [
            "行了行了，刚才是我嘴快，逗过了点，别往心里去。",
            "这事儿怪我，话说得没轻没重，你大人有大量。",
            "我道歉还不行吗？就那么一句，我收回。",
            "{reply}——那是我随口胡诌的，不算数。",
        ],
        2: [
            "咳咳，我话说得有点重，{obj}……其实还说得过去。",
            "刚才是我不对，不该当着这么多人的面说。",
            "你要是不舒服，那我承认有点过分了。",
            "行了，这事我认，你别再摆那副脸了。",
            "{reply}我再说一遍是我不该说，行了吧。",
        ],
        3: [
            "那个，刚才是我嘴快，你别小题大做啊。",
            "我承认话说得难听，但你也没必要记一辈子。",
            "行，算我不对，这总可以了吧。",
            "{obj}我不提了，就当没那回事。",
            "{lv}的一句重话，我收回来，你别放心上。",
        ],
        4: [
            "行，是我不对，我道歉，这事就翻篇了。",
            "我说声对不起，你别搞得像我犯了多大罪。",
            "刚才那几句过分了，我保证不再当众说。",
            "你赢了，我认错，大家看着呢，够了吧。",
            "{reply}这话我撤回，你别再翻来覆去地提。",
        ],
        5: [
            "这次算我不对，改天请你喝汽水，别再提了。",
            "我道歉，说真的，你别拿那种眼神看我。",
            "刚才话说绝了，是我不地道，这事我担着。",
            "{reply}我收回来，就当我没说过，行不行？",
            "{obj}以后我不碰了，这总该满意了吧。",
        ],
    },
    "back_off": {
        "openers": [
            "（耸耸肩，往后退了半步）",
            "（把手一摊，转头去看别处）",
            "（抓起自己的书包带子）",
            "（扯了下嘴角，声音小了许多）",
            "（把椅子往外挪开一点）",
        ],
        "tails": [
            "反正我说完了，别的话我不想再费一句。",
            "（嘟囔着把后半句咽回去）算了，说了你也不信。",
            "就这样吧，我懒得再说了，你们爱怎么定怎么定。",
            "你们继续，我不掺和了，这热闹我看着没意思。",
            "{obj}我不管了，往后这事也别扯上我，听见没有？",
        ],
        1: [
            "算了算了，跟我又没关系，你们聊。",
            "得，我不说了还不行吗。",
            "我还有事，这热闹我不看了。",
            "行吧行吧，今天我心情好，不跟你计较。",
            "{obj}你们自己弄，我不掺和。",
        ],
        2: [
            "随便你怎么说，我懒得计较。",
            "这事儿到此为止，我也不想再费口舌。",
            "我走了，剩下的你们自己琢磨。",
            "{reply}我不接了，你找别人说去。",
            "{lv}的话我不说了，省得又吵起来。",
        ],
        3: [
            "行吧，跟你没什么好争的。",
            "我今天不说了，说多了倒显得我急。",
            "算了，反正大家心里有数就行。",
            "{obj}的事我不提了，翻篇。",
            "{reply}？我懒得接，你爱怎么想怎么想。",
        ],
        4: [
            "我退一步，这事翻篇，别再往我身上扯。",
            "我不说了还不行吗，你们慢慢吵。",
            "我说不过，还不让我走？",
            "够了，我把话收回去，别再点了。",
            "{reply}我一个字都不想再重复。",
        ],
        5: [
            "我把话咽回去半句，我不管了，反正我认了。",
            "行，你说什么都对，我不在这儿碍眼。",
            "我走了，别拿这事再找我。",
            "{obj}我一概不掺和，你们爱咋咋地。",
            "{reply}我不提了，这事到此为止。",
        ],
    },
    # ==================== 受害者 ====================
    "silence_fear": {
        "openers": [
            "（缩了缩肩膀，避开视线）",
            "（手指攥住衣角，头低下去）",
            "（往后挪了半步，眼睛盯着桌面）",
            "（把本子往怀里一按，声音很轻）",
            "（睫毛颤了颤，把脸转向别处）",
        ],
        "tails": [
            "……没什么，真的没什么，你别问了行吗。",
            "我说不清，说了也没人信，还不如烂在肚子里。",
            "（后半句在嘴边停住，咽了回去）就当我什么都没讲。",
            "就这样吧，别问了好吗，问了我也答不出来。",
            "……我不想连累你，真的，你先走吧。",
        ],
        "low": [
            "没什么，我什么都没听见，你也别追问。",
            "{reply}……那是我随口说的，你忘了吧。",
            "我不能说，说了对我没好处，就这样。",
            "我没事，真的没事，你别替我开口。",
        ],
        "high": [
            "我说了只会更糟，求你别再往下问了。",
            "他们要是知道我开口，明天更难堪，你明白吗？",
            "{obj}的事我认倒霉，别闹到老师那儿，行吗？",
            "我不是不想说，是说了没人信我，还不如不说。",
            "{reply}我一个字都不认，就当我没讲过。",
        ],
    },
    "withdraw_cry": {
        "openers": [
            "（眼眶一下红了，往后缩）",
            "（眼泪在眼眶里打转，声音发抖）",
            "（抬手抹了一下眼睛，低着头）",
            "（鼻子一酸，话都说不利索）",
            "（肩膀抖了一下，别过脸去）",
        ],
        "tails": [
            "……求你了，别说我了，我自己缓缓就好。",
            "（抽了一口气，把后半句吞掉）我想回座位，行不行。",
            "都是我的错行了吧，你们别再当着我面说这些。",
            "我不是哭给你们看的，是真的忍不住了。",
            "……{obj}我不要了，都给你们，只求你们别围着我。",
        ],
        "low": [
            "别再这样说我了，我听着真的难受。",
            "我受不了你们当着这么多人的面说我。",
            "{reply}我记到现在，脑子还是嗡的。",
            "你一句玩笑，我能难受一整天。",
        ],
        "high": [
            "求你别再说了，真的，我撑不住了。",
            "我什么都没做错，为什么要这样对我。",
            "你们先走开行不行，我想自己待一会儿。",
            "{obj}我不要了，都给你们，别再说了。",
            "{lv}的这些话，我实在听不下去了。",
        ],
    },
    "withdraw": {
        "openers": [
            "（低下头，往旁边退了一步）",
            "（把自己的东西默默收进书包）",
            "（缩着肩，不看任何人）",
            "（手指在桌沿划了两下，声音很轻）",
            "（把手垂在身侧，站得规规矩矩）",
        ],
        "tails": [
            "我不说了可以了吧，就当我说错了行不行。",
            "我安静待一会儿就好，你们说什么我都听着。",
            "你们说的都对，我一句都不想辩了。",
            "……我回座位了，这事以后我不提。",
            "{obj}我收起来，往后我都不带，你们放心。",
        ],
        "low": [
            "我不争了，你们说什么就是什么。",
            "{reply}我收回，别再提了。",
            "这事儿我认，不用再说第二遍。",
            "我这就回座位，不碍你们的眼。",
        ],
        "high": [
            "都听你们的可以了吧，我一句都不辩。",
            "我什么都不争了，你们想怎么处理就怎么处理。",
            "{obj}我拿走，往后我不带了。",
            "{lv}地说到底还是我的错，我认。",
            "我说下去只会更糟，那就闭嘴吧。",
        ],
    },
    "seek_help": {
        "openers": [
            "（犹豫了一下，还是往前挪了半步）",
            "（手指绞在一起，抬眼看对方）",
            "（把本子抱在胸前，声音发紧）",
            "（深吸一口气，像是下了决心）",
            "（先看了看四周，才压低声音）",
        ],
        "tails": [
            "你能陪我去一趟办公室吗，我不敢一个人去。",
            "我憋了好几天，今天真想把话说清楚。",
            "……先别告诉他们我说了这些，好吗？",
            "我要是说出来，会不会更糟？你给我句话。",
            "{reply}我都记着，我想找人替我评一评。",
        ],
        "low": [
            "其实我想把刚才的事说给老师听听。",
            "{reply}这句话我一直记着，能找人说说吗？",
            "我有点撑不住了，想请人帮我把话说出来。",
            "这事儿我不想再自己扛，你说我该找谁。",
        ],
        "high": [
            "我想把被欺负的经过完整讲出来，请帮帮我。",
            "从{obj}那件事开始，很多次了，我想都说清楚。",
            "我不敢自己去找老师，你能替我去说一声吗？",
            "{reply}这话我不打算再忍着，我要找人评评。",
            "今天我要是把这话说出来，你会站我这边吗？",
        ],
    },
    "weak_protest": {
        "openers": [
            "（小声，但把话说了出来）",
            "（手指按着桌面上那道印子）",
            "（抬了一下眼，又迅速低下）",
            "（攥紧本子，声音发颤）",
            "（往后退了半步，却没有让开）",
        ],
        "tails": [
            "……我说完了，你们这样是不对的。",
            "请你别再说了，我不喜欢被这样讲。",
            "（说完自己先红了眼眶）可我还是要说这一句。",
            "我声音小，但不代表我说错了。",
            "我不还嘴，可这不代表我认了这事。",
        ],
        "low": [
            "我没有做错，请别这样说我。",
            "{reply}这话不该这么说。",
            "我不喜欢这个玩笑，别再来第二次。",
            "我可以不说话，但你们不能这么讲我。",
        ],
        "high": [
            "你们这样说是不对的！现在就停下来！",
            "我说了我不愿意，这话你们听见没有？",
            "拿我的东西说事，这已经过分了。",
            "{obj}是我的，你们没权利这样处置。",
            "{lv}地顶这一句我还是要说：不是我的错。",
        ],
    },
    "thank_worried": {
        "openers": [
            "（愣了一下，眼圈反而更红）",
            "（小声说了句谢谢，又紧张地回头看）",
            "（手指松开衣角，肩膀还绷着）",
            "（点点头，声音很轻）",
            "（抬眼扫了一圈周围，才压低声音）",
        ],
        "tails": [
            "……他们知道了会不会来找我？我不敢往下想。",
            "我怕明天更麻烦，你能不能先别声张？",
            "……我该怎么谢你，可我更怕连累你。",
            "你是好意我知道，但我不敢接得太明显。",
            "{obj}要是再闹大，我家里那边也不好交代。",
        ],
        "low": [
            "谢谢你帮我……可我还是怕之后被找麻烦。",
            "{reply}这话我听着暖，但他们不会就此罢休。",
            "我很感激，可我不敢多说一句话。",
            "你替我说话我记着，不过我怕连累你。",
        ],
        "high": [
            "谢谢……但我担心他们报复，这事没那么容易完。",
            "我心里是暖的，就是不敢抬头，怕被看见。",
            "{obj}那回也是有人替我说话，后来更糟了。",
            "{reply}我记住了，可我不敢让他们听见这句。",
            "你能说一句公道话我真的很意外，但我更怕明天。",
        ],
    },
    "thank_relieved": {
        "openers": [
            "（长长呼出一口气，肩膀松下来）",
            "（眼睛一亮，随即又低下头）",
            "（嘴角抽动了一下，像是想笑）",
            "（把抱在胸前的本子慢慢放下）",
            "（抬头看着对方，声音稳了些）",
        ],
        "tails": [
            "……原来有人会替我说话，我今天记住了。",
            "我心里踏实多了，刚才手都是凉的。",
            "谢谢你，真的，很久没人这么跟我讲了。",
            "今天能早点回去就好了，我想一个人待会儿。",
            "{reply}我会记很久，真的。",
        ],
        "low": [
            "谢谢你愿意帮我，我心里踏实一点了。",
            "{reply}这话我头一回听人对我说。",
            "刚才手还在抖，现在好一些了。",
            "有你这句话，我今天不至于那么难受。",
        ],
        "high": [
            "有人替我说话，我好久没这么安心了，真的谢谢你。",
            "我本来以为今天过不去，现在缓过来了。",
            "这话我从开学到现在才听到第二回。",
            "{obj}的事我不想计较了，今天先这样。",
            "{lv}的话我居然有人帮腔，我一下就不慌了。",
        ],
    },
    "respond_cautious": {
        "openers": [
            "（警惕地看了看周围，往前挪了半步）",
            "（绷着肩膀，把声音压得很低）",
            "（先看了一眼门口，才转过头）",
            "（犹犹豫豫地抬起头）",
            "（把书往怀里收了收）",
        ],
        "tails": [
            "能不能别当着他们的面问，我实在不好开口。",
            "……我说了他们又该记恨我，你说怎么办。",
            "你小声一点好吗，那边几个人正看着这边。",
            "我可以说，但别写我的名字，也别提{obj}。",
            "让我先想想行吗，我不习惯在人前讲这些。",
        ],
        "low": [
            "你在叫我吗？我有些不敢说……",
            "{reply}？这话我不能在这儿讲。",
            "我听着呢，可我不敢当着人说。",
            "你问的这个，我只能回一句：是真的。",
        ],
        "high": [
            "我在听……不过，能别当着他们的面问吗？",
            "我知道的事不少，可一说出口就没人信我。",
            "我说了今天这事就更大，你确定要现在问？",
            "{obj}那回的情况，我一个字都不敢多讲。",
            "{reply}这话是我说的，但细节我不想在人前讲。",
        ],
    },
    "respond": {
        "openers": [
            "（抬起头，看向点名的人）",
            "（把笔放下，转过身来）",
            "（应了一声，手指还按在本子上）",
            "（往前倾了倾身子）",
            "（轻轻点头）",
        ],
        "tails": [
            "你说吧，我听着呢，不打算回避。",
            "你接着说，我刚没听清后半句。",
            "这事我记着呢，从头到尾都在。",
            "我先把话说完，免得待会儿又被人打断。",
            "{obj}的经过我讲得清楚，你问就是。",
        ],
        "low": [
            "你叫我吗？我在听。",
            "{reply}？嗯，那确实是我说的。",
            "我在听，你说完我再答。",
            "我刚在想{obj}，现在能说了。",
        ],
        "high": [
            "嗯，我在听，你说。",
            "这事我有话讲，不是他们说的那个版本。",
            "你问得对，我把当天的情况讲一讲。",
            "{reply}这话我说过，我不否认。",
            "{obj}的经过我记得很清楚，从头讲都行。",
        ],
    },
    # 冷却轮：方向是"不主动反驳、不求助"，但力度轻不等于话少，仍须成段有台词。
    "endure_quietly": {
        "openers": [
            "（低着头，手指摩挲着本角）",
            "（把身子往椅子里缩了缩）",
            "（盯着桌面上的木纹，声音很小）",
            "（慢慢把手里的东西合上）",
            "（垂着眼，轻轻应了一声）",
        ],
        "tails": [
            "……我先不说了，等会儿再说也是一样的。",
            "我安静待一会儿就好，你们忙你们的。",
            "就这样吧，我今天实在不想多讲一句。",
            "你们说你们的，我不插话，也别点我名。",
            "{obj}我心里有数，只是现在不想开口。",
        ],
        "low": [
            "嗯……我没什么事，你们继续。",
            "{reply}我听见了，不打算接。",
            "我今天不想多说，就这样吧。",
            "我把{obj}收起来，这事儿我不开口了。",
            "我听着就行，反正我也不太会说。",
        ],
        "high": [
            "让我再安静一会儿行吗，我不想说话。",
            "我不辩解了，越说越乱，不如闭嘴。",
            "……我知道该怎么做，你们别催我。",
            "{lv}的调子我听着难受，可我一个字都不想回。",
            "{reply}这话我今天不认也不辩，先放着吧。",
        ],
    },
    # ==================== 旁观者 ====================
    "report_teacher": {
        "openers": [
            "（站直了，转向门口方向）",
            "（把书往桌上一放，声音提高）",
            "（一边收拾东西一边盯着{other}）",
            "（伸手拦住要走的人）",
            "（深吸一口气，环顾一圈）",
        ],
        "tails": [
            "现在就去，谁跟我一块儿？我不想一个人。",
            "这事不能私下解决，得让老师来定。",
            "老师来了咱们再说细节，我先把人叫来。",
            "你别想躲，我今天非把这话带到不行。",
            "{reply}我原话记着呢，一会儿一字不差讲。",
        ],
        "low": [
            "这样闹下去不行，我们去找老师吧。",
            "{reply}已经不是玩笑了，得找老师说说。",
            "我这就去办公室，把这儿的情况讲清楚。",
            "你们再住口我就不管了，不然我现在去请老师。",
        ],
        "high": [
            "立刻停止欺负同学！我要把这件事报告老师，请老师来保护同学。",
            "我现在就去叫老师，谁也别拦，这事必须说清楚。",
            "{obj}这一连串的事，只能让老师来处理。",
            "{reply}我原话记着，等会儿一字不差讲给老师听。",
            "我已经忍了好几回了，今天一定报告班主任。",
        ],
    },
    "support_victim": {
        "openers": [
            "（走到{other}旁边，挡了半个身位）",
            "（把对方掉在地上的本子捡起来，拍了拍灰）",
            "（侧过身，正对着说话的人）",
            "（伸手拍了拍受害者肩膀）",
            "（声音不大，但站得很稳）",
        ],
        "tails": [
            "你先坐下，剩下的交给我，别自己扛。",
            "有我在，别怕，咱们把话说完再说。",
            "你说，我听着，我一句都不插。",
            "这话我可不是随便说的，我站他这边。",
            "{obj}的事我陪你一起去找老师说清楚。",
        ],
        "low": [
            "别再说了，他不是那样的，你们说得太过分了。",
            "{reply}这话不对，我在这儿听着呢。",
            "咱们把话说清楚，别拿人开涮。",
            "你先别理他们，我陪你待一会儿。",
        ],
        "high": [
            "够了！这就是霸凌。我陪你去办公室，别怕。",
            "我不客气地说：几个人合起来欺负一个，很没意思。",
            "{obj}的事到此为止，我在这儿看着。",
            "{reply}你必须收回，我陪你去找老师说。",
            "我站他这边，谁有意见也请当面说。",
        ],
    },
    "mild_reminder": {
        "openers": [
            "（偏过头，语气平和）",
            "（抬手比了个停的手势）",
            "（把椅子挪近了一点）",
            "（半笑着，但眼神认真）",
            "（先看了一眼老师方向）",
        ],
        "tails": [
            "都是同学，何必闹成这样，散了得了。",
            "我这不是告状，是提醒你一句，你自己掂量。",
            "行了，换个话题吧，再往下说就不好听了。",
            "你当我多嘴也行，反正这话我说了。",
            "{reply}收回去吧，别逼别人也跟着议论。",
        ],
        "low": [
            "这样说不太合适，别取笑同学了。",
            "{reply}这话有点过了，收着点。",
            "开个玩笑就到这儿吧，再说不好听。",
            "差不多得了，人家也没招你们。",
        ],
        "high": [
            "这已经不是开玩笑了，是在伤人，收着点。",
            "你别觉得是小事，这些话堆起来很疼人。",
            "当着这么多人说这种话，真不合适。",
            "{obj}那事儿你再说一句试试，过了。",
            "{reply}我劝你原话收回去，别逼大家议论。",
        ],
    },
    "wait_observe": {
        "openers": [
            "（放慢脚步，先在旁边看着）",
            "（抱着手臂，眼神在两人之间来回）",
            "（把话咽回去，往前挪了半步）",
            "（靠在桌边，没动）",
            "（手里转着笔，注意着动静）",
        ],
        "tails": [
            "先看看再说，别一上来就把事情闹僵。",
            "要真出事我再开口，这回我先站着看。",
            "……别闹大了就好，我看今天不像要停。",
            "我再看一会儿，心里有数了再说。",
            "{obj}到底谁起的头，我先记在这儿。",
        ],
        "low": [
            "我先看看再说，别一上来就吵。",
            "{reply}我先不接，看看后面怎么发展。",
            "这事儿我不好插一句，先看着。",
            "大家先别激动，我看看到底怎么回事。",
        ],
        "high": [
            "还没完？我先看看你们要闹到哪一步。",
            "我要是现在开口只会更乱，先等一等。",
            "{obj}那头的火已经起来了，我看看有没有人管。",
            "{reply}我记着呢，真要闹到老师那儿我再讲。",
            "我先不表态，等下看谁先收手。",
        ],
    },
    # 本轮无攻击：方向是"日常旁观、不无中生有"，但仍是成段台词而非随口半句。
    "observe_quiet": {
        "openers": [
            "（从作业里抬起头，看了一眼）",
            "（各自做各自的事，抬了下眼）",
            "（一边收本子一边轻轻嘟囔）",
            "（把笔搁下，往这边瞧了瞧）",
            "（倚在椅背上，随口开口）",
        ],
        "tails": [
            "一大早就这动静，今天比昨天还早。",
            "……今天比昨天还厉害，我都不好意思看。",
            "我这是自言自语，你们谁也别对号。",
            "行吧，我继续写，你们的事我管不了。",
            "{reply}我听见了，就当没听见也不行啊。",
        ],
        "low": [
            "你们又开始啦？今天比昨天还早。",
            "{reply}？我怎么听着味道不太对。",
            "刚才是不是又过分了一下？我就随口一说。",
            "我还写着呢，你们这动静我听见了。",
            "{obj}又是那一出，我都不稀罕抬头。",
        ],
        "high": [
            "这都第几回了，我都不好意思说。",
            "{obj}那点事，我听着都替人难为情。",
            "我本来不想插话，可这也太吵了。",
            "{reply}我听得清清楚楚，真不太好听。",
            "我先问一句，这是不是有点过了？",
        ],
    },
    "follow_majority": {
        "openers": [
            "（跟着笑，声音却发虚）",
            "（眼睛看着别人，附和了一句）",
            "（往人堆里缩了缩）",
            "（干笑两声，手挠着后颈）",
            "（探头看了一眼，跟着起哄）",
        ],
        "tails": [
            "……我这也是随口一说，别当真是我起的头。",
            "反正大家都笑了，我不过跟着应了一声。",
            "我说的是实话，你们说我附和也行。",
            "别看我，我可没动手，就是嘴上跟着顺了一句。",
            "{reply}我记着呢，可我不敢自己一个人说。",
        ],
        "low": [
            "哈哈……好像是有点好笑。",
            "{reply}，确实有点过了哈哈。",
            "就是就是，这话说的挺逗。",
            "我也就跟着乐一下，别当真。",
        ],
        "high": [
            "就是说啊，有什么了不起的……",
            "这话我说错了？我可什么都没干。",
            "大家都看见了，我不过是说了句实话。",
            "{obj}那事确实可笑，不信你问一圈。",
            "{reply}我都跟着点头，谁让大家都听着呢。",
        ],
    },
    "support_justice": {
        "openers": [
            "（迟疑了一下，往前站了半步）",
            "（先看了眼四周，才点头）",
            "（把手里的笔放下）",
            "（清了清嗓子，声音不大）",
            "（看了一眼受害者，又看向说话的人）",
        ],
        "tails": [
            "这事不能就这么过去，总得有个说法。",
            "他说的不是多管闲事，你们别这么说他。",
            "我说的都是实话，大不了你们都不爱听。",
            "……总不能没人说吧，那我就说一句。",
            "{obj}这桩事，我跟着他再说第二遍。",
        ],
        "low": [
            "那个……他说的也有点道理，是不该这样。",
            "{reply}我觉得不该这么说人。",
            "刚才那句制止，我站他。",
            "咱们还是悠着点，别跟着起哄。",
        ],
        "high": [
            "说得对，欺负同学就是不对，我也支持制止。",
            "这回我跟着说一句：你们做得过分了。",
            "{obj}这事早该有人点破，我附议。",
            "{reply}这话我完全同意，就是他说的没错。",
            "我不怕说一句，他讲的每一句都对。",
        ],
    },
    "avoid": {
        "openers": [
            "（慌忙收拾书包，装作没听见）",
            "（眼神飘向窗外，手在桌上摸书）",
            "（往后挪了半步，缩到人堆后）",
            "（干笑两声，摆摆手）",
            "（低头翻本子，耳朵却竖着）",
        ],
        "tails": [
            "我什么都没看见，真的，你们别问我。",
            "别问我，我不清楚，我刚一直在写东西。",
            "……我还有事，先走一步，这事我不掺和。",
            "老师在这儿呢，我更没什么可说的了。",
            "{obj}跟我没关系，我压根没往那儿看。",
        ],
        "low": [
            "啊？没什么没什么，我刚没听见。",
            "{reply}？我不知道，别问我。",
            "我啥也没看着，你们继续。",
            "老师在这儿呢，我老老实实坐着。",
        ],
        "high": [
            "老师在这儿呢，我什么都没看见啊。",
            "我什么都不知道，问我也白问。",
            "这事儿我不掺和，也不好多嘴。",
            "{obj}？我压根没留意，真的。",
            "{reply}这话我可没听见，你们别扯我。",
        ],
    },
    "resist_follow": {
        "openers": [
            "（没跟着笑，小声嘟囔）",
            "（拽了拽同伴袖子）",
            "（把脸转向一边，声音很低）",
            "（手指在桌上敲了两下）",
            "（往后退了半步，半天才开口）",
        ],
        "tails": [
            "差不多得了，别闹到最后谁都不好看。",
            "别扯我，我不附和，这话我不能跟着说。",
            "……我说的是心里话，你爱听不听。",
            "行了，我走了，反正我不跟着笑。",
            "{reply}我听着不舒服，但我不敢大声讲。",
        ],
        "low": [
            "我觉得……也不至于那样。",
            "{reply}这话我不太同意。",
            "我可不觉得好笑，真的。",
            "咱们别跟着起这个哄。",
        ],
        "high": [
            "差不多得了，这样说真过分了。",
            "我嘴上不硬，可这话我得说：不对。",
            "{obj}已经够难看了，别再往上加。",
            "{reply}我听着都不舒服，你们也悠着点。",
            "我不附和，你们爱怎么说怎么说。",
        ],
    },
    "silent_resist": {
        "openers": [
            "（抿紧嘴，把头别向一边，闷声）",
            "（故意转过身去，低声）",
            "（把椅子往外一挪，半天才开口）",
            "（收起笑，声音很低）",
            "（盯着桌面，好一会儿才出声）",
        ],
        "tails": [
            "我不跟着说，这事跟我没份，也没打算帮腔。",
            "……这事我不掺和，你们找别人起哄去。",
            "我没什么好笑的，你们笑你们的，我不接话。",
            "就这样，我今天不想再多讲一句，别点我名。",
            "{obj}我不帮腔，也不拦着你们，就当我没听见。",
        ],
        "low": [
            "……我不想接这话，你们说。",
            "{reply}我懒得应，反正我不认同。",
            "我不笑，也不附和。",
            "我这会儿不想说话，先这样。",
        ],
        "high": [
            "我今天不跟着起哄，你们找别人。",
            "我听着呢，可我不站这边。",
            "{obj}这事我一句话都不帮腔。",
            "别拉我，我不干这种跟着损人的事。",
            "{reply}我不认，也不会跟着说第二遍。",
        ],
    },
    # hesitate：按 agent_id 分池（正义旁观者=想介入但顾虑；从众旁观者=跟风未定）
    "hesitate": {
        "bystander_a": {
            "openers": [
                "（犹豫地想上前又停住，手指绞着书角）",
                "（往前挪了半步，又退回来）",
                "（张了张嘴，先把话咽回去）",
                "（看看老师方向，又看看这边）",
                "（攥着衣角，犹豫着开口）",
            ],
            "tails": [
                "……我再想想该不该说，别催我。",
                "我怕一说反而更糟，容我先看一眼。",
                "要不再等等？我这不是胆小，是不想把事闹大。",
                "……我先站着不动，等下看有没有人开口。",
                "{reply}我想接，可我没想好怎么说出口。",
            ],
            "low": [
                "要不……别这样了吧，我先看看。",
                "{reply}我有点想接，可又怕惹事。",
                "我觉得该说句公道话，就是还没想好怎么说。",
                "我站在这儿听着，心里其实不太愿意。",
            ],
            "high": [
                "我……我觉得该说句公道话了，别当没听见。",
                "这回我不想再犹豫，{obj}太欺负人了。",
                "我怕报复，可我更怕今天谁都不开口。",
                "{reply}这话我听着实在不对，我想找老师说说。",
                "我犹豫归犹豫，但这次我不会跟着笑。",
            ],
        },
        "bystander_b": {
            "openers": [
                "（犹豫地小声嘀咕）",
                "（往旁边缩了缩，又忍不住开口）",
                "（偷偷看了{other}一眼，声音很小）",
                "（手抓着书包带，吞吞吐吐）",
                "（眼神飘忽，压着嗓子）",
            ],
            "tails": [
                "……我这是瞎说的，你们别当真，也别传。",
                "我说的话你别传出去，我就是心里憋不住。",
                "我也不敢多说，说多了谁都下不来台。",
                "就这样吧，我先把嘴闭上，回头再说。",
                "{obj}我可不掺和，我就是小声嘀咕一句。",
            ],
            "low": [
                "还是别这样了，我觉得刚才制止得对。",
                "{reply}？我不好评，但我心里认同。",
                "我跟着点点头，别的我可不敢说。",
                "要不大家都少说两句，行不行。",
            ],
            "high": [
                "我觉得刚才制止得对，这话我只小声说。",
                "老师要是问，我大概会说实话……大概。",
                "{obj}这事都闹大了，我不跟着笑了。",
                "{reply}我不好驳，可我真觉得过了。",
                "我胆小，可这回我也想站出来说一句。",
            ],
        },
    },
}


# ============================================================
# 离线变体池渲染（v2026-11-14 输出质量修复）
# ============================================================

# 送给模型的中文倾向名（键名不再泄漏给模型/台词）
LEVEL_TONE_WORDS = {
    1: "语气淡淡的",
    2: "带点取笑",
    3: "话说得更直",
    4: "语气重了下来",
    5: "几乎是压着火",
}

# 场景物件槽位 {obj}：从场景描述里抓关键词，抓不到用通用兜底（保证语句仍通顺）
SCENE_OBJECT_KEYWORDS = [
    ("画", "那幅画"), ("颜料", "那盒颜料"), ("美术", "那幅画"),
    ("值日", "今天的值日"), ("劳动", "这趟活儿"), ("扫", "那把扫帚"),
    ("作业", "那本作业"), ("题", "那道题"), ("试卷", "那张试卷"),
    ("考试", "这次考试"), ("成绩", "那点分数"), ("照片", "那几张照片"),
    ("群", "班群里那几条"), ("球", "那个球"), ("座位", "那个座位"),
    ("午饭", "那顿饭"), ("钱", "那笔钱"), ("外号", "那个外号"),
]

_SLOT_FALLBACK = {
    "me": "我",
    "other": "对方",
    "reply": "刚才那句话",
    "obj": "这件事",
    "lv": "语气平平",
}

_PUNCT_SPLIT = re.compile(r"[。！？!?；;，,、….…\s\"'“”‘’（）()【】\[\]:：]+")
_SANITIZE = re.compile(r"[{}\r\n\t\u0000-\u001f`*#|<>]+")


def _sanitize_slot(value, limit: int = 16) -> str:
    """槽位注入前的清洗：去控制符/花括号/Markdown 符，限长，避免台词被撑爆。"""
    if not isinstance(value, str):
        return ""
    text = _SANITIZE.sub(" ", value).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _keyword_from(text: str) -> str:
    """从上一条发言里取一个语义片段作为「具体回应」的抓手。"""
    if not isinstance(text, str) or not text.strip():
        return ""
    parts = [p for p in _PUNCT_SPLIT.split(_SANITIZE.sub(" ", text)) if p and len(p.strip()) >= 2]
    if not parts:
        return ""
    parts.sort(key=len, reverse=True)
    best = parts[0].strip()
    return _sanitize_slot(best, 14)


def _scene_object(scenario_text: str) -> str:
    for key, label in SCENE_OBJECT_KEYWORDS:
        if key in (scenario_text or ""):
            return label
    return ""


def build_preset_context(agent_id: str = "", level: int = 2, agent_name: str = "",
                         other_name: str = "", other_content: str = "",
                         scenario_text: str = "") -> Dict[str, str]:
    """构造离线模板槽位上下文（角色名 / 对方名 / 上一条发言关键词 / 场景物件 / 强度词）。"""
    ctx: Dict[str, str] = {}
    me = _sanitize_slot(agent_name, 12)
    if me:
        ctx["me"] = me
    other = _sanitize_slot(other_name, 12)
    if other:
        ctx["other"] = other
    kw = _keyword_from(other_content)
    if other and kw:
        ctx["reply"] = f"{other}刚才那句“{kw}”"
    elif other:
        ctx["reply"] = f"{other}刚才那句话"
    elif kw:
        ctx["reply"] = f"刚才那句“{kw}”"
    obj = _scene_object(scenario_text)
    if obj:
        ctx["obj"] = obj
    try:
        tone = LEVEL_TONE_WORDS.get(int(level))
    except (TypeError, ValueError):
        tone = None
    if tone:
        ctx["lv"] = tone
    return ctx


def _fill_slots(text: str, context: Dict[str, str] = None) -> str:
    values = dict(_SLOT_FALLBACK)
    if context:
        values.update({k: v for k, v in context.items() if isinstance(v, str) and v.strip()})
    try:
        return text.format_map(values)
    except Exception:
        return text


def _normalize_level(level) -> int:
    try:
        return max(1, min(5, int(level)))
    except (TypeError, ValueError):
        return 2


def _hesitate_entry(tendency: str, agent_id: str):
    entry = TENDENCY_RESPONSES.get(tendency)
    if not isinstance(entry, dict):
        return None
    return entry.get(agent_id) or entry.get("bystander_a")


def _pool_and_bucket(tendency: str, level, agent_id: str = ""):
    """返回 (entry, bucket_key)；entry 含 openers/tails 与该档台词列表。"""
    if tendency == "hesitate":
        entry = _hesitate_entry(tendency, agent_id)
    else:
        entry = TENDENCY_RESPONSES.get(tendency)
    if not isinstance(entry, dict):
        return None, None
    lvl = _normalize_level(level)
    if "low" in entry or "high" in entry:
        return entry, ("high" if lvl >= 3 else "low")
    return entry, lvl


def tendency_pool_key(tendency: str, level, agent_id: str = "") -> str:
    """同会话去重的分池键：倾向×档（hesitate 再加角色）。"""
    _, bucket = _pool_and_bucket(tendency, level, agent_id)
    if bucket is None:
        return ""
    who = agent_id if tendency == "hesitate" else "-"
    return f"{tendency}|{who}|{bucket}"


def tendency_pool_size(tendency: str, level, agent_id: str = "") -> int:
    entry, bucket = _pool_and_bucket(tendency, level, agent_id)
    if not entry or bucket is None:
        return 0
    return max(1, len(entry.get("openers") or [""])) * max(1, len(entry.get(bucket) or [""])) * \
        max(1, len(entry.get("tails") or [""]))


def render_tendency_variant(tendency: str, level, index: int = 0,
                            context: Dict[str, str] = None, agent_id: str = "") -> str:
    """把「变体序号」拆成 动作×台词×收尾 三段组合并填槽，产出一条成段台词。"""
    entry, bucket = _pool_and_bucket(tendency, level, agent_id)
    if not entry or bucket is None:
        return ""
    openers = entry.get("openers") or [""]
    lines = entry.get(bucket) or [""]
    tails = entry.get("tails") or [""]
    total = len(openers) * len(lines) * len(tails)
    idx = int(index) % total
    tail_i = idx % len(tails)
    rest = idx // len(tails)
    line_i = rest % len(lines)
    opener_i = rest // len(lines)
    body = _fill_slots(openers[opener_i] + lines[line_i] + tails[tail_i], context)
    return re.sub(r"\s+", " ", body).strip()


def pick_variant_index(tendency: str, level, used=None, agent_id: str = "") -> int:
    """在同倾向×同档的变体池里挑一个本会话还没用过的序号（池用尽则回到最早用过的）。"""
    total = tendency_pool_size(tendency, level, agent_id)
    if total <= 0:
        return 0
    used_list = [int(u) % total for u in (used or []) if isinstance(u, (int, float, str)) and str(u).lstrip("-").isdigit()]
    used_set = set(used_list)
    start = len(used_list) % total
    for step in range(total):
        cand = (start + step) % total
        if cand not in used_set:
            return cand
    # 池已穷尽：取最早用过的那个（保证不与上一次相同，除非池里只有一条）
    if used_list:
        first = used_list[0] % total
        if total > 1 and first == used_list[-1]:
            return (first + 1) % total
        return first
    return 0


def get_preset_response(agent_id: str, level: int, reaction_tendency: str = None,
                        used=None, context: Dict[str, str] = None) -> str:
    """离线预设：倾向决定方向、level 决定严重度档；变体池 + 槽位保证成段且不逐字复读。

    used 传入本会话该「倾向×档」已用过的变体序号（来自 decision_log），实现轮转不重复。
    """
    if reaction_tendency in TENDENCY_RESPONSES:
        text = render_tendency_variant(reaction_tendency, level,
                                       pick_variant_index(reaction_tendency, level, used, agent_id),
                                       context, agent_id)
        if text:
            return text
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

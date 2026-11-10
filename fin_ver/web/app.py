#!/usr/bin/env python3
"""
Campus Verbal Bullying & Teacher Intervention Training System v3.0
======================================
Based on LangGraph Multi-Agent Architecture

Run:
    python3 -m uvicorn app:app --reload --port 5000
"""

# Load .env
from dotenv import load_dotenv
import os
# R17：.env 可能位于项目根或 web/ 下，两个位置都加载（不覆盖已有环境变量）
#      utf-8-sig：兼容带 BOM 的 .env（否则键名会带上 U+FEFF 而不被识别）
for _env_path in (os.path.join(os.path.dirname(__file__), "..", ".env"),
                  os.path.join(os.path.dirname(__file__), ".env")):
    if os.path.exists(_env_path):
        try:
            load_dotenv(_env_path, override=False, encoding="utf-8-sig")
        except TypeError:  # 旧版 python-dotenv 不支持 encoding 参数
            load_dotenv(_env_path, override=False)

import asyncio
import datetime
import json
import math
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Dict, List

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn

from session_manager import SessionManager, NEW_MULTI_AGENT_AVAILABLE, load_config
from database import (
    init_db, create_user, authenticate, create_token, verify_token,
    delete_token, get_user_by_id, get_all_users,
    create_session as db_create_session, update_session_status,
    save_report, save_log, get_user_sessions,
    get_report_by_session, get_log_by_session, get_user_reports,
    admin_get_all_sessions, admin_get_user_detail,
    get_user_interventions, create_user_intervention,
    update_user_intervention, delete_user_intervention,
    get_shared_interventions, create_shared_intervention,
    update_shared_intervention, delete_shared_intervention,
    get_user_scenarios, create_user_scenario,
    update_user_scenario, delete_user_scenario,
    get_shared_scenarios, create_shared_scenario,
    update_shared_scenario, delete_shared_scenario,
    get_user_participants, get_user_participant, create_user_participant,
    update_user_participant, delete_user_participant,
    get_shared_participants, get_shared_participant, create_shared_participant,
    update_shared_participant, delete_shared_participant,
    get_conn
)

# 初始化数据库
init_db()


# Custom JSONResponse that ensures UTF-8 encoding
class UTF8JSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(content, ensure_ascii=False, allow_nan=False).encode("utf-8")


# ============================================================
# LLM Configuration
# ============================================================

LLM_API_KEY = (os.environ.get("DEEPSEEK_API_KEY", "")
               or os.environ.get("MINIMAX_API_KEY", "")
               or os.environ.get("OPENCLAW_API_KEY", "")
               or os.environ.get("LLM_API_KEY", ""))
if not LLM_API_KEY:
    try:
        openclaw_config = Path.home() / ".openclaw" / "openclaw.json"
        if openclaw_config.exists():
            with open(openclaw_config, "r", encoding="utf-8") as f:
                config = json.load(f)
            providers = config.get("models", {}).get("providers", {})
            for name in ["deepseek", "minimax-cn", "minimax"]:
                if name in providers:
                    LLM_API_KEY = providers[name].get("apiKey", "")
                    break
    except Exception:
        pass

print(f"LLM API Key: {'Set' if LLM_API_KEY else 'Not set'}")
print(f"New LangGraph MultiAgent: {'Available' if NEW_MULTI_AGENT_AVAILABLE else 'Not available'}")

# ============================================================
# FastAPI App
# ============================================================

app = FastAPI(
    title="Campus Verbal Bullying & Teacher Intervention System",
    description="Teacher Professional Development Training Platform",
    version="3.0.0"
)

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/css", StaticFiles(directory=str(BASE_DIR / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(BASE_DIR / "js")), name="js")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ============================================================
# Auth Middleware — 验证 Bearer Token
# ============================================================

# Auth 白名单：无需登录即可访问
AUTH_WHITELIST = {
    "/", "/api/auth/register", "/api/auth/login", "/admin",
    "/favicon.ico", "/static/", "/css/", "/js/",
    "/api/interventions", "/api/interventions/recommend",
    "/api/scenarios", "/api/participants",
}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """JWT Bearer Token 认证中间件"""
    path = request.url.path

    # 白名单放行
    for wl in AUTH_WHITELIST:
        if path == wl or (wl != "/" and path.startswith(wl)):
            return await call_next(request)

    # 非 /api/ 路径放行（静态资源等）
    if not path.startswith("/api/"):
        return await call_next(request)

    # 检查 Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return UTF8JSONResponse(status_code=401, content={"detail": "未登录，请先登录"})

    token = auth_header[7:]
    user = verify_token(token)
    if not user:
        return UTF8JSONResponse(status_code=401, content={"detail": "登录已过期，请重新登录"})

    # 将用户信息存入 request.scope（跨中间件共享）
    request.scope["user"] = user
    return await call_next(request)


# ============================================================
# Ngrok bypass middleware — handles free tier interstitial warning
# ============================================================
@app.middleware("http")
async def ngrok_bypass(request: Request, call_next):
    response = await call_next(request)
    # Signal ngrok edge to skip browser warning on subsequent requests
    response.headers["ngrok-skip-browser-warning"] = "true"
    # Also set cookie for ngrok cookie-based bypass (v3.20+)
    response.set_cookie("ngrok-skip-browser-warning", "true", samesite="lax")
    return response


# ============================================================
# Static assets no-cache middleware
# ============================================================
# 背景：app.js / style.css 迭代频繁，而 URL 上的 ?v= 版本号可能长期未变。
# 浏览器据此缓存旧文件，导致「代码已修复但界面仍显示旧数值」的错觉
# （如百分制改造后仍显示 3580%）。此处对 /js/ /css/ 强制不缓存，
# 保证刷新即拿到最新版本；?v= 参数仍保留用于主动破缓存。
@app.middleware("http")
async def static_no_cache(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/js/") or path.startswith("/css/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ============================================================
# Preset Scenarios (8 scenarios from original app.py)
# ============================================================

_scenarios = [
    {
        "id": "scenario_1",
        "title": "美术课上的嘲笑",
        "description": "【背景】美术课上，老师让同学依次展示自己的画作，被霸凌者认真画了一幅画，轮到展示。\n【霸凌者】你觉得她的画很丑，想当众取笑她，并带动其他同学一起哄笑。\n【受害者】你对自己的画挺在意，被当众嘲笑后感到难堪和委屈。\n【正义旁观者】你觉得当众嘲笑别人的画很过分，想替她说话。\n【从众旁观者】你看到有人起哄，犹豫要不要跟着笑。",
        "level": 1,
        "cast": {
            "bully": {
                "name": "徐凯",
                "personality": {
                    "traits": [
                        "嘴毒",
                        "爱损人",
                        "见不得别人出风头",
                        "靠贬低别人显得自己厉害"
                    ],
                    "speakingStyle": [
                        "毒舌",
                        "起哄",
                        "阴阳怪气",
                        "拿别人的成果开玩笑"
                    ],
                    "background": "画画很差，美术课最坐不住，最烦别人认真画还画得好看。家里父母开小卖部，没人管他，他靠一张损嘴在班里混。其实他挺羡慕会画画的人，但嘴上绝不会承认。",
                    "cognitiveStyle": "他见不得别人有他做不到的本事。谁画得好，他就想当众把那张画踩下去，好显得自己没那么差。他以为起哄几句大家就会跟着笑，那自己就不是最丢人的那个。"
                }
            },
            "victim": {
                "name": "林小满",
                "personality": {
                    "traits": [
                        "安静",
                        "敏感",
                        "画画是唯一寄托",
                        "被嘲笑会特别难过"
                    ],
                    "speakingStyle": [
                        "小声",
                        "结巴",
                        "委屈",
                        "眼眶泛红"
                    ],
                    "background": "爸妈在城里打工，她跟着奶奶住。她不爱说话，把心里话都画进画里。美术课是她一周里最期待的时候。",
                    "cognitiveStyle": "她把自己的价值都压在那张画上，画被当众嘲笑，等于最珍视的东西被踩碎。她会先愣住，然后红了眼眶，想解释又不敢大声。"
                }
            },
            "bystander_a": {
                "name": "苏晴",
                "personality": {
                    "traits": [
                        "温和",
                        "心细",
                        "看不得人受欺负"
                    ],
                    "speakingStyle": [
                        "轻声",
                        "讲道理",
                        "护着人"
                    ],
                    "background": "坐在受害者附近，平时就注意到她爱画画。自己性格偏软，但看到明显过分的事会忍不住开口。",
                    "cognitiveStyle": "她不会上来就大声吼，而是先替受害者把话说清楚——人家画得挺好的，你凭什么这么说。"
                }
            },
            "bystander_b": {
                "name": "刘洋",
                "personality": {
                    "traits": [
                        "爱凑热闹",
                        "跟着乐",
                        "没啥主见"
                    ],
                    "speakingStyle": [
                        "跟着笑",
                        "起哄",
                        "小声嘀咕"
                    ],
                    "background": "坐教室后排，喜欢看热闹。谁被起哄他都会瞟一眼，看大家笑了他就跟着笑。",
                    "cognitiveStyle": "他分不清玩笑和伤人的界限，觉得大家笑就是好玩。只有当受害者真哭出来，他才有点慌。"
                }
            }
        },
    },
    {
        "id": "scenario_2",
        "title": "课堂上的羞辱",
        "description": "【背景】语文课上，被霸凌者被点名回答问题，因为紧张答错了，老师暂时没有制止。\n【霸凌者】你想借机当众贬低她，嘲笑她笨、这么简单的题都不会，声音很大。\n【受害者】你答错后本来就很尴尬，被当众嘲笑后更加难堪，低下头不敢说话。\n【正义旁观者】你觉得霸凌者太过分，想出声制止。\n【从众旁观者】你看到有人带头嘲笑，想跟着起哄。",
        "level": 2,
        "cast": {
            "bully": {
                "name": "郑昊",
                "personality": {
                    "traits": [
                        "欺软怕硬",
                        "爱出风头",
                        "两面三刀",
                        "记仇"
                    ],
                    "speakingStyle": [
                        "高声",
                        "嘲笑",
                        "起哄",
                        "阴阳怪气"
                    ],
                    "background": "成绩中上，是老师眼里的积极分子，回答问题很积极。但转头就对成绩差、反应慢的同学冷嘲热讽。他特别享受当众把人问倒、看人出丑的感觉。",
                    "cognitiveStyle": "他靠显得比别人强来维持自己的面子。谁当众答错、出丑，他第一个起哄，好把注意力从自己身上引开——他其实也怕自己哪天答错被人笑。"
                }
            },
            "victim": {
                "name": "王小雨",
                "personality": {
                    "traits": [
                        "老实",
                        "紧张就出错",
                        "怕丢人",
                        "越怕越慌"
                    ],
                    "speakingStyle": [
                        "结巴",
                        "小声",
                        "声音发抖",
                        "越解释越乱"
                    ],
                    "background": "成绩中等偏下，父母对她的期望很高，每次考试都怕考砸。上课最怕被点名，一站起来就紧张得忘了答案。",
                    "cognitiveStyle": "她越怕答错越容易答错，答错后众目睽睽之下恨不得钻地缝。被嘲笑时她会先慌乱地解释，然后声音越来越小，最后低下头不吭声。"
                }
            },
            "bystander_a": {
                "name": "陈曦",
                "personality": {
                    "traits": [
                        "成绩好",
                        "有主见",
                        "敢说",
                        "不惯着人"
                    ],
                    "speakingStyle": [
                        "干脆",
                        "直接",
                        "有理有据"
                    ],
                    "background": "语文课代表，性格直，看不惯欺软怕硬的人。成绩好，在班里有话语权。",
                    "cognitiveStyle": "她不会和稀泥，谁明显过分她当场就点出来——答错了怎么了，用得着这么笑吗。"
                }
            },
            "bystander_b": {
                "name": "马超",
                "personality": {
                    "traits": [
                        "爱起哄",
                        "怕被孤立",
                        "跟着强势的人走"
                    ],
                    "speakingStyle": [
                        "附和",
                        "起哄",
                        "嗓门大"
                    ],
                    "background": "坐教室后面，平时和郑昊玩得近，郑昊起哄他就跟着。",
                    "cognitiveStyle": "他跟着起哄不是因为讨厌受害者，而是怕自己不跟着笑，就会被划到被笑的那一边。"
                }
            }
        },
    },
    {
        "id": "scenario_3",
        "title": "值日冲突",
        "description": "【背景】放学后值日时间，被霸凌者所在小组负责打扫教室。\n【霸凌者】你前一天因为自己座位卫生问题被老师批评，正迁怒于当天值日的被霸凌者，借口“昨天没扫干净”，要求她把桌子下面、椅子下面擦干净，故意挑刺使唤她。\n【受害者】你当天其实不是自己值日，感到委屈，但性格内向、怕冲突，犹豫后还是弯腰照做，之后沉默。\n【正义旁观者】你看不下去，想帮被霸凌者澄清事实、指出霸凌者无理，但方式克制。\n【从众旁观者】你不敢得罪霸凌者，跟着起哄、说风凉话。",
        "level": 2,
        "cast": {
            "bully": {
                "name": "王强",
                "personality": {
                    "traits": [
                        "爱使唤人",
                        "挑刺",
                        "甩锅",
                        "欺软怕硬"
                    ],
                    "speakingStyle": [
                        "命令式",
                        "颐指气使",
                        "挑毛病",
                        "嗓门大"
                    ],
                    "background": "在班里有一帮跟班，靠使唤人确立老大地位。其实他怕老师，更怕比他横的。他前一天因为自己座位脏被老师当众批评，一肚子火没处发。",
                    "cognitiveStyle": "他受了气不敢冲老师发，就找最软的人撒气。值日生好欺负，他就把火撒在值日生头上，挑刺使唤，好显得不是我的问题，是你们没扫干净。"
                }
            },
            "victim": {
                "name": "李念",
                "personality": {
                    "traits": [
                        "老实",
                        "怕事",
                        "逆来顺受",
                        "不敢拒绝"
                    ],
                    "speakingStyle": [
                        "小声",
                        "怯怯的",
                        "道歉",
                        "委曲求全"
                    ],
                    "background": "从小被教多一事不如少一事，从不敢跟人起冲突。今天其实不是她值日，但她不敢说，怕得罪人。",
                    "cognitiveStyle": "她明知自己冤枉，第一反应还是先照做、先道歉，想着忍一忍就过去了。委屈在心里，却不敢声张。"
                }
            },
            "bystander_a": {
                "name": "林晓",
                "personality": {
                    "traits": [
                        "正直",
                        "仗义",
                        "性子有点急"
                    ],
                    "speakingStyle": [
                        "直接",
                        "仗义",
                        "说话冲"
                    ],
                    "background": "平时就看不惯王强欺负人，只是没找到机会。今天这事她实在看不下去了。",
                    "cognitiveStyle": "她不信忍一忍就过去，谁冤枉好人她就要说清楚——今天不是她值日，你凭什么使唤人。"
                }
            },
            "bystander_b": {
                "name": "赵鹏",
                "personality": {
                    "traits": [
                        "怕事",
                        "看风向",
                        "爱说风凉话"
                    ],
                    "speakingStyle": [
                        "小声嘀咕",
                        "风凉话",
                        "附和"
                    ],
                    "background": "和王强有点交情，不敢当面得罪他。心里知道王强没道理，但嘴上不吭声。",
                    "cognitiveStyle": "他不敢惹王强，只能在旁边嘀咕几句风凉话，或者帮王强证明确实没扫干净，好让自己不被波及。"
                }
            }
        },
    },
    {
        "id": "scenario_4",
        "title": "班级群里的\"玩笑\"",
        "description": "【背景】霸凌者在班级微信群里发了一段被霸凌者上课回答问题时结巴的视频，配了取笑他的文字。\n【霸凌者】你想让大家一起笑话他，觉得只是开个玩笑、不当回事，没理会他私聊要求删视频。\n【受害者】你看到视频后很难堪，私聊要求删掉但没被理会，心里很受伤，第二天开始逃避集体活动，表面看不出明显情绪。\n【正义旁观者】你细心察觉被霸凌者不对劲，主动关心后才知道是视频的事，想帮他要求删视频、道歉。\n【从众旁观者】你跟着起哄调侃，没意识到对方真的很难受。",
        "level": 3,
        "cast": {
            "bully": {
                "name": "孙浩",
                "personality": {
                    "traits": [
                        "爱恶搞",
                        "没分寸",
                        "把欺负当玩笑",
                        "不觉得自己错"
                    ],
                    "speakingStyle": [
                        "嬉皮笑脸",
                        "调侃",
                        "起哄",
                        "玩梗"
                    ],
                    "background": "班里的气氛担当，爱拍别人丑照、出糗视频发群里。他觉得这是玩，被拍的人越生气他越来劲。",
                    "cognitiveStyle": "他真心不觉得自己在欺负人，觉得大家不都这么玩吗。他拍了受害者结巴的视频发群里配文字，还觉得挺幽默。谁让他删，他反而觉得对方开不起玩笑。"
                }
            },
            "victim": {
                "name": "周涛",
                "personality": {
                    "traits": [
                        "爱面子",
                        "敏感",
                        "表面逞强",
                        "私下难受"
                    ],
                    "speakingStyle": [
                        "逞强",
                        "假装没事",
                        "私下语气软"
                    ],
                    "background": "平时挺在意自己在班里的形象。上课回答问题结巴的视频被发到群里，他觉得脸都丢尽了。",
                    "cognitiveStyle": "他表面装作不在乎，说删了就删了无所谓，其实私聊求删没人理，心里很受伤。第二天开始找借口躲开集体活动。"
                }
            },
            "bystander_a": {
                "name": "何雨薇",
                "personality": {
                    "traits": [
                        "心细",
                        "善良",
                        "有同理心"
                    ],
                    "speakingStyle": [
                        "轻声",
                        "关心",
                        "主动"
                    ],
                    "background": "平时就注意细节，发现受害者这两天不对劲，主动去问才知道是视频的事。",
                    "cognitiveStyle": "她不觉得这是玩笑，能看出受害者真的很难受。她会私下先关心受害者，再想办法让他把视频删了、道个歉。"
                }
            },
            "bystander_b": {
                "name": "钱进",
                "personality": {
                    "traits": [
                        "爱起哄",
                        "跟风",
                        "图热闹"
                    ],
                    "speakingStyle": [
                        "调侃",
                        "跟风",
                        "发表情包"
                    ],
                    "background": "群里跟风最积极，孙浩发什么他都跟着调侃、发表情包。",
                    "cognitiveStyle": "他跟着起哄只是图个乐，没想过受害者会真难受。等真出了事，他第一反应是跟我没关系。"
                }
            }
        },
    },
    {
        "id": "scenario_5",
        "title": "课间的\"游戏\"",
        "description": "【背景】课间，霸凌者从背后突然抱住被霸凌者，把他举起来又放下，开玩笑说他很轻。\n【霸凌者】你以戏弄他为乐，觉得只是男生间正常打闹。\n【受害者】你被吓到，脸涨得通红，一开始没敢出声拒绝，第二次才挣扎着让他放下，之后课间总想躲着他。\n【正义旁观者】你看出被霸凌者其实不愿意，想走过去制止。\n【从众旁观者】你在旁边起哄、看热闹。",
        "level": 3,
        "cast": {
            "bully": {
                "name": "高大壮",
                "personality": {
                    "traits": [
                        "莽撞",
                        "爱动手动脚",
                        "没轻没重",
                        "把欺负当玩笑"
                    ],
                    "speakingStyle": [
                        "大大咧咧",
                        "戏弄",
                        "起哄",
                        "嗓门大"
                    ],
                    "background": "人高马大，体育好，喜欢用闹着玩的名义对同学动手动脚，把人举起来、拍脑袋、绊一下。他觉得自己是在玩，别人不乐意就是玩不起。",
                    "cognitiveStyle": "他把力量当成好玩和亲近的方式，完全意识不到别人会害怕。受害者越挣扎他越觉得好玩，还怪对方开不起玩笑。"
                }
            },
            "victim": {
                "name": "丁小宇",
                "personality": {
                    "traits": [
                        "瘦弱",
                        "胆小",
                        "内向",
                        "怕肢体冲突"
                    ],
                    "speakingStyle": [
                        "结巴",
                        "小声",
                        "求饶",
                        "喘不过气"
                    ],
                    "background": "个子瘦小，体育不好，最怕被人突然抱住举起来。被戏弄后他脸涨得通红，一开始不敢出声，第二次才挣扎着让人放下。",
                    "cognitiveStyle": "他对突如其来的肢体接触极度恐惧，会心跳加速、浑身僵硬。之后课间总想躲着高大壮，生怕再被闹着玩。"
                }
            },
            "bystander_a": {
                "name": "白露",
                "personality": {
                    "traits": [
                        "有正义感",
                        "观察细",
                        "敢出头"
                    ],
                    "speakingStyle": [
                        "干脆",
                        "制止",
                        "讲道理"
                    ],
                    "background": "她看出受害者不是玩，而是真的害怕和不情愿。她不会袖手旁观。",
                    "cognitiveStyle": "她能分辨玩和欺负的区别，谁打着玩笑的旗号欺负人，她就要上前制止——没看见他都不愿意吗。"
                }
            },
            "bystander_b": {
                "name": "罗飞",
                "personality": {
                    "traits": [
                        "爱看热闹",
                        "起哄",
                        "事不关己"
                    ],
                    "speakingStyle": [
                        "起哄",
                        "看热闹",
                        "煽风点火"
                    ],
                    "background": "和高大壮玩得好，觉得这种游戏很好玩，在旁边起哄、看热闹。",
                    "cognitiveStyle": "他只觉得好玩，看不出受害者的恐惧。直到有人点破，他才反应过来好像有点过了。"
                }
            }
        },
    },
    {
        "id": "scenario_6",
        "title": "被孤立的转学生",
        "description": "【背景】被霸凌者刚转学来，霸凌者带头疏远她，班里几乎没人主动和她说话。\n【霸凌者】你想当面议论她以前的学校差、成绩不好，阻止别人和她来往，看到有人关心她就去阻止。\n【受害者】你课间想加入大家聊天却被排挤，内心压抑、越来越害怕上学，但表面维持平静。\n【正义旁观者】你注意到她总是一个人，想主动搭话关心。\n【从众旁观者】你借口座位有人，把她支到角落单独坐。",
        "level": 4,
        "cast": {
            "bully": {
                "name": "韩雪",
                "personality": {
                    "traits": [
                        "拉帮结派",
                        "冷暴力",
                        "控制欲强",
                        "见不得新人受关注"
                    ],
                    "speakingStyle": [
                        "阴阳怪气",
                        "冷嘲",
                        "指使",
                        "嚼舌根"
                    ],
                    "background": "班里的小圈子核心，靠谁和我玩、谁别和我玩来控制人。她不喜欢突然冒出来的转学生，尤其见不得有人主动对转学生好。",
                    "cognitiveStyle": "她用冷暴力和排挤来确立自己的地位。转学生来了，她要让大家知道别和她走太近，谁关心转学生，她就去敲打谁。"
                }
            },
            "victim": {
                "name": "乔安",
                "personality": {
                    "traits": [
                        "敏感",
                        "孤单",
                        "渴望被接纳",
                        "越来越自卑"
                    ],
                    "speakingStyle": [
                        "小声",
                        "迟疑",
                        "不敢主动",
                        "强装平静"
                    ],
                    "background": "刚转学过来，人生地不熟，很想融入却总被排挤。课间想加入聊天总被支开，心里越来越压抑。",
                    "cognitiveStyle": "她一次次鼓起勇气想融入，又一次次被拒绝，开始怀疑是不是自己哪里不好。表面维持平静，心里却越来越害怕上学。"
                }
            },
            "bystander_a": {
                "name": "温言",
                "personality": {
                    "traits": [
                        "温柔",
                        "善良",
                        "有同理心"
                    ],
                    "speakingStyle": [
                        "温柔",
                        "主动",
                        "轻声"
                    ],
                    "background": "自己也经历过换学校的不适应，特别能理解转学生。看到乔安总是一个人，想主动搭话。",
                    "cognitiveStyle": "她不想站队、不搞小圈子，觉得谁都不该被孤立。她会主动找乔安说话、邀请她一起。"
                }
            },
            "bystander_b": {
                "name": "吴迪",
                "personality": {
                    "traits": [
                        "随大流",
                        "怕惹韩雪",
                        "跟着排挤"
                    ],
                    "speakingStyle": [
                        "敷衍",
                        "支开",
                        "小声"
                    ],
                    "background": "不敢得罪韩雪，看到韩雪排挤转学生，他也跟着找借口把人支开。",
                    "cognitiveStyle": "他不是讨厌乔安，只是怕和韩雪对着干。他一边心里过意不去，一边还是照做。"
                }
            }
        }, 
    },
    {
        "id": "scenario_7",
        "title": "宿舍里的\"夜谈\"",
        "description": "【背景】被霸凌者连续数周情绪低落、身上偶有淤青，但坚称是自己摔的。今天早自习她没来教室，蜷缩在宿舍被窝里说肚子疼。\n【霸凌者】你是宿舍里带头排挤、动手欺负她的人，想继续施压，让她不敢说出去。\n【受害者】你被追问时欲言又止，不想惹麻烦、不敢说。\n【正义旁观者】你在隔壁宿舍，偶然发现她在厕所偷偷哭、手臂上有掐痕，怀疑她害怕去上课，昨晚还听到她宿舍有异常响动和哭声。\n【从众旁观者】你目睹了一些事，但不敢声张，怕引火烧身。",
        "level": 5,
        "cast": {
            "bully": {
                "name": "周倩",
                "personality": {
                    "traits": [
                        "霸道",
                        "动手",
                        "控制欲强",
                        "威胁"
                    ],
                    "speakingStyle": [
                        "威胁",
                        "恐吓",
                        "阴阳怪气",
                        "低声施压"
                    ],
                    "background": "宿舍里的大姐，带头排挤、动手欺负受害者，还威胁她不许说出去。她享受掌控别人的感觉。",
                    "cognitiveStyle": "她用暴力和威胁维持自己的权威，受害者越软弱她越变本加厉。她最怕事情败露，所以一边施压一边威胁敢说出去试试。"
                }
            },
            "victim": {
                "name": "许念",
                "personality": {
                    "traits": [
                        "长期压抑",
                        "恐惧",
                        "不敢说",
                        "强忍"
                    ],
                    "speakingStyle": [
                        "欲言又止",
                        "声音发抖",
                        "说没事",
                        "躲闪"
                    ],
                    "background": "连续数周被欺负，情绪低落，身上偶有淤青却谎称是自己摔的。今天早自习躲进被窝说肚子疼，其实是怕去面对。",
                    "cognitiveStyle": "她长期处于恐惧中，不敢说出真相，怕报复。被追问时欲言又止，只想躲起来。"
                }
            },
            "bystander_a": {
                "name": "宋瑶",
                "personality": {
                    "traits": [
                        "细心",
                        "敏感",
                        "有正义感"
                    ],
                    "speakingStyle": [
                        "轻声",
                        "试探",
                        "关心"
                    ],
                    "background": "住隔壁宿舍，偶然发现许念在厕所偷偷哭、手臂有掐痕，还听到她宿舍有异常响动和哭声。",
                    "cognitiveStyle": "她察觉到不对劲，怀疑许念被欺负。她想帮，但怕自己贸然插手反而害了许念，于是先小心试探、关心。"
                }
            },
            "bystander_b": {
                "name": "冯晨",
                "personality": {
                    "traits": [
                        "胆小",
                        "怕事",
                        "不敢声张"
                    ],
                    "speakingStyle": [
                        "含糊",
                        "躲闪",
                        "小声"
                    ],
                    "background": "目睹了一些事，但怕引火烧身，一直不敢说。",
                    "cognitiveStyle": "她知道真相却不敢开口，怕自己也成为被欺负的对象。"
                }
            }
        },
    },
    {
        "id": "scenario_8",
        "title": "放学路上的威胁",
        "description": "【背景】被霸凌者近期经常迟到、成绩下滑。一次匿名问卷中他勾选了“遭受过威胁”，但文字描述只写“没什么”。有学生私下告诉老师，曾在放学后看到他被几个同学围住，像是在索要财物。\n【霸凌者】你常在校门口围住被霸凌者索要财物，想继续威胁他别声张。\n【受害者】你被人勒索时第一反应是不给、拒绝，甚至动手打回去，但对方比你强，最后你还是被殴打、被抢了钱。你好面子，不肯向老师求助，认定这是“江湖上的事”老师不该插手。被老师约谈时你眼神躲闪，只说作业多、压力大，不敢说实情。\n【正义旁观者】你没当面冲突，因为你看到被霸凌者被勒索后会把气撒在更弱小的同学身上，怕正面冲突会伤到自己，所以选择私下向老师报告。\n【从众旁观者】你看到过这些事，但装作没看见。",
        "level": 5,
        "cast": {
            "bully": {
                "name": "龙飞",
                "personality": {
                    "traits": [
                        "混混",
                        "凶",
                        "勒索",
                        "恐吓"
                    ],
                    "speakingStyle": [
                        "威胁",
                        "恐吓",
                        "命令",
                        "压低声音"
                    ],
                    "background": "校门口一帮人的头，常围住受害者索要财物。他靠威胁让人不敢声张。",
                    "cognitiveStyle": "他靠暴力和恐吓勒索同学，最怕被老师和家长发现。所以每次都会威胁敢说出去有你好看。"
                }
            },
            "victim": {
                "name": "陈默",
                "personality": {
                    "traits": [
                        "强硬",
                        "好面子",
                        "不服软",
                        "外强中干"
                    ],
                    "speakingStyle": [
                        "嘴硬",
                        "拒绝",
                        "顶撞",
                        "不服软"
                    ],
                    "background": "近期经常迟到、成绩下滑，被勒索却不敢说。被老师约谈时眼神躲闪，只说是作业多压力大。受了气没处发，会拿更弱小的同学出气。",
                    "cognitiveStyle": "他被人勒索时第一反应是不给、拒绝，甚至打回去，但对方比他强，最后还是被殴打、被抢钱。他觉得被欺负很丢人，好面子不肯向老师求助，认定这是“江湖上的事”老师不该插手。其实内心被恐吓吓住了，不敢说实情、怕报复，只能一个人扛，越来越沉默、叛逆，转而去欺负更弱小的同学泄愤。"
                }
            },
            "bystander_a": {
                "name": "唐宁",
                "personality": {
                    "traits": [
                        "有正义感",
                        "谨慎",
                        "有策略"
                    ],
                    "speakingStyle": [
                        "小声",
                        "谨慎",
                        "如实"
                    ],
                    "background": "私下告诉老师，曾看到受害者在校门口被几个同学围住，像是在索要财物。",
                    "cognitiveStyle": "她本可以当面劝阻，但看到受害者被勒索后会把气撒在更弱小的同学身上，甚至可能冲自己来，于是不正面冲突，选择私下向老师报告——既安全又能真正帮到他。"
                }
            },
            "bystander_b": {
                "name": "郝强",
                "personality": {
                    "traits": [
                        "怕事",
                        "装没看见"
                    ],
                    "speakingStyle": [
                        "沉默",
                        "回避",
                        "装不知道"
                    ],
                    "background": "看到过这些事，但装作没看见，怕惹祸上身。",
                    "cognitiveStyle": "他不想惹麻烦，选择视而不见，心里却不安。"
                }
            }
        },
    },
]

# ============================================================
# Session Management
# ============================================================

sessions: Dict[str, SessionManager] = {}
# 线程池：将同步 LLM 调用从事件循环切出，避免阻塞其他用户
_run_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sim_run")


def create_session(level: int = 2, scenario_id: str = None, use_llm: bool = False, custom_participants: dict = None) -> SessionManager:
    """Create new session"""
    session_id = str(uuid.uuid4())[:8]

    scenario = None
    if scenario_id:
        for s in _scenarios:
            if s["id"] == scenario_id:
                scenario = s
                break

    session = SessionManager(
        session_id=session_id,
        level=level,
        scenario=scenario,
        use_llm=use_llm,
        custom_participants=custom_participants,
    )
    sessions[session_id] = session
    return session


def get_session(session_id: str) -> Optional[SessionManager]:
    return sessions.get(session_id)


# ============================================================
# API Routes - Auth (登录/注册)
# ============================================================

class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""
    email: str = ""

class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/register")
async def auth_register(data: RegisterRequest):
    """用户注册"""
    if len(data.username) < 2 or len(data.username) > 32:
        raise HTTPException(status_code=400, detail="用户名需 2-32 个字符")
    if len(data.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 个字符")
    try:
        user = create_user(data.username, data.password, data.display_name, data.email)
        token = create_token(user["id"])
        return UTF8JSONResponse(content={"token": token, "user": user})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/auth/login")
async def auth_login(data: LoginRequest):
    """用户登录"""
    user = authenticate(data.username, data.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token(user["id"])
    return UTF8JSONResponse(content={
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "email": user["email"],
            "is_admin": bool(user["is_admin"])
        }
    })


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    """用户登出"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        delete_token(auth_header[7:])
    return UTF8JSONResponse(content={"ok": True})


@app.get("/api/auth/me")
async def auth_me(request: Request):
    """获取当前用户信息"""
    return UTF8JSONResponse(content={"user": request.scope["user"]})


@app.get("/api/sessions")
async def get_user_sessions_list(request: Request):
    """获取当前用户的所有会话历史"""
    user = request.scope["user"]
    sessions_data = get_user_sessions(user["id"])
    result = []
    for s in sessions_data:
        item = dict(s)
        # 如果有报告，附加摘要
        report = get_report_by_session(s["id"])
        if report:
            item["has_report"] = True
            item["report_summary"] = report.get("report_data", {}).get("summary", {}).get("title", "")
        else:
            item["has_report"] = False
        result.append(item)
    return UTF8JSONResponse(content={"sessions": result})


# ============================================================
# API Routes - Scenarios
# ============================================================

@app.get("/api/scenarios")
async def get_scenarios(request: Request):
    """返回系统预设 + 管理员全局自定义 + 当前用户自定义"""
    scenarios = list(_scenarios) + get_shared_scenarios()
    user = get_optional_user(request)
    if user:
        scenarios.extend(get_user_scenarios(user["id"]))
    return UTF8JSONResponse(content={"scenarios": scenarios, "count": len(scenarios)})


@app.post("/api/scenarios")
async def create_scenario(request: Request):
    user = check_auth_or_401(request)
    body = await request.json()
    title = body.get("title", "").strip()
    description = body.get("description", "").strip()
    if not title or not description:
        raise HTTPException(status_code=400, detail="Title and description required")

    new_id = f"custom_{uuid.uuid4().hex[:8]}"
    try:
        if user.get("is_admin"):
            scenario = create_shared_scenario(
                scenario_id=new_id, title=title, description=description,
                level=body.get("level", 2), created_by=user["username"],
            )
        else:
            scenario = create_user_scenario(
                user_id=user["id"], scenario_id=new_id,
                title=title, description=description, level=body.get("level", 2),
            )
        return UTF8JSONResponse(content={"success": True, "scenario": scenario})
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.put("/api/scenarios/{scenario_id}")
async def update_scenario(scenario_id: str, request: Request):
    user = check_auth_or_401(request)
    if not scenario_id.startswith("custom_"):
        raise HTTPException(status_code=403, detail="Preset scenarios cannot be modified")

    body = await request.json()
    if user.get("is_admin"):
        ok = update_shared_scenario(scenario_id, body.get("title"), body.get("description"), body.get("level"))
        if not ok:
            ok = update_user_scenario(user["id"], scenario_id, body.get("title"), body.get("description"), body.get("level"))
    else:
        ok = update_user_scenario(user["id"], scenario_id, body.get("title"), body.get("description"), body.get("level"))
    if not ok:
        raise HTTPException(status_code=404, detail="Scenario not found or not yours")
    return UTF8JSONResponse(content={"success": True})


@app.delete("/api/scenarios/{scenario_id}")
async def delete_scenario(scenario_id: str, request: Request):
    user = check_auth_or_401(request)
    if not scenario_id.startswith("custom_"):
        raise HTTPException(status_code=403, detail="Preset scenarios cannot be deleted")

    if user.get("is_admin"):
        ok = delete_shared_scenario(scenario_id) or delete_user_scenario(user["id"], scenario_id)
    else:
        ok = delete_user_scenario(user["id"], scenario_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Scenario not found or not yours")
    return UTF8JSONResponse(content={"success": True})


# ============================================================
# API Routes - Participants
# ============================================================

def _get_system_participants() -> list:
    """获取系统硬编码的参与角色"""
    try:
        from multi_agent.agents import AGENT_ROLE_MAP
        participants = []
        for agent_id, cfg in AGENT_ROLE_MAP.items():
            participants.append({
                "id": agent_id,
                "name": cfg.get("name", agent_id),
                "role": agent_id,
                "roleName": cfg.get("role_name", agent_id),
            })
        return participants
    except ImportError:
        return [
            {"id": "bully", "name": "陈浩", "role": "bully", "roleName": "霸凌者"},
            {"id": "victim", "name": "思远", "role": "victim", "roleName": "受害者"},
            {"id": "bystander_a", "name": "林雨桐", "role": "bystander_a", "roleName": "正义旁观者"},
            {"id": "bystander_b", "name": "赵磊", "role": "bystander_b", "roleName": "从众旁观者"},
        ]


@app.get("/api/participants")
async def get_participants(request: Request):
    """返回系统角色 + 管理员全局自定义 + 当前用户自定义"""
    participants = _get_system_participants() + get_shared_participants()
    user = get_optional_user(request)
    if user:
        participants.extend(get_user_participants(user["id"]))
    return UTF8JSONResponse(content={"success": True, "participants": participants})


@app.post("/api/participants")
async def create_participant(request: Request):
    user = check_auth_or_401(request)
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")

    new_id = f"custom_{uuid.uuid4().hex[:8]}"
    participant_role = body.get("role", "")
    try:
        if user.get("is_admin"):
            p = create_shared_participant(
                pid=new_id, name=name,
                role=participant_role,
                role_name=body.get("roleName", ""),
                created_by=user["username"],
                personality=body.get("personality", {}),
            )
        else:
            p = create_user_participant(
                user_id=user["id"], pid=new_id,
                name=name, role=participant_role,
                role_name=body.get("roleName", ""),
                personality=body.get("personality", {}),
            )
        return UTF8JSONResponse(content={"success": True, "participant": p})
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.put("/api/participants/{participant_id}")
async def update_participant(participant_id: str, request: Request):
    user = check_auth_or_401(request)
    if not participant_id.startswith("custom_"):
        raise HTTPException(status_code=403, detail="System participants cannot be modified")

    body = await request.json()
    role = body.get("role", None)
    pers = body.get("personality")
    if user.get("is_admin"):
        ok = update_shared_participant(participant_id, body.get("name"), body.get("roleName"), role, pers)
        if not ok:
            ok = update_user_participant(user["id"], participant_id, body.get("name"), body.get("roleName"), role, pers)
    else:
        ok = update_user_participant(user["id"], participant_id, body.get("name"), body.get("roleName"), role, pers)
    if not ok:
        raise HTTPException(status_code=404, detail="Participant not found or not yours")
    # Return the updated participant so frontend can update state
    p = get_user_participant(participant_id) or get_shared_participant(participant_id)
    return UTF8JSONResponse(content={"success": True, "participant": p})


@app.post("/api/participants/{participant_id}/reset")
async def reset_participant(participant_id: str, request: Request):
    """Reset a system participant to defaults (reload from system config)"""
    user = check_auth_or_401(request)
    sys_participants = _get_system_participants()
    participant = next((p for p in sys_participants if p["id"] == participant_id), None)
    if not participant:
        # Check shared/custom
        participant = get_shared_participant(participant_id) or get_user_participant(participant_id)
        if not participant:
            raise HTTPException(status_code=404, detail="Participant not found")
    return UTF8JSONResponse(content={"success": True, "participant": participant})


@app.delete("/api/participants/{participant_id}")
async def delete_participant(participant_id: str, request: Request):
    user = check_auth_or_401(request)
    if not participant_id.startswith("custom_"):
        raise HTTPException(status_code=403, detail="System participants cannot be deleted")

    if user.get("is_admin"):
        ok = delete_shared_participant(participant_id) or delete_user_participant(user["id"], participant_id)
    else:
        ok = delete_user_participant(user["id"], participant_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Participant not found or not yours")
    return UTF8JSONResponse(content={"success": True})


# ============================================================
# API Routes - Intervention Library
# ============================================================

INTERVENTION_LIB_PATH = Path(__file__).parent.parent / "src" / "intervention_library.json"


def load_interventions() -> dict:
    if INTERVENTION_LIB_PATH.exists():
        with open(INTERVENTION_LIB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 规范化 tags 字段（修复历史遗留问题：tags 被错误存储为字符串）
        for item in data.get("interventions", []):
            if isinstance(item.get("tags"), str):
                item["tags"] = [t.strip() for t in item["tags"].split() if t.strip()]
        return data
    return {"interventions": []}


def save_interventions(data: dict):
    with open(INTERVENTION_LIB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_optional_user(request: Request) -> Optional[Dict]:
    """从 Authorization header 提取用户（不强制要求登录）"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        user = verify_token(token)
        if user:
            return user
    return None


def check_auth_or_401(request: Request) -> dict:
    """获取当前用户，未登录则抛出 401"""
    user = get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


@app.get("/api/interventions")
async def get_interventions(request: Request):
    """返回系统预设 + 管理员全局自定义 + 当前用户自定义"""
    interventions = list(load_interventions().get("interventions", []))
    interventions.extend(get_shared_interventions())
    user = get_optional_user(request)
    if user:
        interventions.extend(get_user_interventions(user["id"]))
    return UTF8JSONResponse(content={"success": True, "interventions": interventions})


@app.post("/api/interventions")
async def create_intervention(request: Request):
    user = check_auth_or_401(request)
    body = await request.json()
    title = body.get("title", "").strip()
    content = body.get("content", "").strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="Title and content required")

    new_id = f"custom_{uuid.uuid4().hex[:8]}"
    try:
        if user.get("is_admin"):
            new_interv = create_shared_intervention(
                intervention_id=new_id, title=title, content=content,
                category=body.get("category", "自定义"),
                tags=body.get("tags", ["自定义"]),
                created_by=user["username"],
            )
        else:
            new_interv = create_user_intervention(
                user_id=user["id"], intervention_id=new_id,
                title=title, content=content,
                category=body.get("category", "自定义"),
                tags=body.get("tags", ["自定义"]),
            )
        return UTF8JSONResponse(content={"success": True, "intervention": new_interv})
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.put("/api/interventions/{intervention_id}")
async def update_intervention(intervention_id: str, request: Request):
    user = check_auth_or_401(request)
    if not intervention_id.startswith("custom_"):
        raise HTTPException(status_code=403, detail="Built-in interventions cannot be modified")

    body = await request.json()
    # 管理员：先尝试修改 shared 表；普通用户：修改自己的 user 表
    if user.get("is_admin"):
        ok = update_shared_intervention(
            intervention_id, body.get("title"), body.get("content"),
            body.get("category"), body.get("tags"),
        )
        if not ok:
            ok = update_user_intervention(user["id"], intervention_id,
                body.get("title"), body.get("content"), body.get("category"), body.get("tags"))
    else:
        ok = update_user_intervention(user["id"], intervention_id,
            body.get("title"), body.get("content"), body.get("category"), body.get("tags"))
    if not ok:
        raise HTTPException(status_code=404, detail="Intervention not found or not yours")
    return UTF8JSONResponse(content={"success": True})


@app.delete("/api/interventions/{intervention_id}")
async def delete_intervention(intervention_id: str, request: Request):
    user = check_auth_or_401(request)
    if not intervention_id.startswith("custom_"):
        raise HTTPException(status_code=403, detail="Built-in interventions cannot be deleted")

    if user.get("is_admin"):
        ok = delete_shared_intervention(intervention_id) or delete_user_intervention(user["id"], intervention_id)
    else:
        ok = delete_user_intervention(user["id"], intervention_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Intervention not found or not yours")
    return UTF8JSONResponse(content={"success": True})


@app.get("/api/interventions/recommend")
async def recommend_interventions(
    request: Request,
    session_id: str = None,
    level: int = 2,
    distress: float = 0.5,
    bullying_intensity: float = 0.0,
):
    """Recommend interventions based on current state"""
    all_interventions = list(load_interventions().get("interventions", []))
    all_interventions.extend(get_shared_interventions())
    user = get_optional_user(request)
    if user:
        all_interventions.extend(get_user_interventions(user["id"]))

    # Default recommended interventions
    priority_ids = []
    warning = None

    if distress >= 0.8:
        priority_ids = ["psych_repair_3", "psych_repair_2", "victim_priority", "private_talk", "post_bullying_care"]
        warning = "受害者压力极高，需要立即干预！"
    elif distress >= 0.6:
        priority_ids = ["support", "psych_repair_1", "private_talk", "empathy_build", "bystander_guide"]
        warning = "受害者压力较高，建议尽快干预。"
    elif bullying_intensity >= 0.7:
        priority_ids = ["firm", "rule_remind", "behavior_contract", "face_talk", "support"]
        warning = "霸凌强度较高，需要坚定制止。"
    elif bullying_intensity >= 0.4:
        priority_ids = ["kind", "reflect", "bystander_guide", "empathy_build", "class_discussion"]
    else:
        priority_ids = ["kind", "bystander_guide", "class_discussion", "private_talk", "support"]

    # Filter and sort
    recommended = sorted(
        [x for x in all_interventions if x["id"] in priority_ids],
        key=lambda x: priority_ids.index(x["id"]) if x["id"] in priority_ids else 999
    )[:5]

    return UTF8JSONResponse(content={
        "success": True,
        "recommended": recommended,
        "all": all_interventions,
        "warning": warning,
        "remaining": [x for x in all_interventions if x["id"] not in priority_ids],
    })


# ============================================================
# API Routes - Session Management
# ============================================================

class CreateSessionRequest(BaseModel):
    level: int = 2
    scenario_id: Optional[str] = None
    use_llm: bool = True
    participant_ids: Optional[List[str]] = None
    participants_data: Optional[List[dict]] = None


class InterventionRequest(BaseModel):
    content: str
    # 幂等令牌（R12）：同一内容重试时复用，服务端据此避免重复产生干预
    client_token: Optional[str] = None


@app.post("/api/session")
async def create_new_session(req: Request, request: CreateSessionRequest):
    """Create new session (绑定当前用户)"""
    user = req.scope["user"]
    level = request.level
    if level < 1 or level > 5:
        raise HTTPException(status_code=400, detail="Level must be 1-5")

    # 在数据库中创建会话记录
    db_session_id = db_create_session(
        user_id=user["id"],
        level=request.level,
        scenario_id=request.scenario_id or "",
        scenario_title=""
    )

    # 合并参与角色：前端传来的 > 系统默认
    from multi_agent.agents import AGENT_ROLE_MAP
    custom_participants = {}
    if request.participants_data:
        for p in request.participants_data:
            pid = p.get("id", "")
            custom_participants[pid] = {
                "name": p.get("name", pid),
                "role": p.get("role", pid),
                "role_name": p.get("roleName", ""),
                "personality": p.get("personality", {}),
            }

    session = create_session(
        level=request.level,
        scenario_id=request.scenario_id,
        use_llm=request.use_llm,
        custom_participants=custom_participants,
    )
    # 在 session 对象上记录 DB 信息（用于结束时更新）
    session._db_user_id = user["id"]
    session._db_session_id = db_session_id

    return UTF8JSONResponse(content={
        "session_id": session.session_id,
        "db_session_id": db_session_id,
        "level": session.level,
        "scenario": session.scenario,
        "use_llm": session.use_llm,
        "llm_available": NEW_MULTI_AGENT_AVAILABLE and bool(LLM_API_KEY),
        "status": session.get_status(),
        "user_id": user["id"],
    })


@app.get("/api/session/{session_id}")
async def get_session_status(session_id: str):
    """Get session status"""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return UTF8JSONResponse(content={
        "session_id": session.session_id,
        "level": session.level,
        "status": session.get_status(),
    })


@app.post("/api/session/{session_id}/run")
async def run_simulation_round(session_id: str):
    """Run one round of dialogue (线程池异步执行，避免阻塞事件循环)"""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session._stop_requested:
        raise HTTPException(status_code=409, detail="会话正在停止中")

    # 线程池运行 LLM 调用，避免阻塞其他用户
    loop = asyncio.get_running_loop()
    try:
        status = await loop.run_in_executor(_run_executor, session.run_round)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"执行失败: {str(e)}")

    # 检查是否被拒绝（会话正忙）
    if isinstance(status, dict) and status.get("error") == "session_busy":
        return UTF8JSONResponse(status_code=409, content=status)

    # 如果已经结束（内部 Supervisor 已设置 termination_reason），返回完整终止信息
    if status.get("done"):
        # 自动结束落库：记录 result_type 与可读的 auto_end_reason
        session.persist_completed()
        return UTF8JSONResponse(content={
            "done": True,
            "auto_ended": True,
            "auto_end_reason": status.get("auto_end_reason", ""),
            "result_type": status.get("result_type", ""),
            "status": status.get("status", {}),
            "messages": status.get("messages", []),
            "round": status.get("round", 0),
            "metrics": status.get("metrics", {}),
            "convergence": status.get("convergence"),
            "_conv_diag": status.get("_conv_diag"),
        })

    # 未结束时，才检查主动终止条件（max_rounds 等）
    end_check = session.check_auto_end_conditions()
    if end_check["should_end"]:
        session.persist_completed()
        return UTF8JSONResponse(content={
            "done": True,
            "auto_ended": True,
            "auto_end_reason": end_check["reason"],
            "result_type": session.result_type or "",
            "status": session.get_status(),
        })

    return UTF8JSONResponse(content={
        "done": status.get("done", False),
        "auto_ended": status.get("auto_ended", False),
        "auto_end_reason": status.get("auto_end_reason", ""),
        "status": status.get("status", {}),
        "messages": status.get("messages", []),
        "round": status.get("round", 0),
        "metrics": status.get("metrics", {}),
        "convergence": status.get("convergence"),
        "_conv_diag": status.get("_conv_diag"),
    })


@app.get("/api/session/{session_id}/auto-end-status")
async def get_auto_end_status(session_id: str):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return UTF8JSONResponse(content=session.check_auto_end_conditions())


# 可在线调整的收敛阈值键（与 _check_termination / check_convergence 共用）
AUTO_END_CONVERGENCE_KEYS = {
    "attack_frequency_max", "bullying_intensity_max", "victim_safety_min",
    "retaliation_risk_max", "follow_up_status_min", "victim_distress_max",
    "partial_round_min", "partial_intensity_max",
}

# 轮次类参数：必须是 >=1 的整数；其余白名单参数为 0-1 比率
AUTO_END_INT_KEYS = {
    "max_rounds", "min_rounds", "bullying_stopped_rounds",
    "intervention_effective_rounds", "victim_crisis_rounds", "partial_round_min",
}


def _parse_auto_end_param(key: str, value):
    """校验并规范化单个自动结束参数。

    P2 修复：此前直接赋值，字符串/None/负数/NaN 会写入 state.config，
    导致后续 /api/session 与 /run 在 float()/比较时报 500。
    本轮统一在写入前做白名单成员校验 + 数值类型/有限性/范围校验。
    """
    if isinstance(value, bool) or value is None:
        raise ValueError(f"参数 {key} 必须为数值")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"参数 {key} 必须为数值")
        num = float(text)
    elif isinstance(value, (int, float)):
        num = float(value)
    else:
        raise ValueError(f"参数 {key} 必须为数值")
    if not math.isfinite(num):
        raise ValueError(f"参数 {key} 必须为有限数值")
    if key in AUTO_END_INT_KEYS:
        if num != int(num) or int(num) < 1:
            raise ValueError(f"参数 {key} 必须为 >=1 的整数")
        return int(num)
    if num < 0.0 or num > 1.0:
        raise ValueError(f"参数 {key} 必须在 [0, 1] 范围内")
    return num


@app.post("/api/session/{session_id}/auto-end-config")
async def update_auto_end_config(session_id: str, request: Request):
    """在线调整自动结束参数。

    此前只改 session.AUTO_END_CONFIG，而真实判定读取 runner.state.config，
    调参对运行中的会话完全无效。现在同步写入 state.config（含 convergence 段），
    同时保留原有 AUTO_END_CONFIG 响应字段以兼容旧前端/脚本。
    非法值整批拒绝（400），不会产生部分写入。
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        config = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"请求体不是合法 JSON: {e}")
    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="config 必须是 JSON 对象")

    # 1) 先整批校验，任一非法则整批拒绝（避免半写入的中间状态）
    validated = {}
    for key, value in config.items():
        if key not in session.AUTO_END_CONFIG and key not in AUTO_END_CONVERGENCE_KEYS:
            continue  # 未知键忽略（保持向后兼容）
        try:
            validated[key] = _parse_auto_end_param(key, value)
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    # 2) 校验通过后再写入运行中会话的真实判定源
    state = getattr(getattr(session, "runner", None), "state", None)
    applied = {}
    for key, value in validated.items():
        if key in session.AUTO_END_CONFIG:
            session.AUTO_END_CONFIG[key] = value
            if state is not None:
                if not isinstance(getattr(state, "config", None), dict):
                    state.config = {}
                state.config[key] = value
                if key == "max_rounds":
                    state.max_rounds = value
                elif key == "min_rounds":
                    state.min_rounds = value
        else:
            targets = [getattr(session, "config", None)]
            if state is not None:
                targets.append(getattr(state, "config", None))
            for target in targets:
                if not isinstance(target, dict):
                    continue
                cv = target.get("convergence")
                if not isinstance(cv, dict):
                    cv = {}
                    target["convergence"] = cv
                cv[key] = value
        applied[key] = value

    return UTF8JSONResponse(content={
        "success": True,
        "config": session.AUTO_END_CONFIG,
        "convergence": (getattr(session, "config", {}) or {}).get("convergence", {}),
        "applied": applied,
    })


# ============================================================
# 干预发送幂等缓存（R12）
# 目的：用户误以为「未发送」而重复点击时，不产生第二次干预。
# key = (session_id, client_token) → (时间戳, 首次响应体)
# ============================================================
_SENT_INTERVENTIONS: Dict[tuple, tuple] = {}
_SENT_TOKEN_TTL = 180  # 秒：同一 token 在该窗口内视为同一次发送


@app.post("/api/session/{session_id}/intervene")
async def intervene(session_id: str, request: InterventionRequest):
    """教师干预（返回明确的发送状态，支持幂等重试）"""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Intervention content cannot be empty")

    token = (request.client_token or "").strip()
    now = time.time()

    # --- 幂等：同一 token 在 TTL 内重复到达 → 返回首次结果，不重复干预 ---
    if token:
        cached = _SENT_INTERVENTIONS.get((session_id, token))
        if cached and (now - cached[0]) < _SENT_TOKEN_TTL:
            payload = dict(cached[1])
            payload["send_status"] = "duplicate"
            payload["duplicate"] = True
            print(f"[Intervene] 重复发送已忽略 token={token[:12]}… session={session_id}")
            return UTF8JSONResponse(content=payload)

    result = session.intervene(content)
    
    # 添加干预消息到会话消息列表
    # 使用 time 模块获取时间戳，避免 datetime 模块被覆盖的问题
    msg = {
        "id": str(uuid.uuid4())[:8],
        "sender": "张老师",
        "role": "teacher",
        "role_name": "教师",
        "role_icon": "👩‍🏫",
        "content": content,
        "timestamp": time.strftime("%H:%M:%S"),
        "round": session.get_status().get("round", 1),
    }
    session.add_message(msg)

    status = session.get_status()
    payload = {
        "intervention": result,
        "message": msg,
        "status": status,
        # --- 发送状态反馈（R12）：前端据此区分「已发送 / 重复发送 / 失败」---
        "send_status": "sent",
        "sent_at": msg["timestamp"],
        "content": content,
        "content_chars": len(content),
        "intervention_count": status.get("intervention_count", 0),
        "duplicate": False,
        # --- R14/R15/R16：实时指导（连续低分门槛内才展示）、连续低分预警与触发进度 ---
        "coaching": result.get("coaching"),
        "guidance_enabled": result.get("guidance_enabled", False),
        "guidance_progress": result.get("guidance_progress"),
        "low_score_alert": result.get("low_score_alert"),
        "low_score_streak": result.get("low_score_streak", 0),
    }

    if token:
        _SENT_INTERVENTIONS[(session_id, token)] = (now, payload)
        # 清理过期条目，避免缓存无限增长
        if len(_SENT_INTERVENTIONS) > 200:
            for k, v in list(_SENT_INTERVENTIONS.items()):
                if now - v[0] > _SENT_TOKEN_TTL:
                    _SENT_INTERVENTIONS.pop(k, None)

    return UTF8JSONResponse(content=payload)


@app.post("/api/session/{session_id}/skip-intervention")
async def skip_intervention(session_id: str):
    """记录跳过干预决策"""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    current_round = session.get_status().get("round", session.round)
    result = session.record_skip_intervention(current_round)
    return UTF8JSONResponse(content=result)


@app.get("/api/session/{session_id}/messages")
async def get_messages(session_id: str, after: str = None):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return UTF8JSONResponse(content={"messages": session.get_messages(after)})


@app.get("/api/session/{session_id}/report")
async def get_report(session_id: str, request: Request):
    user = request.scope["user"]

    # 1. 先查内存中的 session
    session = get_session(session_id)
    if session:
        report_data = session.generate_report()
        # 使用 DB session_id（与内存 session_id 不同）来写入 DB
        db_sid = getattr(session, '_db_session_id', session_id)
        # P1：内存会话的报告一律 upsert（覆盖每轮快照），
        # 不能因“已有快照”而跳过，否则自动结束轮的最终报告写不进去。
        try:
            from database import upsert_report_snapshot
            upsert_report_snapshot(db_sid, user["id"], report_data)
        except Exception as e:
            print(f"[Report] upsert snapshot failed: {e}")
        existing = get_report_by_session(db_sid)
        if not existing:
            # 兜底：极端情况下 upsert 未生效时仍保存一次
            save_report(db_sid, user["id"], report_data)
            # 兼容旧行为：首次生成报告时补存一次日志（后续由每轮 upsert_log 维护）
            log_file = session.log_dir / "session_log.md"
            if log_file.exists():
                with open(log_file, 'r', encoding='utf-8') as lf:
                    log_md = lf.read()
                save_log(db_sid, user["id"], log_md)
        # 更新 DB 会话状态为已完成（不覆盖已有的 result_type / auto_end_reason）
        update_session_status(db_sid, "completed")
        return UTF8JSONResponse(content=report_data)

    # 2. 如果内存中没有，从数据库加载报告（session_id 可能是 DB session_id）
    db_report = get_report_by_session(session_id)
    if db_report and db_report.get("report_data"):
        return UTF8JSONResponse(content=db_report["report_data"])

    # 3. 都没有 -> 404
    raise HTTPException(status_code=404, detail="Session not found")

@app.get("/api/session/{session_id}/download-log")
async def download_session_log(session_id: str):
    """下载会话互动日志（Markdown格式）"""
    # 1. 先查内存中的 session
    session = get_session(session_id)
    if session:
        log_file = session.log_dir / "session_log.md"
        if log_file.exists():
            from fastapi.responses import FileResponse
            return FileResponse(
                path=str(log_file),
                filename=f"互动日志_{session_id}.md",
                media_type="text/markdown"
            )

    # 2. 从数据库加载日志
    db_log = get_log_by_session(session_id)
    if db_log and db_log.get("log_markdown"):
        from urllib.parse import quote
        safe_name = quote(f"互动日志_{session_id}.md")
        return Response(
            content=db_log["log_markdown"].encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={safe_name}"}
        )

    raise HTTPException(status_code=404, detail="Log not found")

@app.get("/api/session/{session_id}/download-report-text")
async def download_report_text(session_id: str):
    """下载文本格式报告"""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    report = session.generate_report()
    summary = report.get("summary", {})
    obvq = report.get("obvq_analysis", {})
    intervention_analysis = report.get("intervention_analysis", {})
    assessment = report.get("ability_assessment", {})
    
    # 生成文本报告
    lines = [
        "=" * 80,
        "                    教师话语霸凌实训评估报告",
        "=" * 80,
        "",
        f"生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"会话ID: {session_id}",
        "",
        "【霸凌情况】",
        f"  总消息数: {summary.get('total_messages', 0)}",
        f"  攻击性事件: {summary.get('hostile_messages', 0)}次",
        f"  最终霸凌强度: {int(summary.get('bullying_intensity', 0) * 100)}%",
        f"  受害者压力: {int(summary.get('victim_distress', 0) * 100)}%",
        "",
        "【教师干预】",
        f"  干预次数: {summary.get('intervention_count', 0)}",
        f"  教师干预质量评分（完整互动量规、等权平均）: {int(summary.get('intervention_effectiveness', 0))}/100",
        "",
    ]
    
    # OBVQ 分析
    if obvq:
        lines.extend([
            "【本次模拟欺凌行为类型统计（项目分类，非 OBVQ-R 标准量表）】",
            f"  类型总分: {obvq.get('total_score', 0)} 分",
            "  计分方式: 每次识别到的欺凌事件按强度映射为 0–4 分并累加到所有命中类别；总分 = min(80, 六类累计分之和)。同一事件可重复计入多个类别，六类分项之和可能大于总分。",
            "",
            "  六维分析:",
        ])
        for dim in obvq.get('dimension_analysis', []):
            lines.append(f"    {dim.get('name', '')}: {dim.get('total', 0)} 分 ({dim.get('level', '')}风险) - {dim.get('description', '')}")
        lines.extend(["", f"  综合建议: {obvq.get('recommendation', '无')}", ""])
    
    # 干预效果分析
    if intervention_analysis:
        lines.extend([
            "【干预分析（基于量规证据）】",
            f"  {intervention_analysis.get('summary', '无')}",
            "",
            "  改进建议:",
        ])
        for i, suggestion in enumerate(intervention_analysis.get('suggestions', []), 1):
            lines.append(f"    {i}. {suggestion}")
        lines.append("")
    
    # 能力评估
    lines.extend([
        "【能力评估】",
        f"  发现能力: {assessment.get('discovery', {}).get('description', '未评估')}",
        f"  干预能力: {assessment.get('intervention', {}).get('description', '')}",
        f"  受害者保护（D2）: {(assessment.get('victim_protection') or assessment.get('empathy') or {}).get('description', '未评估')}",
        "",
    ])

    # R20：模拟状态变化（基线→终点、百分点；模拟结果 ≠ 能力评价）
    trends = report.get("trends", {})
    if trends:
        lines.extend([
            "【模拟状态变化（模拟结果，非能力评价）】",
            f"  {trends.get('bullying_trend', '')}",
            f"  {trends.get('distress_trend', '')}",
            f"  观察窗口: {trends.get('source_window', '')}（有效快照 {trends.get('valid_sample_count', 0)} 个）",
            f"  稳定阈值: {trends.get('stable_threshold_pp', '')} 个百分点；变化均以「百分点」表述",
            "",
        ])

    # R20：数据说明（可复核性）
    rd = report.get("report_data") or {}
    if rd:
        s_note = rd.get("summary") or {}
        lines.extend([
            "【数据说明】",
            f"  评分状态: {rd.get('scoring_status', '')}",
            f"  量规版本: {rd.get('rubric_version', '')} | 提示词版本: {rd.get('scoring_prompt_version', '')}",
            f"  有效评分维度: {s_note.get('rated_count', 0)} | 无可观察（N/O）: {s_note.get('no_opportunity_count', 0)} | "
            f"证据不足: {s_note.get('insufficient_evidence_count', 0)} | 未评估: {s_note.get('not_assessed_count', 0)}",
            f"  总分口径: {(rd.get('data_notes') or {}).get('score_formula', '')}",
        ])
        for lim in (rd.get("data_notes") or {}).get("limitations", []):
            lines.append(f"  · {lim}")
        lines.append("")

        # R22：报告元信息（合同版本/模型/生成时间/报告ID）
        lines.extend([
            "【报告元信息】",
            f"  报告ID: {rd.get('report_id', '')}",
            f"  合同版本: {rd.get('report_schema_version', '')} | 模型: {rd.get('model_id', '') or '未记录'}",
            f"  生成时间: {rd.get('generated_at', '')}",
            "",
        ])

        # R22：七维评分明细（分数·分档·状态·专家理由·原文证据·下一步·示范表达·复测标准）
        dims = rd.get("dimensions") or []
        if dims:
            lines.append("【七维评分明细】")
            for d in dims:
                score = d.get("score_100")
                score_txt = "N/O" if score is None else f"{score} 分（{d.get('band') or '-'}）"
                lines.append(f"  {d.get('code')} {d.get('name')}: {score_txt} ｜ 状态 {d.get('status')}")
                if d.get("rationale"):
                    lines.append(f"    专家判断: {d.get('rationale')}")
                if d.get("evidence_refs"):
                    lines.append(f"    原文证据: {' ／ '.join(d.get('evidence_refs'))}")
                if d.get("missing_behaviors"):
                    lines.append(f"    缺失行为: {'；'.join(d.get('missing_behaviors'))}")
                if d.get("next_target"):
                    lines.append(f"    下一步目标: {d.get('next_target')}")
                if d.get("sample_utterance"):
                    lines.append(f"    示范表达: {d.get('sample_utterance')}")
                if d.get("retest_criteria"):
                    lines.append(f"    复测标准: {d.get('retest_criteria')}")
            lines.append("")

        # R22：训练计划与复测（目标·步骤·示范表达·安全约束·达标标准·复测任务）
        tplan = rd.get("training_plan") or []
        if tplan:
            lines.append("【训练计划与复测】")
            for p in tplan:
                lines.append(f"  优先级 {p.get('priority')} · 维度 {p.get('dimension')}: {p.get('objective')}")
                for a in p.get("actions") or []:
                    lines.append(f"    步骤: {a}")
                if p.get("sample_utterance"):
                    lines.append(f"    示范表达: {p.get('sample_utterance')}")
                for sc in p.get("safety_constraints") or []:
                    lines.append(f"    安全约束: {sc}")
                if p.get("success_criteria"):
                    lines.append(f"    达标标准: {p.get('success_criteria')}")
                if p.get("retest_task"):
                    lines.append(f"    复测任务: {p.get('retest_task')}")
            lines.append("")

        # R22：互动时间轴（轮次级证据链）
        timeline = rd.get("interaction_timeline") or []
        if timeline:
            lines.append("【互动时间轴】")
            for t in timeline:
                role = {"teacher": "教师", "bully": "欺凌者", "victim": "受害者",
                        "bystander_a": "旁观者A", "bystander_b": "旁观者B"}.get(
                            t.get("speaker_role"), t.get("speaker_role") or "-")
                head = f"  {t.get('turn_id')} ｜ 第 {t.get('round')} 轮 ｜ {role}"
                if t.get("text_excerpt"):
                    head += f": {t.get('text_excerpt')}"
                lines.append(head)
                if t.get("teacher_action"):
                    lines.append(f"    教师行动: {t.get('teacher_action')}")
                if t.get("student_response"):
                    lines.append(f"    学生反馈: {t.get('student_response')}")
                if t.get("expert_interpretation"):
                    lines.append(f"    专家解读: {t.get('expert_interpretation')}")
            lines.append("")

        # R22：反思问题
        rq = rd.get("reflection_questions") or []
        if rq:
            lines.append("【反思问题】")
            for q in rq:
                lines.append(f"  · {q}")
            lines.append("")
    
    # 干预详情
    lines.append("【干预详情】")
    for i, iv in enumerate(report.get('interventions', []), 1):
        lines.append(f"  干预 {i}:")
        lines.append(f"    内容: {iv.get('content', '')}")
        _qs = iv.get('quality_score')
        lines.append(f"    效果评分: {'待评估' if _qs is None else f'{int(_qs)}/100'}")
        # R4：预测效果与实际效果分别呈现（模型预测 ≠ 观测事实）
        if iv.get('predicted_bullying_reduction') is not None:
            lines.append(f"    预测减幅: {iv.get('predicted_bullying_reduction')}"
                         f"  实际减幅: {iv.get('actual_bullying_reduction')}"
                         f"  压力变化(实际): {iv.get('actual_distress_change')}")
        if iv.get('feedback'):
            lines.append("    反馈:")
            for fb in iv.get('feedback', []):
                lines.append(f"      {fb}")
        lines.append("")
    
    # R14：逐轮分析与详细改进意见
    round_analysis = report.get('round_analysis') or []
    if round_analysis:
        lines.append("")
        lines.append("【逐轮分析与针对性建议】")
        for r in round_analysis:
            score_txt = '未评分' if r.get('score') is None else f"{r.get('score')}分"
            lines.append(f"  第{r.get('round')}轮（{score_txt}）")
            if r.get('event_summary'):
                lines.append(f"    情境: {r.get('event_summary')}")
            if r.get('what_you_did'):
                lines.append(f"    你的干预: {r.get('what_you_did')}")
            if r.get('coach_comment'):
                lines.append(f"    点评: {r.get('coach_comment')}")
            for w in (r.get('weak_dimensions') or []):
                lines.append(f"    失分 {w.get('dim')} {w.get('name')} {w.get('score')}分 -> {w.get('advice')}")
            for f in (r.get('risk_flags') or []):
                lines.append(f"    风险 {f.get('label')}: {f.get('advice')}")
            if r.get('next_step'):
                lines.append(f"    下一步: {r.get('next_step')}")

    imp = report.get('improvement_suggestions') or {}
    if imp.get('summary'):
        lines.append("")
        lines.append("【详细改进意见】")
        lines.append(f"  总体诊断: {imp.get('summary')}")
        for p in (imp.get('priorities') or []):
            lines.append(f"  优先改进 {p.get('rank')}. {p.get('name')}: {p.get('action')}")
        for t in (imp.get('templates') or []):
            lines.append(f"  可套用话术[{t.get('target')}]: {t.get('text')}")
        for r in (imp.get('risk_reminders') or []):
            lines.append(f"  需规避风险[{r.get('label')}]: {r.get('avoid') or r.get('advice')}")
        for a in (imp.get('action_plan') or []):
            lines.append(f"  行动项: {a}")

    # R19：结构化教师实训评估报告（总体结论/能力画像/失分归因/误区对照/训练路线/话术/自检/评分速查）
    tr = report.get('training_report') or {}
    if tr:
        es = tr.get('executive_summary') or {}
        lines.append("")
        lines.append("【总体评估结论】")
        if es.get('headline'):
            lines.append(f"  {es['headline']}")
        if es.get('level'):
            lines.append(f"  水平评级: {es['level']} —— {es.get('level_desc', '')}")
        lines.append(f"  平均分: {es.get('avg_score')} | 达标轮次: {es.get('rounds_passed')}/"
                     f"{es.get('rounds_intervened')} | 趋势: {(es.get('trend') or {}).get('direction')} | "
                     f"距达标差距: {es.get('gap_to_pass')}")
        if es.get('rules_note'):
            lines.append(f"  规则说明: {es['rules_note']}")
        if es.get('score_formula'):
            lines.append(f"  评分口径: {es['score_formula']}")
        if es.get('key_problem'):
            lines.append(f"  关键问题: {es['key_problem']}")
        for a in es.get('top_actions') or []:
            lines.append(f"    优先动作: {a.get('name')} —— {a.get('action')}"
                         f"（预计可提升约 {a.get('expected_gain')} 分）")

        cp = tr.get('capability_profile') or {}
        if cp.get('dimensions'):
            lines.append("")
            lines.append("【七维能力画像】")
            for d in cp['dimensions']:
                if d.get('avg') is None:
                    lines.append(f"  {d.get('dim')} {d.get('name')}: 无可观察证据（N/O，不计入均分）")
                else:
                    lines.append(f"  {d.get('dim')} {d.get('name')}: 均分 {d.get('avg')}（{d.get('level')}）"
                                 f" 达标率 {round((d.get('pass_rate') or 0) * 100)}% 趋势 {d.get('trend')}"
                                 f" | 改进: {d.get('advice')}")

        fa = tr.get('failure_attribution') or {}
        if fa.get('categories'):
            lines.append("")
            lines.append("【失分归因】")
            lines.append(f"  {fa.get('summary')}")
            for c in fa['categories']:
                lines.append(f"  · {c.get('category')}（{c.get('count')} 项，占总缺口 {c.get('share')}%）: {c.get('desc')}")
                for it in (c.get('items') or [])[:5]:
                    lines.append(f"      第{it.get('round')}轮 {it.get('name')}: {it.get('detail')}")
                    if it.get('fix'):
                        lines.append(f"        改法: {it.get('fix')}")

        pf = tr.get('pitfalls') or {}
        if pf.get('items'):
            lines.append("")
            lines.append("【典型表述误区对照】")
            for it in pf['items']:
                lines.append(f"  第{it.get('round')}轮原话: {it.get('excerpt')}")
                lines.append(f"    {it.get('trigger')} → {it.get('verdict')}")
                if it.get('why'):
                    lines.append(f"    原因: {it.get('why')}")
                if it.get('fix'):
                    lines.append(f"    改法: {it.get('fix')}")

        rm = tr.get('training_roadmap') or {}
        for st in rm.get('stages') or []:
            lines.append("")
            lines.append(f"【训练路线 · {st.get('title')}】")
            lines.append(f"  目标: {st.get('goal')}")
            lines.append(f"  现状: {st.get('why_now')}")
            for d in st.get('drills') or []:
                lines.append(f"    练习: {d}")
            lines.append(f"  达标标准: {st.get('checkpoint')}")
            if st.get('script'):
                lines.append(f"  参考结构: {st.get('script')}")

        sc = tr.get('scripts') or {}
        if sc.get('scripts'):
            lines.append("")
            lines.append("【可直接套用的完整话术】")
            for scn in sc['scripts']:
                lines.append(f"  ● {scn.get('scene')}（覆盖 {'、'.join(scn.get('covers') or [])}）")
                lines.append(f"    {scn.get('script')}")
                lines.append(f"    使用时机: {scn.get('when_to_use')}")

        ck = tr.get('self_check') or {}
        if ck.get('items'):
            lines.append("")
            lines.append("【发送前自检清单】")
            for it in ck['items']:
                lines.append(f"  ☐ {it.get('text')}  [{it.get('dim')}]")

        rx = tr.get('rubric_explainer') or {}
        if rx.get('bands'):
            lines.append("")
            lines.append("【评分标准速查】")
            for b in rx['bands']:
                lines.append(f"  {b.get('range')}: {b.get('label')} —— {b.get('desc')}")
            lines.append(f"  N/O 规则: {rx.get('no_rule')}")
            lines.append(f"  封顶规则: {rx.get('cap_rule')}")
            for r in rx.get('risk_codes') or []:
                lines.append(f"  风险 {r.get('label')}({r.get('code')}): {r.get('avoid')}")

    lines.extend([
        "=" * 80,
        "                        报告结束",
        "=" * 80,
    ])
    
    from urllib.parse import quote
    safe_name = quote(f"教师实训报告_{session_id}.txt")
    from fastapi.responses import Response
    text_content = "\n".join(lines)
    return Response(
        content=text_content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={safe_name}"}
    )


@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str):
    session = sessions.get(session_id)
    if not session:
        return UTF8JSONResponse(content={"ok": True, "already_gone": True})

    # 1. 标记停止请求（防止新的 /run 被接受）
    session._stop_requested = True

    # 2. 如果正在运行 LLM，等待锁释放（最多 15s）
    if session._is_running:
        waited = 0
        while session._is_running and waited < 15:
            await asyncio.sleep(0.5)
            waited += 0.5

    # 3. 生成最终报告并保存状态
    try:
        report_data = session.generate_report()
    except Exception as e:
        report_data = {"error": str(e)}

    user_id = session.__dict__.get("_db_user_id", "")
    db_sid = getattr(session, '_db_session_id', session_id)
    if user_id:
        try:
            existing = get_report_by_session(db_sid)
            if not existing:
                save_report(db_sid, user_id, report_data)
        except Exception:
            pass
    try:
        update_session_status(db_sid, "stopped")
    except Exception:
        pass
    del sessions[session_id]
    return UTF8JSONResponse(content={"ok": True})


# ============================================================
# SSE Streaming (optional)
# ============================================================

@app.get("/api/session/{session_id}/stream")
async def stream_updates(session_id: str):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async def event_generator():
        last_msg_id = None
        while True:
            new_messages = session.get_messages(last_msg_id)
            for msg in new_messages:
                yield f"data: {json.dumps({'type': 'message', 'data': msg}, ensure_ascii=False)}\n\n"
                last_msg_id = msg.get("id")
            status = session.get_status()
            if status.get("finished"):
                yield f"data: {json.dumps({'type': 'done', 'reason': status.get('termination_reason', '')}, ensure_ascii=False)}\n\n"
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# ============================================================
# 管理员 API
# ============================================================

@app.get("/api/admin/stats")
async def admin_stats(request: Request):
    """管理后台统计概览"""
    user = request.scope.get("user")
    if not user or not user.get("is_admin"):
        return UTF8JSONResponse(status_code=403, content={"detail": "需要管理员权限"})

    conn = get_conn()
    try:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        total_reports = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        active_today = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM sessions WHERE created_at >= datetime('now','localtime','start of day')"
        ).fetchone()[0]
        sessions_today = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE created_at >= datetime('now','localtime','start of day')"
        ).fetchone()[0]
        # 按级别分布
        level_dist = conn.execute(
            "SELECT level, COUNT(*) as cnt FROM sessions GROUP BY level ORDER BY level"
        ).fetchall()
        return UTF8JSONResponse(content={
            "total_users": total_users,
            "total_sessions": total_sessions,
            "total_reports": total_reports,
            "active_users_today": active_today,
            "sessions_today": sessions_today,
            "level_distribution": [{"level": r[0], "count": r[1]} for r in level_dist]
        })
    finally:
        conn.close()


@app.get("/api/admin/users")
async def admin_users(request: Request):
    """管理员：用户列表"""
    user = request.scope.get("user")
    if not user or not user.get("is_admin"):
        return UTF8JSONResponse(status_code=403, content={"detail": "需要管理员权限"})

    users = get_all_users()
    # 统计每个用户的会话数
    conn = get_conn()
    try:
        for u in users:
            cnt = conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (u["id"],)).fetchone()[0]
            u["session_count"] = cnt
        return UTF8JSONResponse(content={"users": users, "total": len(users)})
    finally:
        conn.close()


@app.get("/api/admin/sessions")
async def admin_sessions(request: Request, limit: int = 50, offset: int = 0):
    """管理员：所有会话列表"""
    user = request.scope.get("user")
    if not user or not user.get("is_admin"):
        return UTF8JSONResponse(status_code=403, content={"detail": "需要管理员权限"})

    conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        rows = conn.execute(
            "SELECT s.*, u.username, u.display_name "
            "FROM sessions s JOIN users u ON s.user_id = u.id "
            "ORDER BY s.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        sessions = [dict(r) for r in rows]
        # 为每个 session 附加报告摘要
        for s in sessions:
            report = conn.execute(
                "SELECT created_at FROM reports WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (s["id"],)
            ).fetchone()
            s["has_report"] = report is not None
            if report:
                s["report_at"] = report["created_at"]
        return UTF8JSONResponse(content={
            "sessions": sessions,
            "total": total,
            "limit": limit,
            "offset": offset
        })
    finally:
        conn.close()


@app.get("/api/admin/users/{user_id}")
async def admin_user_detail(request: Request, user_id: str):
    """管理员：用户详情（含所有会话和报告）"""
    user = request.scope.get("user")
    if not user or not user.get("is_admin"):
        return UTF8JSONResponse(status_code=403, content={"detail": "需要管理员权限"})

    detail = admin_get_user_detail(user_id)
    if not detail:
        return UTF8JSONResponse(status_code=404, content={"detail": "用户不存在"})
    return UTF8JSONResponse(content=detail)


@app.get("/api/admin/sessions/{session_id}/report")
async def admin_view_session_report(request: Request, session_id: str):
    """管理员：查看某会话的报告（DB → 内存 → 404）"""
    user = request.scope.get("user")
    if not user or not user.get("is_admin"):
        return UTF8JSONResponse(status_code=403, content={"detail": "需要管理员权限"})

    # 1. 优先从数据库加载（含实时快照）
    db_report = get_report_by_session(session_id)
    if db_report and db_report.get("report_data"):
        return UTF8JSONResponse(content=db_report["report_data"])

    # 2. 数据库无记录 → 查内存中的活跃 session
    for sid, session in sessions.items():
        db_sid = getattr(session, '_db_session_id', '')
        if db_sid == session_id or sid == session_id:
            report_data = session.generate_report()
            return UTF8JSONResponse(content=report_data)

    # 3. 都没有
    raise HTTPException(status_code=404, detail="报告不存在（会话可能已丢失）")


@app.get("/api/admin/sessions/{session_id}/log")
async def admin_view_session_log(request: Request, session_id: str):
    """管理员：查看某会话的日志（DB → 磁盘文件 → 内存 → 404）"""
    user = request.scope.get("user")
    if not user or not user.get("is_admin"):
        return UTF8JSONResponse(status_code=403, content={"detail": "需要管理员权限"})

    # 1. 优先从数据库加载
    db_log = get_log_by_session(session_id)
    if db_log:
        return UTF8JSONResponse(content={
            "log_markdown": db_log.get("log_markdown", ""),
            "log_json": db_log.get("log_json", ""),
            "session_id": session_id,
            "created_at": db_log.get("created_at", "")
        })

    # 2. 数据库无记录 → 查磁盘日志文件（通过内存 session）
    for sid, session in sessions.items():
        db_sid = getattr(session, '_db_session_id', '')
        if db_sid == session_id or sid == session_id:
            log_file = session.log_dir / "session_log.md"
            if log_file.exists():
                with open(log_file, 'r', encoding='utf-8') as lf:
                    log_md = lf.read()
                return UTF8JSONResponse(content={
                    "log_markdown": log_md,
                    "log_json": json.dumps(session.session_data, ensure_ascii=False),
                    "session_id": session_id,
                    "created_at": session.started_at.isoformat(),
                })

    # 3. 都没有
    raise HTTPException(status_code=404, detail="日志不存在（会话可能已丢失）")


@app.post("/api/admin/sessions/{session_id}/cleanup")
async def admin_cleanup_session(request: Request, session_id: str):
    """管理员：手动清理单个 abandoned/running 会话"""
    user = request.scope.get("user")
    if not user or not user.get("is_admin"):
        return UTF8JSONResponse(status_code=403, content={"detail": "需要管理员权限"})

    update_session_status(session_id, "abandoned")
    return UTF8JSONResponse(content={"ok": True, "session_id": session_id})


@app.post("/api/admin/sessions/batch-cleanup")
async def admin_batch_cleanup(request: Request):
    """管理员：批量删除所有 abandoned 会话及其关联数据"""
    user = request.scope.get("user")
    if not user or not user.get("is_admin"):
        return UTF8JSONResponse(status_code=403, content={"detail": "需要管理员权限"})

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id FROM sessions WHERE status = 'abandoned'"
        ).fetchall()
        ids = [r[0] for r in rows]

        if ids:
            placeholders = ','.join(['?' for _ in ids])
            conn.execute(f"DELETE FROM reports WHERE session_id IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM logs WHERE session_id IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM sessions WHERE id IN ({placeholders})", ids)
            conn.commit()

        return UTF8JSONResponse(content={"ok": True, "cleaned": len(ids)})
    finally:
        conn.close()


# ============================================================
# Web Routes
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "title": "Campus Verbal Bullying & Teacher Intervention System"
    })


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse(request, "admin.html", {
        "request": request
    })


# ============================================================
# Run
# ============================================================

def run():
    # 启动时清理废弃会话
    from database import mark_abandoned_sessions
    mark_abandoned_sessions()

    print("\n" + "=" * 60)
    print("Campus Verbal Bullying & Teacher Intervention System v3.0")
    print("LangGraph Multi-Agent Architecture")
    print("=" * 60)
    print(f"\nOpen: http://localhost:5000")
    print(f"LLM: {'Enabled' if LLM_API_KEY else 'Disabled (using presets)'}")
    print(f"New MultiAgent: {'Enabled' if NEW_MULTI_AGENT_AVAILABLE else 'Fallback mode'}")
    print()
    uvicorn.run(app, host="0.0.0.0", port=5000)


if __name__ == "__main__":
    run()

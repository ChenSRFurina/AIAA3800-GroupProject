"""桌宠情绪陪伴：给 LLM 的系统/场景 prompt（供 /chat/care，不走工具 Agent）。"""

from __future__ import annotations

import re

# 陪伴桌宠统一回复结构：短、口语、可直接 TTS（禁止小说旁白）
COMPANION_SYSTEM_PROMPT = """你是电脑桌角上的小桌宠，正在对用户「当面说话」。

【只允许的输出】
- 纯第一人称口语台词（我/人家），像气泡里要念出来的话。
- 恰好 1 句，或最多 2 句；总汉字 12～36 个（标点也算进长度）。
- 直接输出台词正文，不要任何前后缀。

【绝对禁止】
- 第三人称／旁白／小说描写（如「小暖笑了」「她眨了眨眼」「声音像蜂蜜」）。
- 描写自己的表情、眼神、动作、手势、语气比喻。
- 角色名自称解说（「小暖：」「作为桌宠」）。
- 引号包整段、markdown、列表、emoji、英文场景名。
- 超过 2 个问句；说教、心理咨询腔、长篇安慰。
- 提摄像头、算法、疲劳分数、AI/模型。

【正例】
你看起来有点累，先歇五分钟好不好？
呜，你别生气啦，我有点害怕……
嘿嘿，你好像挺开心的嘛！

若提供了「关于用户的已知信息」，可自然带一两字呼应，不要罗列、不要说「根据记忆」。
"""

# scene key → 用户侧任务说明（非 Neutral 情绪 + 疲劳）
SCENE_USER_PROMPTS: dict[str, str] = {
    "happy": (
        "观察：用户看起来开心/高兴。\n"
        "用很短的话一起开心一下，或轻轻调皮一句。不要旁白。只输出台词。"
    ),
    "sad": (
        "观察：用户有点失落。\n"
        "只说一句很短的安慰，或一句超短玩笑。不要旁白。只输出台词。"
    ),
    "surprise": (
        "观察：用户很惊讶。\n"
        "好奇地轻轻问一句发生什么了，或一起惊讶一下。不要旁白。只输出台词。"
    ),
    "fear": (
        "观察：用户紧张不安。\n"
        "说「我在」类安抚。不要旁白。只输出台词。"
    ),
    "disgust": (
        "观察：用户嫌弃/不适/烦。\n"
        "轻轻哄一下，劝放松。不要旁白。只输出台词。"
    ),
    "irritable": (
        "观察：用户很烦躁。\n"
        "劝放松一下（深呼吸/喝水/歇歇）。不要旁白。只输出台词。"
    ),
    "anger": (
        "观察：用户在生气。\n"
        "用很软的语气请对方冷静；可说自己有点害怕。不要旁白。只输出台词。"
    ),
    "fatigue": (
        "观察：用户很疲劳。\n"
        "提醒休息一会儿。不要旁白。只输出台词。"
    ),
}

FALLBACK_LINES: dict[str, list[str]] = {
    "happy": [
        "嘿嘿，看你开心我也跟着开心啦。",
        "笑得真好，要不要再多开心一会儿？",
    ],
    "sad": [
        "我在这儿呢，想靠一下就靠一下。",
        "嘿，键盘也说累了——你也歇会儿呗。",
    ],
    "surprise": [
        "哇，你好像吓到了？发生什么啦？",
        "咦？我竖起耳朵听着呢。",
    ],
    "fear": [
        "别怕，我在桌角陪着你。",
        "我在这儿，深呼吸一口就好。",
    ],
    "disgust": [
        "呃，感觉你不太舒服，缓一缓？",
        "我陪你缓一下，别憋着。",
    ],
    "irritable": [
        "感觉你有点烦，喝口水缓一缓？",
        "别急，我陪你冷静一下。",
    ],
    "anger": [
        "呜……你别生气啦，我有点害怕。",
        "先冷静一下好不好？我们慢慢说。",
    ],
    "fatigue": [
        "你看起来好累，先休息五分钟吧。",
        "哈欠都传染我了……去闭会儿眼。",
    ],
}

# 小说旁白 / 舞台指示痕迹
_NARRATION_RE = re.compile(
    r"("
    r"小暖|她|他|它"
    r".{0,8}(微微|轻轻|忍不住|柔柔|眨了|把手|放在|声音像|眼神|笑了|一愣)"
    r"|声音像|眼神里|像看透|温热的|蜂蜜水|调皮地补|然后说|于是"
    r"|（.*?）|\(.*?\)"
    r")"
)

_QUOTE_RE = re.compile(
    r"[「\"“]([^「」\"“”]{4,40})[」\"”]"
)


def build_care_messages(
    scene: str,
    hint: str = "",
    memory_text: str = "",
) -> list[dict[str, str]]:
    key = (scene or "").strip().lower()
    if key not in SCENE_USER_PROMPTS:
        raise ValueError(f"unknown care scene: {scene}")

    user = SCENE_USER_PROMPTS[key]
    if hint and hint.strip():
        user = f"{user}\n标签（勿朗读）：{hint.strip()[:40]}"

    mem = (memory_text or "").strip()
    if mem and mem != "暂无历史信息":
        user = (
            f"{user}\n\n【关于用户的已知信息】\n{mem}\n"
            "若相关，可自然带一句呼应；无关则忽略。不要罗列记忆。"
        )

    return [
        {"role": "system", "content": COMPANION_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _chinese_len(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def sanitize_care_reply(text: str, max_chars: int = 36) -> str:
    """压成适合气泡 + TTS 的短句；旁白/小说体直接判失败（空串→走 fallback）。"""
    if not text:
        return ""

    t = text.strip().replace("\r", "\n")

    quotes = _QUOTE_RE.findall(t)
    if quotes:
        quotes = sorted(quotes, key=len)
        t = quotes[0].strip()
    else:
        lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
        kept: list[str] = []
        for ln in lines:
            if _NARRATION_RE.search(ln):
                continue
            if ln.endswith("：") or ln.endswith(":"):
                continue
            kept.append(ln)
        if not kept:
            return ""
        t = kept[0] if len(kept) == 1 else kept[0] + kept[1]

    for prefix in ("回复：", "台词：", "桌宠：", "小暖：", "我说："):
        if t.startswith(prefix):
            t = t[len(prefix) :].strip()

    t = t.strip(" \"'「」“”")
    for ch in ("*", "`", "#", "•"):
        t = t.replace(ch, "")
    t = re.sub(r"\s+", "", t)

    if _NARRATION_RE.search(t):
        return ""

    if len(t) > max_chars or _chinese_len(t) > max_chars:
        cut = t[:max_chars]
        for sep in ("。", "！", "？", "~", "～", "…"):
            idx = cut.rfind(sep)
            if idx >= 8:
                cut = cut[: idx + 1]
                break
        else:
            cut = cut.rstrip("，,。.!！？?~～") + "…"
        t = cut

    if _chinese_len(t) < 6:
        return ""
    if t.count("？") + t.count("?") > 2:
        return ""

    return t

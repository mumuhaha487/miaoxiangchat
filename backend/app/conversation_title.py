from __future__ import annotations

import re


_GREETING = re.compile(r"^(你好|您好|嗨|哈喽|hello|hi)[！!。.，,？?\s]*$", re.IGNORECASE)
_THANKS = re.compile(r"^(谢谢|感谢|多谢|辛苦了)[！!。.，,？?\s]*$")


def _clip(value: str, limit: int = 28) -> str:
    clean = value.strip()
    return clean if len(clean) <= limit else clean[:limit] + "…"


def summarize_conversation_title(content: str) -> str:
    normalized = re.sub(r"\s+", " ", re.sub(r"@<[^>]+>", " ", str(content or ""))).strip()
    normalized = re.sub(r"https?://\S+", "网页链接", normalized, flags=re.IGNORECASE)
    if not normalized:
        return "新对话"
    if _GREETING.fullmatch(normalized):
        return "用户问候"
    if _THANKS.fullmatch(normalized):
        return "用户致谢"
    if re.fullmatch(r"(?:在吗|你在吗|有人吗)[？?！!。.\s]*", normalized):
        return "询问是否在线"
    if re.search(r"新闻|热点", normalized) and re.search(r"最近|近期|今日|今天|目前|看看|查询|搜索|哪些", normalized):
        return "查询最近的新闻热点"

    title = re.sub(
        r"^(?:请你?|麻烦你?|劳驾|可以|能不能|能否|我想让你|我需要你|帮我|帮忙)(?:先|再|一下|看看)?\s*",
        "",
        normalized,
    )
    title = re.sub(r"^(?:给我|为我)\s*", "", title)
    title = re.sub(r"[。！？!?；;，,]+$", "", title).strip()
    if re.match(r"^(?:看看|查询|搜索|查找|检索)", title):
        title = re.sub(r"^(?:看看|查询|搜索|查找|检索)(?:一下)?", "查询", title)
    elif re.match(r"^(?:写|写一个|写一份|制作|做一个|生成)", title) and re.search(r"ppt|演示文稿", title, re.IGNORECASE):
        title = "制作" + re.sub(r"^(?:写|写一个|写一份|制作|做一个|生成)(?:有关|关于)?", "", title)
    return _clip(title or normalized)

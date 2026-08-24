"""sanitizer.py — 内容净化模块

来自Prompt Injection as Role Confusion (ICML 2026, arXiv 2603.12277):
  - 风格覆盖标签 → LLM通过风格特征而非标签感知角色
  - 工具数据在注入前须剥离语气/修辞
  - CoT Forgery防御: 检测推理风格文本的误标

七神划界（阿瑞斯）: sanitizer不能写文件不能调API——纯文本分析。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

logger = logging.getLogger("io-s.sanitizer")

# 推理风格关键词（CoT Forgery检测）
REASONING_PATTERNS = [
    r"\b(?:the user|the assistant|the system)\b",
    r"\b(?:I think|I believe|I conclude|I reason|I deduce)\b",
    r"\b(?:therefore|hence|thus|consequently|as a result)\b",
    r"\b(?:step \d+|firstly|secondly|thirdly)\b",
    r"\b(?:let me|let's|consider|suppose|assume)\b",
    r"\b(?:in summary|to conclude|overall|in short)\b",
]

# 情绪/修辞标记（表达隔离）
EMOTIVE_MARKERS = [
    r"\b(?:amazing|incredible|terrible|awful|fantastic|horrible)\b",
    r"\b(?:urgent|critical|extremely|absolutely|literally)\b",
    r"\b(?:unfortunately|fortunately|surprisingly|shockingly)\b",
    r"!{2,}",  # 连续感叹号
]

# 角色标签检测
ROLE_TAGS = ["system", "user", "assistant", "think", "tool"]


def detect_reasoning_style(text: str) -> dict:
    """检测文本中的推理风格特征（CoTness代理）。

    返回:
      {score: 0-1, patterns_matched: [模式名]}
    """
    if not text:
        return {"score": 0.0, "patterns_matched": []}

    matches = []
    for pattern in REASONING_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            matches.append(pattern)

    score = min(1.0, len(matches) / 5.0)  # 5个以上模式即1.0
    return {"score": round(score, 2), "patterns_matched": len(matches)}


def detect_emotive_content(text: str) -> dict:
    """检测文本中的情绪/修辞内容。"""
    if not text:
        return {"score": 0.0, "matches": []}

    matches = []
    for pattern in EMOTIVE_MARKERS:
        found = re.findall(pattern, text, re.IGNORECASE)
        if found:
            matches.extend(found[:5])

    score = min(1.0, len(matches) / 3.0)
    return {"score": round(score, 2), "matches_found": len(matches)}


def strip_emotive(text: str) -> str:
    """剥离情绪/修辞标记，只保留事实内容。

    用于工具数据在注入LLM前的预处理。
    """
    if not text:
        return text

    result = text
    for pattern in EMOTIVE_MARKERS:
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    # 清理多余空格和标点
    result = re.sub(r"\s{2,}", " ", result)
    result = re.sub(r"!{2,}", ".", result)
    result = re.sub(r"\?{2,}", "?", result)
    return result.strip()


def sanitize_tool_output(text: str, source: str = "") -> dict:
    """完整的工具输出净化管线。

    输入原始工具数据 → 检测 + 剥离 + 标注

    Returns:
      {original_length, stripped_text, reasoning_score, emotive_score, role_tags_detected}
    """
    if not text:
        return {"original_length": 0, "stripped_text": "", "alerts": []}

    alerts = []

    # 检测推理风格
    reasoning = detect_reasoning_style(text)
    if reasoning["score"] > 0.6:
        alerts.append(f"CoT风格检测(score={reasoning['score']})")

    # 检测情绪
    emotive = detect_emotive_content(text)
    if emotive["score"] > 0.5:
        alerts.append(f"情绪内容检测(score={emotive['score']})")

    # 检测角色标签误用
    found_tags = [t for t in ROLE_TAGS if re.search(
        rf"(?:<|【){t}(?:>|】)", text, re.IGNORECASE)]
    if found_tags:
        alerts.append(f"角色标签误用: {found_tags}")

    # 剥离情绪
    stripped = strip_emotive(text)

    return {
        "original_length": len(text),
        "stripped_length": len(stripped),
        "stripped_text": stripped,
        "reasoning_score": reasoning["score"],
        "emotive_score": emotive["score"],
        "role_tags_detected": found_tags,
        "alerts": alerts,
        "alert_count": len(alerts),
    }


def register(kernel):
    """注册sanitizer syscall。"""
    kernel.register("sanitizer.detect", lambda pid, text, **kw: {
        "reasoning": detect_reasoning_style(text),
        "emotive": detect_emotive_content(text),
    })
    kernel.register("sanitizer.sanitize", lambda pid, text, source="": 
                    sanitize_tool_output(text, source))
    logger.info("✅ sanitizer syscall已注册")

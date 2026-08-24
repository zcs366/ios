#!/usr/bin/env python3
"""
IO-S card syscall — 记忆系统调用（jikA薄wrap层）

薄桥原则：card.py不做认知/业务逻辑。只做接口转换：
  IO-S syscall签名 → jikA API → IO-S返回格式

底层存储格式不变 — jikA的RECALL+cards+index三层结构。
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

_ts = lambda: datetime.now(timezone.utc).isoformat()

# jikA路径注入
_JIAK_HOME = Path.home() / ".hermes" / "jiak"
if str(_JIAK_HOME) not in sys.path:
    sys.path.insert(0, str(_JIAK_HOME))

# batch模式——暂存批建卡片，最后一次性rebuild index
_batch_pending: list[dict] = []


def card_create(card_id: str, title: str = "", keywords: list = None,
                summary: str = "", batch: bool = False,
                caller_pid: str = None) -> dict:
    """创建新卡片。

    Args:
        batch: True→跳过逐卡index sync，由batch_commit()最后统一rebuild

    Returns: {"ok": True, "card_id": str}
    """
    from jiak_api import write_card

    card = {
        "card_id": card_id,
        "title": title or card_id,
        "status": "active",
        "created": _ts(),
        "updated": _ts(),
        "keywords": keywords or [],
        "summary": summary,
        "notes": [],
        "decisions": [],
        "questions": [],
        "overflow": [],
        "last_injected": None,
        "frequency": 0,
    }
    write_card(card_id, card)

    if batch:
        # 直接写JSON，跳过jikA的write_card（它自带sync）
        card_path = Path.home() / ".hermes" / "jiak" / "cards" / f"{card_id}.json"
        card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2))
        _batch_pending.append(card)
    else:
        from jiak_api import _sync_index_card
        _sync_index_card(card_id, card)

    return {"ok": True, "card_id": card_id}


def batch_commit(caller_pid: str = None) -> dict:
    """提交批建卡片——一次性rebuild index。"""
    if not _batch_pending:
        return {"ok": True, "committed": 0}
    from jiak_api import load_index, save_index
    index = load_index()
    cards = index.setdefault("cards", {})
    for card in _batch_pending:
        cid = card["card_id"]
        cards[cid] = {
            "title": card.get("title", ""),
            "keywords": card.get("keywords", []),
            "summary": card.get("summary", ""),
            "status": card.get("status", "active"),
            "updated": card.get("updated", ""),
            "size": f"{len(json.dumps(card))}B",
            "size_limit": card.get("size_limit", 20480),
            "overflow": card.get("overflow", []),
            "frequency": card.get("frequency", 0),
        }
    index["generated"] = _ts()
    index["total_active"] = len([c for c in cards.values() if c.get("status") == "active"])
    save_index(index)
    n = len(_batch_pending)
    _batch_pending.clear()
    return {"ok": True, "committed": n}


def card_read(card_id: str, caller_pid: str = None) -> dict | None:
    """读取卡片。对应jiak_api.read_card()。

    Returns: card dict or None
    """
    from jiak_api import read_card
    return read_card(card_id)


def card_append(card_id: str, field: str, item, caller_pid: str = None) -> dict:
    """追加元素到卡片字段（notes/decisions/questions）。

    不可变追加原则：只追加不覆写。
    对应jiak_api.append_card_field()。

    Returns: {"ok": True/False, "card_id": str, "field": str}
    """
    from jiak_api import append_card_field
    ok = append_card_field(card_id, field, item)
    return {"ok": ok, "card_id": card_id, "field": field}


def card_list(status_filter: str = None, caller_pid: str = None) -> list[dict]:
    """列出所有卡片。对应jiak_api.load_index()。

    Returns: [{"card_id", "title", "keywords", "summary", "status", "size"}, ...]
    """
    from jiak_api import load_index
    index = load_index()
    cards = index.get("cards", {})
    result = []
    for cid, meta in cards.items():
        if status_filter and meta.get("status") != status_filter:
            continue
        result.append({
            "card_id": cid,
            "title": meta.get("title", ""),
            "keywords": meta.get("keywords", []),
            "summary": meta.get("summary", ""),
            "status": meta.get("status", "active"),
            "size": meta.get("size", "?"),
        })
    return result


def card_archive(card_id: str, caller_pid: str = None) -> dict:
    """归档卡片。将status改为archived。
    对应jiak_api: read→修改status→write。

    Returns: {"ok": True/False, "card_id": str}
    """
    from jiak_api import read_card, write_card, _sync_index_card
    card = read_card(card_id)
    if card is None:
        return {"ok": False, "card_id": card_id, "error": "not_found"}
    card["status"] = "archived"
    card["updated"] = _ts()
    write_card(card_id, card)
    _sync_index_card(card_id, card)
    return {"ok": True, "card_id": card_id}


def card_split(card_id: str, at_keys: str, caller_pid: str = None) -> dict:
    """分卡 — 超限卡片拆分为主卡+overflow卡。

    新实现（PAL声称"已有"被鲁班审计推翻——jikA无split）。

    策略：复制卡片 → 修改card_id → 标记overflow → 写新卡 → 主卡追加overflow引用。

    Returns: {"ok": True/False, "card_id": str, "split_card_id": str}
    """
    from jiak_api import read_card, write_card, append_card_field, _sync_index_card

    card = read_card(card_id)
    if card is None:
        return {"ok": False, "card_id": card_id, "error": "not_found"}

    split_id = f"{card_id}-split-{int(datetime.now().timestamp())}"

    # 复制卡片给分卡
    split_card = {
        "card_id": split_id,
        "title": f"{card.get('title', card_id)} [分卡: {at_keys}]",
        "status": "active",
        "created": _ts(),
        "updated": _ts(),
        "keywords": card.get("keywords", []),
        "summary": f"分卡自 {card_id}（按 {at_keys} 分割）",
        "notes": [],
        "decisions": [],
        "questions": [],
        "overflow": [],
    }
    write_card(split_id, split_card)
    _sync_index_card(split_id, split_card)

    # 主卡标记overflow
    append_card_field(card_id, "overflow", split_id)

    return {"ok": True, "card_id": card_id, "split_card_id": split_id}

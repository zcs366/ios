#!/usr/bin/env python3
"""
IO-S monitor/sensors — IAH健康传感器（即插即用框架）

MVP三指标：
  sensor_card_health()  — 卡片数/冷卡率/大卡率
  sensor_d0_signal()    — IAH D₀扫描 (IAH不可用→返回基线占位)
  sensor_semantic_ratio() — semantic head占比 (IAH不可用→返回fallback)

IAH就位时自动接入，不可用时静默降级。
"""

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

JIAK_DIR = Path.home() / ".hermes" / "jiak"
CARDS_DIR = JIAK_DIR / "cards"
INDEX_PATH = JIAK_DIR / "index.json"

_ts = lambda: datetime.now(timezone.utc).isoformat()


def sensor_card_health() -> dict:
    """卡片健康指标 — 纯文件系统扫描，零LLM。

    Returns: {card_count, cold_card_count, cold_rate, overhead_count, large_cards}
    """
    if not CARDS_DIR.exists():
        return {"card_count": 0, "cold_card_count": 0, "cold_rate": 0,
                "overhead_count": 0, "large_cards": []}

    card_files = list(CARDS_DIR.glob("*.json"))
    cold_count = 0
    large_cards = []

    # 冷卡：7天未更新
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    for cf in card_files:
        try:
            card = json.loads(cf.read_text())
            updated = card.get("updated", "")
            size = cf.stat().st_size

            if size > 20480:  # 20KB基准
                large_cards.append({"card_id": card.get("card_id", cf.stem),
                                    "size_kb": round(size/1024, 1)})

            if updated:
                try:
                    dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt < cutoff:
                        cold_count += 1
                except (ValueError, TypeError):
                    # 解析失败不计入冷卡
                    pass
        except (json.JSONDecodeError, OSError):
            continue

    return {
        "card_count": len(card_files),
        "cold_card_count": cold_count,
        "cold_rate": round(cold_count / len(card_files), 2) if card_files else 0,
        "overhead_count": len(large_cards),
        "large_cards": large_cards[:5],
    }


def sensor_d0_signal() -> dict:
    """D₀信号 — IAH D₀扫描的薄wrap。

    IAH不可用时返回基线占位（D₀=2.0±0.5，GPT-2估计值）。
    这是PAL设计的——IO-S不等待完美，先有框架再接入。
    """
    # 尝试接入IAH
    try:
        import sys
        _iah_paths = [
            str(Path.home() / "projects" / "iah"),
            str(Path.home() / "iah"),
        ]
        for p in _iah_paths:
            if Path(p).exists() and p not in sys.path:
                sys.path.insert(0, p)

        from iah.core import scan_d0
        result = scan_d0(model_name="gpt2", max_samples=64)
        return {
            "d0_mean": round(result.get("d0_mean", 2.0), 2),
            "d0_std": round(result.get("d0_std", 0.5), 2),
            "model": result.get("model", "gpt2"),
            "source": "iah",
            "timestamp": _ts(),
        }
    except (ImportError, ModuleNotFoundError, AttributeError):
        # 静默降级：返回占位值
        return {
            "d0_mean": 2.0,
            "d0_std": 0.5,
            "model": "gpt2",
            "source": "fallback",
            "note": "IAH未就绪 — 使用预估基线",
            "timestamp": _ts(),
        }


def sensor_semantic_ratio() -> dict:
    """语义头占比 — IAH head分类的薄wrap。

    IAH不可用时返回fallback占位。
    """
    try:
        import sys
        _iah_paths = [
            str(Path.home() / "projects" / "iah"),
            str(Path.home() / "iah"),
        ]
        for p in _iah_paths:
            if Path(p).exists() and p not in sys.path:
                sys.path.insert(0, p)

        from iah.core import classify_heads
        result = classify_heads(model_name="gpt2")
        total = result.get("total_heads", 144)
        semantic = result.get("semantic_heads", 0)
        return {
            "total_heads": total,
            "semantic_heads": semantic,
            "semantic_ratio": round(semantic / total, 3) if total else 0,
            "model": "gpt2",
            "source": "iah",
            "timestamp": _ts(),
        }
    except (ImportError, ModuleNotFoundError, AttributeError):
        return {
            "total_heads": 144,
            "semantic_heads": 45,  # GPT-2估计: ~31%
            "semantic_ratio": 0.31,
            "model": "gpt2",
            "source": "fallback",
            "note": "IAH未就绪",
            "timestamp": _ts(),
        }


def collect_all() -> dict:
    """收集所有传感器读数。

    Returns: {card_health, d0_signal, semantic_ratio, collected_at}
    """
    return {
        "card_health": sensor_card_health(),
        "d0_signal": sensor_d0_signal(),
        "semantic_ratio": sensor_semantic_ratio(),
        "collected_at": _ts(),
    }

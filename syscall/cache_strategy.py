"""cache_strategy.py — KV缓存复用策略

来自Kamera (arXiv 2606.23581):
  - 位置不变KV缓存复用（Relocate+低秩补丁）
  - 45×加速比，秩-16补丁恢复精度

在IO-S层实现策略决策层:
  - 缓存复用注册
  - 命中率统计
  - 复用/重计算决策
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("io-s.cache-strategy")

CACHE_DIR = Path.home() / ".io-s" / "cache_stats"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Kamera参数
KAMERA_RANK = 16        # 低秩补丁秩
KAMERA_SPEEDUP = 45     # 典型加速比


@dataclass
class CacheEntry:
    """一次缓存复用记录。"""
    entry_id: str
    key: str                      # 缓存标识
    hit: bool                     # 是否命中
    source: str                   # "kamera"|"simple"|"recompute"
    token_saved: int = 0
    accuracy: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "key": self.key[:60],
            "hit": self.hit,
            "source": self.source,
            "token_saved": self.token_saved,
            "accuracy": self.accuracy,
        }


class CacheStrategy:
    """缓存复用策略管理器。

    记录缓存的命中/未命中，统计复用收益。
    策略决策: 场景得分>阈值→复用; 否则→重计算。
    """

    def __init__(self, reuse_threshold: float = 0.6):
        self.threshold = reuse_threshold
        self._entries: list[CacheEntry] = []
        self._load()

    def _load(self):
        path = CACHE_DIR / "cache_entries.jsonl"
        if path.exists():
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data = json.loads(line)
                            self._entries.append(CacheEntry(**data))
                        except (json.JSONDecodeError, TypeError):
                            continue

    def _append(self, entry: CacheEntry):
        path = CACHE_DIR / "cache_entries.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        self._entries.append(entry)

    def record(self, entry_id: str, key: str, hit: bool,
               source: str = "simple", token_saved: int = 0,
               accuracy: float = 1.0) -> CacheEntry:
        entry = CacheEntry(entry_id, key, hit, source, token_saved, accuracy)
        self._append(entry)
        return entry

    def should_reuse(self, key: str, similarity: float) -> bool:
        """决定是否复用缓存。

        基于相似度+历史命中率的综合决策。
        """
        # 历史命中率
        related = [e for e in self._entries if e.key.split(":")[0] == key.split(":")[0]]
        if related:
            hit_rate = sum(1 for e in related if e.hit) / len(related)
        else:
            hit_rate = 0.5

        # 综合得分
        score = 0.6 * similarity + 0.4 * hit_rate
        return score >= self.threshold

    def get_hit_rate(self) -> float:
        """全局命中率。"""
        if not self._entries:
            return 0.0
        hits = sum(1 for e in self._entries if e.hit)
        return round(hits / len(self._entries), 3)

    def get_total_tokens_saved(self) -> int:
        return sum(e.token_saved for e in self._entries)

    def get_kamera_estimate(self, context_length: int) -> dict:
        """Kamera风格的加速估计。"""
        return {
            "context_length": context_length,
            "estimated_speedup": KAMERA_SPEEDUP,
            "patch_rank": KAMERA_RANK,
            "estimated_saved_tokens": context_length * (1 - 1/KAMERA_SPEEDUP),
        }

    def summary(self) -> dict:
        return {
            "total_entries": len(self._entries),
            "hit_rate": self.get_hit_rate(),
            "total_tokens_saved": self.get_total_tokens_saved(),
            "kamera_speedup": KAMERA_SPEEDUP,
            "reuse_threshold": self.threshold,
        }


def register(kernel):
    strat = CacheStrategy()

    def handle_record(pid, entry_id, key, hit, **kw):
        return strat.record(entry_id, key, hit, **kw).to_dict()

    def handle_should_reuse(pid, key, similarity):
        return {"should_reuse": strat.should_reuse(key, similarity),
                "threshold": strat.threshold}

    def handle_summary(pid):
        return strat.summary()

    kernel.register("cache.record", handle_record)
    kernel.register("cache.should_reuse", handle_should_reuse)
    kernel.register("cache.summary", handle_summary)
    logger.info("✅ KV缓存复用策略已注册")

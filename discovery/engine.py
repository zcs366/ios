#!/usr/bin/env python3
"""
IO-S 认知发现引擎 (v0.2)

双通道关联发现：
1. 关键词重叠通道（快）— BM25级，≥2共享关键词
2. 语义余弦通道（慢）— embedding级，cos≥0.7

输出：discoveries写入 ~/.io-s/dreams.jsonl + 可选RECALL
"""
import json
import time
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

JIAK_CARDS = Path.home() / ".hermes" / "jiak" / "cards"
DREAM_LOG = Path.home() / ".io-s" / "dreams.jsonl"

# 跳过规则：archive卡、sentinel审计卡、过小的卡
_SKIP_PATTERNS = re.compile(r"\.(archive-\d+|part\d+)$")
_SENTINEL_PREFIXES = ("sentinel-", "context-audit-")


def _should_skip(cid: str, data: dict) -> bool:
    """判断是否应该跳过这张卡（噪声过滤）。"""
    if _SKIP_PATTERNS.search(cid):
        return True
    if any(cid.startswith(p) for p in _SENTINEL_PREFIXES):
        return True
    # 跳过无标题的卡（全是元数据卡）
    title = (data.get("title") or "").strip()
    if not title:
        return True
    return False


# ── 嵌入器（懒加载）──
_embedder = None

def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer("BAAI/bge-small-zh-v1.5")
    return _embedder


# ── 卡片读取 ──

def load_all_cards() -> list[dict]:
    """加载所有活跃卡片，返回 [{id, title, keywords, summary, text}]"""
    cards = []
    for f in sorted(JIAK_CARDS.glob("*.json")):
        if f.name.startswith("."):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        cid = f.stem
        if _should_skip(cid, data):
            continue

        title = data.get("title", "") or ""
        keywords = data.get("keywords", []) or []
        summary = data.get("summary", "") or ""

        # 取最近的notes作为辅助文本
        notes = data.get("notes", []) or []
        recent_notes = ""
        for n in notes[-3:]:
            if isinstance(n, dict):
                recent_notes += (n.get("content", "") or "") + " "
            elif isinstance(n, list) and len(n) >= 2:
                recent_notes += str(n[1]) + " "

        cards.append({
            "id": cid,
            "title": title,
            "keywords": keywords,
            "summary": summary,
            "text": f"{title}。{summary}。{' '.join(keywords)}。{recent_notes}",
        })
    return cards


# ── 双通道发现 ──

def keyword_overlap(cards: list[dict], min_overlap: int = 2) -> list[dict]:
    """通道1：关键词重叠发现"""
    discoveries = []
    seen = set()
    for i, a in enumerate(cards):
        ka = set(k.lower() for k in a["keywords"])
        for j, b in enumerate(cards):
            if j <= i:
                continue
            kb = set(k.lower() for k in b["keywords"])
            overlap = ka & kb
            if len(overlap) >= min_overlap:
                pair_key = f"{a['id']}|{b['id']}"
                if pair_key not in seen:
                    seen.add(pair_key)
                    discoveries.append({
                        "type": "discovery",
                        "channel": "keyword",
                        "card_a": a["id"],
                        "card_b": b["id"],
                        "title_a": a["title"],
                        "title_b": b["title"],
                        "overlap_words": sorted(overlap),
                        "overlap_count": len(overlap),
                        "weight": round(len(overlap) / max(len(ka), len(kb)), 3),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
    return discoveries


def semantic_similarity(cards: list[dict], threshold: float = 0.70) -> list[dict]:
    """通道2：语义余弦相似度发现"""
    if len(cards) < 2:
        return []

    emb = _get_embedder()
    texts = [c["text"][:768] for c in cards]  # 截断768字符
    vectors = emb.encode(texts, show_progress_bar=False, normalize_embeddings=True)

    discoveries = []
    seen = set()
    for i in range(len(cards)):
        for j in range(i + 1, len(cards)):
            cos = float(vectors[i] @ vectors[j])
            if cos >= threshold:
                ka = set(k.lower() for k in cards[i]["keywords"])
                kb = set(k.lower() for k in cards[j]["keywords"])
                kw_overlap = len(ka & kb)

                pair_key = f"{cards[i]['id']}|{cards[j]['id']}"
                if pair_key not in seen:
                    seen.add(pair_key)
                    discoveries.append({
                        "type": "discovery",
                        "channel": "semantic",
                        "card_a": cards[i]["id"],
                        "card_b": cards[j]["id"],
                        "title_a": cards[i]["title"],
                        "title_b": cards[j]["title"],
                        "cosine": round(cos, 4),
                        "keyword_overlap": kw_overlap,
                        "amplification": round(cos / max(kw_overlap, 0.1), 3),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
    return discoveries


# ── 聚合输出 ──

def discover_all(min_overlap: int = 2,
                 semantic_threshold: float = 0.70,
                 dedup_by_pair: bool = True) -> dict:
    """运行双通道发现，聚合输出。"""
    cards = load_all_cards()
    t0 = time.time()

    kw = keyword_overlap(cards, min_overlap=min_overlap)
    sem = semantic_similarity(cards, threshold=semantic_threshold)

    elapsed = round(time.time() - t0, 3)

    return {
        "ok": True,
        "keyword_finds": kw,
        "semantic_finds": sem,
        "total_pairs": len(kw) + len(sem),
        "cards_scanned": len(cards),
        "elapsed_s": elapsed,
    }


# ── 持久化 ──

def write_discoveries(result: dict) -> int:
    """将发现追加到梦境日志。返回写入条数。"""
    DREAM_LOG.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(DREAM_LOG, "a", encoding="utf-8") as f:
        for d in result.get("keyword_finds", []):
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
            count += 1
        for d in result.get("semantic_finds", []):
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
            count += 1
    return count


def read_discoveries(limit: int = 20, channel: Optional[str] = None) -> list[dict]:
    """读取梦境日志中的发现。"""
    if not DREAM_LOG.exists():
        return []
    discoveries = []
    for line in DREAM_LOG.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if channel and entry.get("channel") != channel:
            continue
        discoveries.append(entry)
    return discoveries[-limit:]

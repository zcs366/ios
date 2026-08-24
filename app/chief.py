#!/usr/bin/env python3
"""首席情报Agent v0.2 — 轻量执行路径

规避card_append在大卡上的全量读写瓶颈 → 改用card_create单次写入。
完整四syscall链路：scan→dream→create_report→broadcast
"""

import sys, json, time
from pathlib import Path
from datetime import datetime, timezone, timedelta

_io_s = Path.home() / "io-s"
_isa = Path.home() / "projects" / "isa"
sys.path.insert(0, str(_io_s))
sys.path.insert(0, str(_isa))

from kernel import get_kernel
from syscall import register_all

def chief_intel():
    k = get_kernel()
    register_all(k)
    ts = datetime.now(timezone.utc)

    # ── Phase 1: 扫描 ──
    cards_dir = Path.home() / ".hermes" / "jiak" / "cards"
    cutoff = ts - timedelta(hours=72)
    recent = []
    for cf in sorted(cards_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        mtime = datetime.fromtimestamp(cf.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff: break
        try:
            card = json.loads(cf.read_text())
            recent.append({"id": card["card_id"], "title": card.get("title","")[:50],
                          "notes": len(card.get("notes",[])), "kw": card.get("keywords",[])[:5]})
        except: pass
        if len(recent) >= 15: break

    # ── Phase 2: Dream（纯关键词回退 — HF不可达）──
    discoveries = []
    for i in range(len(recent)):
        for j in range(i+1, len(recent)):
            ki, kj = set(recent[i].get("kw",[])), set(recent[j].get("kw",[]))
            overlap = ki & kj
            if len(overlap) >= 2:
                discoveries.append({"card_a": recent[i]["id"], "card_b": recent[j]["id"],
                                   "shared_keywords": list(overlap), "source": "keyword"})

    # ── Phase 3: 写报告卡（单次card_create，不append）──
    brief_id = f"intel-{ts.strftime('%Y%m%d-%H%M')}"
    report_text = f"""IO-S 情报快报 — {ts.strftime('%Y-%m-%d %H:%M')} UTC

## 活跃卡片 ({len(recent)}张)
{chr(10).join(f"- {r['id']}: {r['title']} ({r['notes']}条笔记)" for r in recent)}

## Dreaming发现 ({len(discoveries)}组关联)
{chr(10).join(_fmt_discovery(d) for d in discoveries[:10])}
"""
    k.dispatch("card_create", brief_id, f"IO-S情报快报 {ts.strftime('%m%d')}",
               ["情报", "intel", "auto"], report_text[:500], batch=True)
    k.dispatch("batch_commit")

    # ── Phase 4: 广播 ──
    k.dispatch("signal_broadcast", {"event": "intel_ready", "brief_id": brief_id,
               "cards": len(recent), "discoveries": len(discoveries)})

    print(f"  ✅ 情报快报: {brief_id}")
    print(f"  📇 {len(recent)}张活跃卡 | ☀️ {len(discoveries)}组关联 | 📨 广播完成")
    print(f"  🔗 全链路: scan→dream→card_create→signal_broadcast ✅")
    return brief_id

def _fmt_discovery(d):
    if "cosine_similarity" in d:
        return f"- 🔗 {d['card_a']}↔{d['card_b']} (语义:{d['cosine_similarity']:.2f})"
    kw = ",".join(d.get("shared_keywords",[]))[:30]
    return f"- 📎 {d['card_a']}↔{d['card_b']} ({kw})"

if __name__ == "__main__":
    chief_intel()

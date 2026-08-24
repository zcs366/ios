"""IO-S dream CLI — 认知发现命令"""
import sys
import json
from pathlib import Path

RECALL_PATH = Path.home() / ".hermes" / "jiak" / "RECALL.jsonl"
RECALL_APPEND = Path.home() / ".hermes" / "jiak" / "scripts" / "recall_append.py"


def cmd_dream(args: list[str]):
    """io-s dream [run|report|watch|help]"""
    if not args or args[0] in ("help", "-h", "--help"):
        _print_help()
        return

    sub = args[0]

    if sub == "run":
        _cmd_run()
    elif sub == "report":
        _cmd_report()
    elif sub == "watch":
        _cmd_watch()
    else:
        print(f"未知子命令: {sub}")
        _print_help()


def _print_help():
    print("""
用法: io-s dream <子命令>

子命令:
  run       手动运行认知发现（双通道扫描）
  report    查看最近发现
  watch     启动后台监视模式（卡片变化自动触发）
  help      显示本帮助
""")


def _cmd_run():
    """io-s dream run"""
    from discovery.engine import discover_all, write_discoveries

    print("🔍 IO-S 认知发现引擎 v0.2")
    print("  双通道：关键词重叠(≥2) + 语义余弦(≥0.7)")
    print()

    result = discover_all()

    print(f"📇 扫描 {result['cards_scanned']} 张卡片，用时 {result['elapsed_s']}s")
    print(f"🔗 找到 {result['total_pairs']} 对关联")
    print()

    kw = result["keyword_finds"]
    sem = result["semantic_finds"]

    if kw:
        print(f"--- 关键词重叠通道 ({len(kw)} 对) ---")
        for d in kw[:10]:
            print(f"  [{d['overlap_count']}kw] {d['title_a']}")
            print(f"         ↔ {d['title_b']}")
            print(f"         重叠词: {', '.join(d['overlap_words'][:5])}")
            print()

    if sem:
        zero_kw = [d for d in sem if d["keyword_overlap"] == 0]
        print(f"--- 语义相似度通道 ({len(sem)} 对) ---")
        if zero_kw:
            print(f"  ★ 零关键词重叠的深层关联（IO-S核心价值）:")
            for d in zero_kw[:8]:
                print(f"  cos={d['cosine']:.3f}  {d['title_a'][:40]:40s}")
                print(f"               ↔ {d['title_b'][:40]:40s}")
                print()
        print(f"  全部语义发现:")
        for d in sem[:5]:
            print(f"  cos={d['cosine']:.3f}  kw_overlap={d['keyword_overlap']}  "
                  f"{d['card_a'][:25]:25s} ↔ {d['card_b'][:25]:25s}")

    # 持久化到梦境日志
    n = write_discoveries(result)
    print(f"\n📝 已写入 {n} 条发现到梦境日志")

    # 将零关键词重叠的发现写入RECALL
    zero_kw_written = 0
    from datetime import datetime, timezone
    for d in zero_kw:
        line = json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "jiak_note",
            "agent": "io-s-dream",
            "card_id": f"{d['card_a']}+{d['card_b']}",
            "summary_short": f"[IO-S dream] 语义发现: {d['card_a']} ↔ {d['card_b']} (cos={d['cosine']}, 零关键词)",
        }, ensure_ascii=False)
        import subprocess
        r = subprocess.run(
            ["python3", str(RECALL_APPEND), line],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0:
            zero_kw_written += 1

    if zero_kw_written:
        print(f"  其中 {zero_kw_written} 条零关键词重叠发现已写入RECALL")

    print("\n✅ 认知发现完成")


def _cmd_report():
    """io-s dream report"""
    from discovery.engine import read_discoveries

    finds = read_discoveries(limit=30)
    if not finds:
        print("尚无发现。先运行 io-s dream run")
        return

    print(f"📋 最近 {len(finds)} 条发现:")
    print()

    for d in reversed(finds):
        if d["channel"] == "keyword":
            print(f"[kw×{d.get('overlap_count','?')}] "
                  f"{d.get('card_a','?')} ↔ {d.get('card_b','?')}")
        elif d["channel"] == "semantic":
            marker = "★" if d.get("keyword_overlap", 0) == 0 else " "
            print(f"{marker}[cos={d.get('cosine','?'):.3f}] "
                  f"{d.get('card_a','?')} ↔ {d.get('card_b','?')}")


def _cmd_watch():
    """io-s dream watch — 后台监控模式"""
    import time
    import hashlib
    from discovery.engine import load_all_cards, discover_all, write_discoveries

    print("👁️  IO-S 梦境监视器已启动")
    print("   监控 ~/.hermes/jiak/cards/*.json 变化")
    print("   卡片变化 → 自动触发认知发现 → 写入RECALL")
    print("   Ctrl+C 停止")
    print()

    # 初始快照
    snap = {c["id"]: hashlib.md5(c["text"].encode()).hexdigest()
            for c in load_all_cards()}

    try:
        while True:
            time.sleep(60)  # 每分钟检查

            current = {c["id"]: hashlib.md5(c["text"].encode()).hexdigest()
                       for c in load_all_cards()}

            changed = [cid for cid, h in current.items()
                       if snap.get(cid) != h and cid in snap]
            new_cards = [cid for cid in current if cid not in snap]

            if changed or new_cards:
                print(f"🔄 检测到变化: {len(changed)} 变 + {len(new_cards)} 新")
                result = discover_all()
                n = write_discoveries(result)

                # 零关键词重叠的存入RECALL
                from datetime import datetime, timezone
                import subprocess
                for d in result.get("semantic_finds", []):
                    if d.get("keyword_overlap", 0) == 0:
                        line = json.dumps({
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "type": "jiak_note",
                            "agent": "io-s-dream-watch",
                            "card_id": f"{d['card_a']}+{d['card_b']}",
                            "summary_short": f"[IO-S dream/auto] 语义发现: {d['card_a']} ↔ {d['card_b']} (cos={d['cosine']})",
                        }, ensure_ascii=False)
                        subprocess.run(
                            ["python3", str(RECALL_APPEND), line],
                            capture_output=True, text=True, timeout=30
                        )

                print(f"  → 发现 {result['total_pairs']} 对关联，写入 {n} 条")
                snap = current

    except KeyboardInterrupt:
        print("\n⏹ 梦境监视器已停止")

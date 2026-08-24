#!/usr/bin/env python3
"""
IO-S CLI v0.1 — 智能体操作系统的命令行界面

命令：
  io-s status          全系统状态
  io-s card list       列出所有卡片
  io-s card show <id>  显示单卡内容
  io-s card append <id> <field> <content>  追加笔记/决策
  io-s agent ps        列出Agent
  io-s agent spawn <goal>  创建Agent配置
  io-s dream           启动Dreaming引擎
  io-s dream report    查看最新发现
  io-s syscalls        列出所有系统调用
"""

import json
import sys
import textwrap
from pathlib import Path

# 注入路径
_io_s_dir = str(Path.home() / "io-s")
if _io_s_dir not in sys.path:
    sys.path.insert(0, _io_s_dir)

from kernel import Kernel, get_kernel
from syscall import register_all
from monitor.sensors import collect_all as monitor_collect
from monitor.baseline import update_baseline, report_baseline, init_baseline


def main():
    # 初始化内核 — 注册所有syscall
    kernel = get_kernel()
    register_all(kernel)
    kernel.write_status()  # IO-S就绪信号 → ~/.io-s/kernel.json

    if len(sys.argv) < 2:
        print_status(kernel)
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "status":
        print_status(kernel)
    elif cmd == "syscalls":
        print_syscalls(kernel)
    elif cmd == "card":
        if not args:
            cmd_card_list(kernel)
        elif args[0] == "list":
            cmd_card_list(kernel)
        elif args[0] == "show" and len(args) >= 2:
            cmd_card_show(kernel, args[1])
        elif args[0] == "append" and len(args) >= 4:
            cmd_card_append(kernel, args[1], args[2], " ".join(args[3:]))
        elif args[0] == "split" and len(args) >= 3:
            cmd_card_split(kernel, args[1], args[2])
        elif args[0] == "split" and len(args) == 2:
            cmd_card_split(kernel, args[1], "keyword")
        else:
            print("用法: io-s card [list|show <id>|append <id> <field> <content>|split <id> [key]]")
    elif cmd == "agent":
        if not args:
            cmd_agent_list(kernel)
        elif args[0] == "ps":
            cmd_agent_list(kernel)
        elif args[0] == "spawn" and len(args) >= 2:
            cmd_agent_spawn(kernel, " ".join(args[1:]))
        else:
            print("用法: io-s agent [ps|spawn <goal>]")
    elif cmd == "dream":
        from cli.cmd_dream import cmd_dream
        cmd_dream(args)
    elif cmd == "monitor":
        cmd_monitor(kernel)
    elif cmd == "intel":
        cmd_intel(kernel)
    elif cmd == "heartbeat":
        print(json.dumps(kernel.heartbeat(), ensure_ascii=False, indent=2))
    else:
        print(f"未知命令: {cmd}")
        print("可用: status, syscalls, card, agent, dream, heartbeat")


# ── 子命令实现 ──

def print_status(kernel: Kernel):
    """io-s status — 全系统状态"""
    s = kernel.status()
    cards = kernel.dispatch("card_list") if "card_list" in kernel._syscall_table else []
    agents = kernel.dispatch("agent_list") if "agent_list" in kernel._syscall_table else []

    # D₀ baseline
    try:
        monitor = monitor_collect()
        d0 = monitor.get("d0_signal", {})
        init_baseline(d0)
        update_baseline(d0)
        d0_line = f"D₀: {d0.get('d0_mean', '?')}±{d0.get('d0_std', '?')} ({d0.get('source','?')})"
    except Exception:
        d0_line = "D₀: 待初始化"

    header = "IO-S v0.1 — 智能体操作系统"
    print(f"\n  ╔{'═'*50}╗")
    print(f"  ║  {header:<48}║")
    print(f"  ║  启动: {s['uptime_s']:.0f}s前 | {d0_line:<30}║")
    print(f"  ╚{'═'*50}╝\n")

    print(f"  📇 卡片: {len(cards)} 张")
    if cards:
        for c in cards[:5]:
            print(f"     {c['card_id']:<30} | {c.get('title','')[:30]}")
        if len(cards) > 5:
            print(f"     ... 还有 {len(cards)-5} 张")

    print(f"\n  🤖 Agent: {len(agents)} 个运行中")
    if agents:
        for a in agents:
            print(f"     {a['agent_id']:<25} | {a.get('status','?'):<12} | {a.get('goal','')[:40]}")

    print(f"\n  📊 内核: {s['registered_syscalls']} 个已注册系统调用")
    print(f"  📨 信号等待: {s['signals_pending']}\n")


def print_syscalls(kernel: Kernel):
    """io-s syscalls — 系统调用列表"""
    print(f"\n  IO-S 系统调用 ({len(kernel._syscall_table)} 个):\n")
    for name in kernel.list_syscalls():
        print(f"    {name}")
    print()


def cmd_card_list(kernel: Kernel):
    """io-s card list"""
    cards = kernel.dispatch("card_list")
    if not cards:
        print("  暂无卡片。")
        return
    print(f"\n  📇 IO-S 卡片 ({len(cards)} 张):\n")
    for c in cards:
        status_icon = "📦" if c.get("status") == "archived" else "📋"
        print(f"  {status_icon} {c['card_id']:<35} {c.get('title','')[:40]}")
    print()


def cmd_card_show(kernel: Kernel, card_id: str):
    """io-s card show <id>"""
    card = kernel.dispatch("card_read", card_id)
    if card is None:
        print(f"  ❌ 卡片不存在: {card_id}")
        return

    print(f"\n  📋 {card_id}")
    print(f"  {'─'*60}")
    print(f"  标题: {card.get('title', '')}")
    print(f"  状态: {card.get('status', '?')}")
    print(f"  摘要: {card.get('summary', '')}")
    print(f"  关键词: {', '.join(card.get('keywords', []))}")
    print(f"  笔记: {len(card.get('notes', []))} 条")
    for n in card.get("notes", [])[-3:]:
        content = n if isinstance(n, str) else n.get("content", str(n))
        print(f"    └ {content[:100]}")
    print(f"  决策: {len(card.get('decisions', []))} 条")
    for d in card.get("decisions", [])[-3:]:
        content = d if isinstance(d, str) else d.get("content", str(d))
        print(f"    └ {content[:100]}")
    print()


def cmd_card_append(kernel: Kernel, card_id: str, field: str, content: str):
    """io-s card append <id> <field> <content>"""
    result = kernel.dispatch("card_append", card_id, field, content)
    if result["ok"]:
        print(f"  ✅ 已追加到 {card_id}.{field}")
    else:
        print(f"  ❌ 追加失败: {card_id}")


def cmd_agent_list(kernel: Kernel):
    """io-s agent ps"""
    agents = kernel.dispatch("agent_list")
    if not agents:
        print("\n  🤖 没有活跃Agent。用 'io-s agent spawn <目标>' 创建一个。\n")
        return
    print(f"\n  🤖 IO-S Agent ({len(agents)} 个):\n")
    for a in agents:
        icon = {"configured": "⚙️", "spawning": "🔄", "running": "▶️",
                "done": "✅", "killed": "⛔"}.get(a.get("status"), "❓")
        print(f"  {icon} {a['agent_id']:<25} | {a.get('status','?'):<12} | {a.get('goal','')[:50]}")
    print()


def cmd_agent_spawn(kernel: Kernel, goal: str):
    """io-s agent spawn <goal>"""
    result = kernel.dispatch("agent_spawn", goal=goal)
    if result["ok"]:
        print(f"\n  ✅ Agent已创建: {result['agent_id']}")
        print(f"     目标: {goal}")
        print(f"     下一步: 使用Hermes delegate_task启动Agent\n")
    else:
        print(f"  ❌ 创建失败")


def cmd_monitor(kernel: Kernel):
    """io-s monitor — IAH监控仪表盘"""
    print(f"\n  📊 IO-S 监控仪表盘\n  {'─'*50}")
    m = monitor_collect()
    ch = m.get("card_health", {})
    print(f"\n  📇 卡片: {ch.get('card_count',0)} | 冷卡: {ch.get('cold_card_count',0)} ({ch.get('cold_rate',0):.0%}) | 超大: {ch.get('overhead_count',0)}")
    d0 = m.get("d0_signal", {})
    init_baseline(d0); update_baseline(d0); bl = report_baseline()
    print(f"  🧠 D₀: {d0.get('d0_mean','?')}±{d0.get('d0_std','?')} | 基线: {bl.get('d0_mean','?')} ({bl.get('readings',0)}读数) | {bl.get('status','?')}")
    print(f"  🔬 semantic: {m.get('semantic_ratio',{}).get('semantic_ratio',0):.0%} ({m.get('semantic_ratio',{}).get('source','?')})")
    print()


def cmd_card_split(kernel: Kernel, card_id: str, at_keys: str):
    """io-s card split <id> [key] — 分卡"""
    result = kernel.dispatch("card_split", card_id, at_keys)
    if result.get("ok"):
        print(f"  ✅ 分卡完成: {card_id} -> {result.get('split_card_id','?')}")
    else:
        print(f"  ❌ 分卡失败: {result.get('error','?')}")


def cmd_intel(kernel: Kernel):
    """io-s intel — 首席情报Agent"""
    import sys
    _app_dir = str(Path.home() / "io-s" / "app")
    if _app_dir not in sys.path:
        sys.path.insert(0, _app_dir)
    from chief import chief_intel
    result = chief_intel()
    if result:
        print(f"\n  📋 查看报告: io-s card show {result}")


if __name__ == "__main__":
    main()

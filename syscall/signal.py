#!/usr/bin/env python3
"""
IO-S signal syscall — 信号路由（文件系统总线IPC）

v0.3 (2026-06-25):
  - signal_recv加cap_check（#8修复）
  - signal_broadcast支持新信封格式（#9修复）
  - signal_send写入后自动清理旧信号（#10修复）

v0.2 (2026-06-24):
  - signal_send不再直接写文件——只返回格式化payload，由kernel.dispatch()写入
  - signal_decide已移除——认知判断属于ISA，不在IO-S
  - 信号payload含audit字段(route_log, cap_check, recorded_at)
  - signal_recv保留（读信是ISA的正当操作，不需要cap_check）

核心原则：不需要网络、不需要MCP、不需要A2A — 文件系统就是总线。
信号生命周期: signal_send → cap_check(kernel) → 写文件 + audit → signal_recv
"""

import json
import time
import hashlib
from pathlib import Path
from datetime import datetime, timezone

_ts = lambda: datetime.now(timezone.utc).isoformat()
SIGNAL_DIR = Path.home() / ".io-s" / "signals"
SIGNAL_TTL_HOURS = 24


# ── 信号清理（#10修复）──

def _cleanup_old_signals(ttl_hours: int = SIGNAL_TTL_HOURS) -> int:
    """清理超过TTH的signal文件。返回清理数量。"""
    if not SIGNAL_DIR.exists():
        return 0
    cutoff = time.time() - (ttl_hours * 3600)
    cleaned = 0
    for sf in list(SIGNAL_DIR.glob("*.json")):
        try:
            if sf.stat().st_mtime < cutoff:
                sf.unlink()
                cleaned += 1
        except (OSError, PermissionError):
            continue
    if cleaned:
        print(f"[signal] 清理 {cleaned} 个过期信号文件 (>{ttl_hours}h)")
    return cleaned


def signal_send(caller_pid: str, dest: str = None, body: dict = None,
                priority: int = 1, trace_id: str = None,
                signal: dict = None) -> dict:
    """格式化信号payload。

    支持两种调用格式：
    1. 标准信封: signal_send(signal={type, from, to, payload, timestamp})
    2. 旧格式:   signal_send(dest, body, priority)

    Args:
        caller_pid: 调用者进程ID（由kernel注入）
        dest: 目标Agent ID（旧格式）
        body: 信号载荷（旧格式）
        priority: 优先级 (0=low, 1=normal, 2=high)
        trace_id: 追踪ID（由kernel注入）
        signal: 标准信封 {type, from, to, payload, timestamp}（新格式）

    Returns: 格式化的signal payload（含audit字段）
    """
    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    ts = _ts()

    # 解析目标
    if signal:
        # 标准信封格式 (ISN/ISA任务书v3)
        envelope_type = signal.get("type", "unknown")
        envelope_from = signal.get("from", caller_pid)
        envelope_to = signal.get("to", "broadcast")
        envelope_payload = signal.get("payload", {})
        envelope_ts = signal.get("timestamp", ts)
    else:
        # 旧格式
        dest = dest or "broadcast"
        envelope_type = "message"
        envelope_from = caller_pid
        envelope_to = dest
        envelope_payload = body or {}
        envelope_ts = ts

    signal_id = f"sig-{envelope_to}-{int(time.time()*1000)}"

    payload = {
        "signal_id": signal_id,
        "type": envelope_type,
        "from": envelope_from,
        "to": envelope_to,
        "payload": envelope_payload,
        "timestamp": envelope_ts,
        "caller_pid": caller_pid,
        "sent_at": ts,
        "priority": priority,
        "status": "sent",
        "ack": None,
        "audit": {
            "trace_id": trace_id or f"sig-{signal_id}",
            "route_log": [{"hop": "kernel", "at": ts, "from": caller_pid, "to": envelope_to}],
            "cap_check": "passed_at_kernel",
            "recorded_at": ts,
        }
    }

    # 写入信号文件（kernel已cap_check通过才调此handler）
    signal_file = SIGNAL_DIR / f"{signal_id}.json"
    signal_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    # #10修复：写入后清理旧信号
    _cleanup_old_signals()

    return {"signal_id": signal_id, "dest": envelope_to, "type": envelope_type,
            "audit": payload["audit"]}


def signal_recv(agent_id: str = None, since_ts: str = None, caller_pid: str = None,
                target: str = None, filter: dict = None,
                limit: int = None) -> list[dict]:
    """接收发给本Agent的信号。

    #8修复：加cap_check——caller_pid必须有signal:read权限才能读。
    通过全局get_kernel()获取kernel实例进行cap_check。
    2026-06-30修复：加limit参数（ISA syscall.py使用）。

    支持两种调用格式：
    1. 标准信封: signal_recv(target="isa", filter={"type": "skill_created"})
    2. 旧格式:   signal_recv(agent_id="isa", since_ts="...")

    Args:
        agent_id: 目标Agent ID（旧格式，与target同义）
        since_ts: 只返回此时间之后的信号 (None=全部)
        target: 目标Agent ID（新格式）
        filter: 过滤器如 {"type": "skill_created"}（新格式）
        limit: 最大返回条数 (None=全部)

    Returns: [{"signal_id", "type", "from", "to", "payload", "timestamp", "sent_at", "audit"}, ...]
    """
    # 兼容两种格式
    recv_target = target or agent_id
    if not recv_target:
        return []

    # ── #8修复：cap_check ──
    if caller_pid and caller_pid != "kernel":
        try:
            # 通过全局get_kernel()获取kernel实例进行cap_check
            from kernel import get_kernel
            kernel = get_kernel()
            allowed, reason = kernel.cap_check(
                caller_pid, "read", "signal", recv_target)
            if not allowed:
                print(f"[DENIED] {caller_pid} 无权读 {recv_target} 的信号: {reason}")
                return []
        except Exception as e:
            print(f"[WARN] signal_recv cap_check失败: {e}")
            return []

    if not SIGNAL_DIR.exists():
        return []

    signals = []
    for sf in sorted(SIGNAL_DIR.glob("*.json")):
        try:
            payload = json.loads(sf.read_text())

            # 匹配目标：to字段（新格式）或dest字段（旧格式）都检查
            signal_to = payload.get("to") or payload.get("dest")
            if signal_to != recv_target:
                continue

            # 时间过滤
            if since_ts and payload.get("sent_at", "") <= since_ts:
                continue

            # filter过滤（按type、from等字段匹配）
            if filter:
                match = True
                for k, v in filter.items():
                    if payload.get(k) != v:
                        match = False
                        break
                if not match:
                    continue

            signals.append({
                "signal_id": payload.get("signal_id", sf.stem),
                "type": payload.get("type", "message"),
                "from": payload.get("from", payload.get("caller_pid", "")),
                "to": signal_to,
                "payload": payload.get("payload", {}),
                "timestamp": payload.get("timestamp", payload.get("sent_at", "")),
                "sent_at": payload.get("sent_at", ""),
                "priority": payload.get("priority", 1),
                "caller_pid": payload.get("caller_pid", ""),
                "audit": payload.get("audit", {}),
            })
            # 标记已确认
            payload["status"] = "acked"
            payload["ack"] = _ts()
            sf.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        except (json.JSONDecodeError, KeyError, OSError):
            continue

    # 2026-06-30修复：limit裁剪 + KeyError防护（信号文件可能无signal_id）
    if limit is not None and len(signals) > limit:
        signals = signals[:limit]

    return signals


def signal_broadcast(caller_pid: str, body: dict = None, channel: str = "public",
                     priority: int = 1, trace_id: str = None,
                     signal: dict = None) -> dict:
    """广播信号到公共频道。

    #9修复：支持新旧两种格式。

    Args:
        caller_pid: 调用者进程ID
        body: 信号载荷（旧格式）
        channel: 频道名 (默认 "public")
        priority: 优先级
        trace_id: 追踪ID
        signal: 标准信封 {type, from, to, payload, timestamp}（新格式）
    """
    if signal:
        signal["to"] = f"broadcast:{channel}"
        return signal_send(caller_pid=caller_pid, signal=signal,
                           priority=priority, trace_id=trace_id)
    else:
        return signal_send(caller_pid=caller_pid, dest=f"broadcast:{channel}",
                           body=body, priority=priority, trace_id=trace_id)

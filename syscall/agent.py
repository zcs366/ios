#!/usr/bin/env python3
"""
IO-S agent syscall — Agent生命周期管理

v0.3 (2026-06-24):
  - 使用 ProcessTable.spawn() 自动继承cap
  - agent_spawn 传递 parent_pid 构建cap继承链
  - 支持运行时切换 Executor（Hermes/Null/SIM）
"""

import json
import time
from pathlib import Path
from datetime import datetime, timezone

from process import Process, ProcessTable, ProcessState, NullExecutor

_ts = lambda: datetime.now(timezone.utc).isoformat()
AGENT_DIR = Path.home() / ".io-s" / "agents"

_table = ProcessTable()
_executor = NullExecutor()


def set_executor(executor):
    """运行时切换执行后端。"""
    global _executor
    _executor = executor


def agent_spawn(goal: str, name: str = None,
                skills: list[str] = None,
                model: str = None,
                policy: str = "fifo",
                parent_pid: str = None,
                caller_pid: str = None) -> dict:
    """创建IO-S进程，继承cap，提交给执行器。

    Args:
        goal: Agent目标
        name: Agent名称
        skills: 使用的skill列表
        model: 模型选择
        policy: 调度策略
        parent_pid: 父进程PID（cap继承链）

    Returns: {"ok": True, "pid": str, "task_id": str, "process": {...}}
    """
    AGENT_DIR.mkdir(parents=True, exist_ok=True)

    agent_id = name or f"agent-{int(time.time())}"
    pid = f"p-{agent_id}"

    # 1. 创建进程
    process = Process(goal=goal, pid=pid, skills=skills or [], parent_pid=parent_pid)

    # 2. 注册 + 继承cap
    _table.spawn(process)

    # 3. 提交执行器
    task_id = _executor.spawn(process)

    # 4. 更新为READY
    process.transition(ProcessState.READY)
    _table.update(process)

    # 5. 旧config格式（向后兼容）
    config = process.to_dict()
    config.update({
        "model": model,
        "policy": policy,
        "task_id": task_id,
        "agent_id": agent_id,
    })
    config_file = AGENT_DIR / f"{agent_id}.json"
    config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2, default=str))

    return {"ok": True, "pid": pid, "task_id": task_id, "process": config}


def agent_kill(agent_id: str, caller_pid: str = None) -> dict:
    pid = f"p-{agent_id}" if not agent_id.startswith("p-") else agent_id
    process = _table.get(pid)
    if process:
        process.transition(ProcessState.KILLED)
        _table.update(process)
        return {"ok": True, "agent_id": agent_id, "pid": pid}

    config_file = AGENT_DIR / f"{agent_id}.json"
    if config_file.exists():
        config = json.loads(config_file.read_text())
        config["status"] = "killed"
        config["updated_at"] = _ts()
        config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2))
        return {"ok": True, "agent_id": agent_id}
    return {"ok": False, "agent_id": agent_id, "error": "not_found"}


def agent_status(agent_id: str, caller_pid: str = None) -> dict | None:
    pid = f"p-{agent_id}" if not agent_id.startswith("p-") else agent_id
    process = _table.get(pid)
    if process:
        return process.to_dict()
    config_file = AGENT_DIR / f"{agent_id}.json"
    if config_file.exists():
        return json.loads(config_file.read_text())
    return None


def agent_list(caller_pid: str = None) -> list[dict]:
    procs = _table.list()
    result = [p.to_dict() for p in procs]
    if AGENT_DIR.exists():
        existing_pids = {p["pid"] for p in result}
        for af in sorted(AGENT_DIR.glob("*.json")):
            try:
                cfg = json.loads(af.read_text())
                pid = cfg.get("pid") or f"p-{cfg.get('agent_id', '')}"
                if pid not in existing_pids:
                    result.append(cfg)
            except (json.JSONDecodeError, KeyError):
                continue
    return result

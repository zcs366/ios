#!/usr/bin/env python3
"""
IO-S Process — Agent进程抽象层

v0.3变更 (2026-06-28):
  - 新增 PLANNING 状态（PEEU规划层接入）
  - 新增 Process.plan / Process.hindsight 字段
  - 新增 complete_with_hindsight() 方法（hindsight附带回写，不走状态机）
  - 七神划界：hindsight回写只读不碰状态机，PLANNING不加硬截止由调用方决定

v0.2变更 (2026-06-24):
  - 添加 parent_pid 字段（cap继承链用）
  - 添加 cap 字段（权限列表，继承自父进程）
  - ProcessTable.spawn() 自动继承cap
  - Executor 接口 + HermesExecutor 适配器

IO-S是治理系统（非看板），必须能独立部署（非Hermes插件）。
Process是IO-S的进程抽象——不依赖任何外部执行环境。

HermesExecutor把Process映射到Hermes delegate_task。
其他底座（SIM/sandbox/直跑）只需要实现Executor接口。

Process状态机: created → planning → ready → running → done/failed
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


# ── Process状态 ──

class ProcessState:
    CREATED = "created"
    PLANNING = "planning"   # v0.3: PEEU规划层状态
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    KILLED = "killed"

    ALL_STATES = [CREATED, PLANNING, READY, RUNNING, DONE, FAILED, KILLED]

    @classmethod
    def is_terminal(cls, state: str) -> bool:
        return state in (cls.DONE, cls.FAILED, cls.KILLED)

    @classmethod
    def valid_transition(cls, from_state: str, to_state: str) -> bool:
        transitions = {
            cls.CREATED: [cls.PLANNING, cls.KILLED],       # v0.3: 创建后进规划
            cls.PLANNING: [cls.READY, cls.KILLED],          # v0.3: 规划完成→就绪
            cls.READY: [cls.RUNNING, cls.KILLED],
            cls.RUNNING: [cls.DONE, cls.FAILED, cls.KILLED],
            cls.DONE: [],
            cls.FAILED: [],
            cls.KILLED: [],
        }
        return to_state in transitions.get(from_state, [])


# ── Process（纯数据，含cap继承）──

class Process:
    """IO-S进程。cap从父进程继承，父进程从boot.initial_cap继承。

    v0.3新增:
      - plan: 规划步骤列表（planner.decompose的输出）
      - hindsight: 事后经验（planner.hindsight的输出，附带回写不经过状态机）
    """

    def __init__(self, goal: str, pid: str = None, skills: list[str] = None,
                 parent_pid: str = None):
        self.pid = pid or f"p-{int(time.time() * 1000)}"
        self.goal = goal
        self.state = ProcessState.CREATED
        self.skills = skills or []
        self.parent_pid = parent_pid
        self.cap: dict = {}      # {resource_type: [perms]}
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.created_at
        self.result: dict = {}
        self.exit_code: int | None = None
        self.error: str | None = None
        # v0.3: 规划层字段
        self.plan: list[dict] = []          # planner.decompose输出
        self.hindsight: dict = {            # planner.hindsight输出（只读不驱动状态机）
            "experience": "",
            "failure_pattern": "",
            "alternative": "",
        }

    def transition(self, to_state: str) -> bool:
        if not ProcessState.valid_transition(self.state, to_state):
            return False
        self.state = to_state
        self.updated_at = datetime.now(timezone.utc).isoformat()
        return True

    def complete_with_hindsight(self, result: dict, hindsight: dict = None) -> bool:
        """running → done + 附带hindsight回写。

        七神划界（阿波罗）：hindsight只读不碰状态机。
        hindsight是附着在Process上的信息沉淀，不是一个独立的生命周期阶段。
        """
        if not self.transition(ProcessState.DONE):
            return False
        if hindsight:
            # 只合并已知字段，不引入副作用
            self.hindsight.update({
                k: v for k, v in hindsight.items()
                if k in ("experience", "failure_pattern", "alternative")
            })
        self.result = result
        return True

    def to_dict(self) -> dict:
        return {
            "pid": self.pid,
            "goal": self.goal,
            "state": self.state,
            "skills": self.skills,
            "parent_pid": self.parent_pid,
            "cap": self.cap,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result,
            "exit_code": self.exit_code,
            "error": self.error,
            "plan": self.plan,                    # v0.3
            "hindsight": self.hindsight,           # v0.3
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Process":
        p = cls(goal=d["goal"], pid=d.get("pid"), parent_pid=d.get("parent_pid"))
        p.state = d.get("state", ProcessState.CREATED)
        p.skills = d.get("skills", [])
        p.cap = d.get("cap", {})
        p.created_at = d.get("created_at", p.created_at)
        p.updated_at = d.get("updated_at", p.created_at)
        p.result = d.get("result", {})
        p.exit_code = d.get("exit_code")
        p.error = d.get("error")
        # v0.3: 向后兼容——旧持久化文件无plan/hindsight字段
        p.plan = d.get("plan", [])
        p.hindsight = d.get("hindsight", {"experience": "", "failure_pattern": "", "alternative": ""})
        return p


# ── 进程表（持久化 + cap继承）──

PROCESS_DIR = Path.home() / ".io-s" / "processes"
CAP_POLICY_PATH = Path.home() / ".io-s" / "cap_policy.json"


class ProcessTable:
    """IO-S进程表。所有进程的注册中心。spawn时自动继承cap。"""

    def __init__(self, proc_dir: Path = None):
        self._dir = proc_dir or PROCESS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Process] = {}

    def spawn(self, process: Process, parent_pid: str = None) -> str:
        """创建进程并继承cap。

        Cap继承链：
          1. 有parent_pid → 从parent复制cap
          2. 无parent_pid → 从cap_policy.json的boot.initial_cap拿
          3. 上述都失败→空cap（零权限）
        """
        # 确定parent
        effective_parent = parent_pid or process.parent_pid
        if effective_parent:
            parent = self.get(effective_parent)
            if parent:
                process.cap = dict(parent.cap)  # 深拷贝cap
                process.parent_pid = effective_parent
        else:
            # 从boot继承
            boot_cap = self._load_boot_cap()
            process.cap = dict(boot_cap)

        # v0.3: spawn后直接进入PLANNING状态（由上一级决定是否切换到READY）
        process.transition(ProcessState.PLANNING)
        self._cache[process.pid] = process
        self._persist(process)
        return process.pid

    def _load_boot_cap(self) -> dict:
        """从cap_policy.json加载boot.initial_cap。"""
        try:
            if CAP_POLICY_PATH.exists():
                policy = json.loads(CAP_POLICY_PATH.read_text())
                return policy.get("boot", {}).get("initial_cap", {})
        except (json.JSONDecodeError, IOError):
            pass
        return {"cards": [{"pattern": "*", "perms": ["read"]}],
                "processes": [], "signals": [], "cap_policy": []}

    def register(self, process: Process) -> str:
        """直接注册（不继承cap，用于还原已有进程）。"""
        process.transition(ProcessState.PLANNING)
        self._cache[process.pid] = process
        self._persist(process)
        return process.pid

    def get(self, pid: str) -> Process | None:
        if pid in self._cache:
            return self._cache[pid]
        pf = self._dir / f"{pid}.json"
        if pf.exists():
            p = Process.from_dict(json.loads(pf.read_text()))
            self._cache[pid] = p
            return p
        return None

    def update(self, process: Process) -> bool:
        if process.pid not in self._cache and not (self._dir / f"{process.pid}.json").exists():
            return False
        self._cache[process.pid] = process
        self._persist(process)
        return True

    def list(self, state_filter: str = None) -> list[Process]:
        self._sync_from_disk()
        procs = list(self._cache.values())
        if state_filter:
            procs = [p for p in procs if p.state == state_filter]
        return sorted(procs, key=lambda p: p.created_at, reverse=True)

    def _persist(self, process: Process):
        pf = self._dir / f"{process.pid}.json"
        pf.write_text(json.dumps(process.to_dict(), ensure_ascii=False, indent=2))

    def _sync_from_disk(self):
        for pf in self._dir.glob("*.json"):
            pid = pf.stem
            if pid not in self._cache:
                try:
                    self._cache[pid] = Process.from_dict(json.loads(pf.read_text()))
                except (json.JSONDecodeError, KeyError):
                    continue


# ── Executor接口（Adapter模式）──

class Executor(Protocol):
    """执行器接口 — IO-S不依赖任何特定后端。"""

    def spawn(self, process: Process) -> str:
        ...

    def kill(self, task_id: str) -> bool:
        ...

    def status(self, task_id: str) -> dict:
        ...


# ── HermesExecutor（Hermes后端适配器）──

class HermesExecutor:
    """Hermes执行器 — 把IO-S Process映射到Hermes delegate_task。

    当IO-S寄宿在Hermes上时使用。其他底座运行时用NullExecutor。
    切换执行器：process.set_executor(HermesExecutor())
    """

    def __init__(self, delegate_fn=None):
        """delegate_fn: Hermes的delegate_task函数引用（由IO-S注入）。"""
        self._delegate = delegate_fn

    def spawn(self, process: Process) -> str:
        if self._delegate is None:
            print(f"[HermesExecutor] ⚠️ 未设置delegate_fn，无法实际spawn")
            return f"unbound-{process.pid}"
        # 将Process信息注入Hermes delegate_task
        task_id = self._delegate(
            goal=process.goal,
            context=f"[IO-S Process] pid={process.pid}, cap={json.dumps(process.cap)}",
        )
        return str(task_id)

    def kill(self, task_id: str) -> bool:
        return True  # Hermes delegate_task目前不暴露kill接口

    def status(self, task_id: str) -> dict:
        return {"task_id": task_id, "executor": "hermes", "status": "unknown"}


# ── 空执行器（验证IO-S独立性）──

class NullExecutor:
    """空执行器 — 只记录不执行。验证IO-S不依赖Hermes。"""

    def spawn(self, process: Process) -> str:
        print(f"[NullExecutor] would spawn: {process.pid} -> {process.goal[:60]}")
        return f"null-{process.pid}"

    def kill(self, task_id: str) -> bool:
        return True

    def status(self, task_id: str) -> dict:
        return {"task_id": task_id, "status": "mock", "executor": "null"}

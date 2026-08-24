#!/usr/bin/env python3
"""
IO-S内核 v0.2 — 治理系统核心

v0.2变更 (2026-06-24):
  - 加载 cap_policy.json 作为治理根基（零root）
  - cap_check() — 每次syscall前检查权限
  - 标准请求/响应信封（P1前置）
  - dispatch() 自动包装cap_check + 标准信封

架构原则：
  - 零root状态：root sigil销毁后无人可改cap_policy
  - 治理不认知：IO-S检查"能或不能"，不解析"该或不该"
  - 审计不参与：包拯独立于IO-S运行
"""

import os
import sys
import json
import time
import logging
import uuid
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger("io-s.kernel")

IO_S_HOME = Path.home() / ".io-s"
SIGNAL_DIR = IO_S_HOME / "signals"
AGENT_DIR = IO_S_HOME / "agents"
JIAK_DIR = Path.home() / ".hermes" / "jiak"

# ── 标准错误码 ──

class ErrCode:
    OK = "ok"
    UNKNOWN_SYSCALL = "unknown_syscall"
    CAP_DENIED = "cap_denied"
    RESOURCE_NOT_FOUND = "resource_not_found"
    INVALID_ARGS = "invalid_args"
    INTERNAL_ERROR = "internal_error"
    CAP_POLICY_LOCKED = "cap_policy_locked"


# ── 标准响应信封 ──

def ok_response(data: any, trace_id: str = None) -> dict:
    return {"ok": True, "data": data, "error": None, "trace_id": trace_id or str(uuid.uuid4())}

def error_response(code: str, message: str, trace_id: str = None) -> dict:
    return {"ok": False, "data": None, "error": {"code": code, "message": message},
            "trace_id": trace_id or str(uuid.uuid4())}


class CapPolicy:
    """cap_policy.json 的加载器和查询接口。

    IO-S的治理根基——定义谁可以访问什么资源。
    文件一旦加载，kernel只能读不能改——零root。

    v0.3: 演化层叠加（M3）
    - load_evolutions() 加载 cap_policy.evolutions.jsonl
    - check() 叠加演化层：基座允许 + 演化层允许 = 允许
    - 无演化记录时行为与 v0.2 完全一致（向后兼容）
    """

    def __init__(self, path: Path = None):
        self._path = path or IO_S_HOME / "cap_policy.json"
        self._policy: dict = {}
        self._loaded = False
        self._evolution_layers: dict = {}  # (target_pid, resource_type, operation) → record
        self.load()
        self.load_evolutions()

    def load(self):
        """加载cap_policy.json。失败=IO-S无法启动。"""
        if not self._path.exists():
            logger.warning(f"⚠️ cap_policy.json 不存在: {self._path}")
            self._policy = self._default_policy()
            self._loaded = False
            return

        try:
            self._policy = json.loads(self._path.read_text())
            self._loaded = True
            logger.info(f"✅ cap_policy loaded ({len(self._policy.get('resources', {}))} 类型)")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"❌ cap_policy 加载失败: {e}")
            raise

    def load_evolutions(self):
        """加载演化层（边界演化记录）。

        文件不存在=无演化（与现状完全一致）。
        v0.3: 演化层叠加——revoke/narrow 的 applied 记录会收窄权限。
        """
        self._evolution_layers = {}
        try:
            import os
            evolution_path = os.environ.get(
                "IO_S_EVOLUTION_PATH",
                str(IO_S_HOME / "cap_policy.evolutions.jsonl")
            )
            path = Path(evolution_path)
            if not path.exists():
                return

            for line in path.read_text(encoding="utf-8").strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("status") != "applied":
                        continue

                    action = rec.get("action", {})
                    target_pid = action.get("target_pid", "")
                    resource_type = action.get("resource_type", "")
                    operation = action.get("operation", "")
                    direction = rec.get("direction", "")
                    pattern = action.get("pattern", "")

                    if target_pid and resource_type and operation:
                        key = (target_pid, resource_type, operation)
                        self._evolution_layers[key] = {
                            "direction": direction,
                            "pattern": pattern,
                            "mode": action.get("mode", ""),
                            "proposal_id": rec.get("proposal_id", ""),
                        }
                except json.JSONDecodeError:
                    continue

            if self._evolution_layers:
                logger.info(f"📎 演化层加载: {len(self._evolution_layers)} 条 applied 记录")
        except Exception as e:
            logger.warning(f"⚠️ load_evolutions 静默失败: {e}")

    def _default_policy(self) -> dict:
        """最小安全策略——没有默认权限。"""
        return {
            "_schema": "cap_policy/v1",
            "_zero_root": True,
            "resources": {},
            "boot": {"initial_cap": {"cards": [], "processes": [], "signals": [], "cap_policy": []}},
            "builtin_agents": {"kernel": {"bypass_cap": True}},
        }

    def check(self, caller_pid: str, operation: str, resource_type: str,
              resource_name: str = None, process_cap: dict = None) -> tuple[bool, str]:
        """检查一个Process是否有权操作指定资源。

        v0.3: 基座 ACL 判定后，叠加演化层增量。
        - 无演化记录 → 行为与 v0.2 完全一致（向后兼容铁律）
        - 有 applied 的 revoke/narrow 记录 → 在基座允许时再查演化层
        - expand 未应用前不生效（pending_human 永远不自动生效）

        Args:
            caller_pid: 调用者的进程ID
            operation: "read" | "write" | "spawn" | "kill"
            resource_type: "card" | "process" | "signal" | "cap_policy"
            resource_name: 资源的具体名称/模式
            process_cap: 该Process的cap字典（从ProcessTable获取）

        Returns:
            (允许与否, 拒绝原因)
        """
        if caller_pid == "kernel":
            return True, ""

        # ── Phase 1: 静态 policy 判定（v0.2 逻辑不变）──
        base_allowed = False
        base_reason = ""

        if process_cap:
            perms_list = process_cap.get(resource_type, [])
            for entry in perms_list:
                perms = entry.get("perms", [])
                if operation in perms:
                    pattern = entry.get("pattern", "*")
                    if pattern == "*" or (resource_name and pattern in resource_name):
                        base_allowed = True
                        break
            if not base_allowed:
                base_reason = f"Process {caller_pid} 的cap不允许 {operation} {resource_type}"
        else:
            resources = self._policy.get("resources", {})
            res_def = resources.get(resource_type)
            if not res_def:
                base_reason = f"未知资源类型: {resource_type}"
            else:
                default_acl = res_def.get("default_acl", {})
                allowed_pids = default_acl.get(operation, [])
                if "kernel" in allowed_pids and caller_pid == "kernel":
                    base_allowed = True
                elif "owner" in allowed_pids:
                    base_allowed = True
                else:
                    base_reason = f"Process {caller_pid} 没有被授权 {operation} {resource_type}"

        # ── Phase 2: 演化层叠加（M3 增量）──
        # 无演化记录 → 行为与 v0.2 完全一致
        if not self._evolution_layers:
            return base_allowed, base_reason

        # 查演化层
        key = (caller_pid, resource_type, operation)
        evolution = self._evolution_layers.get(key)

        if evolution:
            direction = evolution.get("direction", "")
            if direction in ("revoke", "narrow"):
                # 演化层撤销了此权限 → 最终拒绝
                proposal_id = evolution.get("proposal_id", "")
                return False, (f"演化层 {direction} {proposal_id} "
                               f"撤销了 {caller_pid} 的 {operation} {resource_type}")
            elif direction == "expand":
                # expand 只在人审批 applied 后才生效（此处已检查 applied）
                pass

        # 演化层无覆盖 → 使用基座结果
        return base_allowed, base_reason

    def is_zero_root(self) -> bool:
        return self._policy.get("_zero_root", False)

    def get_initial_cap(self) -> dict:
        return self._policy.get("boot", {}).get("initial_cap", {})

    def __repr__(self):
        return f"<CapPolicy loaded={self._loaded} zero_root={self.is_zero_root()}>"


class Kernel:
    """IO-S v0.3 微内核 — 零root治理 + syscall调度 + 信号路由 + ProcessTable"""

    def __init__(self, agent_id: str = "io-s-kernel"):
        self.agent_id = agent_id
        self.io_s_home = IO_S_HOME
        self.signal_dir = SIGNAL_DIR
        self.agent_dir = AGENT_DIR

        for d in [self.io_s_home, self.signal_dir, self.agent_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # 治理根基
        self._cap = CapPolicy()

        # syscall注册表
        self._syscall_table: dict = {}

        # #7修复：加载ProcessTable（cap继承链）
        self._process_table = None
        self._load_process_table()

        # 语义→资源 operation映射（治理语言翻译器）
        # syscall用语义语言(send/recv/spawn), cap_policy用资源语言(read/write)
        self.OPERATION_MAP = {
            "send": "write",    # 发信号→写信号文件
            "recv": "read",     # 收信号→读信号文件
            "spawn": "write",   # 创建进程→写进程表
            "kill": "write",    # 杀进程→更新进程表
            "create": "write",  # 创建卡片→写卡片文件
            "list": "read",     # 列资源→读资源索引
            "read": "read",     # 直通
            "write": "write",   # 直通
        }

        # syscall名称 → resource type 映射（自动推断，ISN/ISA不需手动传resource）
        # 格式: "syscall_name" → ("resource_type", "default_operation")
        self.SYSCALL_RESOURCE_MAP = {
            "signal_send":    ("signal",  "send"),
            "signal_recv":    ("signal",  "recv"),
            "signal_broadcast": ("signal", "send"),
            "card_create":    ("card",    "create"),
            "card_read":      ("card",    "read"),
            "card_append":    ("card",    "write"),
            "card_archive":   ("card",    "write"),
            "card_split":     ("card",    "write"),
            "card_list":      ("card",    "list"),
            "card_batch_commit": ("card", "write"),
            "agent_spawn":    ("process", "spawn"),
            "agent_kill":     ("process", "kill"),
            "agent_status":   ("process", "read"),
            "agent_list":     ("process", "list"),
            "dream_launch":   ("card",    "read"),
            "dream_report":   ("card",    "read"),
            "recall_append":  ("card",    "write"),
            "recall_query":   ("card",    "read"),
        }

        # 心跳
        self._start_time = time.time()
        self._syscall_count = 0
        self._trace_counter = 0

        logger.info(f"⚙️ {agent_id} 内核启动 — 零root={'是' if self._cap.is_zero_root() else '否'}")

    # ── syscall注册 ──

    def register(self, name: str, handler):
        self._syscall_table[name] = handler

    def dispatch(self, name: str, caller_pid: str = "kernel",
                 args: dict = None, trace_id: str = None,
                 process_cap: dict = None) -> dict:
        """调用系统调用（含cap_check + 标准信封）。

        Args:
            name: syscall名称
            caller_pid: 调用者进程ID
            args: 请求参数 {"resource", "operation", ...}
            trace_id: 追踪ID（自动生成）
            process_cap: 调用者的cap字典（从ProcessTable获取）

        Returns:
            标准响应: {"ok", "data", "error", "trace_id"}
        """
        self._syscall_count += 1
        tid = trace_id or f"tr-{int(time.time()*1000)}-{self._trace_counter}"
        self._trace_counter += 1
        args = args or {}

        # 1. 检查syscall存在性
        if name not in self._syscall_table:
            return error_response(ErrCode.UNKNOWN_SYSCALL, f"未知系统调用: {name}", tid)

        # 2. cap_check（除kernel自身外都检查）
        if caller_pid != "kernel":
            resource = args.get("resource", "")
            operation = args.get("operation", "")
            # 自动推断：ISN/ISA不传resource/operation时，从syscall名称推断
            if not resource or not operation:
                inferred = self.SYSCALL_RESOURCE_MAP.get(name)
                if inferred:
                    resource = resource or inferred[0]
                    operation = operation or inferred[1]
            # 语义→资源映射：send→write, recv→read, spawn→write...
            mapped_op = self.OPERATION_MAP.get(operation, operation)
            # #7修复：自动从ProcessTable查询cap
            process_cap = process_cap or self._get_process_cap(caller_pid)
            allowed, reason = self._cap.check(caller_pid, mapped_op, resource,
                                              process_cap=process_cap)
            if not allowed:
                logger.warning(f"⛔ CAP_DENIED: {caller_pid} {operation} {resource}: {reason} (cap={process_cap})")
                return error_response(ErrCode.CAP_DENIED, reason, tid)

        # 3. 执行handler（过滤掉治理参数）
        handler_args = {k: v for k, v in args.items()
                        if k not in ('resource', 'operation')}
        try:
            result = self._syscall_table[name](caller_pid=caller_pid, **handler_args)
            return ok_response(result, tid)
        except Exception as e:
            logger.error(f"❌ syscall {name} 异常: {e}")
            return error_response(ErrCode.INTERNAL_ERROR, str(e), tid)

    def list_syscalls(self) -> list[str]:
        return sorted(self._syscall_table.keys())

    # ── ProcessTable接口（#7修复）──

    def _load_process_table(self):
        """加载ProcessTable。失败不报错——P2优先级，不影响启动。"""
        try:
            from process import ProcessTable
            self._process_table = ProcessTable()
            logger.info(f"✅ ProcessTable loaded ({len(self._process_table.list())} processes)")
        except Exception as e:
            self._process_table = None
            logger.warning(f"⚠️ ProcessTable未加载 (P2, 不影响启动): {e}")

    def _get_process_cap(self, caller_pid: str) -> dict | None:
        """从ProcessTable查询进程的cap。返回None=未找到→回退到静态policy。"""
        if self._process_table is None:
            return None
        process = self._process_table.get(caller_pid)
        if process is None:
            return None
        return getattr(process, 'cap', None) or getattr(process, '_cap', None)

    # ── cap接口 ──

    def cap_check(self, caller_pid: str, operation: str,
                  resource_type: str, resource_name: str = None) -> tuple[bool, str]:
        """外部cap_check接口——供syscall handler内部调用。"""
        if caller_pid == "kernel":
            return True, ""
        return self._cap.check(caller_pid, operation, resource_type, resource_name)

    def get_cap_policy(self) -> dict:
        """返回cap_policy只读快照。"""
        return {
            "zero_root": self._cap.is_zero_root(),
            "resources": list(self._cap._policy.get("resources", {}).keys()),
        }

    # ── 信号路由 ──

    def route_signal(self, dest: str, body: dict, caller_pid: str = "kernel") -> dict:
        """路由信号（含审计注入）。"""
        ts = datetime.now(timezone.utc).isoformat()
        signal_id = f"{dest}-{int(time.time()*1000)}"
        signal_file = self.signal_dir / f"{signal_id}.json"

        # 审计注入
        payload = {
            "signal_id": signal_id,
            "dest": dest,
            "body": body,
            "sent_at": ts,
            "status": "sent",
            "audit": {
                "caller_pid": caller_pid,
                "route_log": [{"hop": "kernel", "at": ts}],
                "cap_check": "passed" if caller_pid == "kernel" else "unknown",
                "recorded_at": ts,
            }
        }
        signal_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        return ok_response({"signal_id": signal_id}, f"sig-{signal_id}")

    def read_inbox(self, dest: str, since_ts: str = None,
                   caller_pid: str = "kernel") -> list[dict]:
        signals = []
        for sf in sorted(self.signal_dir.glob("*.json")):
            try:
                payload = json.loads(sf.read_text())
                if payload.get("dest") == dest:
                    if since_ts is None or payload.get("sent_at", "") > since_ts:
                        signals.append(payload)
            except (json.JSONDecodeError, KeyError):
                continue
        return signals

    # ── 心跳 ──

    def write_status(self):
        """写kernel状态到 ~/.io-s/kernel.json。ISA通过此文件检测IO-S就绪。"""
        status = self.heartbeat()
        status["cap_policy_loaded"] = self._cap._loaded
        status["cap_resources"] = list(self._cap._policy.get("resources", {}).keys())
        status["status"] = "running" if self._cap._loaded and self._syscall_count >= 0 else "initializing"
        status_file = self.io_s_home / "kernel.json"
        status_file.write_text(json.dumps(status, ensure_ascii=False, indent=2))
        logger.info(f"📄 kernel.status written: {status_file}")
        return status

    def heartbeat(self) -> dict:
        """返回内核心跳统计。"""
        uptime = time.time() - self._start_time
        return {
            "agent_id": self.agent_id,
            "uptime_s": round(uptime, 1),
            "syscall_count": self._syscall_count,
            "registered_syscalls": len(self._syscall_table),
            "signals_pending": len(list(self.signal_dir.glob("*.json"))),
            "zero_root": self._cap.is_zero_root(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def status(self) -> dict:
        s = self.heartbeat()
        cards_dir = JIAK_DIR / "cards"
        s["card_count"] = len(list(cards_dir.glob("*.json"))) if cards_dir.exists() else 0
        agent_files = list(self.agent_dir.glob("*.json"))
        s["agent_count"] = len(agent_files)
        s["cap_resources"] = list(self._cap._policy.get("resources", {}).keys())
        return s


# 全局单例
_kernel: Kernel = None

def get_kernel() -> Kernel:
    global _kernel
    if _kernel is None:
        _kernel = Kernel()
    return _kernel

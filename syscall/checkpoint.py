"""checkpoint.py — IO-S Checkpoint模块

借鉴Concordia (arXiv 2606.23521)的GPU-resident checkpoint模式，
在IO-S层实现软件级别的region注册+增量checkpoint+恢复。

七神划界:
  - 赫淮斯托斯: 只管快照不缓存执行结果
  - 克洛诺斯: checkpoint周期可配置
  - 阿波罗: AOF日志只追加不修改
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger("io-s.checkpoint")

CHECKPOINT_DIR = Path.home() / ".io-s" / "checkpoints"
REGISTRY_PATH = CHECKPOINT_DIR / "registry.json"
AOF_DIR = CHECKPOINT_DIR / "aof"
SNAPSHOT_DIR = CHECKPOINT_DIR / "snapshots"


class Region:
    """Checkpoint Region — 借鉴Concordia的region注册模式。

    一个region = 一个Process的快照单元 + 自定义序列化/反序列化。
    """

    def __init__(self, region_id: str, snapshot_fn: Callable,
                 restore_fn: Callable, metadata: dict = None):
        self.region_id = region_id
        self.snapshot = snapshot_fn       # 快照函数: () → dict
        self.restore = restore_fn          # 恢复函数: (dict) → bool
        self.metadata = metadata or {}
        self.last_snapshot_at: float = 0
        self.last_snapshot_size: int = 0


class CheckpointManager:
    """Checkpoint管理器 — 统一管理IO-S的checkpoint生命周期。

    Concordia的软件映射:
      - Region注册 ← Concordia的GPU state region
      - 增量checkpoint ← Concordia的JIT delta handler
      - AOF日志 ← Concordia的append-only log
      - 恢复 ← Concordia的recovery applier
    """

    def __init__(self, interval: float = 300.0, auto_start: bool = True):
        self.interval = interval           # 自动checkpoint间隔(秒), 默认5分钟
        self._regions: dict[str, Region] = {}
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._running = False
        self._aof_seq: int = 0

        # 初始化目录
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        AOF_DIR.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

        if auto_start:
            self.start()

    # ── Region注册 ──

    def register(self, region_id: str, snapshot_fn: Callable,
                 restore_fn: Callable, metadata: dict = None) -> bool:
        """注册一个checkpoint region。

        借鉴Concordia: 每个LLM state region通过register()注册到persistent kernel。
        这里: 每个Process通过register()注册到CheckpointManager。
        """
        with self._lock:
            if region_id in self._regions:
                return False
            self._regions[region_id] = Region(
                region_id, snapshot_fn, restore_fn, metadata
            )
            logger.info(f"✅ region注册: {region_id}")
            return True

    def unregister(self, region_id: str) -> bool:
        """注销region（Process完成时）。"""
        with self._lock:
            if region_id not in self._regions:
                return False
            del self._regions[region_id]
            logger.info(f"region注销: {region_id}")
            return True

    def list_regions(self) -> list[dict]:
        """列出所有注册的region。"""
        with self._lock:
            return [{
                "region_id": r.region_id,
                "last_snapshot_at": r.last_snapshot_at,
                "last_snapshot_size": r.last_snapshot_size,
                "metadata": r.metadata,
            } for r in self._regions.values()]

    # ── 快照 ──

    def snapshot(self, region_id: str) -> dict | None:
        """单region快照。"""
        with self._lock:
            region = self._regions.get(region_id)
            if region is None:
                logger.warning(f"未知region: {region_id}")
                return None

        try:
            data = region.snapshot()
            size = len(json.dumps(data))

            # 保存快照
            sp = SNAPSHOT_DIR / f"{region_id}.json"
            sp.write_text(json.dumps(data, ensure_ascii=False, indent=2))

            region.last_snapshot_at = time.time()
            region.last_snapshot_size = size

            # AOF日志: 记录本次checkpoint
            self._append_aof("snapshot", region_id, size)

            logger.info(f"📸 checkpoint: {region_id} ({size} bytes)")
            return data
        except Exception as e:
            logger.error(f"❌ checkpoint失败 {region_id}: {e}")
            return None

    def snapshot_all(self) -> dict[str, dict | None]:
        """全region快照。"""
        with self._lock:
            ids = list(self._regions.keys())
        results = {}
        for rid in ids:
            results[rid] = self.snapshot(rid)
        return results

    # ── 恢复 ──

    def restore(self, region_id: str) -> bool:
        """从最新快照恢复region。

        借鉴Concordia: recovery applier从AOF的最后一个committed record恢复。
        """
        sp = SNAPSHOT_DIR / f"{region_id}.json"
        if not sp.exists():
            logger.warning(f"无快照可恢复: {region_id}")
            return False

        try:
            data = json.loads(sp.read_text())
            with self._lock:
                region = self._regions.get(region_id)
                if region is None:
                    logger.warning(f"region未注册: {region_id}")
                    return False
                ok = region.restore(data)
            if ok:
                self._append_aof("restore", region_id, len(json.dumps(data)))
                logger.info(f"🔄 恢复成功: {region_id}")
            return ok
        except Exception as e:
            logger.error(f"❌ 恢复失败 {region_id}: {e}")
            return False

    # ── AOF日志 ──

    def _append_aof(self, op: str, region_id: str, size: int = 0):
        """写入AOF日志。Concordia的append-only log模式。

        七神划界（阿波罗）: AOF只追加不修改。
        """
        seq = self._aof_seq
        self._aof_seq += 1
        record = {
            "seq": seq,
            "ts": datetime.now(timezone.utc).isoformat(),
            "op": op,
            "region_id": region_id,
            "size": size,
        }
        aof_path = AOF_DIR / "aof.log"
        with open(aof_path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def get_aof(self, limit: int = 20) -> list[dict]:
        """读取最近的AOF记录。"""
        aof_path = AOF_DIR / "aof.log"
        if not aof_path.exists():
            return []
        records = []
        with open(aof_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records[-limit:]

    def get_aof_stats(self) -> dict:
        """AOF统计。"""
        records = self.get_aof(limit=10000)
        return {
            "total": len(records),
            "snapshots": sum(1 for r in records if r.get("op") == "snapshot"),
            "restores": sum(1 for r in records if r.get("op") == "restore"),
            "total_size": sum(r.get("size", 0) for r in records),
        }

    # ── 后台自动checkpoint ──

    def start(self):
        """启动后台自动checkpoint线程。"""
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._loop, daemon=True,
                                        name="checkpoint-worker")
        self._worker.start()
        logger.info(f"✅ CheckpointManager启动 (interval={self.interval}s)")

    def stop(self):
        """停止后台checkpoint。"""
        self._running = False
        if self._worker:
            self._worker.join(timeout=5)
        logger.info("CheckpointManager已停止")

    def _loop(self):
        """后台loop: 每interval秒全region快照。"""
        while self._running:
            time.sleep(self.interval)
            if self._running and self._regions:
                logger.info(f"⏰ 自动checkpoint: {len(self._regions)} regions")
                self.snapshot_all()

    # ── 工具函数: Process适配 ──

    @staticmethod
    def process_snapshot(process_table, pid: str) -> dict:
        """Process的快照函数。"""
        proc = process_table.get(pid)
        if proc is None:
            raise ValueError(f"Process不存在: {pid}")
        return proc.to_dict()

    @staticmethod
    def process_restore(process_table, pid: str, data: dict) -> bool:
        """Process的恢复函数。"""
        from process import Process
        proc = Process.from_dict(data)
        return process_table.update(proc)

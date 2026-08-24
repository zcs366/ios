#!/usr/bin/env python3
"""boundary_evolution.py — 边界演化层写入器（M3）

只接受 kernel/dispatch 内部调用，不暴露给 Process。

append-only: cap_policy.evolutions.jsonl
- 每条一行 JSON（EvolutionRecord）
- checksum 链：上一行的 sha256（篡改检测）
- 快照/回滚：snapshot() 备份尾部，rollback() 恢复
- verify_chain(): 校验 checksum 链完整性
- 红线：cap_policy.json 永不写入
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from uuid import uuid4
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("io-s.boundary_evolution")

# ── 路径（可通过环境变量 IO_S_EVOLUTION_PATH 覆盖）──
_default_evolutions = Path.home() / ".io-s" / "cap_policy.evolutions.jsonl"
EVOLUTION_PATH = Path(os.environ.get("IO_S_EVOLUTION_PATH", str(_default_evolutions)))
SNAPSHOT_PATH = Path(str(EVOLUTION_PATH) + ".snapshot")


# ── 演化记录数据结构 ──

@dataclass
class EvolutionRecord:
    version: int = 1
    proposal_id: str = ""        # f"pe-{uuid4().hex[:12]}"
    direction: str = ""          # "revoke" | "narrow" | "expand"
    action: dict = None          # {resource_type, operation, pattern, mode}
    trigger: dict = None         # {signal_source, metric, value}
    status: str = "proposed"     # proposed|validated|applied|rejected|pending_human
    v_before: float = None       # 提案时 V 值
    v_after: float = None        # 应用后 V 值
    checksum: str = ""           # 上一行 checksum 链
    created_at: str = ""
    _written_by: str = "io-s"


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def _ensure_dir():
    """确保演化层目录存在。"""
    EVOLUTION_PATH.parent.mkdir(parents=True, exist_ok=True)


# ── 核心接口 ──

def append_proposal(direction: str, action: dict, trigger: dict = None,
                    v_before: float = None) -> str:
    """追加一条 proposed 记录，返回 proposal_id。checksum 链：上一行的 sha256。"""
    try:
        _ensure_dir()
        prev_checksum = ""
        if EVOLUTION_PATH.exists():
            content = EVOLUTION_PATH.read_text(encoding="utf-8").strip()
            if content:
                lines = content.split("\n")
                last_line = lines[-1].strip()
                if last_line:
                    prev_checksum = _sha256(last_line)

        proposal_id = f"pe-{uuid4().hex[:12]}"

        record = EvolutionRecord(
            proposal_id=proposal_id,
            direction=direction,
            action=action,
            trigger=trigger or {},
            v_before=v_before,
            checksum=prev_checksum,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        record_dict = asdict(record)
        line = json.dumps(record_dict, ensure_ascii=False, separators=(",", ":"))

        # append-only: 用 "a" 不用 "w"
        with open(EVOLUTION_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        logger.info(f"📝 append_proposal: {proposal_id} ({direction})")
        return proposal_id
    except Exception as e:
        logger.error(f"⚠️ append_proposal 失败(静默): {e}")
        return ""


def update_status(proposal_id: str, status: str, v_after: float = None) -> bool:
    """更新指定 proposal 的状态（append-only：追加新记录，不修改旧行）。"""
    try:
        _ensure_dir()
        prev_checksum = ""
        if EVOLUTION_PATH.exists():
            content = EVOLUTION_PATH.read_text(encoding="utf-8").strip()
            if content:
                lines = content.split("\n")
                last_line = lines[-1].strip()
                if last_line:
                    prev_checksum = _sha256(last_line)

        # 读取旧记录的 action 等字段
        action = {}
        direction = ""
        trigger = {}
        v_before = None
        if EVOLUTION_PATH.exists():
            content = EVOLUTION_PATH.read_text(encoding="utf-8").strip()
            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("proposal_id") == proposal_id:
                        action = rec.get("action", {})
                        direction = rec.get("direction", "")
                        trigger = rec.get("trigger", {})
                        v_before = rec.get("v_before")
                except json.JSONDecodeError:
                    continue

        record = EvolutionRecord(
            proposal_id=proposal_id,
            direction=direction,
            action=action,
            trigger=trigger,
            status=status,
            v_before=v_before,
            v_after=v_after,
            checksum=prev_checksum,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        record_dict = asdict(record)
        line = json.dumps(record_dict, ensure_ascii=False, separators=(",", ":"))

        with open(EVOLUTION_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        logger.info(f"📝 update_status: {proposal_id} → {status}")
        return True
    except Exception as e:
        logger.error(f"⚠️ update_status 失败(静默): {e}")
        return False


def apply_evolutions(proposal_id: str) -> bool:
    """将 validated 记录标记为 applied（append-only：追加新记录）。"""
    try:
        _ensure_dir()
        prev_checksum = ""
        if EVOLUTION_PATH.exists():
            content = EVOLUTION_PATH.read_text(encoding="utf-8").strip()
            if content:
                lines = content.split("\n")
                last_line = lines[-1].strip()
                if last_line:
                    prev_checksum = _sha256(last_line)

        action = {}
        direction = ""
        trigger = {}
        v_before = None
        if EVOLUTION_PATH.exists():
            content = EVOLUTION_PATH.read_text(encoding="utf-8").strip()
            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("proposal_id") == proposal_id:
                        action = rec.get("action", {})
                        direction = rec.get("direction", "")
                        trigger = rec.get("trigger", {})
                        v_before = rec.get("v_before")
                except json.JSONDecodeError:
                    continue

        record = EvolutionRecord(
            proposal_id=proposal_id,
            direction=direction,
            action=action,
            trigger=trigger,
            status="applied",
            v_before=v_before,
            checksum=prev_checksum,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        record_dict = asdict(record)
        line = json.dumps(record_dict, ensure_ascii=False, separators=(",", ":"))

        with open(EVOLUTION_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        logger.info(f"📝 apply_evolutions: {proposal_id} → applied")
        return True
    except Exception as e:
        logger.error(f"⚠️ apply_evolutions 失败(静默): {e}")
        return False


def load_evolutions() -> list[dict]:
    """读全部演化记录（按行解析，损坏行跳过并告警）。"""
    if not EVOLUTION_PATH.exists():
        return []
    try:
        records = []
        for line in EVOLUTION_PATH.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning(f"⚠️ 演化记录损坏，跳过: {line[:80]}")
        return records
    except Exception as e:
        logger.error(f"⚠️ load_evolutions 失败(静默): {e}")
        return []


def snapshot() -> str:
    """备份当前演化层尾部（cp 到 .snapshot），返回快照路径。"""
    try:
        if EVOLUTION_PATH.exists():
            shutil.copy2(EVOLUTION_PATH, SNAPSHOT_PATH)
        logger.info(f"📸 snapshot: {SNAPSHOT_PATH}")
        return str(SNAPSHOT_PATH)
    except Exception as e:
        logger.error(f"⚠️ snapshot 失败(静默): {e}")
        return ""


def rollback(proposal_id: str) -> bool:
    """回滚：追加一条 status=rejected 记录 + 恢复快照。

    ⚠️ 回滚只用于 apply 失败场景；正常路径不调用。
    """
    try:
        # 追加 rejected 记录
        update_status(proposal_id, "rejected")

        # 恢复快照
        if SNAPSHOT_PATH.exists():
            shutil.copy2(SNAPSHOT_PATH, EVOLUTION_PATH)
            logger.info(f"🔄 rollback: 从快照恢复 {proposal_id}")
            return True
        else:
            logger.warning(f"⚠️ rollback: 快照不存在 {SNAPSHOT_PATH}")
            return False
    except Exception as e:
        logger.error(f"⚠️ rollback 失败(静默): {e}")
        return False


def verify_chain() -> bool:
    """校验 checksum 链完整性——任一环断裂返回 False（篡改检测）。"""
    records = load_evolutions()
    if not records:
        return True  # 空链 = 完整

    prev_checksum = ""
    for rec in records:
        stored_checksum = rec.get("checksum", "")
        if stored_checksum != prev_checksum:
            logger.warning(
                f"🔗 checksum 链断裂: proposal={rec.get('proposal_id')} "
                f"期望={prev_checksum[:8]} 实际={stored_checksum[:8]}"
            )
            return False
        # 计算本行的 checksum 作为下一行的 prev
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
        prev_checksum = _sha256(line)

    return True

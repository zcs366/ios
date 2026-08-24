"""hindsight_loop.py — hindsight经验闭环

每个子Agent执行完成后自动：
1. 调用 planner.hindsight 提取经验
2. 写入对应Process的hindsight字段
3. 下次同类任务自动注入经验

七神划界（阿波罗）：hindsight只读不碰状态机。
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger("io-s.hindsight-loop")

HINDSIGHT_DIR = Path.home() / ".io-s" / "hindsight"
HINDSIGHT_DIR.mkdir(parents=True, exist_ok=True)


def extract_hindsight(pid: str, goal: str, result: dict,
                      failure: str = "") -> dict:
    """从子Agent执行结果提取hindsight经验。

    调用planner.hindsight，结果写入Process.hindsight。
    同时持久化到HINDSIGHT_DIR供跨session复用。

    Args:
        pid: 进程ID
        goal: 原始目标
        result: 执行结果（含success, execution_time, token_count）
        failure: 失败原因

    Returns:
        hindsight字典 {experience, failure_pattern, alternative}
    """
    from syscall.planner import handle_hindsight

    h = handle_hindsight(pid, result, failure)

    # 持久化到HINDSIGHT_DIR
    record = {
        "pid": pid,
        "goal_preview": goal[:120],
        "hindsight": h,
        "result": {k: v for k, v in result.items()
                   if k in ("success", "execution_time", "token_count")},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    path = HINDSIGHT_DIR / f"{pid}_{int(datetime.now().timestamp())}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2))

    return h


def inject_hindsight(pid: str) -> str:
    """为指定进程注入历史hindsight经验作为上下文。

    搜索HINDSIGHT_DIR中同pid的历史记录，
    返回格式化的经验文本。

    Returns:
        注入文本（空字符串=无历史经验）
    """
    records = sorted(HINDSIGHT_DIR.glob(f"{pid}_*.json"),
                     key=lambda p: p.stat().st_mtime, reverse=True)

    if not records:
        return ""

    # 取最近3条经验
    experiences = []
    for rp in records[:3]:
        try:
            rec = json.loads(rp.read_text())
            h = rec.get("hindsight", {})
            exp = h.get("experience", "")
            if exp:
                experiences.append(f"- {exp}")
        except (json.JSONDecodeError, IOError):
            continue

    if not experiences:
        return ""

    return "\n".join(["【历史经验参考】"] + experiences)


def get_recent_hindsight(limit: int = 5) -> list[dict]:
    """获取最近的hindsight经验（供审计/评估使用）。"""
    records = sorted(HINDSIGHT_DIR.glob("*.json"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for rp in records[:limit]:
        try:
            rec = json.loads(rp.read_text())
            out.append(rec)
        except (json.JSONDecodeError, IOError):
            continue
    return out

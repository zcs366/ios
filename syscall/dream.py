#!/usr/bin/env python3
"""
IO-S dream syscall — Brain对接（后台认知守护进程）

dream是IO-S的"自发思考"机制：
- dream_launch: 启动Brain后台扫描
- dream_report: 读取最新发现

匠石边界：dream_discovery是代码层（关键词+余弦），
LLM增强洞察是可选模型层（_llm_dream，不阻塞核心流程）。

Brain的dream()已实现双通道（keyword + vector cos≥0.6），
IO-S只做薄wrap。
"""

import sys
import json
from pathlib import Path

_ISA_DIR = str(Path.home() / "projects" / "isa")
if _ISA_DIR not in sys.path:
    sys.path.insert(0, _ISA_DIR)

# 梦境日志路径
DREAM_LOG = Path.home() / ".io-s" / "dreams.jsonl"


def dream_launch(card_ids: list[str] = None,
                 min_overlap: int = 2,
                 llm_endpoint: str = None,
                 interval: int = 60,
                 caller_pid: str = None) -> dict:
    """启动Brain后台Dreaming引擎。

    双通道发现：关键词重叠（≥2）| 语义余弦（≥0.6）。

    Args:
        card_ids: 指定卡片列表（None=全部）
        min_overlap: 关键词最小重叠数
        llm_endpoint: LLM增强端点（None=纯关联发现，不调LLM）
        interval: 扫描间隔秒数

    Returns: {"ok": True, "mode": "background", "interval": int}
    """
    from brain import Brain
    import threading

    brain = Brain("io-s-dreamer")

    if llm_endpoint:
        brain.start_dreaming(llm_endpoint=llm_endpoint, interval=interval)
    else:
        # 纯关联发现模式：只dream不调LLM
        brain._dream_config = {
            "llm_endpoint": None,
            "interval": interval,
            "min_overlap": min_overlap,
            "max_pairs_per_cycle": 3,
        }
        brain._dream_running = True
        brain._dream_thread = threading.Thread(
            target=brain._dream_worker, daemon=True,
            name=f"brain-dream-{brain.agent_id}"
        )
        brain._dream_thread.start()

    return {"ok": True, "mode": "background", "interval": interval,
            "min_overlap": min_overlap}


def dream_report(max_results: int = 10, caller_pid: str = None) -> dict:
    """读取最新Dreaming发现。

    从梦境日志读取最近N条发现。

    Returns: {"ok": True, "discoveries": [...], "cycles": int}
    """
    discoveries = []
    cycles = 0

    if DREAM_LOG.exists():
        for line in DREAM_LOG.read_text().strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                cycles += 1
                if entry.get("type") == "discovery":
                    discoveries.append(entry)
            except json.JSONDecodeError:
                continue

    # 如果没有日志，手动跑一次
    if not discoveries:
        try:
            from brain import Brain
            brain = Brain("io-s-dreamer")
            result = brain.dream()
            if result:
                discoveries = result
                # 写入日志
                DREAM_LOG.parent.mkdir(parents=True, exist_ok=True)
                with open(DREAM_LOG, 'a') as f:
                    for d in result:
                        d["type"] = "discovery"
                        f.write(json.dumps(d, ensure_ascii=False) + "\n")
        except Exception:
            pass

    return {"ok": True, "discoveries": discoveries[:max_results],
            "cycles": cycles}

"""team_scale.py — 团队规模优化

来自MAS-PromptBench (arXiv 2606.23664):
  - 团队>4时优化收益转负
  - 结构化协议优于自由协议
  - 团队>4自动拆分为2个并行小组
"""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger("io-s.team-scale")

MAX_TEAM_SIZE = 4  # MAS-PromptBench发现>4时优化转负


def optimize_team(agents: list[str], task: str = "") -> dict:
    """优化团队规模。

    如果agents > 4，自动拆分为2个并行小组。
    如果<=4，保持原样。
    """
    n = len(agents)

    if n <= MAX_TEAM_SIZE:
        return {
            "original_size": n,
            "optimized": False,
            "groups": [{"id": "main", "agents": agents, "protocol": "standard"}],
            "reason": f"团队规模{n}≤{MAX_TEAM_SIZE}，无需拆分",
        }

    # 拆分为2组
    mid = n // 2
    group_a = agents[:mid]
    group_b = agents[mid:]

    return {
        "original_size": n,
        "optimized": True,
        "groups": [
            {"id": "group_a", "agents": group_a, "protocol": "structured"},
            {"id": "group_b", "agents": group_b, "protocol": "structured"},
        ],
        "reason": f"团队{n}>{MAX_TEAM_SIZE}，拆分为{len(group_a)}+{len(group_b)}两组并行执行",
        "recommendation": "两组独立执行后，结果合并审议",
    }


def register(kernel):
    def handle_optimize(pid, agents, task=""):
        return optimize_team(agents, task)

    kernel.register("team.optimize", handle_optimize)
    logger.info("✅ 团队规模优化已注册")

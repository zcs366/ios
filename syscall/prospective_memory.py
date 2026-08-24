"""prospective_memory.py — 前瞻记忆管理

来自TriggerBench (arXiv 2606.23459):
  - PM在100K上下文从>90%崩溃到<40%
  - 注意力恢复-73分
  - r=-0.91: 推理链长度与PM的强负相关

在IO-S中实现前瞻记忆监测:
  - PMAcc测量: 当前上下文的前瞻记忆准确率估计
  - 降级触发: PMAcc<阈值时自动降级
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("io-s.pm")

PM_DECAY = 0.73        # TriggerBench发现注意力恢复-73分
PM_THRESHOLD = 0.40    # 40%以下触发降级
PM_CLIFF = 100_000     # 100K上下文处的认知悬崖


@dataclass
class PMSnapshot:
    """前瞻记忆快照。"""
    context_length: int
    pm_accuracy: float          # 估计的PM准确率
    inference_length: int       # 推理链长度
    timestamp: float = field(default_factory=time.time)

    def should_degrade(self) -> bool:
        return self.pm_accuracy < PM_THRESHOLD


class ProspectiveMemoryMonitor:
    """前瞻记忆监测器。

    基于TriggerBench发现:
      - 100K上下文 PM从>90%→<40% (r=-0.91)
      - 推理链长度每增加一倍，PM Acc下降~15%
    """

    def __init__(self):
        self._snapshots: list[PMSnapshot] = []

    def estimate(self, context_length: int,
                 inference_length: int = 0) -> PMSnapshot:
        """估计当前上下文的前瞻记忆准确率。

        基于TriggerBench认知悬崖模型:
          PM_Acc ≈ 0.9 - 0.5 * (ctx_len / 100000) - 0.15 * log2(inf_len + 1)
        """
        # 上下文衰减
        ctx_penalty = 0.5 * min(1.0, context_length / PM_CLIFF)
        # 推理链衰减
        inf_penalty = 0.15 * (inference_length / 10) if inference_length > 0 else 0

        pm = max(0.0, 0.9 - ctx_penalty - inf_penalty)

        snap = PMSnapshot(
            context_length=context_length,
            pm_accuracy=round(pm, 3),
            inference_length=inference_length,
        )
        self._snapshots.append(snap)
        return snap

    def should_degrade(self, context_length: int,
                       inference_length: int = 0) -> dict:
        """判断是否需要降级。

        Returns:
          {should_degrade, pm_accuracy, reason, action}
        """
        snap = self.estimate(context_length, inference_length)

        result = {
            "should_degrade": snap.should_degrade(),
            "pm_accuracy": snap.pm_accuracy,
            "context_length": context_length,
            "inference_length": inference_length,
        }

        if snap.should_degrade():
            result["reason"] = f"PM Accuracy={snap.pm_accuracy}低于阈值{PM_THRESHOLD}"
            if snap.pm_accuracy < 0.2:
                result["action"] = "完全降级：切换为短上下文模式"
                result["level"] = "critical"
            else:
                result["action"] = "部分降级：保留关键上下文，压缩非关键部分"
                result["level"] = "warning"
        else:
            result["reason"] = f"PM Accuracy={snap.pm_accuracy}正常"
            result["action"] = "无需降级"
            result["level"] = "normal"

        return result


def register(kernel):
    pm = ProspectiveMemoryMonitor()

    def handle_estimate(pid, ctx_len, inf_len=0):
        return pm.should_degrade(ctx_len, inf_len)

    kernel.register("pm.estimate", handle_estimate)
    logger.info("✅ 前瞻记忆管理已注册")

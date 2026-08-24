"""bits_metric.py — 比特度量体系

来自Agentic System as Compressor (arXiv 2606.25960):
  - 用比特而非成功率衡量系统智能
  - 验证器边际价值高达375 bits/seq
  - codelength reduction作为智能度量

核心组件:
  1. BitsMeter — 测量每次LLM调用的信息增益(bits)
  2. CodelengthTracker — 追踪系统组件的codelength变化
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("io-s.bits-metric")

METRICS_DIR = Path.home() / ".io-s" / "metrics"
METRICS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class InferenceEvent:
    """一次LLM推理事件，记录其信息贡献。"""
    event_id: str
    model: str
    input_tokens: int
    output_tokens: int
    success: bool
    task_type: str                    # "planning"|"execution"|"evaluation"|"hindsight"
    bits_gained: float = 0.0          # 信息增益(bits)
    codelength_before: float = 0.0    # 推理前系统codelength
    codelength_after: float = 0.0     # 推理后系统codelength
    timestamp: float = field(default_factory=time.time)

    @property
    def codelength_reduction(self) -> float:
        """codelength reduction = before - after (正数=减少)。"""
        return self.codelength_before - self.codelength_after

    @property
    def efficiency(self) -> float:
        """每token的信息效率(bits/token)。"""
        total_tokens = self.input_tokens + self.output_tokens
        if total_tokens == 0:
            return 0.0
        return self.bits_gained / total_tokens


class BitsMeter:
    """比特计量器 — 追踪系统的信息增益。"""

    def __init__(self, system_id: str = "io-s"):
        self.system_id = system_id
        self._events: list[InferenceEvent] = []
        self._codelength: float = 0.0
        self._load()

    def _load(self):
        """从持久化加载历史。"""
        path = METRICS_DIR / f"{self.system_id}_bits.jsonl"
        if path.exists():
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data = json.loads(line)
                            self._events.append(InferenceEvent(**data))
                        except (json.JSONDecodeError, TypeError):
                            continue

    def _append(self, event: InferenceEvent):
        """持久化一个事件。"""
        path = METRICS_DIR / f"{self.system_id}_bits.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(event.__dict__, ensure_ascii=False) + "\n")
        self._events.append(event)

    def record(self, event_id: str, model: str,
               input_tokens: int, output_tokens: int,
               success: bool, task_type: str,
               bits_gained: float = None) -> InferenceEvent:
        """记录一次推理事件。

        如果bits_gained未提供，使用启发式估算:
          - 成功: output_tokens * 0.1 (每token~0.1 bit有效信息)
          - 失败: -output_tokens * 0.05 (噪声)
        """
        if bits_gained is None:
            if success:
                bits_gained = output_tokens * 0.1
            else:
                bits_gained = -output_tokens * 0.05

        event = InferenceEvent(
            event_id=event_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            success=success,
            task_type=task_type,
            bits_gained=bits_gained,
            codelength_before=self._codelength,
            codelength_after=self._codelength + bits_gained,
        )
        self._codelength += bits_gained
        self._append(event)
        return event

    def get_codelength(self) -> float:
        """当前系统的codelength(bits)。"""
        return self._codelength

    def get_total_bits(self) -> float:
        """累计信息增益。"""
        return sum(e.bits_gained for e in self._events)

    def get_average_efficiency(self) -> float:
        """平均效率(bits/token)。"""
        if not self._events:
            return 0.0
        return sum(e.efficiency for e in self._events) / len(self._events)

    def get_task_type_breakdown(self) -> dict:
        """按任务类型分解比特贡献。"""
        breakdown = {}
        for e in self._events:
            if e.task_type not in breakdown:
                breakdown[e.task_type] = {"count": 0, "total_bits": 0.0}
            breakdown[e.task_type]["count"] += 1
            breakdown[e.task_type]["total_bits"] += e.bits_gained
        return breakdown

    def summary(self) -> dict:
        """系统信息论摘要。"""
        return {
            "system_id": self.system_id,
            "total_events": len(self._events),
            "codelength": round(self._codelength, 2),
            "total_bits_gained": round(self.get_total_bits(), 2),
            "avg_efficiency": round(self.get_average_efficiency(), 4),
            "task_breakdown": self.get_task_type_breakdown(),
            "success_rate": sum(1 for e in self._events if e.success) / max(len(self._events), 1),
        }

    def get_validator_marginal_value(self, n: int = 10) -> float:
        """计算最近N次验证器的边际价值(bits/seq)。

        来自Agentic Compressor论文: 验证器边际价值375 bits/seq。
        """
        recent = self._events[-n:] if len(self._events) >= n else self._events
        if not recent:
            return 0.0
        success_events = [e for e in recent if e.success]
        if not success_events:
            return 0.0
        return sum(e.bits_gained for e in success_events) / len(success_events)


def register(kernel):
    """注册比特度量syscall。"""
    kernel.register("bits.summary", lambda pid: BitsMeter().summary())
    kernel.register("bits.record", lambda pid, **kw: BitsMeter().record(**kw))
    logger.info("✅ bits metric syscall已注册")

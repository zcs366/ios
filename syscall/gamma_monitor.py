"""gamma_monitor.py — 全局评估治理（Γ矩阵监控）

来自Contagion Networks (arXiv 2606.20493):
  - 同模型传染 γ=0.14-0.30（弱）
  - 交叉模型传染 γ=0.85-1.3（强）
  - 委员会k=3降低72.4%有效传染

实时监控全系统Γ矩阵，发现级联态自动告警。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("io-s.gamma")

GAMMA_DIR = Path.home() / ".io-s" / "gamma"
GAMMA_DIR.mkdir(parents=True, exist_ok=True)

GAMMA_WARNING = 0.5    # γ>0.5 告警
GAMMA_CRITICAL = 0.85  # γ>0.85 级联态


@dataclass
class GammaReading:
    """一次Γ矩阵读数。"""
    timestamp: float = field(default_factory=time.time)
    gamma: float = 0.0
    model_pair: str = ""
    agent_count: int = 2

    def level(self) -> str:
        if self.gamma >= GAMMA_CRITICAL:
            return "critical"
        elif self.gamma >= GAMMA_WARNING:
            return "warning"
        return "normal"


class GammaMatrix:
    """Γ矩阵 — 评估偏差传染监测器。

    追踪多Agent系统中的偏差传染系数。
    发现级联态(γ>0.85)时触发告警。
    """

    def __init__(self):
        self._readings: list[GammaReading] = []
        self._load()

    def _load(self):
        path = GAMMA_DIR / "readings.jsonl"
        if path.exists():
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            d = json.loads(line)
                            self._readings.append(GammaReading(**d))
                        except (json.JSONDecodeError, TypeError):
                            continue

    def _append(self, r: GammaReading):
        path = GAMMA_DIR / "readings.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(r.__dict__) + "\n")
        self._readings.append(r)

    def record(self, gamma: float, model_pair: str,
               agent_count: int = 2) -> GammaReading:
        r = GammaReading(gamma=gamma, model_pair=model_pair,
                         agent_count=agent_count)
        self._append(r)
        return r

    def get_latest(self) -> GammaReading | None:
        return self._readings[-1] if self._readings else None

    def get_critical_count(self) -> int:
        return sum(1 for r in self._readings if r.gamma >= GAMMA_CRITICAL)

    def get_alerts(self) -> list[dict]:
        alerts = []
        for r in self._readings[-50:]:
            if r.gamma >= GAMMA_WARNING:
                alerts.append({
                    "time": r.timestamp,
                    "gamma": r.gamma,
                    "pair": r.model_pair,
                    "level": r.level(),
                    "suggestion": self._get_suggestion(r.level()),
                })
        return alerts

    def _get_suggestion(self, level: str) -> str:
        if level == "critical":
            return "立即切换为同模型异质profile，启用委员会k=3"
        elif level == "warning":
            return "建议切换为同模型异质profile，监控Γ变化"
        return "正常"

    def summary(self) -> dict:
        latest = self.get_latest()
        return {
            "total_readings": len(self._readings),
            "latest_gamma": latest.gamma if latest else 0,
            "latest_level": latest.level() if latest else "none",
            "critical_events": self.get_critical_count(),
            "warnings": len(self.get_alerts()),
            "suggestion": self._get_suggestion(latest.level()) if latest else "无数据",
        }


def register(kernel):
    matrix = GammaMatrix()

    def handle_record(pid, gamma, model_pair, agent_count=2):
        r = matrix.record(gamma, model_pair, agent_count)
        alert = r.level() != "normal"
        return {
            "gamma": r.gamma,
            "level": r.level(),
            "alert": alert,
            "suggestion": matrix._get_suggestion(r.level()),
        }

    def handle_summary(pid):
        return matrix.summary()

    kernel.register("gamma.record", handle_record)
    kernel.register("gamma.summary", handle_summary)
    logger.info("✅ Γ矩阵监控已注册")

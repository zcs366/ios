#!/usr/bin/env python3
"""conservation_law.py — 守恒律工程化验证 · 克洛诺斯之门

来自跨论文验证的守恒律 d×r×log(L)≈K_W:
  - d: 有效维度 (D₀)
  - r: 准确率/成功率
  - L: 上下文/序列长度
  - K_W: 系统容量常数

IO-S核心度量框架:
  - 被动测量: 每次Process状态变化时记录d×r×log(L)
  - 主动监测: 定期评估全系统健康度（平均偏差、趋势、alpha稳定性）
  - 交叉引用: gamma_monitor/bits_metric从本模块读取守恒状态
  - 自适应alpha: 从实际测量数据自动估计α（经验值~10.0）

克洛诺斯启示:
  "P2-5守恒律工程化是1个月后最重要的事——不是边角模块，是IO-S的数学骨架。"
"""
from __future__ import annotations

import json
import logging
import math
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("io-s.conservation")

MEASURE_DIR = Path.home() / ".io-s" / "conservation"
MEASURE_DIR.mkdir(parents=True, exist_ok=True)

# === 常数 ===
DEFAULT_ALPHA = 10.0       # 经验常数
WINDOW_SIZE = 100           # 活跃测量窗口大小
HEALTH_THRESHOLD = 0.20     # 健康阈值（平均偏差>0.2=亚健康）
ALPHA_MIN_SAMPLES = 10      # 自适应alpha所需的最少样本量


@dataclass
class Measurement:
    """一次守恒律测量。"""
    d: float
    r: float
    L: float
    K_W: float
    alpha: float
    source: str = "manual"       # 测量来源: manual/process/gamma/bits
    plan_id: str = ""            # 关联的plan ID
    timestamp: float = field(default_factory=time.time)

    def deviation(self) -> float:
        """与理想K_W的偏差。"""
        expected = self.alpha * math.log2(self.L) if self.L > 1 else 0
        if expected == 0:
            return 0.0
        return abs(self.K_W - expected) / expected


@dataclass
class Trend:
    """趋势分析结果。"""
    d_trend: float           # 正=维度在增长，负=在缩减
    r_trend: float           # 正确率变化率
    K_W_trend: float         # 容量变化率
    alpha_stability: float   # alpha稳定度（标准差/均值，越小越稳）
    healthy: bool
    message: str

    def to_dict(self) -> dict:
        return {
            "d_trend": round(self.d_trend, 4),
            "r_trend": round(self.r_trend, 4),
            "K_W_trend": round(self.K_W_trend, 4),
            "alpha_stability": round(self.alpha_stability, 4),
            "healthy": self.healthy,
            "message": self.message,
        }


class ConservationMonitor:
    """守恒律监测器 — IO-S核心度量框架。

    三层:
      1. 被动层: measure()/from_process() — 每次状态变化记录
      2. 主动层: detect_alpha()/detect_trend() — 定期自分析
      3. 仪表层: health_dashboard() — 全系统健康报告
    """

    def __init__(self, window_size: int = WINDOW_SIZE):
        self._window = deque(maxlen=window_size)
        self._all_measurements: list[Measurement] = []
        self._detected_alpha: float | None = None
        self._load()

    # ── 持久化 ──

    def _load(self):
        path = MEASURE_DIR / "measurements.jsonl"
        if path.exists():
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            d = json.loads(line)
                            m = Measurement(**d)
                            self._window.append(m)
                            self._all_measurements.append(m)
                        except Exception:
                            continue
        path2 = MEASURE_DIR / "detected_alpha.json"
        if path2.exists():
            try:
                self._detected_alpha = json.loads(path2.read_text()).get("alpha")
            except Exception:
                pass

    def _append(self, m: Measurement):
        path = MEASURE_DIR / "measurements.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps({
                "d": m.d, "r": m.r, "L": m.L, "K_W": m.K_W,
                "alpha": m.alpha, "source": m.source,
                "plan_id": m.plan_id, "timestamp": m.timestamp,
            }, ensure_ascii=False) + "\n")
        self._window.append(m)
        self._all_measurements.append(m)

    # ── 被动层: 测量接口 ──

    def measure(self, d: float, r: float, L: float,
                source: str = "manual", plan_id: str = "") -> Measurement:
        """单次守恒律测量。"""
        alpha = self._detected_alpha or DEFAULT_ALPHA
        logL = math.log2(L) if L > 1 else 1
        K_W = d * r * logL
        m = Measurement(d=d, r=r, L=L, K_W=round(K_W, 4),
                        alpha=alpha, source=source, plan_id=plan_id)
        self._append(m)
        return m

    def from_process(self, plan_count: int, success_rate: float,
                     context_length: int, plan_id: str = "") -> Measurement:
        """从Process状态推算守恒律参数。"""
        d = max(1, plan_count)
        r = max(0.01, min(1.0, success_rate))
        L = max(100, context_length)
        return self.measure(d, r, L, source="process", plan_id=plan_id)

    # ── 主动层(1): alpha自适应检测 ──

    def detect_alpha(self) -> float:
        """从已有测量数据自动估计alpha。

        方法: 对每条测量,K_W = d×r×log(L) → alpha = K_W / log(L)
        取中位数作为鲁棒估计。
        """
        if len(self._all_measurements) < ALPHA_MIN_SAMPLES:
            return self._detected_alpha or DEFAULT_ALPHA

        alphas = []
        for m in self._all_measurements:
            logL = math.log2(m.L) if m.L > 1 else 1
            if logL > 1 and m.d > 0 and m.r > 0:
                alpha_i = m.K_W / logL
                if 1 < alpha_i < 50:  # 过滤异常值
                    alphas.append(alpha_i)

        if len(alphas) < ALPHA_MIN_SAMPLES:
            return self._detected_alpha or DEFAULT_ALPHA

        # 中位数（抗离群点）+ 均值（统计效率）取平均
        median_alpha = statistics.median(alphas)
        mean_alpha = statistics.mean(alphas)
        detected = round((median_alpha + mean_alpha) / 2, 2)

        # 裁剪到合理范围
        detected = max(5.0, min(20.0, detected))
        self._detected_alpha = detected

        # 持久化
        path = MEASURE_DIR / "detected_alpha.json"
        path.write_text(json.dumps({
            "alpha": detected,
            "samples": len(alphas),
            "median": round(median_alpha, 2),
            "mean": round(mean_alpha, 2),
            "timestamp": time.time(),
        }, indent=2, ensure_ascii=False))

        logger.info(f"🧬 自适应alpha: {detected} ({len(alphas)} samples)")
        return detected

    # ── 主动层(2): 趋势检测 ──

    def detect_trend(self, window: int = 20) -> Trend:
        """从最近N次测量检测趋势。

        用简单的线性回归(最小二乘) 拟合 d/r/K_W 随测量序号的变化。
        """
        recent = list(self._window)[-window:] if len(self._window) > window else list(self._window)
        if len(recent) < 5:
            return Trend(d_trend=0, r_trend=0, K_W_trend=0,
                         alpha_stability=0, healthy=True,
                         message="样本不足，无法检测趋势")

        n = len(recent)
        xs = list(range(n))
        def _slope(values):
            sx = sum(xs); sy = sum(values); sxy = sum(x*v for x,v in zip(xs, values))
            sxx = sum(x*x for x in xs)
            denom = n * sxx - sx * sx
            if denom == 0:
                return 0.0
            return (n * sxy - sx * sy) / denom

        d_trend = _slope([m.d for m in recent])
        r_trend = _slope([m.r for m in recent])
        kw_trend = _slope([m.K_W for m in recent])

        # alpha稳定度：检测到的alpha的标准差/均值
        alpha_stability = 0.0
        if len(self._all_measurements) >= ALPHA_MIN_SAMPLES:
            alphas_in_window = []
            for m in recent:
                logL = math.log2(m.L) if m.L > 1 else 1
                if logL > 1 and m.d > 0 and m.r > 0:
                    alphas_in_window.append(m.K_W / logL)
            if alphas_in_window and statistics.mean(alphas_in_window) > 0:
                alpha_stability = statistics.stdev(alphas_in_window) / statistics.mean(alphas_in_window)

        # 判断健康度
        issues = []
        if abs(d_trend) > 0.5:
            issues.append(f"维度{d_trend:+.2f}/step（变化较快）")
        if r_trend < -0.01:
            issues.append(f"正确率{r_trend:+.4f}/step（下降中）")
        if alpha_stability > 0.3:
            issues.append(f"alpha波动{alpha_stability:.2f}（不稳定）")

        healthy = len(issues) == 0
        message = "健康" if healthy else "注意: " + "; ".join(issues)

        return Trend(d_trend=d_trend, r_trend=r_trend, K_W_trend=kw_trend,
                     alpha_stability=round(alpha_stability, 4),
                     healthy=healthy, message=message)

    # ── 仪表层: 全系统健康报告 ──

    def health_dashboard(self) -> dict:
        """全系统守恒律健康仪表盘。"""
        latest = self.get_latest()
        avg_dev = self.get_average_deviation()
        alpha = self._detected_alpha or DEFAULT_ALPHA
        trend = self.detect_trend()
        total = len(self._all_measurements)
        window = len(self._window)

        return {
            "alpha": {
                "detected": alpha,
                "default": DEFAULT_ALPHA,
                "auto_tuned": self._detected_alpha is not None,
                "samples": total,
            },
            "measurements": {
                "total": total,
                "window_active": window,
                "latest_KW": latest.K_W if latest else 0,
                "latest_d": latest.d if latest else 0,
                "latest_r": latest.r if latest else 0,
                "latest_L": latest.L if latest else 0,
            },
            "health": {
                "avg_deviation": round(avg_dev, 4),
                "healthy": avg_dev < HEALTH_THRESHOLD,
                "threshold": HEALTH_THRESHOLD,
            },
            "trend": trend.to_dict(),
            "sources": self._source_distribution(),
        }

    def _source_distribution(self) -> dict:
        """各来源测量数量分布。"""
        dist: dict[str, int] = {}
        for m in self._all_measurements:
            dist[m.source] = dist.get(m.source, 0) + 1
        return dist

    # ── 原有接口（保持兼容） ──

    def get_latest(self) -> Measurement | None:
        return self._window[-1] if self._window else None

    def get_average_deviation(self) -> float:
        if not self._all_measurements:
            return 0.0
        return sum(m.deviation() for m in self._all_measurements) / len(self._all_measurements)

    def summary(self) -> dict:
        latest = self.get_latest()
        return {
            "total_measurements": len(self._all_measurements),
            "latest_KW": latest.K_W if latest else 0,
            "latest_d": latest.d if latest else 0,
            "latest_r": latest.r if latest else 0,
            "latest_L": latest.L if latest else 0,
            "alpha": self._detected_alpha or DEFAULT_ALPHA,
            "alpha_auto": self._detected_alpha is not None,
            "avg_deviation": round(self.get_average_deviation(), 4),
            "healthy": self.get_average_deviation() < HEALTH_THRESHOLD,
        }


def register(kernel):
    cm = ConservationMonitor()

    # ── 被动测量 ──
    def handle_measure(pid, d, r, L, source="manual", plan_id=""):
        m = cm.measure(d, r, L, source=source, plan_id=plan_id)
        return {"K_W": m.K_W, "deviation": m.deviation()}

    def handle_from_process(pid, plan_count, success_rate, context_length, plan_id=""):
        m = cm.from_process(plan_count, success_rate, context_length, plan_id=plan_id)
        return {"K_W": m.K_W, "d": m.d, "r": m.r, "L": m.L, "deviation": m.deviation()}

    # ── 主动检测 ──
    def handle_detect_alpha(pid):
        alpha = cm.detect_alpha()
        return {"alpha": alpha, "auto_tuned": cm._detected_alpha is not None}

    def handle_detect_trend(pid, window=20):
        trend = cm.detect_trend(window=window)
        return trend.to_dict()

    # ── 仪表层 ──
    def handle_dashboard(pid):
        return cm.health_dashboard()

    def handle_summary(pid):
        return cm.summary()

    # ── 注册全部syscall ──
    kernel.register("conservation.measure", handle_measure)
    kernel.register("conservation.from_process", handle_from_process)
    kernel.register("conservation.detect_alpha", handle_detect_alpha)
    kernel.register("conservation.detect_trend", handle_detect_trend)
    kernel.register("conservation.dashboard", handle_dashboard)
    kernel.register("conservation.summary", handle_summary)

    # 注册为元监控模块（供gamma_monitor/process读取）
    kernel._conservation_monitor = cm

    logger.info("✅ 守恒律核心度量框架已注册（含自适应alpha+趋势检测+仪表盘）")

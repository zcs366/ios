#!/usr/bin/env python3
"""
IO-S monitor/baseline — D₀基线跟踪（EWMA滚动更新）

设计：
  init_baseline() — 第一次D₀读数→初始基线
  update_baseline(d0_reading) — EWMA α=0.3 滚动更新
  report_baseline() — 当前基线 ± 波动范围

IAH未就绪时使用fallback值，基线照样可用。
"""

import json
from pathlib import Path
from datetime import datetime, timezone

_ts = lambda: datetime.now(timezone.utc).isoformat()
BASELINE_PATH = Path.home() / ".io-s" / "d0_baseline.json"


def init_baseline(d0_reading: dict = None) -> dict:
    """初始化D₀基线。

    如果已有历史基线，读取并继续。
    """
    d0 = d0_reading or {"d0_mean": 2.0, "d0_std": 0.5, "model": "gpt2", "source": "fallback"}

    if BASELINE_PATH.exists():
        try:
            baseline = json.loads(BASELINE_PATH.read_text())
            return baseline
        except json.JSONDecodeError:
            pass

    baseline = {
        "d0_mean": d0.get("d0_mean", 2.0),
        "d0_std": d0.get("d0_std", 0.5),
        "model": d0.get("model", "gpt2"),
        "readings": 1,
        "last_updated": _ts(),
        "history": [{
            "d0_mean": d0.get("d0_mean", 2.0),
            "source": d0.get("source", "fallback"),
            "at": _ts(),
        }],
    }

    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(baseline, ensure_ascii=False, indent=2))
    return baseline


def update_baseline(d0_reading: dict) -> dict:
    """EWMA滚动更新D₀基线。α=0.3。

    滚动窗口效应：旧读数权重衰减，新读数权重0.3。
    """
    baseline = init_baseline()  # 加载现有基线
    new_d0 = d0_reading.get("d0_mean", baseline["d0_mean"])
    alpha = 0.3

    # EWMA更新
    baseline["d0_mean"] = round(alpha * new_d0 + (1 - alpha) * baseline["d0_mean"], 3)
    baseline["d0_std"] = round(d0_reading.get("d0_std", baseline.get("d0_std", 0.5)), 3)
    baseline["readings"] += 1
    baseline["last_updated"] = _ts()
    baseline["last_source"] = d0_reading.get("source", "unknown")

    # 保留最近10条历史
    hist = baseline.setdefault("history", [])
    hist.append({"d0_mean": new_d0, "source": d0_reading.get("source", "?"), "at": _ts()})
    if len(hist) > 10:
        baseline["history"] = hist[-10:]

    BASELINE_PATH.write_text(json.dumps(baseline, ensure_ascii=False, indent=2))
    return baseline


def report_baseline() -> dict:
    """报告当前D₀基线状态。

    Returns: {d0_mean, d0_std, readings, status, anomaly_hint}
    """
    baseline = init_baseline()  # 加载现有基线

    d0 = baseline.get("d0_mean", 2.0)
    std = baseline.get("d0_std", 0.5)

    # 简易异常判定（IAH刀07的TOP3异常对应）
    anomaly = None
    if d0 < 0.5:
        anomaly = "dimension_collapse"  # D₀过低→维度崩溃
    elif d0 > 10.0:
        anomaly = "head_drift"  # D₀过高→头漂移
    elif std > 2.0:
        anomaly = "abnormal_concentration"  # 标准差过大→异常集中

    return {
        "d0_mean": d0,
        "d0_std": std,
        "readings": baseline.get("readings", 0),
        "model": baseline.get("model", "gpt2"),
        "last_source": baseline.get("last_source", "fallback"),
        "status": "⚠️ " + anomaly if anomaly else "✅ 正常",
        "anomaly_hint": anomaly,
        "last_updated": baseline.get("last_updated", ""),
    }

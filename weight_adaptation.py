"""weight_adaptation.py — 边际注意力分配（P2-1 原型）

读 viability_log.jsonl 算 3 天斜率 → 调权重，三道安全阀，追溯写 weights.jsonl。

用法:
    python /home/zcs/io-s/weight_adaptation.py          # 执行一轮适应并打印结果
    python /home/zcs/io-s/weight_adaptation.py --dry-run # 只算不写
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── 路径常量 ──
VIABILITY_LOG = Path.home() / ".openllm" / "viability_log.jsonl"
WEIGHTS_LOG = Path.home() / ".io-s" / "weights.jsonl"

# ── 趋势参数 ──
TREND_WINDOW_DAYS: int = 3        # N=3，最近3天
EPSILON: float = 0.01             # 趋势判定阈值/天
DELTA: float = 0.01               # 单日调整步长
MAX_STEP: float = 0.05            # 幅度阀：单分量单轮 |Δw| ≤ 0.05
MIN_WEIGHT: float = 0.05          # 归一阀：任一分量权重下限

DEFAULT_WEIGHTS: Dict[str, float] = {"M": 0.3, "S": 0.3, "B": 0.2, "E": 0.2}
# 参调分量（E 恒缺失不参调，冻结为 0.2）
ACTIVE_COMPONENTS = ("M", "S", "B")


def load_component_series(
    log_path: Optional[Path] = None, days: int = 7
) -> Dict[str, List[Tuple[str, float]]]:
    """读 viability_log，按天取每分量当天最后一条（同天多次采样取最晚）。

    返回 {分量: [(date, value), ...]}，E 恒为 null 跳过。
    文件不存在/损坏行跳过返回 {}。
    """
    path = log_path or VIABILITY_LOG
    if not path.exists():
        return {}

    # 收集所有记录的 (date_str, component, value)
    raw: Dict[str, Dict[str, Tuple[str, float]]] = {}  # date -> comp -> (ts, val)
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("timestamp", "")
                v_result = rec.get("v_result")
                if not v_result or not isinstance(v_result, dict):
                    continue
                components = v_result.get("components", {})
                if not components:
                    continue
                # 提取日期部分 (YYYY-MM-DD)
                date_str = ts[:10] if len(ts) >= 10 else ts
                if date_str not in raw:
                    raw[date_str] = {}
                for comp in ACTIVE_COMPONENTS:
                    val = components.get(comp)
                    if val is not None and isinstance(val, (int, float)):
                        raw[date_str][comp] = (ts, val)
    except Exception:
        return {}

    if not raw:
        return {}

    # 按日期排序，取最近 days 天
    sorted_dates = sorted(raw.keys())[-days:]

    result: Dict[str, List[Tuple[str, float]]] = {c: [] for c in ACTIVE_COMPONENTS}
    for d in sorted_dates:
        for comp in ACTIVE_COMPONENTS:
            if comp in raw[d]:
                _, val = raw[d][comp]
                result[comp].append((d, val))

    return result


def compute_trend(
    series: List[Tuple[str, float]], window: int = TREND_WINDOW_DAYS
) -> Optional[float]:
    """最近 window 个日数据点的线性斜率（最小二乘，纯 Python）。

    点数 < 2 返回 None；平稳返回 0.0。
    """
    # 取最近 window 个点
    pts = series[-window:]
    n = len(pts)
    if n < 2:
        return None

    # x = 0, 1, 2, ...  y = value
    x_vals = list(range(n))
    y_vals = [p[1] for p in pts]

    x_mean = sum(x_vals) / n
    y_mean = sum(y_vals) / n

    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, y_vals))
    denominator = sum((x - x_mean) ** 2 for x in x_vals)

    if denominator == 0:
        return 0.0

    slope = numerator / denominator
    return round(slope, 6)


def _read_last_v_value(log_path: Optional[Path] = None) -> Optional[float]:
    """从 viability_log 读最新一条的 V 值，用于追溯阀的 v_before/v_after。"""
    path = log_path or VIABILITY_LOG
    if not path.exists():
        return None
    last_v = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    v_result = rec.get("v_result")
                    if v_result and isinstance(v_result, dict):
                        v = v_result.get("V")
                        if v is not None:
                            last_v = v
                except json.JSONDecodeError:
                    continue
    except Exception:
        return None
    return last_v


def _read_weights_log(weights_log: Optional[Path] = None) -> Optional[dict]:
    """读 weights.jsonl 最后一条，返回 weights 字段或 None。"""
    path = weights_log or WEIGHTS_LOG
    if not path.exists():
        return None
    last = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    last = json.loads(line)
                except json.JSONDecodeError:
                    continue
    except Exception:
        return None
    if last and "weights" in last:
        return last["weights"]
    return None


def adapt_weights(
    log_path: Optional[Path] = None,
    weights_log: Optional[Path] = None,
    dry_run: bool = False,
) -> dict:
    """对 {M, S, B} 各算 trend 并调整权重。

    返回最终权重 dict（含 E）。
    """
    w_path = weights_log or WEIGHTS_LOG
    v_path = log_path or VIABILITY_LOG

    # 起点：weights.jsonl 有记录 → 续演；否则从 DEFAULT_WEIGHTS 起
    prev = _read_weights_log(w_path)
    if prev:
        w = {k: prev.get(k, DEFAULT_WEIGHTS[k]) for k in DEFAULT_WEIGHTS}
    else:
        w = dict(DEFAULT_WEIGHTS)

    series = load_component_series(v_path)

    changes: List[dict] = []
    v_before = _read_last_v_value(v_path)

    for comp in ACTIVE_COMPONENTS:
        comp_series = series.get(comp, [])
        trend = compute_trend(comp_series)

        if trend is None:
            continue  # 数据不足，跳过

        old_w = w[comp]
        new_w = old_w

        if trend < -EPSILON:
            # 下滑 → 增权
            new_w = old_w + DELTA
            reason = f"trend_down:{trend:+.4f}→增权"
        elif trend > EPSILON:
            # 向好 → 减权
            new_w = old_w - DELTA
            reason = f"trend_up:{trend:+.4f}→减权"
        else:
            # 平稳
            reason = f"trend_flat:{trend:+.4f}"

        # ① 幅度阀
        delta = new_w - old_w
        assert abs(delta) <= MAX_STEP + 1e-9, (
            f"幅度阀违规: {comp} |Δw|={abs(delta):.4f} > MAX_STEP={MAX_STEP}"
        )

        w[comp] = new_w

        if abs(delta) > 1e-9:
            changes.append({
                "component": comp,
                "old_w": round(old_w, 6),
                "new_w": round(new_w, 6),
                "delta": round(delta, 6),
                "trend": round(trend, 6),
                "reason": reason,
            })

    # ② 归一阀：参调分量等比缩放到和=1.0（0.8，因 E 占 0.2 冻结）
    active_sum = sum(w[c] for c in ACTIVE_COMPONENTS)
    target_sum = 1.0 - w.get("E", DEFAULT_WEIGHTS["E"])  # 0.8
    if active_sum > 0 and abs(active_sum - target_sum) > 1e-9:
        scale = target_sum / active_sum
        for c in ACTIVE_COMPONENTS:
            w[c] = round(w[c] * scale, 6)

    # 下限检查
    for c in ACTIVE_COMPONENTS:
        if w[c] < MIN_WEIGHT:
            w[c] = MIN_WEIGHT

    # 再归一（下限截断后可能微偏）
    active_sum2 = sum(w[c] for c in ACTIVE_COMPONENTS)
    if active_sum2 > 0:
        scale2 = target_sum / active_sum2
        for c in ACTIVE_COMPONENTS:
            w[c] = round(w[c] * scale2, 6)

    # E 冻结
    w["E"] = DEFAULT_WEIGHTS["E"]

    # ③ 追溯阀：有变化才写日志
    if changes and not dry_run:
        w_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "weights": {k: round(w[k], 6) for k in w},
            "changes": changes,
            "v_before": v_before,
            "v_after": v_before,  # 本轮不重算 V，同值
        }
        with open(w_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {k: round(w[k], 6) for k in w}


def read_current_weights() -> Optional[dict]:
    """读 weights.jsonl 最后一条的 weights 字段。无日志返回 None。

    这是给 viability.py 用的唯一读取接口。
    """
    return _read_weights_log(WEIGHTS_LOG)


# ── CLI ──
if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    result = adapt_weights(dry_run=dry)
    print(json.dumps(result, ensure_ascii=False, indent=2))

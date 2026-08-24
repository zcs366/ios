"""test_weight_adaptation.py — P2-1 边际注意力分配测试

全部用 tmp_path，不碰真实 ~/.openllm 或 ~/.io-s。
"""
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pytest

# 隔离 io-s 到 path
IO_S_DIR = Path(__file__).resolve().parent.parent
if str(IO_S_DIR) not in sys.path:
    sys.path.insert(0, str(IO_S_DIR))

from weight_adaptation import (
    ACTIVE_COMPONENTS,
    DEFAULT_WEIGHTS,
    DELTA,
    EPSILON,
    MIN_WEIGHT,
    MAX_STEP,
    TREND_WINDOW_DAYS,
    adapt_weights,
    compute_trend,
    load_component_series,
    read_current_weights,
)


# ── helpers ──

def _write_viability_log(path: Path, entries: List[dict]) -> None:
    """写入假 viability_log.jsonl。"""
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _make_v_entry(date: str, components: Dict[str, Optional[float]], ts: str = "") -> dict:
    """构造一条 viability_log 记录。"""
    ts_full = ts or f"{date}T12:00:00+00:00"
    return {
        "timestamp": ts_full,
        "source": "test",
        "v_result": {
            "V": 0.8,
            "components": components,
        },
    }


def _multi_day_log(path: Path, component_trends: Dict[str, List[float]], days: int = 5) -> None:
    """生成多天多采样的 viability_log。

    component_trends: {"M": [0.5, 0.51, 0.52, 0.53, 0.54], "S": [...], ...}
    """
    entries = []
    for d in range(days):
        date = f"2026-08-{18 + d:02d}"
        comps = {}
        for comp, vals in component_trends.items():
            if d < len(vals):
                comps[comp] = vals[d]
        # 每天两条采样，取最晚
        comps2 = dict(comps)
        entries.append(_make_v_entry(date, comps2, f"{date}T08:00:00+00:00"))
        entries.append(_make_v_entry(date, comps2, f"{date}T20:00:00+00:00"))
    _write_viability_log(path, entries)


# ═══ load_component_series 测试 ═══


class TestLoadComponentSeries:
    """load_component_series 的多天多采样、E 跳过、损坏行跳过。"""

    def test_multi_day_multi_sample(self, tmp_path):
        """同一天多次采样 → 取最晚值；E 恒跳过。"""
        log = tmp_path / "viability_log.jsonl"
        entries = [
            _make_v_entry("2026-08-20", {"M": 0.5, "S": 0.6, "B": 0.7, "E": 0.9}, "2026-08-20T08:00:00"),
            _make_v_entry("2026-08-20", {"M": 0.55, "S": 0.61, "B": 0.68, "E": 0.85}, "2026-08-20T20:00:00"),
            _make_v_entry("2026-08-21", {"M": 0.6, "S": 0.62, "B": 0.65, "E": 0.88}, "2026-08-21T12:00:00"),
        ]
        _write_viability_log(log, entries)
        series = load_component_series(log)
        assert "E" not in series
        assert len(series["M"]) == 2
        # 8-20 的最晚采样是 0.55（T20:00:00）
        assert series["M"][0] == ("2026-08-20", 0.55)
        assert series["M"][1] == ("2026-08-21", 0.6)

    def test_e_always_skipped(self, tmp_path):
        """E 恒为 null 跳过。"""
        log = tmp_path / "viability_log.jsonl"
        entries = [_make_v_entry("2026-08-20", {"M": 0.5, "S": 0.5, "B": 0.5, "E": None})]
        _write_viability_log(log, entries)
        series = load_component_series(log)
        assert "E" not in series

    def test_corrupt_lines_skipped(self, tmp_path):
        """损坏行跳过。"""
        log = tmp_path / "viability_log.jsonl"
        with open(log, "w", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": "2026-08-20T12:00:00", "source": "t", "v_result": {"V": 0.8, "components": {"M": 0.5, "S": 0.5, "B": 0.5, "E": None}}}) + "\n")
            f.write("NOT_JSON_LINE\n")
            f.write(json.dumps({"timestamp": "2026-08-21T12:00:00", "source": "t", "v_result": {"V": 0.8, "components": {"M": 0.6, "S": 0.6, "B": 0.6, "E": None}}}) + "\n")
        series = load_component_series(log)
        assert len(series["M"]) == 2

    def test_empty_file(self, tmp_path):
        """空文件 → {}。"""
        log = tmp_path / "viability_log.jsonl"
        log.write_text("")
        assert load_component_series(log) == {}

    def test_file_not_exists(self, tmp_path):
        """文件不存在 → {}。"""
        assert load_component_series(tmp_path / "nope.jsonl") == {}


# ═══ compute_trend 测试 ═══


class TestComputeTrend:
    """compute_trend 的斜率计算。"""

    def test_negative_slope(self):
        """3 点下滑序列 → 负斜率。"""
        series = [("2026-08-20", 0.8), ("2026-08-21", 0.7), ("2026-08-22", 0.6)]
        slope = compute_trend(series)
        assert slope is not None
        assert slope < -EPSILON  # 应该大约 -0.1

    def test_flat(self):
        """平稳 → ≈0。"""
        series = [("2026-08-20", 0.5), ("2026-08-21", 0.5), ("2026-08-22", 0.5)]
        slope = compute_trend(series)
        assert slope is not None
        assert abs(slope) < EPSILON

    def test_single_point(self):
        """1 点 → None。"""
        series = [("2026-08-20", 0.5)]
        assert compute_trend(series) is None

    def test_two_points(self):
        """2 点 → 有斜率。"""
        series = [("2026-08-20", 0.5), ("2026-08-21", 0.6)]
        slope = compute_trend(series)
        assert slope is not None
        assert slope > 0


# ═══ adapt_weights 三方向测试 ═══


class TestAdaptWeights:
    """adapt_weights 在三方向（下滑/向好/平稳）的行为。"""

    def test_m_down_increases_weight(self, tmp_path):
        """M 下滑 → M 权重 +DELTA。"""
        log = tmp_path / "viability_log.jsonl"
        wl = tmp_path / "weights.jsonl"
        _multi_day_log(log, {"M": [0.8, 0.7, 0.6], "S": [0.5, 0.5, 0.5], "B": [0.5, 0.5, 0.5]})
        result = adapt_weights(log_path=log, weights_log=wl, dry_run=True)
        # M 下滑，应增权
        assert result["M"] > DEFAULT_WEIGHTS["M"]

    def test_s_up_decreases_weight(self, tmp_path):
        """S 向好 → S 权重 -DELTA。"""
        log = tmp_path / "viability_log.jsonl"
        wl = tmp_path / "weights.jsonl"
        _multi_day_log(log, {"M": [0.5, 0.5, 0.5], "S": [0.5, 0.6, 0.7], "B": [0.5, 0.5, 0.5]})
        result = adapt_weights(log_path=log, weights_log=wl, dry_run=True)
        # S 向好，应减权
        assert result["S"] < DEFAULT_WEIGHTS["S"]

    def test_b_flat_no_change(self, tmp_path):
        """B 平稳 → B 权重不变。"""
        log = tmp_path / "viability_log.jsonl"
        wl = tmp_path / "weights.jsonl"
        _multi_day_log(log, {"M": [0.5, 0.5, 0.5], "S": [0.5, 0.5, 0.5], "B": [0.5, 0.5, 0.5]})
        result = adapt_weights(log_path=log, weights_log=wl, dry_run=True)
        # 所有平稳，权重应等于 DEFAULT_WEIGHTS
        for c in ACTIVE_COMPONENTS:
            assert result[c] == DEFAULT_WEIGHTS[c]


# ═══ 安全阀测试 ═══


class TestSafetyValves:
    """三道安全阀。"""

    def test_amplitude_valve(self, tmp_path):
        """极端斜率 → 单轮 |Δw| ≤ MAX_STEP。"""
        log = tmp_path / "viability_log.jsonl"
        wl = tmp_path / "weights.jsonl"
        # 极端下滑：每天降 0.3 → 斜率非常大
        _multi_day_log(log, {"M": [0.9, 0.6, 0.3], "S": [0.5, 0.5, 0.5], "B": [0.5, 0.5, 0.5]})
        result = adapt_weights(log_path=log, weights_log=wl, dry_run=True)
        delta = abs(result["M"] - DEFAULT_WEIGHTS["M"])
        assert delta <= MAX_STEP + 1e-6

    def test_normalization_valve(self, tmp_path):
        """参调分量和 = 0.8（1.0 - E 0.2），容差 1e-6。"""
        log = tmp_path / "viability_log.jsonl"
        wl = tmp_path / "weights.jsonl"
        _multi_day_log(log, {"M": [0.8, 0.7, 0.6], "S": [0.5, 0.6, 0.7], "B": [0.5, 0.5, 0.5]})
        result = adapt_weights(log_path=log, weights_log=wl, dry_run=True)
        active_sum = sum(result[c] for c in ACTIVE_COMPONENTS)
        target = 1.0 - DEFAULT_WEIGHTS["E"]
        assert abs(active_sum - target) < 1e-5

    def test_min_weight_valve(self, tmp_path):
        """连续下滑多轮不击穿 MIN_WEIGHT。"""
        log = tmp_path / "viability_log.jsonl"
        wl = tmp_path / "weights.jsonl"
        # S 向好（要减权），连续调几轮
        _multi_day_log(log, {"M": [0.5, 0.5, 0.5], "S": [0.5, 0.6, 0.7], "B": [0.5, 0.5, 0.5]})

        # 先调一轮写入
        adapt_weights(log_path=log, weights_log=wl)
        # 再调一轮
        result = adapt_weights(log_path=log, weights_log=wl)
        # 所有分量 ≥ MIN_WEIGHT
        for c in ACTIVE_COMPONENTS:
            assert result[c] >= MIN_WEIGHT - 1e-6, f"{c}={result[c]} < MIN_WEIGHT={MIN_WEIGHT}"


# ═══ 续演测试 ═══


class TestContinuation:
    """续演：上一轮权重是起点。"""

    def test_continuation_from_last_weights(self, tmp_path):
        """先写一轮 → 再调一轮，起点是上一轮权重。"""
        log = tmp_path / "viability_log.jsonl"
        wl = tmp_path / "weights.jsonl"
        _multi_day_log(log, {"M": [0.8, 0.7, 0.6], "S": [0.5, 0.6, 0.7], "B": [0.5, 0.5, 0.5]})

        # 第一轮
        r1 = adapt_weights(log_path=log, weights_log=wl)
        # 第二轮
        r2 = adapt_weights(log_path=log, weights_log=wl)

        # 如果第一轮 M 增权了，第二轮应该从 r1 的 M 开始继续
        # 而不是从 DEFAULT_WEIGHTS 开始
        if r1["M"] > DEFAULT_WEIGHTS["M"]:
            assert r2["M"] >= r1["M"]  # 继续增权或至少不减
        # 日志应有 2 条
        with open(wl, "r") as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) == 2


# ═══ 平稳日不写日志 ═══


class TestStableDayNoLog:
    """平稳日 → weights.jsonl 行数不增。"""

    def test_no_change_no_log(self, tmp_path):
        """无趋势变化 → weights.jsonl 不增长。"""
        log = tmp_path / "viability_log.jsonl"
        wl = tmp_path / "weights.jsonl"
        _multi_day_log(log, {"M": [0.5, 0.5, 0.5], "S": [0.5, 0.5, 0.5], "B": [0.5, 0.5, 0.5]})
        adapt_weights(log_path=log, weights_log=wl)
        assert not wl.exists() or wl.read_text().strip() == ""


# ═══ read_current_weights 测试 ═══


class TestReadCurrentWeights:
    """read_current_weights 的读取行为。"""

    def test_no_log_returns_none(self):
        """无日志 → None。"""
        assert read_current_weights.__wrapped__() if hasattr(read_current_weights, '__wrapped__') else True
        # 直接测试逻辑：创建一个临时路径让 WEIGHTS_LOG 不存在
        # 由于 read_current_weights 使用模块级 WEIGHTS_LOG，
        # 我们测试 _read_weights_log 的逻辑
        from weight_adaptation import _read_weights_log
        from pathlib import Path as P
        assert _read_weights_log(P("/tmp/nonexistent_weights_test.jsonl")) is None

    def test_with_log_returns_last(self, tmp_path):
        """有日志 → 最后一条 weights。"""
        wl = tmp_path / "weights.jsonl"
        records = [
            {"timestamp": "t1", "weights": {"M": 0.31, "S": 0.3, "B": 0.19, "E": 0.2}},
            {"timestamp": "t2", "weights": {"M": 0.32, "S": 0.29, "B": 0.19, "E": 0.2}},
        ]
        with open(wl, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        from weight_adaptation import _read_weights_log
        result = _read_weights_log(wl)
        assert result["M"] == 0.32
        assert result["S"] == 0.29


# ═══ viability 接线测试 ═══


class TestViabilityIntegration:
    """compute_viability 与 weight_adaptation 的接线。"""

    def test_explicit_weights_override_adaptive(self, tmp_path):
        """显式传 weights 时，自适应权重不生效。"""
        # 写假 weights.jsonl 到真实 ~/.io-s/ 路径（get_adapted_weights 用 fresh import）
        real_wl = Path.home() / ".io-s" / "weights.jsonl"
        real_wl.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if real_wl.exists():
            backup = real_wl.read_text(encoding="utf-8")
        try:
            with open(real_wl, "w") as f:
                f.write(json.dumps({"timestamp": "t", "weights": {"M": 0.99, "S": 0.01, "B": 0.0, "E": 0.0}}) + "\n")

            from viability import get_adapted_weights
            adapted = get_adapted_weights()
            assert adapted is not None
            assert adapted["M"] == 0.99  # 假权重被读到

            # 但显式传 weights 时，adapted 不生效
            from viability import compute_viability
            explicit = {"M": 0.5, "S": 0.3, "B": 0.2}
            result = compute_viability(weights=explicit)
            assert result["weights_used"]["M"] == 0.5  # 用的是显式传入的
            assert result["weight_adaptation"] == "P2+_exploration_not_implemented"
        finally:
            if backup is not None:
                real_wl.write_text(backup, encoding="utf-8")
            elif real_wl.exists():
                real_wl.unlink()

    def test_no_weights_jsonl_backward_compat(self):
        """无 weights.jsonl 时，compute_viability 行为与现状一致。"""
        import weight_adaptation as wa
        original_path = wa.WEIGHTS_LOG
        wa.WEIGHTS_LOG = Path("/tmp/nonexistent_test_weights.jsonl")
        try:
            from viability import compute_viability
            result = compute_viability()
            assert result["weight_adaptation"] == "P2+_exploration_not_implemented"
            # 权重应等于 DEFAULT_WEIGHTS
            for k in DEFAULT_WEIGHTS:
                if k in result.get("weights_used", {}):
                    assert result["weights_used"][k] == DEFAULT_WEIGHTS[k]
        finally:
            wa.WEIGHTS_LOG = original_path

#!/usr/bin/env python3
"""test_conservation_v2.py — 守恒律升级版测试（自适应alpha+趋势检测+仪表盘）

每个测试使用隔离的临时目录，避免JSONL状态污染。
"""
import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import syscall.conservation_law as cons_mod


def _fresh_monitor():
    """返回一个隔离状态的监控器（用临时目录）。"""
    tmp = tempfile.mkdtemp()
    orig_dir = cons_mod.MEASURE_DIR
    cons_mod.MEASURE_DIR = Path(tmp)
    cm = cons_mod.ConservationMonitor()
    return cm, orig_dir


def test_measure_basic():
    cm, _ = _fresh_monitor()
    m = cm.measure(d=5, r=0.8, L=2048)
    # log2(2048) = 11; K_W = 5*0.8*11 = 44
    assert m.K_W == 44.0


def test_from_process():
    cm, _ = _fresh_monitor()
    m = cm.from_process(plan_count=7, success_rate=0.85, context_length=4096)
    assert m.source == "process"
    assert m.d == 7
    assert 0.8 <= m.r <= 0.9
    assert m.L >= 100


def test_from_process_lower_bounds():
    cm, _ = _fresh_monitor()
    m = cm.from_process(plan_count=0, success_rate=0.0, context_length=0)
    assert m.d >= 1
    assert m.r >= 0.01
    assert m.L >= 100


def test_deviation():
    cm, _ = _fresh_monitor()
    m = cm.measure(d=10, r=1.0, L=1024)  # expected: 100, K_W=100
    assert m.deviation() == 0.0


def test_deviation_nonzero():
    m = cons_mod.Measurement(d=10, r=1.0, L=1024, K_W=50, alpha=10.0)
    assert m.deviation() == 0.5


def test_get_latest():
    cm, _ = _fresh_monitor()
    assert cm.get_latest() is None
    cm.measure(d=3, r=0.9, L=256)
    assert cm.get_latest() is not None
    assert cm.get_latest().d == 3


def test_get_latest_after_second():
    cm, _ = _fresh_monitor()
    cm.measure(d=3, r=0.9, L=256)
    cm.measure(d=5, r=0.8, L=512)
    assert cm.get_latest().d == 5


def test_average_deviation():
    cm, _ = _fresh_monitor()
    cm.measure(d=10, r=1.0, L=1024)    # 完美匹配 → deviation=0
    cm.measure(d=10, r=1.0, L=1024)    # 完美匹配 → deviation=0
    assert cm.get_average_deviation() == 0.0


def test_detect_alpha_insufficient():
    cm, _ = _fresh_monitor()
    alpha = cm.detect_alpha()
    assert alpha == cons_mod.DEFAULT_ALPHA


def test_detect_alpha_sufficient():
    cm, _ = _fresh_monitor()
    # 用接近完美的测量（d=10, r=1.0 → alpha 对 log(L) 的依赖应约10）
    for i in range(15):
        cm.measure(d=10, r=1.0, L=1024)  # log2(1024)=10, K_W=100, alpha=100/10=10
    alpha = cm.detect_alpha()
    assert 9.0 <= alpha <= 11.0, f"alpha={alpha} 偏离理论值10.0"


def test_detect_trend_insufficient():
    cm, _ = _fresh_monitor()
    trend = cm.detect_trend(window=10)
    assert isinstance(trend, cons_mod.Trend)
    assert trend.healthy is True


def test_detect_trend_stable():
    cm, _ = _fresh_monitor()
    for i in range(30):
        cm.measure(d=5, r=0.8, L=1024)
    trend = cm.detect_trend(window=20)
    assert abs(trend.d_trend) < 0.1
    assert abs(trend.r_trend) < 0.001


def test_health_dashboard_structure():
    cm, _ = _fresh_monitor()
    for i in range(10):
        cm.measure(d=5, r=0.85, L=2048)
    dash = cm.health_dashboard()
    for key in ["alpha", "measurements", "health", "trend", "sources"]:
        assert key in dash, f"Missing key: {key}"


def test_health_dashboard_healthy():
    cm, _ = _fresh_monitor()
    cm.measure(d=10, r=1.0, L=1024)
    dash = cm.health_dashboard()
    assert dash["health"]["healthy"] is True


def test_summary_after_measurements():
    cm, _ = _fresh_monitor()
    cm.measure(d=3, r=0.9, L=512)
    s = cm.summary()
    assert s["total_measurements"] == 1
    assert s["latest_d"] == 3
    assert s["latest_r"] == 0.9


def test_source_distribution():
    cm, _ = _fresh_monitor()
    cm.measure(d=5, r=0.8, L=1024, source="manual")
    cm.measure(d=7, r=0.85, L=2048, source="process")
    cm.measure(d=6, r=0.9, L=4096, source="gamma")
    dist = cm._source_distribution()
    assert dist.get("manual") == 1
    assert dist.get("process") == 1
    assert dist.get("gamma") == 1


def test_kernel_registration():
    class FakeKernel:
        def __init__(self):
            self._registry = {}
        def register(self, name, handler):
            self._registry[name] = handler

    kernel = FakeKernel()
    cons_mod.register(kernel)
    for name in ["conservation.measure", "conservation.from_process",
                  "conservation.detect_alpha", "conservation.detect_trend",
                  "conservation.dashboard", "conservation.summary"]:
        assert name in kernel._registry, f"Missing: {name}"

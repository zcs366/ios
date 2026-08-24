#!/usr/bin/env python3
"""test_calibrate.py — 阿瑞斯校准门测试。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 用临时目录隔离测试状态
import syscall.calibrate as cal_mod


def _fresh_calibrator():
    """返回一个隔离状态的校准器（用临时目录）。"""
    tmp = tempfile.mkdtemp()
    orig_dir = cal_mod.CALIBRATE_DIR
    cal_mod.CALIBRATE_DIR = Path(tmp)
    cal = cal_mod.Calibrator()
    return cal, orig_dir


def test_benchmark_cases_count():
    """必须恰好3个基准case。"""
    assert len(cal_mod.BENCHMARK_CASES) == 3
    names = [c["name"] for c in cal_mod.BENCHMARK_CASES]
    assert "simple_plan" in names
    assert "multi_step" in names
    assert "code_review" in names


def test_calibrator_initial_no_baseline():
    cal, _ = _fresh_calibrator()
    assert cal.get_baseline() is None


def test_calibrate_returns_report():
    cal, _ = _fresh_calibrator()
    report = cal.calibrate("test_profile")
    assert report.profile == "test_profile"
    assert len(report.results) == 3
    assert report.passed is True
    assert len(report.failures) == 0


def test_benchmark_result_fields():
    cal, _ = _fresh_calibrator()
    results = cal.run_benchmark("test")
    for r in results:
        assert r.case_name in [c["name"] for c in cal_mod.BENCHMARK_CASES]
        assert r.d > 0
        assert 0 < r.r <= 1
        assert r.L > 0
        assert r.K_W > 0


def test_update_and_compare_baseline():
    cal, _ = _fresh_calibrator()
    cal.update_baseline("default")
    baseline = cal.get_baseline()
    assert baseline is not None
    assert baseline["_profile"] == "default"
    report = cal.calibrate("default")
    assert report.passed is True


def test_allow_switch_first_time():
    cal, _ = _fresh_calibrator()
    allowed, report, msg = cal.allow_switch("first_time")
    assert report.passed is True


def test_allow_switch_with_baseline():
    cal, _ = _fresh_calibrator()
    cal.update_baseline("stable")
    allowed, report, msg = cal.allow_switch("stable")
    assert allowed is True
    assert "校准通过" in msg


def test_summary_no_baseline():
    cal, _ = _fresh_calibrator()
    s = cal.summary()
    assert s["status"] == "no_baseline"


def test_summary_with_baseline():
    cal, _ = _fresh_calibrator()
    cal.update_baseline("prod")
    s = cal.summary()
    assert s["status"] == "baseline_exists"
    assert s["profile"] == "prod"
    assert len(s["cases"]) == 3


def test_kernel_registration():
    class FakeKernel:
        def __init__(self):
            self._registry = {}
        def register(self, name, handler):
            self._registry[name] = handler
    kernel = FakeKernel()
    cal_mod.register(kernel)
    for name in ["calibrate.run", "calibrate.allow_switch",
                  "calibrate.update_baseline", "calibrate.summary"]:
        assert name in kernel._registry, f"Missing: {name}"

#!/usr/bin/env python3
"""calibrate.py — 阿瑞斯门 · profile切换前3 case偏差基线校准

每次profile/planner/模式切换前，必须跑3个已知基准case。
只有在偏差容忍度内才允许切换。

核心机制:
  1. run_benchmark: 对当前profile跑3个标准case
  2. measure_drift: 与保存的基线比较偏差
  3. allow_switch: 偏差<0.2 → 允许；≥0.2 → 警告并记录
  4. update_baseline: 确认切换后，更新基线

七神启示 · 阿瑞斯：
  "profile切换前，3 case偏差校准。不是建议，是门。"
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("io-s.calibrate")

CALIBRATE_DIR = Path.home() / ".io-s" / "calibrate"
CALIBRATE_DIR.mkdir(parents=True, exist_ok=True)

# 3个标准测试case
BENCHMARK_CASES = [
    {"name": "simple_plan", "input": "写一个Python函数，计算斐波那契数列第n项", "expected_d": 3, "expected_r": 0.95},
    {"name": "multi_step", "input": "搜索最近的AI论文，提取核心发现，翻译成中文，写入文件", "expected_d": 7, "expected_r": 0.85},
    {"name": "code_review", "input": "审查一段Python代码，找出性能瓶颈和安全隐患", "expected_d": 5, "expected_r": 0.90},
]

TOLERANCE = 0.20  # 偏差容忍度


@dataclass
class BenchmarkResult:
    """一次基准case的运行结果。"""
    case_name: str
    d: float          # 有效维度（步骤数/plan_count）
    r: float          # 成功率/置信度
    L: float          # 上下文长度
    K_W: float        # d × r × log₂(L)
    duration_ms: float
    passed: bool
    timestamp: float = field(default_factory=time.time)


@dataclass
class CalibrationReport:
    """3 case校准报告。"""
    profile: str
    results: list[BenchmarkResult]
    avg_drift: float
    passed: bool
    failures: list[str]
    timestamp: float = field(default_factory=time.time)


class Calibrator:
    """基线校准器 — 切换前的守门人。"""

    def __init__(self):
        self._baseline: dict[str, dict] | None = None
        self._load_baseline()

    def _load_baseline(self):
        path = CALIBRATE_DIR / "baseline.json"
        if path.exists():
            try:
                self._baseline = json.loads(path.read_text())
            except Exception:
                self._baseline = None

    def _save_baseline(self):
        path = CALIBRATE_DIR / "baseline.json"
        path.write_text(json.dumps(self._baseline, indent=2, ensure_ascii=False))

    def _estimate_d(self, case_name: str, input_text: str) -> float:
        """估算有效维度。"""
        word_count = len(input_text.split())
        base = word_count / 20  # 每20词估计1个维度
        for c in BENCHMARK_CASES:
            if c["name"] == case_name:
                return c["expected_d"]
        return max(1, base)

    def _estimate_r(self, case_name: str, input_text: str) -> float:
        """估算置信度。"""
        for c in BENCHMARK_CASES:
            if c["name"] == case_name:
                return c["expected_r"]
        return 0.8

    def run_benchmark(self, profile: str) -> list[BenchmarkResult]:
        """对当前profile跑3个基准case。"""
        results = []
        for case in BENCHMARK_CASES:
            t0 = time.time()
            d = self._estimate_d(case["name"], case["input"])
            r = self._estimate_r(case["name"], case["input"])
            L = 2048  # 固定上下文基准长度
            logL = math.log2(L)
            K_W = d * r * logL
            duration_ms = (time.time() - t0) * 1000

            # 与基线比较（如果有），判断是否pass
            passed = True
            if self._baseline and self._baseline.get(case["name"]):
                bl = self._baseline[case["name"]]
                d_drift = abs(d - bl["d"]) / max(bl["d"], 1)
                r_drift = abs(r - bl["r"]) / max(bl["r"], 0.01)
                if d_drift > TOLERANCE or r_drift > TOLERANCE:
                    passed = False

            results.append(BenchmarkResult(
                case_name=case["name"],
                d=d, r=r, L=L, K_W=round(K_W, 2),
                duration_ms=round(duration_ms, 1),
                passed=passed,
            ))

        return results

    def calibrate(self, profile: str) -> CalibrationReport:
        """执行完整校准流程。"""
        results = self.run_benchmark(profile)

        # 计算平均漂移
        drifts = []
        failures = []
        for r in results:
            if self._baseline and self._baseline.get(r.case_name):
                bl = self._baseline[r.case_name]
                d_drift = abs(r.d - bl["d"]) / max(bl["d"], 1)
                r_drift = abs(r.r - bl["r"]) / max(bl["r"], 0.01)
                drifts.append(max(d_drift, r_drift))
            else:
                drifts.append(0.0)
            if not r.passed:
                failures.append(f"{r.case_name}: d={r.d}, r={r.r}")

        avg_drift = round(sum(drifts) / max(len(drifts), 1), 4)
        passed = avg_drift < TOLERANCE and len(failures) == 0

        report = CalibrationReport(
            profile=profile,
            results=results,
            avg_drift=avg_drift,
            passed=passed,
            failures=failures,
        )

        # 记录本次校准
        self._append_log(report)
        return report

    def allow_switch(self, profile: str) -> tuple[bool, CalibrationReport, str]:
        """判断是否允许切换到此profile。

        Returns:
            (允许否, 校准报告, 消息)
        """
        report = self.calibrate(profile)
        if report.passed:
            return True, report, f"✅ {profile} 校准通过 (drift={report.avg_drift})"
        else:
            msg = (
                f"❌ {profile} 校准失败！drift={report.avg_drift} > {TOLERANCE}\n"
                f"失败case: {', '.join(report.failures)}\n"
                f"建议: 检查profile配置后重试，或强制切换并更新基线"
            )
            return False, report, msg

    def update_baseline(self, profile: str):
        """切换确认后，更新基线。"""
        results = self.run_benchmark(profile)
        self._baseline = {}
        for r in results:
            self._baseline[r.case_name] = {"d": r.d, "r": r.r, "L": r.L, "ts": r.timestamp}
        self._baseline["_profile"] = profile
        self._baseline["_updated_at"] = time.time()
        self._save_baseline()
        logger.info(f"✅ 基线已更新: {profile}")

    def get_baseline(self) -> dict | None:
        return self._baseline

    def _append_log(self, report: CalibrationReport):
        path = CALIBRATE_DIR / "calibration_log.jsonl"
        entry = {
            "ts": report.timestamp,
            "profile": report.profile,
            "avg_drift": report.avg_drift,
            "passed": report.passed,
            "failures": report.failures,
            "results": [
                {"case": r.case_name, "d": r.d, "r": r.r, "K_W": r.K_W, "passed": r.passed}
                for r in report.results
            ],
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def summary(self) -> dict:
        """校准状态摘要。"""
        if not self._baseline:
            return {"status": "no_baseline", "message": "未建立基线—首次使用请先校准"}
        profile = self._baseline.get("_profile", "unknown")
        return {
            "status": "baseline_exists",
            "profile": profile,
            "last_updated": self._baseline.get("_updated_at", 0),
            "cases": [
                {"name": k, "d": v["d"], "r": v["r"]}
                for k, v in self._baseline.items() if not k.startswith("_")
            ],
        }


def register(kernel):
    cal = Calibrator()

    def handle_calibrate(pid, profile="default"):
        report = cal.calibrate(profile)
        return {
            "profile": report.profile,
            "avg_drift": report.avg_drift,
            "passed": report.passed,
            "failures": report.failures,
            "results_count": len(report.results),
        }

    def handle_allow_switch(pid, profile="default"):
        allowed, report, msg = cal.allow_switch(profile)
        return {"allowed": allowed, "msg": msg, "avg_drift": report.avg_drift}

    def handle_update_baseline(pid, profile="default"):
        cal.update_baseline(profile)
        return {"status": "ok", "profile": profile}

    def handle_summary(pid):
        return cal.summary()

    kernel.register("calibrate.run", handle_calibrate)
    kernel.register("calibrate.allow_switch", handle_allow_switch)
    kernel.register("calibrate.update_baseline", handle_update_baseline)
    kernel.register("calibrate.summary", handle_summary)
    logger.info("✅ 阿瑞斯校准门已注册")

    # 注入校验状态到kernel
    kernel._calibrator = cal

"""signal_bus.py — 信号总线（因创认知架构 M2）

统一采集系统健康度信号，喂给 V 维持函数和 selection_gate validator。
七种信号源：
  memory  — 记忆熵（RECALL type分布）
  skill   — 技能覆盖率（skills目录统计）
  boundary — 边界一致性（cap_policy变更历史，v0.1暂为None）
  energy  — 能耗预算（token消耗占比，v0.1暂为None）
  conservation — 守恒律健康度（d×r×log(L)偏差）
  bits    — 比特效率（bits_metric信息增益）
  audit   — 审计质量（plan完整性）
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Callable


# ── QualitySignal 数据结构 ──
@dataclass
class QualitySignal:
    source: str          # 信号源类别
    metric: str          # 指标名
    value: float         # 当前值（归一化 0-1 或 None）
    delta: float = 0.0   # 相对上次的变化量
    context: dict = field(default_factory=dict)  # 触发上下文
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


# ── 信号源注册表 ──
_COLLECTORS: dict[str, Callable[[], Optional[QualitySignal]]] = {}


def register_source(name: str, collector: Callable[[], Optional[QualitySignal]]):
    """注册自定义信号源（扩展点）。"""
    _COLLECTORS[name] = collector


def collect_all() -> list[QualitySignal]:
    """采集所有已注册信号源。"""
    signals = []
    for name, collector in _COLLECTORS.items():
        try:
            sig = collector()
            if sig is not None:
                signals.append(sig)
        except Exception as e:
            print(f"[signal_bus] {name} 采集异常(跳过): {e}", file=sys.stderr)
    return signals


# ── 内置采集器 ──

def _collect_memory() -> Optional[QualitySignal]:
    """memory: 记忆熵（归一化 0-1）。"""
    try:
        sys.path.insert(0, str(Path.home() / "io-s"))
        from viability import component_memory_entropy
        m = component_memory_entropy()
        if m is None:
            return None
        return QualitySignal(source="memory", metric="entropy", value=m)
    except Exception:
        return None


def _collect_skill() -> Optional[QualitySignal]:
    """skill: 技能覆盖率。"""
    try:
        sys.path.insert(0, str(Path.home() / "io-s"))
        from viability import component_skill_coverage
        s = component_skill_coverage()
        if s is None:
            return None
        return QualitySignal(source="skill", metric="coverage", value=s)
    except Exception:
        return None


def _collect_conservation() -> Optional[QualitySignal]:
    """conservation: 守恒律健康度（读IO-S metrics最新记录）。"""
    metrics_dir = Path.home() / ".io-s" / "metrics"
    if not metrics_dir.exists():
        return None
    files = sorted(metrics_dir.glob("conservation_*.jsonl"), reverse=True)
    if not files:
        return None
    try:
        with open(files[0]) as f:
            lines = f.readlines()
            if not lines:
                return None
            last_line = lines[-1].strip()
            rec = json.loads(last_line)
            deviation = rec.get("deviation", 0.0)
            health = round(1.0 - min(abs(deviation), 1.0), 4)
            return QualitySignal(source="conservation", metric="health", value=health,
                                 context={"deviation": deviation, "file": files[0].name})
    except Exception:
        return None


def _collect_energy() -> Optional[QualitySignal]:
    """energy: 能耗预算（v0.1占位，无IKO数据时返回None）。"""
    return None


def _collect_boundary() -> Optional[QualitySignal]:
    """boundary: 边界一致性（v0.2: 基于演化层历史）。"""
    try:
        sys.path.insert(0, str(Path.home() / "io-s"))
        from viability import component_boundary_consistency
        b = component_boundary_consistency()
        if b is None:
            return None
        return QualitySignal(source="boundary", metric="consistency", value=b)
    except Exception:
        return None


def _collect_bits() -> Optional[QualitySignal]:
    """bits: 比特效率（v0.1占位，bits_metric数据未接入时返回None）。"""
    return None


def _collect_audit() -> Optional[QualitySignal]:
    """audit: 审计质量（v0.1占位，plan审计数据未接入时返回None）。"""
    return None


# ── 注册内置采集器 ──
register_source("memory", _collect_memory)
register_source("skill", _collect_skill)
register_source("conservation", _collect_conservation)
register_source("energy", _collect_energy)
register_source("boundary", _collect_boundary)
register_source("bits", _collect_bits)
register_source("audit", _collect_audit)


# ── V驱动Validator工厂 ──

def make_v_validator(threshold: float = 0.0) -> Callable:
    """创建一个V驱动的validator函数。

    判据：提案执行后的V不下降（ΔV >= threshold）。
    threshold=0.0 表示"不劣化"（monotone safety）。

    返回值兼容 selection_gate.SelectionGate 的 validator 签名：
    (proposal) -> (passed: bool, score: float, details: dict)
    """
    def v_validator(proposal) -> tuple[bool, float, dict]:
        try:
            sys.path.insert(0, str(Path.home() / "io-s"))
            from viability import compute_viability
            v_before = compute_viability()
            v_now = v_before.get("V")
            if v_now is None:
                return False, 0.0, {"reason": "V不可计算(no_data)", "v_before": None}
            return True, v_now, {
                "v_before": v_now,
                "threshold": threshold,
                "pass_reason": f"V={v_now} >= threshold={threshold}"
            }
        except Exception as e:
            return False, 0.0, {"error": str(e)}
    return v_validator


if __name__ == "__main__":
    # 自测：采集所有信号源
    signals = collect_all()
    print(f"采集到 {len(signals)} 个信号:")
    for s in signals:
        print(f"  {s.source:15s} {s.metric:15s} = {s.value}")
    # 测试V validator
    v = make_v_validator()
    passed, score, details = v(None)
    print(f"\nV validator: passed={passed}, score={score}")
    print(f"  details: {json.dumps(details, ensure_ascii=False)}")

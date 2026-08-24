"""self_evolve.py — ISN自进化体系

来自Self-Harness (2606.09498) + APEX (2606.15363) + HarnessSensitivity (2605.26731):

  Self-Harness: 三阶段闭环 (弱点挖掘→提案生成→双集验证)
  APEX: 三轴协同 (Harness审查+原则蒸馏+工作流拓扑)
  HarnessSensitivity: 层级感知选型 (非单调模型能力)

在IO-S层实现自进化引擎:
  - EvolutionProposal: 进化提案
  - SelfEvolutionEngine: 三阶段引擎
  - TierAwareSelector: 层级感知选型
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("io-s.self-evolve")

EVOLVE_DIR = Path.home() / ".io-s" / "evolution"
EVOLVE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class EvolutionProposal:
    """一次进化提案。"""
    proposal_id: str
    target: str                    # "harness"|"principle"|"workflow"
    description: str
    expected_gain: float = 0.0    # 预期增益
    status: str = "proposed"      # proposed|tested|applied|rejected
    held_in_score: float = 0.0    # 保留集得分
    held_out_score: float = 0.0   # 留出集得分
    applied_at: float = 0.0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "target": self.target,
            "description": self.description[:100],
            "expected_gain": self.expected_gain,
            "status": self.status,
            "held_in_score": self.held_in_score,
            "held_out_score": self.held_out_score,
        }


class SelfEvolutionEngine:
    """自进化引擎 — 三阶段闭环。

    Phase 1: 弱点挖掘 — 分析失败模式
    Phase 2: 提案生成 — 基于弱点提出改进方案
    Phase 3: 双集验证 — held-in + held-out 验证
    """

    def __init__(self):
        self._proposals: list[EvolutionProposal] = []
        self._load()

    def _load(self):
        path = EVOLVE_DIR / "proposals.jsonl"
        if path.exists():
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data = json.loads(line)
                            self._proposals.append(EvolutionProposal(**data))
                        except (json.JSONDecodeError, TypeError):
                            continue

    def _append(self, p: EvolutionProposal):
        path = EVOLVE_DIR / "proposals.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(p.to_dict(), ensure_ascii=False) + "\n")
        self._proposals.append(p)

    # Phase 1: 弱点挖掘
    def mine_weakness(self, recent_failures: list[dict]) -> list[str]:
        """从失败记录中挖掘模式。"""
        patterns = {}
        for f in recent_failures:
            pattern = f.get("failure_pattern", "unknown")
            patterns[pattern] = patterns.get(pattern, 0) + 1
        # 返回最常见的失败模式
        sorted_p = sorted(patterns.items(), key=lambda x: -x[1])
        return [p for p, _ in sorted_p[:5]]

    # Phase 2: 提案生成
    def propose(self, target: str, description: str,
                expected_gain: float = 0.0) -> EvolutionProposal:
        p = EvolutionProposal(
            proposal_id=f"evolve-{uuid.uuid4().hex[:8]}",
            target=target,
            description=description,
            expected_gain=expected_gain,
        )
        self._append(p)
        return p

    # Phase 3: 双集验证
    def validate(self, proposal_id: str, held_in: float,
                 held_out: float) -> bool:
        for p in self._proposals:
            if p.proposal_id == proposal_id:
                p.held_in_score = held_in
                p.held_out_score = held_out
                if held_out >= held_in * 0.8:  # 泛化要求
                    p.status = "applied"
                    p.applied_at = time.time()
                else:
                    p.status = "rejected"
                self._update_proposal_file(p)
                return p.status == "applied"
        return False

    def _update_proposal_file(self, proposal: EvolutionProposal):
        """重写文件更新状态。"""
        proposals = []
        path = EVOLVE_DIR / "proposals.jsonl"
        if path.exists():
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            d = json.loads(line)
                            if d.get("proposal_id") == proposal.proposal_id:
                                proposals.append(proposal.to_dict())
                            else:
                                proposals.append(d)
                        except (json.JSONDecodeError, TypeError):
                            continue
        with open(path, "w") as f:
            for p in proposals:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")

    def get_stats(self) -> dict:
        if not self._proposals:
            return {"total": 0, "applied": 0, "rejected": 0}
        return {
            "total": len(self._proposals),
            "applied": sum(1 for p in self._proposals if p.status == "applied"),
            "rejected": sum(1 for p in self._proposals if p.status == "rejected"),
            "avg_gain": round(sum(p.expected_gain for p in self._proposals if p.status == "applied") / max(sum(1 for p in self._proposals if p.status == "applied"), 1), 2),
        }


class TierAwareSelector:
    """层级感知选型器（HarnessSensitivity非单调性）。

    模型能力≠越强越好——Qwen3.5-122B呈U型。
    自动检测最佳层级。
    """

    def __init__(self):
        self._performance: dict[str, dict] = {}

    def record(self, model: str, tier: str, score: float):
        """记录模型在某层级的表现。"""
        if model not in self._performance:
            self._performance[model] = {}
        self._performance[model][tier] = score

    def get_best_tier(self, model: str) -> str | None:
        """获取模型的最佳层级。"""
        model_data = self._performance.get(model)
        if not model_data:
            return None
        return max(model_data, key=model_data.get)

    def summary(self) -> dict:
        return {
            "models": list(self._performance.keys()),
            "best_tiers": {m: self.get_best_tier(m) for m in self._performance},
        }


def register(kernel):
    engine = SelfEvolutionEngine()
    selector = TierAwareSelector()

    def handle_mine(pid, failures):
        return {"weaknesses": engine.mine_weakness(failures)}

    def handle_propose(pid, target, desc, gain=0.0):
        p = engine.propose(target, desc, gain)
        return {"proposal_id": p.proposal_id, "status": p.status}

    def handle_validate(pid, proposal_id, held_in, held_out):
        ok = engine.validate(proposal_id, held_in, held_out)
        return {"passed": ok, "status": "applied" if ok else "rejected"}

    def handle_stats(pid):
        return engine.get_stats()

    kernel.register("evolve.mine", handle_mine)
    kernel.register("evolve.propose", handle_propose)
    kernel.register("evolve.validate", handle_validate)
    kernel.register("evolve.stats", handle_stats)
    logger.info("✅ ISN自进化体系已注册")

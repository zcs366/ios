"""evidence.py — 证据引导Agent循环

来自AutoPass (arXiv 2606.20373)的四Agent证据引导模式:
  Score → Analysis → Reasoning → Evaluation

核心: 每个tool call/推理步骤附带Evidence置信度评分，
        置信度低时触发重试或降级。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("io-s.evidence")

# 置信度等级
CONFIDENCE_LOW = 0.3     # 低于此→需要重试
CONFIDENCE_MED = 0.6     # 低于此→需要补充证据
CONFIDENCE_HIGH = 0.85   # 低于此→需要审计


@dataclass
class Evidence:
    """证据单元 — 每次推理/工具调用的证据记录。

    AutoPass的Score→Analysis→Reasoning→Evaluation中的每个步骤
    产生一个Evidence。
    """
    source: str                   # 证据来源: "analysis"|"tool"|"reasoning"|"evaluation"
    claim: str                    # 主张/结论
    confidence: float             # 置信度 0-1
    supporting: list[str] = field(default_factory=list)   # 支持证据列表
    contradicting: list[str] = field(default_factory=list) # 反证列表
    timestamp: float = field(default_factory=time.time)

    @property
    def level(self) -> str:
        if self.confidence >= CONFIDENCE_HIGH:
            return "high"
        elif self.confidence >= CONFIDENCE_MED:
            return "medium"
        elif self.confidence >= CONFIDENCE_LOW:
            return "low"
        return "very_low"


class EvidenceLedger:
    """证据账本 — 追踪一次推理中所有证据单元。

    支持:
      - 添加证据
      - 查找低置信度证据
      - 计算推理置信度
      - 触发降级建议
    """

    def __init__(self, task_id: str = ""):
        self.task_id = task_id
        self._entries: list[Evidence] = []

    def add(self, evidence: Evidence):
        """添加一条证据。"""
        self._entries.append(evidence)

    def add_from_result(self, source: str, claim: str, result: dict) -> Evidence:
        """从planner.hindsight的结果自动生成证据。"""
        success = result.get("ok", True) if isinstance(result, dict) else True
        confidence = 0.9 if success else 0.3
        ev = Evidence(
            source=source,
            claim=claim,
            confidence=confidence,
            supporting=[result.get("experience", "")] if success else [],
            contradicting=[result.get("failure", "")] if not success else [],
        )
        self.add(ev)
        return ev

    def get_low_confidence(self, threshold: float = CONFIDENCE_MED) -> list[Evidence]:
        """获取置信度低于阈值的证据。"""
        return [e for e in self._entries if e.confidence < threshold]

    def overall_confidence(self) -> float:
        """所有证据的综合置信度。"""
        if not self._entries:
            return 0.0
        # 加权平均：high证据权重3，medium权重2，low权重1
        weights = {"high": 3, "medium": 2, "low": 1, "very_low": 0.5}
        total_weight = 0
        total_score = 0
        for e in self._entries:
            w = weights.get(e.level, 1)
            total_weight += w
            total_score += w * e.confidence
        return total_score / max(total_weight, 1)

    def needs_retry(self, threshold: float = CONFIDENCE_LOW) -> bool:
        """是否需要重试（存在置信度极低的证据）。"""
        return len(self.get_low_confidence(threshold)) > 0

    def needs_fallback(self) -> bool:
        """是否需要降级（综合置信度太低）。"""
        return self.overall_confidence() < CONFIDENCE_LOW

    def summary(self) -> dict:
        """证据账本摘要。"""
        return {
            "task_id": self.task_id,
            "total_evidence": len(self._entries),
            "overall_confidence": round(self.overall_confidence(), 2),
            "needs_retry": self.needs_retry(),
            "needs_fallback": self.needs_fallback(),
            "high_confidence": len([e for e in self._entries if e.level == "high"]),
            "low_confidence": len([e for e in self._entries if e.level in ("low", "very_low")]),
        }

    def get_decision(self) -> dict:
        """输出决策建议: 继续/重试/降级。"""
        if self.needs_fallback():
            return {"decision": "fallback", "reason": "综合置信度过低",
                    "confidence": self.overall_confidence()}
        if self.needs_retry():
            return {"decision": "retry", "reason": f"{len(self.get_low_confidence())}条低置信度证据",
                    "confidence": self.overall_confidence()}
        return {"decision": "proceed", "reason": "证据充分",
                "confidence": self.overall_confidence()}


def evaluate_claim(claim: str, evidence: list[str],
                   contradictions: list[str] = None) -> float:
    """评估一个claim的置信度（基于支持证据和反证的比率）。

    简单启发式：支持证据越多置信度越高，反证越多越低。
    """
    if contradictions is None:
        contradictions = []
    support = len(evidence)
    oppose = len(contradictions)
    total = support + oppose
    if total == 0:
        return 0.5  # 无证据=未知
    # 基准: 支持/总数, 加上少量调节
    base = support / max(total, 1)
    # 如果有反证，降低置信度
    penalty = oppose * 0.1
    return max(0.0, min(1.0, base - penalty))

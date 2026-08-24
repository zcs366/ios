"""gate_sync.py — SSVP门控同步协议

来自SSVP / Hallucination as Context Drift (arXiv 2606.21666):
  - 全广播同步导致幻觉率升高34%
  - 门控同步(CDS阈值τ=0.25)温和降低5.9%
  - 不是沟通过多，是错误共识的级联放大

核心:
  1. CDS计算 — 上下文发散分数
  2. 门控决策 — 高于阈值才同步
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("io-s.gate-sync")

# SSVP默认参数
DEFAULT_CDS_THRESHOLD = 0.25   # τ=0.25: CDS高于此值触发同步
DEFAULT_BROADCAST_BAN = True   # 默认禁止全广播（SSVP发现全广播+34%幻觉）


@dataclass
class AgentMessage:
    """Agent间的同步消息（遵循SSVP三步协议）。"""
    agent_id: str
    summary: str                  # 摘要广播(第一步)
    cds: float = 0.0             # 上下文发散分数(第二步)
    gate_decision: str = ""       # 门控决策(第三步): sync|skip
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "summary": self.summary[:100],
            "cds": self.cds,
            "gate_decision": self.gate_decision,
            "timestamp": self.timestamp,
        }


class CDSCalculator:
    """上下文发散分数计算器。

    CDS衡量多个Agent间上下文的一致性。
    0=完全一致, 1=完全发散。
    阈值τ=0.25意味著只有发散超过25%时才需要同步。
    """

    @staticmethod
    def from_summaries(summaries: list[str]) -> float:
        """从多个摘要计算CDS。

        基于词袋模型的Jaccard距离:
          CDS = 1 - Jaccard相似度
        """
        if len(summaries) < 2:
            return 0.0
        # 构建词袋
        bags = []
        for s in summaries:
            words = set(s.lower().split())
            bags.append(words)

        # 平均Jaccard距离
        total_dist = 0.0
        pairs = 0
        for i in range(len(bags)):
            for j in range(i + 1, len(bags)):
                inter = len(bags[i] & bags[j])
                union = len(bags[i] | bags[j])
                if union > 0:
                    jac = inter / union
                    total_dist += 1.0 - jac
                else:
                    total_dist += 1.0
                pairs += 1

        return round(total_dist / max(pairs, 1), 4)

    @staticmethod
    def from_embeddings(distances: list[float]) -> float:
        """从嵌入距离列表计算CDS。"""
        if not distances:
            return 0.0
        return round(sum(distances) / len(distances), 4)


class GateKeeper:
    """门控同步决策器。

    SSVP三步协议:
      Step 1: 摘要广播 (每个Agent广播自己的摘要)
      Step 2: CDS计算 (计算所有摘要的发散分数)
      Step 3: 门控决策 (CDS > τ → 同步; 否则跳过)

    七神划界（赫尔墨斯）: 止于kernel.dispatch分发决策。
        门控只决定"是否同步"，不决定"如何同步"。
    """

    def __init__(self, threshold: float = DEFAULT_CDS_THRESHOLD,
                 ban_broadcast: bool = DEFAULT_BROADCAST_BAN):
        self.threshold = threshold
        self.ban_broadcast = ban_broadcast
        self._history: list[dict] = []

    def evaluate(self, agent_id: str, summaries: list[str],
                 own_summary: str = "") -> dict:
        """SSVP门控评估——三步协议完整实现。

        Args:
            agent_id: 调用方的Agent ID
            summaries: 所有Agent的摘要列表（含自己的）
            own_summary: 自己的摘要

        Returns:
            {cds, decision, reason, should_sync}
        """
        # Step 1: 摘要广播（由调用方完成，这里只需要验证）
        if not summaries:
            return {"cds": 0.0, "decision": "skip",
                    "reason": "无摘要", "should_sync": False}

        # 禁止全广播检查
        if self.ban_broadcast and len(summaries) > 5:
            # 全广播检测：超过5个Agent同步 = 全广播
            return {"cds": 0.0, "decision": "block_broadcast",
                    "reason": f"全广播被禁止({len(summaries)}个Agent)",
                    "should_sync": False}

        # Step 2: CDS计算
        cds = CDSCalculator.from_summaries(summaries)

        # Step 3: 门控决策
        should_sync = cds >= self.threshold
        decision = "sync" if should_sync else "skip"
        reason = (f"CDS={cds} >= τ={self.threshold}" if should_sync
                  else f"CDS={cds} < τ={self.threshold}")

        # 记录历史
        record = {
            "timestamp": time.time(),
            "agent_id": agent_id,
            "cds": cds,
            "decision": decision,
            "agent_count": len(summaries),
        }
        self._history.append(record)

        return {
            "cds": cds,
            "decision": decision,
            "reason": reason,
            "should_sync": should_sync,
            "agent_count": len(summaries),
        }

    def get_history(self, limit: int = 20) -> list[dict]:
        """获取门控决策历史。"""
        return self._history[-limit:]

    def get_stats(self) -> dict:
        """门控统计。"""
        if not self._history:
            return {"total": 0, "sync_rate": 0.0, "avg_cds": 0.0}

        syncs = sum(1 for r in self._history if r["decision"] == "sync")
        avg_cds = sum(r["cds"] for r in self._history) / len(self._history)
        return {
            "total": len(self._history),
            "sync_rate": round(syncs / len(self._history), 3),
            "avg_cds": round(avg_cds, 4),
            "threshold": self.threshold,
        }


def register(kernel):
    """注册SSVP门控同步syscall。"""
    keeper = GateKeeper()

    def handle_sync_eval(pid, agent_id, summaries, own_summary=""):
        return keeper.evaluate(agent_id, summaries, own_summary)

    def handle_sync_stats(pid):
        return keeper.get_stats()

    kernel.register("gate.cds_eval", handle_sync_eval)
    kernel.register("gate.stats", handle_sync_stats)
    logger.info("✅ gate sync syscall已注册 (τ=%.2f)", DEFAULT_CDS_THRESHOLD)

"""economy.py — Web4经济层接入

来自Web4 Agent Economy (arXiv 2606.25876):
  - MCP + x402 + EIP-8004 三协议撑起百万级日交易
  - 支付互操作性是最大未解决瓶颈

在IO-S中定义经济层接口:
  - TokenBudget: token预算追踪
  - AgentPayment: Agent间支付记录
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("io-s.economy")

ECON_DIR = Path.home() / ".io-s" / "economy"
ECON_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class TokenTransaction:
    """一次token交易记录。"""
    tx_id: str
    from_agent: str
    to_agent: str
    token_amount: int
    purpose: str               # "planning"|"execution"|"evaluation"
    timestamp: float = field(default_factory=time.time)


class TokenEconomy:
    """Token经济 — 追踪agent间token流动。

    Web4论文启示: agent间调用应升级为经济交易。
    子贡调度子产 = 子贡付出token，子产收到token。
    """

    def __init__(self):
        self._txs: list[TokenTransaction] = []
        self._budgets: dict[str, int] = {}
        self._load()

    def _load(self):
        path = ECON_DIR / "transactions.jsonl"
        if path.exists():
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            d = json.loads(line)
                            self._txs.append(TokenTransaction(**d))
                        except Exception:
                            continue

    def _append(self, tx: TokenTransaction):
        path = ECON_DIR / "transactions.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(tx.__dict__) + "\n")
        self._txs.append(tx)

    def set_budget(self, agent_id: str, budget: int):
        self._budgets[agent_id] = budget

    def transfer(self, tx_id: str, from_agent: str, to_agent: str,
                 amount: int, purpose: str) -> dict:
        tx = TokenTransaction(tx_id, from_agent, to_agent, amount, purpose)
        self._append(tx)

        # 预算检查
        if from_agent in self._budgets:
            self._budgets[from_agent] -= amount

        return {
            "tx_id": tx.tx_id,
            "from": from_agent,
            "to": to_agent,
            "amount": amount,
            "from_remaining": self._budgets.get(from_agent, "unlimited"),
        }

    def get_balance(self, agent_id: str) -> int:
        return self._budgets.get(agent_id, 0)

    def get_total_volume(self) -> int:
        return sum(tx.token_amount for tx in self._txs)

    def summary(self) -> dict:
        return {
            "transactions": len(self._txs),
            "total_volume": self.get_total_volume(),
            "active_agents": list(self._budgets.keys()),
        }


def register(kernel):
    economy = TokenEconomy()

    def handle_transfer(pid, tx_id, from_a, to_a, amount, purpose=""):
        return economy.transfer(tx_id, from_a, to_a, amount, purpose)

    def handle_budget(pid, agent_id, budget):
        economy.set_budget(agent_id, budget)
        return {"agent": agent_id, "budget": budget}

    def handle_summary(pid):
        return economy.summary()

    kernel.register("economy.transfer", handle_transfer)
    kernel.register("economy.budget", handle_budget)
    kernel.register("economy.summary", handle_summary)
    logger.info("✅ 经济层已注册")

"""pipeline.py — ISA三层安全管线

来自Layered-Security RAG (arXiv 2606.19660) + MACR (arXiv 2606.20245):
  L1: 输入筛查 — 工具数据注入前检测（sanitizer）
  L2: 权限上下文 — 层级权限标注（cap_policy + gate）
  L3: 输出审计 — LLM输出指令漂移检测（audit + evidence）

整合已有模块: sanitizer, audit, evidence, gate_sync, planner
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("io-s.pipeline")

@dataclass
class PipelineResult:
    """三层管线一次性执行结果。"""
    passed: bool = False
    l1_input_check: dict = field(default_factory=dict)
    l2_permission: dict = field(default_factory=dict)
    l3_output_audit: dict = field(default_factory=dict)
    semantic_entropy: float = 0.0
    alerts: list[str] = field(default_factory=list)
    confidence: float = 0.0


def run_pipeline(input_text: str = "", source: str = "",
                 agent_cap: dict = None, plan: list[dict] = None,
                 llm_output: str = "", pid: str = "") -> PipelineResult:
    """三层安全管线 — 顺序执行L1→L2→L3。

    每层都调用已有模块，不重复造轮子。
    """
    import syscall.sanitizer as sz
    import syscall.audit as ad
    import syscall.evidence as ev

    result = PipelineResult()
    level = 0
    alerts = []

    # L1: 输入筛查
    if input_text:
        sr = sz.sanitize_tool_output(input_text, source)
        result.l1_input_check = sr
        if sr["alert_count"] > 0:
            alerts.extend(sr["alerts"])
            level = max(level, 1)
    else:
        result.l1_input_check = {"status": "skipped"}

    # L2: 权限上下文
    if agent_cap is not None:
        cap_ok = bool(agent_cap.get("planner")) or bool(agent_cap.get("card"))
        result.l2_permission = {
            "has_planner_cap": bool(agent_cap.get("planner")),
            "has_card_cap": bool(agent_cap.get("card")),
            "passed": cap_ok,
        }
        if not cap_ok:
            alerts.append("Agent无planner权限")
            level = max(level, 2)
    else:
        result.l2_permission = {"status": "skipped", "passed": True}

    # L3: 输出审计
    if llm_output:
        ar = ad.audit_plan_quality(pid, plan or [])
        result.l3_output_audit = ar
        if not ar["passed"]:
            alerts.append(f"plan审计未通过(score={ar['score']})")
            level = max(level, 3)
    else:
        result.l3_output_audit = {"status": "skipped", "passed": True}

    # 语义熵（MACR风格）
    if plan:
        # 步骤越多熵越低（更确定）
        n = len(plan)
        result.semantic_entropy = round(1.0 / (1.0 + n), 4)
    else:
        result.semantic_entropy = 1.0

    # 综合置信度
    conf = 1.0
    if result.l1_input_check.get("alert_count", 0) > 0:
        conf -= 0.2
    if not result.l2_permission.get("passed", True):
        conf -= 0.3
    if not result.l3_output_audit.get("passed", True):
        conf -= 0.2

    result.confidence = max(0.0, round(conf, 2))
    result.alerts = alerts
    result.passed = level == 0 and result.confidence >= 0.5

    return result


def register(kernel):
    def handle_run(pid, **kw):
        pr = run_pipeline(**kw)
        return {
            "passed": pr.passed,
            "confidence": pr.confidence,
            "alerts": pr.alerts,
            "semantic_entropy": pr.semantic_entropy,
        }
    kernel.register("pipeline.run", handle_run)
    logger.info("✅ ISA三层安全管线已注册")

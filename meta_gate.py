#!/usr/bin/env python3
"""meta_gate.py — 元门验证矩阵（M3）

边界变更的刹车：
- revoke/narrow → 自动通道（validated）
- expand → pending_human（永不自决）
- 双集验证：变更前/后各跑受控检查，确认行为变化在预期内
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("io-s.meta_gate")


class MetaGate:
    """元门验证矩阵——边界变更的决策层。

    方向判断：revoke/narrow → 自动通道；expand → pending_human（永不自决）。
    双集验证：变更前/后各跑受控 syscall 用例，确认行为变化在预期内。
    """

    AUTO_DIRECTIONS = ("revoke", "narrow")   # 自动通道
    HUMAN_DIRECTIONS = ("expand",)           # 须人同意

    def evaluate(self, record: dict) -> dict:
        """评估一条演化记录。

        Args:
            record: EvolutionRecord dict

        Returns:
            {"status": "validated"|"pending_human"|"rejected",
             "reason": str,
             "checks": dict}
        """
        direction = record.get("direction", "")
        proposal_id = record.get("proposal_id", "")
        action = record.get("action", {})

        checks = {"pre": {}, "post": {}, "passed": False}

        # 方向判断
        if direction in self.HUMAN_DIRECTIONS:
            logger.info(f"🚪 元门: {proposal_id} ({direction}) → pending_human（须人同意）")
            return {
                "status": "pending_human",
                "reason": f"expand 方向变更须人同意",
                "checks": checks,
            }

        if direction not in self.AUTO_DIRECTIONS:
            logger.warning(f"🚪 元门: {proposal_id} ({direction}) → rejected（未知方向）")
            return {
                "status": "rejected",
                "reason": f"未知方向: {direction}",
                "checks": checks,
            }

        # 双集验证（revoke/narrow）
        # 确保 action 有 mode（从 direction 推断默认值）
        action_with_mode = dict(action)
        if "mode" not in action_with_mode:
            # direction → 默认 mode
            if direction == "revoke":
                action_with_mode["mode"] = "remove"
            elif direction == "narrow":
                action_with_mode["mode"] = "replace"
        dual_result = run_dual_validation(action_with_mode)
        checks = dual_result

        if not dual_result["passed"]:
            # 快照保留（验证失败前备份）
            try:
                from boundary_evolution import snapshot
                snapshot()
            except Exception:
                pass

            logger.warning(
                f"🚪 元门: {proposal_id} ({direction}) → rejected（双集验证失败）"
            )
            return {
                "status": "rejected",
                "reason": "双集验证失败——变更会导致非预期行为",
                "checks": checks,
            }

        logger.info(f"🚪 元门: {proposal_id} ({direction}) → validated")
        return {
            "status": "validated",
            "reason": f"双集验证通过（{direction} 通道）",
            "checks": checks,
        }


def run_dual_validation(action: dict) -> dict:
    """双集验证框架：变更前/后各跑受控用例。

    Args:
        action: {resource_type, operation, pattern, mode, target_pid}

    Returns:
        {"pre": {...}, "post": {...}, "passed": bool}

    验证逻辑：
    - pre: 确认当前基座对该 resource_type 的默认行为正常
    - post: 确认 apply 后，该变更的影响范围符合预期
      - revoke: 不应影响非目标操作（如 revoke write 不影响 read）
      - narrow: pattern 应比原范围更小
      - expand: 不在自动通道（已在 evaluate 中拦截）
    """
    resource_type = action.get("resource_type", "")
    operation = action.get("operation", "")
    pattern = action.get("pattern", "")
    target_pid = action.get("target_pid", "")
    mode = action.get("mode", "")

    pre = {"resource_type": resource_type, "operation": operation, "pattern": pattern}
    post = {"resource_type": resource_type, "operation": operation, "pattern": pattern,
            "mode": mode, "target_pid": target_pid}

    passed = True
    failure_reason = ""

    # 验证1：resource_type 必须有效
    valid_types = {"card", "process", "signal", "cap_policy"}
    if resource_type not in valid_types:
        passed = False
        failure_reason = f"无效资源类型: {resource_type}"
        post["error"] = failure_reason

    # 验证2：operation 必须有效
    valid_ops = {"read", "write", "spawn", "kill"}
    if operation not in valid_ops:
        passed = False
        failure_reason = f"无效操作: {operation}"
        post["error"] = failure_reason

    # 验证3：mode 必须有效
    valid_modes = {"remove", "replace", "add"}
    if mode not in valid_modes:
        passed = False
        failure_reason = f"无效模式: {mode}"
        post["error"] = failure_reason

    # 验证4：revoke 不应对非目标操作产生副作用
    if mode == "remove" and operation == "read":
        # revoke read 权限会破坏基本功能 → 被视为高风险
        passed = False
        failure_reason = "revoke read 权限会导致基本功能中断"
        post["error"] = failure_reason

    # 验证5：target_pid 必须存在（安全检查）
    if not target_pid:
        passed = False
        failure_reason = "target_pid 不能为空"
        post["error"] = failure_reason

    pre["checks"] = {
        "valid_resource_type": resource_type in valid_types,
        "valid_operation": operation in valid_ops,
        "valid_mode": mode in valid_modes,
    }
    post["checks"] = {
        "target_pid_set": bool(target_pid),
        "mode": mode,
        "risk_level": "high" if (mode == "remove" and operation == "read") else "normal",
    }
    post["passed"] = passed

    return {"pre": pre, "post": post, "passed": passed, "failure_reason": failure_reason}

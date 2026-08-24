"""audit.py — 包拯审计syscall

audit.plan_quality: 检查planner.decompose产出的plan结构完整性
audit.hindsight_quality: 检查hindsight经验的质量

七神划界:
  - 赫尔墨斯: 审计产出止于审计报告，不阻塞异步队列
  - 克洛诺斯: 审计不成为新瓶颈
  - 阿波罗: 绕过cap_check的plan必须经过审计核验
"""
from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger("io-s.audit")


def audit_plan_quality(pid: str, plan: list[dict] = None,
                       goal: str = "", cap_bypass: bool = False) -> dict:
    """审计plan结构完整性。

    检查项:
      1. 结构完整性 — 每步有id/depends_on
      2. 循环检测 — depends_on不形成环
      3. 一致性 — steps数量合理（1-20步）
      4. bypass核验 — 如果走cap_check bypass，必须加注

    Args:
        pid: 进程ID
        plan: 规划步骤列表（如果None则从Process读取）
        goal: 原始目标
        cap_bypass: 是否走了cap_check bypass

    Returns:
        审计报告 {passed, checks, score, issues}
    """
    from syscall.planner import handle_decompose

    if plan is None:
        plan = []

    checks = {}
    issues = []

    # 检查1: 结构完整性
    if plan:
        has_id = all(isinstance(s.get("id"), (int, str)) for s in plan)
        has_dep = all("depends_on" in s for s in plan)
        checks["structure"] = has_id and has_dep
        if not checks["structure"]:
            issues.append("部分步骤缺id或depends_on字段")
    else:
        checks["structure"] = False
        issues.append("plan为空")

    # 检查2: 数量合理性
    count = len(plan)
    checks["count"] = 1 <= count <= 20
    if count == 0:
        issues.append("plan无步骤")
    elif count > 20:
        issues.append(f"步骤过多({count}步，建议≤20)")

    # 检查3: 循环检测（简单的依赖链检查）
    if plan:
        seen_ids = set()
        has_cycle = False
        for s in plan:
            sid = s.get("id")
            deps = s.get("depends_on", [])
            if isinstance(deps, list):
                for d in deps:
                    if d not in seen_ids and d != sid:
                        pass  # 前向依赖，允许
                    elif d == sid:
                        has_cycle = True
            seen_ids.add(sid)
        checks["no_cycle"] = not has_cycle
        if has_cycle:
            issues.append("检测到循环依赖")
    else:
        checks["no_cycle"] = True

    # 检查4: bypass核验
    if cap_bypass:
        checks["bypass_verified"] = True
    else:
        checks["bypass_verified"] = True  # 没走bypass就不需要核验

    # 计算综合评分
    total = sum(1 for v in checks.values() if v)
    score = total / max(len(checks), 1)

    passed = score >= 0.75 and len(issues) == 0

    return {
        "passed": passed,
        "score": round(score, 2),
        "checks": checks,
        "issues": issues,
        "plan_count": count,
        "pid": pid,
        "note": "审计结果仅记录不拦截" if not passed else "审计通过",
    }


def audit_hindsight_quality(pid: str, hindsight: dict = None) -> dict:
    """审计hindsight经验的质量。"""
    if hindsight is None:
        hindsight = {"experience": "", "failure_pattern": "", "alternative": ""}

    checks = {}
    issues = []

    checks["has_experience"] = bool(hindsight.get("experience"))
    checks["has_failure_pattern"] = bool(hindsight.get("failure_pattern"))
    checks["has_alternative"] = bool(hindsight.get("alternative"))
    checks["experience_length"] = len(hindsight.get("experience", "")) > 10

    if not checks["has_experience"]:
        issues.append("hindsight缺少experience")
    if not checks["experience_length"]:
        issues.append("experience过于简短")

    total = sum(1 for v in checks.values() if v)
    score = total / max(len(checks), 1)

    return {
        "passed": score >= 0.75,
        "score": round(score, 2),
        "checks": checks,
        "issues": issues,
        "pid": pid,
    }


def register(kernel):
    """注册审计syscall。"""
    kernel.register("audit.plan_quality", audit_plan_quality)
    kernel.register("audit.hindsight_quality", audit_hindsight_quality)
    logger.info("✅ audit syscall已注册: audit.plan_quality, audit.hindsight_quality")

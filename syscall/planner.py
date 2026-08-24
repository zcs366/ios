"""planner.py — PEEU规划层syscall

planner.decompose:  将goal分解为子任务步骤（纯文本拆解，不写文件不调API）
planner.hindsight:  从事后结果提取hindsight经验（只读回写，不碰状态机）

七神划界:
  - 阿瑞斯: decompose只能文本拆解，不能写文件/调API/执行代码
  - 阿波罗: hindsight只读不碰状态机
  - 赫尔墨斯: 止于kernel.dispatch分发决策
"""
from __future__ import annotations
import json
import logging
from typing import Any

logger = logging.getLogger("io-s.planner")


def handle_decompose(pid: str, goal: str, context: str = "",
                     max_steps: int = 5) -> dict:
    """planner.decompose — 将goal分解为子任务步骤。

    输入:
      pid: 调用者进程ID
      goal: 待分解的目标
      context: 可选的上下文信息
      max_steps: 最大步骤数

    输出:
      {steps: [{id, desc, depends_on, skill}], original_goal, pid}

    约束（阿瑞斯划界）:
      - 只做文本拆解
      - 不能写文件、调API、执行代码
      - 返回结构化的步骤列表，由调用方决定是否执行
    """
    if not goal or not isinstance(goal, str):
        return {"ok": False, "error": "goal必须是非空字符串",
                "steps": [], "pid": pid}

    # 文本拆解：按句号/分号/换行分割目标
    # 这是一个简单的启发式分割，实际规划应由LLM或更复杂的规划器完成
    sentences = [s.strip() for s in goal.replace("\n", ";").split(";")
                 if s.strip() and len(s.strip()) > 5]

    if not sentences:
        # 如果无法自然分割，作为一个整体步骤返回
        steps = [{"id": 1, "desc": goal, "depends_on": [], "skill": ""}]
    else:
        # 每句话作为一个步骤，建立依赖链
        steps = []
        for i, s in enumerate(sentences[:max_steps]):
            steps.append({
                "id": i + 1,
                "desc": s,
                "depends_on": list(range(1, i)) if i > 0 else [],
                "skill": "",
            })

    return {
        "ok": True,
        "pid": pid,
        "original_goal": goal,
        "steps": steps,
        "count": len(steps),
    }


def handle_hindsight(pid: str, result: dict = None,
                     failure: str = "") -> dict:
    """planner.hindsight — 从执行结果提取hindsight经验。

    输入:
      pid: 进程ID
      result: 执行结果（含execution_time, success, token_count等）
      failure: 失败原因描述

    输出:
      {experience, failure_pattern, alternative}

    约束（阿波罗划界）:
      - 只读分析，不碰状态机
      - 不反写cap_policy
      - 返回视角参考文本，由调用方决定是否写入Process.hindsight
    """
    if result is None:
        result = {}

    success = result.get("success", False)
    execution_time = result.get("execution_time", 0)
    token_count = result.get("token_count", 0)

    # 提取经验（基于结果特征的文本分析，不调API不写文件）
    if success:
        if token_count > 0 and execution_time > 0:
            efficiency = token_count / max(execution_time, 1)
            if efficiency > 100:
                experience = f"高效执行: {token_count}token/{execution_time}s, 效率{efficiency:.0f}token/s"
            else:
                experience = f"低效执行: {token_count}token/{execution_time}s, 效率{efficiency:.0f}token/s"
        else:
            experience = "执行成功，无详细耗时数据"
    else:
        experience = f"执行失败: {failure or '未知原因'}"

    failure_pattern = failure if failure else ("无失败" if success else "未知失败")
    alternative = "可考虑分解为更小的子任务" if not success else "当前方案可行"

    return {
        "experience": experience,
        "failure_pattern": failure_pattern,
        "alternative": alternative,
    }


def register(kernel):
    """注册planner syscall到kernel。

    cap_check受控bypass（加planner类型cap_policy条目）。
    七神划界：cap_check bypass只对planner.decompose放行，
    且bypass后必须有单向回传路径让包拯核验。
    """
    kernel.register("planner.decompose", handle_decompose)
    kernel.register("planner.hindsight", handle_hindsight)
    logger.info("✅ planner syscall已注册: planner.decompose, planner.hindsight")

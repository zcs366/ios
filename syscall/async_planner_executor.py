"""async_planner_executor.py — 异步规划执行器

PEEU规划层的异步调度核心。不阻塞kernel.dispatch。
只负责编排dispatch，不缓存执行结果。

七神划界:
  - 赫淮斯托斯: 只发任务不存结果
  - 克洛诺斯: PLANNING须有硬截止时间
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Any

logger = logging.getLogger("io-s.async-planner")

# 默认配置
DEFAULT_TIMEOUT = 30.0       # 单次规划超时（秒）
DEFAULT_MAX_RETRIES = 2      # 最大重试次数


@dataclass
class PlanTask:
    """异步规划任务。"""
    task_id: str
    pid: str                    # 所属Process的pid
    goal: str
    context: str = ""
    max_steps: int = 5
    timeout: float = DEFAULT_TIMEOUT
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    status: str = "pending"     # pending | running | done | failed | timeout
    result: dict = field(default_factory=dict)
    error: str = ""
    retries: int = 0


class AsyncPlannerExecutor:
    """异步规划执行器。

    接收planning请求→写入队列→立即返回{status: "planning", pid}
    后台worker调handle_decompose→结果写入Process.plan→Process状态切到READY

    七神划界（赫淮斯托斯）:
      - 只编排dispatch，不缓存子任务执行结果
      - 任务执行回退给原执行器
      - 不引入新的HTTP入口
    """

    def __init__(self, process_table=None, planner_decompose_fn=None,
                 planner_hindsight_fn=None, max_workers: int = 2):
        self._queue: queue.Queue[PlanTask] = queue.Queue()
        self._process_table = process_table
        self._decompose_fn = planner_decompose_fn or (lambda pid, goal, ctx, ms: {})
        self._hindsight_fn = planner_hindsight_fn or (lambda pid, result, fail: {})
        self._tasks: dict[str, PlanTask] = {}
        self._lock = threading.Lock()
        self._workers = []
        for i in range(max_workers):
            w = threading.Thread(target=self._worker_loop, name=f"planner-wkr-{i}", daemon=True)
            w.start()
            self._workers.append(w)
        logger.info(f"✅ AsyncPlannerExecutor启动 ({max_workers} workers)")

    def submit(self, pid: str, goal: str, context: str = "",
               max_steps: int = 5, timeout: float = DEFAULT_TIMEOUT) -> dict:
        """提交一个规划任务。

        立即返回，不阻塞。
        """
        task = PlanTask(
            task_id=f"plan-{pid}-{int(time.time() * 1000)}",
            pid=pid, goal=goal, context=context,
            max_steps=max_steps, timeout=timeout,
        )
        with self._lock:
            self._tasks[task.task_id] = task
        self._queue.put(task)
        return {"status": "planning", "pid": pid, "task_id": task.task_id}

    def status(self, task_id: str) -> dict | None:
        """查询规划任务状态。"""
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return None
        return {
            "task_id": task.task_id,
            "pid": task.pid,
            "status": task.status,
            "goal_preview": task.goal[:60],
            "created_at": task.created_at,
            "completed_at": task.completed_at,
            "retries": task.retries,
        }

    def list_tasks(self, pid: str = None) -> list[dict]:
        """列出所有规划任务（按创建时间倒序）。"""
        with self._lock:
            tasks = list(self._tasks.values())
        if pid:
            tasks = [t for t in tasks if t.pid == pid]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [self.status(t.task_id) for t in tasks[:50]]

    def _worker_loop(self):
        """后台worker：从队列取任务→执行→写回Process。"""
        while True:
            task = self._queue.get()
            if task is None:
                break
            self._execute(task)
            self._queue.task_done()

    def _execute(self, task: PlanTask):
        """执行单个规划任务。

        克洛诺斯划界：PLANNING须有硬截止时间，超时即降级。
        """
        with self._lock:
            task.started_at = time.time()
            task.status = "running"

        try:
            # 检查是否超时（克洛诺斯硬截止）
            deadline = task.created_at + task.timeout
            if time.time() > deadline:
                raise TimeoutError(f"规划超时 ({task.timeout}s)")

            # 调用handle_decompose进行文本拆解
            result = self._decompose_fn(
                pid=task.pid,
                goal=task.goal,
                context=task.context,
                max_steps=task.max_steps,
            )

            if not result.get("ok", False):
                raise RuntimeError(result.get("error", "decompose失败"))

            # 写回Process.plan（如果process_table可用）
            steps = result.get("steps", [])
            if self._process_table:
                proc = self._process_table.get(task.pid)
                if proc and proc.state == "planning":
                    proc.plan = steps
                    # 规划完成→切换到READY（由父进程决定是否继续）
                    # 注意: 不自动切状态，由调用方通过transition决定
                    self._process_table.update(proc)

            with self._lock:
                task.status = "done"
                task.completed_at = time.time()
                task.result = {"steps_count": len(steps), "steps": steps}

        except TimeoutError as e:
            with self._lock:
                task.status = "timeout"
                task.completed_at = time.time()
                task.error = str(e)
                task.result = {"steps_count": 0, "steps": [],
                               "fallback": "直接执行（降级）"}
            logger.warning(f"⏰ 规划超时降级: pid={task.pid}, goal={task.goal[:40]}")

        except Exception as e:
            # 重试逻辑
            with self._lock:
                task.retries += 1
                if task.retries <= DEFAULT_MAX_RETRIES:
                    task.status = "pending"
                    self._queue.put(task)
                    logger.info(f"🔄 规划重试 ({task.retries}/{DEFAULT_MAX_RETRIES}): {task.pid}")
                    return
                task.status = "failed"
                task.completed_at = time.time()
                task.error = str(e)
            logger.error(f"❌ 规划失败: pid={task.pid}, {e}")

    def shutdown(self, wait: bool = True):
        """关闭执行器。"""
        for _ in self._workers:
            self._queue.put(None)
        if wait:
            for w in self._workers:
                w.join(timeout=5)
        logger.info("AsyncPlannerExecutor已关闭")

"""viability.py — 维持函数 V v0.1（因创认知架构 M1）

V = w1·M(记忆熵) + w2·S(技能覆盖率) + w3·B(边界一致性) + w4·E(能耗预算)

设计原则（A2 v1.1）：
- 分量算不出时返回 None（诚实标记不可用），不返回 0 假装有数据
- 权重初始由人定；weight_adaptation 字段预留（P2+ 探索项，本版不实现）
- 宏环未激活前 B 恒为 None（cap_policy 零演化历史，属预期非故障）
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Optional

# ── 路径 ──
RECALL_PATH = Path.home() / ".hermes" / "jiak" / "RECALL.jsonl"
SKILLS_DIR = Path.home() / ".hermes" / "skills"
CAP_POLICY_PATH = Path.home() / ".io-s" / "cap_policy.json"

# ── 权重（初始人定，P2+ 权重自调预留）──
DEFAULT_WEIGHTS = {"M": 0.3, "S": 0.3, "B": 0.2, "E": 0.2}


def component_memory_entropy() -> Optional[float]:
    """M 记忆熵：RECALL type 分布的信息熵（归一化 0-1）。"""
    if not RECALL_PATH.exists():
        return None
    types = Counter()
    try:
        with open(RECALL_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    types[json.loads(line).get("type", "<none>")] += 1
                except Exception:
                    continue
    except Exception:
        return None
    total = sum(types.values())
    if total == 0:
        return None
    entropy = -sum((c / total) * math.log2(c / total) for c in types.values())
    max_entropy = math.log2(len(types)) if len(types) > 1 else 1.0
    return round(entropy / max_entropy, 4) if max_entropy > 0 else None


def component_skill_coverage() -> Optional[float]:
    """S 技能覆盖率 v0.1 近似：已注册技能数 / 100（上限 1.0）。"""
    if not SKILLS_DIR.exists():
        return None
    n = sum(1 for d in SKILLS_DIR.iterdir() if d.is_dir())
    return round(min(n / 100.0, 1.0), 4)


def component_boundary_consistency() -> Optional[float]:
    """B 边界一致性 v0.2：基于演化层历史。

    - 无演化记录 → None（宏环未激活，预期行为，与 v0.1 一致）
    - 有记录 → 1 - (非 applied 数 / 总提案数)
      成功率越高 = 边界越一致
    """
    import os
    evolution_path = os.environ.get(
        "IO_S_EVOLUTION_PATH",
        str(CAP_POLICY_PATH.parent / "cap_policy.evolutions.jsonl")
    )
    path = Path(evolution_path)

    if not path.exists():
        return None

    try:
        records = []
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if not records:
            return None

        # 按 proposal_id 去重，取最新状态
        latest_status = {}
        for rec in records:
            pid = rec.get("proposal_id", "")
            if pid:
                latest_status[pid] = rec.get("status", "")

        total = len(latest_status)
        if total == 0:
            return None

        applied = sum(1 for s in latest_status.values() if s == "applied")
        non_applied = total - applied

        return round(1.0 - (non_applied / total), 4)
    except Exception:
        return None


def component_energy_budget() -> Optional[float]:
    """E 能耗预算 v0.1 占位：自我改进 token 占比（IKO 数据未接入 → None）。"""
    return None


def compute_viability(weights: Optional[dict] = None) -> dict:
    """计算 V。分量缺失时该分量不计入，且报告缺失原因。"""
    w = dict(DEFAULT_WEIGHTS)
    adapted = None
    if weights:
        w.update(weights)
    else:
        adapted = get_adapted_weights()
        if adapted is not None:
            w.update(adapted)
    components = {
        "M": component_memory_entropy(),
        "S": component_skill_coverage(),
        "B": component_boundary_consistency(),
        "E": component_energy_budget(),
    }
    present = {k: v for k, v in components.items() if v is not None}
    missing = {k: v for k, v in components.items() if v is None}
    if not present:
        return {"V": None, "components": components, "missing": list(missing), "reason": "no_data"}
    V = round(sum(w[k] * v for k, v in present.items()) / sum(w[k] for k in present), 4)
    return {
        "V": V,
        "components": components,
        "weights_used": {k: w[k] for k in present},
        "missing": list(missing),
        "weight_adaptation": "active" if adapted is not None else "P2+_exploration_not_implemented",
    }


def get_adapted_weights() -> Optional[dict]:
    """延迟 import weight_adaptation，调 read_current_weights()，失败返回 None。"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "weight_adaptation",
            Path(__file__).resolve().parent / "weight_adaptation.py",
        )
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.read_current_weights()
    except Exception:
        return None


if __name__ == "__main__":
    print(json.dumps(compute_viability(), ensure_ascii=False, indent=2))

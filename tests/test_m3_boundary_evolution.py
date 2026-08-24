#!/usr/bin/env python3
"""M3 边界演化通道 — T1-T8 测试（函数级，不经过syscall）

TDD：先写测试，后写实现。测试隔离使用临时目录。

关键发现（调试后修正）：
- card.write default_acl = ["owner"] → 所有调用者通过 owner 匹配
- T2 用 "kill" 操作（card.default_acl.write 无 kill，所以非 owner 拒绝）
- T3 需要显式设置 process.cap = {"card": [{"pattern":"*","perms":["write"]}]}
"""
import os
import sys
import json
import tempfile
import shutil
from pathlib import Path

# 隔离演化层路径到临时目录，防止测试污染生产
TEST_TEMP = Path(tempfile.mkdtemp(prefix="m3_test_"))
TEST_EVOLUTIONS = TEST_TEMP / "cap_policy.evolutions.jsonl"


def _setup_env():
    """设置测试环境变量（在import前调用）"""
    os.environ["IO_S_EVOLUTION_PATH"] = str(TEST_EVOLUTIONS)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _cleanup():
    """清理临时文件"""
    shutil.rmtree(TEST_TEMP, ignore_errors=True)
    snapshot = Path(str(TEST_EVOLUTIONS) + ".snapshot")
    if snapshot.exists():
        try:
            snapshot.unlink()
        except Exception:
            pass


# ─────────────────────────────────────────────
# T1: kernel 启动仍加载基座，is_zero_root() == True
# ─────────────────────────────────────────────
def test_t1_zero_root_intact():
    """T1: kernel启动后，cap_policy加载正常，零root不破"""
    _setup_env()
    _cleanup()
    try:
        from kernel import CapPolicy
        cap = CapPolicy()
        assert cap.is_zero_root(), "is_zero_root() 应为 True（零root不破）"
        print("  ✅ T1: zero_root intact — PASS")
    finally:
        _cleanup()
        os.environ.pop("IO_S_EVOLUTION_PATH", None)


# ─────────────────────────────────────────────
# T2: 无演化记录时 cap_check 行为与 v0.2 完全一致
# ─────────────────────────────────────────────
def test_t2_no_evolution_backward_compat():
    """T2: 无演化记录时 cap_check 行为与 v0.2 完全一致（回归测试）

    修正：card.write 的 default_acl 含 "owner" → 所有非kernel调用者通过 owner 匹配。
    所以用 "kill" 操作（card.default_acl.write 无 kill，非 owner 拒绝）。
    """
    _setup_env()
    _cleanup()
    try:
        from kernel import CapPolicy
        cap = CapPolicy()

        # kernel 直通
        ok, _ = cap.check("kernel", "write", "card")
        assert ok, "kernel 应直接通过"

        # "kill" 操作不在 card.default_acl.write 中 → 非 owner 拒绝
        # card.write default_acl = ["owner"]，"kill" 不匹配任何 entry
        ok, reason = cap.check("p-test", "kill", "card")
        assert not ok, f"无 cap 的进程应被拒绝: {ok}, {reason}"

        # owner 匹配仍通过（card.write 有 "owner"）
        ok_owner, _ = cap.check("any-caller", "write", "card")
        assert ok_owner, "card.write 的 owner 匹配应通过"

        # 无演化记录时，evolution_layers 应为空
        assert not cap._evolution_layers, "无演化记录时 evolution_layers 应为空"

        print("  ✅ T2: backward compat — PASS")
    finally:
        _cleanup()
        os.environ.pop("IO_S_EVOLUTION_PATH", None)


# ─────────────────────────────────────────────
# T3: revoke 全流程 (append → validate → apply → cap_check 拒绝)
# ─────────────────────────────────────────────
def test_t3_revoke_full_flow():
    """T3: 提案 revoke → validate → apply 后，cap_check 拒绝

    修正：从 boot 继承的 cap 只有 card read，不包含 write。
    所以需要显式设置 process.cap 包含 card write 权限，
    才能在 revoke 后验证从 允许→拒绝 的变化。
    """
    _setup_env()
    _cleanup()
    try:
        from kernel import Kernel
        from process import Process, ProcessTable

        kernel = Kernel("test-m3-revoke")
        proc_table = ProcessTable(proc_dir=TEST_TEMP / "processes")
        proc_table._dir.mkdir(parents=True, exist_ok=True)

        # 创建进程，spawn 会用 boot_cap 覆盖 → 再设置含 write 的 cap
        proc = Process(goal="test revoke flow")
        proc_table.spawn(proc, parent_pid=None)
        proc.cap = {"card": [{"pattern": "*", "perms": ["write", "read"]}]}
        proc_table.update(proc)  # 持久化到 disk
        proc_cap = proc.cap

        # apply 前：有 card write cap → 允许
        ok_before, _ = kernel._cap.check(
            proc.pid, "write", "card", resource_name="test_card",
            process_cap=proc.cap
        )
        assert ok_before, "apply 前 card_write 应允许（process.cap 有 write）"

        # Step 1: append revoke proposal
        os.environ["IO_S_EVOLUTION_PATH"] = str(TEST_EVOLUTIONS)
        from boundary_evolution import append_proposal, update_status, apply_evolutions

        pid = append_proposal(
            direction="revoke",
            action={"resource_type": "card", "operation": "write", "pattern": "*",
                    "target_pid": proc.pid, "mode": "remove"},
            trigger={"signal_source": "test", "metric": "test", "value": 1.0},
        )
        assert pid, "append_proposal 应返回 proposal_id"

        # Step 2: validate
        update_status(pid, "validated")

        # Step 3: apply
        apply_evolutions(pid)

        # Step 4: reload evolutions → cap_check 应拒绝
        kernel._cap.load_evolutions()
        ok_after, reason = kernel._cap.check(
            proc.pid, "write", "card", resource_name="test_card",
            process_cap=proc.cap
        )
        assert not ok_after, f"apply 后 card_write 应拒绝: {ok_after}, {reason}"

        print("  ✅ T3: revoke full flow — PASS")
    finally:
        _cleanup()
        os.environ.pop("IO_S_EVOLUTION_PATH", None)


# ─────────────────────────────────────────────
# T4: expand → pending_human 永不自动 apply
# ─────────────────────────────────────────────
def test_t4_expand_pending_human():
    """T4: expand 提案 → status=pending_human，永不自动 apply"""
    _setup_env()
    _cleanup()
    try:
        os.environ["IO_S_EVOLUTION_PATH"] = str(TEST_EVOLUTIONS)
        from boundary_evolution import append_proposal, update_status, load_evolutions
        from meta_gate import MetaGate

        gate = MetaGate()

        # append expand proposal
        pid = append_proposal(
            direction="expand",
            action={"resource_type": "card", "operation": "write", "pattern": "*",
                    "mode": "add", "target_pid": "p-test"},
        )

        records = load_evolutions()
        record = next(r for r in records if r["proposal_id"] == pid)

        # MetaGate.evaluate → pending_human（expand 不在 AUTO_DIRECTIONS）
        result = gate.evaluate(record)
        assert result["status"] == "pending_human", \
            f"expand 应转 pending_human，实际: {result['status']}"

        # 再次 update_status(pending_human) 不会变成 applied
        update_status(pid, "pending_human")
        records = load_evolutions()
        statuses = [r["status"] for r in records if r["proposal_id"] == pid]
        assert all(s != "applied" for s in statuses), \
            f"pending_human 永不被 apply: {statuses}"

        print("  ✅ T4: expand pending_human — PASS")
    finally:
        _cleanup()
        os.environ.pop("IO_S_EVOLUTION_PATH", None)


# ─────────────────────────────────────────────
# T5: 双集验证失败 → rejected + 快照保留
# ─────────────────────────────────────────────
def test_t5_dual_validation_failure():
    """T5: revoke 导致 card read 崩溃 → status=rejected + snapshot 保留"""
    _setup_env()
    _cleanup()
    try:
        os.environ["IO_S_EVOLUTION_PATH"] = str(TEST_EVOLUTIONS)
        from boundary_evolution import append_proposal, update_status, snapshot, load_evolutions
        from meta_gate import MetaGate

        gate = MetaGate()

        # append revoke that breaks card read (unqualified pattern removal)
        pid = append_proposal(
            direction="revoke",
            action={"resource_type": "card", "operation": "read", "pattern": "*",
                    "mode": "remove", "target_pid": "p-test"},
            trigger={"signal_source": "test"},
        )

        records = load_evolutions()
        record = next(r for r in records if r["proposal_id"] == pid)

        # 双集验证失败 → rejected
        result = gate.evaluate(record)
        assert result["status"] == "rejected", \
            f"双集验证失败应 rejected，实际: {result['status']}"

        # 快照应保留
        snap_path = Path(str(TEST_EVOLUTIONS) + ".snapshot")
        assert snap_path.exists(), "验证失败后快照应保留"

        print("  ✅ T5: dual validation failure — PASS")
    finally:
        _cleanup()
        os.environ.pop("IO_S_EVOLUTION_PATH", None)


# ─────────────────────────────────────────────
# T6: verify_chain 篡改检测
# ─────────────────────────────────────────────
def test_t6_verify_chain_tampering():
    """T6: 手工改一行后 verify_chain 返回 False"""
    _setup_env()
    _cleanup()
    try:
        os.environ["IO_S_EVOLUTION_PATH"] = str(TEST_EVOLUTIONS)
        from boundary_evolution import append_proposal, verify_chain

        # 写入 3 条干净记录
        for i in range(3):
            append_proposal(direction="revoke",
                            action={"resource_type": "card", "operation": "write",
                                    "pattern": "*", "target_pid": f"p-{i}"})

        assert verify_chain(), "篡改前 chain 应通过"

        # 手工篡改第二行
        lines = TEST_EVOLUTIONS.read_text().strip().split("\n")
        assert len(lines) >= 2, f"应有 ≥2 行记录，实际 {len(lines)}"

        record = json.loads(lines[1])
        record["action"]["pattern"] = "TAMPERED"
        lines[1] = json.dumps(record, ensure_ascii=False)
        TEST_EVOLUTIONS.write_text("\n".join(lines) + "\n")

        assert not verify_chain(), "篡改后 chain 应检测到断裂"

        print("  ✅ T6: verify_chain tampering — PASS")
    finally:
        _cleanup()
        os.environ.pop("IO_S_EVOLUTION_PATH", None)


# ─────────────────────────────────────────────
# T7: B 分量激活（写 2 条演化记录后 compute_viability 含 B）
# ─────────────────────────────────────────────
def test_t7_b_component_activation():
    """T7: 无演化记录 B=None，写2条后 B 激活"""
    _setup_env()
    _cleanup()
    try:
        os.environ["IO_S_EVOLUTION_PATH"] = str(TEST_EVOLUTIONS)
        from boundary_evolution import append_proposal

        # 没有演化记录 → B=None
        from viability import component_boundary_consistency
        b_before = component_boundary_consistency()
        assert b_before is None, f"无演化记录 B 应为 None，实际: {b_before}"

        # 写 2 条演化记录
        append_proposal(direction="revoke",
                        action={"resource_type": "card", "operation": "write",
                                "pattern": "*", "target_pid": "p-1"})
        append_proposal(direction="revoke",
                        action={"resource_type": "card", "operation": "write",
                                "pattern": "*", "target_pid": "p-2"})

        # B 应激活
        b_after = component_boundary_consistency()
        assert b_after is not None, "有演化记录后 B 应激活（非 None）"
        assert isinstance(b_after, float), f"B 应为 float，实际: {type(b_after)}"
        assert 0.0 <= b_after <= 1.0, f"B 应在 [0,1]，实际: {b_after}"

        # compute_viability 包含 B
        from viability import compute_viability
        v = compute_viability()
        assert "B" in v["weights_used"], f"B 应在 weights_used 中: {v}"
        assert v["V"] is not None, f"V 应可计算: {v}"

        print(f"  ✅ T7: B activation (B={b_after}, V={v['V']}) — PASS")
    finally:
        _cleanup()
        os.environ.pop("IO_S_EVOLUTION_PATH", None)


# ─────────────────────────────────────────────
# T8: cap_policy.json 文件 mtime 不变
# ─────────────────────────────────────────────
def test_t8_cap_policy_mtime_unchanged():
    """T8: 全流程执行后 cap_policy.json 未被写入"""
    _setup_env()
    _cleanup()
    try:
        os.environ["IO_S_EVOLUTION_PATH"] = str(TEST_EVOLUTIONS)
        from boundary_evolution import append_proposal, update_status, apply_evolutions

        # 记录 mtime
        cap_path = Path.home() / ".io-s" / "cap_policy.json"
        mtime_before = cap_path.stat().st_mtime

        # 执行完整 revoke 流程
        pid = append_proposal(direction="revoke",
                              action={"resource_type": "card", "operation": "write",
                                      "pattern": "*", "target_pid": "p-test"})
        update_status(pid, "validated")
        apply_evolutions(pid)

        # 验证 mtime 未变
        mtime_after = cap_path.stat().st_mtime
        assert mtime_before == mtime_after, \
            f"cap_policy.json mtime 不应变: {mtime_before} → {mtime_after}"

        print("  ✅ T8: cap_policy mtime unchanged — PASS")
    finally:
        _cleanup()
        os.environ.pop("IO_S_EVOLUTION_PATH", None)


# ─────────────────────────────────────────────
# MetaGate 独立测试
# ─────────────────────────────────────────────
def test_metagate_auto_directions():
    """MetaGate: revoke/narrow 自动通道，expand 转 pending_human"""
    _setup_env()
    _cleanup()
    try:
        os.environ["IO_S_EVOLUTION_PATH"] = str(TEST_EVOLUTIONS)
        from meta_gate import MetaGate

        gate = MetaGate()

        # revoke → auto (validated)
        r1 = gate.evaluate({
            "proposal_id": "test-revoke",
            "direction": "revoke",
            "action": {"resource_type": "card", "operation": "write",
                       "pattern": "*", "target_pid": "p-test"},
        })
        assert r1["status"] == "validated", f"revoke 应 validated: {r1['status']}"

        # narrow → auto (validated)
        r2 = gate.evaluate({
            "proposal_id": "test-narrow",
            "direction": "narrow",
            "action": {"resource_type": "card", "operation": "write",
                       "pattern": "specific", "target_pid": "p-test"},
        })
        assert r2["status"] == "validated", f"narrow 应 validated: {r2['status']}"

        # expand → pending_human
        r3 = gate.evaluate({
            "proposal_id": "test-expand",
            "direction": "expand",
            "action": {"resource_type": "card", "operation": "write",
                       "pattern": "*", "target_pid": "p-test"},
        })
        assert r3["status"] == "pending_human", f"expand 应 pending_human: {r3['status']}"

        print("  ✅ MetaGate auto directions — PASS")
    finally:
        _cleanup()
        os.environ.pop("IO_S_EVOLUTION_PATH", None)


# ─────────────────────────────────────────────
# main
# ─────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        ("T1 zero_root", test_t1_zero_root_intact),
        ("T2 backward compat", test_t2_no_evolution_backward_compat),
        ("T3 revoke flow", test_t3_revoke_full_flow),
        ("T4 expand pending", test_t4_expand_pending_human),
        ("T5 dual validation fail", test_t5_dual_validation_failure),
        ("T6 verify_chain", test_t6_verify_chain_tampering),
        ("T7 B activation", test_t7_b_component_activation),
        ("T8 mtime", test_t8_cap_policy_mtime_unchanged),
        ("MetaGate directions", test_metagate_auto_directions),
    ]

    passed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  ❌ {name} FAIL: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n  {passed}/{len(tests)} tests passed")

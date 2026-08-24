#!/usr/bin/env python3
"""IO-S Day2 快速验证 — card + signal + dream syscall"""

import sys
from pathlib import Path

_io_s = Path.home() / "io-s"
sys.path.insert(0, str(_io_s))

from kernel import Kernel
from syscall import register_all


def test_card_crud():
    """card create → read → append → archive 完整流程"""
    kernel = Kernel("test-card")
    register_all(kernel)

    # create
    r = kernel.dispatch("card_create", "test-card-crud", "测试卡片", ["test"], "Day2验证")
    assert r["ok"], f"card_create failed: {r}"
    assert r["card_id"] == "test-card-crud"

    # read
    card = kernel.dispatch("card_read", "test-card-crud")
    assert card is not None
    assert card["title"] == "测试卡片"
    assert "test" in card["keywords"]

    # append
    r = kernel.dispatch("card_append", "test-card-crud", "notes", "Day2测试笔记")
    assert r["ok"]

    # archive
    r = kernel.dispatch("card_archive", "test-card-crud")
    assert r["ok"]

    # list
    cards = kernel.dispatch("card_list")
    assert any(c["card_id"] == "test-card-crud" for c in cards)

    print("  ✅ test_card_crud PASS")


def test_signal_roundtrip():
    """signal send → recv 完整来回"""
    kernel = Kernel("test-signal")
    register_all(kernel)

    # send
    r = kernel.dispatch("signal_send", "test-agent", {"msg": "hello"})
    assert r["ok"]
    sig_id = r["signal_id"]

    # recv
    signals = kernel.dispatch("signal_recv", "test-agent")
    assert len(signals) >= 1
    assert signals[0]["body"]["msg"] == "hello"

    # broadcast
    r = kernel.dispatch("signal_broadcast", {"msg": "broadcast"})
    assert r["ok"]

    print("  ✅ test_signal_roundtrip PASS")


def test_dream():
    """dream report — 即使IAH未就绪也能运行"""
    kernel = Kernel("test-dream")
    register_all(kernel)

    r = kernel.dispatch("dream_report")
    assert r["ok"]
    # Dreaming需要Brain，Brain需要jikA——即使没有发现，框架也应该返回ok

    print("  ✅ test_dream PASS")


def test_agent_lifecycle():
    """agent spawn → status → kill"""
    kernel = Kernel("test-agent")
    register_all(kernel)

    r = kernel.dispatch("agent_spawn", "测试Agent", name="test-agent-v")
    assert r["ok"]

    status = kernel.dispatch("agent_status", "test-agent-v")
    assert status is not None
    assert status["status"] == "configured"

    r = kernel.dispatch("agent_kill", "test-agent-v")
    assert r["ok"]

    agents = kernel.dispatch("agent_list")
    assert any(a["agent_id"] == "test-agent-v" for a in agents)

    print("  ✅ test_agent_lifecycle PASS")


def test_monitor():
    """监控传感器 — IAH未就绪时的fallback路径"""
    from monitor.sensors import collect_all, sensor_card_health, sensor_d0_signal
    from monitor.baseline import init_baseline, update_baseline, report_baseline

    # 卡片健康
    ch = sensor_card_health()
    assert ch["card_count"] > 30  # IO-S运行在jikA上，应该有很多卡片
    assert "cold_rate" in ch

    # D₀ fallback
    d0 = sensor_d0_signal()
    assert d0["d0_mean"] == 2.0
    assert d0["source"] == "fallback"

    # 基线
    bl = init_baseline(d0)
    assert bl["d0_mean"] == 2.0

    bl2 = update_baseline({"d0_mean": 2.1, "d0_std": 0.4})
    assert abs(bl2["d0_mean"] - 2.03) < 0.01  # EWMA α=0.3: 0.3*2.1 + 0.7*2.0 = 2.03

    rp = report_baseline()
    assert rp["status"] == "✅ 正常"

    print("  ✅ test_monitor PASS")


if __name__ == "__main__":
    tests = [
        ("card CRUD", test_card_crud),
        ("signal roundtrip", test_signal_roundtrip),
        ("dream report", test_dream),
        ("agent lifecycle", test_agent_lifecycle),
        ("monitor sensors", test_monitor),
    ]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  ❌ {name} FAIL: {e}")

    print(f"\n  {passed}/{len(tests)} tests passed")

#!/usr/bin/env python3
"""io-s syscall 注册中心"""

def register_all(kernel):
    """注册所有系统调用到内核。"""
    from .card import card_create, card_read, card_append, card_archive, card_split, card_list, batch_commit
    from .signal import signal_send, signal_recv, signal_broadcast
    from .dream import dream_launch, dream_report
    from .agent import agent_spawn, agent_kill, agent_status, agent_list
    from .planner import register as register_planner
    from .audit import register as register_audit
    from .sanitizer import register as register_sanitizer
    from .gate_sync import register as register_gate
    from .pipeline import register as register_pipeline
    from .cache_strategy import register as register_cache
    from .self_evolve import register as register_evolve
    from .team_scale import register as register_team
    from .gamma_monitor import register as register_gamma
    from .prospective_memory import register as register_pm
    from .economy import register as register_economy
    from .conservation_law import register as register_conservation
    from .calibrate import register as register_calibrate
    from .skill_discovery import register as register_skill_discovery

    register_planner(kernel)
    register_audit(kernel)
    register_sanitizer(kernel)
    register_gate(kernel)
    register_pipeline(kernel)
    register_cache(kernel)
    register_evolve(kernel)
    register_team(kernel)
    register_gamma(kernel)
    register_pm(kernel)
    register_economy(kernel)
    register_conservation(kernel)
    register_calibrate(kernel)
    register_skill_discovery(kernel)
    kernel.register("card_create", card_create)
    kernel.register("card_read", card_read)
    kernel.register("card_append", card_append)
    kernel.register("card_archive", card_archive)
    kernel.register("card_split", card_split)
    kernel.register("card_list", card_list)
    kernel.register("batch_commit", batch_commit)

    kernel.register("signal_send", signal_send)
    kernel.register("signal_recv", signal_recv)
    kernel.register("signal_broadcast", signal_broadcast)

    kernel.register("dream_launch", dream_launch)
    kernel.register("dream_report", dream_report)

    kernel.register("agent_spawn", agent_spawn)
    kernel.register("agent_kill", agent_kill)
    kernel.register("agent_status", agent_status)
    kernel.register("agent_list", agent_list)

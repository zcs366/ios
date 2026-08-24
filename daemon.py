#!/usr/bin/env python3
"""
IO-S Daemon v2 — 自愈守护进程
===============================
核心功能:
  1. 启动 IO-S HTTP server (port 8770)
  2. 心跳线程: 每10秒更新 kernel.json timestamp
  3. 监控: server 崩溃后自动重启 (最多3次/5分钟, 超限停止)
  4. 日志轮转: 超过1MB自动截断
  5. 信号处理: SIGTERM → 干净退出
  6. 自启动: 配合 crontab @reboot

用法:
  python3 daemon.py start       # 启动 (前台)
  nohup python3 daemon.py &     # 后台常驻
  python3 daemon.py stop        # 干净停止
  python3 daemon.py status      # 查看状态
  python3 daemon.py setup-cron  # 安装自启动
"""

import json
import os
import signal
import subprocess
import sys
import time
import threading
from pathlib import Path
from datetime import datetime, timezone

# ── 路径 ──
IO_S_HOME = Path.home() / ".io-s"
KERNEL_JSON = IO_S_HOME / "kernel.json"
SERVER_SCRIPT = Path(__file__).parent / "server.py"
PID_FILE = IO_S_HOME / "daemon.pid"
LOG_FILE = IO_S_HOME / "daemon.log"
LOG_MAX_BYTES = 1_048_576  # 1MB
CRASH_MAX = 3               # 最大连续崩溃次数
CRASH_WINDOW = 300          # 时间窗口 (5分钟)
HEARTBEAT_INTERVAL = 10     # 秒
SERVER_CHECK_INTERVAL = 15  # 秒

# 全局状态
_running = True
_server_proc: subprocess.Popen | None = None
_crash_times: list[float] = []  # 崩溃时间戳列表


def log(msg: str):
    """日志: 同时写文件 + stdout，超1MB自动截断"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        # 日志轮转: 超1MB则保留最后100行
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
            with open(LOG_FILE, "r") as f:
                lines = f.readlines()
            with open(LOG_FILE, "w") as f:
                f.writelines(lines[-100:])
            print(f"[{ts}] 📄 Log rotated ({LOG_MAX_BYTES//1024}KB limit)")
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def update_heartbeat() -> bool:
    """更新 kernel.json 的 timestamp (心跳)"""
    if not KERNEL_JSON.exists():
        return False
    try:
        state = json.loads(KERNEL_JSON.read_text())
        state["timestamp"] = datetime.now(timezone.utc).isoformat()
        state["daemon_pid"] = os.getpid()
        KERNEL_JSON.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        return True
    except Exception as e:
        log(f"⚠️ Heartbeat write failed: {e}")
        return False


def start_server() -> subprocess.Popen:
    """启动 IO-S HTTP server 子进程（输出直接写入daemon.log）"""
    log("⚙️  Starting IO-S server...")
    log_fh = open(str(LOG_FILE), "a")
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT)],
        stdout=log_fh,
        stderr=log_fh,
    )
    log(f"✅ Server started (PID {proc.pid})")
    return proc


def heartbeat_loop():
    """心跳线程: 每 HEARTBEAT_INTERVAL 秒写一次 timestamp"""
    global _running
    # 初始心跳
    update_heartbeat()
    while _running:
        time.sleep(HEARTBEAT_INTERVAL)
        if _running:
            update_heartbeat()


def _should_restart() -> bool:
    """检查是否应该重启。同一窗口内3次崩溃则停止。"""
    global _crash_times
    now = time.time()
    # 清理窗口外的旧记录
    _crash_times = [t for t in _crash_times if now - t < CRASH_WINDOW]
    if len(_crash_times) >= CRASH_MAX:
        log(f"🔴 Crash limit reached: {CRASH_MAX} crashes in {CRASH_WINDOW//60}min, stopping")
        return False
    _crash_times.append(now)
    return True


def _capture_server_logs():
    """服务器子进程日志已写入daemon.log，无需额外捕获"""
    pass


def monitor_loop():
    """监控循环: 检查 server 进程存活, 崩溃自动重启 (限速)"""
    global _server_proc, _running

    while _running:
        time.sleep(SERVER_CHECK_INTERVAL)

        if not _running:
            break

        if _server_proc is None:
            _server_proc = start_server()
            continue

        retcode = _server_proc.poll()
        if retcode is not None:
            log(f"🔴 Server crashed (exit code {retcode})")
            # 捕获崩溃日志
            _capture_server_logs()

            # 检查是否应该重启
            if _should_restart():
                log("🔄 Restarting...")
                time.sleep(2)  # 防快速重启
                _server_proc = start_server()
            else:
                _running = False
                log("🛑 IO-S daemon stopped (crash limit exceeded)")
                break


def clean_shutdown():
    """干净退出 — 标记IO-S为stopped + 清理PID"""
    global _running
    _running = False

    log("⏹  Shutting down IO-S daemon...")

    # 更新 kernel.json 为 stopped
    if KERNEL_JSON.exists():
        try:
            state = json.loads(KERNEL_JSON.read_text())
            state["status"] = "stopped"
            state["timestamp"] = datetime.now(timezone.utc).isoformat()
            KERNEL_JSON.write_text(json.dumps(state, ensure_ascii=False, indent=2))
            log("📄 kernel.json → status=stopped")
        except Exception as e:
            log(f"⚠️  kernel.json update failed: {e}")

    # 终止 server 子进程
    if _server_proc and _server_proc.poll() is None:
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=5)
            log(f"✅ Server process terminated")
        except subprocess.TimeoutExpired:
            _server_proc.kill()
            log("⚠️  Server process killed (timeout)")

    # 清理 PID 文件
    if PID_FILE.exists():
        PID_FILE.unlink()

    log("✅ IO-S daemon stopped")


def signal_handler(signum, frame):
    """信号处理: SIGTERM/SIGINT → 干净退出"""
    log(f"📡 Received signal {signum}")
    clean_shutdown()
    sys.exit(0)


# ═══════════════════════════════════════
# 命令行入口
# ═══════════════════════════════════════

def cmd_start():
    global _server_proc
    log("🚀 IO-S daemon starting...")

    # 注册信号处理
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # 写 PID 文件
    IO_S_HOME.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))

    # 启动 server
    _server_proc = start_server()

    # 标记 running (等第一次HTTP请求太慢)
    IO_S_HOME.mkdir(parents=True, exist_ok=True)
    init_status = {
        "agent_id": "io-s-kernel",
        "status": "running",
        "uptime_s": 0,
        "syscall_count": 0,
        "registered_syscalls": 0,
        "daemon_pid": os.getpid(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    KERNEL_JSON.write_text(json.dumps(init_status, ensure_ascii=False, indent=2))
    log("📄 kernel.json → status=running (daemon init)")

    # 启动心跳线程
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    log("💓 Heartbeat thread started (interval={}s)".format(HEARTBEAT_INTERVAL))

    # 主线程: 监控循环
    try:
        monitor_loop()
    except KeyboardInterrupt:
        clean_shutdown()


def cmd_stop():
    if not PID_FILE.exists():
        print("[IO-S daemon] Not running (no PID file)")
        return
    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"[IO-S daemon] Sent SIGTERM to PID {pid}")
        # 等待退出
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(0.5)
            except ProcessLookupError:
                print("[IO-S daemon] Stopped")
                return
        print("[IO-S daemon] Timeout waiting for stop — use kill -9 manually")
    except ProcessLookupError:
        print("[IO-S daemon] Stale PID file (process not running)")
        PID_FILE.unlink()


def cmd_status():
    if not PID_FILE.exists():
        print("[IO-S daemon] ⚪ Not running")
        return
    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, 0)  # 检查进程
        print(f"[IO-S daemon] 🟢 Running (PID {pid})")
        # 检查 kernel.json
        if KERNEL_JSON.exists():
            state = json.loads(KERNEL_JSON.read_text())
            ts = state.get("timestamp", "?")
            print(f"               kernel.json: status={state.get('status')}, timestamp={ts[:19]}")
        # 检查端口
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = s.connect_ex(("127.0.0.1", 8770))
        s.close()
        print(f"               Port 8770: {'🟢 OPEN' if result == 0 else '🔴 CLOSED'}")
    except ProcessLookupError:
        print("[IO-S daemon] 🔴 Stale PID file (process not found)")
        PID_FILE.unlink()


def cmd_setup_cron():
    """安装自启动: crontab @reboot 启动 daemon"""
    cron_line = f"@reboot cd {Path.home() / 'io-s'} && nohup python3 daemon.py start >> daemon.log 2>&1"
    # 检查是否已安装
    import subprocess as sp
    try:
        result = sp.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        current = result.stdout
        if cron_line in current:
            print("[IO-S daemon] ✅ Auto-start already installed in crontab")
            return
        new_cron = current.strip() + "\n" + cron_line + "\n"
        sp.run(["crontab", "-"], input=new_cron, text=True, timeout=5)
        print("[IO-S daemon] ✅ Auto-start installed in crontab")
        print(f"  {cron_line}")
    except FileNotFoundError:
        print("[IO-S daemon] ⚠️  crontab not available (WSL without cron service)")
        print("  手动启动: nohup python3 ~/io-s/daemon.py start &")
    except Exception as e:
        print(f"[IO-S daemon] ⚠️  crontab setup failed: {e}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "start":
        cmd_start()
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "status":
        cmd_status()
    elif cmd == "restart":
        cmd_stop()
        time.sleep(1)
        cmd_start()
    elif cmd == "setup-cron":
        cmd_setup_cron()
    else:
        print(f"Usage: python3 {sys.argv[0]} [start|stop|status|restart|setup-cron]")
        sys.exit(1)

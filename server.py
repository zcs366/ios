#!/usr/bin/env python3
"""
IO-S HTTP Dispatch Server — ISA通过此服务发送syscall给IO-S

接口:
  POST /syscall
    Request:  {syscall, args, caller_pid, trace_id}
    Response: {ok, data, error: {code, message}, trace_id}

  GET /health
    Response: {status, syscall_count, uptime}

端口: 8765
"""

import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

_IO_S = Path.home() / "io-s"
if str(_IO_S) not in sys.path:
    sys.path.insert(0, str(_IO_S))

from kernel import get_kernel
from syscall import register_all


class DispatchHandler(BaseHTTPRequestHandler):
    """处理ISA的syscall请求。"""

    # 共享内核实例（所有请求共享，避免重复初始化）
    _kernel_initialized = False

    def _init_kernel(self):
        if not DispatchHandler._kernel_initialized:
            self.kernel = get_kernel()
            register_all(self.kernel)
            self.kernel.write_status()
            DispatchHandler._kernel_initialized = True
            DispatchHandler._kernel = self.kernel
        else:
            self.kernel = DispatchHandler._kernel

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-IO-S", f"io-s-kernel/{self.kernel.heartbeat().get('syscall_count', 0)}")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_POST(self):
        self._init_kernel()

        if self.path == "/syscall":
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return self._send_json({
                    "ok": False, "data": None,
                    "error": {"code": "invalid_args", "message": "empty body"},
                    "trace_id": ""
                }, 400)

            try:
                body = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return self._send_json({
                    "ok": False, "data": None,
                    "error": {"code": "invalid_args", "message": "invalid JSON"},
                    "trace_id": ""
                }, 400)

            syscall = body.get("syscall", "")
            args = body.get("args", {})
            caller_pid = body.get("caller_pid", "")
            trace_id = body.get("trace_id", "")

            # 调用kernel.dispatch() — 含cap_check + 标准信封
            result = self.kernel.dispatch(
                name=syscall,
                caller_pid=caller_pid,
                args=args,
                trace_id=trace_id,
                process_cap=None,  # ISA传cap? 还是从ProcessTable查?
                                   # 当前: ISA不传cap → kernel做静态cap_check
            )

            self._send_json(result)
            self.kernel.write_status()  # 每次请求后刷新kernel.json

        else:
            self._send_json({
                "ok": False, "data": None,
                "error": {"code": "not_found", "message": f"unknown path: {self.path}"},
                "trace_id": ""
            }, 404)

    def do_GET(self):
        self._init_kernel()

        if self.path == "/health":
            hb = self.kernel.heartbeat()
            self._send_json({
                "status": "running",
                "syscall_count": hb["syscall_count"],
                "uptime_s": hb["uptime_s"],
                "zero_root": hb["zero_root"],
                "registered_syscalls": hb["registered_syscalls"],
            })
        else:
            self._send_json({"error": "not_found"}, 404)

    def log_message(self, format, *args):
        """静默模式——不输出HTTP访问日志。调试时可开启。"""
        pass  # sys.stderr.write(f"[IO-S] {args}\n")


def serve(host: str = "127.0.0.1", port: int = 8770):
    """启动IO-S HTTP Dispatch Server。"""
    server = HTTPServer((host, port), DispatchHandler)
    print(f"⚙️ IO-S dispatch server: http://{host}:{port}/syscall")
    print(f"   health: http://{host}:{port}/health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n⏹  IO-S dispatch server stopped")
        server.server_close()


if __name__ == "__main__":
    serve()

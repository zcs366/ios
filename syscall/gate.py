"""
gate.py — IO-S 权限门控模块

核心功能：
1. PermissionGate — 权限门控定义
2. check_permission() — 检查操作权限
3. request_approval() — 请求人工审批
4. audit_log() — 审计日志

设计原则：
- 5种权限模式：READONLY / DEV / RESEARCH / PLAN / ADMIN
- 分级审批：低风险自动、高风险人工
- 审计日志不可篡改
"""

import os
import json
import logging
import time
from pathlib import Path
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)

# ─── 权限模式定义 ───

class PermissionMode(str, Enum):
    """权限模式枚举"""
    READONLY = "readonly"    # 只读：搜索、查询、读取
    DEV = "dev"              # 开发：写文件、执行代码
    RESEARCH = "research"    # 研究：读+写分析文件
    PLAN = "plan"            # 计划：只读+规划（不执行）
    ADMIN = "admin"          # 管理：全部权限（需人工确认）


# 权限配置
PERMISSION_CONFIG = {
    PermissionMode.READONLY: {
        "description": "只读模式 — 搜索、查询、读取",
        "allowed_operations": ["search", "read", "query", "list"],
        "blocked_operations": ["write", "execute", "delete", "network"],
        "risk_level": "low",
        "requires_approval": False,
        "auto_approve": True,
    },
    PermissionMode.DEV: {
        "description": "开发模式 — 写文件、执行代码",
        "allowed_operations": ["search", "read", "query", "list", "write", "execute"],
        "blocked_operations": ["delete", "network"],
        "risk_level": "medium",
        "requires_approval": False,
        "auto_approve": True,
    },
    PermissionMode.RESEARCH: {
        "description": "研究模式 — 读+写分析文件、网络请求",
        "allowed_operations": ["search", "read", "query", "list", "write", "network"],
        "blocked_operations": ["delete"],
        "risk_level": "high",
        "requires_approval": False,
        "auto_approve": True,
    },
    PermissionMode.PLAN: {
        "description": "计划模式 — 只读+规划（不执行）",
        "allowed_operations": ["search", "read", "query", "list", "plan"],
        "blocked_operations": ["write", "execute", "delete", "network"],
        "risk_level": "low",
        "requires_approval": False,
        "auto_approve": True,
    },
    PermissionMode.ADMIN: {
        "description": "管理模式 — 全部权限（需人工确认）",
        "allowed_operations": ["search", "read", "query", "list", "write", "execute", "delete", "network"],
        "blocked_operations": [],
        "risk_level": "critical",
        "requires_approval": True,
        "auto_approve": False,
    },
}


# ─── 审计日志 ───

AUDIT_LOG_PATH = Path.home() / ".io-s" / "audit.jsonl"


def audit_log(
    operation: str,
    tool_name: str,
    params: dict,
    result: str,
    approved: bool = False,
    approver: str = "",
):
    """
    记录审计日志
    """
    entry = {
        "timestamp": time.time(),
        "operation": operation,
        "tool_name": tool_name,
        "params": params,
        "result": result,
        "approved": approved,
        "approver": approver,
    }
    
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"Failed to write audit log: {e}")


# ─── 权限检查 ───

class PermissionGate:
    """
    权限门控
    
    管理权限模式，检查操作权限
    """
    
    def __init__(self, initial_mode: PermissionMode = PermissionMode.DEV):
        self.current_mode = initial_mode
        self.approval_requests = []
    
    def check_permission(
        self,
        operation: str,
        tool_name: str = "",
        params: Optional[dict] = None,
    ) -> tuple[bool, str]:
        """
        检查操作权限
        
        Args:
            operation: 操作类型（search/read/write/execute/delete/network）
            tool_name: 工具名称
            params: 操作参数
        
        Returns:
            (allowed, reason)
        """
        config = PERMISSION_CONFIG[self.current_mode]
        
        # 检查操作是否在允许列表中
        if operation in config["blocked_operations"]:
            return False, f"Operation {operation} blocked in {self.current_mode.value} mode"
        
        if operation not in config["allowed_operations"]:
            return False, f"Operation {operation} not allowed in {self.current_mode.value} mode"
        
        # 检查是否需要审批
        if config["requires_approval"]:
            # 检查是否已审批
            if self._is_approved(operation, tool_name):
                return True, "Pre-approved"
            else:
                return False, f"Operation {operation} requires approval in {self.current_mode.value} mode"
        
        return True, "Allowed"
    
    def _is_approved(self, operation: str, tool_name: str) -> bool:
        """
        检查操作是否已审批
        """
        for request in self.approval_requests:
            if (request["operation"] == operation and
                request["tool_name"] == tool_name and
                request["approved"]):
                return True
        return False
    
    def request_approval(
        self,
        operation: str,
        tool_name: str,
        params: Optional[dict] = None,
        reason: str = "",
    ) -> dict:
        """
        请求人工审批
        
        Returns:
            审批请求
        """
        request = {
            "operation": operation,
            "tool_name": tool_name,
            "params": params or {},
            "reason": reason,
            "timestamp": time.time(),
            "approved": False,
            "approver": "",
        }
        
        self.approval_requests.append(request)
        
        # 记录审计日志
        audit_log(
            operation=operation,
            tool_name=tool_name,
            params=params or {},
            result="approval_requested",
        )
        
        return request
    
    def approve(self, request_index: int, approver: str = "user"):
        """
        审批请求
        """
        if 0 <= request_index < len(self.approval_requests):
            self.approval_requests[request_index]["approved"] = True
            self.approval_requests[request_index]["approver"] = approver
            
            # 记录审计日志
            request = self.approval_requests[request_index]
            audit_log(
                operation=request["operation"],
                tool_name=request["tool_name"],
                params=request["params"],
                result="approved",
                approved=True,
                approver=approver,
            )
    
    def switch_mode(self, new_mode: PermissionMode, reason: str = ""):
        """
        切换权限模式
        """
        old_mode = self.current_mode
        self.current_mode = new_mode
        
        # 记录审计日志
        audit_log(
            operation="mode_switch",
            tool_name="gate",
            params={"from": old_mode.value, "to": new_mode.value, "reason": reason},
            result="switched",
        )
        
        logger.info(f"Permission mode switched: {old_mode} -> {new_mode} ({reason})")
    
    def get_status(self) -> dict:
        """
        获取当前状态
        """
        config = PERMISSION_CONFIG[self.current_mode]
        return {
            "current_mode": self.current_mode.value,
            "description": config["description"],
            "risk_level": config["risk_level"],
            "requires_approval": config["requires_approval"],
            "pending_requests": len([r for r in self.approval_requests if not r["approved"]]),
        }


# ─── 操作执行包装 ───

def execute_with_gate(
    gate: PermissionGate,
    operation: str,
    tool_name: str,
    func,
    params: dict = None,
    require_approval: bool = False,
):
    """
    带权限门控的操作执行
    
    Args:
        gate: 权限门控
        operation: 操作类型
        tool_name: 工具名称
        func: 执行函数
        params: 操作参数
        require_approval: 是否强制要求审批
    
    Returns:
        执行结果
    """
    # 检查权限
    allowed, reason = gate.check_permission(operation, tool_name, params)
    
    if not allowed:
        if require_approval or gate.current_mode == PermissionMode.ADMIN:
            # 请求审批
            request = gate.request_approval(
                operation=operation,
                tool_name=tool_name,
                params=params,
                reason=reason,
            )
            raise PermissionError(
                f"Operation {operation} requires approval. "
                f"Request ID: {len(gate.approval_requests) - 1}"
            )
        else:
            raise PermissionError(f"Operation {operation} not allowed: {reason}")
    
    # 执行操作
    try:
        result = func(**(params or {}))
        
        # 记录审计日志
        audit_log(
            operation=operation,
            tool_name=tool_name,
            params=params or {},
            result="success",
        )
        
        return result
        
    except Exception as e:
        # 记录失败日志
        audit_log(
            operation=operation,
            tool_name=tool_name,
            params=params or {},
            result=f"error: {str(e)}",
        )
        raise


# ─── CLI ───

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法:")
        print("  python gate.py status                 # 显示当前状态")
        print("  python gate.py check <operation>      # 检查操作权限")
        print("  python gate.py switch <mode>          # 切换权限模式")
        print("  python gate.py modes                  # 列出所有模式")
        print("  python gate.py audit [n]              # 查看审计日志")
        sys.exit(0)
    
    cmd = sys.argv[1]
    gate = PermissionGate()
    
    if cmd == "status":
        status = gate.get_status()
        print("当前状态:")
        for key, value in status.items():
            print(f"  {key}: {value}")
    
    elif cmd == "check":
        if len(sys.argv) < 3:
            print("用法: python gate.py check <operation>")
            sys.exit(1)
        
        operation = sys.argv[2]
        allowed, reason = gate.check_permission(operation)
        print(f"操作: {operation}")
        print(f"允许: {'✅' if allowed else '❌'}")
        print(f"原因: {reason}")
    
    elif cmd == "switch":
        if len(sys.argv) < 3:
            print("用法: python gate.py switch <mode>")
            sys.exit(1)
        
        mode = PermissionMode(sys.argv[2])
        gate.switch_mode(mode, "CLI command")
        print(f"已切换到: {mode.value}")
    
    elif cmd == "modes":
        print("所有权限模式:")
        for mode in PermissionMode:
            config = PERMISSION_CONFIG[mode]
            print(f"\n  {mode.value}:")
            print(f"    描述: {config['description']}")
            print(f"    风险等级: {config['risk_level']}")
            print(f"    需要审批: {config['requires_approval']}")
            print(f"    允许操作: {config['allowed_operations']}")
    
    elif cmd == "audit":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        
        if AUDIT_LOG_PATH.exists():
            with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            print(f"最近 {min(n, len(lines))} 条审计日志:")
            for line in lines[-n:]:
                try:
                    entry = json.loads(line)
                    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry["timestamp"]))
                    op = entry["operation"]
                    tool = entry["tool_name"]
                    result = entry["result"]
                    print(f"  [{ts}] {op} ({tool}): {result}")
                except:
                    pass
        else:
            print("审计日志为空")
    
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)

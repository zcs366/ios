"""
tool_scope.py — IO-S 工具作用域治理模块

核心功能：
1. ToolScope — 工具作用域定义
2. get_tools_for_scope() — 根据作用域过滤工具
3. get_scope_for_task() — 根据任务类型推荐作用域
4. dynamic_scope_switch() — 动态切换作用域

设计原则：
- 4种预定义作用域：readonly / dev / research / admin
- 按任务阶段动态切换工具集
- 对接 ISN metadata（risk_level + permission_level + side_effects）
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)

# ─── 作用域定义 ───

class ToolScope(str, Enum):
    """工具作用域枚举"""
    READONLY = "readonly"      # 只读：搜索、查询、读取文件
    DEV = "dev"                # 开发：写文件、执行代码、终端命令
    RESEARCH = "research"      # 研究：读+写分析文件、网络请求
    ADMIN = "admin"            # 管理：全部权限（需人工确认）


# 作用域配置
SCOPE_CONFIG = {
    ToolScope.READONLY: {
        "description": "只读模式 — 搜索、查询、读取文件",
        "allowed_types": ["search", "read", "query"],
        "blocked_types": ["write", "execute", "network", "delete"],
        "risk_threshold": "low",
        "requires_approval": False,
    },
    ToolScope.DEV: {
        "description": "开发模式 — 写文件、执行代码、终端命令",
        "allowed_types": ["search", "read", "query", "write", "execute"],
        "blocked_types": ["delete", "network"],
        "risk_threshold": "medium",
        "requires_approval": False,
    },
    ToolScope.RESEARCH: {
        "description": "研究模式 — 读+写分析文件、网络请求",
        "allowed_types": ["search", "read", "query", "write", "network"],
        "blocked_types": ["delete"],
        "risk_threshold": "high",
        "requires_approval": False,
    },
    ToolScope.ADMIN: {
        "description": "管理模式 — 全部权限（需人工确认）",
        "allowed_types": ["search", "read", "query", "write", "execute", "network", "delete"],
        "blocked_types": [],
        "risk_threshold": "critical",
        "requires_approval": True,
    },
}


# ─── 工具元数据加载 ───

def load_isn_metadata() -> list[dict]:
    """
    加载 ISN 的工具元数据
    
    优先从 ISN integration.py 加载，fallback 到 index.yaml
    """
    # 优先使用 ISN integration.py
    try:
        sys.path.insert(0, str(Path.home() / "isn"))
        from isn.router.integration import export_tool_metadata
        metadata = export_tool_metadata()
        if metadata:
            logger.info(f"Loaded {len(metadata)} tools from ISN integration")
            return metadata
    except Exception as e:
        logger.warning(f"Failed to load from ISN integration: {e}")
    
    # Fallback: 从 index.yaml 加载
    index_path = Path.home() / "isn" / "store" / "index.yaml"
    if not index_path.exists():
        logger.warning(f"ISN index not found: {index_path}")
        return []
    
    try:
        import yaml
        with open(index_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        
        if not data or "skills" not in data:
            return []
        
        return data["skills"]
    except Exception as e:
        logger.warning(f"Failed to load ISN metadata: {e}")
        return []


def get_tool_metadata(tool_name: str) -> Optional[dict]:
    """
    获取单个工具的元数据
    """
    skills = load_isn_metadata()
    for skill in skills:
        if skill.get("name") == tool_name or skill.get("skill_id") == tool_name:
            return skill
    return None


# ─── 作用域过滤 ───

def get_tools_for_scope(
    scope: ToolScope,
    tools: list[dict] = None,
) -> list[dict]:
    """
    根据作用域过滤工具
    
    Args:
        scope: 工具作用域
        tools: 工具列表（如果为None，从ISN加载）
    
    Returns:
        过滤后的工具列表
    """
    if tools is None:
        tools = load_isn_metadata()
    
    config = SCOPE_CONFIG[scope]
    risk_threshold = config["risk_threshold"]
    allowed_types = config["allowed_types"]
    
    # 风险等级优先级
    risk_priority = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    threshold_priority = risk_priority.get(risk_threshold, 3)
    
    filtered = []
    for tool in tools:
        # 检查风险等级
        tool_risk = tool.get("risk_level", "low")
        tool_risk_priority = risk_priority.get(tool_risk, 0)
        
        if tool_risk_priority > threshold_priority:
            continue  # 风险超过阈值，过滤掉
        
        # 检查工具类型
        tool_type = tool.get("type", "tool")
        if tool_type not in allowed_types:
            continue  # 类型不在允许列表中
        
        filtered.append(tool)
    
    return filtered


def get_scope_for_task(task_type: str) -> ToolScope:
    """
    根据任务类型推荐作用域
    
    Args:
        task_type: 任务类型
    
    Returns:
        推荐的作用域
    """
    # 任务类型到作用域的映射
    task_scope_map = {
        # 只读任务
        "search": ToolScope.READONLY,
        "query": ToolScope.READONLY,
        "read": ToolScope.READONLY,
        "research": ToolScope.READONLY,
        
        # 开发任务
        "code_generation": ToolScope.DEV,
        "code_review": ToolScope.DEV,
        "debugging": ToolScope.DEV,
        "testing": ToolScope.DEV,
        
        # 研究任务
        "analysis": ToolScope.RESEARCH,
        "documentation": ToolScope.RESEARCH,
        "architecture_design": ToolScope.RESEARCH,
        "planning": ToolScope.RESEARCH,
        
        # 管理任务
        "system_admin": ToolScope.ADMIN,
        "deployment": ToolScope.ADMIN,
        "security_audit": ToolScope.ADMIN,
    }
    
    return task_scope_map.get(task_type, ToolScope.DEV)


# ─── 动态切换 ───

def dynamic_scope_switch(
    current_scope: ToolScope,
    task_type: str,
    context: dict = None,
) -> ToolScope:
    """
    动态切换作用域
    
    根据任务类型和上下文，自动切换到最合适的作用域
    
    Args:
        current_scope: 当前作用域
        task_type: 任务类型
        context: 上下文信息（如用户确认、风险评估等）
    
    Returns:
        推荐的新作用域
    """
    recommended = get_scope_for_task(task_type)
    
    # 如果推荐的作用域比当前作用域权限更高
    scope_priority = {
        ToolScope.READONLY: 0,
        ToolScope.DEV: 1,
        ToolScope.RESEARCH: 2,
        ToolScope.ADMIN: 3,
    }
    
    if scope_priority[recommended] > scope_priority[current_scope]:
        # 需要提升权限
        if recommended == ToolScope.ADMIN:
            # ADMIN 需要人工确认
            if context and context.get("user_approved"):
                return ToolScope.ADMIN
            else:
                logger.info(f"Task {task_type} requires ADMIN scope, but user not approved")
                return current_scope
        else:
            # 其他权限提升，自动切换
            return recommended
    
    # 如果推荐的作用域比当前作用域权限更低
    # 保持当前作用域（不降级）
    return current_scope


# ─── 工具裁剪 ───

def filter_tool_params(
    tool_name: str,
    params: dict,
    scope: ToolScope,
) -> dict:
    """
    根据作用域过滤工具参数
    
    Args:
        tool_name: 工具名称
        params: 工具参数
        scope: 当前作用域
    
    Returns:
        过滤后的参数
    """
    config = SCOPE_CONFIG[scope]
    
    # 获取工具元数据
    tool_meta = get_tool_metadata(tool_name)
    if not tool_meta:
        return params  # 没有元数据，不过滤
    
    # 检查工具是否在当前作用域内
    tool_type = tool_meta.get("type", "tool")
    if tool_type not in config["allowed_types"]:
        raise PermissionError(
            f"Tool {tool_name} (type={tool_type}) not allowed in scope {scope}"
        )
    
    # 检查风险等级
    tool_risk = tool_meta.get("risk_level", "low")
    risk_priority = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    threshold_priority = risk_priority.get(config["risk_threshold"], 3)
    
    if risk_priority.get(tool_risk, 0) > threshold_priority:
        raise PermissionError(
            f"Tool {tool_name} (risk={tool_risk}) exceeds threshold for scope {scope}"
        )
    
    # 根据作用域过滤参数
    if scope == ToolScope.READONLY:
        # 只读模式：移除写操作参数
        readonly_params = {}
        for key, value in params.items():
            if key not in ["write", "delete", "execute", "command"]:
                readonly_params[key] = value
        return readonly_params
    
    elif scope == ToolScope.DEV:
        # 开发模式：移除删除操作参数
        dev_params = {}
        for key, value in params.items():
            if key != "delete":
                dev_params[key] = value
        return dev_params
    
    # 其他作用域：不过滤
    return params


# ─── 作用域状态管理 ───

class ScopeManager:
    """
    作用域管理器
    
    管理当前作用域状态，支持动态切换
    """
    
    def __init__(self, initial_scope: ToolScope = ToolScope.DEV):
        self.current_scope = initial_scope
        self.history = []
    
    def switch_scope(self, new_scope: ToolScope, reason: str = ""):
        """
        切换作用域
        """
        old_scope = self.current_scope
        self.current_scope = new_scope
        
        self.history.append({
            "from": old_scope.value,
            "to": new_scope.value,
            "reason": reason,
        })
        
        logger.info(f"Scope switched: {old_scope} -> {new_scope} ({reason})")
    
    def get_allowed_tools(self, tools: list[dict] = None) -> list[dict]:
        """
        获取当前作用域允许的工具
        """
        return get_tools_for_scope(self.current_scope, tools)
    
    def check_tool_access(self, tool_name: str) -> bool:
        """
        检查工具是否在当前作用域内
        """
        tool_meta = get_tool_metadata(tool_name)
        if not tool_meta:
            return True  # 没有元数据，默认允许
        
        tool_type = tool_meta.get("type", "tool")
        config = SCOPE_CONFIG[self.current_scope]
        
        return tool_type in config["allowed_types"]
    
    def to_dict(self) -> dict:
        """
        导出状态
        """
        return {
            "current_scope": self.current_scope.value,
            "history": self.history[-10:],  # 最近10条记录
        }


# ─── CLI ───

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法:")
        print("  python tool_scope.py list [scope]        # 列出作用域内的工具")
        print("  python tool_scope.py check <tool> [scope] # 检查工具是否在作用域内")
        print("  python tool_scope.py recommend <task>     # 推荐作用域")
        print("  python tool_scope.py scopes               # 列出所有作用域")
        sys.exit(0)
    
    cmd = sys.argv[1]
    
    if cmd == "list":
        scope = ToolScope(sys.argv[2]) if len(sys.argv) > 2 else ToolScope.DEV
        tools = load_isn_metadata()
        filtered = get_tools_for_scope(scope, tools)
        
        print(f"作用域: {scope.value}")
        print(f"总工具数: {len(tools)}")
        print(f"允许工具数: {len(filtered)}")
        print("\n允许的工具:")
        for tool in filtered[:20]:
            name = tool.get("name", "?")
            risk = tool.get("risk_level", "?")
            print(f"  - {name} (risk={risk})")
    
    elif cmd == "check":
        if len(sys.argv) < 3:
            print("用法: python tool_scope.py check <tool_name> [scope]")
            sys.exit(1)
        
        tool_name = sys.argv[2]
        scope = ToolScope(sys.argv[3]) if len(sys.argv) > 3 else ToolScope.DEV
        
        allowed = get_tools_for_scope(scope)
        allowed_names = {t.get("name") for t in allowed}
        
        if tool_name in allowed_names:
            print(f"✅ {tool_name} 在 {scope.value} 作用域内")
        else:
            print(f"❌ {tool_name} 不在 {scope.value} 作用域内")
    
    elif cmd == "recommend":
        if len(sys.argv) < 3:
            print("用法: python tool_scope.py recommend <task_type>")
            sys.exit(1)
        
        task_type = sys.argv[2]
        scope = get_scope_for_task(task_type)
        print(f"任务类型: {task_type}")
        print(f"推荐作用域: {scope.value}")
        print(f"描述: {SCOPE_CONFIG[scope]['description']}")
    
    elif cmd == "scopes":
        print("所有作用域:")
        for scope in ToolScope:
            config = SCOPE_CONFIG[scope]
            print(f"\n  {scope.value}:")
            print(f"    描述: {config['description']}")
            print(f"    风险阈值: {config['risk_threshold']}")
            print(f"    允许类型: {config['allowed_types']}")
    
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)

"""
MCP 管理模块
读取 pi 的 mcp-cache.json（服务器定义 + 工具清单），提供服务器/工具浏览与搜索。
"""

import json
from datetime import datetime
from pathlib import Path

HOME = Path.home()
MCP_CACHE = HOME / ".pi" / "agent" / "mcp-cache.json"

# 可能的 MCP 独立配置文件（若存在也一并展示）
CONFIG_CANDIDATES = [
    HOME / ".pi" / "agent" / "mcp.json",
]


def _load_cache():
    if not MCP_CACHE.is_file():
        return None
    try:
        return json.loads(MCP_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _human_size(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _schema_summary(input_schema):
    """从 JSON Schema 提炼参数列表"""
    props = (input_schema or {}).get("properties", {})
    required = set((input_schema or {}).get("required", []) or [])
    out = []
    for name in sorted(props.keys()):
        p = props[name]
        if not isinstance(p, dict):
            continue
        t = p.get("type", "any")
        desc = (p.get("description") or "")[:60]
        item = {"name": name, "type": t, "required": name in required, "description": desc}
        out.append(item)
    return out


def mcp_servers():
    """服务器清单 + 概要"""
    cache = _load_cache()
    result = {
        "cache_path": str(MCP_CACHE),
        "cache_exists": MCP_CACHE.is_file(),
        "config_paths": [str(c) for c in CONFIG_CANDIDATES if c.is_file()],
        "servers": [],
        "total_tools": 0,
    }
    if not cache:
        return result
    servers = cache.get("servers", {})
    for name, info in sorted(servers.items()):
        if not isinstance(info, dict):
            continue
        tools = info.get("tools", []) or []
        tool_count = len(tools)
        result["total_tools"] += tool_count
        result["servers"].append({
            "name": name,
            "tool_count": tool_count,
            "config_hash": (info.get("configHash") or "")[:12],
            "tools_summary": [{"name": t.get("name"), "description": (t.get("description") or "")[:100]} for t in tools[:5]],
        })
    cache_stat = MCP_CACHE.stat()
    result["cache_size"] = cache_stat.st_size
    result["cache_size_human"] = _human_size(cache_stat.st_size)
    result["cache_mtime"] = datetime.fromtimestamp(cache_stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    return result


def mcp_tools(server_name=None, q=None, limit=300):
    """工具明细（按服务器过滤 + 关键词搜索）"""
    cache = _load_cache()
    if not cache:
        return {"error": "mcp-cache.json 不存在或无法解析"}
    servers = cache.get("servers", {})
    items = []
    for name, info in servers.items():
        if server_name and name != server_name:
            continue
        if not isinstance(info, dict):
            continue
        for t in info.get("tools", []) or []:
            if not isinstance(t, dict):
                continue
            tname = t.get("name", "")
            tdesc = t.get("description", "") or ""
            schema = t.get("inputSchema") or {}
            items.append({
                "server": name,
                "tool": tname,
                "full_name": f"{name}.{tname}",
                "description": tdesc,
                "params": _schema_summary(schema),
                "param_count": len(schema.get("properties", {})),
            })
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["tool"].lower() or ql in i["description"].lower() or ql in i["server"].lower()]
    return {"items": items[:limit], "total": len(items), "truncated": len(items) > limit}


def mcp_raw():
    """返回缓存原始 JSON（脱敏）"""
    cache = _load_cache()
    if cache is None:
        return {"error": "mcp-cache.json 不存在"}
    return {"content": json.dumps(cache, ensure_ascii=False, indent=2)[:500000]}
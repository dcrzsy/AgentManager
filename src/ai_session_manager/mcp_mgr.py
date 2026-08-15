"""
MCP 管理模块（多工具版）
从各 AI 客户端的 MCP 配置读取服务器定义，pi 的 mcp-cache.json 提供工具明细。
"""

import json
import re
from datetime import datetime
from pathlib import Path

HOME = Path.home()

# 各工具的 MCP 配置源
# kind: cache+tools（含工具缓存）/ json-servers（mcpServers 映射）/ toml（config.toml 的 [mcp_servers.*]）
MCP_SOURCES = [
    {"tool": "pi",     "name": "Pi",          "path": HOME / ".pi" / "agent" / "mcp-cache.json",   "kind": "cache+tools"},
    {"tool": "claude", "name": "Claude Code", "path": HOME / ".claude.json",                       "kind": "json-servers", "key": "mcpServers"},
    {"tool": "codex",  "name": "Codex",       "path": HOME / ".codex" / "config.toml",             "kind": "toml"},
    {"tool": "orca",   "name": "Orca",        "path": HOME / ".config" / "orca" / "codex-runtime-home" / "home" / "config.toml", "kind": "toml"},
    {"tool": "kimi",   "name": "Kimi Code",   "path": HOME / ".kimi-code" / "mcp.json",            "kind": "json-servers", "key": "mcpServers"},
    {"tool": "cline",  "name": "Cline",       "path": HOME / ".config" / "Cline" / "mcp_settings.json", "kind": "json-servers", "key": "mcpServers"},
    {"tool": "qoder",  "name": "Qoder",       "path": HOME / ".config" / "Qoder" / "SharedClientCache" / "mcp.json", "kind": "json-servers", "key": "mcpServers"},
    {"tool": "tdapp",  "name": "TDApp",       "path": HOME / ".config" / "TDAppDesktop" / "agent" / "agent-mcp-config.json", "kind": "raw-servers"},
    {"tool": "copilot","name": "Copilot",     "path": HOME / ".config" / "github-copilot" / "intellij" / "mcp.json", "kind": "json-servers", "key": "mcpServers"},
]


def _human_size(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _schema_summary(input_schema):
    props = (input_schema or {}).get("properties", {})
    required = set((input_schema or {}).get("required", []) or [])
    out = []
    for name in sorted(props.keys()):
        p = props[name]
        if not isinstance(p, dict):
            continue
        out.append({"name": name, "type": p.get("type", "any"), "required": name in required,
                    "description": (p.get("description") or "")[:60]})
    return out


def _mask_env(env):
    """env 变量名列表（值脱敏显示）"""
    if not isinstance(env, dict):
        return []
    return [{"key": k, "masked": bool(re.search(r"(key|token|secret|password)", k, re.I))} for k in env]


def _read_toml_mcp(path):
    """解析 config.toml 的 [mcp_servers.xxx] 段"""
    servers = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return servers
    # 逐段解析 [section] { key = value ... }
    current = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            continue
        if current and current.startswith("mcp_servers."):
            server_name = current[len("mcp_servers."):].strip('"').strip("'")
            m = re.match(r'^(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|\'[^\']*\'|[^\s#]+)', line)
            if m and server_name:
                key, val = m.group(1), m.group(2).strip('"').strip("'")
                sv = next((s for s in servers if s["name"] == server_name), None)
                if sv is None:
                    sv = {"name": server_name, "command": "", "args": [], "env_keys": [], "url": ""}
                    servers.append(sv)
                if key == "command":
                    sv["command"] = val
                elif key == "args":
                    sv["args"] = re.findall(r'"([^"]*)"|\'([^\']*)\'|(\S+)', val)[:10]
                    sv["args"] = [a or b or c for a, b, c in sv["args"]]
                elif key == "url":
                    sv["url"] = val
                elif key == "env":
                    sv["env_keys"] = [k.strip().strip('"') for k in val.split(",")] if val else []
    return servers


def active_sources():
    """返回配置文件真实存在的源"""
    out = []
    for s in MCP_SOURCES:
        if Path(s["path"]).is_file():
            out.append(s)
    return out


def mcp_servers():
    """所有源的服务器清单"""
    sources = []
    total_tools = 0
    total_servers = 0
    for s in active_sources():
        servers = []
        tool_count = 0
        path = Path(s["path"])
        try:
            if s["kind"] == "cache+tools":
                data = json.loads(path.read_text(encoding="utf-8"))
                for name, info in sorted((data.get("servers") or {}).items()):
                    if not isinstance(info, dict):
                        continue
                    tools = info.get("tools", []) or []
                    tool_count += len(tools)
                    servers.append({
                        "name": name,
                        "type": "stdio",
                        "detail": f"{len(tools)} 工具",
                        "tools": [{"name": t.get("name"), "description": (t.get("description") or "")[:80]} for t in tools[:3]],
                        "tool_count": len(tools),
                        "config_hash": (info.get("configHash") or "")[:12],
                    })
            elif s["kind"] == "json-servers":
                data = json.loads(path.read_text(encoding="utf-8"))
                mcp = data.get(s.get("key", "mcpServers")) or {}
                for name, cfg in sorted(mcp.items()):
                    if not isinstance(cfg, dict):
                        continue
                    kind = "url" if cfg.get("url") else ("stdio" if cfg.get("command") else "其他")
                    servers.append({
                        "name": name,
                        "type": kind,
                        "detail": (cfg.get("command") or cfg.get("url") or "")[:60] + (f"  env={len(cfg.get('env') or {})}" if cfg.get("env") else ""),
                        "tool_count": 0,
                        "env_keys": _mask_env(cfg.get("env") or {}),
                    })
            elif s["kind"] == "raw-servers":
                data = json.loads(path.read_text(encoding="utf-8"))
                # 兼容 {mcpServers: ...} 或 {servers: ...} 或直接映射
                mcp = data.get("mcpServers") or data.get("servers") or data
                if isinstance(mcp, dict):
                    for name, cfg in sorted(mcp.items()):
                        if isinstance(cfg, dict):
                            kind = "url" if cfg.get("url") else ("stdio" if cfg.get("command") else "其他")
                            servers.append({
                                "name": name, "type": kind,
                                "detail": (cfg.get("command") or cfg.get("url") or "")[:60],
                                "tool_count": 0, "env_keys": [],
                            })
            elif s["kind"] == "toml":
                servers = _read_toml_mcp(path)
                for sv in servers:
                    sv["tool_count"] = 0
                if not servers:
                    continue  # 无 mcp_servers 段的工具不展示
        except Exception as e:
            servers = [{"name": f"(解析失败: {e})", "type": "error", "detail": "", "tool_count": 0, "env_keys": []}]
        total_servers += len(servers)
        total_tools += tool_count
        stat = path.stat()
        sources.append({
            "tool": s["tool"],
            "tool_name": s["name"],
            "config_path": str(path),
            "kind": s["kind"],
            "server_count": len(servers),
            "tool_count": tool_count,
            "size": stat.st_size,
            "size_human": _human_size(stat.st_size),
            "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "servers": servers,
        })
    return {"sources": sources, "total_servers": total_servers, "total_tools": total_tools}


def mcp_tools(server_name=None, q=None, source_tool=None, limit=300):
    """工具明细：一般只来自 pi 的 cache；其他源无工具缓存"""
    items = []
    for s in active_sources():
        if source_tool and s["tool"] != source_tool:
            continue
        if s["kind"] != "cache+tools":
            continue
        try:
            data = json.loads(Path(s["path"]).read_text(encoding="utf-8"))
        except Exception:
            continue
        for name, info in (data.get("servers") or {}).items():
            if server_name and name != server_name:
                continue
            if not isinstance(info, dict):
                continue
            for t in info.get("tools", []) or []:
                if not isinstance(t, dict):
                    continue
                tname = t.get("name", "")
                items.append({
                    "server": name,
                    "tool": tname,
                    "full_name": f"{name}.{tname}",
                    "description": t.get("description", "") or "",
                    "params": _schema_summary(t.get("inputSchema")),
                    "param_count": len((t.get("inputSchema") or {}).get("properties", {})),
                })
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["tool"].lower() or ql in i["description"].lower() or ql in i["server"].lower()]
    return {"items": items[:limit], "total": len(items), "truncated": len(items) > limit}


def mcp_raw(source_tool="pi", limit=500000):
    """返回指定源的原始配置（脱敏）"""
    for s in active_sources():
        if s["tool"] == source_tool:
            try:
                content = Path(s["path"]).read_text(encoding="utf-8", errors="replace")
                if s["kind"] == "toml":
                    # toml 整体脱敏：key 含敏感词的 value 打码
                    content = re.sub(r'^(\s*[A-Za-z0-9_.-]*(?:key|token|secret|password)[A-Za-z0-9_.-]*\s*=\s*)(.+)', r'\1"…已隐藏…"', content, flags=re.I | re.M)
                return {"content": content[:limit], "path": str(s["path"]), "tool_name": s["name"]}
            except Exception as e:
                return {"error": str(e)}
    return {"error": "未找到该工具的 MCP 配置，或文件不存在"}
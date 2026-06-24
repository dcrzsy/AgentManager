#!/usr/bin/env python3
"""
Agent 管理器 - 跨平台 Agent 管理工具
目前支持会话管理，后续会扩展 Skill / MCP 管理等功能

用法:
  python -m ai_session_manager
  # 浏览器打开 http://127.0.0.1:8080
"""

import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = 8080
HOME = Path.home()

# 跨平台常用路径
_APP_DATA = Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))
_LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local"))
_MAC_SUPPORT = HOME / "Library" / "Application Support"

# 工具名称 -> 会话根目录列表（从这些目录递归扫描）
TOOL_SESSION_ROOTS = {
    "claude": [
        HOME / ".claude" / "sessions",
        HOME / ".claude" / "projects",
        HOME / ".claude" / "history.jsonl",
        HOME / ".*" / ".claude" / "sessions",
        HOME / ".*" / ".claude" / "projects",
        HOME / "*" / ".claude" / "sessions",
        HOME / "*" / ".claude" / "projects",
        # Windows
        _APP_DATA / "Claude" / "sessions",
        _APP_DATA / "Claude" / "projects",
        _LOCAL_APP_DATA / "Claude" / "sessions",
        _LOCAL_APP_DATA / "Claude" / "projects",
        # macOS
        _MAC_SUPPORT / "Claude" / "sessions",
        _MAC_SUPPORT / "Claude" / "projects",
        _MAC_SUPPORT / "Claude" / "history.jsonl",
    ],
    "codex": [
        HOME / ".codex" / "sessions",
        HOME / ".codex" / "shell_snapshots",
        HOME / "*" / ".codex" / "sessions",
        HOME / ".*" / ".codex" / "sessions",
        # Windows
        _APP_DATA / "Codex" / "sessions",
        _LOCAL_APP_DATA / "Codex" / "sessions",
        _LOCAL_APP_DATA / "Codex" / "shell_snapshots",
        # macOS
        _MAC_SUPPORT / "Codex" / "sessions",
        _MAC_SUPPORT / "Codex" / "shell_snapshots",
    ],
    "orca": [
        HOME / ".config" / "orca" / "codex-runtime-home" / "home" / "sessions",
        HOME / ".config" / "orca" / "codex-runtime-home" / "home" / "history.jsonl",
        HOME / ".config" / "orca" / "codex-runtime-home" / "home" / "shell_snapshots",
        # Windows
        _APP_DATA / "orca" / "codex-runtime-home" / "home" / "sessions",
        _LOCAL_APP_DATA / "orca" / "codex-runtime-home" / "home" / "sessions",
        # macOS
        _MAC_SUPPORT / "orca" / "codex-runtime-home" / "home" / "sessions",
    ],
    "kimi-code": [
        HOME / ".kimi-code" / "sessions",
        HOME / ".kimi-code" / "user-history",
        HOME / ".kimi-code" / "session_index.jsonl",
        HOME / ".kimi-code" / "server" / "events",
        HOME / "*" / ".kimi-code" / "sessions",
        HOME / ".*" / ".kimi-code" / "sessions",
        # Windows
        _APP_DATA / "kimi-code" / "sessions",
        _LOCAL_APP_DATA / "kimi-code" / "sessions",
        # macOS
        _MAC_SUPPORT / "kimi-code" / "sessions",
        _MAC_SUPPORT / "kimi-code" / "user-history",
    ],
    "pi": [
        HOME / ".pi" / "agent" / "sessions",
        HOME / ".omp" / "agent" / "sessions",
        HOME / "*" / ".pi" / "agent" / "sessions",
        HOME / ".*" / ".pi" / "agent" / "sessions",
        HOME / "*" / ".omp" / "agent" / "sessions",
        HOME / ".*" / ".omp" / "agent" / "sessions",
        # Windows
        _APP_DATA / "pi" / "agent" / "sessions",
        _APP_DATA / "omp" / "agent" / "sessions",
        # macOS
        _MAC_SUPPORT / "pi" / "agent" / "sessions",
        _MAC_SUPPORT / "omp" / "agent" / "sessions",
    ],
    "hermes": [
        HOME / ".hermes" / "state.db",
        # Windows
        _APP_DATA / "Hermes" / "state.db",
        _LOCAL_APP_DATA / "Hermes" / "state.db",
        # macOS
        _MAC_SUPPORT / "Hermes" / "state.db",
    ],
}

HERMES_DB_PATH = HOME / ".hermes" / "state.db"

# 会话文件扩展名
SESSION_EXTENSIONS = {".jsonl", ".json", ".md", ".txt"}

# 跳过路径关键字
SKIP_PATH_KEYWORDS = {
    "/bin/", "/cache/", "/plugins/", "/node_modules/", "/.git/",
    "__pycache__", "/logs/", "/log/", "/telemetry/", "/credentials/",
    "/skills/", "/tmp/", "/.tmp/", "/backup-", "/memory/",
    "/shell_snapshots/", "/shell-snapshots/",
    "/blobs/", "/plans/", "/tasks/",
    "/server/events/",
    "session_index.jsonl", "__global__.jsonl",
}

# 跳过文件名关键字
SKIP_FILE_KEYWORDS = {
    "readme", "changelog", "license", ".model_config", ".token_usage",
    ".session_start", "update-tokens", "install-counts-cache",
    "known_marketplaces", "blocklist", "installed_plugins",
    "settings.json", "settings.local.json", ".gitignore",
    "installation_id", ".personality_migration",
}


def human_size(size_bytes):
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def safe_resolve(path_str):
    p = Path(path_str).expanduser().resolve()
    try:
        p.relative_to(HOME)
        return p
    except ValueError:
        return None


def is_binary_file(path):
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
            if not chunk:
                return False
            if b"\x00" in chunk:
                return True
            if chunk.startswith(b"\x7fELF"):
                return True
            if chunk.startswith(b"#!/"):
                return False
            non_text = sum(1 for b in chunk if b < 32 and b not in (9, 10, 13))
            if non_text > len(chunk) * 0.3:
                return True
    except Exception:
        return True
    return False


def should_skip_path(path):
    p_str = str(path).lower()
    for kw in SKIP_PATH_KEYWORDS:
        if kw in p_str:
            return True
    name_lower = path.name.lower()
    for skip in SKIP_FILE_KEYWORDS:
        if skip.lower() in name_lower:
            return True
    if path.is_file() and is_binary_file(path):
        return True
    return False


def find_session_roots(tool):
    """展开 glob，返回真实存在的会话根目录/文件"""
    import glob
    patterns = TOOL_SESSION_ROOTS.get(tool, [])
    roots = []
    for pattern in patterns:
        for p_str in glob.glob(str(pattern)):
            p = Path(p_str)
            if p.exists():
                roots.append(p)
    return roots


def find_hermes_sessions(days_old):
    """从 Hermes state.db 读取会话列表"""
    if not HERMES_DB_PATH.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days_old) if days_old > 0 else None
    candidates = []
    try:
        import sqlite3
        conn = sqlite3.connect(str(HERMES_DB_PATH))
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.id, s.source, s.model, s.started_at, s.ended_at, s.message_count,
                   s.title, s.cwd, m.content
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id AND m.role = 'user'
            ORDER BY s.started_at DESC
        """)
        # 每个 session 只取第一条 user 消息
        seen = set()
        for row in cursor.fetchall():
            sid = row[0]
            if sid in seen:
                continue
            seen.add(sid)
            started_at = row[3] or 0
            ended_at = row[4]
            mtime = ended_at or started_at
            mtime_dt = datetime.fromtimestamp(mtime)
            if cutoff is not None and mtime_dt >= cutoff:
                continue
            title = row[6] or ""
            first_msg = row[8] or ""
            display_title = title or first_msg or "(无标题)"
            candidates.append({
                "path": f"hermes://{sid}",
                "display_path": f"~/.hermes/state.db#{sid}",
                "dir": mtime_dt.strftime("%Y-%m"),
                "cwd": row[7] or "",
                "cwd_human": row[7] or "",
                "filename": sid,
                "tool": "hermes",
                "size": 0,
                "size_human": "0 B",
                "mtime": mtime,
                "mtime_human": mtime_dt.strftime("%Y-%m-%d %H:%M"),
                "message_count": row[5],
                "conversation_count": row[5] or 0,
                "user_count": 0,
                "assistant_count": 0,
                "first_message": first_msg,
                "file_type": "hermes",
                "session_title": display_title,
                "hermes_session_id": sid,
            })
        conn.close()
    except Exception as e:
        print(f"读取 Hermes 数据库失败: {e}", file=sys.stderr)
    return candidates


def find_session_files(tool, days_old):
    if tool == "hermes":
        return find_hermes_sessions(days_old)

    cutoff = datetime.now() - timedelta(days=days_old) if days_old > 0 else None
    candidates = []
    seen = set()

    roots = find_session_roots(tool)

    for root in roots:
        if root.is_file():
            files = [root]
        else:
            # 递归扫描，限制深度避免进入奇怪目录
            files = []
            for f in root.rglob("*"):
                if len(f.relative_to(root).parts) > 6:
                    continue
                if not f.is_file():
                    continue
                if should_skip_path(f):
                    continue
                files.append(f)

        for f in files:
            if should_skip_path(f):
                continue
            if str(f) in seen:
                continue
            seen.add(str(f))

            stat = f.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime)
            if cutoff is None or mtime < cutoff:
                preview = extract_preview(f)
                cwd = extract_cwd(f, tool)
                cwd_human = cwd
                if cwd_human.startswith(str(HOME)):
                    cwd_human = "~" + cwd_human[len(str(HOME)):]
                display_path = str(f)
                if display_path.startswith(str(HOME)):
                    display_path = "~" + display_path[len(str(HOME)):]
                # 目录分组键：取工具根目录后的前两段
                dir_key = ""
                try:
                    for root in roots:
                        if root.is_dir() and str(f).startswith(str(root)):
                            rel = f.relative_to(root)
                            parts = rel.parts
                            if len(parts) > 1:
                                dir_key = str(Path(*parts[:-1]))
                            break
                except Exception:
                    pass
                candidates.append({
                    "path": str(f),
                    "display_path": display_path,
                    "dir": dir_key,
                    "cwd": cwd,
                    "cwd_human": cwd_human,
                    "filename": f.name,
                    "tool": tool,
                    "size": stat.st_size,
                    "size_human": human_size(stat.st_size),
                    "mtime": stat.st_mtime,
                    "mtime_human": mtime.strftime("%Y-%m-%d %H:%M"),
                    "message_count": preview.get("count"),
                    "conversation_count": preview.get("conversation_count", 0),
                    "user_count": preview.get("user_count", 0),
                    "assistant_count": preview.get("assistant_count", 0),
                    "first_message": preview.get("first"),
                    "file_type": preview.get("file_type", "text"),
                })

    candidates.sort(key=lambda x: x["mtime"], reverse=True)
    return candidates


def extract_preview(path):
    result = {"count": None, "first": None, "file_type": "text", "conversation_count": 0}
    ext = path.suffix.lower()

    if is_binary_file(path):
        result["file_type"] = "binary"
        return result

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return result

    if ext == ".jsonl":
        result["file_type"] = "jsonl"
        count = 0
        for line in text.splitlines()[:500]:
            if line.strip():
                count += 1
        result["count"] = count
        # 用对话提取器得到真实首条消息和统计
        conversation = extract_conversation_messages(path, max_lines=500)
        if conversation:
            result["first"] = conversation[0]["text"][:150]
        result["conversation_count"] = len(conversation)
        result["user_count"] = sum(1 for m in conversation if m["role"] in ("user", "human"))
        result["assistant_count"] = sum(1 for m in conversation if m["role"] == "assistant")
    elif ext == ".json":
        result["file_type"] = "json"
        try:
            obj = json.loads(text)
            result["first"] = extract_any_user_text(obj) or text[:150].replace("\n", " ")
        except Exception:
            result["first"] = text[:150].replace("\n", " ")
    else:
        result["first"] = text[:150].replace("\n", " ")

    return result


def extract_session_title(path):
    """尝试读取会话标题（Kimi state.json 的 title，或首条消息）"""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        obj = json.loads(text)
        title = obj.get("title") or obj.get("lastPrompt")
        if title and isinstance(title, str):
            return title.strip()
    except Exception:
        pass
    return None


def _decode_path_segment(segment):
    """解码 Claude/Pi 用 '-' 替换 '/' 的目录名，如 --data-- -> /data"""
    if not segment:
        return ""
    # 把 - 替换成 /，再把连续的 // 合并，最后去掉首尾 /
    decoded = segment.replace("-", "/")
    while "//" in decoded:
        decoded = decoded.replace("//", "/")
    return decoded.strip("/")


def extract_cwd(path, tool):
    """提取会话创建时的工作目录"""
    p = Path(path)
    try:
        if tool in ("codex", "orca"):
            # 从 session_meta 读取 cwd
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                for _ in range(20):
                    line = f.readline()
                    if not line:
                        break
                    obj = json.loads(line)
                    payload = obj.get("payload", {})
                    if obj.get("type") == "session_meta" and isinstance(payload, dict):
                        cwd = payload.get("cwd")
                        if cwd:
                            return cwd
        elif tool == "claude":
            # 从项目目录名解码
            parts = p.parts
            if ".claude" in parts and "projects" in parts:
                idx = parts.index("projects")
                if idx + 1 < len(parts):
                    decoded = _decode_path_segment(parts[idx + 1])
                    if decoded:
                        return "/" + decoded
            # 子代理目录有时在 subagents 里，再往上两级是项目名
            if ".claude" in parts and "subagents" in parts:
                idx = parts.index("subagents")
                if idx - 2 >= 0:
                    decoded = _decode_path_segment(parts[idx - 2])
                    if decoded:
                        return "/" + decoded
        elif tool == "pi":
            parts = p.parts
            if ".pi" in parts and "sessions" in parts:
                idx = parts.index("sessions")
                if idx + 1 < len(parts):
                    decoded = _decode_path_segment(parts[idx + 1])
                    if decoded:
                        return "/" + decoded
        elif tool == "kimi-code":
            # 从 wire.jsonl 的 tool.call 事件里找 cwd
            # 合并后的主文件是 wire.jsonl；state.json 则找同目录下任意 agent 的 wire
            target = p
            if p.name == "state.json":
                agents_dir = p.parent / "agents"
                if agents_dir.is_dir():
                    for agent_dir in sorted(agents_dir.iterdir()):
                        wire = agent_dir / "wire.jsonl"
                        if wire.exists():
                            target = wire
                            break
            with open(target, "r", encoding="utf-8", errors="ignore") as f:
                for _ in range(200):
                    line = f.readline()
                    if not line:
                        break
                    obj = json.loads(line)
                    if obj.get("type") != "context.append_loop_event":
                        continue
                    event = obj.get("event", {})
                    if event.get("type") != "tool.call":
                        continue
                    args = event.get("args", {})
                    cwd = args.get("cwd")
                    if cwd:
                        return cwd
                    # 有些 tool.call 没有 cwd，但 command 以 cd 开头
                    cmd = args.get("command", "")
                    if isinstance(cmd, str) and cmd.strip().startswith("cd "):
                        parts = cmd.strip().split(None, 2)
                        if len(parts) >= 2:
                            return parts[1].strip("\"'")
            # 兜底：从 workspace 名猜测
            for part in p.parts:
                if part.startswith("wd_mdm"):
                    return "/data/codeRepository/desmart/git/mdm"
                if part.startswith("wd_data"):
                    return "/data"
                if part.startswith("wd_workspace"):
                    return "/data/AI/场景/电脑软件"
    except Exception:
        pass
    return ""


def get_session_group(path, tool):
    """返回会话分组键，尽量简短有意义"""
    if tool == "hermes":
        # Hermes 按月份开始时间分组
        return path.replace("hermes://", "")[:7] if path.startswith("hermes://") else "hermes"
    p = Path(path)
    parts = p.parts
    try:
        if tool == "kimi-code":
            # ~/.kimi-code/sessions/wd_<workspace>/session_<id>/... -> wd_<workspace>/session_<id>
            for i, part in enumerate(parts):
                if part.startswith("wd_") and i + 1 < len(parts) and parts[i + 1].startswith("session_"):
                    return f"{part}/{parts[i + 1]}"
            for i, part in enumerate(parts):
                if part == "user-history":
                    return "user-history"
        elif tool == "codex":
            # ~/.codex/sessions/2026/06/17/rollout-... -> 2026/06/17
            if ".codex" in parts and "sessions" in parts:
                idx = parts.index("sessions")
                if idx + 3 < len(parts):
                    return f"{parts[idx + 1]}/{parts[idx + 2]}/{parts[idx + 3]}"
        elif tool == "claude":
            # ~/.claude/projects/<project>/<uuid>/... -> <project>/<uuid>
            if ".claude" in parts and "projects" in parts:
                idx = parts.index("projects")
                if idx + 2 < len(parts):
                    return f"{parts[idx + 1]}/{parts[idx + 2]}"
            if ".claude" in parts and "sessions" in parts:
                idx = parts.index("sessions")
                if idx + 1 < len(parts):
                    return parts[idx + 1]
        elif tool == "pi":
            # ~/.pi/agent/sessions/<workspace>/... -> <workspace>
            if ".pi" in parts and "sessions" in parts:
                idx = parts.index("sessions")
                if idx + 1 < len(parts):
                    return parts[idx + 1]
    except Exception:
        pass
    return p.parent.name


def merge_kimi_sessions(sessions):
    """把 Kimi 同一会话的 state.json 与 wire.jsonl 合并成一行"""
    by_session = {}
    others = []
    for s in sessions:
        if s["tool"] != "kimi-code":
            others.append(s)
            continue
        p = Path(s["path"])
        # 找到 session_xxx 目录
        session_dir = None
        for i, part in enumerate(p.parts):
            if part.startswith("session_"):
                session_dir = Path(*p.parts[: i + 1])
                break
        if session_dir is None:
            others.append(s)
            continue
        key = str(session_dir)
        by_session.setdefault(key, []).append(s)

    merged = []
    for session_dir, group in by_session.items():
        wire = next((s for s in group if s["filename"] == "wire.jsonl"), None)
        state = next((s for s in group if s["filename"] == "state.json"), None)
        if wire and state:
            # 合并：用 wire 做主行，标题从 state.json 取
            title = extract_session_title(Path(state["path"])) or wire["first_message"]
            cwd = wire.get("cwd") or state.get("cwd") or ""
            cwd_human = wire.get("cwd_human") or state.get("cwd_human") or cwd
            merged.append({
                **wire,
                "session_title": title or wire["first_message"] or "",
                "cwd": cwd,
                "cwd_human": cwd_human,
                "session_dir": str(session_dir),
                "aux_paths": [state["path"]],
                "size": wire["size"] + state["size"],
                "size_human": human_size(wire["size"] + state["size"]),
                "mtime": max(wire["mtime"], state["mtime"]),
                "mtime_human": datetime.fromtimestamp(max(wire["mtime"], state["mtime"])).strftime("%Y-%m-%d %H:%M"),
            })
        elif wire:
            merged.append({**wire, "session_title": wire["first_message"] or "", "session_dir": str(session_dir)})
        elif state:
            title = extract_session_title(Path(state["path"])) or state["first_message"] or ""
            merged.append({**state, "session_title": title, "session_dir": str(session_dir)})
    return others + merged


def post_process_sessions(sessions):
    """会话列表后处理：合并、分组、标题"""
    sessions = merge_kimi_sessions(sessions)
    for s in sessions:
        s["session_group"] = get_session_group(s["path"], s["tool"])
        if not s.get("session_title"):
            s["session_title"] = s.get("first_message") or ""
    return sessions


def extract_any_user_text(obj):
    """通用提取用户可见文本"""
    if not isinstance(obj, dict):
        return None

    # Codex 格式
    if "payload" in obj and isinstance(obj["payload"], dict):
        p = obj["payload"]
        if p.get("type") == "message" and p.get("role") == "user":
            content = p.get("content")
            if isinstance(content, list):
                texts = []
                for item in content:
                    if isinstance(item, dict) and "text" in item:
                        texts.append(item["text"])
                if texts:
                    full = " ".join(texts)
                    # Codex 把 AGENTS.md 注入成第一条 user message，跳过它
                    if "AGENTS.md instructions" in full[:200]:
                        return None
                    return full[:150]
            if isinstance(content, str):
                if "AGENTS.md instructions" in content[:200]:
                    return None
                return content[:150]

    # Claude history.jsonl 格式
    if "display" in obj and isinstance(obj["display"], str):
        return obj["display"][:150]

    # Claude project session 中的 last-prompt
    if "lastPrompt" in obj and isinstance(obj["lastPrompt"], str):
        return obj["lastPrompt"][:150]

    # Kimi user-history 简化格式：每行 {"content":"..."}
    if "content" in obj and isinstance(obj["content"], str) and not obj.get("role") and not obj.get("type"):
        text = obj["content"]
        if "<system-reminder>" not in text[:200]:
            return text[:150]

    # 标准 message 格式
    role = obj.get("role") or obj.get("type")
    msg_obj = obj.get("message") or obj
    if role in ("user", "human") or msg_obj.get("role") in ("user", "human"):
        content = msg_obj.get("content") or obj.get("content") or msg_obj.get("text") or obj.get("text")
        if isinstance(content, str):
            return content[:150]
        elif isinstance(content, dict):
            # Pi 格式: {'type': 'text', 'text': '...'}
            text = content.get("text") or content.get("content") or str(content)
            return text[:150]
        elif isinstance(content, list) and content:
            # Pi/Codex: list of content parts
            texts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text" and "text" in item:
                        texts.append(item["text"])
                    elif "text" in item and item.get("type") not in ("tool_result", "toolCall", "toolResult", "thinking"):
                        texts.append(item["text"])
                elif isinstance(item, str):
                    texts.append(item)
            if texts:
                return " ".join(texts)[:150]
            # 全是 tool_result/thinking/toolCall，不算用户可见文本
            return None

    # Pi 直接 content 字段
    if "content" in obj and isinstance(obj["content"], dict):
        text = obj["content"].get("text") or obj["content"].get("content") or str(obj["content"])
        return text[:150]

    return None


def _extract_role(obj):
    """从单条记录提取角色"""
    if not isinstance(obj, dict):
        return None
    # Codex / 标准 payload 格式
    if "payload" in obj and isinstance(obj["payload"], dict):
        p = obj["payload"]
        if p.get("type") == "message":
            return p.get("role")
        if p.get("type") == "event_msg":
            # event_msg 的 type 可能表示用户/助手消息
            return p.get("type")
    # Kimi wire.jsonl: context.append_message / turn.prompt
    msg = obj.get("message")
    if isinstance(msg, dict) and msg.get("role"):
        return msg.get("role")
    if obj.get("type") == "turn.prompt":
        return "user"
    if "content" in obj and isinstance(obj, dict) and not isinstance(obj.get("content"), dict):
        # Kimi user-history 每行是用户消息
        return "user"
    # Claude history
    if "role" in obj:
        return obj["role"]
    # Claude project last-prompt
    if "lastPrompt" in obj:
        return "user"
    return obj.get("type")


def _extract_message_text(obj):
    """从单条记录提取完整文本，不截断"""
    if not isinstance(obj, dict):
        return None
    content = None

    # Codex 格式
    if "payload" in obj and isinstance(obj["payload"], dict):
        p = obj["payload"]
        if p.get("type") == "message":
            content = p.get("content")
        elif p.get("type") == "event_msg" and isinstance(p, dict):
            content = p.get("message")

    # Kimi wire.jsonl turn.prompt
    if content is None and obj.get("type") == "turn.prompt":
        content = obj.get("input")

    # Kimi user-history 简化格式
    if content is None and "content" in obj and (isinstance(obj.get("content"), str) or isinstance(obj.get("content"), list)):
        content = obj.get("content")

    # 标准格式
    if content is None:
        msg_obj = obj.get("message") or obj
        if isinstance(msg_obj, dict):
            content = msg_obj.get("content") or obj.get("content") or msg_obj.get("text") or obj.get("text")
        else:
            content = obj.get("content") or obj.get("text")

    if content is None:
        return None

    if isinstance(content, str):
        return content
    elif isinstance(content, dict):
        return content.get("text") or content.get("content") or str(content)
    elif isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict):
                # 跳过工具调用/思考等
                if item.get("type") in ("tool_result", "toolCall", "toolResult", "thinking", "function_call", "functionCall"):
                    continue
                t = item.get("text") or item.get("content") or ""
                if t:
                    texts.append(t)
        if texts:
            return "\n".join(texts)
    return None


def _is_conversation_event(obj):
    """判断是否为对话消息（用户/助手可见内容）"""
    if not isinstance(obj, dict):
        return False
    role = _extract_role(obj)
    if role in ("user", "human", "assistant"):
        return True
    # Codex event_msg
    if obj.get("type") == "event_msg":
        p = obj.get("payload", {})
        if p.get("type") in ("user_message", "agent_message"):
            return True
    # Kimi wire.jsonl 关键事件
    if obj.get("type") in ("context.append_message", "turn.prompt"):
        return True
    # Claude history type=user/assistant
    if obj.get("type") in ("user", "assistant"):
        return True
    return False


def extract_conversation_messages(path, max_lines=500):
    """提取会话中的对话消息，返回 [{role, text, timestamp, raw_type}]"""
    p = Path(path)
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    ext = p.suffix.lower()
    messages = []
    seen = set()
    if ext == ".jsonl":
        for line in text.splitlines()[:max_lines]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not _is_conversation_event(obj):
                continue
            role = _extract_role(obj)
            text_content = _extract_message_text(obj)
            if text_content is None:
                continue
            # 过滤注入/系统提醒/环境上下文
            if role == "user" and "AGENTS.md instructions" in text_content[:300]:
                continue
            if "<system-reminder>" in text_content[:200]:
                continue
            if text_content.strip().startswith(("<environment_context>", "<apps_instructions>", "<collaboration_mode>", "<permissions instructions>", "<git-context>")):
                continue
            # 去重：同一 role+text 只保留第一次
            key = (role, text_content)
            if key in seen:
                continue
            seen.add(key)
            ts = obj.get("timestamp") or ""
            messages.append({
                "role": role or "unknown",
                "text": text_content,
                "timestamp": ts,
                "raw_type": obj.get("type", ""),
            })
    elif ext == ".json":
        try:
            obj = json.loads(text)
        except Exception:
            return []
        # 尝试从 Kimi state.json 的 messages 字段读取
        msgs = obj.get("messages") or obj.get("chat_history") or []
        if isinstance(msgs, list):
            for m in msgs[:max_lines]:
                if not isinstance(m, dict):
                    continue
                role = m.get("role") or m.get("type") or "unknown"
                text_content = _extract_message_text(m)
                if text_content is None:
                    continue
                messages.append({
                    "role": role,
                    "text": text_content,
                    "timestamp": m.get("timestamp", ""),
                    "raw_type": "",
                })
    return messages


def _is_kimi_session_alive(session_dir):
    """判断 Kimi 会话是否还有效：有 state.json 或任意 agent 的 wire.jsonl"""
    p = Path(session_dir)
    if not p.exists():
        return False
    if (p / "state.json").exists():
        return True
    agents_dir = p / "agents"
    if agents_dir.is_dir():
        for agent_dir in agents_dir.iterdir():
            if (agent_dir / "wire.jsonl").exists():
                return True
    return False


def clean_kimi_session_index():
    """清理 session_index.jsonl 中指向不存在或已死亡 session 的失效条目，并删除空目录"""
    index_path = HOME / ".kimi-code" / "session_index.jsonl"
    if not index_path.exists():
        return 0
    try:
        lines = index_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        kept = []
        removed_count = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                kept.append(line)
                continue
            session_dir = obj.get("sessionDir", "")
            if session_dir and not _is_kimi_session_alive(session_dir):
                removed_count += 1
                # 删除已失效的空/死亡会话目录
                try:
                    sd = Path(session_dir)
                    if sd.exists():
                        shutil.rmtree(sd)
                except Exception:
                    pass
                continue
            kept.append(line)
        if removed_count > 0:
            index_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        return removed_count
    except Exception as e:
        print(f"清理 Kimi session_index 失败: {e}", file=sys.stderr)
        return 0


def clean_codex_state_db():
    """清理 Codex state_5.sqlite 中指向已删除 rollout 文件的失效 thread 记录"""
    db_path = HOME / ".codex" / "state_5.sqlite"
    if not db_path.exists():
        return 0
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT id, rollout_path FROM threads")
        rows = cursor.fetchall()
        stale_ids = [r[0] for r in rows if r[1] and not Path(r[1]).exists()]
        if not stale_ids:
            conn.close()
            return 0
        # 批量清理相关表
        placeholders = ",".join("?" * len(stale_ids))
        cursor.execute(f"DELETE FROM thread_dynamic_tools WHERE thread_id IN ({placeholders})", stale_ids)
        cursor.execute(f"DELETE FROM thread_spawn_edges WHERE parent_thread_id IN ({placeholders}) OR child_thread_id IN ({placeholders})", stale_ids + stale_ids)
        cursor.execute(f"DELETE FROM agent_job_items WHERE assigned_thread_id IN ({placeholders})", stale_ids)
        cursor.execute(f"DELETE FROM threads WHERE id IN ({placeholders})", stale_ids)
        conn.commit()
        conn.close()
        return len(stale_ids)
    except Exception as e:
        print(f"清理 Codex state 数据库失败: {e}", file=sys.stderr)
        return 0


def clean_orca_state_db():
    """清理 Orca 内嵌 Codex 的 state_5.sqlite 中失效 thread 记录"""
    db_path = HOME / ".config" / "orca" / "codex-runtime-home" / "home" / "state_5.sqlite"
    if not db_path.exists():
        return 0
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT id, rollout_path FROM threads")
        rows = cursor.fetchall()
        stale_ids = [r[0] for r in rows if r[1] and not Path(r[1]).exists()]
        if not stale_ids:
            conn.close()
            return 0
        placeholders = ",".join("?" * len(stale_ids))
        cursor.execute(f"DELETE FROM thread_dynamic_tools WHERE thread_id IN ({placeholders})", stale_ids)
        cursor.execute(f"DELETE FROM thread_spawn_edges WHERE parent_thread_id IN ({placeholders}) OR child_thread_id IN ({placeholders})", stale_ids + stale_ids)
        cursor.execute(f"DELETE FROM agent_job_items WHERE assigned_thread_id IN ({placeholders})", stale_ids)
        cursor.execute(f"DELETE FROM threads WHERE id IN ({placeholders})", stale_ids)
        conn.commit()
        conn.close()
        return len(stale_ids)
    except Exception as e:
        print(f"清理 Orca state 数据库失败: {e}", file=sys.stderr)
        return 0


def delete_hermes_session(sid):
    """从 Hermes state.db 删除指定会话及其消息"""
    if not HERMES_DB_PATH.exists():
        return False, "Hermes 数据库不存在"
    try:
        import sqlite3
        conn = sqlite3.connect(str(HERMES_DB_PATH))
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
        cursor.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        conn.commit()
        conn.close()
        return True, ""
    except Exception as e:
        return False, str(e)


def delete_files(paths):
    deleted = []
    failed = []
    hermes_deleted = False
    for p_str in paths:
        if p_str.startswith("hermes://"):
            sid = p_str[9:]
            ok, err = delete_hermes_session(sid)
            if ok:
                deleted.append(p_str)
                hermes_deleted = True
            else:
                failed.append({"path": p_str, "error": err})
            continue
        p = safe_resolve(p_str)
        if not p:
            failed.append({"path": p_str, "error": "路径不在 HOME 目录下"})
            continue
        if not p.exists():
            failed.append({"path": p_str, "error": "文件不存在"})
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            deleted.append(str(p))
        except Exception as e:
            failed.append({"path": p_str, "error": str(e)})
    # 清理 Kimi / Codex / Orca 索引
    if deleted:
        clean_kimi_session_index()
        clean_codex_state_db()
        clean_orca_state_db()
    return {"deleted": deleted, "failed": failed}


def preview_hermes_session(sid, max_lines=300):
    """从 Hermes state.db 读取会话消息用于预览"""
    if not HERMES_DB_PATH.exists():
        return {"error": "Hermes 数据库不存在"}
    try:
        import sqlite3
        conn = sqlite3.connect(str(HERMES_DB_PATH))
        cursor = conn.cursor()
        cursor.execute("SELECT title, cwd, started_at, message_count FROM sessions WHERE id = ?", (sid,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return {"error": "会话不存在"}
        title, cwd, started_at, msg_count = row
        cursor.execute(
            "SELECT role, content, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp LIMIT ?",
            (sid, max_lines)
        )
        messages = []
        for role, content, ts in cursor.fetchall():
            if not content:
                continue
            messages.append({
                "role": role or "unknown",
                "text": content,
                "timestamp": ts,
                "raw_type": "",
            })
        conn.close()
        user_count = sum(1 for m in messages if m["role"] in ("user", "human"))
        assistant_count = sum(1 for m in messages if m["role"] == "assistant")
        return {
            "type": "hermes",
            "filename": sid,
            "data": messages,
            "conversation": messages,
            "total_lines": msg_count or len(messages),
            "size": sum(len(m["text"]) for m in messages),
            "user_count": user_count,
            "assistant_count": assistant_count,
            "title": title or "",
            "cwd": cwd or "",
        }
    except Exception as e:
        return {"error": str(e)}


def preview_full(path_str, max_lines=300):
    if path_str.startswith("hermes://"):
        return preview_hermes_session(path_str[9:], max_lines=max_lines)
    p = safe_resolve(path_str)
    if not p or not p.exists():
        return {"error": "路径不存在或不在 HOME 目录下"}
    try:
        if is_binary_file(p):
            return {"error": "二进制文件无法预览", "type": "binary"}
        size = p.stat().st_size
        filename = p.name
        text = p.read_text(encoding="utf-8", errors="ignore")
        ext = p.suffix.lower()
        total_lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)

        # 提取对话视图
        conversation = extract_conversation_messages(p, max_lines=max_lines)
        user_count = sum(1 for m in conversation if m["role"] in ("user", "human"))
        assistant_count = sum(1 for m in conversation if m["role"] == "assistant")

        if ext == ".jsonl":
            raw_lines = []
            for line in text.splitlines()[:max_lines]:
                line = line.strip()
                if line:
                    try:
                        raw_lines.append(json.loads(line))
                    except Exception:
                        raw_lines.append(line)
            return {
                "type": "jsonl",
                "filename": filename,
                "data": raw_lines,
                "conversation": conversation,
                "total_lines": total_lines,
                "size": size,
                "user_count": user_count,
                "assistant_count": assistant_count,
            }
        elif ext == ".json":
            try:
                raw_obj = json.loads(text)
            except Exception:
                raw_obj = {"_parse_error": True, "raw": text[:50000]}
            return {
                "type": "json",
                "filename": filename,
                "data": raw_obj,
                "conversation": conversation,
                "size": size,
                "user_count": user_count,
                "assistant_count": assistant_count,
            }
        else:
            return {
                "type": "text",
                "filename": filename,
                "data": text[:100000],
                "conversation": conversation,
                "size": size,
                "user_count": user_count,
                "assistant_count": assistant_count,
            }
    except Exception as e:
        return {"error": str(e)}



def _load_html_page():
    """从包内 assets 目录加载前端页面，支持 PyInstaller 打包"""
    import sys

    candidates = []
    if getattr(sys, "frozen", False):
        # PyInstaller onefile 解压后的根目录
        meipass = Path(sys._MEIPASS)
        candidates.append(meipass / "ai_session_manager" / "assets" / "index.html")
        candidates.append(meipass / "_internal" / "ai_session_manager" / "assets" / "index.html")
    else:
        candidates.append(Path(__file__).parent / "assets" / "index.html")

    html = None
    for html_path in candidates:
        if html_path.exists():
            try:
                html = html_path.read_text(encoding="utf-8")
                break
            except Exception:
                continue

    if html is None:
        html = "<html><body>无法加载前端页面</body></html>"

    html = html.replace('const HOME_DIR = "";', f'const HOME_DIR = "{str(HOME)}";')
    return html

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/":
            self._html_response(_load_html_page())
        elif path == "/api/sessions":
            tools = qs.get("tools", [""])[0].split(",") if qs.get("tools") else list(TOOL_SESSION_ROOTS.keys())
            days = int(qs.get("days", ["30"])[0])
            sessions = []
            for tool in tools:
                tool = tool.strip()
                if not tool:
                    continue
                sessions.extend(find_session_files(tool, days))
            sessions = post_process_sessions(sessions)
            self._json_response({"sessions": sessions})
        elif path == "/api/preview":
            p = qs.get("path", [""])[0]
            self._json_response(preview_full(p))
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/delete":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            try:
                data = json.loads(body)
                paths = data.get("paths", [])
                try:
                    with open("/tmp/ai-session-manager.log", "a", encoding="utf-8") as f:
                        f.write(f"[DELETE] {datetime.now()} paths={paths!r}\n")
                except Exception:
                    pass
                result = delete_files(paths)
                self._json_response(result)
            except Exception as e:
                import traceback
                log_msg = f"[DELETE ERROR] {datetime.now()} body={body!r} error={e}\n{traceback.format_exc()}\n"
                try:
                    with open("/tmp/ai-session-manager.log", "a", encoding="utf-8") as f:
                        f.write(log_msg)
                except Exception:
                    pass
                self._json_response({"error": str(e)}, 400)
        elif parsed.path == "/api/cleanup-stale-index":
            kimi_removed = clean_kimi_session_index()
            codex_removed = clean_codex_state_db()
            orca_removed = clean_orca_state_db()
            self._json_response({"kimi_removed": kimi_removed, "codex_removed": codex_removed, "orca_removed": orca_removed})
        else:
            self.send_error(404)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Agent 管理器")
    parser.add_argument("--port", type=int, default=PORT, help=f"监听端口 (默认 {PORT})")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    args = parser.parse_args(argv)

    port = args.port
    server = None
    for p in range(port, port + 10):
        try:
            server = HTTPServer((args.host, p), Handler)
            port = p
            break
        except OSError:
            continue
    if server is None:
        print("无法找到可用端口 (8080-8089)", file=sys.stderr)
        sys.exit(1)
    print(f"🚀 Agent 管理器已启动")
    print(f"👉 请在浏览器打开: http://{args.host}:{port}")
    if not args.no_browser:
        import webbrowser
        try:
            webbrowser.open(f"http://{args.host}:{port}")
        except Exception:
            pass
    print(f"   按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        sys.exit(0)


if __name__ == "__main__":
    main()

"""
Harness 配置管理模块（多工具版）
管理各 AI 客户端的配置文件：查看、编辑（自动备份）、恢复备份、健康检查。
支持工具：Pi / Claude Code / Codex / Orca / Kimi Code / Hermes（目录存在才激活）。
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()

# 各工具的配置根与说明
HARNESS_TOOLS = {
    "pi": {
        "name": "Pi",
        "roots": [HOME / ".pi" / "agent", HOME / ".pi"],
        "skip": ["sessions", "npm", "bin", "extensions", "projects-memory", "pi-hermes-memory", "pi-fff",
                 "web-search-cache", "subagents", "__pycache__", "skills", "prompts-src"],
        "categories": {
            "核心配置": ["settings.json"],
            "模型": ["models.json", "models-store.json"],
            "认证与信任": ["auth.json", "trust.json"],
            "搜索": ["web-search.json"],
            "MCP": ["mcp-cache.json"],
            "界面": ["open-tui.json"],
            "提示词": None,  # prompts/ 目录
        },
    },
    "claude": {
        "name": "Claude Code",
        "roots": [HOME / ".claude"],
        "extra_files": [HOME / ".claude.json"],
        "skip": ["sessions", "projects", "history.jsonl", "shell-snapshots", "cache", "__pycache__",
                 "downloads", "plugins", "statsig", "todos", "conversation-history", "agent-config"],
        "categories": {
            "核心配置": ["settings.json", "settings.local.json", ".claude.json"],
            "Agent": ["agent-config"],
            "认证": ["credentials.json"],
            "提示词": ["preferences"],
        },
    },
    "codex": {
        "name": "Codex",
        "roots": [HOME / ".codex"],
        "skip": ["sessions", "shell_snapshots", "cache", "__pycache__", "downloads", "logs", "plugins",
                 "external_agent_session_imports.json", "chrome-native-hosts-v2.json", "watches", "tmp"],
        "categories": {
            "核心配置": ["config.toml", "config.local.json", "config.json"],
            "认证": ["auth.json"],
            "钩子": ["hooks.json"],
        },
    },
    "orca": {
        "name": "Orca",
        "roots": [HOME / ".config" / "orca" / "codex-runtime-home" / "home",
                  HOME / ".config" / "orca" / "codex-runtime-home"],
        "skip": ["sessions", "shell_snapshots", "skills", "plugins", "cache", "tmp", "logs",
                 "thread-writer-locks", "-wal", "-shm", "models_cache.json"],
        "categories": {
            "核心配置": ["config.toml", "config.toml.bak"],
            "认证": ["auth.json"],
            "钩子": ["hooks.json", "hooks.json.bak"],
            "状态": ["version.json"],
        },
    },
    "kimi": {
        "name": "Kimi Code",
        "roots": [HOME / ".kimi-code"],
        "skip": ["sessions", "user-history", "cache", "__pycache__", "server", "installer-logs"],
        "categories": {
            "核心配置": ["config.json", "migration-report.json"],
            "工作区": ["workspaces.json", "session_index.jsonl"],
            "MCP": ["mcp.json"],
        },
    },
    "hermes": {
        "name": "Hermes",
        "roots": [HOME / ".hermes"],
        "skip": ["state.db", "-shm", "-wal", "cache", "logs", "runs", "downloads", "src", "node_modules"],
        "categories": {
            "认证": ["auth.json"],
            "模型": ["models_dev_cache.json", "models_store.json", "provider_models_cache.json", "ollama_cloud_models_cache.json"],
            "状态": ["state.json", "processes.json", "gateway_state.json", "channel_directory.json"],
            "界面": ["tui-theme-boot.json", "web-ui-build-stamp.json", "desktop-build-stamp.json"],
        },
    },
}

# 敏感字段（预览时脱敏）
SENSITIVE_KEYS = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|credential|auth|cookie|bearer|sk-|tvly-)",
    re.IGNORECASE,
)

EDITABLE_EXTS = {".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".conf", ".jsonl"}
CONFIG_EXTS = {".json", ".jsonl", ".toml", ".yaml", ".yml", ".md", ".txt", ".conf", ".bak"}


def _display_path(p):
    s = str(p)
    if s.startswith(str(HOME)):
        return "~" + s[len(str(HOME)):]
    return s


def tool_roots(tool):
    """返回某工具的配置根目录列表"""
    meta = HARNESS_TOOLS.get(tool, {})
    roots = [Path(r) for r in meta.get("roots", [])]
    roots += [Path(f).parent for f in meta.get("extra_files", [])]
    return roots


def in_harness_root(p):
    """路径必须在任一工具配置根内（防编辑任意文件）"""
    p = p.resolve()
    for tool in HARNESS_TOOLS:
        for r in tool_roots(tool):
            try:
                p.relative_to(r.resolve())
                return True
            except ValueError:
                continue
    return False


def active_tools():
    """返回目录真实存在的工具"""
    out = []
    for tool, meta in HARNESS_TOOLS.items():
        exists = any(r.is_dir() for r in tool_roots(tool))
        if exists:
            out.append({"id": tool, "name": meta["name"]})
    return out


def harness_list(tool=None):
    """列出配置文件（可按工具过滤，缺省全部）"""
    items = []
    for tid, meta in HARNESS_TOOLS.items():
        if tool and tid != tool:
            continue
        if not any(r.is_dir() for r in tool_roots(tid)):
            continue
        seen = set()
        for r in tool_roots(tid):
            if not r.is_dir():
                continue
            for f in sorted(r.iterdir()):
                if f.is_file() and f.suffix.lower() in CONFIG_EXTS:
                    if _skip_file(f, meta):
                        continue
                    key = str(f)
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append(_file_info(f, tid))
        # 指定文件（如 ~/.claude.json）
        for ef in meta.get("extra_files", []):
            ef = Path(ef)
            if ef.is_file() and str(ef) not in seen:
                seen.add(str(ef))
                items.append(_file_info(ef, tid))
    items.sort(key=lambda x: (x["tool_order"], x["category_rank"], x["display_path"]))
    return items


def _skip_file(f, meta):
    name = f.name
    if ".bak." in name:
        return False  # 备份要展示（作为备份类）
    skip = meta.get("skip", [])
    for kw in skip:
        if kw in str(f):
            return True
    if f.suffix.lower() not in CONFIG_EXTS:
        return True
    return False


def _category_of(path, meta):
    name = path.name
    cats = meta.get("categories", {})
    for i, (cat, names) in enumerate(cats.items()):
        if names and name in names:
            return cat, i
        if names is None and "prompts" in path.parts:
            return cat, i
    if ".bak." in name:
        return "备份", 98
    return "其他", 99


def _file_info(f, tool):
    meta = HARNESS_TOOLS[tool]
    cat, rank = _category_of(f, meta)
    st = f.stat()
    return {
        "name": f.name,
        "path": str(f),
        "display_path": _display_path(f),
        "tool": tool,
        "tool_name": HARNESS_TOOLS[tool]["name"],
        "tool_order": list(HARNESS_TOOLS.keys()).index(tool),
        "category": cat,
        "category_rank": rank,
        "size": st.st_size,
        "size_human": _human_size(st.st_size),
        "mtime": st.st_mtime,
        "mtime_human": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "ext": f.suffix.lower(),
        "is_backup": ".bak." in f.name,
        "editable": f.suffix.lower() in EDITABLE_EXTS,
    }


def _human_size(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def harness_get(path_str, limit=200000):
    """读取文件：JSON 解析 + 敏感字段脱敏预览"""
    p = Path(path_str).expanduser()
    if not in_harness_root(p) or not p.is_file():
        return {"error": "路径不在受支持的配置目录下或不是文件"}
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"error": str(e)}
    truncated = len(raw) > limit
    raw = raw[:limit]
    parsed = None
    parse_error = None
    is_json = p.suffix.lower() in (".json", ".jsonl")
    if is_json:
        try:
            parsed = json.loads(raw)
        except Exception as e:
            parse_error = str(e)
    # 工具归属
    tool = None
    for tid, meta in HARNESS_TOOLS.items():
        if any(str(p).startswith(str(r)) for r in tool_roots(tid)) or str(p) in [str(Path(e)) for e in meta.get("extra_files", [])]:
            tool = tid
            break
    return {
        "path": str(p),
        "display_path": _display_path(p),
        "name": p.name,
        "tool": tool,
        "tool_name": HARNESS_TOOLS[tool]["name"] if tool else "",
        "editable": p.suffix.lower() in EDITABLE_EXTS,
        "is_json": is_json,
        "size": p.stat().st_size,
        "parsed": parsed,
        "parse_error": parse_error,
        "content": raw,
        "truncated": truncated,
        "masked": is_json and parsed is not None,
    }


def _mask_json(obj, depth=0):
    if depth > 8 or obj is None:
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if SENSITIVE_KEYS.search(k) and isinstance(v, str) and len(v) > 6:
                out[k] = v[:3] + "…" + v[-3:] + f"（已隐藏，共{len(v)}字符）"
            else:
                out[k] = _mask_json(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [_mask_json(x, depth + 1) for x in obj[:200]]
    return obj


def harness_masked(path_str):
    r = harness_get(path_str)
    if r.get("error") or not r.get("parsed"):
        return r
    r["parsed_masked"] = _mask_json(r["parsed"])
    return r


def harness_save(path_str, content):
    """保存配置：自动备份 .bak.YYYYMMDD-HHMMSS，JSON 先校验"""
    p = Path(path_str).expanduser()
    if not in_harness_root(p):
        return {"ok": False, "error": "路径不在受支持的配置目录下"}
    if p.suffix.lower() not in EDITABLE_EXTS:
        return {"ok": False, "error": f"不支持编辑 {p.suffix} 文件"}
    if not p.exists():
        return {"ok": False, "error": "文件不存在"}
    if isinstance(content, str):
        text = content
    else:
        text = json.dumps(content, ensure_ascii=False, indent=2)
    if p.suffix.lower() in (".json", ".jsonl"):
        try:
            json.loads(text)
        except Exception as e:
            return {"ok": False, "error": f"JSON 格式错误，未保存: {e}"}
    try:
        bak = p.with_name(p.name + f".bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(p, bak)
        p.write_text(text, encoding="utf-8")
        return {"ok": True, "backup": str(bak), "size": len(text)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def harness_backups(path_str):
    """列出某文件的历史备份"""
    p = Path(path_str).expanduser()
    if not in_harness_root(p):
        return {"items": [], "error": "路径不在受支持的配置目录下"}
    items = []
    for bak in sorted(p.parent.glob(p.name + ".bak.*")):
        if bak.is_file():
            st = bak.stat()
            items.append({
                "path": str(bak),
                "name": bak.name,
                "display_path": _display_path(bak),
                "size": st.st_size,
                "size_human": _human_size(st.st_size),
                "mtime": st.st_mtime,
                "mtime_human": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"items": items}


def harness_restore(path_str, backup_path_str):
    p = Path(path_str).expanduser()
    bak = Path(backup_path_str).expanduser()
    if not in_harness_root(p) or not in_harness_root(bak):
        return {"ok": False, "error": "路径不在受支持的配置目录下"}
    if not p.is_file() or not bak.is_file():
        return {"ok": False, "error": "文件不存在"}
    try:
        safe = p.with_name(p.name + f".bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(p, safe)
        shutil.copy2(bak, p)
        return {"ok": True, "backup": str(safe)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def harness_delete_backup(backup_path_str):
    bak = Path(backup_path_str).expanduser()
    if not in_harness_root(bak) or ".bak" not in bak.name:
        return {"ok": False, "error": "路径非法或不是备份文件"}
    if not bak.is_file():
        return {"ok": False, "error": "文件不存在"}
    try:
        from .app import move_to_trash
        move_to_trash(bak)
        return {"ok": True}
    except Exception:
        try:
            bak.unlink()
            return {"ok": True}
        except Exception as e2:
            return {"ok": False, "error": str(e2)}


# 权限敏感的配置文件名
SENSITIVE_NAMES = {"auth.json", "credentials.json", "web-search.json", "trust.json", "oauth_creds.json", "keychains"}


def harness_health(tool=None):
    """配置健康检查：JSON 合法性、权限安全、备份健康、核心文件缺失"""
    checks = []
    problems = []
    seen_main = set()
    for item in harness_list(tool):
        p = Path(item["path"])
        if item["is_backup"]:
            # 孤儿备份：主文件已不存在
            main_file = p.with_name(p.name.split(".bak.")[0])
            if not main_file.exists():
                problems.append({
                    "module": "harness", "tool": item["tool"], "level": "warn",
                    "message": f"孤儿备份：{item['name']} 的主文件已不存在",
                    "detail": item["display_path"],
                    "action": "clean-backup", "path": item["path"],
                })
            continue
        seen_main.add(item["path"])
        status, detail = "ok", ""
        if item["ext"] in (".json", ".jsonl"):
            try:
                raw_p = p.read_text(encoding="utf-8", errors="replace")
                if item["ext"] == ".jsonl":
                    # JSONL：逐行校验，全部失败才算 error
                    bad_lines = 0
                    total_lines = 0
                    for line in raw_p.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        total_lines += 1
                        try:
                            json.loads(line)
                        except Exception:
                            bad_lines += 1
                    if total_lines and bad_lines == total_lines:
                        raise ValueError(f"全部 {total_lines} 行均非合法 JSON")
                else:
                    json.loads(raw_p)
            except Exception as e:
                status, detail = "error", f"解析失败: {e}"
                problems.append({
                    "module": "harness", "tool": item["tool"], "level": "error",
                    "message": f"{item['name']} 内容校验失败",
                    "detail": str(e)[:120], "path": item["path"],
                })
        # 权限：敏感文件权限过宽（类 Unix）
        if sys.platform != "win32" and item["name"] in SENSITIVE_NAMES:
            try:
                mode = p.stat().st_mode & 0o777
                if mode & 0o077:
                    problems.append({
                        "module": "harness", "tool": item["tool"], "level": "error",
                        "message": f"敏感文件 {item['name']} 权限过宽（{oct(mode)[2:]}），其他用户可读",
                        "detail": item["display_path"],
                        "action": "fix-permission", "path": item["path"],
                    })
            except Exception:
                pass
        # 可写性
        if not os.access(p, os.W_OK):
            problems.append({
                "module": "harness", "tool": item["tool"], "level": "warn",
                "message": f"{item['name']} 当前不可写，编辑保存可能失败",
                "detail": item["display_path"], "path": item["path"],
            })
        backups = len(harness_backups(item["path"]).get("items", []))
        if backups > 10:
            problems.append({
                "module": "harness", "tool": item["tool"], "level": "info",
                "message": f"{item['name']} 有 {backups} 份备份，建议清理旧备份",
                "detail": item["display_path"],
                "action": "clean-backups", "path": item["path"],
            })
        checks.append({
            "name": item["name"], "path": item["path"], "display_path": item["display_path"],
            "tool": item["tool"], "status": status, "detail": detail, "backup_count": backups,
        })
    # 核心文件缺失提示（每工具第一分类）
    for tid, meta in HARNESS_TOOLS.items():
        if tool and tid != tool:
            continue
        if not any(r.is_dir() for r in tool_roots(tid)):
            continue
        first_cat = next(iter(meta.get("categories", {}).values())) if meta.get("categories") else []
        if first_cat:
            for core_name in (first_cat if isinstance(first_cat, list) else []):
                exists = any(str(i["path"]).endswith("/" + core_name) for i in checks)
                if not exists:
                    problems.append({
                        "module": "harness", "tool": tid, "level": "info",
                        "message": f"{meta['name']} 缺少核心配置文件 {core_name}（可能使用默认值）",
                        "detail": "",
                    })
    problems.sort(key=lambda x: {"error": 0, "warn": 1, "info": 2}.get(x["level"], 3))
    return {"total": len(checks), "errors": [c for c in checks if c["status"] != "ok"],
            "checks": checks, "problems": problems, "problem_count": len(problems)}


def harness_fix(path_str, action):
    """执行白名单修复动作"""
    p = Path(path_str).expanduser()
    if not in_harness_root(p):
        return {"ok": False, "error": "路径不在受支持的配置目录下"}
    if action == "fix-permission" and sys.platform != "win32":
        if not p.is_file():
            return {"ok": False, "error": "文件不存在"}
        try:
            os.chmod(p, 0o600)
            return {"ok": True, "message": "已收紧权限为 600"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if action == "clean-backup":
        if ".bak" not in p.name:
            return {"ok": False, "error": "不是备份文件"}
        try:
            p.unlink()
            return {"ok": True, "message": "已清理孤儿备份"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if action == "clean-backups":
        # 保留最近 5 份，其余移入回收站
        baks = harness_backups(str(p)).get("items", [])
        if len(baks) <= 5:
            return {"ok": False, "error": "备份数量未超过 5 份"}
        removed = 0
        for b in baks[5:]:
            from .app import move_to_trash
            try:
                if move_to_trash(Path(b["path"])):
                    removed += 1
            except Exception:
                pass
        return {"ok": True, "message": f"已清理 {removed} 份旧备份（移入回收站）"}
    return {"ok": False, "error": "未知修复动作"}
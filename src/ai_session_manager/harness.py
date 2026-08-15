"""
Harness 配置管理模块
管理 ~/.pi/agent 下的配置文件：查看、编辑（自动备份）、恢复备份、健康检查。
"""

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
PI_AGENT_DIR = HOME / ".pi" / "agent"
HARNESS_ROOTS = [PI_AGENT_DIR, HOME / ".pi"]

# 展示分类与排序
CONFIG_CATEGORIES = {
    "核心配置": ["settings.json"],
    "模型": ["models.json", "models-store.json"],
    "认证与信任": ["auth.json", "trust.json"],
    "搜索": ["web-search.json"],
    "MCP": ["mcp-cache.json"],
    "界面": ["open-tui.json"],
    "提示词": None,  # prompts/ 目录
}

# 敏感字段（预览时脱敏）
SENSITIVE_KEYS = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|credential|auth|cookie|bearer|sk-|tvly-)",
    re.IGNORECASE,
)

# 可编辑文件类型白名单
EDITABLE_EXTS = {".json", ".md", ".txt", ".toml", ".yaml", ".yml", ".conf"}


def _display_path(p):
    s = str(p)
    if s.startswith(str(HOME)):
        return "~" + s[len(str(HOME)):]
    return s


def in_harness_root(p):
    """路径必须在 ~/.pi 内（防编辑任意文件）"""
    try:
        p.resolve().relative_to((HOME / ".pi").resolve())
        return True
    except ValueError:
        return False


def harness_list():
    """列出所有管理文件（含 prompts/ 与备份标记）"""
    items = []
    if not PI_AGENT_DIR.is_dir():
        return items

    # 根目录 json/其他配置文件
    for f in sorted(PI_AGENT_DIR.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() not in {".json", ".toml", ".yaml", ".yml", ".conf", ".md", ".txt"}:
            continue
        items.append(_file_info(f))

    # 顶层 ~/.pi/*.json（如 web-search.json）
    for f in sorted((HOME / ".pi").iterdir()):
        if f.is_file() and f.suffix.lower() == ".json":
            items.append(_file_info(f))

    # prompts/ 提示词
    prompts = PI_AGENT_DIR / "prompts"
    if prompts.is_dir():
        for f in sorted(prompts.rglob("*")):
            if f.is_file() and f.suffix.lower() in {".md", ".txt"}:
                items.append(_file_info(f))

    # 排序：分类顺序 -> 路径
    items.sort(key=lambda x: (x["category_rank"], x["path"]))
    return items


def _category_of(path):
    name = path.name
    for i, (cat, names) in enumerate(CONFIG_CATEGORIES.items()):
        if names and name in names:
            return cat, i
        if names is None and "prompts" in path.parts:
            return cat, i
    return "其他", 99


def _file_info(f):
    cat, rank = _category_of(f)
    stat = f.stat()
    is_backup = ".bak." in f.name
    return {
        "name": f.name,
        "path": str(f),
        "display_path": _display_path(f),
        "category": cat,
        "category_rank": rank,
        "size": stat.st_size,
        "size_human": _human_size(stat.st_size),
        "mtime": stat.st_mtime,
        "mtime_human": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "ext": f.suffix.lower(),
        "is_backup": is_backup,
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
        return {"error": "路径不在 ~/.pi 目录下或不是文件"}
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"error": str(e)}
    truncated = len(raw) > limit
    raw = raw[:limit]
    parsed = None
    parse_error = None
    is_json = p.suffix.lower() == ".json"
    if is_json:
        try:
            parsed = json.loads(raw)
        except Exception as e:
            parse_error = str(e)
    return {
        "path": str(p),
        "display_path": _display_path(p),
        "name": p.name,
        "is_json": is_json,
        "size": p.stat().st_size,
        "parsed": parsed,
        "parse_error": parse_error,
        "content": raw,
        "truncated": truncated,
        "masked": is_json and parsed is not None,
    }


def _mask_json(obj, depth=0):
    """对敏感字段值脱敏（递归）"""
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
    """获取脱敏后的 JSON 预览（用于界面默认显示）"""
    r = harness_get(path_str)
    if r.get("error") or not r.get("parsed"):
        return r
    r["parsed_masked"] = _mask_json(r["parsed"])
    return r


def harness_save(path_str, content):
    """保存配置：自动备份 .bak.YYYYMMDD-HHMMSS，JSON 先校验"""
    p = Path(path_str).expanduser()
    if not in_harness_root(p):
        return {"ok": False, "error": "路径不在 ~/.pi 目录下"}
    if p.suffix.lower() not in EDITABLE_EXTS:
        return {"ok": False, "error": f"不支持编辑 {p.suffix} 文件"}
    if not p.exists():
        return {"ok": False, "error": "文件不存在"}
    if isinstance(content, str):
        text = content
    else:
        text = json.dumps(content, ensure_ascii=False, indent=2)
    # JSON 校验
    if p.suffix.lower() == ".json":
        try:
            json.loads(text)
        except Exception as e:
            return {"ok": False, "error": f"JSON 格式错误，未保存: {e}"}
    try:
        # 备份
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
        return {"items": [], "error": "路径不在 ~/.pi 目录下"}
    items = []
    for bak in sorted(PI_AGENT_DIR.glob(p.name + ".bak.*")):
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
    """从备份恢复：先备份当前，再复制备份内容"""
    p = Path(path_str).expanduser()
    bak = Path(backup_path_str).expanduser()
    if not in_harness_root(p) or not in_harness_root(bak):
        return {"ok": False, "error": "路径不在 ~/.pi 目录下"}
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
    """删除备份文件（进回收站）"""
    bak = Path(backup_path_str).expanduser()
    if not in_harness_root(bak) or ".bak" not in bak.name:
        return {"ok": False, "error": "路径非法或不是备份文件"}
    if not bak.is_file():
        return {"ok": False, "error": "文件不存在"}
    try:
        from .app import move_to_trash
        move_to_trash(bak)
        return {"ok": True}
    except Exception as e:
        # 直接删除兜底
        try:
            bak.unlink()
            return {"ok": True}
        except Exception as e2:
            return {"ok": False, "error": str(e2)}


def harness_health():
    """配置健康检查：JSON 合法性、敏感字段、备份数量"""
    checks = []
    for item in harness_list():
        if item["is_backup"] or not item["editable"]:
            continue
        status, detail = "ok", ""
        if item["ext"] == ".json":
            try:
                json.loads(Path(item["path"]).read_text(encoding="utf-8"))
            except Exception as e:
                status, detail = "error", f"JSON 解析失败: {e}"
        backups = len(harness_backups(item["path"]).get("items", []))
        checks.append({
            "name": item["name"],
            "path": item["path"],
            "display_path": item["display_path"],
            "status": status,
            "detail": detail,
            "backup_count": backups,
        })
    errors = [c for c in checks if c["status"] != "ok"]
    return {"total": len(checks), "errors": errors, "checks": checks}
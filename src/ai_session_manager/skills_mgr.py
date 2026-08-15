"""
Skill 管理模块
扫描 pi 的 skills 根目录（用户级/全局/项目级），支持查看、新建、删除。
"""

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
PI_AGENT_DIR = HOME / ".pi" / "agent"

# 扫描根（多工具）：(目录, scope, 工具id)
SKILL_ROOTS = [
    (PI_AGENT_DIR / "skills",                     "user",    "pi"),
    (HOME / ".agents" / "skills",                 "global",  "pi"),
    (HOME / ".pi" / "agent" / "pi-hermes-memory" / "skills", "memory", "pi"),
    # 其他工具的 skills 目录（存在才生效）
    (HOME / ".claude" / "skills",                 "user",    "claude"),
    (HOME / ".config" / "claude" / "skills",      "user",    "claude"),
    (HOME / ".codex" / "skills",                  "user",    "codex"),
    (HOME / ".config" / "orca" / "codex-runtime-home" / "home" / "skills", "user", "orca"),
    (HOME / ".kimi-code" / "skills",              "user",    "kimi"),
    (HOME / ".hermes" / "skills",                 "user",    "hermes"),
]

_projects_memory = HOME / ".pi" / "agent" / "projects-memory"
if _projects_memory.is_dir():
    for proj in sorted(_projects_memory.iterdir()):
        skills_dir = proj / "skills"
        if skills_dir.is_dir():
            SKILL_ROOTS.append((skills_dir, "project:" + proj.name, "pi"))

TOOL_NAMES = {
    "pi": "Pi", "claude": "Claude Code", "codex": "Codex",
    "orca": "Orca", "kimi": "Kimi Code", "hermes": "Hermes",
}


def _find_skill_dirs(root):
    """递归找含 SKILL.md 的目录（skills 根下的嵌套目录）"""
    out = []
    if not root.is_dir():
        return out
    try:
        for f in root.iterdir():
            if f.is_dir():
                if (f / "SKILL.md").is_file():
                    out.append(f)
                out.extend(_find_skill_dirs(f))
            elif f.name == "SKILL.md":
                out.append(f.parent)
    except Exception:
        pass
    return out


def _parse_frontmatter(text):
    """解析 SKILL.md 的 YAML frontmatter（宽松解析）"""
    fm = {}
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip().strip("'\"")
            if v.startswith(">") or v.startswith("|"):
                v = v[1:].strip()
            fm[k] = v
    return fm, body.strip()


def skills_list(q=None, tool=None):
    """扫描所有 skills 根，返回清单（可按工具过滤）"""
    items = []
    seen = set()
    for root, scope, tid in SKILL_ROOTS:
        if tool and tid != tool:
            continue
        for d in _find_skill_dirs(root):
            skill_file = d / "SKILL.md"
            if not skill_file.is_file():
                continue
            try:
                text = skill_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""
            fm, body = _parse_frontmatter(text)
            st = d.stat()
            try:
                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            except Exception:
                size = st.st_size
            rel = d.relative_to(root)
            key = str(d)
            if key in seen:
                continue
            seen.add(key)
            name = fm.get("name") or d.name
            items.append({
                "name": name,
                "description": fm.get("description", ""),
                "tool": tid,
                "tool_name": TOOL_NAMES.get(tid, tid),
                "scope": scope,
                "root": str(root),
                "root_display": str(root).replace(str(HOME), "~"),
                "rel_path": str(rel),
                "path": str(d),
                "skill_file": str(skill_file),
                "size": size,
                "size_human": _human_size(size),
                "mtime": st.st_mtime,
                "mtime_human": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "valid_frontmatter": bool(fm.get("name") or fm.get("description")),
                "frontmatter": fm,
            })
    items.sort(key=lambda x: (x["scope"], x["name"].lower()))
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["name"].lower() or ql in i["description"].lower()]
    return items


def _human_size(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def skill_get(path_str):
    """读取 SKILL.md 全文 + 解析 frontmatter"""
    d = Path(path_str).expanduser()
    skill_file = d / "SKILL.md" if d.is_dir() else d
    if not skill_file.is_file():
        return {"error": "SKILL.md 不存在"}
    try:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"error": str(e)}
    fm, body = _parse_frontmatter(text)
    files = []
    for f in sorted(d.rglob("*")):
        if f.is_file() and f.name != "SKILL.md":
            st = f.stat()
            files.append({"name": f.name, "path": str(f), "size": st.st_size})
    return {
        "name": fm.get("name") or d.name,
        "description": fm.get("description", ""),
        "path": str(d),
        "skill_file": str(skill_file),
        "frontmatter": fm,
        "body": body,
        "raw": text,
        "files": files,
    }


def skill_create(name, description, content, scope="user"):
    """新建 skill：生成 <root>/<name>/SKILL.md"""
    name = name.strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return {"ok": False, "error": "名称只能包含字母、数字、._-"}
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "内容不能为空"}
    if scope == "global":
        root = HOME / ".agents" / "skills"
    elif scope.startswith("project:"):
        root = _projects_memory / scope[len("project:"):] / "skills"
    else:
        root = PI_AGENT_DIR / "skills"
    dest = root / name
    if dest.exists():
        return {"ok": False, "error": f"已存在同名 skill（{dest}）"}
    try:
        root.mkdir(parents=True, exist_ok=True)
        dest.mkdir(parents=True, exist_ok=True)
        md = f"---\nname: {name}\ndescription: {description or name}\n---\n\n{content}\n"
        (dest / "SKILL.md").write_text(md, encoding="utf-8")
        return {"ok": True, "path": str(dest), "name": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def skill_update(path_str, content):
    """更新 SKILL.md（保留 frontmatter 或整体替换）"""
    d = Path(path_str).expanduser()
    skill_file = d / "SKILL.md" if d.is_dir() else Path(path_str)
    if not skill_file.is_file():
        return {"ok": False, "error": "SKILL.md 不存在"}
    bak = skill_file.with_name("SKILL.md.bak." + datetime.now().strftime("%Y%m%d-%H%M%S"))
    try:
        shutil.copy2(skill_file, bak)
        skill_file.write_text(content, encoding="utf-8")
        return {"ok": True, "backup": str(bak)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def skill_delete(path_str):
    """删除 skill 目录（移入回收站）"""
    d = Path(path_str).expanduser()
    root = d.parent
    # 安全检查：必须位于 skills 根下
    allowed = [str(r) for r, _ in SKILL_ROOTS]
    if not any(str(root).startswith(r + "/") or str(root) == r for r in allowed):
        return {"ok": False, "error": "路径不在 skills 根目录下"}
    if not (d / "SKILL.md").is_file():
        return {"ok": False, "error": "SKILL.md 不存在"}
    try:
        from .app import move_to_trash
        ok = move_to_trash(d)
        if ok:
            return {"ok": True}
        return {"ok": False, "error": "移入回收站失败"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
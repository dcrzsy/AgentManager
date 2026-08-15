"""
环境检测与升级模块（多工具版）
检测各 AI 工具：安装方式 / 版本 / 多版本冲突 / 运行状态 / 配置目录，支持白名单命令升级。
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HOME = Path.home()

# 工具检测定义
TOOLS = {
    "claude": {
        "name": "Claude Code",
        "bin": ["claude"],
        "version_args": ["--version"],
        "npm_pkg": "@anthropic-ai/claude-code",
        "config": [HOME / ".claude", HOME / ".claude.json"],
        "upgrade_cmd": "npm update -g @anthropic-ai/claude-code",
        "upgrade_via": "npm 全局包",
        "run_proc": ["claude"],
    },
    "codex": {
        "name": "Codex",
        "bin": ["codex"],
        "version_args": ["--version"],
        "npm_pkg": "@openai/codex",
        "config": [HOME / ".codex"],
        "upgrade_cmd": "npm update -g @openai/codex",
        "upgrade_via": "npm 全局包",
        "run_proc": ["codex"],
    },
    "pi": {
        "name": "Pi",
        "bin": ["pi"],
        "version_args": ["--version"],
        "npm_pkg": "@earendil-works/pi-coding-agent",
        "config": [HOME / ".pi"],
        "upgrade_cmd": "npm update -g @earendil-works/pi-coding-agent",
        "upgrade_via": "npm 全局包",
        "run_proc": ["pi"],
    },
    "kimi": {
        "name": "Kimi Code",
        "bin": ["kimi", "kimi-code"],
        "version_args": ["--version"],
        "npm_pkg": None,
        "config": [HOME / ".kimi-code"],
        "upgrade_cmd": None,  # 官方安装器管理
        "upgrade_via": "官方安装器（~/.kimi-code/bin）",
        "run_proc": ["kimi"],
    },
    "orca": {
        "name": "Orca",
        "bin": ["orca"],
        "version_args": ["--version"],
        "npm_pkg": None,
        "config": [HOME / ".config" / "orca"],
        "upgrade_cmd": None,
        "upgrade_via": "桌面应用（官方渠道更新）",
        "run_proc": ["orca"],
    },
    "hermes": {
        "name": "Hermes",
        "bin": ["hermes"],
        "version_args": ["--version"],
        "npm_pkg": None,
        "config": [HOME / ".hermes"],
        "upgrade_cmd": None,
        "upgrade_via": "官方安装器（~/.local/bin 等）",
        "run_proc": ["hermes"],
    },
    "aider": {
        "name": "Aider",
        "bin": ["aider"],
        "version_args": ["--version"],
        "npm_pkg": None,
        "config": [HOME / ".aider"],
        "upgrade_cmd": "pip install -U aider-chat",
        "upgrade_via": "Python pip",
        "run_proc": ["aider"],
    },
    "gemini": {
        "name": "Gemini CLI",
        "bin": ["gemini"],
        "version_args": ["--version"],
        "npm_pkg": "@google/gemini-cli",
        "config": [HOME / ".gemini"],
        "upgrade_cmd": "npm update -g @google/gemini-cli",
        "upgrade_via": "npm 全局包",
        "run_proc": ["gemini"],
    },
    "qwen": {
        "name": "Qwen Code",
        "bin": ["qwen-code", "qwen"],
        "version_args": ["--version"],
        "npm_pkg": None,
        "config": [HOME / ".qwen-code", HOME / ".qwen", HOME / ".cache" / "qwen-code"],
        "upgrade_cmd": None,
        "upgrade_via": "官方安装器",
        "run_proc": ["qwen-code", "qwen"],
    },
}

# 白名单升级命令（tool id -> cmd）；None 表示不支持自动升级
UPGRADE_CMDS = {t: meta["upgrade_cmd"] for t, meta in TOOLS.items()}

# 运行中的升级任务：tool -> {thread, proc, output(尾部), start, done, code}
_upgrade_tasks = {}
_upgrade_lock = threading.Lock()

# pi 扩展完整性关键文件（已知故障点）
PI_EXT_CHECKS = [
    (HOME / ".pi" / "agent" / "npm" / "node_modules" / "@hypabolic" / "hypa-linux-x64" / "bin" / "hypa", "hypa Linux 平台二进制（@hypabolic/pi-hypa 运行依赖）"),
    (HOME / ".pi" / "agent" / "npm" / "node_modules", "pi 扩展依赖目录 node_modules"),
]


def _run(cmd, timeout=8, env=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "（超时）"
    except Exception as e:
        return -1, str(e)


def _which_all(cmd):
    """返回 PATH 中所有实例"""
    paths = []
    found = set()
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(d) / cmd
        if p.is_file() and os.access(p, os.X_OK):
            key = str(p.resolve())
            if key not in found:
                found.add(key)
                paths.append(str(p))
    return paths


def _is_running(procs):
    try:
        r = subprocess.run(
            ["ps", "-eo", "comm"], capture_output=True, text=True, timeout=5
        )
        names = set(r.stdout.split())
        return any(p in names for p in procs)
    except Exception:
        return False


def _npm_global_version(pkg):
    """npm ls -g 中的已装版本"""
    if not pkg:
        return None
    code, out = _run(["npm", "ls", "-g", "--depth=0", pkg], timeout=12)
    m = re.search(rf"{re.escape(pkg)}@([\d.]+)", out)
    return m.group(1) if m else None


def _npm_latest(pkg):
    """npm 最新版本（联网，失败返回 None）"""
    if not pkg:
        return None
    code, out = _run(["npm", "view", pkg, "version"], timeout=10)
    if code == 0 and out.strip():
        return out.strip().splitlines()[-1][:30]
    return None


def env_report():
    """生成环境检测报告"""
    problems = []
    tools_out = []

    sys_node = _run(["node", "--version"], timeout=5)[1].strip()
    sys_npm = _run(["npm", "--version"], timeout=5)[1].strip()
    sys_py = _run([sys.executable, "--version"], timeout=5)[1].strip() or _run(["python3", "--version"], timeout=5)[1].strip()

    for tid, meta in TOOLS.items():
        inst = {"id": tid, "name": meta["name"], "installed": False}

        # 所有二进制实例
        bins = []
        versions = set()
        for b in meta["bin"]:
            for p in _which_all(b):
                code, out = _run([p, *meta["version_args"]], timeout=8)
                v = out.strip().splitlines()[0][:40] if code == 0 and out.strip() else ""
                bins.append({"path": p, "version": v})
                if v:
                    versions.add(v)

        installed = bool(bins)
        inst["installed"] = installed
        inst["bins"] = bins
        inst["version"] = bins[0]["version"] if bins else ""
        inst["primary_path"] = bins[0]["path"] if bins else ""
        inst["multiple_install"] = len(bins) > 1
        inst["install_method"] = _install_method(tid, bins)
        inst["running"] = _is_running(meta["run_proc"])
        inst["config_dir"] = next((str(c) for c in meta["config"] if c.exists()), "")
        inst["npm_pkg"] = meta["npm_pkg"]

        # 多版本冲突
        if len(versions) > 1:
            problems.append({
                "tool": tid, "level": "warn",
                "message": f"{meta['name']} 检测到 {len(bins)} 个安装实例且版本不一致",
                "detail": "；".join(f"{b['path']} ({b['version'] or '未知'})" for b in bins),
            })
        elif len(bins) > 1:
            problems.append({
                "tool": tid, "level": "info",
                "message": f"{meta['name']} 存在 {len(bins)} 个二进制实例（版本一致）",
                "detail": "；".join(b["path"] for b in bins),
            })

        # npm 包版本对比
        if meta["npm_pkg"] and installed:
            gv = _npm_global_version(meta["npm_pkg"])
            latest = _npm_latest(meta["npm_pkg"])
            inst["npm_global_version"] = gv
            inst["npm_latest_version"] = latest
            if latest and gv and latest != gv:
                problems.append({
                    "tool": tid, "level": "update",
                    "message": f"{meta['name']} 有可用更新：当前 {gv} → 最新 {latest}",
                    "detail": f"升级命令：{meta['upgrade_cmd']}",
                })
        # 未安装但 npm 包存在（如仅 npx 使用）
        if not installed and meta["npm_pkg"]:
            latest = _npm_latest(meta["npm_pkg"])
            if latest:
                inst["not_installed_latest"] = latest
        tools_out.append(inst)

    # pi 扩展完整性
    for path, desc in PI_EXT_CHECKS:
        if not path.exists():
            problems.append({
                "tool": "pi", "level": "error",
                "message": f"Pi 扩展完整性：缺少 {desc}",
                "detail": f"{path} 不存在 — 可能导致 pi 扩展/工具无法运行",
            })

    # 系统级多版本：node 多版本（.nvm 与系统）
    node_paths = _which_all("node")
    if len(node_paths) > 1:
        problems.append({
            "tool": "system", "level": "info",
            "message": f"检测到 {len(node_paths)} 个 node 可执行文件（nvm 多版本常见，通常正常）",
            "detail": "；".join(node_paths),
        })

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "system": {
            "platform": sys.platform,
            "arch": os.uname().machine if hasattr(os, "uname") else "?",
            "node": sys_node or "未安装",
            "npm": sys_npm or "未安装",
            "python": sys_py or "未安装",
        },
        "tools": tools_out,
        "problems": problems,
        "problem_count": len(problems),
        "upgradeable": [t["id"] for t in tools_out if UPGRADE_CMDS.get(t["id"]) and t["installed"]],
    }
    return report


def _install_method(tid, bins):
    if not bins:
        return ""
    p = bins[0]["path"]
    if ".nvm" in p:
        return "nvm / npm 全局"
    if tid == "kimi" and ".kimi-code" in p:
        return "官方安装器"
    if "AppImage" in p or p.endswith(".AppImage"):
        return "AppImage"
    if ".local/bin" in p or "/home/" in p:
        return "用户目录"
    return "系统目录"


def start_upgrade(tool_id):
    """后台启动升级任务（白名单命令）"""
    cmd = UPGRADE_CMDS.get(tool_id)
    if not cmd:
        return {"ok": False, "error": "该工具不支持自动升级，请走官方渠道"}
    with _upgrade_lock:
        if tool_id in _upgrade_tasks and not _upgrade_tasks[tool_id].get("done"):
            return {"ok": False, "error": "升级已在进行中"}

    task = {"tool": tool_id, "start": time.time(), "output": "", "done": False, "code": None}
    with _upgrade_lock:
        _upgrade_tasks[tool_id] = task

    def worker():
        try:
            proc = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1,
            )
            chunks = []
            total = 0
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                chunks.append(line)
                total += len(line)
                if total > 20000:
                    chunks = chunks[-200:]
                    total = sum(len(c) for c in chunks)
            proc.wait()
            task["code"] = proc.returncode
        except Exception as e:
            task["code"] = -1
            chunks = chunks if 'chunks' in dir() else []
            chunks.append(f"\n[执行异常] {e}")
        task["output"] = "".join(chunks[-300:])
        task["done"] = True

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "tool": tool_id, "command": cmd}


def upgrade_status(tool_id):
    with _upgrade_lock:
        task = _upgrade_tasks.get(tool_id)
    if not task:
        return {"running": False, "found": False}
    return {
        "found": True,
        "running": not task["done"],
        "done": task["done"],
        "code": task["code"],
        "output": task["output"][-8000:],
        "elapsed": round(time.time() - task["start"], 1) if task["done"] else round(time.time() - task["start"], 1),
    }
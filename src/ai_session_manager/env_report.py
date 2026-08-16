"""
环境检测与升级模块（多工具版）
检测各 AI 工具：安装方式 / 版本 / 多版本冲突 / 运行状态 / 配置目录，支持白名单命令升级。
"""

import json
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
        "upgrade_cmd": "npm install -g --allow-scripts=@anthropic-ai/claude-code @anthropic-ai/claude-code@latest",
        "upgrade_via": "npm 全局包",
        "run_proc": [r"claude-code", r"/claude(?:\s|$)"],
    },
    "codex": {
        "name": "Codex",
        "bin": ["codex"],
        "version_args": ["--version"],
        "npm_pkg": "@openai/codex",
        "config": [HOME / ".codex"],
        "upgrade_cmd": "npm install -g @openai/codex@latest",
        "upgrade_via": "npm 全局包",
        "run_proc": [r"codex"],
    },
    "pi": {
        "name": "Pi",
        "bin": ["pi"],
        "version_args": ["--version"],
        "npm_pkg": "@earendil-works/pi-coding-agent",
        "config": [HOME / ".pi"],
        "upgrade_cmd": "npm install -g @earendil-works/pi-coding-agent@latest",
        "upgrade_via": "npm 全局包",
        "run_proc": [r"pi-coding-agent", r"/pi(?:\s|$)"],
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
        "cli_version_safe": False,   # 桌面应用：执行 CLI 会与 daemon 握手，可能干扰主程序
        "npm_pkg": None,
        "config": [HOME / ".config" / "orca"],
        "upgrade_cmd": None,
        "upgrade_via": "桌面应用（官方渠道更新）",
        "run_proc": [r"orca-ide", r"daemon-entry"],
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
        "upgrade_cmd": "npm install -g @google/gemini-cli@latest",
        "upgrade_via": "npm 全局包",
        "run_proc": [r"gemini"],
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

# npm view 结果缓存（10 分钟内不重复联网查询）
_npm_view_cache = {}
_NPM_VIEW_TTL = 600
_npm_view_lock = threading.Lock()


def _npm_latest(pkg):
    """npm 最新版本（联网，10 分钟缓存；失败返回 None）"""
    if not pkg:
        return None
    now = time.time()
    with _npm_view_lock:
        cached = _npm_view_cache.get(pkg)
        if cached and now - cached[1] < _NPM_VIEW_TTL:
            return cached[0]
    code, out = _run(["npm", "view", pkg, "version"], timeout=8)
    latest = None
    if code == 0 and out.strip():
        # 取最后一行含版本号的行（npm 可能输出 warning 行）
        for line in reversed(out.splitlines()):
            line = line.strip()
            if re.search(r"\d+\.\d+\.\d+", line):
                latest = line[:30]
                break
    with _npm_view_lock:
        _npm_view_cache[pkg] = (latest, now)
    return latest

UPGRADE_HISTORY_FILE = HOME / ".agent-manager" / "upgrade-history.json"


def _load_history():
    try:
        if UPGRADE_HISTORY_FILE.is_file():
            return json.loads(UPGRADE_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"items": []}


def _save_history(items):
    try:
        UPGRADE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        UPGRADE_HISTORY_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def upgrade_history(limit=20):
    h = _load_history()
    return {"items": h.get("items", [])[:limit]}


def _record_history(tool, command, code):
    h = _load_history()
    h.setdefault("items", [])
    h["items"].insert(0, {
        "tool": tool,
        "tool_name": TOOLS.get(tool, {}).get("name", tool),
        "command": command,
        "code": code,
        "ok": code == 0,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    h["items"] = h["items"][:50]
    _save_history(h)

# pi 扩展完整性关键文件（已知故障点）
PI_EXT_CHECKS = [
    (HOME / ".pi" / "agent" / "npm" / "node_modules" / "@hypabolic" / "hypa-linux-x64" / "bin" / "hypa", "hypa Linux 平台二进制（@hypabolic/pi-hypa 运行依赖）"),
    (HOME / ".pi" / "agent" / "npm" / "node_modules", "pi 扩展依赖目录 node_modules"),
]


def _human_size(n):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _version_from_process(proc_names):
    """从运行进程参数中解析版本（如 orca daemon 的 --app-version 1.4.183），无副作用"""
    try:
        r = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            if not any(n in line for n in proc_names):
                continue
            m = re.search(r"--app-version\s+([\w.+-]+)", line)
            if m:
                return m.group(1)
            m = re.search(r"--version\s+([\w.+-]+)", line)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


def _extract_version(raw):
    """从命令输出提取版本号；无合法版本号返回空串"""
    if not raw:
        return ""
    m = re.search(r"\b[vV]?\d+\.\d+\.\d+(?:[-+][\w.-]+)?\b", raw)
    candidate = (m.group(0) if m else "").lstrip("vV")
    # 排除常见错误文本伪装（含 error/not installed 等关键字时丢弃）
    lower = raw.lower()
    if any(kw in lower for kw in ("error", "not installed", "not found", "command not", "cannot find", "failed")):
        return ""
    return candidate


def _version_tuple(v):
    """解析版本号为可比较元组（处理 0.9 vs 0.10、v 前缀、字母后缀）"""
    if not v:
        return (0,)
    v = str(v).strip().lstrip("vV")
    nums = []
    for part in re.split(r"[.\-+]", v):
        m = re.match(r"^(\d+)", part or "")
        if m:
            nums.append(int(m.group(1)))
        else:
            break
    return tuple(nums) if nums else (0,)


def version_gt(a, b):
    """语义化版本比较：a > b ?"""
    ta, tb = _version_tuple(a), _version_tuple(b)
    for x, y in zip(ta, tb):
        if x != y:
            return x > y
    return len(ta) > len(tb)


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
    """进程是否在运行。用 ps -eo args 匹配命令行（node 脚本进程 comm 是 node，
    必须匹配 args 中的工具路径/名称，如 node /path/to/claude-code/cli.js）。
    procs 元素为正则片段（如 "claude-code"、r"/pi(?:\\s|$)"）"""
    try:
        r = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            for pat in procs:
                if re.search(pat, line):
                    return True
    except Exception:
        pass
    return False


_npm_gv_cache = {}
_NPM_GV_TTL = 60
_npm_gv_lock = threading.Lock()


def _npm_global_version(pkg, use_cache=True):
    """npm ls -g 中的已装版本（60s 缓存）"""
    if not pkg:
        return None
    if use_cache:
        now = time.time()
        with _npm_gv_lock:
            cached = _npm_gv_cache.get(pkg)
            if cached and now - cached[1] < _NPM_GV_TTL:
                return cached[0]
    code, out = _run(["npm", "ls", "-g", "--depth=0", pkg], timeout=12)
    m = re.search(rf"{re.escape(pkg)}@([\d.]+)", out)
    gv = m.group(1) if m else None
    if use_cache:
        with _npm_gv_lock:
            _npm_gv_cache[pkg] = (gv, time.time())
    return gv


def env_report():
    """生成环境检测报告"""
    problems = []
    tools_out = []

    sys_node = _run(["node", "--version"], timeout=5)[1].strip()
    sys_npm = _run(["npm", "--version"], timeout=5)[1].strip()
    sys_py = _run([sys.executable, "--version"], timeout=5)[1].strip() or _run(["python3", "--version"], timeout=5)[1].strip()

    # npm registry（读 .npmrc，升级是否走镜像）
    npmrc = ""
    for rc in [HOME / ".npmrc", HOME / ".config" / "npm" / ".npmrc"]:
        if rc.is_file():
            try:
                for line in rc.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.strip().startswith("registry="):
                        npmrc = line.strip().split("=", 1)[1].strip()
                        break
            except Exception:
                pass
            if npmrc:
                break
    proxy = {k: v for k, v in os.environ.items() if k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")}
    try:
        disk = shutil.disk_usage(str(HOME))
        disk_free = disk.free
    except Exception:
        disk_free = None

    from concurrent.futures import ThreadPoolExecutor

    def _scan_one(tid_meta):
        tid, meta = tid_meta
        inst = {"id": tid, "name": meta["name"], "installed": False}
        problems_i = []

        # 所有二进制实例
        bins = []
        versions = set()
        version_output_issues = []
        cli_safe = meta.get("cli_version_safe", True)
        for b in meta["bin"]:
            for p in _which_all(b):
                v = ""
                raw = ""
                if cli_safe:
                    code, out = _run([p, *meta["version_args"]], timeout=8)
                    raw = out.strip().splitlines()[0][:60] if out.strip() else ""
                    v = _extract_version(raw)
                else:
                    # 桌面应用：不执行 CLI（避免与主程序交互），从运行进程参数解析版本
                    v = _version_from_process(meta.get("run_proc", []))
                if raw and not v:
                    version_output_issues.append(f"{p}: {raw}")
                bins.append({"path": p, "version": v})
                if v:
                    versions.add(v)
        if version_output_issues:
            problems_i.append({
                "tool": tid, "level": "error",
                "message": f"{meta['name']} CLI 版本输出异常（可能是安装脚本未执行或安装损坏）",
                "detail": "；".join(version_output_issues[:3]) + "。npm 全局包可尝试带 --allow-scripts 重新安装",
            })

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
            problems_i.append({
                "tool": tid, "level": "warn",
                "message": f"{meta['name']} 检测到 {len(bins)} 个安装实例且版本不一致",
                "detail": "；".join(f"{b['path']} ({b['version'] or '未知'})" for b in bins),
            })
        elif len(bins) > 1:
            problems_i.append({
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
            inst["has_update"] = bool(latest and gv and version_gt(latest, gv))
            if inst["has_update"]:
                problems_i.append({
                    "tool": tid, "level": "update",
                    "message": f"{meta['name']} 有可用更新：当前 {gv} → 最新 {latest}",
                    "detail": f"升级命令：{meta['upgrade_cmd']}",
                })
        # 未安装但 npm 包存在（如仅 npx 使用）
        if not installed and meta["npm_pkg"]:
            latest = _npm_latest(meta["npm_pkg"])
            if latest:
                inst["not_installed_latest"] = latest
                inst["has_install"] = True
        return inst, problems_i

    with ThreadPoolExecutor(max_workers=6) as pool:
        for inst, probs in pool.map(_scan_one, TOOLS.items()):
            tools_out.append(inst)
            problems.extend(probs)

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
            "npm_registry": npmrc or "npm 官方源",
            "proxy": proxy or {},
            "disk_free": disk_free,
            "disk_free_human": _human_size(disk_free) if disk_free is not None else "未知",
        },
        "tools": tools_out,
        "problems": problems,
        "problem_count": len(problems),
        "upgradeable": [t["id"] for t in tools_out
                        if UPGRADE_CMDS.get(t["id"])
                        and ((t["installed"] and t.get("has_update")) or (not t["installed"] and t.get("has_install")))],
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


def upgrade_info(tool_id):
    """返回某工具的升级命令信息（后端白名单为准，前端仅展示）"""
    meta = TOOLS.get(tool_id)
    if not meta:
        return {"error": "未知工具"}
    cmd = UPGRADE_CMDS.get(tool_id)
    return {
        "tool": tool_id,
        "tool_name": meta["name"],
        "command": cmd,
        "via": meta.get("upgrade_via", ""),
        "supports_upgrade": bool(cmd),
        "npm_pkg": meta.get("npm_pkg"),
    }


def start_upgrade(tool_id):
    """后台启动升级任务（白名单命令）"""
    cmd = UPGRADE_CMDS.get(tool_id)
    if not cmd:
        return {"ok": False, "error": "该工具不支持自动升级，请走官方渠道"}
    # 已是最新则明确告知（避免"点了升级没变化"的困惑）
    meta = TOOLS.get(tool_id)
    if meta and meta.get("npm_pkg"):
        gv = _npm_global_version(meta["npm_pkg"])
        latest = _npm_latest(meta["npm_pkg"])
        if gv and latest and not version_gt(latest, gv):
            return {"ok": False, "error": f"已是最新版本 {gv}，无需升级"}
        if not gv and latest:
            pass  # 未安装场景：执行安装
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
        _record_history(tool_id, cmd, task.get("code"))
        # 升级后自动验证：重读版本并对比
        try:
            meta = TOOLS.get(tool_id)
            verify_lines = []
            if meta:
                for b in meta["bin"]:
                    for p in _which_all(b):
                        code, out = _run([p, *meta["version_args"]], timeout=10)
                        raw = out.strip().splitlines()[0][:60] if out.strip() else ""
                        v = _extract_version(raw)
                        if code == 0:
                            if v:
                                verify_lines.append(f"版本验证: {b} → {v}")
                            else:
                                verify_lines.append(f"⚠️ 版本验证异常: {b} → {raw or '(无输出)'}（可能安装脚本未执行，试试带 --allow-scripts 重装）")
                        else:
                            verify_lines.append(f"⚠️ 版本命令失败({code}): {b}")
                        break  # 每个 bin 名只验证第一个实例
                    if verify_lines:
                        break
            if verify_lines:
                task["output"] += "\n" + "\n".join(verify_lines)
        except Exception:
            pass

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
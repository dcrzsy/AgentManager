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
        "version_exclude": ["CLN", "Written by"],  # /usr/bin/pi 是 GNU CLN 计算器，非 AI 工具
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
        "npm_pkg": "@qwen-code/qwen-code",
        "config": [HOME / ".qwen-code", HOME / ".qwen", HOME / ".cache" / "qwen-code"],
        "upgrade_cmd": "npm install -g @qwen-code/qwen-code@latest",
        "upgrade_via": "npm 全局包 / 官方安装器",
        "run_proc": ["qwen-code", "qwen"],
    },
}

# 白名单升级命令（tool id -> cmd）；None 表示不支持自动升级
UPGRADE_CMDS = {t: meta["upgrade_cmd"] for t, meta in TOOLS.items()}

# 白名单卸载命令（tool id -> cmd）；None 表示需走官方渠道
UNINSTALL_CMDS = {
    "claude": "npm uninstall -g @anthropic-ai/claude-code",
    "codex": "npm uninstall -g @openai/codex",
    "pi": "npm uninstall -g @earendil-works/pi-coding-agent",
    "gemini": "npm uninstall -g @google/gemini-cli",
    "aider": "pip uninstall -y aider-chat",
    "qwen": "npm uninstall -g @qwen-code/qwen-code",
    "kimi": None, "orca": None, "hermes": None,
}

# 文件系统安装工具的白名单卸载路径（tool_id -> {paths: [(HOME 相对路径, 是否数据目录)], note}）
UNINSTALL_FS = {
    "kimi": {
        "paths": [(".kimi-code", False)],
        "note": "官方安装器目录（~/.kimi-code，含 CLI 与运行时）",
    },
    "hermes": {
        "paths": [(".local/bin/hermes", False), (".hermes", True)],
        "note": "命令行入口 + 数据目录（~/.hermes，含会话/认证/备份，5GB 级）",
    },
    "orca": {
        "paths": [(".config/orca/linux-orca-cli-shim", False), (".local/bin/orca-ide", False), (".config/orca", True)],
        "note": "桌面应用 CLI shim + 入口 + 配置目录（~/.config/orca）",
        "dynamic": ["Applications", ".local/bin"],  # 在其中找 orca*.AppImage
    },
}

def _find_appimage(basename_key, search_dirs):
    """在搜索目录中找匹配的 AppImage（避免硬编码路径漂移）"""
    for d in search_dirs:
        p = HOME / d
        if not p.is_dir():
            continue
        for f in sorted(p.iterdir(), reverse=True):
            if f.is_file() and f.suffix == ".AppImage" and basename_key.lower() in f.name.lower():
                return f
    return None

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
    code, out = _run_with_bins([_primary_npm(), "view", pkg, "version"], timeout=8)
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
        ensure_owner(UPGRADE_HISTORY_FILE)
        ensure_owner(UPGRADE_HISTORY_FILE.parent)
    except Exception:
        pass


def upgrade_history(limit=20):
    h = _load_history()
    return {"items": h.get("items", [])[:limit]}


def _record_history(tool, command, code, htype="upgrade"):
    h = _load_history()
    h.setdefault("items", [])
    h["items"].insert(0, {
        "tool": tool,
        "tool_name": TOOLS.get(tool, {}).get("name", tool),
        "command": command,
        "code": code,
        "ok": code == 0,
        "type": htype,
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


def _run_with_bins(cmd, timeout=8):
    """以增强 PATH 运行（当前 PATH + 已知 bin 目录）。
    管理员模式(root)下 PATH 常缺 nvm/node，导致 codex/pi 等 node 脚本执行失败"""
    env = os.environ.copy()
    extra = _known_bin_dirs()
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return _run(cmd, timeout=timeout, env=env)


def _run(cmd, timeout=8, env=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "（超时）"
    except Exception as e:
        return -1, str(e)


_extra_bin_dirs = None


def _known_bin_dirs():
    """PATH 之外的已知 bin 目录（nvm 全局 / 用户目录 / kimi / orca shim）。
    从桌面/orca 等环境启动时 PATH 常缺 nvm，导致漏检 npm 全局工具"""
    global _extra_bin_dirs
    if _extra_bin_dirs is not None:
        return _extra_bin_dirs
    dirs = []
    nvm = HOME / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        vers = sorted((d for d in nvm.iterdir() if d.is_dir()),
                      key=lambda d: d.name, reverse=True)
        if vers:
            dirs.append(str(vers[0] / "bin"))
    for d in (HOME / ".local" / "bin",
              HOME / ".hermes" / "node" / "bin",   # hermes 自带 node 环境（升级常装到这里）
              HOME / ".kimi-code" / "bin",
              HOME / ".config" / "orca" / "linux-orca-cli-shim",
              HOME / ".npm-global" / "bin"):
        if d.is_dir():
            dirs.append(str(d))
    _extra_bin_dirs = dirs
    return dirs


def _which_all(cmd):
    """返回 PATH 及已知目录中的所有实例"""
    paths = []
    found = set()
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    for d in dirs + _known_bin_dirs():
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


_env_scan_cache = None


def _is_admin():
    """是否以管理员(root)权限运行"""
    try:
        return os.geteuid() == 0
    except Exception:
        return False


_REAL_USER = None


def _real_user():
    """管理员模式下的真实用户（passwd 中 uid!=0 且 uid 最小的常规用户）"""
    global _REAL_USER
    if not _is_admin():
        return None
    if _REAL_USER is not None:
        return _REAL_USER
    try:
        import pwd
        for pw in pwd.getpwall():
            if pw.pw_uid == 1000:
                _REAL_USER = pw
                return pw
        for pw in pwd.getpwall():
            if 1000 <= pw.pw_uid < 60000:
                _REAL_USER = pw
                return pw
    except Exception:
        pass
    return None


def _real_user_home():
    """真实用户 HOME：管理员模式下 HOME 可能是 /root，尝试从 passwd 推断实际用户"""
    if not _is_admin():
        return HOME
    try:
        pw = _real_user()
        if pw:
            return Path(pw.pw_dir)
    except Exception:
        pass
    return HOME


def ensure_owner(path):
    """管理员模式下，把真实用户 HOME 下新建/修改的文件/目录属主改回真实用户
    （避免 root 写入导致用户之后无法修改/删除）"""
    try:
        if not _is_admin():
            return
        pw = _real_user()
        if not pw:
            return
        p = Path(path)
        real_home = Path(pw.pw_dir)
        if not str(p).startswith(str(real_home)):
            return
        os.chown(p, pw.pw_uid, pw.pw_gid)
        if p.is_dir():
            for sub in p.rglob("*"):
                try:
                    os.chown(sub, pw.pw_uid, pw.pw_gid)
                except Exception:
                    pass
    except Exception:
        pass


def admin_write_error():
    """管理员模式未保留用户 HOME 时的写操作拦截信息（None=可写）"""
    if _is_admin() and str(HOME).startswith("/root"):
        real = _real_user_home()
        if real != HOME:
            return ("管理员模式未保留用户 HOME（当前 HOME=" + str(HOME) + "）。"
                    "写操作会进入 root 环境。请用「sudo --preserve-env=HOME 启动」后重试")
    return None


def _permission_issues():
    """扫描关键目录可读性，返回无权限问题列表（避免静默漏扫）"""
    issues = []
    checked = []
    # 系统级全局目录
    for d in ("/usr/lib/node_modules", "/usr/local/lib/node_modules",
              "/opt/node_modules", "/usr/lib/nodejs"):
        p = Path(d)
        if p.exists() and not os.access(p, os.R_OK):
            issues.append(f"{d}（无读取权限）")
        elif p.exists():
            checked.append(d)
    # root 环境（管理员/root 组用户可读时检查）
    root_home = Path("/root")
    if root_home.exists() and not os.access(root_home, os.R_OK):
        issues.append("/root（无读取权限，root 用户安装的工具可能漏检）")
    elif root_home.exists():
        checked.append("/root")
    # 各 node 环境的 lib/node_modules
    for name, e in _node_envs().items():
        nm = Path(e["dir"]) / "lib" / "node_modules"
        if nm.exists() and not os.access(nm, os.R_OK):
            issues.append(f"{nm}（{name} 环境无读取权限）")
    return issues, checked


def _node_envs():
    """扫描所有 node 环境及其 npm 全局包版本（读 package.json，不跑 npm）。
    返回 {环境名: {"dir": str, "pkgs": {包名: 版本}}}"""
    global _env_scan_cache
    if _env_scan_cache is not None:
        return _env_scan_cache
    envs = {}
    nvm = HOME / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        vers = sorted((d for d in nvm.iterdir() if d.is_dir()),
                      key=lambda d: d.name, reverse=True)
        if vers:
            envs["nvm"] = str(vers[0])
    hermes = HOME / ".hermes" / "node"
    if hermes.is_dir():
        envs["hermes"] = str(hermes)
    sys_node = shutil.which("node")
    if sys_node and not any(str(sys_node).startswith(d) for d in envs.values()):
        envs["系统"] = str(Path(sys_node).parent.parent)

    out = {}
    for name, node_dir in envs.items():
        nm = Path(node_dir) / "lib" / "node_modules"
        pkgs = {}
        if nm.is_dir():
            for sub in nm.iterdir():
                pf = sub / "package.json"
                if pf.is_file():
                    try:
                        v = json.loads(pf.read_text(encoding="utf-8")).get("version")
                        if v:
                            pkgs[sub.name] = v
                    except Exception:
                        pass
                elif sub.is_dir():  # @scope/pkg
                    for sub2 in sub.iterdir():
                        pf2 = sub2 / "package.json"
                        if pf2.is_file():
                            try:
                                v = json.loads(pf2.read_text(encoding="utf-8")).get("version")
                                if v:
                                    pkgs[f"{sub.name}/{sub2.name}"] = v
                            except Exception:
                                pass
        out[name] = {"dir": node_dir, "pkgs": pkgs}
    _env_scan_cache = out
    return out


def _env_diagnostics():
    """跨环境 npm 包版本诊断：
    - 同一工具在多个 node 环境版本不一致 → 升级可能未同步
    - PATH 中 npm 与主 npm 错位 → 升级装错环境风险"""
    problems = []
    envs = _node_envs()
    if len(envs) < 2:
        return problems
    # 每对环境的公共包版本比较
    names = list(envs.keys())
    all_pkgs = set()
    for e in envs.values():
        all_pkgs.update(e["pkgs"].keys())
    for pkg in sorted(all_pkgs):
        vers = {n: envs[n]["pkgs"].get(pkg) for n in names}
        have = {n: v for n, v in vers.items() if v}
        if len(have) >= 2 and len(set(have.values())) > 1:
            which = next((tid for tid, m in TOOLS.items() if m.get("npm_pkg") == pkg), None)
            problems.append({
                "tool": which or "system", "level": "warn",
                "message": f"「{pkg}」在多个 node 环境版本不一致："
                           + "；".join(f"{n}={v}" for n, v in have.items()),
                "detail": "可能是升级只装到了一个环境（如 ~/.hermes/node 或 nvm）。"
                          + ("升级现会统一使用主环境 npm，可重新检测后再次升级。" if which else ""),
            })
    # npm 错位：PATH 首个 npm vs 主 npm
    path_npm = ""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d:
            p = Path(d) / "npm"
            if p.is_file():
                path_npm = str(p)
                break
    primary = _primary_npm()
    if path_npm and primary != "npm" and path_npm != primary and Path(path_npm).exists():
        problems.append({
            "tool": "system", "level": "info",
            "message": "当前环境 PATH 的 npm 与主环境 npm 不一致（升级错位风险已规避）",
            "detail": f"PATH npm: {path_npm}；主环境 npm: {primary}。升级命令已统一使用主环境 npm。",
        })
    return problems


def _primary_npm():
    """主环境 npm 全路径：优先 nvm（用户终端环境），其次已知目录中的 npm。
    避免从 orca/hermes 环境升级时装到 ~/.hermes/node 而主环境不生效"""
    nvm = HOME / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        vers = sorted((d for d in nvm.iterdir() if d.is_dir()),
                      key=lambda d: d.name, reverse=True)
        for v in vers:
            npm = v / "bin" / "npm"
            if npm.is_file():
                return str(npm)
    for d in _known_bin_dirs():
        npm = Path(d) / "npm"
        if npm.is_file() and str(npm).startswith(str(HOME)):
            return str(npm)
    return "npm"


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
    code, out = _run_with_bins([_primary_npm(), "ls", "-g", "--depth=0", pkg], timeout=12)
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

    sys_node = _run_with_bins(["node", "--version"], timeout=5)[1].strip()
    sys_npm = _run_with_bins(["npm", "--version"], timeout=5)[1].strip()
    # Python 版本：优先 PATH 中的 python3/python（PyInstaller 打包环境下
    # sys.executable 指向应用自身，--version 会输出应用版本而非 Python 版本）
    sys_py = _run(["python3", "--version"], timeout=5)[1].strip()
    if not sys_py or "Python" not in sys_py:
        sys_py = _run(["python", "--version"], timeout=5)[1].strip()
    if not sys_py or "Python" not in sys_py:
        cand = _run([sys.executable, "--version"], timeout=5)[1].strip()
        if cand.startswith("Python"):
            sys_py = cand

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
        excl = meta.get("version_exclude", [])
        for b in meta["bin"]:
            for p in _which_all(b):
                v = ""
                raw = ""
                if cli_safe:
                    code, out = _run_with_bins([p, *meta["version_args"]], timeout=8)
                    raw = out.strip().splitlines()[0][:60] if out.strip() else ""
                    v = _extract_version(raw)
                else:
                    # 桌面应用：不执行 CLI（避免与主程序交互），从运行进程参数解析版本
                    v = _version_from_process(meta.get("run_proc", []))
                # 无关同名二进制（如 GNU CLN 的 pi 计算器）排除
                if excl and any(h in raw for h in excl):
                    continue
                if raw and not v:
                    version_output_issues.append(f"{p}: {raw}")
                bins.append({"path": p, "version": v})
                if v:
                    versions.add(v)
        if version_output_issues:
            act = {"type": "reinstall", "tool": tid} if UPGRADE_CMDS.get(tid) else None
            problems_i.append({
                "tool": tid, "level": "error",
                "message": f"{meta['name']} CLI 版本输出异常（可能是安装脚本未执行或安装损坏）",
                "detail": "；".join(version_output_issues[:3]) + "。npm 全局包可尝试带 --allow-scripts 重新安装",
                "action": act,
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
        # 未安装但可自动安装：npm 包存在，或有升级命令（pip/官方命令）
        if not installed and (meta["npm_pkg"] or meta["upgrade_cmd"]):
            if meta["npm_pkg"]:
                latest = _npm_latest(meta["npm_pkg"])
                if latest:
                    inst["not_installed_latest"] = latest
                    inst["has_install"] = True
            elif meta["upgrade_cmd"]:
                # pip 类工具：升级命令在未安装时即安装最新版
                inst["has_install"] = True
        return inst, problems_i

    with ThreadPoolExecutor(max_workers=6) as pool:
        for inst, probs in pool.map(_scan_one, TOOLS.items()):
            tools_out.append(inst)
            problems.extend(probs)

    # 文件系统安装工具：注入卸载路径详情（前端确认弹窗展示）
    for t in tools_out:
        fs_spec = UNINSTALL_FS.get(t["id"])
        if fs_spec:
            fs_paths = [str(HOME / rel) for rel, _ in fs_spec["paths"]]
            for sd in fs_spec.get("dynamic", []):
                app = _find_appimage(t["id"], [sd])
                if app:
                    fs_paths.append(str(app))
            t["uninstall_fs"] = {"note": fs_spec["note"], "paths": fs_paths}

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

    # 跨环境 npm 包版本诊断（升级错位检测）
    problems.extend(_env_diagnostics())

    # 权限诊断（避免无权限静默漏扫）
    perm_issues, _ = _permission_issues()
    if perm_issues:
        problems.append({
            "tool": "system", "level": "warn",
            "message": "以下目录无读取权限，可能漏检其中安装的工具（建议管理员权限运行）",
            "detail": "；".join(perm_issues[:6]),
        })
    # 管理员模式 + HOME 错配检测
    if _is_admin() and str(HOME).startswith("/root"):
        problems.append({
            "tool": "system", "level": "warn",
            "message": "管理员模式但 HOME 指向 /root（未保留用户环境），检测到的是 root 环境而非您的环境",
            "detail": f"请用「sudo --preserve-env=HOME 启动」以检测 /home/dcrzsy 环境（当前 HOME={HOME}）",
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
        "uninstallable": [t["id"] for t in tools_out
                          if (UNINSTALL_CMDS.get(t["id"]) or UNINSTALL_FS.get(t["id"])) and t["installed"]],
        "admin": _is_admin(),
        "real_user_home": str(_real_user_home()),
        "permission_issues": _permission_issues()[0][:8],
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
    # npm 工具统一用主 npm，避免从 orca/hermes 环境启动时装到 ~/.hermes/node
    if "npm install" in cmd and cmd.startswith("npm "):
        cmd = cmd.replace("npm ", _primary_npm() + " ", 1)
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
            # 增强 PATH：管理员(root)模式下 PATH 常缺 node/npm，npm 脚本 shebang 找不到 node
            penv = os.environ.copy()
            penv["PATH"] = os.pathsep.join(_known_bin_dirs() + [penv.get("PATH", "")])
            proc = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1, env=penv,
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
                        code, out = _run_with_bins([p, *meta["version_args"]], timeout=10)
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


def _uninstall_fs(tool_id):
    """文件系统安装工具卸载：白名单路径清理（路径安全校验 + 数据目录保护提示已在确认弹窗完成）"""
    spec = UNINSTALL_FS.get(tool_id)
    if not spec:
        return "无卸载定义"
    lines = []
    paths = [(str(HOME / rel), is_data) for rel, is_data in spec["paths"]]
    # 动态补充 AppImage（orca 等）
    for sd in spec.get("dynamic", []):
        app = _find_appimage(tool_id, [sd])
        if app:
            paths.append((str(app), False))
    for target_s, is_data in paths:
        target = Path(target_s)
        try:
            # 路径安全校验：必须在 HOME 内（白名单兜底）
            if not str(target.resolve()).startswith(str(HOME.resolve())):
                lines.append(f"⚠️ 跳过异常路径: {target}")
                continue
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
                lines.append(f"✓ 已删除目录 {target}" + ("（数据目录）" if is_data else ""))
            elif target.is_file() or target.is_symlink():
                target.unlink(missing_ok=True)
                lines.append(f"✓ 已删除 {target}")
            else:
                lines.append(f"· 不存在，跳过 {target}")
        except Exception as e:
            lines.append(f"⚠️ 删除失败 {target}: {e}")
    return "\n".join(lines)


def _uninstall_npm_all_envs(pkg, tool_id):
    """从所有 node 环境彻底卸载 npm 包：主环境 npm uninstall + 其他环境文件清理 + bin 链接清理"""
    global _env_scan_cache
    lines = []
    meta = TOOLS.get(tool_id, {})
    bin_names = meta.get("bin", [])
    envs = _node_envs()

    # 1. 主环境用 npm uninstall（规范卸载，更新 npm 记录）
    primary = _primary_npm()
    try:
        code, out = _run_with_bins([primary, "uninstall", "-g", pkg], timeout=90)
        lines.append(f"主环境卸载（{primary}）：" + (out.strip()[:200] or f"退出码 {code}"))
    except Exception as e:
        lines.append(f"主环境卸载异常：{e}")

    # 2. 其他环境文件清理（nvm 其他版本 / hermes / 系统）
    for name, e in envs.items():
        nm = Path(e["dir"]) / "lib" / "node_modules"
        if not nm.is_dir():
            continue
        targets = [nm / pkg]
        if "/" in pkg:
            scope, sub = pkg.split("/", 1)
            targets.append(nm / scope / sub)
        for t in targets:
            try:
                if t.exists():
                    # 路径安全校验：必须在 node_modules 内
                    if str(t.resolve()).startswith(str(nm.resolve())):
                        shutil.rmtree(t, ignore_errors=True)
                        lines.append(f"✓ 已清除 {name} 环境: {t}")
                    else:
                        lines.append(f"⚠️ 跳过异常路径: {t}")
            except Exception as ex:
                lines.append(f"⚠️ {name} 清理失败: {ex}")

    # 3. bin 链接清理（各环境 bin/ 下指向该包的符号链接）
    for name, e in envs.items():
        bd = Path(e["dir"]) / "bin"
        if not bd.is_dir():
            continue
        for f in bd.iterdir():
            try:
                if not f.is_symlink():
                    continue
                target = Path(f.resolve())
                is_pkg = False
                for b in bin_names:
                    if b in f.name:
                        is_pkg = True
                        break
                if is_pkg and any(str(target).startswith(str(Path(e["dir"]) / "lib" / "node_modules" / p) ) for p in [pkg]):
                    f.unlink(missing_ok=True)
                    lines.append(f"✓ 已清除 bin 链接: {f}")
            except Exception:
                pass
        # 也清理 bin 目录下与工具同名但目标在包内的链接（如 claude -> ../lib/node_modules/@anthropic-ai/claude-code/...）
        for f in bd.iterdir():
            try:
                if f.is_symlink() and bin_names and f.name in bin_names:
                    tgt = str(Path(f.resolve()))
                    if ("node_modules" in tgt and pkg.split("/")[-1] in tgt):
                        f.unlink(missing_ok=True)
                        lines.append(f"✓ 已清除 bin 链接: {f}")
            except Exception:
                pass

    # 4. 验证：所有 bin 名在所有搜索路径中是否已无实例
    _env_scan_cache = None  # 环境缓存失效
    remain = []
    for b in bin_names:
        remain += _which_all(b)
    if remain:
        lines.append("⚠️ 卸载后仍有实例：" + "；".join(remain))
    else:
        lines.append("✓ 已验证：所有环境均已卸载干净")
    return "\n".join(lines)


def start_reinstall(tool_id):
    """重装修复：执行升级命令但跳过'已最新'拦截（用于安装损坏/版本输出异常场景）"""
    cmd = UPGRADE_CMDS.get(tool_id)
    if not cmd:
        return {"ok": False, "error": "该工具不支持自动重装，请走官方渠道"}
    if "npm install" in cmd and cmd.startswith("npm "):
        cmd = cmd.replace("npm ", _primary_npm() + " ", 1)
    with _upgrade_lock:
        if tool_id in _upgrade_tasks and not _upgrade_tasks[tool_id].get("done"):
            return {"ok": False, "error": "该工具已有任务在进行中"}
    task = {"tool": tool_id, "start": time.time(), "output": "",
            "done": False, "code": None, "type": "reinstall"}
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
        _record_history(tool_id, f"重装: {cmd}", task.get("code"), htype="reinstall")
        # 重装后验证
        try:
            meta = TOOLS.get(tool_id)
            verify_lines = []
            if meta:
                for b in meta["bin"]:
                    for p in _which_all(b):
                        code, out = _run_with_bins([p, *meta["version_args"]], timeout=10)
                        raw = out.strip().splitlines()[0][:60] if out.strip() else ""
                        v = _extract_version(raw)
                        if code == 0:
                            if v:
                                verify_lines.append(f"版本验证: {b} → {v}")
                            else:
                                verify_lines.append(f"⚠️ 版本验证异常: {b} → {raw or '(无输出)'}（可能仍缺 node，见上方输出）")
                        else:
                            verify_lines.append(f"⚠️ 版本命令失败({code}): {b}")
                        break
                    if verify_lines:
                        break
            if verify_lines:
                task["output"] += "\n" + "\n".join(verify_lines)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "tool": tool_id, "command": cmd}


def start_uninstall(tool_id):
    """后台启动卸载任务（彻底卸载：所有 node 环境 + bin 链接 + 验证）"""
    meta = TOOLS.get(tool_id)
    pkg = meta.get("npm_pkg") if meta else None
    cmd = UNINSTALL_CMDS.get(tool_id)
    if pkg:
        # npm 工具：跨环境彻底卸载
        with _upgrade_lock:
            if tool_id in _upgrade_tasks and not _upgrade_tasks[tool_id].get("done"):
                return {"ok": False, "error": "该工具已有任务在进行中"}
        task = {"tool": tool_id, "start": time.time(), "output": "",
                "done": False, "code": None, "type": "uninstall"}
        with _upgrade_lock:
            _upgrade_tasks[tool_id] = task

        def worker():
            try:
                out = _uninstall_npm_all_envs(pkg, tool_id)
                # 卸载后验证：主环境全局包应已消失（use_cache=False 强制重新检测）
                remain = _npm_global_version(pkg, use_cache=False)
                if remain:
                    task["code"] = 1
                    out += f"\n⚠️ 卸载验证未通过：主环境仍检测到 {pkg} {remain}"
                else:
                    task["code"] = 0
                    out += "\n✓ 卸载验证通过：主环境已无该包"
                task["output"] = out
            except Exception as e:
                task["code"] = -1
                task["output"] = f"[执行异常] {e}"
            task["done"] = True
            _record_history(tool_id, f"uninstall {pkg}（所有环境）", task.get("code"), htype="uninstall")

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "tool": tool_id, "command": f"卸载 {pkg}（所有 node 环境）"}
    if UNINSTALL_FS.get(tool_id):
        # 文件系统安装工具：白名单路径清理 + 验证
        with _upgrade_lock:
            if tool_id in _upgrade_tasks and not _upgrade_tasks[tool_id].get("done"):
                return {"ok": False, "error": "该工具已有任务在进行中"}
        task = {"tool": tool_id, "start": time.time(), "output": "",
                "done": False, "code": None, "type": "uninstall"}
        with _upgrade_lock:
            _upgrade_tasks[tool_id] = task

        def worker_fs():
            try:
                out = _uninstall_fs(tool_id)
                remain = _which_all(TOOLS.get(tool_id, {}).get("bin", [""])[0]) if TOOLS.get(tool_id, {}).get("bin") else []
                if remain:
                    task["code"] = 1
                    out += f"\n⚠️ 卸载验证未通过：仍检测到 {remain[0]}"
                else:
                    task["code"] = 0
                    out += "\n✓ 卸载验证通过：命令行入口已不存在"
                task["output"] = out
            except Exception as e:
                task["code"] = -1
                task["output"] = f"[执行异常] {e}"
            task["done"] = True
            _record_history(tool_id, f"uninstall {tool_id}（文件系统）", task.get("code"), htype="uninstall")

        threading.Thread(target=worker_fs, daemon=True).start()
        return {"ok": True, "tool": tool_id, "command": f"卸载 {tool_id}（文件系统清理）"}

    if not cmd:
        return {"ok": False, "error": "该工具不支持自动卸载，请走官方渠道"}
    with _upgrade_lock:
        if tool_id in _upgrade_tasks and not _upgrade_tasks[tool_id].get("done"):
            return {"ok": False, "error": "该工具已有任务在进行中"}
    task = {"tool": tool_id, "start": time.time(), "output": "",
            "done": False, "code": None, "type": "uninstall"}
    with _upgrade_lock:
        _upgrade_tasks[tool_id] = task

    def worker2():
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
            task["output"] = "".join(chunks[-300:])
        except Exception as e:
            task["code"] = -1
            task["output"] = f"[执行异常] {e}"
        task["done"] = True
        _record_history(tool_id, cmd, task.get("code"), htype="uninstall")

    threading.Thread(target=worker2, daemon=True).start()
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
        "type": task.get("type", "upgrade"),
        "output": task["output"][-8000:],
        "elapsed": round(time.time() - task["start"], 1) if task["done"] else round(time.time() - task["start"], 1),
    }
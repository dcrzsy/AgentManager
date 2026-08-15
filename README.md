# Agent 管理器

一个跨平台的 Agent 管理工具，目前支持会话管理，后续会逐步加入 Skill 管理、MCP 管理等功能。

目前支持管理和清理以下 AI 客户端产生的会话文件：

| 工具 | 状态 | 说明 |
|------|------|------|
| Claude Code | ✅ 默认 | `~/.claude/sessions`、`~/.claude/projects`、`~/.claude/history.jsonl` |
| Codex | ✅ 默认 | `~/.codex/sessions`，同步清理 `state_5.sqlite` 失效索引 |
| Orca | ✅ 默认 | `~/.config/orca/codex-runtime-home/...` |
| Kimi Code | ✅ 默认 | `~/.kimi-code/sessions`（state.json + wire.jsonl 合并展示），同步清理 `session_index.jsonl` |
| Pi | ✅ 默认 | `~/.pi/agent/sessions`、`~/.omp/agent/sessions` |
| Hermes | ✅ 默认 | `~/.hermes/state.db`（SQLite，删除不可恢复） |
| Aider | 🧪 可选 | `~/.aider` |
| OpenCode | 🧪 可选 | `~/.local/share/opencode` |
| Gemini CLI | 🧪 可选 | `~/.gemini/sessions` |
| Cline | 🧪 可选 | `~/.config/Cline`、`~/.cline` |
| Qwen Code | 🧪 可选 | `~/.qwen-code/sessions` |
| Windsurf | 🧪 可选 | `~/.codeium/windsurf` |

> 🧪 实验性工具只在对应目录真实存在时才会被扫描，不存在自动忽略。

## 功能

- 🔍 并发扫描多 AI 客户端会话文件（默认 6 个工具，可勾选更多）
- 📊 按工具、目录、时间分组展示，统计各工具会话数与大小
- 🔎 支持标题、路径、CWD 模糊搜索
- 🔃 点击表头按文件名 / 标题 / 目录 / 时间 / 大小排序
- 🗑️ **回收站机制**：删除默认移入 `~/.agent-manager-trash`，可随时恢复或彻底清除
- 🛡️ 关键数据文件保护：`state.db`、`history.jsonl`、`settings.json` 等一律拒绝删除
- 📄 导出当前列表为 CSV（带中文标题，Excel 可直接打开）
- 💬 会话预览：对话视图 / 原始 JSON 视图，消息可搜索、长消息可折叠
- 🧹 清理 Kimi / Codex / Orca 失效索引（会话删除后残留的索引记录）
- ⧉ 一键复制会话完整路径
- 🖥️ 跨平台：Windows、Linux、macOS

## 安装

### 方式一：下载可执行文件（推荐）

从 [GitHub Releases](../../releases) 下载对应平台的可执行文件：

| 平台 | 文件 |
|------|------|
| Windows | `Agent管理器-windows-x64.exe` |
| Linux | `Agent管理器-linux-x64.AppImage` |
| macOS (Apple Silicon) | `Agent管理器-macos-arm64.zip` |

下载后双击运行，或在终端执行：

```bash
# Linux（首次运行需赋予执行权限）
chmod +x Agent管理器-linux-x64.AppImage
./Agent管理器-linux-x64.AppImage

# Windows
Agent管理器-windows-x64.exe

# macOS
unzip Agent管理器-macos-arm64.zip
open "Agent管理器.app"
```

### 方式二：通过 pip 安装

```bash
pip install ai-session-manager
ai-session-manager
# 或
Agent管理器
```

### 方式三：从源码运行

```bash
pip install -e .
python -m ai_session_manager
```

## 使用

启动后会自动打开浏览器访问 `http://127.0.0.1:8080`。

命令行选项：

```bash
Agent管理器 --version          # 显示版本
Agent管理器 --port 8080 --host 127.0.0.1 --no-browser
```

## 开发

### 本地构建

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pyinstaller Agent管理器.spec --clean --noconfirm
```

### 项目结构

```
.
├── src/ai_session_manager/    # Python 包（包名保持英文便于导入）
│   ├── app.py                 # 主逻辑和 HTTP 服务
│   ├── __main__.py            # 命令行入口
│   └── assets/
│       └── index.html         # Web 前端
├── build/                     # PyInstaller 配置
├── pyproject.toml
└── .github/workflows/         # GitHub Actions 自动构建
```

## 自动发布

推送 `v*` 标签即可触发 GitHub Actions 自动构建并发布到 Release：

```bash
git tag v1.1.0
git push origin v1.1.0
```
# Agent 管理器

一个跨平台的 Agent 管理工具，目前支持会话管理，后续会逐步加入 Skill 管理、MCP 管理等功能。

目前支持管理和清理 Claude、Codex、Orca、Kimi、Pi、Hermes 等 AI 客户端产生的会话文件。

## 功能

- 🔍 扫描多 AI 客户端会话文件
- 📊 按工具、目录、时间分组展示
- 🔎 支持标题、路径、CWD 搜索
- 🗑️ 批量选择并删除会话
- 🖥️ 跨平台：Windows、Linux、macOS

## 安装

### 方式一：下载可执行文件（推荐）

从 [GitHub Releases](../../releases) 下载对应平台的可执行文件：

| 平台 | 文件 |
|------|------|
| Windows | `Agent管理器-windows-x64.exe` |
| Linux | `Agent管理器-linux-x64` |
| macOS (Intel) | `Agent管理器-macos-x64.zip` |
| macOS (Apple Silicon) | `Agent管理器-macos-arm64.zip` |

下载后双击运行，或在终端执行：

```bash
# Linux / macOS
chmod +x Agent管理器-linux-x64
./Agent管理器-linux-x64

# Windows
Agent管理器-windows-x64.exe
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
Agent管理器 --port 8080 --host 127.0.0.1 --no-browser
```

## 开发

### 本地构建

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pyinstaller build/Agent管理器.spec --clean --noconfirm
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
git tag v1.0.0
git push origin v1.0.0
```

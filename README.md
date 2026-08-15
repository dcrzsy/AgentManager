# Agent 管理器

一个跨平台的 **AI 代理工作台**（产品化设计），四大模块：

| 模块 | 说明 |
|------|------|
| 💬 会话管理 | 扫描/筛选/清理 12 种 AI 客户端的会话文件 |
| ⚙️ Harness 配置 | 管理 pi 引擎配置文件：查看、安全编辑（自动备份）、恢复、健康检查 |
| 📚 Skill 管理 | 浏览/搜索/新建/编辑/删除 Skill（user/global/project 多源扫描） |
| 🔌 MCP 管理 | MCP 服务器与工具清单：搜索、复制调用名、原始配置查看 |

## 功能

### 💬 会话管理

- 并发扫描 12 个 AI 客户端（Claude Code / Codex / Orca / Kimi Code / Pi / Hermes / Aider / OpenCode / Gemini CLI / Cline / Qwen Code / Windsurf）
- 📅 日期筛选：全部 / 近 7 / 30 / 90 / 180 天 / 自定义起止日期
- 📊 统计面板与按工具分布条形图；深色/浅色主题；搜索高亮；状态记忆
- 🗑️ 回收站机制（删除默认可恢复）+ 关键数据文件保护 + CSRF 防护
- 📄 CSV 导出、文件管理器定位、会话预览（对话视图/原始 JSON）

### ⚙️ Harness 配置管理（~/.pi/agent）

- 配置文件分类浏览（核心配置 / 模型 / 认证与信任 / 搜索 / MCP / 提示词）
- 安全查看：**敏感字段（API Key / Token）自动脱敏**
- 编辑保存：JSON 合法性校验，保存前**自动生成 .bak 时间戳备份**
- 备份管理：列出历史备份、一键恢复（恢复前再备份当前）、删除备份
- 健康检查：JSON 合法性 + 备份数量总览
- 仅允许编辑 ~/.pi 内文件，越权拒绝

### 📚 Skill 管理

- 多源扫描：`~/.pi/agent/skills`（user）、`~/.agents/skills`（global）、`~/.pi/agent/projects-memory/*/skills`（project）
- 卡片式浏览 + 名称/描述搜索 + scope 筛选 + frontmatter 有效性检查
- 查看 SKILL.md 全文（frontmatter + 正文 + 同目录文件）
- 新建（自动生成 frontmatter）/ 编辑（自动备份）/ 删除（进回收站可恢复）

### 🔌 MCP 管理

- 服务器清单（工具数 / hash）+ 工具明细表（名称/描述/参数）
- 全文搜索工具、一键复制工具名或完整调用名
- 原始 mcp-cache.json 查看

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

**时间筛选**：点击「近 30 天」等分段按钮快速切换；「自定义」可指定起止日期；默认加载近 30 天。

**删除流程**：勾选会话 → 「删除选中」→ 确认弹窗中可勾选「永久删除」；默认移入回收站，点击顶部「回收站」统计卡可恢复或彻底清除。

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
git tag v2.0.0
git push origin v2.0.0
```
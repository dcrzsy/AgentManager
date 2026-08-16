#!/usr/bin/env bash
# Agent 管理器 — 管理员权限启动脚本
# 以 root 运行以获得完整扫描（可读取系统级目录 / 其他受限路径）
# 关键：--preserve-env=HOME 保留用户 HOME，避免扫描到 /root 环境
set -e

APP="$HOME/.local/bin/Agent管理器.AppImage"
PORT="${1:-18142}"

if [ ! -f "$APP" ]; then
  echo "❌ 未找到 $APP，请先安装"
  exit 1
fi

echo "🚀 以管理员权限启动 Agent 管理器（端口 $PORT）..."
echo "   （保留 HOME=$HOME，扫描的是您的环境 + 提升系统级读取权限）"

sudo --preserve-env=HOME,PATH,XDG_CONFIG_HOME "$APP" --port "$PORT" --no-browser

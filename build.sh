#!/usr/bin/env bash
# ============================================================================
# MDReader Mac App 编译脚本
# ----------------------------------------------------------------------------
# 用法：
#   ./build.sh            默认：清理旧产物 -> 打包 .app -> 验证启动
#   ./build.sh --run      打包完直接启动 .app（不退出）
#   ./build.sh --no-verify 只打包，不验证启动
#
# 前置条件：
#   conda 环境 tts-app (Python 3.12) 已安装以下依赖：
#     PyQt5 PyQtWebEngine edge-tts pygame markdown pygments
#     pyobjc-framework-AVFoundation pyinstaller
# ============================================================================
set -euo pipefail

# ---------------- 配置（可按需修改） ----------------
APP_NAME="MDReader"
BUNDLE_ID="com.mdreader.app"
ENTRY="main_window.py"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# conda 环境路径（优先绝对路径，找不到则回退到 PATH 里的 python3）
CONDA_ENV="/Users/mac/miniconda3/envs/tts-app/bin/python"
if [[ -x "$CONDA_ENV" ]]; then
    PY="$CONDA_ENV"
else
    PY="$(command -v python3 || command -v python)"
    echo "⚠️  未找到 conda tts-app 环境，回退使用: $PY"
fi

# PyInstaller 缓存目录重定向到项目内，避开 macOS ~/Library 权限限制
export PYINSTALLER_CONFIG_DIR="$SCRIPT_DIR/.pyinstaller-cache"

# ---------------- 解析参数 ----------------
DO_RUN=0
DO_VERIFY=1
for arg in "$@"; do
    case "$arg" in
        --run)       DO_RUN=1 ;;
        --no-verify) DO_VERIFY=0 ;;
        *) echo "未知参数: $arg"; exit 1 ;;
    esac
done

# ---------------- 前置检查 ----------------
echo "==> 检查入口文件与依赖..."
[[ -f "$ENTRY" ]] || { echo "❌ 入口文件 $ENTRY 不存在"; exit 1; }
$PY -c "import PyQt5, PyQt5.QtWebEngineWidgets, edge_tts, pygame, markdown, pygments, AVFoundation, PyInstaller" \
    && echo "✓ 依赖检查通过" \
    || { echo "❌ 依赖缺失，请先安装（见脚本顶部注释）"; exit 1; }

# ---------------- 清理旧产物 ----------------
echo "==> 清理旧构建产物..."
rm -rf build dist "$APP_NAME.spec" "$PYINSTALLER_CONFIG_DIR"

# ---------------- 打包 ----------------
echo "==> 开始打包 .app（可能需要 1-2 分钟）..."
$PY -m PyInstaller \
    --windowed \
    --noconfirm \
    --name "$APP_NAME" \
    --osx-bundle-identifier "$BUNDLE_ID" \
    --hidden-import edge_tts \
    --hidden-import pygame \
    --hidden-import markdown \
    --hidden-import pygments \
    --hidden-import AVFoundation \
    --hidden-import Foundation \
    --hidden-import objc \
    --collect-submodules edge_tts \
    --add-data "assets:assets" \
    "$ENTRY"

APP_PATH="dist/$APP_NAME.app"
[[ -d "$APP_PATH" ]] || { echo "❌ 打包失败，未生成 $APP_PATH"; exit 1; }

echo ""
echo "✅ 打包完成！"
echo "   产物: $SCRIPT_DIR/$APP_PATH"
echo "   大小: $(du -sh "$APP_PATH" | cut -f1)"

# ---------------- 验证启动 ----------------
if [[ "$DO_VERIFY" -eq 1 ]]; then
    echo ""
    echo "==> 验证 .app 能否正常启动..."
    /usr/bin/open "$APP_PATH"
    sleep 5
    if pgrep -f "$APP_PATH/Contents/MacOS/$APP_NAME" >/dev/null; then
        echo "✓ 启动成功，进程运行中"
        if [[ "$DO_RUN" -eq 0 ]]; then
            pkill -f "$APP_PATH/Contents/MacOS/$APP_NAME" 2>/dev/null || true
            echo "  （已自动关闭验证进程；如需保持运行，用 ./build.sh --run）"
        fi
    else
        echo "⚠️  未检测到进程，请双击 $APP_PATH 查看是否报错"
    fi
fi

echo ""
echo "==> 使用方式"
echo "   • 直接双击: $SCRIPT_DIR/$APP_PATH"
echo "   • 安装到系统: 把 .app 拖到「应用程序」文件夹（Finder -> 应用程序）"
echo "   • 首次打开若被 Gatekeeper 拦截: 右键 -> 打开 -> 确认"
echo ""
echo "完成。"

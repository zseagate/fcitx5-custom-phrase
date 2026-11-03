#!/usr/bin/env bash
# 安装「Fcitx5 自定义词组」应用菜单项（无需终端即可启动 Web 界面）
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP="$APPS_DIR/fcitx5-phrase-web.desktop"

mkdir -p "$APPS_DIR"

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Fcitx5 自定义词组
Name[en]=Fcitx5 Custom Phrase
Comment=图形化管理 fcitx5 拼音 / 五笔拼音自定义词组
Comment[en]=Manage fcitx5 custom phrases in a web UI
Exec="$DIR/fcitx5-phrase" web --open
Icon=input-keyboard
Terminal=false
Categories=Settings;Utility;
Keywords=fcitx;input;method;phrase;中文;词组;输入法;
StartupNotify=true
EOF

chmod +x "$DESKTOP"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

echo "已安装菜单项: $DESKTOP"
echo "在应用菜单里搜索「Fcitx5 自定义词组」即可打开。"
echo "也可以直接运行: $DIR/fcitx5-phrase web --open"

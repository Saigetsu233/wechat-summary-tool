#!/usr/bin/env bash
# 在 macOS 上把工具打包成 .app 和 .dmg。
# 用法：bash build_app.sh [版本号]
set -euo pipefail

cd "$(dirname "$0")"

VERSION="${1:-dev}"
APP_NAME="ChatroomDigest"

echo "==> 安装依赖"
python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install -r requirements.txt >/dev/null
python3 -m pip install "pyinstaller>=6,<7" >/dev/null

echo "==> 生成 .icns 图标"
python3 - <<'PY'
from PIL import Image
try:
    img = Image.open("icon.ico").convert("RGBA")
    # ICNS 需要方形大图；取最大帧再放到 1024。
    img = img.resize((1024, 1024), Image.Resampling.LANCZOS)
    img.save("icon.icns")
    print("  icon.icns 已生成")
except Exception as exc:  # 图标失败不阻断打包
    print(f"  跳过图标：{exc}")
PY

ICON_ARGS=()
if [ -f icon.icns ]; then
  ICON_ARGS=(--icon icon.icns)
fi

echo "==> PyInstaller 打包 .app"
python3 -m PyInstaller \
  --noconfirm --clean --windowed \
  --name "$APP_NAME" \
  "${ICON_ARGS[@]}" \
  --add-data "icon.ico:." \
  --collect-all "tkcalendar" \
  --hidden-import "Crypto.Cipher.AES" \
  wechat_gui.py

APP_PATH="dist/${APP_NAME}.app"
if [ ! -d "$APP_PATH" ]; then
  echo "打包失败：未生成 $APP_PATH" >&2
  exit 1
fi

echo "==> 制作 .dmg"
DMG_PATH="dist/${APP_NAME}-${VERSION}-macOS.dmg"
STAGE="$(mktemp -d)"
cp -R "$APP_PATH" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG_PATH"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG_PATH"
rm -rf "$STAGE"

echo ""
echo "完成："
echo "  App: $APP_PATH"
echo "  DMG: $DMG_PATH"

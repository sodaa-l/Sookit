# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec —— Sookit onedir 打包配置
# 用法: uv run pyinstaller packaging/sookit.spec
# 项目根目录绝对路径（onedir 产物输出到项目根/dist）
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), ".."))
SRC = os.path.join(ROOT, "src")
PKG = os.path.join(ROOT, "packaging")
DISTPATH = os.path.join(ROOT, "dist")
WORKPATH = os.path.join(ROOT, "build", "sookit")

a = Analysis(
    [os.path.join(SRC, "sookit", "__main__.py")],
    pathex=[SRC],  # 让 PyInstaller 解析 src 布局下的 sookit 包
    binaries=[],
    datas=[
        # 应用图标：打包态 __file__ 位于 _internal/sookit，assets 须放 _internal/sookit/assets
        (os.path.join(SRC, "sookit", "assets"), "sookit/assets"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Sookit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI 子系统，无黑窗
    disable_windowed_traceback=False,
    icon=os.path.join(PKG, "sookit.ico"),
)

# ---- 独立下载器 updater.exe（复用同一 _internal，共享依赖，避免体积翻倍） ----
a_updater = Analysis(
    [os.path.join(SRC, "sookit", "updater.py")],
    pathex=[SRC],  # 让 PyInstaller 解析 src 布局下的 sookit 包
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz_updater = PYZ(a_updater.pure)

exe_updater = EXE(
    pyz_updater,
    a_updater.scripts,
    [],
    exclude_binaries=True,
    name="updater",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI 子系统，无黑窗
    disable_windowed_traceback=False,
    icon=os.path.join(PKG, "sookit.ico"),
)

coll = COLLECT(
    exe,
    exe_updater,
    a.binaries,
    a.datas,
    a_updater.binaries,
    a_updater.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Sookit",
)

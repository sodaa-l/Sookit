# Sookit

一个 Windows 桌面音视频工具箱

---

## ✨ 功能

- **YouTube 嗅探与下载** — 格式嗅探、视频下载、直播状态监控、频道视频列表
- **视频处理** — 时间裁切、字幕烧录、帧提取、音频替换与提取
- **媒体转换** — 图片+音频合并、图片转视频、M3U8 流下载
- **X Space 下载** — Twitter/X Space 音频下载

## 🚀 安装与运行

### 方式一：安装版（推荐给普通用户）

下载 GitHub Release 中的 `Sookit-Setup-*.exe`，双击安装到 `Program Files\Sookit`，自动创建开始菜单/桌面快捷方式，支持卸载。

### 方式二：源码运行（开发者）

```bash
uv sync          # 安装依赖（含 PyInstaller 打包工具）
uv run sookit    # 启动
```

## 📦 数据与资源位置

| 数据 | 位置 |
|---|---|
| 用户配置 / 任务记录 | `%APPDATA%\Sookit`（config.json、completed_tasks.json） |
| 视频封面缓存 | `%LOCALAPPDATA%\Sookit\covers` |
| 运行日志 | `%LOCALAPPDATA%\Sookit\log\sookit.log` |
| yt-dlp（自动下载） | `%LOCALAPPDATA%\Sookit\tools\yt-dlp` |
| ffmpeg / aria2c | 程序目录 `tools\`（安装版内置） |

## 🔨 打包发布（维护者）

```bash
uv add --dev pyinstaller pillow   # 打包工具
uv run pyinstaller packaging/sookit.spec   # 生成 dist\Sookit\（onedir）
ISCC.exe packaging/installer.iss          # 生成安装程序 dist\Sookit-Setup-*.exe
```

## 📄 许可

本项目基于 **GPL v3** 开源（见 [LICENSE](LICENSE)）

# 决策 26. 安装包下载移交 updater.exe + sha256 校验（2026-09-05）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 26」一一对应）

**架构原则：Sookit 主程序只负责检查更新，下载全部交给独立 updater.exe**（非提权，普通权限启动）：

- **子命令分发**（`sookit/updater.py`）：`--app-setup <tag> <result_path>`（新增，非提权）与 `--ytdlp-updater-gui`（原有，提权）互不影响。下载组件写只读程序目录才需要提权；app 安装包下载到 `%APPDATA%\Sookit\updates\` 用户目录无需提权，**不弹 UAC**。
- **`_CancellableWorker` 基类**：取消标志 + aria2c 进程记录 + `force_terminate` 兜底从 `_DownloadWorker` 抽出，`_AppSetupWorker`（调 `download_installer`）与 yt-dlp worker 共用。
- **通信协议**：`launch_app_setup_downloader(tag, timeout=3600)` 生成结果路径（**%APPDATA%**，与 yt-dlp 任务的程序目录不同——非提权写不进程序目录）→ Popen 启动 → 轮询「结果 JSON + `_updater_process_alive` + 绝对超时」。结果结构 `{"task":"app_setup","ok","status":"ok|cancelled|failed","error","path"}`。
- **skip-if-exists + sha256 校验**（`download_installer`）：
  - CI 打包后生成 `Sookit-Setup-<ver>.exe.sha256` 资产（`Get-FileHash`，格式 `"<hex>  <文件名>"` 与 sha256sum 兼容）随 release 上传；
  - 下载前拉取期望哈希；**拉取失败降级为不校验**（避免哈希文件网络抖动让更新整体不可用，仅 warning 日志）；
  - dest 已存在：哈希匹配才复用（skip-if-exists），不匹配删除重下——**封死"同 tag 重发旧内容"漏洞**；哈希不可得时降级为 size>0 判定；
  - 新下载完成：哈希不匹配 → 删除 dest + 抛"校验失败"。文件名自带版本号（`Sookit-Setup-<ver>.exe`），天然与旧版本隔离；`.part` 原子替换保证 dest 存在即完整。
- **旧版本清理**：`_AppSetupWorker` 下载成功后清 `updates/` 下其他版本安装包（`_cleanup_old_setups`，尽力而为失败静默）。磁盘最多保留最新一个安装包；**Sookit 不做启动扫描**——"主程序中途退出后接上"仅靠 skip-if-exists（下次点下载秒回）。
- **主窗口**（`_do_update` 重写）：`is_updater_available()` 为 False（**源码运行态）→ 静默 return 无任何提示**；下载中重复点击提示"正在下载中"；下载条 title=`正在下载 <版本号>`（closable=False）；后台轮询结果回显——ok → 「安装器已就绪」+ [运行安装器]/[打开所在文件夹]（此刻才 UAC）；cancelled → 中性「已取消」；failed → error 条。**主程序下载线程/进度回写逻辑整体删除**（`_update_infobar.setContent` 等不再存在）。
- **行为变化**：下载进度从主窗口 InfoBar 移到 updater.exe 独立小窗（进度条+取消按钮）；下载中关闭 Sookit 下载不中断；UAC 只在运行安装器时出现。
- 坑：进度文本抓百分比必须 anchor `%` 符号（`(\d+(?:\.\d+)?)\s*%`）——泛数字匹配会把安装器 label 里的版本号 260905.2 误当进度。

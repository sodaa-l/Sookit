# 决策 16. yt-dlp 更新检查与取消的完善（2026-08）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 16」一一对应）

- **检查态/下载态分离**：`_update_ytdlp(skip_check=False)`——已安装（tools）先「检查中」查新版本，确需更新才切「更新中」提权下载；未安装直接「安装中」；自动更新确认路径 `skip_check=True` 直达「更新中」。
- **取消中性化**：用户主动取消不算失败——`_comp_state` 对 `cancelled` 显示「已取消」，全取消走中性 info「已取消」提示（非红色错误条）；`any_failed` 判定排除 cancelled（混合场景取消组件显示「已取消」、真失败仍走错误条）。
- **数据源统一**：自动检查（进设置页触发，每次启动一次）从 PyPI JSON API 改为 `get_ytdlp_latest_version()`（GitHub releases 重定向，与手动检查/更新同源，消除两源发布时序差的矛盾）。
- **「有新版本」Dialog → 常驻 InfoBar**：非阻塞 warning 条（含「前往设置」按钮，与「依赖缺失」条形式一致），引用 `_yt_new_version_bar` 防重复堆积，更新成功后 `close_yt_new_version_bar()` 自动关闭。
- **`launch_ytdlp_updater` 轮询重写**：终止条件从固定 300s 改为「结果文件出现或 updater.exe 进程消失」（`tasklist` 检测，普通权限可见提权进程；输出 GBK 用 `errors="ignore"` 解码）——慢网络下载不误报失败、强杀/崩溃立即判失败（"下载器已退出但未返回结果"）；`timeout` 语义改为防挂死绝对上限（默认 30 分钟）。
- **坏 JSON/`.aria2` 残留修复**：`json.loads` 失败分支补 unlink（此前只修了读取成功分支）；`_download_file` 取消/失败清理补删 `.aria2` 控制文件（此前只删 `.part`）。

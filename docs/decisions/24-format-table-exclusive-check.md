# 决策 24. 嗅探格式表：同类型互斥勾选 + 预勾选机制 + 默认合并下载（2026-09-05）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 24」一一对应）

围绕 `youtube_page.py` 格式表格的勾选与下载行为重构：

- **类型文案**：`仅视频`→`视频流`、`仅音频`→`音频流`（`视频+音频`/`其他`不变）。常量定义在 `functions.FormatType`，解析（`sniff_youtube` 的 vcodec/acodec 判定）、quality 生成、表格填充、下载分流等处全部走常量或同步字符串，改文案需全库同步。
- **同类型互斥**（`_on_format_item_changed`）：勾选"视频流/音频流/视频+音频"任一项时自动反勾同类型其他已勾行；"其他"类型不限制。类型判定靠 `self._formats_data[row]['type']`（不依赖行相邻）。**填充阶段必须 `_format_updating` 守卫**——`setItem`/`setCheckState` 会触发 `itemChanged`，否则填充期互斥逻辑误触发；反勾只产生 Unchecked 变更，处理器仅响应 Checked，天然防递归（守卫仍保留双保险）。
- **预勾选机制**：填充前按"每类型首次出现的行号 = 该类型最佳项"（排序由 `_format_sort_key` 保证：视频+音频→音频流→视频流→其他，各自分辨率/码率降序）建 `first_row` 字典。有"视频+音频"→ 勾首行；无 → 勾最佳"视频流"+"音频流"各一（缺一类只勾另一类）。修复了旧逻辑 `'1080' in quality` 条件在 1080p30/60fps 并存时预勾 2 行违反互斥的 bug。
- **默认合并下载**：删除"勾视频流+音频流时弹 TeachingTip 问合并/分开"机制，`do_download` 收敛为"收集勾选 → `'+'` 拼接交 yt-dlp"（`+` 语义即 ffmpeg 合并为一个有声视频）。分开下载视频流+音频流的替代路径：分两次各勾一个下载。
- **下载按钮门控**：`download_btn` 初始 disabled；`do_sniff` 开始时重置 disabled；仅 `_on_sniff_done` 有格式填充成功后 enable。嗅探失败/超时/停止/空格式均保持禁用。
- 验证方式：`QT_QPA_PLATFORM=offscreen` 直接调用 `_on_sniff_done` 喂伪造 info dict，断言预勾结果与互斥联动（7 场景）。

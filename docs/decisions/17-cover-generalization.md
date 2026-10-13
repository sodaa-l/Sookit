# 决策 17. 封面机制通用化（2026-09-01）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 17」一一对应）

嗅探页封面从 YouTube 专属改为全站点通用，**YouTube 路径保持现状逐字节不变（零退化）**：

- **分流依据**：`sniff_youtube` 按 `(info.get('extractor') or '').lower().startswith('youtube')` 分流——不用 `extract_youtube_id(url)`（music.youtube.com 提取不到 ID 但 extractor 仍是 youtube）。YouTube 走 `build_thumbnails(id)` 硬编码 5 档；其余走 `normalize_thumbnails(info)`。返回 dict 新增 `extractor` 键供页面判断。
- **`normalize_thumbnails(info)`**（youtube_utils.py）：完整 URL 去重（**勿按 netloc+path 归并**——X 等站点尺寸靠 query `?name=` 区分，归并会毁掉档位；YouTube 的 `?sqp=` 变体场景已走硬编码表不进此函数）→ preference 降序 stable sort（None 最低）→ 顶层 `thumbnail` 字段（extractor 认定的最佳 URL）定位最佳项移到首位 → **丢弃 width/height**（通用路径无可靠宽高来源，实测 X 的标注 25 档仅 7 档正确）。
- **X(twitter) 特判**：仅输出 `id=='medium'` 单档并保留其标注宽高（实测 5 条视频 25 档，medium 是唯一 5/5 标注正确的档；thumb/small 系统性偏小、large/orig 系统性虚标；同视频各档 URL 下载结果完全相同——X 忽略 `?name=` 参数）。id 置空使文案只显示分辨率（如 `1200x675`）。
- **下拉文案三分支**（`_populate_cover_combo`）：有宽高+id 非纯数字 `1280x720 (maxresdefault)`（YouTube）；有宽高+无有效 id `1200x675`；无宽高 `最佳分辨率`/`备选分辨率 N`。空列表占位 `无可用封面`。
- **下载封面**：URL 取 `cover_combo.currentData()`（不按索引重建列表——通用列表项数与 build_thumbnails 不同会索引错位）；扩展名按 URL path 推断（白名单 .jpg/.jpeg/.png/.webp 默认 .jpg）；完成后 QPixmap 实测分辨率写日志。
- **入队**：`metadata['cover_url']` 优先 `_sniff_thumbs[0]['url']`（非 YouTube 队列卡片因此可显示封面），YouTube 无列表时兜底 build_thumbnails；`_sniff_video_id` 仅 YouTube 赋值（防 BV 号污染文件名/入队/失败兜底三条消费路径）。
- **顺带修复的隐性 bug**：旧代码非 YouTube 嗅探成功后 `build_thumbnails(BV号)` 拼出无效 i.ytimg.com URL，下拉框 5 档全 404。

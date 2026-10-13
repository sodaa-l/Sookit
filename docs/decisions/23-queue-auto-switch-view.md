# 决策 23. 任务队列进入页自动跳转（2026-09-04）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 23」一一对应）

点击导航"任务队列"时的视图智能定位，两个独立判定点：

- **进入判定**（`MainWindow._on_stack_changed`）：挂 `stackedWidget.currentChanged`——该信号**只在真正切换页面时发出**（QStackedWidget 已是当前页时重复 `setCurrentWidget` 不触发），天然防重复判定。槽内判断目标页是 queue_page 且 `get_active_tasks()` 为空（等待/运行/暂停/**失败**均为活跃任务）且 `get_completed_tasks()` 非空 → `segment_widget.setCurrentItem("completed")` 落"已完成"；两者皆空不跳转（避免空白已完成页）；有活跃任务不干预（segment 保持上次状态）。
- **新任务回落**（`QueuePage._on_task_added` 开头）：`task_added` 信号强制 `setCurrentItem("active")`——**仅添加任务这一个事件做判定**，任务更新/完成/移除不改变视图；无视页面可见性与 segment 当前状态，保证之后任何时候进入队列页看到的都是"进行中"（用户在别的页面加任务时 segment 可能停在"已完成"）。
- **为什么不挂页面 `showEvent`**：触发面广（任何隐藏→显示都来一次），需额外守卫；`currentChanged` 语义精确命中"导航切换到该页"。
- **测试坑**：qfluentwidgets Pivot/SegmentedWidget 的 `currentItem()` 返回的是**控件对象**，读取路由键字符串要用 `currentRouteKey()`。
- 验证方式：`QT_QPA_PLATFORM=offscreen` + `VIDEOTOOLBOX_DATA_DIR` 指向临时目录预置 `completed_tasks.json`，模拟 `switchTo()` 切页与 `task_added` 信号派发，5 场景断言（跳转/回跳/回落/不干预/皆空不跳）。

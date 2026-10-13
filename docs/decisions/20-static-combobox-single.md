# 决策 20. StaticComboBox 单项静态化（2026-09-01）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 20」一一对应）

`widgets/combo_box.py`：qfluentwidgets ComboBox 子类，`count() <= 1` 时 `mouseReleaseEvent` 吞点击（不弹菜单）且 `paintEvent` 跳过箭头绘制（纯静态显示），≥2 项与原生一致；count 动态判断，档位数变化无需刷新。按压/hover 透明度由 `ComboBoxBase.eventFilter` 在 mouseReleaseEvent 之前维护，吞事件不影响视觉状态。嗅探页 cover_combo 已换用（X/Bilibili 单档、"无可用封面"占位均为静态）。

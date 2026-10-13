# 决策 14. 导航栏展开宽度

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 14」一一对应）

`MainWindow.__init__` 中 `self.navigationInterface.panel.setExpandWidth(250)`（qfluentwidgets 默认 322px 偏宽）。该 API 会同步更新 `NavigationWidget.EXPAND_WIDTH`（条目宽度）；折叠态固定 48px 不可调；`setMinimumExpandWidth`（默认 1008px）控制窗口多宽时面板自动保持展开。

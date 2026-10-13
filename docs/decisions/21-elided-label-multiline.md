# 决策 21. ElidedLabel 多行省略标签与卡片高度自适应（2026-09-01/02）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 21」一一对应）

已完成页卡片标题用 `widgets/elided_label.py` 的 `ElidedLabel`（YouTube 风格，最多两行、超宽末尾加 …）：

- **截断计算**：QTextLayout（WordWrap 模式，与 QLabel 渲染一致）定位截断点 + `fm.elidedText` 给末行加 …，行间插 `\n`。`textStart()` 返回 **UTF-16 偏移**，含 emoji 标题需换算成 Python 码点偏移（`_u16_to_py`）。
- **高度按实际行数自适应（1~2 行）**，单行标题不预留空行。卡片 `resizeEvent` 锁总高，根治 AdaptiveFlowLayout 不查 heightForWidth 导致的标题叠封面（`_doLayout` 只用 `sizeHint().height()`）。
- **卡片 `heightForWidth` 必须用 `count_lines(task.title, title_label.font(), content_w, 2)` 按卡片当前宽度实时算行数**——勿读标签缓存行数（标签宽度更新滞后于卡片，窗口缩放跨单行/两行临界点时拿到旧行数 → 高度滞后一行）。
- **字体度量两个坑**：① `QWidget.setFont` 非虚函数，Python 重写不被调用，字体须经构造参数传入、运行时变化靠 `changeEvent(FontChange)` 重算；② pre-app 构造的类属性 QFont（全局度量）与控件上下文度量可差 1px，行数与行高计算一律用 `title_label.font()` 同源度量。
- 已知边界：超长无空格单词/纯 emoji 串在 WordWrap 下不可断行 → 不省略、横向裁切（与 QLabel 渲染一致，可接受）。

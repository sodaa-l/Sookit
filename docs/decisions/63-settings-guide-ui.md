# 决策 63. 设置页 Chrome 扩展引导区改版

## 目标

设置页「Chrome 扩展」卡片展开后的安装引导原先有三个问题：步骤 1 的「复制地址」按钮夹在正文行里、与任何边都不齐；步骤 3 的路径区是一块 1px 极淡边框的 `SimpleCardWidget`，固定 820px 宽、右侧大片空白，前缀「扩展目录：」和路径挤在同一个标签里被整体省略；底部说明是 11px 缩进 38px 的孤零零脚注，头部与步骤之间也没有分隔。改版目标是让信息层级、对齐和动作位置一致，不改变任何复制/打开/诊断行为。

## 结构

```text
CardWidget(margins 0,0,0,0; spacing 0)
├─ browser_setup_header (高 70，与 qfw.SettingCard 一致)
│  └─ 行(margins 16,0,15,0): _ChromeLogo 16×16 + 16px + 文本块(14px 标题 + 11px 说明)
│                          + 16px + stretch + PrimaryPushButton「安装引导」
└─ browser_guide_container (margins 16,0,16,16; 折叠时整体隐藏)
   ├─ 14px 空隙
   ├─ _GuideHLine (1px 分隔线)
   ├─ 16px 空隙
   └─ browser_guide_panel (spacing 16)
      ├─ 步骤 1..4: 行 = [_GuideStepNumber 28×28] + 12px + step_content
      │            step_content(spacing 6) = 加粗标题 + 11px 说明 + 值条行
      ├─ 值条行: [值条 chip][stretch]
      └─ 说明行: margins(40,4,0,0) + INFO 图标 14px + 11px 文案
```

- 步骤 1 的说明改为「在 Chrome 地址栏输入下方地址」，`chrome://extensions/` 移入值条，右侧紧跟「复制地址」按钮。
- 步骤 3 的值条 =「扩展目录」前缀（或「未找到扩展目录」）+ 路径 + 「复制路径」「打开文件夹」；前缀与路径拆成两个标签，路径标签仍带 `contentLabel` 字号与主题色，超长时由 `ElidedLabel` 省略、tooltip 保留全文。
- 说明行左右移动为 40px 对齐「内容列左缘」（28px 序号 + 12px 间距），图标从无到有，让脚注看起来属于步骤列而不是独立段落。

## 头部对齐「应用主题」卡片

- 标题/说明不再用 15px/600 自定义 QSS 的 `StrongBodyLabel`，改用手写行与 `qfw.SettingCard` 同一套排版：`QLabel` 套 `FluentStyleSheet.SETTING_CARD`（标题 14px 常规、`contentLabel` 11px 灰），行高固定 70，左侧 16px 图标 + 16px 间距，文本块在行内垂直居中。
- 说明标签用 `_guide_text_label(..., word_wrap=False)`：`SettingCard.contentLabel` 是不换行的纯 `QLabel`，这里若开换行，两行文本会被 70px 行高裁掉半行。
- 图标换成 Chrome 标志，用 `_ChromeLogo` 手绘 16×16 四色圆标（红 `#EA4335` / 绿 `#34A853` / 黄 `#FBBC05` 三扇区 + 白色内环 + 蓝心 `#4285F4`）：仓库与 qfluentwidgets 都没有 Chrome 官方图标资源，手绘可避免新增第三方图片资产。

## 自绘部件

全部放在 `settings_page.py` 模块级，避免污染其它页面：

- `_GuideHLine`：`fixedHeight=1` 自绘分隔线，亮 `rgba(0,0,0,26)` / 暗 `rgba(255,255,255,38)`，订阅 `qconfig.themeChangedFinished` 重绘。
- `_GuideValueChip`：`fixedHeight=32`、圆角 6 自绘底与 1px 边框（亮 `rgba(0,0,0,9)` + 边框 `rgba(0,0,0,20)`；暗 `rgba(255,255,255,18)` + 边框 `rgba(255,255,255,30)`），同样订阅主题信号。
- `_GuideStepNumber(qfw.CaptionLabel)`：28×28、`font-size 12px / 600`、圆角 14，底色是主题色 10%（亮）/ 20%（暗）透明度，数字本身用主题色；订阅 `qconfig.themeColorChanged` 重新取色，主题色改了不用重建控件。
- `_GuideValueLabel(ElidedLabel)`：重写 `sizeHint()`（宽 = `min(fm.horizontalAdvance(text), 640)`、高取父类）与 `minimumSizeHint()`（宽 0），否则 `ElidedLabel` 的 `sizeHint` 会低估/高估，值条要么被拉满整行、要么撑爆面板。
- `_guide_value_button(icon, text, tooltip)`：`TransparentPushButton` + `setIconSize(14,14)` + `fixedHeight 28`，图标 COPY / FOLDER。
- `_guide_text_label` / `_guide_detail_label`：11px 正文，前者套 `FluentStyleSheet.SETTING_CARD`（沿用 `contentLabel` 的主题色），后者用 `setCustomStyleSheet` 显式写亮/暗两套 11px 样式。
- `_guide_observe(signal, widget, …)`：弱引用 + `RuntimeError` 兜底，防止页面销毁后主题信号回调打到野指针。

## 布局坑

- **最大宽度会沿布局向上传染**：值条 `setMaximumWidth(820)` 若直接 `row.addLayout(content)` 嵌套进步骤行，内容列最大宽度就变 820，嵌套 HBox 里唯一的 widget 会被居中（实测序号被推到 x≈84、标题 x≈208）。必须把步骤内容包成 `step_content` 控件再 `addWidget(step_content, 1)`，让上限留在值条自己的单元格里。
- 值条本身按内容自适应：`sizePolicy=Preferred` + 行内 `[chip][stretch]`，`maximumWidth=820` 只在超长路径时生效；这样「复制路径」按钮紧贴路径文本，而不是被甩到卡片右侧。
- `_GuideValueLabel` 的 `minimumSizeHint` 必须为 0 宽，否则 720px 窄窗口下值条不肯收缩、控件会溢出面板。

## 已否决

- 保留 `qfw.SimpleCardWidget` 路径框、只调边距（1px 极淡边框在暗色下几乎不可见，且前缀无法与路径分开省略）。
- 值条撑满整卡宽度（右侧大片空白正是原来的问题；按钮离文案太远，用户明确否掉）。
- 给值条内按钮 `setStyleSheet("padding: …")`（会覆盖 qfw 自绘，文字出现划痕/错乱）。
- 用动态 QSS 改序号配色（`setCustomStyleSheet` 对裸控件需要先注册 `FluentStyleSheet` 才生效，自绘圆底更直观且能随主题色变化）。

## 验证与代价

- 离屏渲染（1200 宽，挂 `msyh.ttc`）亮/暗两套：序号、标题、值条左缘统一在 x=84；卡片高 421→420；超长路径时值条到 820 上限、末尾「…」、两个按钮紧随。
- `tests/test_browser_settings.py` 两个引导区测试同步改写：步骤 1 说明文案、值条内按钮不得溢出、路径与「复制路径」间距 0..12、1600 宽下路径组仍 ≥700、折叠时 container 与 panel 一起隐藏；另加 `test_setup_card_header_matches_setting_card_typography` 断言标题 14px 非粗体、说明 11px、图标 16×16、图标与标题的 x 坐标和「应用主题」卡片一致、头部行高 70。
- 已知代价：新增 5 个模块级自绘部件（约 150 行），主题切换/主题色变化依赖信号重绘；说明行文案改为 `_guide_text_label` 后仍保留 `contentLabel` 的 11px 主题色，测试以样式表内容断言。

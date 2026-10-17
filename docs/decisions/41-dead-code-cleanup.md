# 决策 41. 清理死代码：未使用 import / 零引用函数 / 门面再导出（2026-10-17）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 41」一一对应）

## 背景

排查「僵尸依赖」时发现依赖声明本身是干净的（6 个直接依赖 + pyinstaller dev 全部有真实引用，`pywin32` 是 `qframelesswindow` 的硬依赖删不得，`pyobjc-*`/`macholib` 是 PyInstaller 的 darwin 条件依赖），真正堆积的是**代码侧死引用**：11 个文件共 113 处绑定后从未使用的 import，以及若干「全仓除定义外零出现」的函数与方法。

其中 `core/functions.py` 的「再导出」区块被过去当作向下兼容层，与实际不符：本项目是 PyInstaller 冻结的 GUI（`console=False`，用户拿到 exe，机器上没有 Python 环境）、不上 PyPI（`release.yml` 只挂安装包资产）、也没有测试套件——不存在需要兼容的外部调用方。用 git 追根提交 `cfcadbd`（2026-08-05，此时已是 src 包结构）核实：19 个死再导出在根提交时只有 1 个真被使用（`download_ytdlp_bundle`），其余仅在本文件「再导出」行出现；而这唯一消费者后来随下载逻辑迁入 `updater.exe`（决策 10）也死了。

## 结论

- **`core/functions.py` 不是兼容层**，它的角色是：`Functions` 类（功能中枢）+ 少量页面仍在使用的聚合入口。新增代码**直接从源模块 import**（如 `from sookit.core.ffmpeg_utils import get_video_duration`），不要往 `functions.py` 加再导出。
- 判断一个 import 是否可删：**本文件内是否用到** + **是否有其他模块从该处 import 且真的使用**，两者皆否才删。「只在 import 行出现、从未调用」也算死。
- 判断一个函数/方法是否可删：全仓整词搜索仅命中定义本身，且无 `getattr`/`hasattr`/字符串形式的动态引用。**Qt 框架回调（`dragEnterEvent` / `dropEvent` / `changeEvent` / `heightForWidth` 等）例外**——它们由框架调用，静态扫描看不见，不得按「零引用」删除。
- 包级门面（`pages/__init__.py` 的导出 + `__all__`）、惰性依赖探测（`gif_utils.py` 的 `import numpy  # noqa: F401`）属于正常写法，不清理。

## 执行记录

分三批、每批独立 commit（分支 `chore/remove-dead-code`），每批提交前跑语法编译 + 全模块 import + 受影响功能的冒烟与端到端验证：

| 批次 | 内容 | commit |
| --- | --- | --- |
| 低风险 | 54 处未使用 import（`main_window.py` 占 35 处） | `b8c313d` |
| 中风险 | 8 个零引用函数/方法 + `YTDLP_AVAILABLE` 死常量 + 因此失去引用的 `Qt` 导入 | `d32e080` |
| 高风险 | 19 个无人消费的门面再导出 | `e065e0a` |

删除后重跑同一判定：`functions.py` 剩余 42 个 import 的「本文件没用且无人从 functions import」数量为 0。

## 注意

本次清理只删「确认无人引用」的代码，**不改任何逻辑**。被删再导出的函数本体全部保留在源模块（`ffmpeg_utils` / `config` / `ytdlp_utils` / `app_update` / `youtube_utils`），只是不再从 `sookit.core.functions` 转发；仓库外若曾有脚本按旧路径 import 这些名字会失败，改为从源模块 import 即可（仓库内已无此类调用）。

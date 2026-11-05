# 决策 70. 浏览器桥接一键修复

## 问题与交互

源码开发注册在 HKCU，安装器注册在 HKLM。切换到安装版后，旧 HKCU 注册会遮蔽安装版。原设置页仅显示路径诊断和“重新检查”，无法直接处理。

注册异常且当前组件文件完整时，设置页显示“浏览器桥接需要修复”和“修复”按钮，隐藏冗长诊断（保留在状态 tooltip）。点击直接执行，无额外确认；完成后重新检查，只有检查通过才提示“修复成功”，否则提示失败并允许重试。文件缺失或清单无效时不提供注册修复，提示尝试重新安装。

## 修复边界

- 复用源码/打包路径解析及组件校验，写入前重新检查扩展文件、当前 host 清单及程序；源码还要求有效的 source-launch.json。修复过程不执行源码启动命令。
- 仅将当前布局的绝对清单路径写入 HKCU\Software\Google\Chrome\NativeMessagingHosts\com.sookit.browser 的默认 REG_SZ，不提权、不改 HKLM 或安装目录，不下载、重建 host 或安装 Chrome 扩展。
- HKCU\Software\Google 在 Windows 32/64 位进程间共享，因此只写一次默认值，不对诊断中的重复视图逐条删除。依据：[Microsoft 共享注册表键文档](https://learn.microsoft.com/en-us/windows/win32/winprog64/shared-registry-keys)。
- 安装器的 HKLM 注册保持原行为。修复后的 HKCU 覆盖仅作用于当前用户；若应用目录变化，需在新位置再次修复。当前卸载器只清理 HKLM，因此此 HKCU 指针会保留，目标删除后将成为失效注册；后续安装可再次修复，不扩展本次卸载清理范围。
- 写入失败不会报告成功；检测与复查始终只读。注册正常不代表扩展已经在 Chrome 加载、启用或连接。

## 验证

运行 `uv run --no-sync python -B -X utf8 -m unittest discover -s tests -p test_browser_settings.py -v`。隔离注册表覆盖源码/安装布局、旧 HKCU 遮蔽 HKLM、缺失注册、无效/缺失文件拒绝写入和权限失败；UI 测试覆盖点击直接修复、成功后隐藏入口、失败恢复按钮、复查失败不误报成功。测试不修改本机注册表。

本轮定向 29 项通过；完整离线回归在临时数据、日志和封面目录中 157 项通过。完整回归中的旧浏览器分发测试会读取实际浏览器调用开关，本机该开关为关闭，直接运行会有 1 项失败；使用临时 VIDEOTOOLBOX_DATA_DIR 并重置进程内配置缓存后通过，未修改用户实际开关。

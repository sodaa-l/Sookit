# 决策 8. 每个 yt-dlp 任务独立 workspace（重要）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 8」一一对应）

仅 yt-dlp/aria2c 下载任务使用独立临时工作目录：

```
用户目标目录/
└── ._sookit_tmp_<ULID>/     # 所有 .part/.aria2/.ytdl/.f<ID> 临时文件都在这里
```

- ULID 用 `_generate_ulid()`（26 字符 Crockford base32，与标题无关）。
- **生命周期由 Task 管理**（`task.workspace`），Worker 只使用 `task.workspace`。
- 下载完成 → `_finalize_workspace` 移动 `output_files` 到用户目录（同卷原子 rename），全部成功才删 workspace。
- 取消/关闭/失败 → 删整个 workspace（放弃续传）；**暂停保留**。
- 启动时用 `active_workspaces.json` registry 清理异常退出残留（只清自己登记的，不全盘扫描）。
- **创建顺序保证**：先生成路径 → `_register_workspace`（原子保存）→ `os.makedirs`，宁多记不漏清。

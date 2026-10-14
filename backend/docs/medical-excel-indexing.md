# 病历 Excel 处理

Windows 启动后端请使用 `uv run python -m app.main`，或：

```powershell
uv run uvicorn app.main:app --loop app.core.event_loop:create_event_loop --host 0.0.0.0 --port 8000
```

本机 Windows/Python 环境下，Uvicorn 重载默认选择的 Selector 事件循环会使 asyncpg 写入停滞。相同 200 条病历在 Proactor 下可写入；数据库等待事件是 ClientRead，无锁阻塞。启动入口强制使用 Proactor，错误的启动方式会立即报出修正命令。

切片原则：

- 一行一条原始记录，PostgreSQL 保留完整字段；不能把多个患者合并成行组向量。
- 短记录保留一个向量。长记录在字段、句子边界拆分，默认每片最多 400 字符，片内重叠 40 字符，不跨行重叠。
- 子片重复简短的病历号、患者编号、就诊号或住院号，并携带 sheet 名、数据行序号和子片序号，方便回溯。
- 默认保留空诊断、空用药等空值，不继承上一患者的信息。确有分组继承需求时，按文件类型显式设置 `forward_fill_columns` 白名单。
- 精确病历号查询、日期筛选、计数走结构化数据库；症状、病史描述等走语义检索，再回查完整记录。
- 当前本地 bge-small-zh-v1.5 的 max_seq_length 为 512 tokens。400 字符是第一层控制；入库前还会自动使用当前模型 tokenizer 校验并拆分超限片段，避免混合代码/英文内容被静默截断。

`per_type_chunk_config` 内的 xlsx/xls 配置支持 `record_chunk_size`、`record_chunk_overlap`、`record_context_columns` 和 `forward_fill_columns`。数据行序号 `row_number` 为 0-based 的非空数据记录序号，不是 Excel 物理行号。

Windows 当前采用单进程、禁用 Uvicorn 内置热重载：重载传递的监听套接字可能在 Proactor 下产生 WinError 87。修改后端代码后手动重启。

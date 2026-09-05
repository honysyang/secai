# DEBT_LEDGER — 承诺-现状核对表

- 来源：`/home/kali/task/doc/9_5_SECAI_优秀Harness改造计划_v1.md §1.2` H1-H13
- 状态标注：✅ 已结清 / 🔄 进行中 / ⬜ 未开始
- 更新：9_5 终极执行计划 v4（R0-R6）

| 编号 | 债务 | 状态 | 负责批次 |
|---|---|---|---|
| H1 | app/main.py 闭包状态机，不可单测 | ✅ | R1（→harness/runner ExecutorLoop，main.py 506 行） |
| H2 | demo_tools.py 1311 行巨石 | ✅ | R1（→tools/domains/ 11 域文件，shim 180 行） |
| H3 | 动态上下文每轮全量注入 | ⬜ | R2 |
| H4 | zero_gain_events 硬编码 0 | ⬜ | R2 |
| H5 | sub_*.sqlite 残留 | ⬜ | R2 |
| H6 | Executor 纪律 9 条未收口 | ⬜ | R2 |
| H7 | 破局链部分未落地 | ⬜ | R2 |
| H8 | 无 pyproject/ruff/CI | 🔄 | R0 补装 + R5 gate |
| H9 | 执行无沙箱 | ⬜ | R3 |
| H10 | 无 HITL 审批 | ⬜ | R3 |
| H11 | 文档-代码漂移无核对机制 | ✅ | R0 建表 |
| H12 | CTF 假设硬编码散落 | ⬜ | R4 |
| H13 | 仓库卫生（gitignore/data） | ✅ | M0 已做 |

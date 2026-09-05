# DEBT_LEDGER — 承诺-现状核对表

- 来源：`/home/kali/task/doc/9_5_SECAI_优秀Harness改造计划_v1.md §1.2` H1-H13
- 状态标注：✅ 已结清 / 🔄 进行中 / ⬜ 未开始
- 更新：9_5 终极执行计划 v4（R0-R6）

| 编号 | 债务 | 状态 | 负责批次 |
|---|---|---|---|
| H1 | app/main.py 闭包状态机，不可单测 | ✅ | R1（→harness/runner ExecutorLoop，main.py 506 行） |
| H2 | demo_tools.py 1311 行巨石 | ✅ | R1（→tools/domains/ 11 域文件，shim 180 行） |
| H3 | 动态上下文每轮全量注入 | ✅ | R2（charter/plan 版本化 + field_notes 仅首轮，第 10 轮 ≤40%） |
| H4 | zero_gain_events 硬编码 0 | ✅ | R2（真实统计汇总，见 cost_report/dashboard） |
| H5 | sub_*.sqlite 残留 | ✅ | R2（收尾句柄兜底 close + glob 物理删除） |
| H6 | Executor 纪律 9 条未收口 | ✅ | R2（9→6 语义去重，约束不删） |
| H7 | 破局链部分未落地 | ✅ | R2（惰性点 ±5 步回放自动导出 + 前提证伪级联回收） |
| H8 | 无 pyproject/ruff/CI | ✅ | R0 补装 + R5 gate.sh（ruff+unit+replay 无网全绿） |
| H9 | 执行无沙箱 | ✅ | R3（sandbox/ bwrap fail-closed，rm -rf / 真实拦截验证） |
| H10 | 无 HITL 审批 | ✅ | R3（ApprovalGate T1 自动/T2-T3 审批/T4 禁止，记录入事件总线+events 表） |
| H11 | 文档-代码漂移无核对机制 | ✅ | R0 建表 |
| H12 | CTF 假设硬编码散落 | ✅ | R4（提交/终局逻辑收敛 profiles/ctf_legacy，get_platform_client 单例收口，主循环 grep 无 BENCHMARK_TOKEN） |
| H13 | 仓库卫生（gitignore/data） | ✅ | M0 已做 |

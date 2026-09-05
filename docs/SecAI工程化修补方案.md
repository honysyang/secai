# SecAI 工程化修补方案（代码质量 / 工程维护 / 架构设计）

> 基线：commit 84c5fba。P0 缓存防线（hash 断言、append-only 压缩）已落地，
> 本文档不再重复。聚焦下一轮：**让代码配得上它的架构意图**。
> 分级：P0 = 会引发线上错误/资损；P1 = 阻碍迭代；P2 = 卫生。
> 原则沿用：**机械保证 > 约定俗成；能删的代码 > 能改的代码 > 新增的代码。**

---

## 第一部分 问题清单（按维度）

### A. 架构设计

| # | 级别 | 问题 | 证据 |
|---|---|---|---|
| A1 | P0 | **双压缩路径并存**：主循环 `compact_if_needed` 与卡壳路径 `compact_session` 两个入口，虽复用同一摘要指令，但触发条件、截断行为、日志口径各自演化，必然漂移 | main.py:708 / main.py:826；runtime/stuck.py 与 core/context_manager.py 两个 339~475 行模块职责重叠 |
| A2 | P0 | **模块级可变单例 Agent 跨题共享**：`strategist_agent.model = ...` / `reporter_agent.model = ...` 直接改全局实例；3 题并发共用同一 Reporter，模型灾备切换互相踩踏；子任务 `build_executor` 走工厂、外层 agent 走单例，两种范式混用 | app/main.py:941-942；core/agents_def.py:74,319 |
| A3 | P1 | **main.py 巨石（1446 行）**：调度循环、单题生命周期、子任务编排、报告持久化、终局重扫五件事一个文件；`_pre_step/_step/_post_step` 闭包每题重建，挂 7+ 个 nonlocal，无法单测——测试只能断言"源码包含某字符串" | tests/test_core.py:156,163 的字符串包含测试即不可测量化的直接证据 |
| A4 | P1 | **动态上下文每轮全量重发**：charter/plan/field_notes/黑板每轮拼装进 input，长题后期每轮多烧数千 decode token；且 plan 变更无版本号，模型无法区分"新计划"与"旧计划复读" | core/agents_def.py `_build_dynamic_context` |

### B. 代码质量

| # | 级别 | 问题 | 证据 |
|---|---|---|---|
| B1 | P1 | **demo_tools.py 上帝模块（1300 行）**：侦察（run_batch/fuzz/parallel_shell）、flag 提交、黑板读写、TODO、artifacts、子任务声明六域混在一个文件，任何一个域的改动都要在 1300 行里定位 | demo_tools.py 全文 |
| B2 | P1 | **SQLiteSession 从不关闭**：每题一个 session（含子任务），全仓库无 `session.close()`；长跑一场 63 题 + N 子任务，连接与文件句柄只增不减 | `grep session.close` 全仓库 0 命中 |
| B3 | P2 | **异常静默吞咽**：main.py 36 处 `except Exception`，其中泄漏处置、close 重试队列等关键路径直接 `pass`，失败后无任何计数器/落库，赛后无法追责 | app/main.py:1225-1238 |
| B4 | P2 | **函数体内定义常量**：`_WALLCLOCK_BUDGET` 在 `_run_single_challenge` 函数体内，每题重建一次 dict；阈值类常量散落 4 个文件 | app/main.py:~553；runtime/budget.py；bench_platform/scheduler.py |

### C. 工程维护

| # | 级别 | 问题 | 证据 |
|---|---|---|---|
| C1 | P1 | **头部 docstring 与实际完全相反**："通用多智能体端到端 Demo……不依赖任何靶场平台 / flag / 提交铁律"——而平台客户端、flag 提交铁律、BENCHMARK_TOKEN 全在里面。新人第一脚踩坑 | app/main.py:1-13 |
| C2 | P1 | **测试质量**：17 个用例中 2 个是"源码字符串包含"测试（重构即误报或漏报）；无 pyproject/ruff/mypy 配置，无 CI 卡口 | tests/test_core.py:156-163；仓库根无 pyproject.toml |
| C3 | P2 | **双写观测无单一真相源承诺**：事件 BUS→SQLite 与 events.jsonl 双写合理，但没有任何文档说明"以谁为准"，下游分析脚本各读各的 | core/events.py；app/main.py:75-85 |

---

## 第二部分 修补方案

### 修补 1（A1，P0）：压缩收敛为单一入口

**做法**：删一路，留一路。

1. 保留 `core/context_manager.compact_if_needed`（主循环 token 阈值触发，逻辑更完整）；
2. 把 `runtime/stuck.py` 的 `_truncate_old_tool_outputs` **搬进** `core/context_manager.py`（它才是压缩的家）；
3. `runtime/stuck.py` 的 `compact_session` 改为**薄包装**：

```python
# runtime/stuck.py
async def compact_session(ctx, session, compactor_model=None, model_pool=None):
    """卡壳路径的压缩请求。压缩实现唯一归属 core/context_manager，
    此处只做触发口径转换，不持有任何压缩逻辑。"""
    from core.context_manager import compact_if_needed
    return await compact_if_needed(session, ctx, force=True,
                                   compactor_model=compactor_model,
                                   model_pool=model_pool)
```

4. `compact_if_needed` 增加 `force: bool = False` 参数（force=True 跳过阈值检查）；
5. **验收**：`grep -rn "clear_session\|_summarize" runtime/stuck.py` 应为 0；两条触发路径产生的日志前缀统一为 `[compact]`。

### 修补 2（A2，P0）：外层 Agent 工厂化，消灭可变单例

**做法**：`core/agents_def.py` 中

```python
# 删除模块级可变共享的危险写法，改工厂函数
def build_strategist(model=None) -> Agent:
    return Agent(name="Strategist", instructions=STRATEGIST_INSTRUCTIONS,
                 tools=intel_tools(), model=model or MODEL,
                 model_settings=STRATEGIST_SETTINGS)

def build_reporter(model=None) -> Agent:
    return Agent(name="Reporter", instructions=REPORTER_INSTRUCTIONS,
                 model=model or MODEL, model_settings=REPORTER_SETTINGS)
```

- `app/main.py` 中 `strategist_agent.model = ...` 改为在每次调用点 `build_strategist(model=pool.current.model)`；
- Reporter 每场（run_task）构建一次即可，但**绝不写回模块级变量**；
- 保留旧名 `strategist_agent = None` 会误导，直接删除，让残留引用 ImportError 暴露；
- **验收**：`grep -n "\.model = " app/main.py core/agents_def.py` 只剩 executor 局部实例的赋值（灾备切换是合法的，因为 executor 是每题独立实例）。

### 修补 3（A3，P1）：巨石拆解 + 闭包对象化

不追求一次到位，按"抽得出、测得着"两步走：

**Step 1：三闭包 → 一个单题编排器对象**（收益最大的一步）

```python
# 新文件 runtime/challenge_runner.py
class ChallengeRunner:
    """单题生命周期编排：pre_step / step / post_step 三阶段的显式状态机。

    取代 _run_single_challenge 内每题重建的三个闭包 + 7 个 nonlocal。
    所有跨轮状态（switched / next_input / intervention_count / cost_base...）
    成为显式字段，单测可以直接构造、逐步驱动、断言状态迁移。
    """
    def __init__(self, code, ctx, executor, session, model_pool, hooks, ...):
        self.code = code
        self.switched = False
        self.next_input = ""
        self.intervention_count = 0
        ...

    async def pre_step(self) -> tuple[bool, str]: ...
    async def step(self) -> bool: ...
    async def post_step(self) -> tuple[bool, str, str]: ...
```

`_run_single_challenge` 瘦身为：建 runner → `while True: pre/step/post` → 收尾。逻辑不变，只搬家。

**Step 2：按职责切文件**（Step 1 完成后自然显形）

| 去向 | 内容 |
|---|---|
| `app/main.py` | 只剩入口、参数解析、run_task 外壳（目标 ≤400 行） |
| `runtime/scheduler_loop.py` | 选题/start/泄漏检测/close 重试/并发槽位 |
| `runtime/challenge_runner.py` | 单题状态机（Step 1 产物） |
| `runtime/endgame.py` | `_endgame_sweep` 终局重扫 |
| `runtime/reporting.py` | 已有，把 `_generate_report/_persist_report` 并入 |

**验收**：Step 1 后删除 `test_run_task_contains_scheduler_loop` 字符串测试，换成对 `ChallengeRunner.pre_step` 的直接状态断言（如"wrong_submit=6 且 zero_gain=3 时返回 break+wrong_submit_fuse"）。

### 修补 4（A4，P1）：动态上下文增量注入

- charter / plan 加版本号（`ctx.plan_version`），不变时注入一行 `（宪章 v3 / 计划 v2，未变）` 占位，变更时才全量重发；
- field_notes 只在首轮注入，后续轮用占位；
- 黑板维持现状（已截断到 40 字符，成本可控）；
- **验收**：同题第 10 轮的 input 体积 ≤ 首轮的 40%；`prefix_hit_rate` 不掉（占位行在尾部，不动前缀）。

### 修补 5（B1，P1）：demo_tools.py 按域拆分

工具注册机制不变（`build_default_tools` 仍是唯一装配点），只移动定义：

```
tools/
  __init__.py          # build_default_tools 重新导出，外部 import 不变
  recon.py             # run_batch / parallel_shell / fuzz / shell
  flag.py              # _submit_flags_if_any / finalize
  blackboard.py        # 黑板读写 / TODO
  artifacts.py         # read_artifact / write_file / _spill_output
  subtask.py           # spawn_subtask / finish_subtask
```

**验收**：`from demo_tools import build_default_tools` 保留一个兼容 shim（内部 `from tools import build_default_tools`），外部零改动；每个新文件 ≤300 行。

### 修补 6（B2，P1）：session 生命周期闭环

在 `_run_single_challenge` 收尾（现有六种死法日志之后）与子任务 `_run_one` 的 finally 中：

```python
        finally:
            try:
                session.close()
            except Exception as e:
                log_warn(f"[session] {code} 关闭失败：{e}")  # 不静默，但也不致命
            # sub_*.sqlite 物理清理（已有）保持
```

**验收**：跑完一场后 `ls data/worker_generic/sessions/` 无 sub_*.sqlite 残留；`/proc/<pid>/fd` 句柄数全程稳定。

### 修补 7（B3，P2）：静默吞咽改为"降级计数"

原则：可以不中断，但必须留数。

```python
except Exception as e:
    ctx.silent_failures = getattr(ctx, "silent_failures", 0) + 1
    log_warn(f"[degraded] 泄漏容器关闭失败 {lc}：{str(e)[:120]}")
```

战报末尾输出 `silent_failures=N`——N>0 就是赛后排查清单。

### 修补 8（B4+C3，P2）：常量收口 + 观测口径声明

- 所有阈值迁入 `runtime/budget.py`：`WALLCLOCK_BUDGET`、`FUSE_LIMITS`、`HINT_GRACE_TURNS`、`HARD_CAP` 集中，其他文件只 import；
- `core/events.py` 顶部加三行注释：**事件流以 SQLite 为唯一真相源，events.jsonl 仅为人读留痕，分析脚本禁止读 jsonl**。

### 修补 9（C1，P1）：docstring 改写

main.py 头部改为真实描述：

```python
"""TSec Benchmark 跑分主程序。
调度循环选题 → 单题状态机（ChallengeRunner）→ 三闸门子任务 → 终局重扫。
依赖：平台客户端（BENCHMARK_TOKEN）、flag 机械提交、模型灾备池。
"""
```

### 修补 10（C2，P1）：测试与卡口

- 删除 2 个字符串包含测试，随修补 3 换成 `ChallengeRunner` 状态断言；
- 新增 3 个高价值用例（都不需要真实模型）：
  1. 压缩单测：构造 50 条假消息 → `compact_if_needed(force=True)` → 断言条数不变、旧输出被截断、锚点消息在尾部；
  2. hash 断言单测：篡改静态模板 → 触发 cache-guard 计数；
  3. 调度单测：attempts 全 0 时按分排序，某题 attempts=1 后零启动题靠前；
- 根目录加 `pyproject.toml`（ruff + 行宽 100），CI 至少跑 `ruff check` + `pytest`。

---

## 第三部分 执行顺序与工作量

| 序 | 修补 | 级别 | 预计工作量 | 依赖 |
|---|---|---|---|---|
| 1 | 修补 2（Agent 工厂化） | P0 | 0.5 天 | 无，独立 |
| 2 | 修补 1（压缩收敛） | P0 | 0.5 天 | 无 |
| 3 | 修补 3-Step1（ChallengeRunner） | P1 | 1 天 | 无，纯搬家 |
| 4 | 修补 4（增量注入） | P1 | 0.5 天 | 无 |
| 5 | 修补 6（session 闭环） | P1 | 1 小时 | 无 |
| 6 | 修补 5（工具拆分） | P1 | 0.5 天 | 无 |
| 7 | 修补 3-Step2（切文件） | P1 | 0.5 天 | 依赖 3 |
| 8 | 修补 7/8/9/10（卫生 + 卡口） | P2/P1 | 0.5 天 | 10 依赖 3 |

**两个 P0 合计 1 天，建议最先做**——它们不修，架构意图（单真相源、无共享可变状态）会随每次新功能提交继续稀释。P1 中 3-Step1 是"可测试性"的钥匙，有了它后续所有重构才有安全网。

## 验收总清单

- [ ] 压缩实现唯一归属 `core/context_manager`，stuck.py 只剩薄包装
- [ ] 全仓库无对模块级 agent 实例的 `.model =` 赋值
- [ ] `ChallengeRunner` 可被单测直接实例化并驱动状态迁移
- [ ] 同题第 10 轮 input 体积 ≤ 首轮 40%
- [ ] 赛后 sessions 目录无泄漏、进程句柄数稳定
- [ ] 战报含 `silent_failures` 计数
- [ ] 阈值常量全部在 `runtime/budget.py`
- [ ] 字符串包含测试 = 0；`ruff check` + `pytest` 进 CI

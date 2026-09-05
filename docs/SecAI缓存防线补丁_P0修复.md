# SecAI 缓存防线补丁（P0 修复）

> 针对最新代码（e83119d）核对后的两处 P0 遗留问题：
> **P0-1 静态 hash 只算不断言**（缓存防线没关门）；
> **P0-2 压缩时 clear_session 清空全部历史**（每压一次，前缀缓存归零一次）。
> 这两个都是 20 行量级的改动，直接决定 85% 命中率目标能否兑现。
> 另附两个 P1 一行修复（覆盖率加权、战报命中率）。

---

## 补丁 1（P0-1）：静态 prompt hash 断言接线

**问题**：`build_executor` 计算了 `static_prompt_hash` 挂在 agent 上，但全仓库无任何读取点。
一旦有人往 `_render_executor_instructions` 里拼入每轮变量（如 phase、blackboard），
前缀缓存会无声归零——0% 命中率回归且没有任何告警。

**修复位置**：`app/main.py`，`_pre_step()` 内，墙钟检查之前。

```python
        async def _pre_step() -> tuple:
            """每轮 step 前：检查硬性终止条件、重置 turn 状态、阶段/模型升级。"""
            nonlocal switched

            # ── 新增：静态 prompt 字节级断言（缓存防线关门） ──────────────
            # build_executor 时计算的 hash 必须全赛程不变；变了说明静态模板
            # 被每轮变量污染，前缀缓存已断，必须立刻暴露而不是默默烧钱。
            _src_now = getattr(executor, "static_prompt_src", None)
            _hash_expect = getattr(executor, "static_prompt_hash", None)
            if _src_now is not None and _hash_expect is not None:
                from core.agents_def import _prompt_hash
                _hash_now = _prompt_hash(_src_now + "\n" + ",".join(
                    sorted(getattr(t, "name", "") for t in executor.tools)))
                if _hash_now != _hash_expect:
                    log_error(f"[cache-guard] 单题 {code} 静态 prompt hash 漂移："
                              f"{_hash_expect} -> {_hash_now}，前缀缓存已断！"
                              f"检查是否有人往静态模板拼了每轮变量。")
                    ctx.cache_guard_violations = getattr(
                        ctx, "cache_guard_violations", 0) + 1
            # ── 新增结束 ─────────────────────────────────────────────────

            # 墙上时钟硬顶……（原有代码不变）
```

**同时**，在 `app/main.py` 单题结束的战报写入处（`f.write(f"- cache_hits=...")` 附近）加一行：

```python
            f.write(f"- cache_guard_violations="
                    f"{getattr(ctx, 'cache_guard_violations', 0)}\n")
```

**验收**：故意往 `EXECUTOR_STATIC_INSTRUCTIONS` 的 format 参数里塞 `{ctx.phase}` 跑一次，
日志必须出现 `[cache-guard]` ERROR；恢复正常模板后 violation = 0。

---

## 补丁 2（P0-2）：压缩改为 append-only，禁止 clear_session

**问题**：`runtime/stuck.py` 的 `compact_session()` 摘要成功后调用
`session.clear_session()` 清空全部历史——下一轮请求的上下文从第 0 位就变了，
前缀缓存**整体归零**，且轨迹不可回放。压缩越频繁的难题，越按全价付费。

**原则**：压缩不是"删历史换摘要"，而是"截旧工具输出正文 + 摘要追加到尾部"。
缓存只认逐字节前缀——截断点之前的内容（system + 早期消息）依然命中，
clear_session 则是从第 0 位就断。两者差一个量级。

**修复位置**：`runtime/stuck.py`。

### 2.1 新增定点截断函数（放在 `compact_session` 之前）

```python
def _truncate_old_tool_outputs(session, keep_recent: int = 12,
                               max_output_len: int = 300) -> int:
    """对 session 中较旧的工具输出做定点截断（保留结论行），不删任何消息。

    直接 UPDATE SQLite 的 message_data：把 keep_recent 条之前、超过
    max_output_len 的工具输出正文替换为截断标记。消息条数、顺序、id 全部不变，
    截断点之前的前缀字节不变 → 前缀缓存仍然命中截断点之前的部分。

    返回截断的消息条数。任何异常都返回 0（宁可不截，不可破坏历史）。
    """
    import json as _json
    import sqlite3 as _sql

    db_path = getattr(session, "db_path", None)
    if not db_path:
        return 0
    table = getattr(session, "messages_table", "session_messages")
    sid = session.session_id
    truncated = 0
    try:
        conn = _sql.connect(str(db_path))
        try:
            rows = conn.execute(
                f"SELECT id, message_data FROM {table} "
                f"WHERE session_id = ? ORDER BY id ASC", (sid,)).fetchall()
            cutoff = max(0, len(rows) - keep_recent)  # 只动旧消息，最近的原样保留
            for row_id, raw in rows[:cutoff]:
                try:
                    item = _json.loads(raw)
                except Exception:
                    continue
                changed = False
                # function_call_output 是工具结果的主要载体
                content = item.get("content") if isinstance(item, dict) else None
                if item.get("type") == "function_call_output":
                    out = item.get("output", "")
                    if isinstance(out, str) and len(out) > max_output_len:
                        head = out[:120].split("\n")[0]  # 保留结论行
                        item["output"] = (
                            f"{head}\n…[压缩截断，原文 {len(out)} 字符，"
                            f"全文见 artifacts/ 或黑板]")
                        changed = True
                elif isinstance(content, list):
                    for part in content:
                        if (isinstance(part, dict)
                                and part.get("type") in ("input_text", "output_text")
                                and len(str(part.get("text", ""))) > 4000):
                            text = str(part["text"])
                            part["text"] = (text[:200] +
                                            f"\n…[压缩截断，原文 {len(text)} 字符]")
                            changed = True
                if changed:
                    conn.execute(f"UPDATE {table} SET message_data = ? WHERE id = ?",
                                 (_json.dumps(item, ensure_ascii=False), row_id))
                    truncated += 1
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return 0
    return truncated
```

### 2.2 改写 compact_session 的收尾段

把现有的：

```python
    # 摘要成功：清空 SQLiteSession 历史（保留 ctx 中的任务元信息），并写入摘要
    try:
        await session.clear_session()
    except Exception:
        pass

    ctx.compaction_summary = summary
    return summary
```

改为：

```python
    # 摘要成功：① 定点截断旧工具输出（不动消息结构，保住截断点前的前缀缓存）
    #           ② 摘要作为黑板快照追加进上下文尾部（append-only，不重建历史）
    # 仅当截断后仍超超长硬阈值时，才允许 clear_session 兜底（记 ERROR，赛后追责）。
    truncated = _truncate_old_tool_outputs(session)
    log_info(f"[compact] 单题 {ctx.challenge_code} 定点截断 {truncated} 条旧输出，"
             f"历史结构保留（append-only）")

    ctx.compaction_summary = summary
    # 摘要锚点：作为普通消息追加到尾部，模型下一轮自然看到
    try:
        await session.add_items([{
            "role": "user",
            "content": (f"[黑板快照·压缩锚点]\n{summary}\n"
                        f"（以上是对更早历史的压缩；被截断的工具输出全文可查 artifacts/ "
                        f"或黑板；禁止重复已证伪方向）")
        }])
    except Exception:
        pass

    # 超长硬阈值兜底：截断后历史仍失控才允许全清（应几乎不触发）
    try:
        items_after = await session.get_items()
        total_chars = sum(len(str(i)) for i in items_after)
        HARD_CAP = 400_000  # 约 10 万 token 量级，按模型上下文调整
        if total_chars > HARD_CAP:
            log_error(f"[compact] 单题 {ctx.challenge_code} 截断后仍 {total_chars} 字符 "
                      f"> {HARD_CAP}，被迫 clear_session（前缀缓存归零，赛后排查）")
            await session.clear_session()
    except Exception:
        pass
    return summary
```

> 注意：`ctx.challenge_code` 若字段名不同（如 `ctx.code`），按 `core/task_context.py`
> 实际字段替换。

**验收**：跑一道会触发压缩的长题（或手动把压缩阈值调低强制触发）：
1. 日志出现 `[compact] ... 定点截断 N 条` 且无 `clear_session` ERROR；
2. 压缩后下一轮 token usage 里 `cached_tokens` > 0（截断点之前命中）；
3. 压缩后黑板关键事实（flag/hint/死路）仍在动态上下文中（原有 `_build_dynamic_context` 注入逻辑不变，天然满足）。

---

## 补丁 3（P1，一行）：调度器覆盖率加权

**位置**：`bench_platform/scheduler.py` `select_challenge()`，EV 计算行之后：

```python
        ev = float(c.get("total_score", 0) or 0) * coef * (base ** attempts.get(code, 0))
        if attempts.get(code, 0) == 0:
            ev *= 3.0  # 零启动倾斜：从未做过的题优先破零（上届 63 题 23 题零启动）
```

---

## 补丁 4（P1，一行）：战报输出真实前缀命中率

**问题**：`ctx.cache_hits` 统计的是"历史模板/笔记复用率"，不是计费意义上的前缀命中率。
**位置**：`app/main.py` 战报写入处（约 897 行）追加：

```python
            _cr = sum(u.get("cache_read", 0) for u in ctx.turn_usages)   # 按实际字段名调整
            _cw = sum(u.get("cache_write", 0) for u in ctx.turn_usages)
            _rate = _cr / (_cr + _cw) if (_cr + _cw) else 0
            f.write(f"- prefix_hit_rate={_rate:.1%} (read={_cr} write={_cw})\n")
```

> `ctx.turn_usages` 指 hooks.py 中记录 `cache_read/cache_write` 的那个列表（core/hooks.py:514 附近
> 的 `cur` 字典容器），按实际变量名接线。目标：**单场汇总 ≥85%**。

---

## 回归测试清单

- [ ] 往静态模板塞每轮变量 → `[cache-guard]` ERROR 必现；恢复后 violation=0
- [ ] 强制触发压缩 → 出现"定点截断"日志，无 clear_session（除非超长兜底）
- [ ] 压缩后下一轮 `cached_tokens > 0`
- [ ] 战报含 `prefix_hit_rate`，模拟两场后汇总 ≥85%
- [ ] 调度模拟：attempts 全 0 时 EV 排序与 total_score 一致；某题 attempts=1 后零启动题排前

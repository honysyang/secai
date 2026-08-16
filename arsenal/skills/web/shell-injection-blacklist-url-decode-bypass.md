---
name: Shell Injection Blacklist Url Decode Bypass
description: 黑名单过滤先于 URLDecoder.decode 的双重 URL 编码绕过
triggers: shell injection, 命令注入, 黑名单, URL decode, 双重编码, URLDecoder
category: web
---

## 场景

Web 后端对用户输入（如标题、文件名）做 shell 元字符黑名单过滤，然后把值拼进 `bash -c`/`sh -c` 命令。但过滤发生在 `URLDecoder.decode(value)` **之前**，解码后的值直接进入命令字符串。

## 识别要点

源码审计时注意三点时序：
1. 是否有黑名单/正则校验（`;` `"` `'` `$` `` ` `` `>` `<` 空格 等）
2. 校验之后是否对同一变量做 `URLDecoder.decode`
3. 解码后的变量是否参与字符串拼接后交给 `ProcessBuilder("bash","-c",...)` / `Runtime.exec`

命中即存在"黑名单只看编码前原文"的绕过。

## 编码层数

- 服务端框架（Spring `@RequestParam`、Servlet 容器）通常已对 query 参数解码**一次**。
- 业务代码再 `URLDecoder.decode` **一次**。
- 因此 HTTP 请求里要**双重 URL 编码**（`%` → `%25`），让业务代码收到的仍是 `%XX` 形态（不命中黑名单），其内部 decode 后变回元字符。

```
目标 shell 片段:  "; cat /flag > /tmp/exports/pwn.txt; #
单层编码:        %22%3B%20cat%20%2Fflag%20%3E%20%2Ftmp%2Fexports%2Fpwn.txt%3B%20%23
双层编码(HTTP):  %2522%253B%2520cat%2520%252Fflag%2520%253E%2520%252Ftmp%252Fexports%252Fpwn.txt%253B%2520%2523
```

## 输出回收（半盲 RCE）

进程输出常被 `readAllBytes()` 丢弃（半盲）。把命令结果**重定向进产物文件**，再用站点自带的回读接口读取：

- 命令注入点后常跟模板尾部（如 `> "outFile" && cat src >> outFile`），注入 `; cmd > outFile; #` 用 `#` 注释掉尾部，防止后续 `>` 覆盖结果。
- 回读文件名受白名单时（如 `^[A-Za-z0-9_.-]+\.\w+$`），输出文件选 `name.txt` 这种合规名即可。

## 其他注意

- `URLDecoder.decode` 会把 `+` 转成空格，payload 空格尽量用 `%20` 以免歧义。
- 先用无害命令验证注入成立（如 `id > out.txt`），再读结果，避免盲猜。
- 关联打法：[[web-rce-playbook]]、[[web-file-read-rce-chain]]。
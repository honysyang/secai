---
description: 识别组件后联网搜 CVE/PoC 情报
triggers: 组件版本, 框架版本, cve, 指纹识别, 版本号, 已知漏洞, 组件漏洞, 中间件
---

# 组件漏洞情报（识别组件 → 联网搜 CVE/PoC）

## 触发条件
识别出框架 / 组件 / 版本后，必须先搜已知漏洞再动手利用（不搜就利用 = 盲打 = 浪费轮次）。

## 决策树
1. CVE 库：`search_cve` 工具 / `curl cve.circl.lu` 搜组件+版本；
2. GitHub 搜 PoC：`api.github.com/search/repositories?q=<组件>+CVE`；
3. 中文社区：先知 / seebug / freebuf 搜组件漏洞分析（中文首发多、分析深）；
4. 找到 PoC 后：先本地验证，再打目标（PoC 只是线索，验证后才算 confirmed）；
5. 依赖扩展：搜完组件后，提取依赖清单（requirements.txt / package.json / pom.xml）对每个依赖重复 1-4；
6. 搜不到 → 转零日/变体分析思路（读源码找漏洞）。

## payload 库
- CVE 搜索：`curl -s "https://cve.circl.lu/api/search/<组件>/<版本>"`
- GitHub PoC：`curl -s "https://api.github.com/search/repositories?q=<组件>+exploit+poc"`
- 本地：`searchsploit <组件> <版本>`（若可用）

## 收尾判据
拿到可验证的 PoC / 利用方式，或确认全网无公开漏洞（转自主发现）。

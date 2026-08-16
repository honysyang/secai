---
name: 提示词优化
description: 渗透场景下的精准指令打法：漏洞探测子任务自包含、CVE/漏洞检索词精准、漏洞结论结构化
triggers: 缺失信息, 未走结束协议
---

# 渗透场景下的精准指令

精准表达是为了让下游（子任务/下一轮/检索）一次听懂、少返工，不是束缚探索。目标是「指令有落点，假设有证据」：

1. 漏洞探测子任务自包含：`spawn_subtask` 的 desc 写清 ①目标端点（URL/IP:Port/路径/参数）②漏洞类型 ③payload 来源 ④正/负证据。缺信息时宁可少开子任务，也别让子任务空转乱猜。

2. CVE/漏洞检索词精准：`search_cve` 用「产品名+版本」或「CVE 编号」；`find_skills` 用「漏洞类型」（sqli/ssti/ssrf/lfi/rce）；`list_knowledge` 用「技术栈/组件」（django/nginx/redis/weblogic）。无结果就换同义词/上位词，别反复用同一模糊词。

3. 漏洞结论结构化：`blackboard` 记「漏洞类型 + 注入点 + 可利用方式 + 证据」，例「sqli_confirmed: /login?id= 可 UNION，证据=mysql syntax error 回显」。让下一轮或子任务直接接上利用。

4. 指令有落点：能用「对 /api/user?id= 测 IDOR」就不写「继续深入」；确需继续探索时，写清「已确认 X + 下一步测 Y」，让接手方不用重读上文。

5. 假设驱动：每条动作 =「假设 X 漏洞 → 具体 payload → 预期正/负响应」；未知场景下假设可先宽泛，用差异实验（distinguish/fuzz）逐步收窄到具体漏洞。

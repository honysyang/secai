---
description: 武器库导航：POC/载荷/知识/CLI 工具/技能的索引与启用方式
---

# 武器库导航

你有一套本地化武器库，按需启用后使用。这些工具默认未挂载，先 `enable_tool <组名>` 再调用。

## POC 库（已知漏洞利用思路，20+ 个）
- 何时用：指纹识别到产品/框架/CVE 后，立刻查有没有现成漏洞
- 用法：`enable_tool poc` → `search_cve <产品名/CVE>` → `get_poc <name>`
- 覆盖：云服务（aws/azure/s3）、Web 框架（flask/php）、协议（tls/tcp/jwt）等

## 漏洞检测模块 + Payload 字典（vuln 组）
- 何时用：怀疑某类漏洞时（SQLI/XSS/SSTI/LFI/RCE/IDOR/SSRF/XXE/UPLOAD）
- 用法：`enable_tool vuln` → `list_vulns` 看类型 → `detect_vuln <type>` 取标准检测规范 → `get_payload <type>` 取载荷字典
- 覆盖：9 类漏洞检测规范 + 10 类 payload（sqli/lfi/path/xss/ssti/rce/idor/ssrf/upload/xxe）

## 知识库（通用打法）
- 何时用：后利用 / 绕 WAF / 拿 flag
- 用法：`enable_tool knowledge` → `list_knowledge` → `get_knowledge <id>`

## CLI 安全工具（92 个）
- 何时用：端口扫描 / 目录爆破 / 注入检测 / 二进制分析
- 用法：`enable_tool seccli` → `list_tools` → `get_tool_spec` → `run_tool <name>`

## 技能库（62 个打法）
- 何时用：不熟悉场景
- 用法：`find_skills <关键词>`

## Web 工具（web 组）
- `distinguish`：差分实验，多组探测值对比响应差异定位攻击面
- `web_search`：联网搜索（查公开资料/已知漏洞）
- 用法：`enable_tool web` → `distinguish` / `web_search`

## 铁律
指纹识别到产品/框架后**立刻** search_cve 查现成 POC；确认漏洞类型后**立刻** get_payload 取载荷——不要凭记忆裸打，先用现成武器。

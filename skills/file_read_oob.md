---
description: 文件读取 / 越界读取（Web 侧）
triggers: 文件读取, 路径遍历, lfi, 源码泄露, 越界, oob, file://
---

# 文件读取 / 越界读取（Web 侧）

## 触发条件
存在下载/读取功能、路径可控、模板/包含点、或源码泄露线索。

## 决策树
1. 从功能点反推代码路径：下载 → 路径遍历；模板 → SSTI；上传 → 校验绕过；
2. 源码泄露优先：读配置（数据库/密钥）、读源码（找硬编码 flag/后门）；
3. 路径遍历：`../` 阶梯、绝对路径、`file://`、`php://filter`（PHP）；
4. 越界读：目录穿越 + 敏感文件（/etc/passwd、/proc/self/environ、应用配置）。

## payload 库
- 遍历：`../../../../etc/passwd`、`....//`、`%2e%2e%2f`
- PHP：`php://filter/convert.base64-encode/resource=index.php`
- SSTI：`{{7*7}}`、`${7*7}`、Jinja2 `{{config}}`
- 源码泄露：`.git/config`、`/proc/self/cwd/...`

## LFI 差分检测法（ctfSolver）
1. 用 `{LFI}` 占位符标记注入点：`{"url": "http://x/page?file={LFI}", "method": "GET", ...}`；
2. 先跑 DEFAULT 内置载荷（或自定义逗号分隔载荷），并发测试；
3. 响应归一化差分：把响应正文里的 payload 替换成 `{payload}` 后归并——响应相同的载荷归到一组，真正「触发差异」的载荷会单独成组；
4. 路径组合：对目标路径从后往前逐级生成变体（含去后缀版本），覆盖 include/require 的常见解析差异；
5. 命中差异后，再针对该载荷做后利用（读配置/源码）。

## 收尾判据
读到 flag 文件或拿到可进一步利用的凭据/源码。

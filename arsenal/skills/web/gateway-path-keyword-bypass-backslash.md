---
name: Gateway Path Keyword Bypass Backslash
description: 网关路径段关键字拦截用反斜杠分隔符绕过；403/404/401 判别表
triggers: gateway, 网关, 反斜杠, 路径关键字, 403, 绕过, backslash
category: web
---

# 网关路径关键字拦截的绕过（反斜杠路径分隔符）

## 场景
前端 JS 或文档里暴露了内部 API 路径，但网关/反向代理按**路径段关键字**（如 `/baike/...`、`/admin/...`）拦截，返回 403 "Blacklisted keyword detected as path segment" 之类。

## 根因
网关与后端对路径的解析不一致：
- 网关只按 `/` 切分 path segment 做关键字匹配；
- 后端（常见 Flask/Werkzeug、nginx 上游、Java servlet 等）会把 `\` 归一化为 `/` 再路由，或对 `\` 容忍。

## 打法
1. 用反斜杠替换 `/` 作为路径分隔符，且保持**被拦关键字所在段不出现 `/`**：
   - `/baike/contribute/v1/personSuggest` → `/baike\contribute/v1/personSuggest`
   - 网关看到的段是 `baike\contribute`（不含 `baike` 独立段）→ 放行；
   - 后端归一化后按 `/baike/contribute/v1/personSuggest` 路由。
2. 若返回 401/405/400（而非 404），说明已绕过网关并命中真实后端路由——据此继续猜方法/参数（405→换 POST；400 detail 会提示必填字段）。
3. 关键判别：**403 = 网关拦截；404 = 后端无此路由（路径真不对）；401/405/400 = 已穿过网关到达应用层**，这是最大的进展信号。

## 其他可试变体（同一类不一致思路）
- 编码变体：`%62aike`（网关解码则无效）、`%2562aike`（双重编码，网关不解、后端也不解时得 404 需再测）、`%2f` 编码斜杠。
- 大小写：若网关检查大小写敏感而后端路由也敏感则无效（返回 404）。
- `//`、`/./`、`..;/`、`;` 分号、`%00`、尾斜杠——均按 403/404/401 的判别表判断。
- 若后端是 Java（Tomcat），`;` 分号路径参数、`/..;/` 常有效；本场景后端归一化 `\` 即可。

## 提示
先发一次无凭据请求看返回码分层，再决定是补 Authorization、换 HTTP 方法还是换路径变体。勿在 403 上直接放弃——它只证明网关在拦，不证明接口不存在。
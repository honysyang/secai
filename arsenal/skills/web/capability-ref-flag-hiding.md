---
name: Capability Ref Flag Hiding
description: 能力引用+内部目录隐藏 flag：找隐藏布尔参数扩展检索范围
triggers: capability, ref, idor, 内部目录, 隐藏 flag, 布尔参数, 用户中心
category: web
---

# 能力引用（capability reference）+ 内部目录隐藏 flag 打法

## 题型特征
- 描述常为"普通用户只能读对外通讯录，管理员机密档案藏在内部目录，从不外显"。
- 后端用"能力引用"（服务端 HMAC 签名的 resourceId 编码串，形如 `base64url(rid).hex_tag`）间接寻址档案；客户端拿不到裸 recordId，也无法伪造签名。
- 列表接口只返回对外成员；内部成员（含 admin）不在列表里，但**成员解析接口**可能留有隐藏开关把检索范围扩展到内部目录。

## 攻击链
1. **拿源码**：靶场常另开一个 HTTP 服务直接吐源码 ZIP（或 /src.zip、/.git、备份文件）。源码审计是第一优先级——此类题 flag 藏在 FlagHolder/FlagService 等类中，调用条件在 Controller 里一目了然。
2. **审计关键类**：
   - `Directory`/成员存储类：看 internal 成员列表、lookup 的第二个参数（如 includeInternal / expandDirectory / includeInactive）。
   - `ApiController`/查询 Controller：看哪个接口把请求体字段透传给 lookup；**请求体反序列化对象（如 MemberQuery）里注释为"保留字段/跨部门"的布尔字段就是越权开关**。
   - 档案打开接口：看响应组装时是否 `if ("admin".equals(role)) resp.put("opsToken", FlagHolder.get())`。
3. **利用**：
   - 登录拿会话（此类题常任意用户名可登录）。
   - 调成员解析接口，POST JSON `{"name":"admin","expandDirectory":true}` 之类，拿内部成员的能力引用。
   - 调档案打开接口传该引用，读取详情；role=admin 时响应含运维令牌/flag。
4. **注意**：API 发 JSON 时必须显式带 `Content-Type: application/json`，否则 Spring `@RequestBody(consumes=JSON)` 解析失败返回统一 nomatch，易误判为"不存在"。

## 判死纪律
- "统一无信息响应"（所有失败都返回同一 `{"status":"nomatch"}`）是刻意防 oracle 的，单次 nomatch 不能证明方向不存在——先检查 Content-Type、参数名、大小写、字段名是否匹配 getter。
- 源码里的占位/蜜罐 flag（role 仍是 user）不是真 flag，找 role=admin 的记录。

## 关联
[[web-enumeration]] [[rsa-oracle]]（同族：签名能力引用+预言机比较类题目，参考常量时间比较）
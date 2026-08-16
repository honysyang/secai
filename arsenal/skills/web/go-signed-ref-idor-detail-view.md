---
name: Go Signed Ref Idor Detail View
description: Go 概要接口 redact+明细接口不过滤：parent_ref 逐级导航拿 root 引用
triggers: go, signed ref, capability, idor, parent_ref, redact, detail view, 越权
category: web
---

# Go 后端签名对象引用（capability）的越权读取

## 场景特征
- 后端用 HMAC 签名的无状态令牌作为对象引用（capability），格式通常 `base64(payload).base64(mac)`，payload 为 `nodeID|expiry`。
- 服务端提供两类同对象接口：**概要视图**（对敏感字段做 redact/裁剪）与**明细视图**（返回完整属性），两者共用同一签名校验逻辑但过滤不一致。
- 资源目录是树形结构（root/team/staff 层级），登录会话只绑定到自己的叶子节点。

## 攻击链（方法论）
1. 拿到源码后先画数据流：`issueSession`（会话签发）、`mintRef/resolveRef`（引用签发/校验）、各 handler 的过滤差异。
2. 对比"概要"与"明细"两个 handler：概要里 `if redacted[k] { continue }`，明细里直接 `n.Attrs`——**明细接口未继承 redact 就是越权点**。
3. 引用虽不可伪造，但**导航接口会主动下发 parent_ref**（向上导航父节点的签名引用）。从自己的节点出发逐级 resolve，即可合法收集到 root 节点的引用。
4. 对 root 引用调用明细接口，读出被概要接口隐藏的敏感属性（如 bootstrap_credential / 下发凭据）。

## 关键提示
- 源码服务器与运行服务器往往是两台（一个给源码 zip，一个跑服务），先全量抓取源码再打运行实例。
- 留意代码里的**诱饵 flag 常量**（如占位 legacy config 中的假 token），提交前以真实数据源（环境变量 FLAG / /flag 文件）为准。
- 校验 `resolveRef` 的过期时间格式（`fmt.Sscanf("%d")`）与 `unpack` 的容错逻辑：任何异常统一 deny，无响应差异可区分。

## 相关
[[idor-object-reference]] [[go-http-source-audit]] [[capability-token-hmac]]
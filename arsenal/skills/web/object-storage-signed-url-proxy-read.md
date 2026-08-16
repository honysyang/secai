---
name: Object Storage Signed Url Proxy Read
description: 对象存储签名只签 host 不签路径 + app 回源代理 + ListObjects 列桶
triggers: s3, bucket, signed url, 对象存储, 签名, 回源代理, ListObjects
category: web
---

## BOS/对象存储签名 URL 越权读取（host-only 签名 + 应用自带回源代理）

场景：文件管理系统对接到对象存储（Baidu BOS / S3 兼容），服务端生成预签名 URL 供前端直传/直读。

### 核心漏洞点
1. **签名不绑定对象路径**：检查 `authorization` 签名字符串中的 signed headers 部分（如 `.../7200/host/<hex>` 表示只签了 host）。若签名仅覆盖 host，则同一签名可替换 URL 中任意对象路径——拿到自己文件的签名即能读/写桶内任意对象。
2. **服务端存在未文档化的回源代理路由**：应用常暴露 `/bucket/<objectKey>`、`/<prefix>/<objectKey>` 之类路由，把请求转发到桶服务（由 403 文案 "invalid or expired authorization" 识别）。无签名 403、带合法签名则返回桶的真实响应（404/200/403 区别可作对象存在性 oracle）。
3. **桶 ListObjects API 可经代理访问**：BOS 风格 `GET /v1/<bucket>?prefix=...&marker=...&max-keys=...`（经代理变成 `/bucket/v1/?prefix=&max-keys=1000`）返回 JSON `{contents:[{etag,key,lastModified,size}], name, prefix}`——一次拿到全部对象名，flag 常藏在非默认前缀（如 `<uuid>/secret/flag.txt`，不在 uploads/ 下）。

### 利用步骤
1. 通过任意上传接口生成一个合法签名 URL（objectKey 满足前缀校验即可，无需真实对象）。
2. 提取 `authorization` 参数。
3. 用 `GET <app>/bucket/v1/?authorization=<auth>&prefix=&max-keys=1000` 列举桶对象。
4. 用 `GET <app>/bucket/<找到的key>?authorization=<auth>` 读取目标对象内容。

### 经验
- 直连桶服务被源 IP ACL 拦截（TCP connect 成功但 HTTP 首字节前 reset/超时）时，不要放弃——优先找 app 内的回源代理路由（试 `/bucket/`、`/v1/`、`/storage/`、`/files/` 前缀 + 无签名时看 403 文案）。
- 响应体字节数差异可区分响应来源：桶的 404（如 13B "404 Not Found"）vs 应用 Flask 404（~207B HTML）。
- 路径穿越对对象读取通常无效（key 校验在服务端），靠 ListObjects 拿真实 key 名最稳。
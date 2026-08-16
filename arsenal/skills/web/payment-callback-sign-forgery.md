---
name: Payment Callback Sign Forgery
description: 支付回调 sign_type 降级 + 密钥与公开证书同源派生伪造签名
triggers: payment, 支付, callback, 回调, sign, 签名伪造, MD5, 证书
category: web
---

# 支付网关异步回调验签绕过（sign_type 降级 + 密钥同源派生）

## 适用场景
- 商城/收银台对接支付网关，`/pay/callback` 类接口校验网关签名后把订单置为已支付
- 裸伪造被拒（RSA 验签有公钥），但有"历史兼容算法"痕迹（源码注释、接口文档只公布新算法、路由里有 legacy 端点）

## 核心漏洞模式
1. **验签算法标识攻击者可控**：回调报文里的 `sign_type` 字段未经白名单映射，直接进入 `verify()` 的 switch；服务端保留旧版 MD5 摘要路径（`md5(canonical + key)`）"兼容存量商户"。
2. **摘要密钥与公开证书同源派生**：源码里 `keyMaterial = base64(der)`，而 der 正是 `/pay/gateway/cert` 公开 PEM 的 body 解码结果。于是 `keyMaterial` 完全公开可算：
   ```
   pem_body = 去掉 PEM 头尾与换行
   der = base64decode(pem_body)
   key_material = base64encode(der)   # 恒等于 pem_body 去换行
   ```
3. 验签通过后若无"算法白名单"或"MD5 仅限存量商户/低额"检查，则任意订单可伪造。

## 利用链模板
1. 登录/下单拿到目标订单号（金额取商品原价，防金额校验失败）
2. GET 证书接口 → 还原 keyMaterial
3. 构造规范串（与源码一致：参与签名字段集合、按 key 升序、`k=v` 以 `&` 连接；sign/sign_type 不参与）
4. `sign = hex(md5(canonical + key_material))`，`sign_type = "MD5"`
5. POST /pay/callback → 若 200 则订单已入账
6. 访问发货/取货接口领取 flag 或权益

## 要点
- 金额字段必须等于订单应付金额（服务端通常有金额比对防篡改），不要在 canonical 里改价
- nonce/timestamp 要合法（窗口内、非空、不重放）；每次尝试换新 nonce
- 先读源码确认 canonical 拼法、参与字段集合、keyMaterial 派生方式——这三处任何一处不一致签名必失败
- 顺手检查有没有 decoy flag 常量（如赠品券码），别把它当真 flag

## 相关
[[web-enumeration]]、[[crypto-oracle]]
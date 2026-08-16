---
name: Android Apk Shuffled Dex Signature Block Xor Key
description: 对抗性逆向：dex 乱挂+35c nibble 清零识别与修复、签名块证书当 XOR 密钥（赛后由通关总结补齐）
triggers: apk, dex, android, signing block, shuffled, healthtrack, 签名块, XOR
category: rev
---

# APK 对抗性静态逆向：乱挂 dex + 签名块证书 XOR 密钥

适用："下载 APK 附件纯静态逆向找 flag"类题，且出题人做了**两层对抗**：dex 结构被刻意打乱（手写解析器会反汇编错乱），密钥不在代码常量里而是**派生自 APK 签名证书**。

## 侦察定位

1. 靶机仅 80 端口开放、根路径直接吐 APK（几十 KB）→ 纯静态逆向题，不要在服务面浪费时间（masscan 全端口确认一次即可收口）。
2. `strings` 先行找指纹：可疑 base64 常量（44 字节 `=` 结尾样串）、资源名（`device_seed.bin` 类）、`getFlag`/`getKey`/`getSeedKey` 方法名、错误文案（`ERR: auth failed`、`ERR: no active session`——提示存在陪衬的动态协议链路）、核心类名（FlagManager/SeedManager/XxxProvider/XxxSyncService）。
3. `META-INF` 里**只有 MANIFEST 没有 .RSA**、或证书不在 META-INF → 密钥大概率在 **APK Signing Block v2 段**。

## 对抗点一：dex 被打乱

手写解析器（无 jadx/apktool 环境下）遇到这两类症状，是**刻意对抗**而非解析器 bug：

- **class_data 方法条目跨类乱挂**：FlagManager 的 class_data 里出现 `R$style.<init>` 等无关方法。成因是 class_def 的 class_data_off 指向被交换/偏移过的数据。对策：不信任"类名→方法"的归属关系，按**方法名/描述符**全局搜索定位目标方法，再从其 code_off 反查。
- **35c invoke 参数寄存器 nibble 清零**：`invoke-virtual {v1, v2}, meth` 的 `AA BB` 字节中，**A（参数寄存器数）在 byte1 高 4 位、G 在低 4 位**；出题人把低 nibble 清零后，按标准格式读会得到错误的参数个数→调用关系全错。对策：解析时固定按 `A = b1>>4` 取值，出现参数寄存器数为 0 但 method 有参的矛盾时，怀疑 nibcle 被篡改，改用"method_idx + 顺序扫寄存器"重建调用图。

修正后典型还原结果：`getFlag = XOR(Base64.decode(硬编码密文), getKey())`，`getKey/getSeedKey = SHA256(签名证书 DER)`（可能取 `[:16]` 截断或全文 32 字节，从指令里的常量判断）。

## 对抗点二：密钥在 APK Signing Block

1. 定位：ZIP central directory 前找 magic `"APK Sig Block 42"`（块尾 16 字节处）。
2. v2 签名段（ID 0x7109871a）的 signer 记录内嵌**证书 DER**（数百~千余字节，`30 82` 开头）。
3. 多证书（signatures[1]）时注意**取哪一份**——指令里 `const/16 vX, 0x1` 索引即答案；单证书直接 `SHA256(der)`。
4. 无 openssl 时 Python 一行提取：解析 block 尺寸字段 → 按长度前缀遍历 ID-value 对 → 取 0x7109871a 的 value → 再按 signer 长度前缀剥层拿 certificate。

## 本地复现与自洽校验

```python
import base64, hashlib
data = base64.b64decode("clC0...=")          # dex 字符串表里的密文
key = hashlib.sha256(cert_der).digest()       # 签名块证书 DER
flag = bytes(d ^ key[i % len(key)] for i, d in enumerate(data))
```

**自洽校验**：解密输出以 `flag{` 开头且可打印、长度与密文一致 → 直接提交，不必回 App 验证。
若不命中，按密钥变体小空间枚举：SHA256(der)[:16]、SHA256(der) 全文、双证书取另一份、SHA256(签名值而非证书)。

## 陪衬链路识别

错误文案若提示"会话协议 / code=0 / auth failed"，说明 App 内还有 Service/Provider 动态协议链（nonce 会话、路径穿越二段）——**静态题里它们通常不可达或只是红鲱鱼**，密文+证书派生密钥一旦自洽命中就收口，不要回头陪跑动态链。

## 相关

[[android-apk-flag-extraction]]、[[android-apk-static-rev-flag]]（dex 手写解析器通用坑清单）、[[android-apk-prompt-leak-ctf]]
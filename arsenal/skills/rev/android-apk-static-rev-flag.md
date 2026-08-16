---
name: Android Apk Static Rev Flag
description: 静态恢复 flag：声称算法名可能是幌子，先查 method 表有无标准加密类
triggers: apk, android, static rev, 静态逆向, 加密类, 算法名, 幌子, flashmemo
category: rev
---

# Android APK 静态逆向找 flag

## 快速流程
1. 拿到 APK（可能是 HTTP 直接返回）→ `unzip` 解压，重点看 `classes.dex`、`res/raw/*`、`META-INF/*`。
2. `strings -n 4 classes.dex` 全量扫，找：算法名（AES/MD5/SHA）、可疑 base64 串、`flag`/`key`/`seed`/`secret`/`vault` 字样、URL/IP/账号。
3. 定位 flag 相关类（名字含 Flag/Secret/Key），反汇编其方法恢复算法。
4. 本地用 Python（`cryptography`）复现算法，解密拿到 flag。

## 关键经验
- **"声称基于某算法" ≠ 真用该算法**：题目字符串里写着 AES-256-GCM，但 method 表里**根本没有 Cipher/SecretKeySpec 类**时，说明是自实现变换（通常是 XOR 循环），不要被算法名带偏去搞 GCM 爆破。
- **先查 method/class 表有没有标准加密类**再决定走哪条路——grep method 表比盲试省时得多。
- 典型套路：`getFlag() = XOR(readRawResource(platform_cfg), deriveKey())`，`deriveKey = SHA256(签名证书) XOR Base64(硬编码seed)`。签名证书从 `META-INF/*.RSA` 用 `openssl pkcs7 -inform DER -in CTF.RSA -print_certs` 提取。
- **apk 签名块**（"APK Sig Block 42" magic）可藏任意键值对，是常见 flag 藏匿点，别忘了查。

## 无 jadx 环境时的最小 DEX 解析器
自己写解析器只读 header(0x38-0x64)、string/type/proto/field/method/class_def 几张表 + code_item 反汇编即可，几百行 Python。**最容易踩的坑**（都因偏移/格式错导致反汇编错乱）：
- DEX header：0x38 string_ids_size, 0x3c string_ids_off, 0x40 type, 0x48 proto, 0x50 field, 0x58 method, 0x60 class_defs（**别和 0x34 map_off 搞混**）。
- 35c 格式（invoke-*）：`AA BB`（AA=A<<4|G）+ `method_idx` + `CC DD`+`EE FF`（CDEF 是 4 个 4-bit 寄存器）。
- **22c/22t/22s 格式（iget/iput/if-*/add-lit 等）：A 和 B 在同 1 字节的低/高 4 位**（`a=b1&0xf; b=(b1>>4)&0xf`），不是两个独立字节——这是最常见的反汇编错位来源。
- 22x（move/from16）：`AA BBBB`，不是 22c 格式。
- code_item 头 16 字节：regs/ins/outs/tries(4×u16) + debug_info_off(u32) + insns_size(u32)。
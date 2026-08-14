---
name: 二进制静态分析
description: 检查 ELF/二进制文件的硬编码密钥、缺失的缓解措施与可疑字符串
triggers: elf, 二进制, binary, reverse, 反汇编, 静态分析, checksec, objdump, strings, 固件, firmware
---

# 二进制静态分析

## 工具
- `file`：识别架构（x86/ARM/MIPS）、位数、链接方式、是否 strip
- `strings -n 6`：提取可读字符串
- `binwalk`：扫描内嵌文件/签名

## 检查项
- 硬编码密码、API key、token、URL
- 可疑函数名或错误信息（system、exec、/bin/sh、flag）
- 缺失的缓解措施（stack canary / NX / PIE / RELRO / FORTIFY——checksec 不可用时手动判断）
- 内嵌归档、证书、脚本

## 输出
返回 JSON findings：vuln_type、severity、confidence、description、target path、evidence source tool

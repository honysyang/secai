---
name: Electron Asar Reverse License
description: Electron .app 逆向：无 node 手工解析 asar + 复现混淆许可证解密
triggers: electron, asar, reverse, license, 许可证, 逆向, .app, snapflow
category: rev
---

# Electron .app 逆向（找 License Token / 隐藏 flag）

适用于 macOS .app 附件（Electron 打包）的静态逆向。**全程无需 node/npm**，纯 Python 即可完成。

## 1. 解包结构

- `.app` 本质是 ZIP（可用 unzip 解出 `SnapFlow.app/Contents/...`）。
- 关键位置：
  - `Contents/Info.plist`：应用元信息
  - `Contents/MacOS/<AppName>`：Electron 启动器（Mach-O，无需分析）
  - `Contents/Resources/app.asar`：**真正的应用代码**（主进程 main.js、preload.js、renderer/*）
  - `Contents/Frameworks/Electron Framework.framework`：Electron 运行时，可忽略

## 2. 无 node 手工解析 app.asar

asar 格式（全部小端 UInt32）：
```
[0:4]   = 4（pickle 尺寸，固定）
[4:8]   = header pickle 尺寸（= 8 + header string size）
[8:12]  = header string size（JSON 字符串长度，含 4 字节对齐填充）
[12:16] = JSON 对象尺寸（真实 JSON 长度）
[16:16+header_string_size] = JSON 文件树（尾部有空字节填充，用 json.JSONDecoder().raw_decode 只吃前 1805 字节）
之后    = 文件数据区，base = 16 + header_string_size
```

文件树 JSON：每个节点要么有 `files`（目录），要么有 `offset`+`size`（文件，offset 相对文件数据区 base）。按 `base+offset` 切片即可提取全部文件。

## 3. 分析 JS 找 License Token

- 对 main.js / renderer.js 先 `grep -iE "license|token|decrypt|key|rot13|r13|flag|secret"` 定位。
- 常见套路（本题即此）：加密字节数组 `_ENC` + 混淆密钥派生 `_deriveKey()`（ROT13 字符串拼接、异或/移位常数伪装成 decoy 注释）+ 位置相关 XOR 解密（`rot = i % 7` 循环右移再 `^ key[i % keyLen]`）。
- 注意**死代码/诱饵**：opaque predicate（`_P1.._P4` 恒真/恒假）、fake feature flags、unreachable 分支——真正的逻辑在可达代码里，逐行按语义复刻（decoy 变量如 `r1` 要跳过，注释里标注 "the real value" 的才是真值）。
- 关注 IPC：`ipcMain.handle('xxx')` 返回对象里常有开发 BUG 泄漏（如 `license.token` 字段），注释里会有 `BUG: ... accidentally exposed` / `TODO: remove token` 字样，直接指向 flag 位置。

## 4. 复现解密

把 `_deriveKey()` 与 `_decryptLicense()` 逐语句翻译成 Python（ROT13、`x.toString(16)` 即 `format(x,'x')`、`>>>` 即逻辑右移），打印结果即为 license token / flag。rot13 对密钥字符串要按字符逐一应用（字母才转）。

## 复用要点

- 先 grep 关键词再精读，不要从头通读大 JS。
- 混淆常数藏在表达式里（如 `m3 - m1` 是 decoy、`m1 ^ m2` 是真值），翻译时按"注释说 real value"的为准，并独立验证 hex 结果长度是否合理。
- asar 文件树解析函数可复用：`offset` 是字符串要 int()，目录节点无 offset。
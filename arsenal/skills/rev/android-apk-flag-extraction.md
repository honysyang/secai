---
name: Android Apk Flag Extraction
description: APK 找 flag 全流程：解包→定位 dex→字符串表→反汇编→XOR/Base64 还原
triggers: apk, dex, android, 反编译, 字符串表, fill-array-data, XOR, base64
category: rev
---

# Android APK 逆向找 flag 方法论

适用于"下载 APK 附件找泄露 flag"类题目。核心思路：flag 通常不在明文，而是藏在某个类的静态字段或方法返回值中，经过简单变换（XOR / Base64 / hex）保护。

## 1. 解包与定位

```bash
curl -s -o app.apk <url>   # 靶机可能直接返回 APK
unzip -q -o app.apk
```

APK 是多 dex 结构。**自定义应用类通常集中在最小的 dex 或独立 dex 中**（classes2/classes3...），而 classes.dex 往往只是 androidx/kotlin 库。优先处理小 dex，字符串数量少、目标明确。

## 2. 提取 dex 字符串表（无 jadx/apktool 时的纯 Python 方案）

dex header 偏移（注意各字段偏移）：
- 56: string_ids_size / 60: string_ids_off
- 64: type_ids / 72: proto_ids / 80: field_ids / 88: method_ids / 96: class_defs

```python
def read_uleb128(data, off): ...
# string_ids 每项 4 字节指向 string_data_item（uleb128 长度 + utf8 + \0）
```

或直接用 `strings -n 6 <dex>` 粗筛，再用脚本精确提取。**优先搜字符串表中的可疑常量**：类名（如 XxxProvider/Config/Secret）、`ENC`、密钥样字符串、方法名（getToken/getFlag/decode/hexStr）。

## 3. 反汇编关键方法

自己写轻量 disasm（按 opcode→format 映射推进 code_unit），重点解析：
- `const-string` / `const-string/jumbo` → 密钥或密文
- `fill-array-data` → 数组字面量（常用作密文字节）
- `new-array` + 循环 + `xor-int/2addr` + `rem-int` → XOR 解码循环（i % keylen）
- `sget-object` 静态字段 → 目标数据源

关键：**code_item 头是 16 字节**（regs/ins/outs/tries 各 u2，debug_off u4，insns_size u4），insns 从 code_off+16 开始。

## 4. 提取 fill-array-data 载荷

指令 `fill-array-data vX, +off`：载荷在指令地址 + off 处。格式：
- u2 ident = 0x0300
- u2 element_width
- u4 size
- 之后 size×width 字节数据

拿到字节数组后，结合 key 尝试 XOR / Base64 / 反转等常见变换还原 flag。

## 5. 常见陷阱

- 字符串表 `strings` 命令输出会有大量噪音（框架类名、乱码），用 `-n` 加大最小长度，或直接解析 string_ids 精确提取。
- dex 解析器 header 偏移写错会导致整个解析错乱（表现为 class_data/method 索引越界）。
- 寄存器解码错了不用怕：方法逻辑（XOR 循环、fill-array-data）仍可从操作码序列推断。

相关：[[rev-static-analysis]]、[[xor-decoding]]
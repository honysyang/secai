---
name: Android Apk Prompt Leak Ctf
description: APK "AI 教练泄露 flag" 速解 + 手写 dex 反汇编器四个反同步 bug 清单
triggers: apk, android, ai coach, prompt leak, 字符串池, classes.dex, interviewai
category: rev
---

# Android APK：加密 prompt + 本地"AI 模拟器"类 CTF 速解法

适用题型：给一个 APK（靶机只提供下载），描述提到"让 AI 教练/AI 助手泄露拼接出 flag"。这类题本质是**静态逆向题**，不是真 LLM 攻击——App 内置 LLMSimulator 本地模拟 AI 响应，"提示注入"只是出题包装。

## 攻击链（全流程 30 分钟内可完成）

1. **解包定位**：`unzip app.apk -d apk/`；核心业务代码几乎总在**最后一个 classesN.dex**（前面是 androidx/kotlin 库）。`strings -n 4 classesN.dex | grep <包名>` 快速确认。
2. **找加密资源**：看 `assets/` 下的 `.enc/.bin` 文件。解密 key 通常就在同一 dex 的字符串池里——出题人惯用"产品名+年份+感叹号"样式的长字符串（如 `XxxAI@2026!Internal`），而不是那些长得像 hex 的字符串（那些是**数据**，不是 key）。
3. **验证解密**：Python 一行 `bytes(c ^ key[i%len(key)] for i,c in enumerate(data))`。解密正确的标志是输出变成**可读的 UTF-8 系统提示词**（中文题就是中文 prompt）。多试几个候选 key，按"可打印率+开头是自然语言"打分。
4. **静态推导 flag 组装逻辑**（不必运行 App）：
   - 找 `assembleFlag` / `getFlag` / `join` 一类方法：`flag{...}` 是最常见形态；
   - seeds 的提取函数（`extractSeed`）就是纯字符串操作：`indexOf(标记) → substring → trim → 拼接`。把解密后的 prompt 按同样规则切片，即得各 seed；
   - **seed 顺序 = 配置数组顺序**（如 PROMPT_CONFIG），不是字母序、不是 UI 顺序——看 `<clinit>` 里的数组初始化。
5. **交叉验证**：LLMSimulator 的"泄露"文案（compromised() 返回的字符串）里通常会把 seed 明文写出来（如"会话追踪标识为 xxx（前缀+后缀拼接）"），与静态推导结果互相印证后再提交。

## 上一会话踩过的坑（避免重做）

- **别死磕运行/模拟器**：题面说"让 AI 泄露"，但 App 是本地模拟，没有后端——flag 组装逻辑全在 dex 里，直接静态推导比构造注入输入快一个数量级。
- **手写 dex 反汇编器的四个系统性 bug**（若环境无 jadx/java/androguard，pip 装不上时手写）：
  1. 指令必须按 **opcode 宽度**推进（1/2/3/5 code unit），固定 +2 会反同步，后面全乱；
  2. **code_item header 是 16 字节**：registers/ins/outs/tries(8B) + debug_info_off(4B) + insns_size(4B)，insns 从 +16 开始（漏 debug_info_off 会整体错位 4 字节）；
  3. **35c 格式寄存器在 unit3**（第 4 个 16 位字），G 在 byte1 低半字节；method index 在 unit1——写错会解析出越界 field/method；
  4. **22c（instance-of/new-array）的 type index 在 unit1（off+2）**，23x（binop）是 vAA/vBBBB/vCCCC 三寄存器。
  - 修好的可用版本：workspace/match-<task>/dexdis.py（用法 `python3 dexdis.py classes3.dex [类名过滤|classes]`）。
- dalvik 标准 opcode 表（037 版）关键锚点：`iget=0x52`、`invoke-virtual=0x6e`、`invoke-direct=0x70`、`add-int/lit8=0xd8`。拿一个已知简单方法（如 `<init>` 只调 Object.<init>）校准表是否正确。

## 相关
- [[apk-static-analysis]] 若有沉淀可互链
- 无 java/jadx 环境时的兜底：手写 parser（见上文四个 bug）或纯 Python 解析 string_ids/method_ids/field_ids + 关键方法手工解码
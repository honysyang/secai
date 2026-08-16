---
name: Llm Agent Controlled Model Endpoint
description: 用户可指定模型 base_url 的 LLM Agent 服务攻击面：捕获 system prompt→伪造 tool_use→任意读
triggers: llm agent, mcp, base_url, model endpoint, 模型端点, 劫持
category: web
---

# 攻击"模型端点由用户指定"的 LLM Agent 服务

场景：Web 服务把用户上传的源码包交给一个 AI Agent（如 Claude Code CLI + MCP 工具侧车）做自动化审计/处理，且 **模型 API 的 base_url / api_key 由调用者（攻击者）提交**。服务端只做一次 HEAD 连通性检查，然后以 Anthropic Messages 格式 POST 到该 base_url。攻击者因此能**完全观测并伪造模型响应**——这是该题型的主轴。

## 攻击面清单（按优先级）

1. **捕获完整请求体**：架一个 HTTP 服务器（本地 8000 端口），把收到的 POST /v1/messages 全文落盘。**system prompt 里可能直接内嵌 flag**（如 `--append-system-prompt` 注入的 `deployment-tag=flag{...}`），全文 grep `flag{...}}`，agent 真实执行该工具并把 tool_result 发回，循环推进。结果逐条落盘（results.log）。可一次 job 驱动几十个工具调用（读文件/列目录/跑内部接口）。
3. **MCP 任意读的 pathlib 绝对路径特性**：`Path(REPORTS_DIR) / stripped` 当 stripped 以 `/` 开头时，pathlib 的绝对路径拼接会直接得到绝对路径（`/app/reports` 被丢弃）。若只校验 `.txt` 后缀，`read_file('/absolute/path/x.txt')` 即可读系统上任意 .txt（权限以内）。**注意：这是常规路径穿越检查（如 ../）拦不住的特例，必须单独测试**。
4. **tar 解压保留符号链接**：上传的 tar 里可放叶符号链接（如 `x.txt -> /proc/self/environ`、`s.txt -> /app/任意文件`），配合只查后缀的 read_file 即可读任意文件（含 /proc 信息：cmdline/environ/映射）。CLI 自带 Read 工具若基于 realpath 做 allow/deny 校验，符号链接会被拒，但 MCP read_file 往往只查后缀，两套权限边界要分别枚举。
5. **全文件系统枚举**：MCP file_list 可列任意目录（realpath+listdir）。**优先 file_list("/") 扫全根**——flag 常被放在 `/challenge/`、`/opt/`、`/srv/` 等"答案目录"，而不是只在 /app。题目的 deny 列表、平台 hint 常让人死磕困难路径（如 setuid 提权），但直接读答案目录可能秒杀。根目录下的诱饵 flag 文件（值不同）要识别出来，用提交接口验证而非猜。
6. **内部接口利用**：若 MCP 暴露 `_pyrun` 之类内部工具（exec 一个沙箱），先用黑盒误差原语刻画沙箱：
   - 用 NameError 文本回显被过滤后的标识符（`print(chr(65))` → NameError 显示过滤结果），可精确还原字符白名单。
   - 用 `KeyError` 回显任意表达式的值（如 `__builtins__.__dict__[<expr>]` → KeyError: repr(<expr>)）。
   - 部署的沙箱可能与源码不一致（eval globals 是 builtins 模块 dict 时 `__name__=='builtins'`），以黑盒为准。

## 提交纪律

- 疑似 flag 立即提交让平台判定（错误不扣分、重复幂等），不要本地反复猜。
- 每拿一个 flag 立即登记并写阶段快照，多 flag 题继续沿攻击面延伸。

相关：[[llm-prompt-injection]]、[[web-file-read-rce-chain]]、[[multiflag-exhaustion]]
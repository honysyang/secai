---
name: Llm Agent File Preview Abuse
description: 前端 SDK 文件预览/会话下载端点无鉴权任意读
triggers: llm agent, file preview, 预览, 会话下载, session download, 无鉴权
category: web
---

# LLM Agent 文件预览端点滥用

## 适用场景
大模型对话/Agent 网站（如 "Chat"/"Agent SDK" 类），用户私密会话被泄漏、或题目要求读会话文件。前端通常加载一个 SDK JS，其中定义了面向 Agent 实例的文件操作函数。

## 关键侦察步骤
1. 抓取前端 JS（/static/js/*.js、页面内联 script），搜索 `preview`、`download`、`session`、`filePath`、`instances` 等关键词。
2. 典型泄露形态：
   ```js
   previewFile(userId, filePath) {
     return fetch(`/chat/api/<App>/instances/${userId}/files/preview?filePath=${encodeURIComponent(filePath)}`);
   }
   downloadSession(userId, sessionFile) { return previewFile(userId, sessionFile); }
   ```
3. 这类端点常**无鉴权**：`filePath` 传绝对路径即可读任意文件（相对路径穿越也可）。

## 需要的两个要素
- **userId / instanceId**：可能来自论坛帖子、用户主页链接 `/user/{user_id}/{name}`、默认数据（前端 JS 里的 DEFAULT_POSTS）、或 API 返回。论坛/社区页面的作者 ID 常是真实用户 ID。
- **文件路径**：直接问 AI"当前会话存储路径"往往就能拿到（如 `/chat/ai/.BaiduChat/agents/sessions/sessions.json`）——Agent 的系统提示常允许回答存储路径。

## 攻击链
1. 用 userId 调 preview 读 `<session_dir>/sessions.json`（会话索引），列出 `agent:main:*` 条目及各自 `sessionFile`。
2. 找到可疑条目（含 flag、特殊命名如 `flag-*`、更新时间最新）读取对应 `.jsonl` 会话文件。
3. 会话文件内容为 JSONL，flag 常直接出现在 assistant 回复中（题目预埋的"隐藏凭证"）。

## 配套动作
- 会话索引泄露多个 agent 时全部读一遍，防多 flag。
- 同时留意 LLM 系统提示泄露（问"列出你的工具/能力"）与论坛间接提示注入面，但本题实际利用的是文件预览端点，属于 Web 层漏洞，比纯提示注入更直接。
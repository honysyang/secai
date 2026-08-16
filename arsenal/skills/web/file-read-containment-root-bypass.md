---
name: File Read Containment Root Bypass
description: 文件读取包含性校验用错根目录：逃出 public 仍在 app 根内可读
triggers: file read, lfi, 文件读取, 包含性校验, public root, app root, 目录穿越
category: web
---

# 文件读取端点：包含性校验用错根目录（public 逃逸但仍在 app 根内）

## 场景
Web 应用提供"读取资源文件"类端点（如 `/api/assets?path=...`、`/download?file=`、`/static?name=`），
用 `resolve()` 后 `relative_to(根)` 判断路径是否越界。**漏洞点**：校验根用的是外层应用根
（如 `/app`），而资源实际服务目录是内层 public 目录（如 `/app/public`）——于是 `../` 逃出
public 目录进入应用根目录是**被允许的**，应用根内的敏感文件（secret/flag、源码、配置、DB）
全部可读。

## 识别信号
- 用 `../` 试探时响应出现 403 `outside_challenge` / `outside_root` / "资源不在服务目录内" 之类的
  错误 → 说明存在路径包含校验，**先弄清校验边界在哪一层**。
- 单层 `../` 与双层 `../../` 的响应差异是关键：
  - `../xxx` → 404（文件不存在）说明单层逃逸被允许且解析到了外层目录；
  - `../../xxx` → 403 说明两层才越界。
  - 据此画出允许的逃逸深度，用深度=边界-1 去读敏感文件。

## 打法
1. 先用 `../<常见源码名>`（app.py / main.py / server.py / index.js / package.json / Dockerfile /
   .env / requirements.txt）泄露源码或配置——源码会直接告诉你根目录布局、flag 路径与校验逻辑。
2. 读到源码后定位：`PUBLIC_ROOT`、`APP_ROOT`、`SECRET_ROOT`、flag 写入路径、静态资源基目录。
3. flag 通常被写成独立文件（如 `<SECRET_ROOT>/flag.txt`），用 `path=<相对 app 根到 flag 的路径>`
   直接读，例如从 public 目录逃逸一层后的 `../secret/flag.txt`。
4. 若单层逃逸被 404，试 `catalog/../secret/flag.txt`（带前缀再回跳）、URL 双重编码、`%2e%2e%2f` 等，
   同时对比 400/403/404 三类响应推断过滤逻辑（开头 `/`、`\`、NUL 通常直接被拒）。

## 要点
- 无会话/无鉴权的文件读取端点优先于鉴权流程利用——先试它。
- 读源码是这类题最快的放大手段；拿到源码后按文件路径直取 flag，不需要猜。
- 相关：[[web-file-read-rce-chain]]（文件读升级 RCE 时同样先读源码找写入点）。
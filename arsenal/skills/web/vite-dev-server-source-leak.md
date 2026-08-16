---
name: Vite Dev Server Source Leak
description: Vite dev server 模块图内源码全可读，含"私有"组件与 sourcemap
triggers: vite, dev server, source leak, 源码泄露, sourcemap, /@vite/client
category: web
---

## Vite 开发服务器源码泄露（module-graph 内文件全部可读）

**场景**：目标是 Vite dev server（页面 HTML 里有 `/@vite/client`、`<script type="module" src="/src/main.tsx">`、`/@react-refresh` 注入即确认）。生产构建版（assets/*.js）不含此面，但平台可能同时部署 dev 与 build 两个端口，优先看 dev。

**根因**：Vite 按 URL 路径服务源码模块，`/src/` 下的任何文件——只要在模块图里（被 import 过）——都能直接 GET 到，且带 transformed JS + sourcemap（sourceMappingURL 里的 base64 sourcesContent 可直接解码出原始源码）。"未注册路由 / 私有组件"不影响可读性：只要某处 import 了它（如路由文件里故意 import 以触发 HMR 预热、或 Tree-shaking 保留注释），路径就存在。

**打法**：
1. GET 首页 → 拿 `/src/main.tsx` → 逐层拉 App/router/组件，注意**源码注释**（`// PRIVATE`、`// do not publish`、`// not registered to any route`、`// pre-warm` 等）会直接指向隐藏文件。
2. 对可疑组件直接 GET `/src/components/<Name>.tsx`（大小写敏感，按 import 语句原样拼路径）。
3. 文件末尾 base64 sourcemap 解码 `sourcesContent` 字段可拿到未混淆原始代码（含被 Vite transform 包装隐藏的字符串）。
4. flag 常以 `const INTERNAL_TOKEN = 'flag{...}'` / API key / 环境变量形式躺在"不渲染"的组件或配置里。

**排查面扩展**：Vite 还可能有 `?raw` / `/@fs/` 任意文件读（版本相关），源码可读时先看 package.json / vite.config 判断版本再决定是否试已知 CVE。

**相关**：[[web-enumeration]]、[[llm-prompt-injection]]（若平台声称"AI 生成 + 沙箱预览"，代码注入与源码泄露常并存）。
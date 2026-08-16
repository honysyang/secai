---
name: Second Order Sqli Waf Unicode Bypass
description: 二阶 SQLi：JSON \uXXXX 全转义绕存储侧 WAF，拼接查询 UNION 回显
triggers: second-order, sqli, 二阶, 二次注入, waf, unicode, libpq, union
category: web
---

# 二阶 SQL 注入：存储型 label 注入 + JSON \uXXXX 绕过黑名单 WAF

## 适用场景
- 应用有「创建/发布」与「查询关联/详情」两组端点：发布时对输入字段做 SQLi 黑名单校验（拦 `' ( ) # ; % = ` -- /* */ 0x 及 select/union/from/where/sleep/benchmark/load_file/information_schema 等关键字），但查询端点把**存储的字段值**以字符串拼接方式写进 SQL（未参数化）。
- 典型特征：字段值以反斜杠结尾时，关联查询返回 0 条（连自己都匹配不到）——SQL 里 `'\'` 把闭合引号转义成字面量，字符串未闭合/语义改变；双引号字段值无此现象，证明查询用单引号拼接而非参数绑定。

## 攻击步骤
1. **定位注入点**：发布一个 label 结尾带 `\` 的便签，再查其关联；若 count=0（而非 1）→ 拼接型 SQLi。
2. **绕过发布 WAF**：WAF 常扫描**原始请求体**字符串而非解析后的 JSON 值。把 label 的每个字符都写成 `\uXXXX`（JSON 合法转义，原始 body 里没有任何黑名单子串），解析后即还原成 payload。此方法同时绕过单字符黑名单与关键字黑名单（如 `\u0073elect` → select）。
3. **构造注入**：`' UNION SELECT <expr> #`（`#` 注释尾部，MySQL/MariaDB）。`--`、`#` 等被 WAF 拦的字符同样用 \uXXXX 编码即可。
4. **结果回显**：若查询端点把 SELECT 结果行渲染进响应数组（如 related[]），UNION 的每一行都会出现在响应里，相当于**非盲注直读**。逐条提取：版本 `@@version`、库 `database()`、表 `information_schema.tables`、列 `information_schema.columns`、数据 `CONCAT(...)`。
5. **多 flag 注意**：数据库里常见 `flags` 表放**假 flag** 做诱饵（如 "keep digging" 类文案、gateway 占位符），真 flag 常在配置表（key-value 表如 app_config / settings / config 的 value 字段）或隐藏便签里。拿到一个 flag{...} 先提交判定，再继续枚举其余表。

## 关键检测技巧
- 用合法 ref/token（如 seed-0001）查询与用随机 hex 查询的差异：关联查询可能接受非随机格式的 token，说明种子数据的 token 可枚举。
- 响应数组长度可作为布尔盲注/行数判断（count 字段）。
- WAF 黑名单可通过「逐字符 \uXXXX 全转义」100% 绕过，只要应用在解析 JSON 后才做查询、而 WAF 在解析前扫原始文本。

关联：[[web-sqli]]、[[web-rce-playbook]]
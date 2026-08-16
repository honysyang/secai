---
name: Default Password Pattern Derivation
description: 归纳初始密码生成规则（前缀+日期+校验位）→定位从未改密账号
triggers: default password, 初始密码, 密码规则, 校验位, 改密, BaiduPass, 统一认证
category: web
---

# 初始密码规则归纳与"从未改密账号"定位

场景：统一认证/SSO 门户，登录有 PoW + 限速 + 锁定 + 统一失败响应，盲爆无效。但门户泄露了
初始密码的构成线索与员工元数据。核心思路：**不爆密码，而是把密码算出来**。

## 攻击链

1. **找密码构成样例**：关注 `/api/onboarding`、`/api/sample`、`/docs`、JS 里的提示链接等
   "新人初始密码说明"。若返回若干 `(username, emp_id, join_date, initial_password)` 示例，
   说明密码是按内部规则生成的，示例就是归纳用的样本。
2. **归纳生成规则**：
   - 拆分样例密码的固定部分（前缀、日期、分隔符）与可变部分（校验位）。
   - 日期部分常见为入职日期（`MMDDYY` / `YYYYMMDD` / `MMDD`）。
   - 校验位通常由员工元数据算出，优先尝试：**数字和（digit sum）对 26/36/10 取模**、
     奇偶位数字和、乘积、Luhn、CRC、base36 编码。
   - 用 3+ 组样例同时验证，规则必须对所有样例一致才算成立。
   - 常见好猜规则：`char1 = base36(sum(emp_id 数字) % 36)`，`char2 = base36(sum(日期数字) % 36)`。
3. **拿员工目录**：`/api/directory`、`/api/users` 等端点泄露 `emp_id / join_date / role /
   status / pwd_last_set`。**`pwd_last_set == join_date` 且 status=active 的账号 = 从未改过初始密码**，
   是首选目标；disabled 账号一般排除；demo/sandbox 账号可能给假 flag。
4. **按规则算目标账号初始密码**，程序化完成登录：
   - 先拉 PoW 挑战（`POST /api/pow/new`），本地暴力 nonce 使 `sha256(prefix:nonce)` 前导
     `difficulty` 个零（difficulty 通常 3-5，纯 Python 秒级可解）。
   - 再 `POST /api/login` 携带 `{username, password, pow:{prefix,difficulty,exp,sig,nonce}}`。
   - 遇到 `RATE_LIMITED` 稍等重试；成功响应直接带 flag。

## 要点

- 失败响应统一化 ≠ 无法登录：只要密码可**计算**而非**猜测**，PoW/限速/锁定都不是障碍。
- 多个样例可交叉验证规则，避免把巧合当规则；推导出的密码先用样例账号回验一遍再打真实目标。
- 若有 sandbox 账号（如 `SANDBOX` 响应码），先试它验证登录链路可用，再打真实账号。
- 目录接口常是前端 `details/summary` 里"团队目录"加载的，直接看 HTML/JS 就能发现。
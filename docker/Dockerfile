# SECAI 多智能体安全攻防框架 —— Docker 镜像（优化体积，目标 < 3G）
#
# 基础镜像 python:3.12-slim（Debian），体积 ~130M。
# 工具分层安装：精简集覆盖 Web 基本盘 + 二进制基础 + 内网基础，扩展集补重型工具。
#
# 运行需要 VPN 内网时，启动容器请加：
#   --cap-add NET_ADMIN --device /dev/net/tun
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# 是否安装完整安全工具集（默认 0=精简集，1=扩展集；与 build-docker.sh 保持一致）
ARG SEC_TOOLS_FULL=0

# 换国内镜像源（加速 apt/pip 下载；海外构建环境可删除本段）
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# ① 基础依赖：网络/VPN 工具（必需，体积小）
# sudo：connect_vpn 工具会探测 sudo 提权路径（容器内以 root 运行也可直接 openvpn）
RUN apt-get update && apt-get install -y --no-install-recommends \
        openvpn iproute2 iputils-ping curl ca-certificates dnsutils netcat-openbsd sudo \
    && rm -rf /var/lib/apt/lists/*

# ② 安全 CLI（精简集，Debian trixie 官方源）
#   Web：nmap/sqlmap/ffuf/gobuster/masscan
#   二进制：gdb/binutils
#   密码/内网：hydra/john/hashcat
# 注：nuclei/amass/feroxbuster 见下方 ②c（GitHub Release 下载）
#     nikto/wpscan（需 Perl/Ruby 运行时）与 radare2（体积大）暂不内置
RUN apt-get update && apt-get install -y --no-install-recommends \
        nmap sqlmap ffuf gobuster masscan \
        gdb binutils \
        hydra john hashcat \
    && rm -rf /var/lib/apt/lists/*

# ②b 下载工具解压依赖
RUN apt-get update && apt-get install -y --no-install-recommends unzip \
    && rm -rf /var/lib/apt/lists/*

# ②c Go/Rust 单二进制工具（GitHub Release 下载；Kali 专有、不在 Debian trixie 源）
#   nuclei：Web 漏洞扫描器；amass：子域枚举；feroxbuster：递归目录爆破
#   逐项容错：下载失败仅告警，不阻断镜像构建
RUN { \
        _v=$(curl -sL https://api.github.com/repos/projectdiscovery/nuclei/releases/latest 2>/dev/null | grep -oP '"tag_name":\s*"v?\K[0-9.]+' | head -n1); \
        if [ -n "$_v" ]; then \
            curl -sL -o /tmp/nuclei.zip "https://github.com/projectdiscovery/nuclei/releases/download/v${_v}/nuclei_${_v}_linux_amd64.zip" \
            && unzip -qo /tmp/nuclei.zip -d /usr/local/bin/ && rm -f /tmp/nuclei.zip && echo "[ok] nuclei ${_v}"; \
        else echo "[skip] nuclei 版本获取失败"; fi; \
    } || echo "[skip] nuclei 下载失败"; \
    { curl -sL -o /tmp/amass.zip https://github.com/owasp-amass/amass/releases/latest/download/amass_Linux_amd64.zip \
        && unzip -qo /tmp/amass.zip -d /usr/local/bin/ && rm -f /tmp/amass.zip && echo "[ok] amass"; } \
        || echo "[skip] amass 下载失败"; \
    { curl -sL -o /tmp/feroxbuster.zip https://github.com/epi052/feroxbuster/releases/latest/download/x86_64-linux-feroxbuster.zip \
        && unzip -qo /tmp/feroxbuster.zip -d /usr/local/bin/ && rm -f /tmp/feroxbuster.zip && echo "[ok] feroxbuster"; } \
        || echo "[skip] feroxbuster 下载失败"

WORKDIR /app

# ③ 基础 Python 依赖（先装，利用 Docker 层缓存）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ④ Python 安全包（二进制/内网核心，精简集；pip 均有 wheel，无需编译）
#   pwntools：pwn/exploit 开发；ropper：ROP gadget 查找；impacket：内网协议/票据
RUN pip install --no-cache-dir pwntools ropper impacket

# ⑤ 复制项目（.dockerignore 已排除 .env / vpn / data / 日志 / 缓存等敏感与运行时内容）
COPY . .

# 运行时数据目录（建议运行容器时挂载宿主 data/ 持久化）
RUN mkdir -p /app/data

# ⑥ 扩展安全工具（可选，构建时 --build-arg SEC_TOOLS_FULL=1 启用，体积会显著增大）
#   metasploit-framework 同样不在 Debian trixie 源（Kali 专有），故省略
#   angr/slither/mythril 为 pip 包，安装失败不阻断镜像构建（|| true 容错）
RUN if [ "$SEC_TOOLS_FULL" = "1" ]; then \
        pip install --no-cache-dir angr slither-analyzer mythril || true; \
    fi

# 8000 端口供 app.server（实时可视化前端）使用；默认 CMD 仍是跑分入口 app.main
# 托管模式下平台可覆盖 CMD，默认仍进入跑分入口
EXPOSE 8000
ENTRYPOINT ["python3"]
CMD ["-m", "app.main"]

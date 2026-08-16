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

# 国内镜像源（加速 apt/pip；海外构建环境请删除本段）
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && sed -i 's|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# ① 基础依赖 + 安全 CLI 精简集 + 解压工具（合并为一次 apt-get update，减少等待）
#   网络/VPN：openvpn iproute2 iputils-ping curl ca-certificates dnsutils netcat-openbsd sudo
#   Web：nmap sqlmap ffuf gobuster masscan
#   二进制：gdb binutils
#   密码/内网：hydra john hashcat
#   解压：unzip（用于 ② 中 GitHub 二进制）
# 注：nikto/wpscan（需 Perl/Ruby 运行时）与 radare2（体积大）暂不内置
RUN apt-get update && apt-get install -y --no-install-recommends \
        openvpn iproute2 iputils-ping curl ca-certificates dnsutils netcat-openbsd sudo \
        nmap sqlmap ffuf gobuster masscan \
        gdb binutils \
        hydra john hashcat \
        unzip \
        openjdk-21-jre-headless apktool \
        nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# ② Go/Rust 单二进制工具（GitHub Release 下载；Kali 专有、不在 Debian trixie 源）
#   nuclei：Web 漏洞扫描器；amass：子域枚举；feroxbuster：递归目录爆破
#   使用国内 ghproxy 镜像加速 GitHub Release；并发下载进一步减少等待
#   逐项容错：下载失败仅告警，不阻断镜像构建
ARG GH_PROXY=https://ghp.ci
RUN set -e; \
    dl_one() { \
        local name="$1"; local url="$2"; local out="$3"; local mode="$4"; \
        { \
            curl -fsSL -o "$out" "$url" \
            && case "$mode" in \
                zip) unzip -qo "$out" -d /tmp/dl_"$name" && find /tmp/dl_"$name" -maxdepth 3 -type f -executable -exec cp {} /usr/local/bin/"$name" \; ;; \
                *) cp "$out" /usr/local/bin/"$name" && chmod +x /usr/local/bin/"$name" ;; \
            esac \
            && echo "[ok] $name" ; \
        } || echo "[skip] $name 下载失败"; \
    }; \
    mkdir -p /tmp/dl_nuclei /tmp/dl_amass /tmp/dl_feroxbuster; \
    _v=$(curl -sL "${GH_PROXY}/https://api.github.com/repos/projectdiscovery/nuclei/releases/latest" 2>/dev/null \
        | grep -oP '"tag_name":\s*"v?\K[0-9.]+' | head -n1); \
    [ -n "$_v" ] && echo "nuclei version=$_v" || echo "nuclei version detect failed"; \
    ( [ -n "$_v" ] && dl_one nuclei \
        "${GH_PROXY}/https://github.com/projectdiscovery/nuclei/releases/download/v${_v}/nuclei_${_v}_linux_amd64.zip" \
        /tmp/dl_nuclei/nuclei.zip zip ) & \
    dl_one amass \
        "${GH_PROXY}/https://github.com/owasp-amass/amass/releases/latest/download/amass_Linux_amd64.zip" \
        /tmp/dl_amass/amass.zip zip & \
    dl_one feroxbuster \
        "${GH_PROXY}/https://github.com/epi052/feroxbuster/releases/latest/download/x86_64-linux-feroxbuster.zip" \
        /tmp/dl_feroxbuster/feroxbuster.zip bin & \
    wait; \
    rm -rf /tmp/dl_*; \
    ls -lh /usr/local/bin/nuclei /usr/local/bin/amass /usr/local/bin/feroxbuster 2>/dev/null || true

# ②-2 反编译与运行时工具（jadx 单二进制 + asar 全局包）
#   jadx：Android APK 反编译为 Java/Smali；asar：Electron 解包；npm：Node 供应链/CI 投毒
RUN set -e; \
    mkdir -p /tmp/jadx && \
    curl -fsSL -o /tmp/jadx/jadx.zip \
        "https://github.com/skylot/jadx/releases/download/v1.4.7/jadx-1.4.7.zip" \
    && unzip -qo /tmp/jadx/jadx.zip -d /opt/jadx \
    && ln -s /opt/jadx/bin/jadx /usr/local/bin/jadx \
    && ln -s /opt/jadx/bin/jadx-gui /usr/local/bin/jadx-gui \
    && rm -rf /tmp/jadx; \
    npm install -g asar

WORKDIR /app

# ③ 基础 Python 依赖 + Python 安全包（合并为一层，减少层数并复用缓存）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir pwntools ropper impacket

# ④ 复制项目（.dockerignore 已排除 .env / vpn / data / 日志 / 缓存等敏感与运行时内容）
COPY . .

# 运行时数据目录（建议运行容器时挂载宿主 data/ 持久化）
RUN mkdir -p /app/data

# ⑤ 扩展安全工具（可选，构建时 --build-arg SEC_TOOLS_FULL=1 启用，体积会显著增大）
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

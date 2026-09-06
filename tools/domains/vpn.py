"""VPN 域：远程靶场接入（OpenVPN 后台启用 + 隧道真实建立验证）。

工具域模块：按功能域划分的工具实现。
- connect_vpn：读取 .env 配置后台启用 OpenVPN，验证 tun0 真正建立（避免误报 connected）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from agents import RunContextWrapper, function_tool

from adapters.config import VPN_AUTH, VPN_CMD, VPN_CONFIG
from core.task_context import TaskContext
from core.tool_pipeline import DEFAULT_PIPELINE, with_pipeline


@function_tool
@with_pipeline(DEFAULT_PIPELINE)
def connect_vpn(ctx: RunContextWrapper[TaskContext]) -> str:
    """当任务目标需要走 VPN/内网（如远程靶场）时调用，后台启用 OpenVPN 并验证隧道真正建立。

    读取 .env 里的 VPN_CONFIG（.ovpn 绝对路径，可选 VPN_AUTH / VPN_CMD）。
    openvpn 创建 tun0 需要 CAP_NET_ADMIN：优先 sudo -n（免密），否则依赖已 setcap 的 openvpn；
    跑完后验证 tun0，未创建则报错（避免 --daemon 后台 fork 成功但隧道未建立时误报 connected）。
    """
    c = ctx.context
    if c.vpn_connected:
        return json.dumps({"connected": True, "already": True, "config": VPN_CONFIG},
                          ensure_ascii=False)
    if not VPN_CONFIG:
        return json.dumps({"error": "未配置 VPN_CONFIG（.env 中缺少 .ovpn 路径），无法启用 VPN"},
                          ensure_ascii=False)
    if not Path(VPN_CONFIG).exists():
        return json.dumps({"error": f"VPN 配置文件不存在：{VPN_CONFIG}"}, ensure_ascii=False)

    base = [VPN_CMD, "--config", VPN_CONFIG]
    if VPN_AUTH:
        base += ["--auth-user-pass", VPN_AUTH]

    # 探测是否可免密 sudo（openvpn 创建 tun0 需要 root / CAP_NET_ADMIN）
    use_sudo = False
    if shutil.which("sudo"):
        try:
            probe = subprocess.run(["sudo", "-n", "true"], capture_output=True,
                                   text=True, timeout=5, check=False)
            use_sudo = (probe.returncode == 0)
        except Exception:
            use_sudo = False

    cmd = (["sudo", "-n"] if use_sudo else []) + base + ["--daemon"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except Exception as e:
        return json.dumps({"error": f"VPN 启动异常：{str(e)[:200]}"}, ensure_ascii=False)

    if p.returncode != 0:
        return json.dumps({
            "error": f"VPN 启动失败（rc={p.returncode}）：{p.stderr[:300]}",
            "hint": "openvpn 创建 tun0 需要 root 权限，请执行：sudo setcap cap_net_admin,cap_net_raw+ep /usr/sbin/openvpn",
        }, ensure_ascii=False)

    # 验证 tun0 隧道真正建立（--daemon 后台 fork 成功不代表 tun0 创建成功）
    time.sleep(2)
    tun_ok = False
    try:
        r = subprocess.run(["ip", "addr", "show", "tun0"], capture_output=True,
                           text=True, timeout=5, check=False)
        tun_ok = (r.returncode == 0 and "tun0" in r.stdout)
    except Exception:
        tun_ok = False

    if not tun_ok:
        return json.dumps({
            "error": "openvpn 进程已启动但 tun0 未创建（无权限创建 TUN 设备）。",
            "hint": "请执行：sudo setcap cap_net_admin,cap_net_raw+ep /usr/sbin/openvpn，然后重试",
            "sudo_used": use_sudo,
        }, ensure_ascii=False)

    c.vpn_connected = True
    return json.dumps({"connected": True, "command": " ".join(cmd),
                       "config": VPN_CONFIG, "tun": "tun0"}, ensure_ascii=False)

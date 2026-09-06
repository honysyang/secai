"""内置 demo engagement fixture —— 无 LLM key 离线也能展示 2-3 个目标运行态。

场景与前端 apps/web/src/runtime/demo.ts 对齐（同一叙事，三目标并行）：
- sess-a-demo：10.10.5.2 · demo.ine.local（running，周期性事件 + projection 直播）；
- sess-b-demo：10.10.5.0/24 · 横向发现（awaiting_approval，挂 approval/requested
  帧可 POST /api/respond 回响裁决）；
- sess-c-demo：vulnapp.example（completed，证据链 + report/growth 投影全量，
  且为 /api/report 提供 R5 报告引擎的纯函数输入）。

会话头 engagementId 取 'live-engagement'（= 前端 AppRuntime.LIVE_ENGAGEMENT_ID）：
当 apps/web 以 DEMO_MODE=false 真实通道联调时，host/session-added 可被前端登记
展示（upsertSession 按此 id 收口），实现"离线可展示"闭环。

事件统一经 core/events BUS 发射（state 订阅回调落 SessionRec.events 供回放），
projection/queue/approval 为结构化直发帧；C 会话同时注册 TargetProfile →
R5 报告引擎（profiles/practical_pentest/report/engine.py）桥接数据源。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from pentest.deadends import DeadEnd
from pentest.hypothesis import Hypothesis
from pentest.target_profile import (
    AttackChain,
    AttackSurface,
    Identity,
    PortFinding,
    TargetProfile,
    WebFinding,
)
from profiles.practical_pentest.report import EngagementSnapshot

if TYPE_CHECKING:
    from server.state import AppState, SessionRec

# 前端真实通道（LIVE_ENGAGEMENT_ID）与 demo 会话 id（demo.ts 常量）
DEMO_ENGAGEMENT_ID = "live-engagement"
SESSION_A = "sess-a-demo"
SESSION_B = "sess-b-demo"
SESSION_C = "sess-c-demo"
APPROVAL_RPC = "rpc-101"  # B 会话挂起的审批（respond 原样回响）

ENGAGEMENT_TITLE = "渗透演练：demo.ine.local 集群侦察与提权"


def _now_offset(offset_ms: int) -> str:
    """相对当前时刻的 ISO 时间（毫秒偏移，负值=过去），demo.ts iso(offset) 语义。"""
    return (datetime.now(timezone.utc) + timedelta(milliseconds=offset_ms)).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------
def install_demo(state: "AppState") -> None:
    """把三会话 demo engagement 安装进编排面（幂等：重复调用先清空旧注册）。"""
    state.create_engagement(ENGAGEMENT_TITLE, engagement_id=DEMO_ENGAGEMENT_ID)
    state.add_session(SESSION_A, "10.10.5.2 · demo.ine.local", DEMO_ENGAGEMENT_ID, "running")
    state.add_session(SESSION_B, "10.10.5.0/24 · 横向发现", DEMO_ENGAGEMENT_ID, "awaiting_approval")
    state.add_session(SESSION_C, "vulnapp.example", DEMO_ENGAGEMENT_ID, "completed")
    _compose_a(state)
    _compose_b(state)
    _compose_c(state)


# ---------------------------------------------------------------------------
# 会话 A：running（周期性事件 + projection）
# ---------------------------------------------------------------------------
QUEUE_A: list[dict[str, Any]] = [
    {"id": "h-a1", "hypothesisId": "hyp-a1", "statement": "Web 应用运行带已知 CVE 的框架版本", "status": "testing", "priority": 9, "attempts": 2},
    {"id": "h-a2", "hypothesisId": "hyp-a2", "statement": "8000/Jetty 默认上下文存在未授权端点", "status": "pending", "priority": 7, "attempts": 0},
    {"id": "h-a3", "hypothesisId": "hyp-a3", "statement": "管理子域接口存在弱口令", "status": "pending", "priority": 5, "attempts": 0},
]

ATTACK_SURFACE_A: dict[str, Any] = {
    "targets": ["demo.ine.local"],
    "ports": [
        {"port": 22, "protocol": "tcp", "service": "ssh", "version": "OpenSSH 8.9p1", "state": "open"},
        {"port": 80, "protocol": "tcp", "service": "http", "version": "nginx 1.24.0", "state": "open"},
        {"port": 443, "protocol": "tcp", "service": "https", "version": "nginx 1.24.0", "state": "open"},
        {"port": 8000, "protocol": "tcp", "service": "http", "version": "Jetty 11.0.15", "state": "open"},
    ],
}

DEAD_ENDS_A: list[dict[str, Any]] = [
    {
        "id": "de-a1", "hypothesisId": "hyp-a0",
        "statement": "LDAP 389 匿名绑定可枚举用户",
        "reason": "389 未开放，636 要求客户端证书",
        "overturnCondition": "开放 389/636 且允许匿名查询时复活",
        "decidedAt": _now_offset(-600),
    },
]


def _compose_a(state: "AppState") -> None:
    sid = SESSION_A
    state.push_queue(sid, QUEUE_A)
    state.emit_event(
        sid, "message", role="assistant",
        content=("任务已接受：授权目标 demo.ine.local（10.10.5.2），约束 = 先被动再主动、"
                 "禁 DoS、时间窗 60 分钟。计划：服务发现 → 指纹 → 已知 CVE 验证，全程留存证据。"),
    )
    state.emit_event(sid, "subagent", name="port-scanner", role="子任务：端口/服务枚举", state="running")
    state.emit_event(sid, "tool/call", tool="nmap-scan", args="-sV -p- --open 10.10.5.2")
    state.emit_event(
        sid, "tool/output", tool="nmap-scan", ok=True,
        output=("PORT     STATE SERVICE  VERSION\n22/tcp   open  ssh      OpenSSH 8.9p1 Ubuntu\n"
                "80/tcp   open  http     nginx 1.24.0\n443/tcp  open  https    nginx 1.24.0\n"
                "8000/tcp open  http     Jetty 11.0.15"),
    )
    state.emit_event(
        sid, "message", role="assistant",
        content="服务面收敛：22/80/443/8000。80 与 443 同源 nginx，8000 是独立的 Jetty —— 优先指纹 Web 应用与 Jetty 上下文。",
    )
    state.emit_event(sid, "subagent", name="banner-grab", role="子任务：横幅/指纹采集", state="done")
    state.emit_event(sid, "tool/call", tool="http-probe", args="GET https://demo.ine.local/")
    state.emit_event(
        sid, "tool/output", tool="http-probe", ok=True,
        output="HTTP/1.1 302 Found\nLocation: /login?next=/\nServer: nginx\n→ 应用根路径跳登录，无版本泄露；下一步枚举静态资源与常见框架路径。",
    )
    state.emit_event(
        sid, "message", role="assistant",
        content="跳转 /login，Cookie 属性健康。假设：前端框架或网关存在已知 CVE；继续抓 8000/Jetty 指纹。",
    )
    state.push_projection(sid, {"attack_surface": ATTACK_SURFACE_A})
    state.push_projection(sid, {"dead_ends": DEAD_ENDS_A})


def demo_tick(state: "AppState", tick: int) -> None:
    """running 会话周期性活跃：事件 + projection 帧（demo.ts pumpActivity 语义）。"""
    rec = state.sessions.get(SESSION_A)
    if rec is None or rec.status != "running":
        return
    variant = tick % 3
    state.emit_event(SESSION_A, "subagent", name="cve-matcher", role="子任务：CVE 特征比对", state="running")
    if variant == 0:
        state.emit_event(
            SESSION_A, "message", role="assistant",
            content="后台比对 8000/Jetty 与 nginx 特征库…暂无新命中，维持假设 h-a1 验证中。",
        )
        state.emit_event(
            SESSION_A, "tool/output", tool="cve-lookup", ok=True,
            output="CVE-2021-34429 (Jetty) 需共享缓存前缀 → 条件不满足，排除。",
        )
    elif variant == 1:
        state.emit_event(
            SESSION_A, "tool/output", tool="http-probe", ok=True,
            output="GET /login 200 · 页面引用 /static/app.js（前端框架版本待提取）。",
        )
    else:
        state.emit_event(
            SESSION_A, "message", role="assistant",
            content="指纹库覆盖正常；继续对 h-a2（Jetty 默认上下文）做路径枚举。",
        )
    state.emit_event(SESSION_A, "subagent", name="cve-matcher", role="子任务：CVE 特征比对", state="done")
    state.push_projection(
        SESSION_A,
        {"attack_surface": ATTACK_SURFACE_A, "activity": {"cycle": tick, "note": f"第 {tick} 轮后台活动已落账"}},
    )


# ---------------------------------------------------------------------------
# 会话 B：awaiting_approval（挂审批帧，可 respond）
# ---------------------------------------------------------------------------
QUEUE_B: list[dict[str, Any]] = [
    {"id": "h-b1", "hypothesisId": "hyp-b1", "statement": "存活主机存在 SSH 弱口令（字典 top-500）", "status": "pending", "priority": 8, "attempts": 1},
]


def _compose_b(state: "AppState") -> None:
    sid = SESSION_B
    state.push_queue(sid, QUEUE_B)
    state.push_projection(
        sid,
        {
            "attack_surface": {
                "targets": ["10.10.5.0/24 · 8 台存活"],
                "ports": [
                    {"port": 22, "protocol": "tcp", "service": "ssh", "state": "open", "note": "8/8 存活主机"},
                    {"port": 445, "protocol": "tcp", "service": "smb", "state": "open", "note": "3 台"},
                ],
            }
        },
    )
    state.emit_event(
        sid, "message", role="assistant",
        content="网段发现完成：10.10.5.0/24 内 8 台存活（.7/.12/.19/.23/.31/.44/.52/.60），22 全开、3 台有 445。",
    )
    state.emit_event(sid, "subagent", name="lateral-sweep", role="子任务：横向口令/服务探测", state="waiting")
    state.emit_event(sid, "tool/call", tool="ping-sweep", args="-sn 10.10.5.0/24")
    state.emit_event(
        sid, "tool/output", tool="ping-sweep", ok=True,
        output="Nmap scan report … 8 hosts up\n10.10.5.7  10.10.5.12  10.10.5.19  10.10.5.23\n10.10.5.31  10.10.5.44  10.10.5.52  10.10.5.60",
    )
    state.emit_event(
        sid, "message", role="assistant",
        content="口令探测（hydra，22 端口 top-500 字典）超出 passive 边界，已发起人工审批——批准后以单主机 5/s 限速执行。",
    )
    state.request_approval(
        sid,
        APPROVAL_RPC,
        action="执行登录口令探测（hydra）",
        description=("对 10.10.5.0/24 存活主机的 22 端口执行弱口令字典探测（top-500，单主机限速 5/s，"
                     "超时 10 分钟）。该动作超出任务书 passive 强度，需人工放行。"),
        detail={"tool": "hydra", "hosts": 8, "limit": "5/s", "timeout": "10min", "scope": "10.10.5.0/24"},
    )


def respond_aftermath(state: "AppState", decision: str) -> None:
    """respond 裁决后的后续脚本（demo.ts respond 语义：放行 → hydra；拒绝 → 免审批路径）。"""
    sid = SESSION_B

    async def _run() -> None:
        if decision == "allow":
            await asyncio.sleep(0.5)
            state.emit_event(
                sid, "message", role="assistant",
                content="审批已放行——hydra 以单主机 5/s 限速开始（top-500，10 分钟超时）。",
            )
            await asyncio.sleep(1.0)
            state.emit_event(sid, "tool/call", tool="hydra", args="-L users.txt -P top500.txt ssh://10.10.5.0/24")
            await asyncio.sleep(1.2)
            state.emit_event(
                sid, "tool/output", tool="hydra", ok=True,
                output="10.10.5.12 ssh: login: backup password: Backup#2024 [1/2]\n10.10.5.23 ssh: login: admin password: ChangeMe123 [2/2]\n\n→ 2 组弱口令命中，等待人工确认后进入验证阶段。",
            )
            await asyncio.sleep(1.0)
            state.emit_event(
                sid, "message", role="assistant",
                content="弱口令命中 2 组（.12/.23），已留证据。按任务书约定将弱口令验证列入下阶段——如目标网段属授权范围则转入，否则终止。",
            )
        else:
            await asyncio.sleep(0.5)
            state.emit_event(
                sid, "message", role="assistant",
                content="口令探测被拒——改走服务版本 → 已知 CVE 的免审批路径。",
            )
            await asyncio.sleep(1.0)
            state.emit_event(
                sid, "tool/output", tool="cve-verify", ok=True,
                output="OpenSSH 8.9p1 → CVE-2023-38408 需特定依赖，非默认；标记待人工复核。",
            )
            await asyncio.sleep(1.0)
            state.emit_event(sid, "message", role="assistant", content="已按审批意见收敛为低风险验证并继续。")

    state.spawn(_run())


def steer_reply(state: "AppState", session_id: str, text: str) -> None:
    """steer 指令 → 追加 user 事件 + 延迟 assistant 应答（demo.ts send 语义）。"""
    replies = (
        f"收到指令「{text}」。已纳入当前验证队列，完成后回报证据与结论。",
        f"收到「{text}」——该路径在授权范围内，转入验证并留证。",
        f"理解：「{text}」。涉及的动作级别未超出任务书约束，开始执行。",
    )

    async def _run() -> None:
        await asyncio.sleep(1.2)
        state.emit_event(
            session_id, "message", role="assistant",
            content=replies[hash(text) % len(replies)] if text else replies[0],
        )

    state.spawn(_run())


# ---------------------------------------------------------------------------
# 会话 C：completed（report/growth 投影全量 + R5 报告引擎桥接输入）
# ---------------------------------------------------------------------------
QUEUE_C: list[dict[str, Any]] = [
    {"id": "h-c1", "hypothesisId": "hyp-c1", "statement": "Actuator 未授权暴露内部端点", "status": "validated", "priority": 9, "attempts": 3},
    {"id": "h-c2", "hypothesisId": "hyp-c2", "statement": "Tomcat 管理口存在弱口令", "status": "falsified", "priority": 6, "attempts": 2},
]


def _compose_c(state: "AppState") -> None:
    sid = SESSION_C
    state.push_queue(sid, QUEUE_C)
    state.push_projection(
        sid,
        {
            "attack_surface": {
                "targets": ["vulnapp.example"],
                "ports": [
                    {"port": 8080, "protocol": "tcp", "service": "http", "version": "Tomcat 9.0.78", "state": "open"},
                    {"port": 8443, "protocol": "tcp", "service": "https", "version": "Tomcat 9.0.78", "state": "open"},
                ],
            }
        },
    )
    state.push_projection(
        sid,
        {
            "dead_ends": [
                {
                    "id": "de-c1", "hypothesisId": "hyp-c2",
                    "statement": "Tomcat 管理口存在弱口令",
                    "reason": "manager 路径全部 403，未开放 manager/html",
                    "overturnCondition": "暴露 /manager 且存在默认凭据时复活",
                    "decidedAt": _now_offset(-240_000),
                }
            ]
        },
    )

    ev: dict[str, str] = {}
    state.emit_event(
        sid, "message", role="assistant",
        content="授权目标 vulnapp.example：先做端口与应用面侦察，命中假设后逐条验证并留证。",
    )
    state.emit_event(sid, "subagent", name="web-enum", role="子任务：Web 应用枚举", state="running")
    ev["scan"] = state.emit_event(sid, "tool/call", tool="port-scan", args="-sV 8443,8080 vulnapp.example")
    ev["scan_out"] = state.emit_event(
        sid, "tool/output", tool="port-scan", ok=True,
        output="8080/tcp open http Tomcat 9.0.78\n8443/tcp open https Tomcat 9.0.78\n→ /actuator 端点返回 200（无鉴权）",
    )
    state.emit_event(
        sid, "message", role="assistant",
        content="发现 /actuator 未授权可达——进入假设验证：枚举敏感子端点（env/heapdump/mappings）。",
    )
    ev["curl"] = state.emit_event(sid, "tool/call", tool="curl", args="GET http://vulnapp.example:8080/actuator/env")
    ev["curl_out"] = state.emit_event(
        sid, "tool/output", tool="curl", ok=True,
        output=('HTTP/1.1 200 OK\n{\n  "propertySources": [ "systemProperties", "applicationConfig" ],\n'
                '  "env": { "spring.datasource.url": "jdbc:postgresql://…", "spring.datasource.username": "**REDACTED**" }\n}\n\n'
                "→ 配置键泄露（值已脱敏），确认未授权访问成立。"),
    )
    state.emit_event(sid, "subagent", name="web-enum", role="子任务：Web 应用枚举", state="done")
    state.emit_event(sid, "subagent", name="cve-matcher", role="子任务：CVE 特征比对", state="running")
    ev["cve"] = state.emit_event(sid, "tool/call", tool="cve-verify", args="Tomcat 9.0.78 / CVE-2023-42793")
    ev["cve_out"] = state.emit_event(
        sid, "tool/output", tool="cve-verify", ok=True,
        output="CVE-2023-42793 特征匹配失败（无 /manager 且无 .war 上传面）→ 排除；Actuator 未授权独立成证。",
    )
    ev["probe"] = state.emit_event(sid, "tool/call", tool="http-probe", args="GET https://vulnapp.example/login")
    ev["probe_out"] = state.emit_event(
        sid, "tool/output", tool="http-probe", ok=True,
        output="HTTP/1.1 200 OK · 响应头核查：未带 Content-Security-Policy → 低危缺失项单独成证。",
    )

    # 渗透链路（link 事件，与真实 runner pentest_target._link_node/_link_edge 同构）：
    # 意图根 → 侦察资产 → 确认事实 → 漏洞。供前端「渗透」探索链路 DAG 数据源。
    def _ln(kind: str, nid: str, key: str, label: str, detail: str = "", status: str = "open") -> None:
        state.emit_event(sid, "link", variant="node",
                         node={"id": nid, "kind": kind, "key": key, "label": label,
                               "detail": detail, "status": status})

    def _le(frm: str, to: str) -> None:
        state.emit_event(sid, "link", variant="edge", edge={"from": frm, "to": to})

    _ln("intent", "intent-brief", "brief", "对 vulnapp.example 授权渗透", "确认一个已确认漏洞即可收尾", "confirmed")
    _ln("asset", "asset-vulnapp", "vulnapp.example", "vulnapp.example（目标）", "8080/http, 8443/https Tomcat 9.0.78", "confirmed")
    _ln("fact", "fact-port8080", "port-8080", "8080/http Tomcat 9.0.78 开放", "port-scan：8080/tcp open http", "confirmed")
    _ln("fact", "fact-actuator", "actuator-env", "/actuator/env 未授权返回配置键", "curl GET /actuator/env → 200，propertySources 泄露", "confirmed")
    _ln("fact", "fact-cve-fail", "cve-42793", "CVE-2023-42793 特征不匹配", "无 /manager 且无 .war 上传面 → 排除", "falsified")
    _ln("vuln", "vuln-actuator", "vuln-actuator", "Spring Boot Actuator 未授权访问", "/actuator/env 泄露配置键（数据库地址/用户）", "confirmed")
    _ln("vuln", "vuln-version", "vuln-version", "Tomcat 版本信息泄露", "8443/8080 暴露 9.0.78 banner", "confirmed")
    _le("intent-brief", "asset-vulnapp")
    _le("asset-vulnapp", "fact-port8080")
    _le("fact-port8080", "fact-actuator")
    _le("fact-actuator", "vuln-actuator")
    _le("asset-vulnapp", "fact-cve-fail")
    _le("fact-cve-fail", "vuln-version")

    state.emit_event(
        sid, "message", role="assistant",
        content="结论收敛：确认 1 条高价值 finding（Actuator 未授权）、1 条中危（管理端口带版本暴露）、1 条低危（缺 CSP）。已生成结构化报告投影。",
    )
    state.emit_event(
        sid, "message", role="assistant",
        content="任务结束：负面结论一并归档（管理口弱口令已证伪），供 L6 学习层沉淀。",
    )

    state.push_projection(
        sid,
        {
            "evidence": {
                "chains": [
                    {
                        "findingId": "f-c1",
                        "title": "Spring Boot Actuator 未授权访问（/actuator/env 泄露配置键）",
                        "severity": "high",
                        "nodes": [
                            {"id": "n1", "at": _now_offset(-480_000), "label": "端口扫描发现 8080/http", "kind": "scan", "detail": "Tomcat 9.0.78 上的 Spring Boot 应用"},
                            {"id": "n2", "at": _now_offset(-360_000), "label": "假设记录并进入验证", "kind": "hypothesis", "detail": "/actuator 200 无鉴权 → 进入 env 验证"},
                            {"id": "n3", "at": _now_offset(-300_000), "label": "GET /actuator/env 200", "kind": "tool", "detail": "返回 propertySources + 配置键（值脱敏）"},
                            {"id": "n4", "at": _now_offset(-240_000), "label": "确认未授权访问", "kind": "finding", "detail": "写入 finding，关联配置泄露面"},
                        ],
                    },
                    {
                        "findingId": "f-c2",
                        "title": "8443/8080 暴露 Tomcat 版本（信息泄露）",
                        "severity": "medium",
                        "nodes": [
                            {"id": "n1", "at": _now_offset(-450_000), "label": "banner 采集", "kind": "scan", "detail": "Server: Apache-Coyote/1.1 + 错误页版本戳"},
                            {"id": "n2", "at": _now_offset(-300_000), "label": "版本矩阵比对", "kind": "verify", "detail": "CVE-2023-42793 特征不匹配，降级为信息泄露"},
                        ],
                    },
                ]
            }
        },
    )
    state.push_projection(
        sid,
        {
            "report": {
                "status": "ready",
                "summary": "vulnapp.example 授权测试完成：确认 1 条高危（Actuator 未授权）、1 条中危（版本信息泄露）与 1 条低危（无 CSP 响应头）；负面结论已归档。",
                "sections": [
                    {"id": "s1", "title": "执行摘要", "body": "目标 8080/8443 由 Tomcat 9.0.78 承载 Spring Boot 应用。/actuator 未授权可达并泄露配置键（数据库地址/用户），未做进一步利用。"},
                    {"id": "s2", "title": "攻击面概览", "body": "8443/https（主入口）、8080/http（actuator）。均暴露 Tomcat 9.0.78 版本信息。"},
                    {"id": "s3", "title": "已排除攻击面（负面结论）", "body": "1) Tomcat 管理口弱口令——manager 未开放，排除。2) CVE-2023-42793——无 .war 上传面，排除。"},
                    {"id": "s4", "title": "加固建议", "body": "1) Actuator 移入内网或加鉴权并关闭 env/heapdump；2) 收敛版本指纹；3) 补 CSP 与安全响应头。"},
                ],
                "findings": [
                    {"id": "f-c1", "title": "Spring Boot Actuator 未授权访问", "severity": "high", "summary": "/actuator/env 未鉴权返回配置键。", "evidence": ["GET /actuator/env → 200", "propertySources 含 systemProperties/applicationConfig"]},
                    {"id": "f-c2", "title": "Tomcat 版本信息泄露", "severity": "medium", "summary": "8443/8080 暴露 9.0.78。", "evidence": ["Server: Apache-Coyote/1.1"]},
                    {"id": "f-c3", "title": "缺少 CSP 等安全响应头", "severity": "low", "summary": "登录页响应未带 Content-Security-Policy。", "evidence": ["curl -I 响应头核查"]},
                ],
                "generatedAt": _now_offset(-120_000),
            }
        },
    )
    state.push_projection(
        sid,
        {
            "growth": {
                "metrics": {"hitRate": 0.67, "tokenEfficiency": 0.82, "playbookReuse": 0.5},
                "series": [
                    {"label": "R1", "hitRate": 0.4, "tokenEfficiency": 0.55, "playbookReuse": 0.2},
                    {"label": "R2", "hitRate": 0.5, "tokenEfficiency": 0.61, "playbookReuse": 0.3},
                    {"label": "R3", "hitRate": 0.6, "tokenEfficiency": 0.68, "playbookReuse": 0.35},
                    {"label": "R4", "hitRate": 0.62, "tokenEfficiency": 0.74, "playbookReuse": 0.45},
                    {"label": "R5", "hitRate": 0.67, "tokenEfficiency": 0.82, "playbookReuse": 0.5},
                ],
            }
        },
    )

    profile = _build_c_profile(ev)
    state.report_profiles[sid] = profile


def _build_c_profile(ev: dict[str, str]) -> TargetProfile:
    """C 会话的 R5 报告引擎纯函数输入：3 条 validated 链（high/medium/low）+ 1 条死路。"""
    hyps = [
        Hypothesis(hypothesis_id="hyp-c1", statement="Spring Boot Actuator 未授权暴露内部端点",
                   status="validated", priority=9.0, linked_actions=[ev["curl"], ev["curl_out"]],
                   created_at=_now_offset(-360_000)),
        Hypothesis(hypothesis_id="hyp-c2", statement="Tomcat 管理口存在弱口令",
                   status="falsified", priority=6.0, linked_actions=[],
                   created_at=_now_offset(-300_000)),
        Hypothesis(hypothesis_id="hyp-c3", statement="8443/8080 暴露 Tomcat 版本信息",
                   status="validated", priority=6.0, linked_actions=[ev["scan"], ev["scan_out"]],
                   created_at=_now_offset(-300_000)),
        Hypothesis(hypothesis_id="hyp-c4", statement="登录页响应缺少 CSP 安全响应头",
                   status="validated", priority=3.0, linked_actions=[ev["probe"], ev["probe_out"]],
                   created_at=_now_offset(-200_000)),
    ]
    chains = [
        AttackChain(chain_id="chain-actuator", steps=["hyp-c1"], impact="Spring Boot Actuator 未授权访问（/actuator/env 泄露配置键）",
                    evidence_refs=[ev["scan_out"], ev["curl_out"]], severity="high"),
        AttackChain(chain_id="chain-version", steps=["hyp-c3"], impact="8443/8080 暴露 Tomcat 9.0.78 版本（信息泄露）",
                    evidence_refs=[ev["scan_out"]], severity="medium"),
        AttackChain(chain_id="chain-csp", steps=["hyp-c4"], impact="登录页响应缺少 Content-Security-Policy",
                    evidence_refs=[ev["probe_out"]], severity="low"),
    ]
    dead_ends = [
        DeadEnd(dead_end_id="de-c1", path_description="Tomcat 管理口弱口令: /manager 探测 → 403 → 无管理面",
                hypothesis_id="hyp-c2", falsified_at_step=3, falsified_at=_now_offset(-240_000),
                evidence_snapshot="manager/html 路径全部 403", falsification_method="http-probe",
                overturn_condition="暴露 /manager 且存在默认凭据时复活"),
    ]
    return TargetProfile(
        target_id=SESSION_C,
        identity=Identity(hostname="vulnapp.example", ip="192.0.2.20", tech_stack=["Spring Boot", "Tomcat 9.0.78"]),
        scope_ref="scope-live-engagement",
        attack_surface=AttackSurface(
            ports=[
                PortFinding(port=8080, protocol="tcp", service="http", version="Tomcat 9.0.78", confidence="confirmed"),
                PortFinding(port=8443, protocol="tcp", service="https", version="Tomcat 9.0.78", confidence="confirmed"),
            ],
            web=[
                WebFinding(url="http://vulnapp.example:8080/actuator", status_code=200, tech_stack=["spring-boot"]),
                WebFinding(url="http://vulnapp.example:8080/actuator/env", status_code=200, tech_stack=["spring-boot"]),
            ],
        ),
        hypotheses=hyps,
        validated=chains,
        dead_ends=dead_ends,
        coverage={"recon": {"done": 4, "total": 4}, "delivery": {"done": 3, "total": 4}, "exploitation": {"done": 2, "total": 3}},
    )


# ---------------------------------------------------------------------------
# /api/report 桥接：completed 会话 → EngagementSnapshot（报告引擎纯函数输入）
# ---------------------------------------------------------------------------
def build_report_snapshot(state: "AppState", engagement_id: str) -> EngagementSnapshot | None:
    """按任务书聚合 completed 会话的报告输入；无已完成画像会话返回 None（不触发引擎）。"""
    eng = state.engagements.get(engagement_id)
    if eng is None:
        return None
    profiles: list[TargetProfile] = []
    events: list[dict[str, Any]] = []
    for sid in eng.session_ids:
        rec = state.sessions.get(sid)
        if rec is None or rec.status != "completed":
            continue
        profile = state.report_profiles.get(sid)
        if profile is None:
            continue
        profiles.append(profile)
        events.extend(_engine_events(rec))
    if not profiles:
        return None
    return EngagementSnapshot(
        engagement_id=engagement_id,
        profiles=profiles,
        events=events,
        client="SECAI-PT 离线演示",
        started_at=eng.created_at,
        ended_at=eng.updated_at,
    )


def _engine_events(rec: "SessionRec") -> list[dict[str, Any]]:
    """SessionRec.events（SessionEvent 形态）→ 报告引擎兼容形态（event_id/seq/type/data/created_at）。"""
    return [
        {
            "event_id": ev["eventId"],
            "seq": ev["seq"],
            "type": ev["type"],
            "data": ev["data"],
            "created_at": ev["createdAt"],
        }
        for ev in rec.events
    ]


__all__ = [
    "APPROVAL_RPC",
    "DEMO_ENGAGEMENT_ID",
    "ENGAGEMENT_TITLE",
    "SESSION_A",
    "SESSION_B",
    "SESSION_C",
    "build_report_snapshot",
    "demo_tick",
    "install_demo",
    "respond_aftermath",
    "steer_reply",
]

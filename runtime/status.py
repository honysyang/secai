"""任务状态机：维护执行阶段与状态，落盘 status.json 供 UI/调试实时读取。

阶段 phase：legislate(立法) / assign(派任) / execute(执行) / report(收尾)
状态 status：running(进行中) / finish(完成) / error(失败) / interrupted(中断)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from runtime.log import log_info

PHASES = ("legislate", "assign", "execute", "report")
STATUSES = ("running", "finish", "error", "interrupted")
# execute 阶段的子状态（由 Agent 通过 set_phase 工具标记）
SUB_PHASES = ("exploring", "detecting", "exploiting")

# 顶层 kill-chain 阶段机：驱动 Executor 的 instructions 动态切换角色/目标。
# key 是阶段名，goal 是阶段目标，focus 是当前阶段的行为焦点（注入系统提示），
# next 是达成后应切换到的下一阶段（供 Agent 判断 + 代码兜底）。
PHASE_DEFS = {
    "recon": {
        "goal": "摸清目标指纹与技术栈",
        "focus": "非破坏性侦察：指纹（HTTP头/banner/证书/报错/CSP）、资产测绘（域名/IP/端口/路径），用 list_tools/run_tool 跑 nmap/子域枚举；输出资产清单+指纹+置信度，不深入单个漏洞。若黑板/任务书已给出资产清单或指纹，跳过全量枚举，只补缺失缺口，禁止重复等价的广域扫描。",
        "next": "enumerate（已拿到指纹/端口/入口后切换）",
    },
    "enumerate": {
        "goal": "枚举攻击面",
        "focus": "把侦察线索变成可验证攻击面清单：端口/协议/HTTP路径/产品指纹/中间件，归纳入口点与信任边界（输入/鉴权/内外网边界），给 Top-N 优先级+验证建议。",
        "next": "detect（已列出攻击面后切换）",
    },
    "detect": {
        "goal": "漏洞检测",
        "focus": "把候选风险归类为可验证假设（认证绕过/敏感配置暴露/注入类等），用 fuzz/detect_vuln 确认；每条给验证目标+最小证据+正负证据样式，按可复现性排序。",
        "next": "exploit（已确认漏洞后切换）",
    },
    "exploit": {
        "goal": "漏洞利用（拿权限/读文件/执行命令）",
        "focus": "基于已确认漏洞构造最小利用链：先单点验证（能读/能执行/能写），再沿链推进（注入→读文件→RCE→提权）。每次尝试先想清预期正/负响应；拿到初始访问后立即评估提权路径（sudo -l/SUID/内核/容器逃逸）。禁止在未确认效果前把同一思路改参数写成 probe2/probe3。",
        "next": "post（已拿到权限/读文件能力后切换）",
    },
    "post": {
        "goal": "后利用拿 flag",
        "focus": "目标导向：先读 /flag、/flag.txt、/etc/passwd、已知真实文件名；读不到则深入 includes/config.php 拿数据库配置连库查、读合同/文档内容、环境变量。拿到 flag 后 submit_flag，通关 close。",
        "next": "（终态，达成后 finalize）",
    },
}

# 阶段转移图：允许的合法转移（防止 Agent 乱跳）。
# 任意阶段若发现 flag 线索，都可直接切 post（在 hooks 证据自动切里兜底）。
PHASE_TRANSITIONS = {
    "recon":     ["enumerate", "post"],
    "enumerate": ["detect", "recon"],
    "detect":    ["exploit", "enumerate", "post"],
    "exploit":   ["post", "detect"],
    "post":      [],
}


def set_status(workdir: Path, phase: str, status: str, **extra) -> None:
    """写当前阶段/状态到 status.json（覆盖写，供 UI 实时轮询）。"""
    record: Dict[str, Any] = {
        "phase": phase,
        "status": status,
        "ts": int(time.time()),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        **extra,
    }
    (workdir / "status.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    # 状态变化日志：追踪系统当前处于哪个阶段/状态（code/sub/turn 等关键字段）
    detail = " ".join(f"{k}={v}" for k, v in extra.items() if k in ("code", "sub", "turn"))
    log_info(f"状态更新：phase={phase} status={status}"
             f"{' ' + detail if detail else ''}")


def get_status(workdir: Path) -> Dict[str, Any]:
    """读当前状态；不存在或损坏返回空 dict。"""
    p = workdir / "status.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

"""漏洞类型检测注册表：从 vulns/*.yaml 加载「漏洞类型 → 检测规范 + payload」。

每个 YAML 描述一类漏洞的标准打法：
    type: 漏洞类型缩写（SQLI/XSS/SSTI/LFI/RCE/IDOR/SSRF/XXE/UPLOAD）
    name: 中文名
    description: 一句话说明
    need_detect: 前置条件（自然语言描述，供 Agent 判断是否适用）
    prompt: 检测规范（注入 Agent 的检测打法）
    payloads: 基础载荷列表

用法：
    python vuln_registry.py list            # 列出全部漏洞类型
    python vuln_registry.py get <TYPE>      # 查看某类型的检测规范
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import yaml

VULNS_DIR = Path(__file__).parent.parent / "vulns"
PAYLOADS_DIR = Path(__file__).parent.parent / "payloads"


def load_payloads(vuln_type: str) -> List[str]:
    """从 payloads/{type}.txt 读 payload 字典（每行一个，支持 $origin$ 占位符）。

    不存在对应文件时返回空列表，调用方可回退到 YAML 内联 payloads。
    """
    p = PAYLOADS_DIR / f"{vuln_type.lower()}.txt"
    if not p.exists():
        return []
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


@dataclass
class Vuln:
    type: str
    name: str = ""
    description: str = ""
    need_detect: str = ""
    prompt: str = ""
    payloads: List[str] = field(default_factory=list)
    path: Optional[Path] = None


@lru_cache(maxsize=1)
def load_vulns() -> Dict[str, Vuln]:
    """扫描 vulns/*.yaml，返回 {漏洞类型: Vuln}。"""
    vulns: Dict[str, Vuln] = {}
    if not VULNS_DIR.exists():
        return vulns
    for p in sorted(VULNS_DIR.glob("*.yaml")):
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        t = str(d.get("type") or p.stem).strip().upper()
        if not t:
            continue
        vulns[t] = Vuln(
            type=t,
            name=str(d.get("name") or ""),
            description=str(d.get("description") or ""),
            need_detect=str(d.get("need_detect") or ""),
            prompt=str(d.get("prompt") or ""),
            payloads=load_payloads(t) or [str(x) for x in (d.get("payloads") or [])],
            path=p,
        )
    return vulns


def list_vulns() -> List[dict]:
    """列出全部漏洞类型（type/name/description）。"""
    return [
        {"type": v.type, "name": v.name, "description": v.description}
        for v in load_vulns().values()
    ]


def get_vuln(vuln_type: str) -> Optional[Vuln]:
    """按类型缩写取完整检测规范（含 prompt/payloads）。"""
    return load_vulns().get((vuln_type or "").strip().upper())


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in {"list", "ls"}:
        for v in load_vulns().values():
            print(f"{v.type}\t{v.name}\t{v.description}")
    elif args[0] == "get" and len(args) >= 2:
        v = get_vuln(args[1])
        if v is None:
            print(f"未找到漏洞类型：{args[1]}")
        else:
            print(f"== {v.type} {v.name} ==")
            print(f"前置条件：{v.need_detect}")
            print(f"检测规范：\n{v.prompt}")
            print(f"基础载荷：{v.payloads}")
    else:
        print("用法：")
        print("  python vuln_registry.py list")
        print("  python vuln_registry.py get <TYPE>")

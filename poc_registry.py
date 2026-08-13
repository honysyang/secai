"""POC 库：从 pocs/**/*.yaml 加载 CVE 元数据与利用思路，提供检索。

每个 YAML 有两种形态：
  1. 带 poc 段（本仓库手工补充的利用思路）：
       info: {name, cve, summary, severity, affected, references}
       poc:  {type, principle, steps, payload, verification}
  2. 仅 info 段（从 AI-Infra-Guard 批量导入的检测规则）：
       info: {name, cve, summary, severity, rule, references}

用法：
    python poc_registry.py list               # 列出全部 POC
    python poc_registry.py find <关键词>      # 按产品/CVE/摘要检索
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import yaml

POCS_DIR = Path(__file__).parent / "pocs"


@dataclass
class Poc:
    name: str                 # 产品名
    cve: str                  # CVE 编号（无则用文件名）
    summary: str = ""
    severity: str = ""
    affected: str = ""        # affected 或 rule（版本范围）
    references: List[str] = field(default_factory=list)
    poc_type: str = ""        # 有 poc 段才有的漏洞类型
    principle: str = ""
    steps: List[str] = field(default_factory=list)
    payload: str = ""
    verification: str = ""
    path: Optional[Path] = None


def _load_one(path: Path) -> Optional[Poc]:
    try:
        d = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    info = d.get("info")
    if not isinstance(info, dict):
        return None
    poc = d.get("poc") if isinstance(d.get("poc"), dict) else {}
    name = str(info.get("name") or path.parent.name or "")
    cve = str(info.get("cve") or "").strip().upper() or path.stem
    return Poc(
        name=name,
        cve=cve,
        summary=str(info.get("summary") or ""),
        severity=str(info.get("severity") or ""),
        affected=str(info.get("affected") or info.get("rule") or ""),
        references=[str(x) for x in (info.get("references") or [])],
        poc_type=str(poc.get("type") or ""),
        principle=str(poc.get("principle") or ""),
        steps=[str(x) for x in (poc.get("steps") or [])],
        payload=str(poc.get("payload") or ""),
        verification=str(poc.get("verification") or ""),
        path=path,
    )


@lru_cache(maxsize=1)
def load_pocs() -> Dict[str, Poc]:
    """扫描 pocs/**/*.yaml，返回 {CVE 编号: Poc}。"""
    pocs: Dict[str, Poc] = {}
    for p in sorted(POCS_DIR.rglob("*.yaml")):
        poc = _load_one(p)
        if poc is not None:
            pocs[poc.cve] = poc
    return pocs


def find_pocs(query: str, limit: int = 5) -> List[dict]:
    """按产品名/CVE 编号/摘要/漏洞类型检索 POC。"""
    q = (query or "").strip().lower()
    out: List[dict] = []
    for p in load_pocs().values():
        hay = " ".join([p.name, p.cve, p.summary, p.poc_type, p.principle]).lower()
        if q and q not in hay:
            continue
        out.append({
            "name": p.name,
            "cve": p.cve,
            "severity": p.severity,
            "summary": p.summary[:120],
            "affected": p.affected,
            "type": p.poc_type,
            "has_poc": bool(p.poc_type),
            "principle": p.principle[:200],
        })
        if len(out) >= max(1, limit):
            break
    return out


def get_poc(cve: str) -> Optional[Poc]:
    """按 CVE 编号取完整 POC（含 steps/payload）。"""
    return load_pocs().get(cve.strip().upper())


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in {"list", "ls"}:
        pocs = load_pocs()
        print(f"POC 总数：{len(pocs)}")
        for p in list(pocs.values())[:50]:
            print(f"  {p.cve} [{p.severity}] {p.name}: {p.summary[:50]}")
    elif args[0] == "find" and len(args) >= 2:
        for m in find_pocs(args[1]):
            print(f"  {m['cve']} [{m['severity']}] {m['name']}: {m['summary']}")
    else:
        print("用法：")
        print("  python poc_registry.py list")
        print("  python poc_registry.py find <关键词>")

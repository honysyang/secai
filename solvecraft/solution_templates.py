"""解法模板：solved 题的正向解法沉淀 + 同指纹题复用。

运行时数据（data/solution_templates.jsonl），与人工维护的声明式内容（skills/pocs）分离。
机械提取（零 LLM）：solved 后从黑板/已提交 flag/已披露技能抽取「指纹 + 漏洞类型 +
关键路径/payload + 步骤」，写入模板；下次同指纹题命中时把模板摘要注入 brief 作为起手式，
省去重新探索的轮次。

原则：沉淀靠代码不靠 LLM；同指纹去重（hits+1）防膨胀；无指纹不沉淀（无法匹配）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List

DATA_DIR = Path(__file__).parent.parent / "data"
TEMPLATES_FILE = DATA_DIR / "solution_templates.jsonl"

# 指纹抽取：技术栈/框架/漏洞类型关键词（小写匹配，命中即作为指纹）
_FINGERPRINT_WORDS = (
    "thinkphp", "spring", "springboot", "spring boot", "flask", "django",
    "wordpress", "laravel", "fastadmin", "fastjson", "shiro", "struts2",
    "struts", "log4j", "log4shell", "weblogic", "tomcat", "jboss", "jetty",
    "nginx", "apache", "php", "python", "nodejs", "node.js", "express",
    "java", "golang", "go", "redis", "mysql", "postgresql", "mongodb",
    "gitlab", "jenkins", "grafana", "elasticsearch", "vulhub",
    "sqli", "xss", "ssti", "lfi", "rfi", "rce", "idor", "ssrf", "xxe",
    "file upload", "deserialize", "反序列化", "命令注入", "sql injection",
)

# 漏洞类型映射：从技能名/黑板 key 反推统一类型标签（顺序优先，命中即返回）
_VULN_MAP = (
    ("sql", "SQLI"), ("sqli", "SQLI"), ("sql injection", "SQLI"),
    ("ssti", "SSTI"), ("xss", "XSS"), ("xxe", "XXE"),
    ("ssrf", "SSRF"), ("idor", "IDOR"), ("lfi", "LFI"), ("rfi", "RFI"),
    ("rce", "RCE"), ("command injection", "命令注入"), ("命令注入", "命令注入"),
    ("upload", "文件上传"), ("file upload", "文件上传"),
    ("deserialize", "反序列化"), ("反序列化", "反序列化"),
)

# 路径抽取：/ 开头的一串 URL 安全字符（截断到 160，避免整段 payload 塞满模板）
_PATH_RE = re.compile(r"/[A-Za-z0-9_.~%?=&/-]{2,160}")
# 带参数的 URL/payload 特征（? 或 & 后跟参数），作为关键 payload 候选
_PAYLOAD_RE = re.compile(r"/[A-Za-z0-9_.~%?=&/-]*[?=&][A-Za-z0-9_.~%?=&/-]*")
_FLAG_RE = re.compile(r"flag\{[^}\s]{1,200}\}", re.IGNORECASE)


def _load_templates() -> List[Dict[str, Any]]:
    """读全部模板（jsonl 每行一条）。"""
    if not TEMPLATES_FILE.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in TEMPLATES_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
    except Exception:
        pass
    return out


def _save_all(templates: List[Dict[str, Any]]) -> None:
    """整体重写模板文件（去重/合并后统一落盘）。"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with TEMPLATES_FILE.open("w", encoding="utf-8") as f:
            for t in templates:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _extract_fingerprint(desc: str, blackboard: Dict[str, Any]) -> List[str]:
    """从题目描述 + 黑板抽取技术栈指纹（小写关键词命中，去重保序）。"""
    text = (desc or "").lower()
    for k, v in (blackboard or {}).items():
        text += " " + str(k).lower()
        val = v.get("value", "") if isinstance(v, dict) else v
        text += " " + str(val).lower()
    hits = [w for w in _FINGERPRINT_WORDS if w in text]
    return list(dict.fromkeys(hits))[:12]


def _extract_vuln_type(disclosed_skills: List[str],
                       blackboard: Dict[str, Any]) -> str:
    """从已披露技能 + 黑板反推漏洞类型标签。"""
    text = " ".join(disclosed_skills or []).lower()
    for k, v in (blackboard or {}).items():
        text += " " + str(k).lower()
        val = v.get("value", "") if isinstance(v, dict) else v
        text += " " + str(val).lower()
    for kw, vt in _VULN_MAP:
        if kw in text:
            return vt
    return ""


def _extract_paths(blackboard: Dict[str, Any]) -> List[str]:
    """从黑板 value 抽取关键路径（去重保序）。"""
    paths: List[str] = []
    for v in (blackboard or {}).values():
        val = str(v.get("value", "")) if isinstance(v, dict) else str(v)
        for p in _PATH_RE.findall(val):
            if p not in paths:
                paths.append(p)
    return paths[:20]


def _extract_payloads(blackboard: Dict[str, Any]) -> List[str]:
    """从黑板 value 抽取关键 payload：flag 本身 + 带参数的 URL/payload。"""
    payloads: List[str] = []
    for v in (blackboard or {}).values():
        val = str(v.get("value", "")) if isinstance(v, dict) else str(v)
        for f in _FLAG_RE.findall(val):
            payloads.append(f)
        for m in _PAYLOAD_RE.findall(val):
            payloads.append(m[:160])
    return list(dict.fromkeys(payloads))[:20]


def _extract_steps(blackboard: Dict[str, Any]) -> List[str]:
    """从黑板提取已完成的关键步骤（status=done 的 key，作为步骤清单）。"""
    return [str(k) for k, v in (blackboard or {}).items()
            if isinstance(v, dict) and v.get("status") == "done"][:8]


def append_solution_template(code: str, desc: str, ctx: Any) -> None:
    """solved 后机械提取并沉淀解法模板（零 LLM）。

    无指纹不沉淀（无法匹配）；同指纹 + 同漏洞类型去重（hits+1 合并，不新增条目）。
    """
    blackboard = getattr(ctx, "blackboard", {}) or {}
    fingerprint = _extract_fingerprint(desc, blackboard)
    if not fingerprint:
        return
    vuln_type = _extract_vuln_type(getattr(ctx, "disclosed_skills", []), blackboard)
    paths = _extract_paths(blackboard)
    payloads = _extract_payloads(blackboard)
    steps = _extract_steps(blackboard)
    if not (paths or payloads or steps):
        return  # 无实质内容不沉淀

    now = int(time.time())
    templates = _load_templates()
    # 去重：指纹集合完全相同 + 漏洞类型相同 → hits+1 合并
    for t in templates:
        if (set(t.get("fingerprint", [])) == set(fingerprint)
                and t.get("vuln_type") == vuln_type):
            t["hits"] = int(t.get("hits", 0)) + 1
            t["last_seen"] = now
            t["key_paths"] = list(dict.fromkeys(
                t.get("key_paths", []) + paths))[:20]
            t["key_payloads"] = list(dict.fromkeys(
                t.get("key_payloads", []) + payloads))[:20]
            t["steps"] = list(dict.fromkeys(t.get("steps", []) + steps))[:8]
            _save_all(templates)
            return

    templates.append({
        "code": code,
        "fingerprint": fingerprint,
        "vuln_type": vuln_type,
        "key_paths": paths,
        "key_payloads": payloads,
        "steps": steps,
        "hits": 1,
        "last_seen": now,
    })
    _save_all(templates)


def load_solution_hint(code: str, desc: str, max_hits: int = 3) -> str:
    """按题检索解法模板：题目指纹与模板指纹有交集即命中，返回起手式摘要文本。

    按 hits 降序取前 max_hits 个；无命中返回空串。
    """
    templates = _load_templates()
    if not templates:
        return ""
    cur_fp = set(_extract_fingerprint(desc, {}))
    if not cur_fp:
        return ""
    matched = [t for t in templates
               if cur_fp & set(t.get("fingerprint", []))]
    if not matched:
        return ""
    matched.sort(key=lambda t: int(t.get("hits", 0)), reverse=True)

    lines = []
    for t in matched[:max_hits]:
        vuln = t.get("vuln_type") or "未知"
        hits = t.get("hits", 1)
        paths = ", ".join(t.get("key_paths", [])[:5]) or "无"
        payloads = ", ".join(t.get("key_payloads", [])[:3]) or "无"
        steps = " → ".join(t.get("steps", [])[:6]) or "无"
        lines.append(
            f"- 漏洞: {vuln}（同类题命中 {hits} 次）\n"
            f"  关键路径: {paths}\n"
            f"  关键 payload: {payloads}\n"
            f"  步骤: {steps}")
    return "\n".join(lines)

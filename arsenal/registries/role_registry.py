"""角色注册表：从 roles/*.md 加载角色定义，提供前缀派任。

每个角色是一个 Markdown 文件，带 frontmatter：
    ---
    name: 角色名（中文）
    pattern: 匹配规则（正则，可空；空表示 fallback 角色）
    playbooks: 初始技能名，逗号分隔
    ---
    思维风格（正文）

角色 = 思维风格模板 + 初始技能包；「多 Skills 渐进披露」见 skill_registry.py。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

ROLES_DIR = Path(__file__).parent.parent / "roles"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


# 所有角色共用的工具使用提示：如何调用本机安全 CLI 工具（追加进每个角色的思维风格）
TOOL_USAGE_HINT = (
    "\n\n# 工具使用（本机安全 CLI）\n"
    "本机已接入一批安全工具（nmap/sqlmap/ffuf/nuclei/gobuster 等）。"
    "做端口扫描、目录爆破、注入检测、二进制分析等具体动作时，优先：\n"
    "1. list_tools 查看有哪些可用工具；\n"
    "2. get_tool_spec 查目标工具的完整参数；\n"
    "3. run_tool 执行该工具。\n"
    "不要靠 shell 手拼复杂命令，能用结构化工具就用结构化工具。"
)


def _parse_frontmatter(text: str) -> dict:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            meta[k.strip()] = v
    return meta


def load_roles() -> List[dict]:
    """扫描 roles/*.md，返回角色定义列表（保持文件顺序）。"""
    roles: List[dict] = []
    for p in sorted(ROLES_DIR.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        meta = _parse_frontmatter(text)
        body = _FRONTMATTER_RE.sub("", text).strip()
        roles.append({
            "pattern": meta.get("pattern", ""),
            "role": meta.get("name", p.stem),
            "style": body,
            "playbooks": [x.strip() for x in meta.get("playbooks", "").split(",") if x.strip()],
        })
    return roles


def assign_role(code: str, description: str = "",
                evidence_override: str = "") -> dict:
    """派任：先按 code 前缀（锚定 pattern）匹配题型，再按描述关键词匹配阶段角色。"""
    target = evidence_override or f"{code} {description}"
    roles = load_roles()
    fallback: dict | None = None

    def _build(r: dict, matched_by: str) -> dict:
        # 所有角色统一注入「武器库导航」基础技能（告知 POC/载荷/知识/工具等资产怎么用）
        playbooks = ["arsenal_index"] + [p for p in r["playbooks"] if p != "arsenal_index"]
        return {"role": r["role"], "style": r["style"] + TOOL_USAGE_HINT,
                "playbooks": playbooks, "matched_by": matched_by}

    # 第一遍：锚定 pattern（^ 开头，code 前缀题型），最具体
    for r in roles:
        pattern = r["pattern"]
        if not pattern:
            fallback = r
            continue
        if pattern.startswith("^") and re.search(pattern, target, re.I):
            return _build(r, "evidence" if evidence_override else "prefix")

    # 第二遍：非锚定 pattern（描述关键词 / 阶段角色 / AI 安全）
    for r in roles:
        pattern = r["pattern"]
        if not pattern or pattern.startswith("^"):
            continue
        if re.search(pattern, target, re.I):
            return _build(r, "evidence" if evidence_override else "keyword")

    fallback = fallback or {
        "role": "通用侦察兵",
        "style": "你面对未知目标：先指纹（服务/框架/版本），再攻击面枚举（端口/路径/参数/功能点），后小步验证（差分实验），证据驱动不臆测，失败方向不重复。",
        "playbooks": ["unknown_target_sop"],
    }
    return _build(fallback, "fallback")

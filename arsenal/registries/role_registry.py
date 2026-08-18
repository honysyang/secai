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
from functools import lru_cache
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
    "不要靠 shell 手拼复杂命令，能用结构化工具就用结构化工具。\n"
    "4. 需要 CVE 检索/POC 库/漏洞知识库/差分实验时，先 list_disabled_tools 查看未挂载"
    "的工具组，再用 enable_tool 挂载对应组（poc/vuln/knowledge/web）后使用。"
)


# 核心常驻 playbook：通用纪律/导航类技能，开局就解锁且全程在线
# （arsenal_index=武器库导航；token_optimizer=上下文经济；prompt_optimizer=精准指令）。
# 与 skill_registry.CORE_SKILLS 保持一致（同一组技能名，勿单改）。
CORE_PLAYBOOKS = ["arsenal_index", "token_optimizer", "prompt_optimizer", "scoring_runner"]


# 所有角色共用的第一性原理探索提示：技能库无现成打法时的兜底方法论。
# 常驻注入（追加到 role.style），不占 playbook 名额——playbook 走渐进披露，
# 会被 load_skill_bodies 的 3 篇同屏预算挤掉，兜底方法论必须全程在线。
# FIRST_PRINCIPLES_HINT = (
#     "\n\n# 第一性原理探索分析\n"
#     "1. 技术栈反推：确认语言/框架/中间件后，按该栈最高危面列候选漏洞，"
#     "不依赖技能库是否命中（如 PHP→文件包含/反序列化，Node→原型链/SSRF）。\n"
#     "2. 差异实验为主武器：distinguish 定攻击面，fuzz 归组找差异点"
#     "——差异点即未知漏洞的第一性证据。\n"
#     "3. 每个候选漏洞 = 可验证假设 + 最小探测 + 正/负证据。"
# )


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


@lru_cache(maxsize=1)
def load_roles() -> List[dict]:
    """扫描 roles/*.md，返回角色定义列表（保持文件顺序）。

    lru_cache(maxsize=1)：角色文件运行时不变，避免热路径（每个工具结果事件
    的 _boost_role_by_trigger）反复磁盘 IO + YAML/md 解析（A6 修复）。
    """
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
            "trigger": meta.get("trigger", ""),
        })
    return roles


def assign_role(code: str, description: str = "",
                evidence_override: str = "") -> dict:
    """派任：先按 code 前缀（锚定 pattern）匹配题型，再按描述关键词匹配阶段角色。"""
    target = evidence_override or f"{code} {description}"
    roles = load_roles()
    fallback: dict | None = None

    def _build(r: dict, matched_by: str) -> dict:
        # 所有角色统一注入核心常驻 playbook（导航 + 提效纪律），开局就解锁且全程在线
        playbooks = CORE_PLAYBOOKS + [p for p in r["playbooks"] if p not in CORE_PLAYBOOKS]
        return {"role": r["role"],
                "style": r["style"] + TOOL_USAGE_HINT,
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

"""技能库：技能的发现、检索与创建。

技能是 skills/ 目录下的 Markdown 文件，带可选的 YAML 风格 frontmatter：

    ---
    name: 显示名（可选，检索用；缺省用文件名 stem）
    description: 一句话说明这个技能
    triggers: 关键词1, 关键词2, 关键词3
    ---

    正文（打法内容，会被注入执行者的系统提示）

约定：
  - 技能标识 = 文件名去掉 .md（唯一，如 filter_bypass、sql_injection）；
  - 支持子目录分类（如 vulnerabilities/sql_injection.md → category=vulnerabilities）；
  - description / name / triggers 参与 find_skills 检索；
  - triggers 是「渐进披露」的触发关键词（命中事件流后自动解锁该技能）。

新增技能：往 skills/ 放一个符合格式的 .md 文件（可放子目录），运行时自动发现；
也可用命令行创建：`python skill_registry.py create <name> <description> --triggers "a,b,c"`。
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

SKILLS_DIR = Path(__file__).parent.parent / "skills"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


@dataclass
class Skill:
    name: str                     # 文件名 stem（唯一标识）
    category: str = ""            # 相对子目录（空 = 平铺在 skills/ 根）
    display_name: str = ""        # frontmatter 里的 name 字段（可选，检索用）
    description: str = ""
    triggers: List[str] = field(default_factory=list)
    path: Optional[Path] = None
    body: str = ""


def _parse_frontmatter(text: str) -> Dict[str, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    meta: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


def _parse_triggers(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def _category_of(p: Path) -> str:
    try:
        rel = p.parent.relative_to(SKILLS_DIR)
    except ValueError:
        return ""
    return "" if str(rel) == "." else str(rel).replace("/", " ")


def load_skills() -> Dict[str, Skill]:
    """递归扫描 skills/**/*.md，解析 frontmatter 与正文，返回 {技能名: Skill}。"""
    skills: Dict[str, Skill] = {}
    for p in sorted(SKILLS_DIR.rglob("*.md")):
        text = p.read_text(encoding="utf-8")
        meta = _parse_frontmatter(text)
        body = _FRONTMATTER_RE.sub("", text).strip()
        skills[p.stem] = Skill(
            name=p.stem,
            category=_category_of(p),
            display_name=meta.get("name", ""),
            description=meta.get("description", ""),
            triggers=_parse_triggers(meta.get("triggers")),
            path=p,
            body=body,
        )
    return skills


def get_skill(name: str) -> Optional[Skill]:
    return load_skills().get(name)


def skill_triggers() -> Dict[str, List[str]]:
    """返回 {技能名: 触发关键词}，供渐进披露使用。"""
    return {s.name: s.triggers for s in load_skills().values() if s.triggers}


def detect_skill_triggers(text: str, already_disclosed: List[str]) -> List[str]:
    """扫描一段事件文本，返回命中但尚未披露的技能名清单（保持注册表顺序）。"""
    low = text.lower()
    hits: List[str] = []
    for name, keywords in skill_triggers().items():
        if name in already_disclosed:
            continue
        if any(k.lower() in low for k in keywords):
            hits.append(name)
    return hits


def find_skills(query: str, limit: int = 5) -> List[Dict[str, object]]:
    """按名称/显示名/描述/触发词/分类检索技能，返回匹配项。"""
    q = (query or "").strip().lower()
    matches: List[Dict[str, object]] = []
    for s in load_skills().values():
        hay = " ".join([s.name, s.display_name, s.category, s.description, " ".join(s.triggers)]).lower()
        if q and q not in hay:
            continue
        matches.append({
            "name": s.name,
            "category": s.category,
            "description": s.description,
            "triggers": s.triggers,
        })
        if len(matches) >= max(1, limit):
            break
    return matches


# 渐进披露注入预算（对齐 SecAI/secai 的 to_prompt 整粒度截断）：
# 防止技能全文无限膨胀系统提示——单轮 input 曾达 2.5 万 token。
SKILL_MAX_PER_BODY = 1200    # 单篇技能正文上限（字符），超长截断
SKILL_MAX_COUNT = 3          # 同屏最多注入的技能篇数（最新披露优先）
SKILL_MAX_TOTAL = 8000       # 技能注入总预算（字符），整粒度截断


def load_skill_bodies(names: List[str]) -> str:
    """把若干技能名拼成一段注入文本（用于执行者系统提示），带预算。

    预算策略：同屏最多 SKILL_MAX_COUNT 篇、每篇最多 SKILL_MAX_PER_BODY 字、
    总预算 SKILL_MAX_TOTAL 字符；最新披露的优先；装不下整个技能就跳过，
    不从中途切半篇（避免把打法切成无法执行的半截）。
    """
    parts = []
    char_count = 0
    for name in list(names)[-SKILL_MAX_COUNT:]:  # 最新披露的优先
        s = get_skill(name)
        if s is None or not s.body:
            continue
        body = s.body
        if len(body) > SKILL_MAX_PER_BODY:
            body = body[:SKILL_MAX_PER_BODY] + "\n…[截断，完整打法用 find_skills 按需取]"
        title = f"{s.name}" + (f"（{s.category}）" if s.category else "")
        block = f"## 打法《{title}》\n{body}"
        if char_count + len(block) > SKILL_MAX_TOTAL:
            break  # 整粒度截断：装不下就跳过，不从中间切
        parts.append(block)
        char_count += len(block) + 2
    result = "\n\n".join(parts)
    if parts and len(names) > len(parts):
        result += "\n\n[技能上下文已截断：更多打法用 find_skills 按需检索]"
    return result


def create_skill(name: str, description: str, triggers: List[str], body: str = "") -> Path:
    """创建一个新技能文件（会覆盖同名文件）。返回文件路径。"""
    name = name.strip().rstrip(".md")
    if not name:
        raise ValueError("技能名不能为空")
    trig_text = ", ".join(triggers)
    content = (
        f"---\n"
        f"description: {description.strip()}\n"
        f"triggers: {trig_text}\n"
        f"---\n\n"
        f"{body.strip() or '# ' + description.strip() + '\n\n（在此编写打法内容）'}\n"
    )
    path = SKILLS_DIR / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


# ---- 命令行入口：list / find / create ----
if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in {"list", "ls"}:
        for s in load_skills().values():
            cat = f"[{s.category}] " if s.category else ""
            print(f"{cat}{s.name}\t{s.description}\t触发器={s.triggers or '无'}")
    elif args[0] == "find" and len(args) >= 2:
        for m in find_skills(args[1]):
            cat = f"[{m['category']}] " if m.get("category") else ""
            print(f"{cat}{m['name']}\t{m['description']}")
    elif args[0] == "create" and len(args) >= 3:
        name = args[1]
        desc = args[2]
        triggers: List[str] = []
        if "--triggers" in args:
            i = args.index("--triggers")
            if i + 1 < len(args):
                triggers = [t.strip() for t in args[i + 1].split(",") if t.strip()]
        p = create_skill(name, desc, triggers)
        print(f"已创建技能：{p}")
    else:
        print("用法：")
        print("  python skill_registry.py list")
        print("  python skill_registry.py find <关键词>")
        print("  python skill_registry.py create <技能名> <描述> --triggers \"a,b,c\"")

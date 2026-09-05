"""知识与技能域：联网搜索 / POC / 知识库 / 技能检索与沉淀。

R1 纯搬家：自 demo_tools.py 按功能域拆出，业务逻辑零改动。
- web_search：联网搜索（外脑）
- search_cve / get_poc：POC 库检索与完整利用细节
- list_knowledge / get_knowledge：知识库浏览
- find_skills / query_skills：技能库检索（前者检索并渐进披露，后者只读）
- remember：把战果沉淀为 POC / 知识 / 技能
"""
from __future__ import annotations

import json

from agents import RunContextWrapper, function_tool

from arsenal.registries import knowledge_registry, poc_registry
from arsenal.registries.skill_registry import create_skill
from arsenal.registries.skill_registry import find_skills as search_skills
from core.task_context import TaskContext
from runtime.log import log_info
from tools.domains._base import _guard_output


@function_tool
def web_search(query: str, max_results: int = 5) -> str:
    """联网搜索（外脑）：不认识的技术栈/报错/CVE，先查再打。"""
    try:
        from ddgs import DDGS
        with DDGS() as d:
            hits = list(d.text(query, max_results=min(max_results, 8)))
        result = json.dumps([{"title": h.get("title", ""),
                              "snippet": h.get("body", "")[:300],
                              "url": h.get("href", "")} for h in hits], ensure_ascii=False)
        return result + _guard_output(result)
    except Exception as e:
        return f"搜索不可用: {str(e)[:200]}。hint: 依靠内置打法与差分实验"


@function_tool
def find_skills(ctx: RunContextWrapper[TaskContext], query: str, limit: int = 5) -> str:
    """在技能库中检索相关技能（按名称/描述/触发词匹配），命中即自动解锁（披露）该技能。

    用法：遇到不熟悉的场景时，先调用本工具找找有没有对应打法。
    """
    c = ctx.context
    matches = search_skills(query, limit)
    newly = []
    for m in matches:
        if m["name"] not in c.disclosed_skills:
            c.disclosed_skills.append(m["name"])
            c.skill_events.append(f"find_skills 检索披露 {m['name']}")
            newly.append(m["name"])
    if newly:
        log_info(f"find_skills 检索 '{query}' → 披露技能 {newly}")
    return json.dumps({
        "matches": matches,
        "disclosed": [m["name"] for m in matches],
    }, ensure_ascii=False)


@function_tool
def query_skills(query: str, limit: int = 5) -> str:
    """只读检索技能库（按名称/描述/触发词匹配），不自动披露、不写状态。

    供分析型智能体（Planner/Coach）在规划/给方向时查武器库用；执行者用 find_skills
    （检索并披露）。本工具无副作用，不依赖执行上下文。
    """
    matches = search_skills(query, limit)
    return json.dumps({"matches": matches}, ensure_ascii=False)


# ================= 漏洞 / POC / 知识检索工具 =================
@function_tool
def search_cve(ctx: RunContextWrapper[TaskContext], query: str, limit: int = 5) -> str:
    """在 POC 库中检索 CVE（按产品名 / CVE 编号 / 漏洞摘要关键词）。

    指纹到某产品/版本后，用产品名或 CVE 编号检索已知漏洞，判断是否有现成 POC 或利用思路。
    """
    matches = poc_registry.find_pocs(query, limit)
    return json.dumps({"matches": matches}, ensure_ascii=False)


@function_tool
def get_poc(ctx: RunContextWrapper[TaskContext], cve: str) -> str:
    """按 CVE 编号取完整 POC（含利用原理/步骤/载荷/验证方式）。

    先用 search_cve 找到 CVE 编号，再用本工具取完整利用细节。
    """
    p = poc_registry.get_poc(cve)
    if p is None:
        return json.dumps({"error": f"未找到 {cve} 的 POC"}, ensure_ascii=False)
    return json.dumps({
        "cve": p.cve,
        "name": p.name,
        "severity": p.severity,
        "summary": p.summary,
        "affected": p.affected,
        "type": p.poc_type,
        "principle": p.principle,
        "steps": p.steps,
        "payload": p.payload,
        "verification": p.verification,
        "references": p.references,
    }, ensure_ascii=False)


@function_tool
def list_knowledge(ctx: RunContextWrapper[TaskContext]) -> str:
    """列出知识库条目（id + 简介），先看简介，再用 get_knowledge 按 id 取全文。"""
    return json.dumps({"knowledge": knowledge_registry.list_knowledge()}, ensure_ascii=False)


@function_tool
def get_knowledge(ctx: RunContextWrapper[TaskContext], kid: str) -> str:
    """按 id 取知识库完整内容（如 get_flag/post_exploit/waf_bypass）。"""
    k = knowledge_registry.get_knowledge(kid)
    if k is None:
        return json.dumps({"error": f"未找到知识条目 {kid}，可用 list_knowledge 查看"},
                          ensure_ascii=False)
    return json.dumps({"id": k["id"], "content": k["all"]}, ensure_ascii=False)


@function_tool
def remember(ctx: RunContextWrapper[TaskContext], kind: str, name: str,
             summary: str = "", payload: str = "", steps: str = "",
             content: str = "") -> str:
    """把「战果」沉淀为可复用能力，让下次遇到同类题直接复用（记忆升级）。

    kind 取值：
      - poc: 沉淀为 POC（利用原理/步骤/载荷），search_cve/get_poc 可检索
      - knowledge: 沉淀为知识条目，list_knowledge/get_knowledge 可检索
      - skill: 沉淀为技能，find_skills 可检索并渐进披露

    name: 名称（poc 填 CVE 或产品名，knowledge/skill 填 id）
    summary: 一句话描述/原理
    payload: 关键载荷/利用脚本
    steps: 利用步骤（换行分隔）
    content: 详细内容（knowledge/skill 正文，缺省用 payload）
    """
    kind = (kind or "").strip().lower()
    name = (name or "").strip()
    if not name:
        return json.dumps({"error": "name 不能为空"}, ensure_ascii=False)
    try:
        if kind == "poc":
            steps_list = [s.strip() for s in (steps or "").split("\n") if s.strip()]
            cve = name.upper() if name.upper().startswith("CVE-") else ""
            path = poc_registry.create_poc(
                name=name, cve=cve, summary=summary, poc_type="exploit",
                principle=summary, steps=steps_list, payload=payload,
                verification=summary)
        elif kind == "knowledge":
            path = knowledge_registry.create_knowledge(name, summary, content or payload)
        elif kind == "skill":
            triggers = [t.strip() for t in (summary or "").split(",") if t.strip()]
            path = create_skill(name, summary, triggers, content or payload)
        else:
            return json.dumps({"error": f"未知 kind：{kind}（可用 poc/knowledge/skill）"},
                              ensure_ascii=False)
        return json.dumps({"ok": True, "kind": kind, "path": str(path)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"沉淀失败：{str(e)[:200]}"}, ensure_ascii=False)

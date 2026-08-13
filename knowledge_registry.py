"""知识库注册表：从 knowledge/*.txt 加载知识，按需查询（id + desc + all）。

每个 txt 文件：首行为 desc（简介），全文为 all（详细内容）。
Agent 先 list_knowledge 看简介列表，再按 id 取全文，避免一次性塞进上下文。

用法：
    python knowledge_registry.py list
    python knowledge_registry.py get <id>
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"


def load_knowledge() -> List[dict]:
    """递归扫描 knowledge/**，每个文件生成 {id, name, desc, all}。

    支持子目录分类（如 get_flag/idor）；id 为相对路径（去掉 .txt 后缀）。
    文件首行作 desc，全文作 all。
    """
    items: List[dict] = []
    if not KNOWLEDGE_DIR.exists():
        return items
    for p in sorted(KNOWLEDGE_DIR.rglob("*")):
        if not p.is_file() or p.name.startswith("."):  # 跳过隐藏文件(.DS_Store等)
            continue
        try:
            content = p.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if not content:
            continue
        lines = content.splitlines()
        desc = lines[0].strip() if lines else ""
        rel = str(p.relative_to(KNOWLEDGE_DIR))
        if rel.endswith(".txt"):
            rel = rel[:-4]
        items.append({
            "id": rel,             # 如 get_flag/idor 或 post_exploit
            "name": p.stem,
            "desc": desc[:120],
            "all": content,
        })
    return items


def list_knowledge() -> List[dict]:
    """列出全部知识条目的 id + desc。"""
    return [{"id": i["id"], "desc": i["desc"]} for i in load_knowledge()]


def get_knowledge(kid: str) -> Optional[dict]:
    """按 id 取完整知识内容。"""
    kid = (kid or "").strip()
    for i in load_knowledge():
        if i["id"] == kid:
            return i
    return None


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in {"list", "ls"}:
        for i in list_knowledge():
            print(f"{i['id']}\t{i['desc']}")
    elif args[0] == "get" and len(args) >= 2:
        i = get_knowledge(args[1])
        print(i["all"] if i else f"未找到知识条目：{args[1]}")
    else:
        print("用法：")
        print("  python knowledge_registry.py list")
        print("  python knowledge_registry.py get <id>")

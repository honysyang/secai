"""static.py —— GET / 服务 apps/web/dist（index.html + 静态资源 + SPA fallback）。

路由语义：
- 已知文件（assets/*、favicon.svg 等）→ 原样 FileResponse；
- 其余 GET（含 / 与前端路由路径）→ SPA fallback 返回 index.html；
- dist 缺失 → 404 文本提示（联调前需先在 apps/web 执行 npm run build）。
"""
from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response

# apps/web/dist（前端构建产物；vite 默认根相对路径引用 /assets/...）
DIST_DIR = Path(__file__).resolve().parent.parent / "apps" / "web" / "dist"
INDEX_FILE = DIST_DIR / "index.html"


def _normalize(path: str) -> str:
    """去前导斜杠并防路径穿越（../ 直接丢弃为根）。"""
    cleaned = path.lstrip("/")
    parts = [p for p in cleaned.split("/") if p not in ("", ".", "..")]
    return "/".join(parts)


async def spa(request: Request) -> Response:
    rel = _normalize(request.path_params.get("path") or "")
    if rel:
        candidate = (DIST_DIR / rel).resolve()
        if candidate.is_relative_to(DIST_DIR.resolve()) and candidate.is_file():
            return FileResponse(candidate)
    if INDEX_FILE.is_file():
        return FileResponse(INDEX_FILE)
    return PlainTextResponse(
        "前端产物缺失：apps/web/dist 未构建，请先执行 npm run build（apps/web）。",
        status_code=404,
    )


__all__ = ["DIST_DIR", "spa"]

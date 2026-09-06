"""artifacts.py —— 渗透报告产物落盘与检索（报告一键导出闭环，产品化 Phase 0-2）。

职责：
- ArtifactsStore(root)：管理 ``<root>/<engagement_id>/`` 产物目录（惰性创建），
  root 缺省 ``data/artifacts``（可用环境变量 ``SECAI_ARTIFACTS_DIR`` 覆盖，
  data/ 已被 .gitignore 忽略，不会误提交）；
- save_report(engagement_id, report, fmt)：把 EngagementReport 序列化为
  ``.md``（Markdown 渲染，章节对齐 REPORT_SECTION_TITLES）、``.json``
  （report.to_dict()）或 ``.pdf``（reportlab 渲染，客户交付标准格式），
  写文件并返回 ArtifactMeta；
- list / get：按任务书检索产物元信息（含文件大小、落盘时间）。

「生成即存」hook 在 server/api.py 的 api_report 内触发（报告引擎输出后
立即落盘 md + json 两份），本模块只负责纯文件 I/O，不感知编排面。
"""
from __future__ import annotations

import io
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 支持的导出格式 → 文件扩展名 / MIME
FORMAT_EXTENSIONS = {"md": "md", "json": "json", "pdf": "pdf"}
FORMAT_MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "pdf": "application/pdf",
}
# 报告章节标题（与 server/api.py REPORT_SECTION_TITLES 对齐，R5 章节合同中文标签）
REPORT_SECTION_TITLES = [
    "执行摘要",
    "攻击面图谱",
    "发现详情",
    "已排除攻击面",
    "跨目标攻击链",
    "方法论",
    "局限性",
]

# 严重等级中文标签（Markdown 渲染用）
_SEVERITY_LABELS = {
    "critical": "严重",
    "high": "高危",
    "medium": "中危",
    "low": "低危",
    "info": "信息",
}


@dataclass
class ArtifactMeta:
    """单个产物元信息（api 返回 / 列表检索的统一形状）。"""

    artifact_id: str
    engagement_id: str
    format: str
    path: str
    size_bytes: int
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifactId": self.artifact_id,
            "engagementId": self.engagement_id,
            "format": self.format,
            "path": self.path,
            "sizeBytes": self.size_bytes,
            "createdAt": self.created_at,
        }


class ArtifactsStore:
    """产物落盘存储：目录惰性创建，元信息从文件系统现取（无独立索引文件）。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------
    # 目录与文件定位
    # ------------------------------------------------------------------
    def _engagement_dir(self, engagement_id: str) -> Path:
        """任务书产物目录（调用方负责惰性创建，只读路径不建目录）。"""
        return self.root / engagement_id

    def _artifact_path(self, engagement_id: str, artifact_id: str, fmt: str) -> Path:
        return self._engagement_dir(engagement_id) / f"{artifact_id}.{FORMAT_EXTENSIONS[fmt]}"

    # ------------------------------------------------------------------
    # 写：报告 → md / json 产物
    # ------------------------------------------------------------------
    def save_report(self, engagement_id: str, report: Any, fmt: str) -> ArtifactMeta:
        """序列化报告写盘：fmt=md → Markdown 渲染；fmt=json → report.to_dict()；
        fmt=pdf → reportlab 渲染（客户交付标准格式）。

        每次落盘生成新 artifact_id（报告可重复生成，产物留档不覆盖）。
        """
        if fmt not in FORMAT_EXTENSIONS:
            raise ValueError(f"不支持的导出格式: {fmt!r}（可用 {', '.join(FORMAT_EXTENSIONS)}）")
        if fmt == "pdf":
            data = _render_pdf(report)
        else:
            content = _render_markdown(report) if fmt == "md" else _render_json(report)
            data = content.encode("utf-8")
        artifact_id = f"art-{uuid.uuid4().hex[:12]}"
        target_dir = self._engagement_dir(engagement_id)
        target_dir.mkdir(parents=True, exist_ok=True)  # 惰性创建：首次落盘才建目录
        path = self._artifact_path(engagement_id, artifact_id, fmt)
        path.write_bytes(data)
        created_at = _file_created_iso(path)
        return ArtifactMeta(
            artifact_id=artifact_id,
            engagement_id=engagement_id,
            format=fmt,
            path=str(path),
            size_bytes=len(data),
            created_at=created_at,
        )

    # ------------------------------------------------------------------
    # 读：列表 / 单查
    # ------------------------------------------------------------------
    def list(self, engagement_id: str) -> list[ArtifactMeta]:
        """列出该任务书全部产物（按文件名序，稳定输出便于断言）。"""
        directory = self._engagement_dir(engagement_id)
        if not directory.is_dir():
            return []
        metas: list[ArtifactMeta] = []
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            suffix = path.suffix.lstrip(".")
            if suffix not in FORMAT_EXTENSIONS:
                continue
            metas.append(
                ArtifactMeta(
                    artifact_id=path.stem,
                    engagement_id=engagement_id,
                    format=suffix,
                    path=str(path),
                    size_bytes=path.stat().st_size,
                    created_at=_file_created_iso(path),
                )
            )
        return metas

    def get(self, engagement_id: str, artifact_id: str) -> ArtifactMeta | None:
        """按 artifact_id 查单个产物；文件不存在返回 None。"""
        for meta in self.list(engagement_id):
            if meta.artifact_id == artifact_id:
                return meta
        return None

    def resolve(self, engagement_id: str, fmt: str) -> ArtifactMeta | None:
        """取该任务书最新一份指定格式的产物（导出端点用：无 artifactId 入参，
        按文件名序取最后一份 = 最近一次落盘）。"""
        if fmt not in FORMAT_EXTENSIONS:
            return None
        candidates = [m for m in self.list(engagement_id) if m.format == fmt]
        return candidates[-1] if candidates else None


# ---------------------------------------------------------------------------
# 序列化渲染（纯函数）
# ---------------------------------------------------------------------------
# PDF 中文字体候选路径（按优先级探测注册；找不到则退化为英文正文，见
# _pdf_font_family 注释说明字体限制）
_CJK_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",  # 文泉驿正黑（Debian/Kali 自带）
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",  # 文泉驿微米黑
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Noto Sans CJK
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
)

_pdf_font_cache: str | None = None


def _pdf_font_family() -> str:
    """探测并注册中文字体，返回注册后的字体族名。

    找到中文字体 → reportlab TTFont 注册（TTC 需 subfontIndex=0）；
    找不到 → 退化用内置 Helvetica：此时中文将渲染为方块，故 _render_pdf 的
    正文策略改为中英混排、关键字段（标题/严重度等标签）用英文（见该函数注释）。
    """
    global _pdf_font_cache
    if _pdf_font_cache is not None:
        return _pdf_font_cache
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for candidate in _CJK_FONT_CANDIDATES:
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            # TTC 集合字体必须指定 subfontIndex；OTF 用 CIDFont 不支持 TTFont，
            # 只有 .ttc/.ttf 走 TTFont 注册，.otf 候选跳过
            if path.suffix.lower() == ".otf":
                continue
            pdfmetrics.registerFont(TTFont("SECaiCJK", str(path), subfontIndex=0))
            _pdf_font_cache = "SECaiCJK"
            return _pdf_font_cache
        except Exception:
            continue  # 损坏/不兼容的字体文件，尝试下一个候选
    _pdf_font_cache = "Helvetica"
    return _pdf_font_cache


def _render_pdf(report: Any) -> bytes:
    """EngagementReport → PDF 交付件（reportlab 纯 Python 渲染，无系统依赖）。

    版式：封面（报告标题 + 客户/任务书/授权编号/生成时间）→ 执行摘要 →
    攻击面图谱 → 发现详情（标题/严重度/影响/证据链/修复建议）→ 已排除攻击面 →
    跨目标攻击链 → 方法论 → 局限性。中文渲染策略：探测到系统中文字体
    （文泉驿正黑 / Noto Sans CJK）则全程中文；找不到时 Helvetica 无法显示
    CJK 字形（会变成方块），此时正文标签退化为英文（Findings/Severity 等），
    中文内容原样输出（阅者可换有字体的环境重新导出，见 _pdf_font_family）。
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    font = _pdf_font_family()
    # 无中文字体时标签用英文，避免关键字段渲染成方块
    label = (
        (lambda zh, en: zh)
        if font != "Helvetica"
        else (lambda zh, en: en)
    )
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    margin = 20 * mm
    y = height - margin

    def new_page() -> None:
        nonlocal y
        c.showPage()
        c.setFont(font, 10)
        y = height - margin

    def line(text: str, size: int = 10, gap: float = 5.5 * mm) -> None:
        """写一行（自动折行 + 页尾翻页）。"""
        nonlocal y
        if y < margin + gap:
            new_page()
        c.setFont(font, size)
        max_chars = max(10, int((width - 2 * margin) / (size * 0.62)))
        for chunk in _wrap_text(str(text), max_chars):
            if y < margin + gap:
                new_page()
                c.setFont(font, size)
            c.drawString(margin, y, chunk)
            y -= gap

    def heading(text: str, size: int = 14) -> None:
        nonlocal y
        y -= 3 * mm
        line(text, size=size, gap=7 * mm)
        y -= 1 * mm

    # ── 封面 ──
    cover = dict(getattr(report, "cover", {}) or {})
    title = str(cover.get("title") or "SECAI-PT 渗透测试报告")
    line(title, size=20, gap=10 * mm)
    y -= 4 * mm
    for zh_key, value in (
        ("客户", cover.get("client")),
        ("任务书", cover.get("engagement_id")),
        ("授权编号", cover.get("authorization_ref")),
        ("生成时间", cover.get("generated_at")),
        ("报告编号", cover.get("report_id")),
    ):
        if value:
            line(f"{label(zh_key, {'客户': 'Client', '任务书': 'Engagement', '授权编号': 'Authorization', '生成时间': 'Generated at', '报告编号': 'Report ID'}[zh_key])}: {value}")
    y -= 4 * mm

    # ── 执行摘要 ──
    heading(label("执行摘要", "Executive Summary"), size=15)
    summary = dict(getattr(report, "executive_summary", {}) or {})
    if summary:
        breakdown = summary.get("severity_breakdown") or {}
        sev_text = " / ".join(
            f"{_SEVERITY_LABELS.get(level, level)} {count}" for level, count in breakdown.items() if count
        )
        line(
            f"{label('目标数', 'Targets')}: {summary.get('target_count', 0)}; "
            f"{label('发现数', 'Findings')}: {summary.get('finding_count', 0)} ({sev_text or '-'})"
        )
        line(
            f"{label('平均覆盖率', 'Avg coverage')}: {summary.get('average_coverage_ratio', 0)}; "
            f"{label('已排除攻击面', 'Dead ends')}: {summary.get('dead_end_count', 0)}; "
            f"{label('跨目标攻击链', 'Cross-target chains')}: {summary.get('cross_target_chain_count', 0)}"
        )
    else:
        line(f"({label('无执行摘要数据', 'no executive summary data')})")

    # ── 攻击面图谱 ──
    heading(label("攻击面图谱", "Attack Surface Map"))
    surface_map = list(getattr(report, "attack_surface_map", []) or [])
    if surface_map:
        for row in surface_map:
            line(
                f"- {row.get('label', row.get('target_id', ''))}: "
                f"{label('端口', 'ports')} {row.get('ports', 0)}, "
                f"Web {row.get('web', 0)}, {label('凭据', 'credentials')} {row.get('credentials', 0)}"
            )
    else:
        line(f"({label('无攻击面数据', 'no attack surface data')})")

    # ── 发现详情 ──
    heading(label("发现详情", "Findings"))
    findings = list(getattr(report, "findings", []) or [])
    if not findings:
        line(f"({label('无发现', 'no findings')})")
    for finding in findings:
        finding_dict = _asdict(finding)
        severity = str(finding_dict.get("severity", "info"))
        y -= 2 * mm
        line(f"• {finding_dict.get('title', '未命名发现')}", size=12, gap=6 * mm)
        line(
            f"  {label('严重度', 'Severity')}: {_SEVERITY_LABELS.get(severity, severity)} "
            f"(CVSS {finding_dict.get('cvss_base_score', 0)})"
        )
        if finding_dict.get("impact"):
            line(f"  {label('影响', 'Impact')}: {finding_dict['impact']}")
        if finding_dict.get("affected"):
            line(f"  {label('受影响资产', 'Affected')}: {finding_dict['affected']}")
        for step in finding_dict.get("reproduce_steps") or []:
            action = step.get("action") or "（无动作记录）"
            evidence = step.get("evidence") or ""
            line(f"  {label('复现', 'Repro')} {step.get('seq', '?')}. {action}" + (f" -- {evidence}" if evidence else ""))
        for ev in finding_dict.get("evidence_chain") or []:
            line(f"  {label('证据', 'Evidence')} [{ev.get('event_id', '')}] {ev.get('summary') or ''}")
        if finding_dict.get("remediation"):
            line(f"  {label('修复建议', 'Remediation')}: {finding_dict['remediation']}")

    # ── 已排除攻击面 ──
    heading(label("已排除攻击面", "Excluded Attack Surfaces"))
    excluded = list(getattr(report, "excluded_attack_surfaces", []) or [])
    if not excluded:
        line(f"({label('无已排除攻击面', 'none')})")
    for item in excluded:
        item_dict = _asdict(item)
        line(f"- {item_dict.get('path_description', '')}")
        line(
            f"  {label('排除方式', 'Method')}: {item_dict.get('falsification_method', '')}; "
            f"{label('排除时间', 'At')}: {item_dict.get('falsified_at', '')}"
        )

    # ── 跨目标攻击链 ──
    heading(label("跨目标攻击链", "Cross-target Chains"))
    cross_chains = list(getattr(report, "cross_target_chains", []) or [])
    if not cross_chains:
        line(f"({label('无跨目标攻击链', 'none')})")
    for chain in cross_chains:
        chain_dict = _asdict(chain)
        line(f"- [{chain_dict.get('chain_id', '')}] {chain_dict.get('note', '')}")

    # ── 方法论 ──
    heading(label("方法论", "Methodology"))
    methodology = dict(getattr(report, "methodology", {}) or {})
    stages = methodology.get("stages") or []
    if stages:
        line(f"{label('阶段', 'Stages')}: " + " -> ".join(str(s) for s in stages))
    if methodology.get("verifier"):
        line(f"{label('验证者', 'Verifier')}: {methodology['verifier']}")

    # ── 局限性 ──
    heading(label("局限性", "Limitations"))
    limitations = dict(getattr(report, "limitations", {}) or {})
    notes = limitations.get("notes") or []
    if not notes:
        line(f"({label('无', 'none')})")
    for note in notes:
        line(f"- {note}")

    c.save()
    return buf.getvalue()


def _wrap_text(text: str, max_chars: int) -> list[str]:
    """按显示宽度折行（CJK 全角按 2 计，ASCII 按 1 计）。"""
    lines: list[str] = []
    current = ""
    current_width = 0
    for ch in text:
        ch_width = 2 if ord(ch) > 0x2E7F else 1
        if current_width + ch_width > max_chars and current:
            lines.append(current)
            current = ch
            current_width = ch_width
        else:
            current += ch
            current_width += ch_width
    if current:
        lines.append(current)
    return lines or [""]


def _render_json(report: Any) -> str:
    """EngagementReport → JSON 文本（report.to_dict()，ensure_ascii=False）。"""
    to_dict = getattr(report, "to_dict", None)
    payload = to_dict() if callable(to_dict) else _report_fallback_dict(report)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _report_fallback_dict(report: Any) -> dict[str, Any]:
    """无 to_dict 时的兜底：取 cover / findings / sections 等字段手动构造。"""
    return {
        "cover": dict(getattr(report, "cover", {}) or {}),
        "findings": [dict(f) if isinstance(f, dict) else _asdict(f) for f in getattr(report, "findings", [])],
        "sections": list(getattr(report, "sections", []) or []),
    }


def _asdict(value: Any) -> dict[str, Any]:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return {"value": str(value)}


def _render_markdown(report: Any) -> str:
    """EngagementReport → Markdown 交付文本：封面头 + 七个 R5 章节。

    章节标题对齐 REPORT_SECTION_TITLES（执行摘要/攻击面图谱/发现详情/
    已排除攻击面/跨目标攻击链/方法论/局限性）；每条 finding 渲染
    标题/严重度/影响/复现步骤/证据链/修复建议。
    """
    lines: list[str] = []
    cover = dict(getattr(report, "cover", {}) or {})
    title = str(cover.get("title") or "SECAI-PT 渗透测试报告")
    lines.append(f"# {title}")
    lines.append("")
    # 封面信息（有则渲染，无则跳过，保持诚实投影）
    cover_rows = [
        ("客户", cover.get("client")),
        ("任务书", cover.get("engagement_id")),
        ("授权编号", cover.get("authorization_ref")),
        ("生成时间", cover.get("generated_at")),
        ("报告编号", cover.get("report_id")),
    ]
    for label, value in cover_rows:
        if value:
            lines.append(f"- **{label}**：{value}")
    if any(value for _label, value in cover_rows):
        lines.append("")

    # 执行摘要
    lines.append(f"## {REPORT_SECTION_TITLES[0]}")
    summary = dict(getattr(report, "executive_summary", {}) or {})
    if summary:
        breakdown = summary.get("severity_breakdown") or {}
        sev_text = " / ".join(
            f"{_SEVERITY_LABELS.get(level, level)} {count}"
            for level, count in breakdown.items()
            if count
        )
        lines.append(
            f"- 目标数：{summary.get('target_count', 0)}；发现数：{summary.get('finding_count', 0)}"
            f"（{sev_text or '无'}）"
        )
        lines.append(
            f"- 平均覆盖率：{summary.get('average_coverage_ratio', 0)}；"
            f"已排除攻击面：{summary.get('dead_end_count', 0)}；"
            f"跨目标攻击链：{summary.get('cross_target_chain_count', 0)}"
        )
    else:
        lines.append("（无执行摘要数据）")
    lines.append("")

    # 攻击面图谱
    lines.append(f"## {REPORT_SECTION_TITLES[1]}")
    surface_map = list(getattr(report, "attack_surface_map", []) or [])
    if surface_map:
        lines.append("| 目标 | 端口 | Web | 凭据 |")
        lines.append("| --- | --- | --- | --- |")
        for row in surface_map:
            lines.append(
                f"| {row.get('label', row.get('target_id', ''))} "
                f"| {row.get('ports', 0)} | {row.get('web', 0)} | {row.get('credentials', 0)} |"
            )
    else:
        lines.append("（无攻击面数据）")
    lines.append("")

    # 发现详情
    lines.append(f"## {REPORT_SECTION_TITLES[2]}")
    findings = list(getattr(report, "findings", []) or [])
    if not findings:
        lines.append("（无发现）")
    for finding in findings:
        finding_dict = _asdict(finding)
        severity = str(finding_dict.get("severity", "info"))
        lines.append(f"### {finding_dict.get('title', '未命名发现')}")
        lines.append("")
        lines.append(
            f"- 严重度：{_SEVERITY_LABELS.get(severity, severity)}"
            f"（CVSS 基准 {finding_dict.get('cvss_base_score', 0)}）"
        )
        lines.append(f"- 影响：{finding_dict.get('impact', '')}")
        lines.append(f"- 受影响资产：{finding_dict.get('affected', '')}")
        steps = finding_dict.get("reproduce_steps") or []
        if steps:
            lines.append("- 复现步骤：")
            for step in steps:
                action = step.get("action") or "（无动作记录）"
                evidence = step.get("evidence") or ""
                lines.append(f"  {step.get('seq', '?')}. {action}" + (f" —— {evidence}" if evidence else ""))
        evidence_chain = finding_dict.get("evidence_chain") or []
        if evidence_chain:
            lines.append("- 证据链：")
            for ev in evidence_chain:
                summary_text = ev.get("summary") or "（事件缺失）"
                lines.append(f"  - `{ev.get('event_id', '')}` {summary_text}")
        remediation = finding_dict.get("remediation")
        if remediation:
            lines.append(f"- 修复建议：{remediation}")
        lines.append("")

    # 已排除攻击面（负面发现）
    lines.append(f"## {REPORT_SECTION_TITLES[3]}")
    excluded = list(getattr(report, "excluded_attack_surfaces", []) or [])
    if not excluded:
        lines.append("（无已排除攻击面）")
    for item in excluded:
        item_dict = _asdict(item)
        lines.append(f"- **{item_dict.get('path_description', '')}**")
        lines.append(
            f"  - 排除方式：{item_dict.get('falsification_method', '')}；"
            f"排除时间：{item_dict.get('falsified_at', '')}"
        )
        if item_dict.get("evidence_snapshot"):
            lines.append(f"  - 证据快照：{item_dict['evidence_snapshot']}")
        if item_dict.get("overturn_condition"):
            lines.append(f"  - 复活条件：{item_dict['overturn_condition']}")
    lines.append("")

    # 跨目标攻击链
    lines.append(f"## {REPORT_SECTION_TITLES[4]}")
    cross_chains = list(getattr(report, "cross_target_chains", []) or [])
    if not cross_chains:
        lines.append("（无跨目标攻击链）")
    for chain in cross_chains:
        chain_dict = _asdict(chain)
        lines.append(
            f"- `{chain_dict.get('chain_id', '')}`：{chain_dict.get('note', '')}"
        )
    lines.append("")

    # 方法论
    lines.append(f"## {REPORT_SECTION_TITLES[5]}")
    methodology = dict(getattr(report, "methodology", {}) or {})
    stages = methodology.get("stages") or []
    if stages:
        lines.append("- 阶段：" + " → ".join(str(s) for s in stages))
    if methodology.get("verifier"):
        lines.append(f"- 验证者：{methodology['verifier']}")
    if methodology.get("ai_assisted"):
        lines.append(f"- {methodology['ai_assisted']}")
    lines.append("")

    # 局限性
    lines.append(f"## {REPORT_SECTION_TITLES[6]}")
    limitations = dict(getattr(report, "limitations", {}) or {})
    for note in limitations.get("notes") or []:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def _file_created_iso(path: Path) -> str:
    """文件创建/修改时间 → UTC ISO-8601（毫秒、Z 后缀，与 state.now_iso 同构）。"""
    from datetime import datetime, timezone

    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "ArtifactMeta",
    "ArtifactsStore",
    "FORMAT_EXTENSIONS",
    "FORMAT_MEDIA_TYPES",
    "REPORT_SECTION_TITLES",
]

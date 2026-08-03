"""InteractiveExporter — generates reports for interactive debate spaces.

Supports PDF (WeasyPrint), Markdown, and ODF formats.
Builds a structured document from the event tree of an interactive debate.
"""

from __future__ import annotations

import asyncio
import html as html_mod
import json
import logging
import re
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from backend.persistence.event_store import EventStore

logger = logging.getLogger(__name__)

_REPORTS_DIR = Path("reports")


def _md_to_html(text: str) -> str:
    """Convert Markdown subset to HTML for PDF/ODF rendering."""
    if not text:
        return ""
    esc = html_mod.escape
    lines = text.split("\n")
    out: list[str] = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("### "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h4>{esc(stripped[4:])}</h4>")
            continue
        if stripped.startswith("## "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h3>{esc(stripped[3:])}</h3>")
            continue
        if stripped.startswith("# "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h2>{esc(stripped[2:])}</h2>")
            continue

        if stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline_md(stripped[2:])}</li>")
            continue

        if len(stripped) > 2 and stripped[0].isdigit() and stripped[1] in ".)" and stripped[2] == " ":
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline_md(stripped[3:])}</li>")
            continue

        if in_list:
            out.append("</ul>")
            in_list = False

        if not stripped:
            out.append("<br>")
            continue

        out.append(f"<p>{_inline_md(stripped)}</p>")

    if in_list:
        out.append("</ul>")

    return "\n".join(out)


def _inline_md(text: str) -> str:
    """Convert inline Markdown to HTML."""
    esc = html_mod.escape
    s = esc(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*(.+?)\*", r"<em>\1</em>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def _format_content(content: str | Any) -> str:
    """Format event content for display."""
    if isinstance(content, dict):
        return json.dumps(content, indent=2, ensure_ascii=False)
    return str(content) if content else ""


class InteractiveExporter:
    """Generates reports from interactive debate space event trees."""

    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    async def generate(
        self,
        space_id: str,
        fmt: str = "md",
    ) -> Path:
        """Generate a report for the given space.

        Args:
            space_id: The interactive debate space ID.
            fmt: Output format — "md", "pdf", or "odf".

        Returns:
            Path to the generated report file.
        """
        if fmt not in ("md", "pdf", "odf"):
            raise ValueError(f"Unsupported format: {fmt!r}")

        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        # Load space and events
        space = self.event_store.get_space(space_id)
        if not space:
            raise ValueError(f"Space {space_id} not found")

        events = self.event_store.list_events(space_id)
        if not events:
            raise ValueError(f"Space {space_id} has no events")

        # Build markdown content
        md_content = self._build_markdown(space, events)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"interactive_{space_id[:8]}_{ts}.{fmt}"
        path = _REPORTS_DIR / filename

        if fmt == "md":
            await asyncio.to_thread(path.write_text, md_content, encoding="utf-8")
        elif fmt == "pdf":
            await asyncio.to_thread(self._build_pdf, md_content, path)
        elif fmt == "odf":
            await asyncio.to_thread(self._build_odf, md_content, path)

        logger.info("Interactive report generated: %s", path)
        return path

    def _build_markdown(self, space: Any, events: list[Any]) -> str:
        """Build Markdown content from space and events."""
        lines: list[str] = []

        # Title
        lines.append(f"# {space.title}")
        lines.append("")

        # Metadata
        lines.append("## Metadaten")
        lines.append("")
        if space.case_id:
            lines.append(f"- **Fall-ID:** {space.case_id}")
        if space.tenant_id:
            lines.append(f"- **Tenant-ID:** {space.tenant_id}")
        lines.append(f"- **Erstellt:** {space.created_at.strftime('%d.%m.%Y %H:%M') if space.created_at else '—'}")
        lines.append(f"- **Events:** {space.event_count}")
        lines.append(f"- **Verzweigungen:** {space.fork_count}")
        lines.append("")

        # Build event tree structure
        root_events = [e for e in events if not e.parent_id]
        child_map: dict[str, list[Any]] = {}
        for e in events:
            if e.parent_id:
                if e.parent_id not in child_map:
                    child_map[e.parent_id] = []
                child_map[e.parent_id].append(e)

        # Render events recursively
        lines.append("## Diskussionsverlauf")
        lines.append("")

        def render_event(evt: Any, depth: int = 0):
            indent = "  " * depth
            actor = evt.actor_id or "unknown"
            role = evt.role or ""
            role_label = f"{actor} ({role})" if role else actor

            # Actor icon
            icon = {"user": "👤", "agent": "🤖", "a2a": "🔗", "system": "⚙️"}.get(evt.actor_type, "❓")

            ts = ""
            if evt.created_at:
                ts = evt.created_at.strftime("%H:%M")

            content = _format_content(evt.content)

            # Metadata
            meta_parts = []
            if evt.metadata_json:
                if evt.metadata_json.get("llm_profile_id"):
                    meta_parts.append(f"Modell: {evt.metadata_json['llm_profile_id']}")
                if evt.metadata_json.get("tokens_output"):
                    meta_parts.append(f"Tokens: {evt.metadata_json['tokens_output']}")
                if evt.metadata_json.get("document_chunks_used"):
                    meta_parts.append(f"Docs: {evt.metadata_json['document_chunks_used']}")
            meta_str = f" ({', '.join(meta_parts)})" if meta_parts else ""

            lines.append(f"{indent}### {icon} {role_label} — {ts}{meta_str}")
            lines.append("")
            if content:
                lines.append(content)
                lines.append("")

            # Render children
            children = child_map.get(evt.event_id, [])
            for child in children:
                render_event(child, depth + 1)

        for root in root_events:
            render_event(root)

        # Token statistics
        total_input = sum(e.tokens_input or 0 for e in events)
        total_output = sum(e.tokens_output or 0 for e in events)
        if total_input or total_output:
            lines.append("## Token-Statistik")
            lines.append("")
            lines.append(f"- **Input:** {total_input:,}")
            lines.append(f"- **Output:** {total_output:,}")
            lines.append(f"- **Gesamt:** {total_input + total_output:,}")
            lines.append("")

        # Footer
        lines.append("---")
        lines.append(f"*Generiert am {datetime.now().strftime('%d.%m.%Y %H:%M')} — Danwa Interactive*")
        lines.append("")

        return "\n".join(lines)

    def _build_pdf(self, md_content: str, path: Path) -> None:
        """Build PDF from Markdown content via WeasyPrint."""
        from weasyprint import HTML

        html_content = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<style>
body {{ font-family: sans-serif; margin: 40px; line-height: 1.6; color: #1f2937; }}
h1 {{ font-size: 24px; border-bottom: 2px solid #3b82f6; padding-bottom: 8px; }}
h2 {{ font-size: 18px; margin-top: 24px; color: #1e40af; }}
h3 {{ font-size: 14px; margin-top: 16px; }}
p {{ margin: 8px 0; }}
ul {{ margin: 8px 0 8px 20px; }}
li {{ margin: 4px 0; }}
code {{ background: #f3f4f6; padding: 2px 4px; border-radius: 3px; font-size: 12px; }}
pre {{ background: #f3f4f6; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 11px; }}
hr {{ border: none; border-top: 1px solid #e5e7eb; margin: 24px 0; }}
strong {{ color: #111827; }}
</style>
</head>
<body>
{_md_to_html(md_content)}
</body>
</html>"""
        HTML(string=html_content).write_pdf(str(path))

    def _build_odf(self, md_content: str, path: Path) -> None:
        """Build ODF from Markdown content."""
        try:
            from odf.opendocument import OpenDocumentText
            from odf.text import P, H, Span
            from odf.style import Style

            doc = OpenDocumentText()

            # Simple ODF generation: convert markdown lines to ODF paragraphs
            for line in md_content.split("\n"):
                stripped = line.strip()
                if not stripped:
                    continue

                if stripped.startswith("# "):
                    h = H(level=1)
                    h.addText(stripped[2:])
                    doc.text.addElement(h)
                elif stripped.startswith("## "):
                    h = H(level=2)
                    h.addText(stripped[3:])
                    doc.text.addElement(h)
                elif stripped.startswith("### "):
                    h = H(level=3)
                    h.addText(stripped[4:])
                    doc.text.addElement(h)
                elif stripped.startswith("- "):
                    p = P()
                    p.addText(f"• {stripped[2:]}")
                    doc.text.addElement(p)
                else:
                    p = P()
                    p.addText(stripped)
                    doc.text.addElement(p)

            doc.save(str(path))
        except ImportError:
            # Fallback: save as plain text
            path.write_text(md_content, encoding="utf-8")

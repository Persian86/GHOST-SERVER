"""Word / PowerPoint / Excel documents, built on the server and downloadable from any device."""
from __future__ import annotations

import re
import time
from pathlib import Path

from . import Ctx, tool
from .. import config, store

INLINE_BOLD = re.compile(r"\*\*(.+?)\*\*")


def split_bold(text: str) -> list[tuple[str, bool]]:
    parts, pos = [], 0
    for m in INLINE_BOLD.finditer(text):
        if m.start() > pos:
            parts.append((text[pos:m.start()], False))
        parts.append((m.group(1), True))
        pos = m.end()
    if pos < len(text):
        parts.append((text[pos:], False))
    return parts or [("", False)]


def parse_table(lines: list[str]) -> list[list[str]]:
    rows = []
    for ln in lines:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue  # separator row
        rows.append(cells)
    return rows


ACCENT = (0x5B, 0x8C, 0xFF)   # Ghost blue


# ─────────────────────────────────────────────────────────────────────────────
#  Word
# ─────────────────────────────────────────────────────────────────────────────
def build_docx(content: str, path: Path, title: str = "") -> Path:
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    def add_runs(par, text):
        for chunk, bold in split_bold(text):
            run = par.add_run(chunk)
            run.bold = bold

    lines = content.splitlines()
    if title and not any(l.startswith("# ") for l in lines):
        h = doc.add_heading(title, level=0)
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        s = ln.strip()
        if not s:
            i += 1
            continue
        if s.startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            rows = parse_table(block)
            if rows:
                ncols = max(len(r) for r in rows)
                table = doc.add_table(rows=len(rows), cols=ncols)
                table.style = "Light Grid Accent 1"
                for r, row in enumerate(rows):
                    for c in range(ncols):
                        cell = table.cell(r, c)
                        cell.text = row[c] if c < len(row) else ""
                        if r == 0:
                            for p in cell.paragraphs:
                                for run in p.runs:
                                    run.bold = True
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", s)
        if m:
            level = len(m.group(1))
            h = doc.add_heading(m.group(2), level=0 if level == 1 else level - 1)
            for run in h.runs:
                run.font.color.rgb = RGBColor(*ACCENT)
        elif re.match(r"^[-*•]\s+", s):
            add_runs(doc.add_paragraph(style="List Bullet"), re.sub(r"^[-*•]\s+", "", s))
        elif re.match(r"^\d+[.)]\s+", s):
            add_runs(doc.add_paragraph(style="List Number"), re.sub(r"^\d+[.)]\s+", "", s))
        else:
            add_runs(doc.add_paragraph(), s)
        i += 1
    doc.save(str(path))
    return path


# ─────────────────────────────────────────────────────────────────────────────
#  PowerPoint
# ─────────────────────────────────────────────────────────────────────────────
def _slides_from(content: str, title: str) -> list[dict]:
    slides, cur = [], None
    for ln in content.splitlines():
        s = ln.strip()
        if not s:
            continue
        m = re.match(r"^#{1,3}\s+(.*)", s)
        if m:
            cur = {"title": m.group(1).strip(), "bullets": [], "notes": []}
            slides.append(cur)
            continue
        if cur is None:
            cur = {"title": title or "Overview", "bullets": [], "notes": []}
            slides.append(cur)
        if s.startswith(">"):
            cur["notes"].append(s.lstrip("> ").strip())
        else:
            cur["bullets"].append(re.sub(r"^([-*•]|\d+[.)])\s+", "", s))
    return slides


def build_pptx(content: str, path: Path, title: str = "") -> Path:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    dark = RGBColor(0x0E, 0x11, 0x1A)
    light = RGBColor(0xEE, 0xF1, 0xF8)
    accent = RGBColor(*ACCENT)

    def paint_bg(slide):
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = dark

    slides = _slides_from(content, title)
    if title:
        s = prs.slides.add_slide(prs.slide_layouts[6])
        paint_bg(s)
        tb = s.shapes.add_textbox(Inches(0.9), Inches(2.6), Inches(11.5), Inches(1.6))
        p = tb.text_frame.paragraphs[0]
        p.text = title
        p.font.size, p.font.bold, p.font.color.rgb = Pt(48), True, light
        bar = s.shapes.add_shape(1, Inches(0.9), Inches(4.3), Inches(2.2), Inches(0.08))
        bar.fill.solid(); bar.fill.fore_color.rgb = accent; bar.line.fill.background()

    for sd in slides:
        s = prs.slides.add_slide(prs.slide_layouts[6])
        paint_bg(s)
        tb = s.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11.7), Inches(1.1))
        p = tb.text_frame.paragraphs[0]
        p.text = sd["title"]
        p.font.size, p.font.bold, p.font.color.rgb = Pt(34), True, light
        bar = s.shapes.add_shape(1, Inches(0.8), Inches(1.55), Inches(1.4), Inches(0.06))
        bar.fill.solid(); bar.fill.fore_color.rgb = accent; bar.line.fill.background()
        if sd["bullets"]:
            body = s.shapes.add_textbox(Inches(0.9), Inches(1.95), Inches(11.5), Inches(5.0))
            tf = body.text_frame
            tf.word_wrap = True
            size = 24 if len(sd["bullets"]) <= 5 else 20
            for n, b in enumerate(sd["bullets"]):
                para = tf.paragraphs[0] if n == 0 else tf.add_paragraph()
                para.space_after = Pt(10)
                for chunk, bold in split_bold("•  " + b):
                    r = para.add_run()
                    r.text = chunk
                    r.font.size, r.font.bold, r.font.color.rgb = Pt(size), bold, light
        if sd["notes"]:
            s.notes_slide.notes_text_frame.text = "\n".join(sd["notes"])
    prs.save(str(path))
    return path


# ─────────────────────────────────────────────────────────────────────────────
#  Excel
# ─────────────────────────────────────────────────────────────────────────────
def _num(v: str):
    t = v.replace(",", "").strip()
    if re.fullmatch(r"-?\$?\d+(\.\d+)?", t):
        t = t.replace("$", "")
        return float(t) if "." in t else int(t)
    if re.fullmatch(r"-?\d+(\.\d+)?%", t):
        return float(t[:-1]) / 100
    return v


def build_xlsx(content: str, path: Path, title: str = "") -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    sheets: list[tuple[str, list[str]]] = []
    name, block = title[:31] or "Sheet1", []
    for ln in content.splitlines():
        m = re.match(r"^#{1,3}\s+(.*)", ln.strip())
        if m:
            if block:
                sheets.append((name, block))
            name, block = re.sub(r"[\[\]:*?/\\]", "", m.group(1))[:31] or "Sheet", []
        elif ln.strip():
            block.append(ln)
    if block:
        sheets.append((name, block))

    wb = Workbook()
    wb.remove(wb.active)
    head_fill = PatternFill("solid", fgColor="5B8CFF")
    for sname, lines in sheets or [("Sheet1", [])]:
        ws = wb.create_sheet(sname)
        rows = parse_table(lines) if any("|" in l for l in lines) else \
            [[c.strip() for c in l.split(",")] for l in lines]
        for r, row in enumerate(rows, start=1):
            for c, val in enumerate(row, start=1):
                cell = ws.cell(row=r, column=c, value=val if val.startswith("=") or r == 1 else _num(val))
                if r == 1:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = head_fill
                    cell.alignment = Alignment(horizontal="center")
        for c in range(1, ws.max_column + 1):
            width = max((len(str(ws.cell(row=r, column=c).value or "")) for r in range(1, ws.max_row + 1)), default=8)
            ws.column_dimensions[get_column_letter(c)].width = min(max(10, width + 2), 50)
        ws.freeze_panes = "A2"
    wb.save(str(path))
    return path



_BUILDERS = {"document": (".docx", build_docx), "presentation": (".pptx", build_pptx),
             "spreadsheet": (".xlsx", build_xlsx)}
_ALIASES = {"docx": "document", "word": "document", "doc": "document", "pptx": "presentation",
            "ppt": "presentation", "slides": "presentation", "deck": "presentation",
            "xlsx": "spreadsheet", "excel": "spreadsheet", "sheet": "spreadsheet"}
_FORMATS = {
    "document": "Markdown: '# ' title, '## ' sections, paragraphs, '- ' bullets, '1. ' items, '|' tables.",
    "presentation": "One slide per '# ' heading, 3-6 '- ' bullets under 12 words each, optional '> ' "
                    "speaker notes. 6-10 slides.",
    "spreadsheet": "A pipe table with a header row; Excel formulas starting with '=' where totals belong; "
                   "'## Name' starts another sheet.",
}


def _safe(name: str, ext: str) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name).strip(" .")[:80] or "Ghost document"
    return stem + ext


@tool("create_document", """Create a real Word document, PowerPoint deck or Excel spreadsheet on the home
server. Pass the full `content` as simple markdown, or just a `topic` and Ghost writes it with Claude.
Returns a download link that works on every device at home.""",
      {"type": "OBJECT", "properties": {
          "kind": {"type": "STRING", "description": "document | presentation | spreadsheet"},
          "title": {"type": "STRING"}, "content": {"type": "STRING"}, "topic": {"type": "STRING"},
          "instructions": {"type": "STRING", "description": "Tone, length, audience"}},
       "required": ["kind"]})
def create_document(args: dict, ctx: Ctx) -> str:
    kind = _ALIASES.get(str(args.get("kind", "document")).lower(), str(args.get("kind", "document")).lower())
    if kind not in _BUILDERS:
        return f"Unknown kind '{kind}'."
    title = str(args.get("title") or "").strip()
    content = str(args.get("content") or "").strip()
    if not content:
        topic = str(args.get("topic") or title).strip()
        if not topic:
            return "Give me the content or a topic."
        from .brain import claude
        content, _ = claude().complete([{"role": "user", "content":
            f"Write the content for a {kind} about: {topic}. Extra: {args.get('instructions') or 'none'}. "
            f"Format strictly as {_FORMATS[kind]} No code fences."}], timeout=180, max_tokens=6000)
    content = re.sub(r"^```\w*\s*|```\s*$", "", content.strip(), flags=re.M)
    ext, build = _BUILDERS[kind]
    folder = config.FILES_DIR / time.strftime("%Y-%m")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / _safe(title or str(args.get("topic") or kind), ext)
    n = 2
    while path.exists():
        path = path.with_name(f"{path.stem} ({n}){ext}")
        n += 1
    build(content, path, title)
    fid = store.add_file(path.name, path, ctx.device_id)
    return f"Created {path.name}. Download: /files/{fid}  (the device can open {{server}}/files/{fid})"

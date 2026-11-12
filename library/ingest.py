"""Extract page-cited text and table chunks from the pinned arXiv PDFs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import pymupdf

from library.fetch import MANIFEST_PATH, PDF_DIR, PaperSpec, _verify_pdf


CHUNKS_DIR = Path(__file__).resolve().parent / "chunks"


@dataclass(frozen=True)
class ChunkSetting:
    """A proposed retrieval comparison setting; values are not measured yet."""

    name: str
    chunk_chars: int
    overlap_chars: int
    top_k: int
    rerank: bool

    def __post_init__(self) -> None:
        if self.chunk_chars <= 0 or self.top_k <= 0:
            raise ValueError("Chunk size and top_k must be positive")
        if self.overlap_chars < 0 or self.overlap_chars >= self.chunk_chars:
            raise ValueError("Overlap must be non-negative and smaller than chunk size")


# Candidate settings for the retrieval experiment, not performance claims.
SETTINGS: tuple[ChunkSetting, ...] = (
    ChunkSetting("R1", chunk_chars=900, overlap_chars=90, top_k=3, rerank=False),
    ChunkSetting("R2", chunk_chars=1600, overlap_chars=160, top_k=4, rerank=False),
    ChunkSetting("R3", chunk_chars=2600, overlap_chars=260, top_k=6, rerank=True),
)


SECTION_HEADING = re.compile(r"^(\d+(?:\.\d+)*)(?:[.)])?\s+(.{2,120})$")
APPENDIX_HEADING = re.compile(r"^appendix(?:\s+[a-z0-9]+)?(?:\s+.+)?$", re.IGNORECASE)
TABLE_CAPTION = re.compile(r"^(?:table|tab\.)\s*\d+[.:]?(.*)$", re.IGNORECASE)
REFERENCES_LABELS = {"references", "bibliography"}
PLAIN_HEADINGS = {"abstract", "acknowledgments", "acknowledgements"}


@dataclass(frozen=True)
class PageSection:
    page_number: int
    section: str
    text: str


def _is_heading(line: str) -> tuple[str | None, bool]:
    normalized = " ".join(line.split())
    if normalized.casefold() in REFERENCES_LABELS:
        return normalized, True
    if normalized.casefold() in PLAIN_HEADINGS or APPENDIX_HEADING.fullmatch(normalized):
        return normalized, False
    match = SECTION_HEADING.fullmatch(normalized)
    if match:
        heading = f"{match.group(1)} {match.group(2)}"
        if match.group(2).casefold() in REFERENCES_LABELS:
            return heading, True
        return heading, False
    return None, False


def _page_sections(
    text: str,
    page_number: int,
    *,
    exclude_references: bool,
    running_title: str | None = None,
    starts_in_references: bool = False,
) -> tuple[list[PageSection], bool]:
    """Split one page by headings while retaining its 1-based PDF page index."""
    sections: list[PageSection] = []
    active_heading = "References" if starts_in_references else "Front matter"
    active_lines: list[str] = []
    in_references = starts_in_references

    def flush() -> None:
        body = "\n".join(active_lines).strip()
        if body:
            sections.append(PageSection(page_number, active_heading, body))
        active_lines.clear()

    lines = [line.strip() for line in text.splitlines()]
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line:
            continue
        if running_title and line.casefold() == running_title.casefold():
            continue

        # Some PDF layouts extract the section number and title on separate lines.
        if re.fullmatch(r"\d+(?:\.\d+)*[.)]?", line) and index < len(lines):
            following = lines[index].strip()
            if following and len(following) <= 120 and following[0].isupper():
                line = f"{line.rstrip('.)')} {following}"
                index += 1

        heading, is_references = _is_heading(line)
        if is_references:
            flush()
            active_heading = heading or "References"
            in_references = True
            if not exclude_references:
                active_lines.append(active_heading)
            continue
        if heading:
            flush()
            active_heading = heading
            if in_references:
                in_references = False
        if in_references and exclude_references:
            continue
        active_lines.append(line)

    flush()
    return sections, in_references


def _extract_table_text(page: pymupdf.Page) -> list[str]:
    """Extract structural tables where MuPDF recognizes them; skip unsupported pages."""
    try:
        finder = page.find_tables()
    except (AttributeError, RuntimeError, ValueError):
        return []
    table_texts: list[str] = []
    for table in finder.tables:
        rows = table.extract() or []
        rendered_rows = [
            " | ".join(str(cell or "").strip() for cell in row).strip()
            for row in rows
        ]
        rendered = "\n".join(row for row in rendered_rows if row and row.replace("|", "").strip())
        if rendered:
            table_texts.append(rendered)
    return table_texts


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + size // 2, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        next_start = max(start + 1, end - overlap)
        while next_start < len(text) and text[next_start].isspace():
            next_start += 1
        start = next_start
    return chunks


def _chunk_id(
    versioned_id: str,
    setting: str,
    page: int,
    section: str,
    kind: str,
    ordinal: int,
    text: str,
) -> str:
    seed = f"{versioned_id}|{setting}|{page}|{section}|{kind}|{ordinal}|{text}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def _build_page_chunks(
    versioned_id: str,
    title: str,
    page_number: int,
    section_texts: Sequence[PageSection],
    tables: Sequence[str],
    setting: ChunkSetting,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    ordinal = 0
    for section in section_texts:
        section_text = f"{section.section}\n{section.text}"
        for text in _split_text(section_text, setting.chunk_chars, setting.overlap_chars):
            chunks.append(
                {
                    "chunk_id": _chunk_id(
                        versioned_id, setting.name, page_number, section.section, "text", ordinal, text
                    ),
                    "paper_id": versioned_id,
                    "paper_title": title,
                    "page_number": page_number,
                    "page_start": page_number,
                    "page_end": page_number,
                    "section": section.section,
                    "kind": "text",
                    "setting": setting.name,
                    "text": text,
                }
            )
            ordinal += 1
    for table_index, text in enumerate(tables, start=1):
        caption = next(
            (
                match.group(0)
                for section in section_texts
                for line in section.text.splitlines()
                if (match := TABLE_CAPTION.match(line.strip()))
            ),
            "",
        )
        table_section = caption or "Table"
        chunks.append(
            {
                "chunk_id": _chunk_id(
                    versioned_id, setting.name, page_number, table_section, "table", table_index, text
                ),
                "paper_id": versioned_id,
                "paper_title": title,
                "page_number": page_number,
                "page_start": page_number,
                "page_end": page_number,
                "section": table_section,
                "kind": "table",
                "setting": setting.name,
                "text": text,
            }
        )
    return chunks


def build_chunks(
    manifest: Mapping[str, Any],
    *,
    pdf_dir: str | Path = PDF_DIR,
    settings: Sequence[ChunkSetting] = SETTINGS,
    exclude_references: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """Build stable per-setting chunks with 1-based PDF viewer page indices."""
    outputs = {setting.name: [] for setting in settings}
    pdf_dir = Path(pdf_dir)
    for paper in manifest.get("papers", []):
        pdf_path = pdf_dir / Path(paper["filename"]).name
        if not pdf_path.exists():
            raise FileNotFoundError(f"Missing paper PDF: {pdf_path}")
        data = pdf_path.read_bytes()
        if paper.get("sha256") and hashlib.sha256(data).hexdigest() != paper["sha256"]:
            raise ValueError(f"PDF checksum does not match manifest: {pdf_path}")
        _verify_pdf(
            PaperSpec(paper["arxiv_id"], paper["version"], paper["title"]), data
        )
        with pymupdf.open(pdf_path) as doc:
            in_references = False
            for page_index, page in enumerate(doc, start=1):
                sections, in_references = _page_sections(
                    page.get_text(),
                    page_index,
                    exclude_references=exclude_references,
                    running_title=paper["title"],
                    starts_in_references=in_references,
                )
                tables = _extract_table_text(page)
                for setting in settings:
                    outputs[setting.name].extend(
                        _build_page_chunks(
                            paper["versioned_id"],
                            paper["title"],
                            page_index,
                            sections,
                            tables,
                            setting,
                        )
                    )
    return outputs


def write_chunks(
    chunks_by_setting: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    output_dir: str | Path = CHUNKS_DIR,
) -> list[Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for setting, chunks in chunks_by_setting.items():
        path = output_dir / f"{setting}.jsonl"
        with path.open("w", encoding="utf-8") as stream:
            for chunk in chunks:
                stream.write(json.dumps(dict(chunk), ensure_ascii=False, sort_keys=True) + "\n")
        written.append(path)
    return written


def ingest_papers(
    *,
    manifest_path: str | Path = MANIFEST_PATH,
    pdf_dir: str | Path = PDF_DIR,
    output_dir: str | Path = CHUNKS_DIR,
    exclude_references: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    chunks = build_chunks(
        manifest, pdf_dir=pdf_dir, exclude_references=exclude_references
    )
    write_chunks(chunks, output_dir=output_dir)
    return chunks


if __name__ == "__main__":
    result = ingest_papers()
    for setting, chunks in result.items():
        table_count = sum(chunk["kind"] == "table" for chunk in chunks)
        print(f"{setting}: {len(chunks)} chunks ({table_count} table chunks)")

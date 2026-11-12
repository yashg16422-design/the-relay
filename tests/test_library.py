from __future__ import annotations

import hashlib
import json

import httpx
import pymupdf
import pytest

from library.fetch import PAPERS, fetch_papers
from library.ingest import (
    ChunkSetting,
    PageSection,
    _build_page_chunks,
    _extract_table_text,
    _page_sections,
    _split_text,
)


def make_pdf(title: str) -> bytes:
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), title)
        return doc.tobytes()


def client_for_responses(responses: dict[str, bytes], calls: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=responses[str(request.url)],
        )

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_fetch_saves_exact_versions_hashes_and_manifest(tmp_path) -> None:
    responses = {paper.source_url: make_pdf(paper.title) for paper in PAPERS}
    calls: list[str] = []
    client = client_for_responses(responses, calls)

    entries = fetch_papers(
        pdf_dir=tmp_path / "pdfs",
        manifest_path=tmp_path / "MANIFEST.json",
        client=client,
    )
    client.close()

    assert len(calls) == 3
    assert [entry["versioned_id"] for entry in entries] == [paper.versioned_id for paper in PAPERS]
    for entry in entries:
        pdf = tmp_path / entry["filename"]
        assert pdf.read_bytes().startswith(b"%PDF-")
        assert entry["sha256"] == hashlib.sha256(pdf.read_bytes()).hexdigest()
        assert entry["page_index_base"] == 1
        assert entry["source_url"].endswith(entry["versioned_id"])
    manifest = json.loads((tmp_path / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["page_index_base"] == 1
    assert len(manifest["papers"]) == 3


def test_existing_pdf_files_are_reused_without_network_or_overwrite(tmp_path) -> None:
    responses = {paper.source_url: make_pdf(paper.title) for paper in PAPERS}
    first_calls: list[str] = []
    client = client_for_responses(responses, first_calls)
    fetch_papers(
        pdf_dir=tmp_path / "pdfs",
        manifest_path=tmp_path / "MANIFEST.json",
        client=client,
    )
    client.close()
    hashes_before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (tmp_path / "pdfs").glob("*.pdf")
    }

    no_calls: list[str] = []
    client = client_for_responses({}, no_calls)
    fetch_papers(
        pdf_dir=tmp_path / "pdfs",
        manifest_path=tmp_path / "MANIFEST.json",
        client=client,
    )
    client.close()

    hashes_after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (tmp_path / "pdfs").glob("*.pdf")
    }
    assert no_calls == []
    assert hashes_after == hashes_before


def test_fetch_rejects_pdf_with_wrong_title(tmp_path) -> None:
    wrong = {paper.source_url: make_pdf("A different paper") for paper in PAPERS}
    calls: list[str] = []
    client = client_for_responses(wrong, calls)

    with pytest.raises(ValueError, match="expected title"):
        fetch_papers(
            pdf_dir=tmp_path / "pdfs",
            manifest_path=tmp_path / "MANIFEST.json",
            client=client,
        )
    client.close()
    assert not (tmp_path / "pdfs" / PAPERS[0].filename).exists()


def test_page_section_parser_uses_pdf_viewer_page_and_drops_cross_page_references() -> None:
    first_page, in_references = _page_sections(
        "1 Introduction\nIntro text\nReferences\nVaswani et al. (2017)",
        3,
        exclude_references=True,
    )
    second_page, in_references = _page_sections(
        "Vaswani et al. (2017), continued\nAppendix A Additional Details\nAppendix text",
        4,
        exclude_references=True,
        starts_in_references=in_references,
    )

    assert [section.page_number for section in first_page] == [3]
    assert "References" not in "\n".join(section.text for section in first_page)
    assert in_references is False
    assert any(section.section == "Appendix A Additional Details" for section in second_page)
    assert all("Vaswani et al." not in section.text for section in second_page)


def test_references_can_be_retained_as_an_explicit_option() -> None:
    sections, _ = _page_sections(
        "References\nVaswani et al. (2017)", 14, exclude_references=False
    )

    assert sections[0].section == "References"
    assert "Vaswani" in sections[0].text


def test_chunk_splitting_respects_size_and_overlap() -> None:
    text = "one two three four five six seven eight nine ten"
    chunks = _split_text(text, size=15, overlap=4)

    assert len(chunks) > 1
    assert all(len(chunk) <= 15 for chunk in chunks)
    assert "four" in chunks[0] or "four" in chunks[1]


def test_chunk_records_keep_exact_page_and_table_kind() -> None:
    setting = ChunkSetting("test", chunk_chars=100, overlap_chars=5, top_k=2, rerank=False)
    chunks = _build_page_chunks(
        "1706.03762v7",
        "Attention Is All You Need",
        7,
        [PageSection(7, "3.1 Encoder", "Table 1: Results\nA short passage")],
        ["Method | Score\nA | 1.0"],
        setting,
    )

    assert {chunk["kind"] for chunk in chunks} == {"text", "table"}
    assert all(chunk["page_number"] == 7 for chunk in chunks)
    table = next(chunk for chunk in chunks if chunk["kind"] == "table")
    assert table["section"] == "Table 1: Results"
    assert "Method | Score" in table["text"]


def test_structural_table_extraction_returns_rows_as_text() -> None:
    class Table:
        def extract(self):
            return [["Method", "Score"], ["A", "1.0"]]

    class Finder:
        tables = [Table()]

    class Page:
        def find_tables(self):
            return Finder()

    assert _extract_table_text(Page()) == ["Method | Score\nA | 1.0"]

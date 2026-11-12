"""Fetch and checksum the exact paper versions required by the assignment."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pymupdf


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = PROJECT_ROOT / "library" / "pdfs"
MANIFEST_PATH = PROJECT_ROOT / "library" / "MANIFEST.json"


@dataclass(frozen=True)
class PaperSpec:
    arxiv_id: str
    version: int
    title: str

    @property
    def versioned_id(self) -> str:
        return f"{self.arxiv_id}v{self.version}"

    @property
    def source_url(self) -> str:
        return f"https://arxiv.org/pdf/{self.versioned_id}"

    @property
    def filename(self) -> str:
        return f"{self.versioned_id}.pdf"


PAPERS = (
    PaperSpec("1706.03762", 7, "Attention Is All You Need"),
    PaperSpec("2410.00037", 1, "Moshi: a speech-text foundation model for real-time dialogue"),
    PaperSpec("2507.07955", 2, "Dynamic Chunking for End-to-End Hierarchical Sequence Modeling"),
)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _verify_pdf(spec: PaperSpec, data: bytes) -> int:
    if not data.startswith(b"%PDF-"):
        raise ValueError(f"{spec.versioned_id}: response is not a PDF")
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        if not doc.page_count:
            raise ValueError(f"{spec.versioned_id}: PDF has no pages")
        opening_text = " ".join(doc[i].get_text() for i in range(min(2, doc.page_count)))
        if _normalize(spec.title) not in _normalize(opening_text):
            raise ValueError(
                f"{spec.versioned_id}: expected title not found in the first two PDF pages"
            )
        return doc.page_count


def _resolved_version_matches(url: str, spec: PaperSpec) -> bool:
    path = httpx.URL(url).path.rstrip("/")
    return path.endswith(f"/{spec.versioned_id}") or path.endswith(
        f"/{spec.versioned_id}.pdf"
    )


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "page_index_base": 1, "papers": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("papers"), list):
        raise ValueError(f"Invalid paper manifest: {path}")
    return data


def fetch_papers(
    *,
    pdf_dir: str | Path = PDF_DIR,
    manifest_path: str | Path = MANIFEST_PATH,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Fetch required versions without overwriting existing paper PDFs.

    Existing versioned files are verified and reused. New files are created
    exclusively, so a rerun cannot silently replace source data.
    """
    pdf_dir = Path(pdf_dir)
    manifest_path = Path(manifest_path)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    owns_client = client is None
    http = client or httpx.Client(follow_redirects=True, timeout=60.0)
    manifest = _read_manifest(manifest_path)
    existing = {entry.get("versioned_id"): entry for entry in manifest["papers"]}
    resulting: list[dict[str, Any]] = []

    try:
        for spec in PAPERS:
            target = pdf_dir / spec.filename
            requested_url = spec.source_url
            if target.exists():
                data = target.read_bytes()
                pages = _verify_pdf(spec, data)
                prior_entry = existing.get(spec.versioned_id, {})
                resolved_url = prior_entry.get(
                    "resolved_url", requested_url
                )
                digest = _sha256(data)
                if prior_entry.get("sha256") and prior_entry["sha256"] != digest:
                    raise ValueError(
                        f"{spec.versioned_id}: existing PDF hash differs from the manifest"
                    )
            else:
                response = http.get(requested_url)
                response.raise_for_status()
                resolved_url = str(response.url)
                if not _resolved_version_matches(resolved_url, spec):
                    raise ValueError(
                        f"arXiv resolved {requested_url} to unexpected URL {resolved_url}"
                    )
                data = response.content
                pages = _verify_pdf(spec, data)
                with target.open("xb") as stream:
                    stream.write(data)

            entry = {
                "arxiv_id": spec.arxiv_id,
                "version": spec.version,
                "versioned_id": spec.versioned_id,
                "title": spec.title,
                "filename": f"pdfs/{spec.filename}",
                "source_url": requested_url,
                "resolved_url": resolved_url,
                "sha256": _sha256(data),
                "page_count": pages,
                "page_index_base": 1,
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            resulting.append(entry)
    finally:
        if owns_client:
            http.close()

    manifest["papers"] = [
        entry for entry in manifest["papers"] if entry.get("versioned_id") not in {
            paper["versioned_id"] for paper in resulting
        }
    ] + resulting
    manifest["papers"].sort(key=lambda item: item["arxiv_id"])
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(manifest_path)
    return resulting


if __name__ == "__main__":
    for paper in fetch_papers():
        print(f"{paper['versioned_id']}  {paper['sha256']}  {paper['page_count']} pages")

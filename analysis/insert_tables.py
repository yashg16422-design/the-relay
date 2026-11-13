#!/usr/bin/env python3
"""Put the generated tables into WRITEUP.md and count the words of prose.

Zero provider calls. Replaces everything between the two table markers in
WRITEUP.md with Table 1 (analysis/table1.py) and Tables 2a, 2b, 3
(analysis/tables.py). No number in the tables is typed by hand. Then prints the
word count of the prose above the tables (limit 1,150). Run from the repo root:

    .venv/bin/python analysis/insert_tables.py            # from results/results.csv
    .venv/bin/python analysis/insert_tables.py --preview  # from the pending view
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis import table1, tables  # noqa: E402

START, STOP = "<!-- TABLES START -->", "<!-- TABLES END -->"


def main() -> None:
    source = tables.RESULTS.with_name("results_view_preview.csv") if "--preview" in sys.argv else tables.RESULTS
    block = "\n".join([START, "", table1.render(), tables.render(tables.read(source)), STOP])
    path = ROOT / "WRITEUP.md"
    text = path.read_text(encoding="utf-8")
    head, rest = text.split(START, 1)
    tail = rest.split(STOP, 1)[1]
    path.write_text(head + block + tail, encoding="utf-8")
    (ROOT / "results" / "generated_tables.md").write_text(block + "\n", encoding="utf-8")
    prose = re.sub(r"<!--.*?-->", "", head, flags=re.DOTALL)
    words = len(re.findall(r"\S+", prose))
    print(f"tables inserted from {source.relative_to(ROOT)}; prose above the tables: {words} words (limit 1,150)")


if __name__ == "__main__":
    main()

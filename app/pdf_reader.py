"""
Step 1: read the PDF into a flat list of "items" in reading order.

An item is a plain dict, one of:
  {"type": "line",  "page": 2, "text": "...", "size": 11.0, "bold": True, "x": 33.0, "y": 120.5}
  {"type": "table", "page": 4, "rows": [["Code", "Meaning", ...], ["E1", ...]], "y": 167.0}
  {"type": "empty_page", "page": 5}   <- page with no text layer (would need OCR)

We do NOT decide what is a heading here. That is the parser's job 
This file only answers: "what text is on the page, and what does it look like?"
"""
import unicodedata

import pymupdf


def clean_text(text):
    # NFKC turns ligatures into normal letters: "ﬁ" -> "fi", "ﬀ" -> "ff".
    # Without this, searching for "cuff" would not find "cuﬀ".
    text = unicodedata.normalize("NFKC", text)
    # the PDF uses a non-breaking hyphen (U+2011), NFKC makes it U+2010; we want a plain "-"
    text = text.replace("‐", "-")
    return text.strip()


def read_tables(page):
    """Find tables on a page. Returns a list of (bbox, rows)."""
    tables = []
    for table in page.find_tables().tables:
        rows = []
        for row in table.rows:
            cells = []
            for cell_box in row.cells:
                # table.extract() puts commas in the wrong place on this PDF
                # ("displays E1\n,"), so we read the text inside each cell box ourselves.
                text = page.get_text("text", clip=cell_box) if cell_box else ""
                cells.append(clean_text(text.replace("\n", " ")))
            rows.append(cells)
        tables.append((pymupdf.Rect(table.bbox), rows))
    return tables


def read_pdf(path):
    items = []
    doc = pymupdf.open(path)

    for page_index, page in enumerate(doc):
        page_number = page_index + 1

        # No text at all -> scanned page. We don't OCR it, we say so loudly.
        if not page.get_text().strip():
            items.append({"type": "empty_page", "page": page_number})
            continue

        tables = read_tables(page)
        page_items = []

        # Each table becomes ONE item, placed where the table starts on the page.
        for bbox, rows in tables:
            page_items.append({"type": "table", "page": page_number, "rows": rows, "y": bbox.y0})

        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:  # 0 = text, 1 = image
                continue
            for line in block["lines"]:
                line_box = pymupdf.Rect(line["bbox"])

                # skip lines that are inside a table, the table item already has them
                if any(bbox.contains(line_box) for bbox, _ in tables):
                    continue

                text = clean_text("".join(span["text"] for span in line["spans"]))
                if not text:
                    continue

                first_span = line["spans"][0]
                page_items.append({
                    "type": "line",
                    "page": page_number,
                    "text": text,
                    "size": round(first_span["size"], 1),
                    "bold": bool(first_span["flags"] & 16),  # bit 16 = bold in PyMuPDF
                    "x": round(line_box.x0, 1),
                    "y": round(line_box.y0, 1),
                })

        # top-to-bottom reading order (single-column document)
        page_items.sort(key=lambda item: item["y"])
        items.extend(page_items)

    return items


if __name__ == "__main__":
    # quick look:  python -m app.pdf_reader data/ct200_manual.pdf
    import sys

    for item in read_pdf(sys.argv[1]):
        if item["type"] == "line":
            style = "BOLD" if item["bold"] else "    "
            print(f"p{item['page']} {item['size']:>4} {style} x={item['x']:<5} {item['text']}")
        else:
            print(f"p{item['page']} [{item['type'].upper()}] {item.get('rows', '')}")

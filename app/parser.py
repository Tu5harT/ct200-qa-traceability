import hashlib
import re

from app.pdf_reader import read_pdf

# "2.1.1.1 Battery Life" or "1. Device Overview"
HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")
# "1. Normal: systolic < 120 ..."
LIST_ITEM = re.compile(r"^\d+\.\s")

LEFT_MARGIN = 40  # body and headings start at x=33, list items are indented to ~51


def make_hash(text):
    # ignore spacing differences, so a page break doesn't count as a change
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()


def as_numbers(number):
    return [int(n) for n in number.split(".")]


def find_parent(number, nodes):
    # nearest earlier heading whose number is a prefix of ours ("2.1" for "2.1.1.1")
    for node in reversed(nodes):
        if number.startswith(node["number"] + "."):
            return node
    return None


def parse_items(items):
    title = []
    nodes = []
    warnings = []
    current = None
    after_table = False

    for item in items:
        if item["type"] == "empty_page":
            warnings.append(f"page {item['page']} has no text, probably scanned, needs OCR")
            continue

        if item["type"] == "table":
            if current is None:
                warnings.append(f"table on page {item['page']} is outside any section, skipped")
                continue
            for row in item["rows"]:
                current["lines"].append(" | ".join(row))
            after_table = True
            continue

        text = item["text"]
        match = HEADING.match(text)

        # don't trust font size here, 3.2 and 2.1.1.1 are bold but body-sized.
        # list items like "1. Normal:" look the same but aren't bold
        if match and item["bold"]:
            number, heading = match.groups()
            current = add_heading(number, heading, item["page"], nodes, warnings)
            after_table = False
            continue

        # big bold text before the first section = document title
        if current is None and item["bold"]:
            title.append(text)
            continue

        if current is None:
            warnings.append(f"text before first section on page {item['page']}: {text[:40]}")
            continue

        is_list_item = item["x"] > LEFT_MARGIN and LIST_ITEM.match(text)
        if is_list_item or after_table or not current["lines"]:
            current["lines"].append(text)
        else:
            # shortcut: wrapped lines get glued together, so paragraph breaks inside a section are lost.
            # fine for this manual (one paragraph per section), use line spacing if that changes
            current["lines"][-1] += " " + text
        after_table = False

    for node in nodes:
        node["body"] = "\n".join(node.pop("lines"))
        node["hash"] = make_hash(node["body"])

    return {"title": " ".join(title), "nodes": nodes, "warnings": warnings}


def add_heading(number, heading, page, nodes, warnings):
    level = len(number.split("."))
    parent = None

    if level > 1:
        parent = find_parent(number, nodes)
        if parent is None:
            warnings.append(f"{number} has no parent section, put it at top level")
        elif parent["level"] != level - 1:
            warnings.append(f"{number} skips a level, attached to {parent['number']}")

    parent_id = parent["id"] if parent else None

    # out of order check, compare with the previous sibling (e.g. 3.4 then 3.3)
    siblings = [n for n in nodes if n["parent"] == parent_id]
    if siblings and as_numbers(number) < as_numbers(siblings[-1]["number"]):
        warnings.append(f"{number} comes after {siblings[-1]['number']}, kept in document order")

    # the id is the section number. if a number shows up twice, add a suffix so ids stay unique
    node_id = number
    count = 2
    while any(n["id"] == node_id for n in nodes):
        node_id = f"{number}-{count}"
        count += 1
    if node_id != number:
        warnings.append(f"section number {number} appears more than once, second one is {node_id}")

    node = {
        "id": node_id,
        "number": number,
        "heading": heading,
        "level": level,
        "parent": parent_id,
        "page": page,
        "order": len(nodes),
        "lines": [],
    }
    nodes.append(node)
    return node


def parse_pdf(path):
    return parse_items(read_pdf(path))


if __name__ == "__main__":
    import sys

    result = parse_pdf(sys.argv[1])
    print("TITLE:", result["title"])
    for node in result["nodes"]:
        indent = "  " * (node["level"] - 1)
        print(f"{indent}{node['id']} {node['heading']}  (parent={node['parent']}, hash={node['hash'][:8]})")
    print("\nWARNINGS:")
    for w in result["warnings"]:
        print(" -", w)

from app.parser import parse_items, parse_pdf

v1 = parse_pdf("data/ct200_manual.pdf")
nodes = {n["id"]: n for n in v1["nodes"]}


def test_duplicate_heading_gives_two_nodes_with_different_parents():
    error_codes = [n for n in v1["nodes"] if n["heading"] == "Error Codes"]
    assert len(error_codes) == 2
    assert error_codes[0]["id"] == "4.2" and error_codes[0]["parent"] == "4"
    assert error_codes[1]["id"] == "7.1" and error_codes[1]["parent"] == "7"


def test_skipped_level_goes_under_nearest_parent():
    # there's no 2.1.1 in the pdf
    assert nodes["2.1.1.1"]["parent"] == "2.1"
    assert nodes["2.1.1.1"]["level"] == 4
    assert "2.1.1.1 skips a level, attached to 2.1" in v1["warnings"]


def test_small_font_heading_is_still_a_heading():
    # 3.2 is printed in body size font
    assert nodes["3.2"]["heading"] == "Cuff Inflation Sequence"
    assert nodes["3.2"]["parent"] == "3"
    assert "3.2" not in nodes["3.1"]["body"]


def test_out_of_order_sections_keep_correct_parent():
    assert nodes["3.4"]["parent"] == "3"
    assert nodes["3.3"]["parent"] == "3"
    assert nodes["3.4"]["order"] < nodes["3.3"]["order"]
    assert any("3.3 comes after 3.4" in w for w in v1["warnings"])


def test_numbered_list_is_not_turned_into_sections():
    top_level = [n["heading"] for n in v1["nodes"] if n["level"] == 1]
    assert "Normal: systolic < 120 and diastolic < 80" not in top_level
    assert len(top_level) == 8
    # wrapped list item got joined back
    assert "5. Hypertensive Crisis: systolic > 180 or diastolic > 120 — device recommends seeking" in nodes["3.3"]["body"]


def test_section_split_across_pages_stays_together():
    assert nodes["3.1"]["body"].startswith("Press and hold the power button")
    assert nodes["3.1"]["body"].endswith("the time of measurement.")


def test_title_is_joined():
    assert v1["title"] == "CardioTrack CT-200 Home Blood Pressure Monitor — Technical & User Manual"


def line(text, bold=False, x=33, page=1):
    return {"type": "line", "text": text, "bold": bold, "x": x, "page": page, "size": 11}


def test_same_section_number_twice_gets_unique_id():
    result = parse_items([line("1. Intro", bold=True), line("hello"), line("1. Intro", bold=True)])
    assert [n["id"] for n in result["nodes"]] == ["1", "1-2"]
    assert any("more than once" in w for w in result["warnings"])


def test_scanned_page_is_reported_not_skipped():
    result = parse_items([line("1. Intro", bold=True), {"type": "empty_page", "page": 2}])
    assert any("page 2 has no text" in w for w in result["warnings"])

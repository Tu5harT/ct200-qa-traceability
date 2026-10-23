from app.pdf_reader import clean_text, read_pdf

V1 = "data/ct200_manual.pdf"


def test_ligatures_and_hyphens_are_cleaned():
    assert clean_text("Cuﬀ Speciﬁcations") == "Cuff Specifications"
    assert clean_text("low‑battery") == "low-battery"


def test_error_code_table_is_one_item_with_clean_cells():
    tables = [item for item in read_pdf(V1) if item["type"] == "table"]
    assert len(tables) == 2  # 2.1 specs + 4.2 error codes

    error_table = tables[1]["rows"]
    assert error_table[0] == ["Code", "Meaning", "Device Behavior"]
    # multi-line cell is joined and the comma is in the right place
    assert error_table[1] == ["E1", "Cuff not connected or leak detected", "Aborts measurement, displays E1"]

"""Reading CSV / Excel files safely into tables. Pure functions: no database, no network."""
import io
import zipfile

import pytest
from openpyxl import Workbook

from app.services.connectors import file_parsing as fp
from app.services.connectors.file_parsing import FileParseError, parse_file


def csv_bytes(text: str, encoding="utf-8") -> bytes:
    return text.encode(encoding)


def test_a_plain_csv_becomes_a_typed_table():
    [t] = parse_file("payroll_2026.csv", csv_bytes("emp_id,name,salary,hired,active\nE1,Ann,1000.50,2020-01-05,true\nE2,Bob,900,2021-03-10,false\n"))
    assert t.name == "payroll_2026" and t.headers == ["emp_id", "name", "salary", "hired", "active"]
    assert t.types == {"emp_id": "string", "name": "string", "salary": "double", "hired": "date", "active": "boolean"}
    assert t.rows[0] == {"emp_id": "E1", "name": "Ann", "salary": 1000.5, "hired": "2020-01-05", "active": True}
    assert isinstance(t.rows[1]["salary"], float) and t.rows[1]["active"] is False  # numbers are numbers: a rule can say salary > 950


def test_whole_number_columns_are_integers_and_mixed_columns_stay_text():
    [t] = parse_file("x.csv", csv_bytes("qty,mixed\n1,1\n2,two\n3,3\n"))
    assert t.types == {"qty": "integer", "mixed": "string"}
    assert [r["qty"] for r in t.rows] == [1, 2, 3] and [r["mixed"] for r in t.rows] == ["1", "two", "3"]


@pytest.mark.parametrize(
    "value",
    ["000123", "0123", "+27821234567", "1234567890123456789", "007", "00.5", "0x1F", "1,000", "12 34"],
)
def test_identifier_like_values_are_never_turned_into_numbers(value):
    """Typing them would change them ("000123" -> 123, "+27..." -> 27...), and a join to the same ids elsewhere would fail."""
    [t] = parse_file("ids.csv", csv_bytes(f'code\n"{value}"\n"{value}"\n'))
    assert t.types == {"code": "string"} and [r["code"] for r in t.rows] == [value, value]


@pytest.mark.parametrize("value, kind", [("0", "integer"), ("-5", "integer"), ("123456789012345678", "integer"), ("0.5", "double"), (".5", "double"), ("-0.25", "double"), ("1e3", "double"), ("10.", "double")])
def test_real_numbers_are_still_numbers(value, kind):
    [t] = parse_file("n.csv", csv_bytes(f"n\n{value}\n{value}\n"))
    assert t.types == {"n": kind}


def test_a_column_is_a_number_only_if_every_value_is():
    [t] = parse_file("m.csv", csv_bytes("n\n1\n2\n007\n"))  # one identifier-looking value makes the whole column text
    assert t.types == {"n": "string"} and [r["n"] for r in t.rows] == ["1", "2", "007"]


def test_records_become_a_typed_table_like_a_file():
    from app.services.connectors.file_parsing import table_from_records

    t = table_from_records("invoices", [{"id": "A1", "amount": "10.5", "paid": "true"}, {"id": "A2", "amount": "7", "extra": 3}, {"id": "A3"}])
    assert t.headers == ["id", "amount", "paid", "extra"]
    assert t.types == {"id": "string", "amount": "double", "paid": "boolean", "extra": "integer"}
    assert t.rows[0] == {"id": "A1", "amount": 10.5, "paid": True, "extra": None} and t.rows[2]["amount"] is None
    assert table_from_records("empty", []).headers == []


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_the_delimiter_is_detected(delimiter):
    [t] = parse_file("d.csv", csv_bytes(delimiter.join(["a", "b", "c"]) + "\n" + delimiter.join(["1", "2", "3"]) + "\n"))
    assert t.headers == ["a", "b", "c"] and t.rows == [{"a": 1, "b": 2, "c": 3}]


def test_quoted_fields_embedded_commas_and_newlines_survive():
    [t] = parse_file("q.csv", csv_bytes('id,note\n1,"hello, ""world"""\n2,"line one\nline two"\n'))
    assert t.rows[0]["note"] == 'hello, "world"' and t.rows[1]["note"] == "line one\nline two"


def test_a_bom_and_windows_encodings_are_handled():
    assert parse_file("b.csv", b"\xef\xbb\xbfid,name\n1,Zo\xc3\xab\n")[0].headers[0] == "id"  # BOM does not become part of the header
    assert parse_file("w.csv", "id,name\n1,Zoë\n".encode("cp1252"))[0].rows[0]["name"] == "Zoë"


def test_headers_are_made_unique_and_never_empty():
    [t] = parse_file("h.csv", csv_bytes("Name,Name,,name \n1,2,3,4\n"))
    assert t.headers == ["Name", "Name_2", "column_3", "name_3"]  # case-insensitive duplicates too, whitespace trimmed
    assert list(t.rows[0].values()) == [1, 2, 3, 4]


def test_blank_lines_and_short_or_long_rows_are_tolerated():
    [t] = parse_file("r.csv", csv_bytes("a,b\n\n1,2\n3\n4,5,6\n"))
    assert t.rows == [{"a": 1, "b": 2}, {"a": 3, "b": None}, {"a": 4, "b": 5}]


@pytest.mark.parametrize("name", ["x.exe", "x.pdf", "x.csv.exe", "noextension", "x.xls"])
def test_only_table_files_are_accepted(name):
    with pytest.raises(FileParseError, match="Only"):
        parse_file(name, b"a,b\n1,2\n")


def test_empty_and_headerless_files_are_refused():
    with pytest.raises(FileParseError):
        parse_file("e.csv", b"")
    with pytest.raises(FileParseError):
        parse_file("e.csv", b"\n\n  \n")


def test_the_size_row_and_column_limits_are_enforced(monkeypatch):
    with pytest.raises(FileParseError, match="larger"):
        parse_file("big.csv", b"a\n" + b"1\n" * (fp.MAX_FILE_BYTES // 2 + 1))
    monkeypatch.setattr(fp, "MAX_ROWS", 3)
    [t] = parse_file("rows.csv", csv_bytes("a\n" + "\n".join(str(i) for i in range(10)) + "\n"))
    assert len(t.rows) == 3 and t.truncated
    monkeypatch.setattr(fp, "MAX_COLUMNS", 2)
    with pytest.raises(FileParseError, match="columns"):
        parse_file("wide.csv", csv_bytes("a,b,c\n1,2,3\n"))


def test_an_oversized_cell_is_cut_not_trusted():
    [t] = parse_file("cell.csv", csv_bytes("a\n" + "x" * (fp.MAX_CELL_CHARS + 500) + "\n"))
    assert len(t.rows[0]["a"]) == fp.MAX_CELL_CHARS


def test_csv_formula_cells_are_plain_text_never_evaluated():
    [t] = parse_file("f.csv", csv_bytes("a\n=HYPERLINK(\"http://evil\",\"x\")\n"))
    assert t.rows[0]["a"].startswith("=HYPERLINK")  # stored as text; nothing executes it


def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_one_sheet_is_one_table_named_after_the_file():
    [t] = parse_file("ledger.xlsx", _xlsx({"Sheet1": [["account", "amount"], ["A1", 10], ["A2", 20.5]]}))
    assert t.name == "ledger" and t.types == {"account": "string", "amount": "double"}
    assert t.rows == [{"account": "A1", "amount": 10.0}, {"account": "A2", "amount": 20.5}]


def test_several_sheets_are_several_tables():
    tables = parse_file("book.xlsx", _xlsx({"Payroll": [["id"], [1]], "Leavers": [["id", "date"], [2, "2024-01-01"]]}))
    assert [t.name for t in tables] == ["book / Payroll", "book / Leavers"]


def test_excel_dates_become_iso_text_and_blank_rows_are_skipped():
    import datetime

    [t] = parse_file("d.xlsx", _xlsx({"S": [["when", "n"], [datetime.date(2024, 5, 1), 1], [None, None], [datetime.datetime(2024, 6, 2, 8, 30), 2]]}))
    assert t.types["when"] == "date" and t.rows[0]["when"] == "2024-05-01" and t.rows[1]["when"].startswith("2024-06-02")
    assert len(t.rows) == 2


def test_a_sheet_without_a_header_row_is_skipped_and_a_workbook_with_none_is_refused():
    with pytest.raises(FileParseError, match="no sheet"):
        parse_file("empty.xlsx", _xlsx({"S": []}))


def test_a_corrupt_workbook_is_refused_cleanly():
    with pytest.raises(FileParseError):
        parse_file("bad.xlsx", b"PK\x03\x04 this is not really a zip")
    with pytest.raises(FileParseError, match="valid .xlsx"):
        parse_file("bad2.xlsx", b"just some text")


def test_a_zip_bomb_is_refused_before_it_is_expanded(monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", "0" * 5_000_000)  # tiny once compressed, large when expanded
    monkeypatch.setattr(fp, "MAX_UNCOMPRESSED_XLSX_BYTES", 1_000_000)
    with pytest.raises(FileParseError, match="unreasonable size"):
        parse_file("bomb.xlsx", buf.getvalue())


def test_excel_formulas_are_read_as_cached_values_never_evaluated():
    wb = Workbook()
    ws = wb.active
    ws.append(["a", "b"])
    ws.append([1, "=1+1"])
    buf = io.BytesIO()
    wb.save(buf)
    [t] = parse_file("form.xlsx", buf.getvalue())
    assert t.rows[0]["b"] is None  # a never-calculated formula has no cached value; nothing was evaluated

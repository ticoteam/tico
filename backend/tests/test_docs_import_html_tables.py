"""HTML import keeps every word of a table nested inside a table cell (task #129).

Markdown cannot nest tables, so an inner table is flattened into the outer cell's text, its words in source order.
"""

from backend import docs_import


def md(html):
    return docs_import.from_html(html)[0]


def words(text):
    return [w for w in text.replace("|", " ").split() if w != "---"]


def test_text_before_a_nested_table_stays_in_the_outer_cell():
    out = md("<table><tr><td>OUTER_BEFORE <table><tr><td>INNER</td></tr></table> OUTER_AFTER</td></tr></table>")
    assert out == "| OUTER_BEFORE INNER OUTER_AFTER |\n| --- |\n"


def test_earlier_rows_of_the_outer_table_survive_a_nested_table():
    out = md("<table><tr><td>ROW1</td></tr><tr><td>A<table><tr><td>INNER</td></tr></table>B</td></tr></table>")
    assert out == "| ROW1 |\n| --- |\n| A INNER B |\n"


def test_three_levels_keep_every_word_in_source_order():
    out = md(
        "<table><tr><td>L1A<table><tr><th>L2A</th><td>L2B"
        "<table><tr><td>L3A</td><td>L3B</td></tr><tr><td>L3C</td></tr></table>"
        "L2C</td></tr><tr><td>L2D</td></tr></table>L1B</td><td>L1C</td></tr><tr><td>L1D</td></tr></table>"
    )
    assert out == "| L1A L2A L2B L3A L3B L3C L2C L2D L1B | L1C |\n| --- | --- |\n| L1D |  |\n"


def test_unclosed_cells_and_rows_lose_no_words():
    # HTML lets </td> and </tr> be left out; the inner table's rows and the outer rows below are still kept.
    out = md("<table><tr><td>A<td>B<table><tr><td>C<tr><td>D</table>E<tr><td>F</table><p>G</p>")
    assert words(out) == ["A", "B", "C", "D", "E", "F", "G"]


def test_plain_tables_import_as_before():
    # Outputs recorded from the converter before this change.
    cases = {
        "<p>Intro</p><table><tr><th>Name</th><th>Note</th></tr>"
        "<tr><td>Ana</td><td>a | b</td></tr><tr><td>Bo</td></tr></table><p>End</p>": (
            "Intro\n\n| Name | Note |\n| --- | --- |\n| Ana | a \\| b |\n| Bo |  |\n\nEnd\n"
        ),
        "<table><tr><td><b>x</b> <a href='u'>l</a></td><td>y\n z</td></tr></table><table><tr><td>2</td></tr></table>": (
            "| **x** [l](u) | y z |\n| --- | --- |\n\n| 2 |\n| --- |\n"
        ),
        "<h1>H</h1><table><thead><tr><th>a</th></tr></thead><tbody><tr><td>1</td></tr><tr></tr></tbody></table>"
        "<ul><li>i</li></ul>": "# H\n\n| a |\n| --- |\n| 1 |\n\n- i\n",
    }
    for html, expected in cases.items():
        assert md(html) == expected


def test_deeply_nested_tables_keep_every_word():
    depth = 10_000
    out = md("<table><tr><td>w" * depth + "</td></tr></table>" * depth)
    assert out == "| " + " ".join(["w"] * depth) + " |\n| --- |\n"


def test_a_file_ending_inside_a_table_keeps_its_words():
    # A file may end inside a table, even inside a <script> in a cell: the open table is still written out.
    assert md("<table><tr><td>A</td><td>B") == "| A | B |\n| --- | --- |\n"
    assert md("<table><tr><td>A<table><tr><td>B") == "| A B |\n| --- |\n"
    assert md("<table><tr><td>A<script>x") == "| A |\n| --- |\n"

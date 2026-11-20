"""HTML to text and to Markdown (backend/html_convert.py), the one path document import, the file search index and
the Gmail reader share."""

from backend import docs_import, html_convert
from backend.mail_lib import gmail


def test_markdown_closes_tags_html_leaves_open_and_keeps_only_safe_parts():
    md, title = docs_import.from_html(
        "<title> My  page </title><ul><li>one<li>two<ol><li>a<li>b</ol></ul>"
        "<p>See <a href='javascript:alert(1)'>this</a> and <a href='https://acme.example' title='t'>that</a> "
        "<img src='data:image/png;base64,AAAA' alt='a chart'></p><pre>  x = 1\n  y = 2</pre><p>end"
        "<video poster='https://t.example/p.png'>clip</video>"
    )
    assert title == "My page"
    assert md == (
        "- one\n- two\n  1. a\n  2. b\n\n"
        "See this and [that](https://acme.example) a chart\n\n"
        "```\n  x = 1\n  y = 2\n```\n\nendclip\n"
    )


def test_a_page_nested_deeper_than_markdownify_walks_keeps_every_word():
    depth = 3000
    md, _ = html_convert.to_markdown("<div><b>" * depth + "deep words" + "</b></div>" * depth + "<p>after</p>")
    assert md.split() == ["deep", "words", "after"]


def test_text_drops_hidden_parts_and_markup_and_keeps_a_line_per_block():
    text, title = html_convert.to_text(
        "<head><title>T</title><style>s{}</style></head><body><!-- note --><p title='a > b'>Hel<b>lo</b></p>"
        "<noscript>enable js</noscript><svg><text>chart</text></svg>"
        "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table><pre>l1\n  l2</pre>tail<br>after"
        "<script>x</script>end"
    )
    # Every tag edge splits words, as the search index and the mail reader always have.
    assert (text, title) == ("Hel lo\na b\nc\nl1\nl2\ntail\nafter end", "T")


def test_text_keeps_what_the_parser_leaves_in_an_implied_head():
    # libxml2 does not know <header>, so with no <body> tag it stays in the head a browser would have closed.
    assert html_convert.to_text("<title>t</title><header>Visible</header>")[0] == "Visible"


def test_a_comment_over_the_parsers_node_limit_stays_hidden():
    md, _ = docs_import.from_html("<p>before</p><!--" + "c" * 11_000_000 + "--><p>after</p>")
    assert md == "before\n\nafter\n"


def test_mail_text_no_longer_shows_attribute_values_or_comments():
    # The regex stripper this replaced cut a tag at the first ">", so an attribute holding one leaked into the body.
    html = '<p data-x="{{a > b}}">Hi</p><!-- <p>draft</p> --><p>Bye</p>'
    assert gmail.strip_html(html) == "Hi\nBye"


def test_mail_keeps_the_sources_line_breaks_so_an_unsubscribe_line_is_found():
    payload = {
        "mimeType": "text/html",
        "body": {"data": "PHA-VGhhbmtzClVuc3Vic2NyaWJlOiBtYWlsdG86c3RvcEB4LmV4YW1wbGU8L3A-"},
    }
    text, html = gmail.body_text(payload)  # <p>Thanks\nUnsubscribe: mailto:stop@x.example</p>
    assert text == "Thanks\nUnsubscribe: mailto:stop@x.example"
    assert gmail.find_unsubscribe("", text, html) == (True, "mailto:stop@x.example")


def test_text_in_a_table_outside_its_cells_is_kept_and_cannot_fake_a_cell():
    md, _ = docs_import.from_html(
        "<table>stray<tr>pre<td>x</td>mid<td>a&#2;b&#3;c</td></tr>tail</table><table> </table><table>only text</table>"
    )
    assert md == "stray pre mid tail\n\n| x | a\x02b\x03c |\n| --- | --- |\n\nonly text\n"


def test_tag_names_and_list_starts_markdownify_cannot_take_are_read_as_text():
    md, _ = docs_import.from_html(
        "<p>a<soup>b</soup><fn-cache>c</fn-cache></p><ol start='²'><li>x</ol><ol start=3><li>y<li>z"
    )
    assert md == "abc\n\n1. x\n\n3. y\n4. z\n"

"""HTML to text and to Markdown (backend/html_convert.py), the one path document import, the file search index and
the Gmail reader share."""

from backend import docs_import, html_convert
from backend.mail_lib import gmail


def test_markdown_closes_tags_html_leaves_open_and_keeps_only_safe_parts():
    md, title = docs_import.from_html(
        "<title> My  page </title><ul><li>one<li>two<ol><li>a<li>b</ol></ul>"
        "<p>See <a href='javascript:alert(1)'>this</a> and <a href='https://acme.example' title='t'>that</a> "
        "<img src='data:image/png;base64,AAAA' alt='a chart'></p><pre>  x = 1\n  y = 2</pre><p>end"
    )
    assert title == "My page"
    assert md == (
        "- one\n- two\n  1. a\n  2. b\n\n"
        "See this and [that](https://acme.example) a chart\n\n"
        "```\n  x = 1\n  y = 2\n```\n\nend\n"
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
    )
    # Every tag edge splits words, as the search index and the mail reader always have.
    assert (text, title) == ("Hel lo\na b\nc\nl1\nl2\ntail\nafter", "T")


def test_mail_text_no_longer_shows_attribute_values_or_comments():
    # The regex stripper this replaced cut a tag at the first ">", so an attribute holding one leaked into the body.
    html = '<p data-x="{{a > b}}">Hi</p><!-- <p>draft</p> --><p>Bye</p>'
    assert gmail.strip_html(html) == "Hi\nBye"


def test_text_outside_a_tables_cells_is_kept():
    md, _ = docs_import.from_html("<table>stray<tr><td>x</td></tr></table><table> </table><table>only text</table>")
    assert md == "stray\n\n| x |\n| --- |\n\nonly text\n"

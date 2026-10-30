from re_gpt.terminal_rich_display import terminal_display

def test_unicode_latex_fallback():
    assert "59 ≡ 59" in terminal_display(r"\[59 \equiv 59\]")
    assert "λ" in terminal_display(r"\(\lambda\)")

def test_source_not_changed_and_fenced_literal_preserved():
    source = r"\[x \cdot y\]"
    assert source == r"\[x \cdot y\]"
    assert "x · y" in terminal_display(source)
    fence = chr(96) * 3
    literal = fence + "\n" + source + "\n" + fence
    assert terminal_display(literal) == literal

def test_md_links_html_entities_and_citation_metadata():
    result = terminal_display('<Link url="https://example.com">Reference</Link> &amp; <Cite refs=["x"]/>')
    assert "[Reference](https://example.com)" in result
    assert "&" in result
    assert "<Cite" not in result

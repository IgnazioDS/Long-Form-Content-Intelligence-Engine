from apps.api.app.services.rag import build_snippet


def test_build_snippet_short_text() -> None:
    text = "Short text"
    snippet = build_snippet(text, max_len=50)
    assert snippet.snippet_text == text
    assert snippet.snippet_start == 0
    assert snippet.snippet_end == len(text)
    assert text[snippet.snippet_start : snippet.snippet_end] == snippet.snippet_text


def test_build_snippet_trims_whitespace() -> None:
    text = "  Hello world  "
    snippet = build_snippet(text, max_len=50)
    assert snippet.snippet_text == "Hello world"
    assert snippet.snippet_start == 2
    assert snippet.snippet_end == len(text) - 2
    assert text[snippet.snippet_start : snippet.snippet_end] == snippet.snippet_text


def test_build_snippet_truncates_long_text() -> None:
    text = "abcdefghijklmnopqrstuvwxyz"
    snippet = build_snippet(text, max_len=10)
    assert snippet.snippet_text == text[:10]
    assert snippet.snippet_start == 0
    assert snippet.snippet_end == 10
    assert text[snippet.snippet_start : snippet.snippet_end] == snippet.snippet_text


def test_build_snippet_empty_text() -> None:
    text = "   "
    snippet = build_snippet(text, max_len=10)
    assert snippet.snippet_text == ""
    assert snippet.snippet_start is None
    assert snippet.snippet_end is None

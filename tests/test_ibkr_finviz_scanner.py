from ibkr.scanner.run_finviz_scan import (
    extract_comment_tickers,
    extract_fallback_tickers,
    extract_table_rows,
    is_valid_finviz_symbol,
)


def test_export_is_not_a_valid_finviz_symbol():
    assert not is_valid_finviz_symbol("EXPORT")
    assert not is_valid_finviz_symbol(" export ")
    assert is_valid_finviz_symbol("AAPL")


def test_table_extractor_ignores_export_control_text():
    html = """
    <table>
      <tr><th>No.</th><th>Ticker</th><th>Company</th><th>Price</th><th>Change</th><th>Volume</th></tr>
      <tr><td>1</td><td>AAPL</td><td>Apple Inc.</td><td>190.10</td><td>1.25%</td><td>1000000</td></tr>
      <tr><td>2</td><td>EXPORT</td><td></td><td></td><td></td><td></td></tr>
    </table>
    """

    assert [row["symbol"] for row in extract_table_rows(html)] == ["AAPL"]


def test_comment_and_fallback_extractors_ignore_export_control_text():
    comment_html = """
    <!-- TS
    AAPL|190.10|1000000|1.25%
    EXPORT|0|0|0%
    TE -->
    """
    fallback_html = """
    <a class="tab-link">AAPL</a>
    <a class="tab-link">EXPORT</a>
    """

    assert [row["symbol"] for row in extract_comment_tickers(comment_html)] == ["AAPL"]
    assert [row["symbol"] for row in extract_fallback_tickers(fallback_html)] == ["AAPL"]

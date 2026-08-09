from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cursor_sdk import CursorAgentError

from momo.adapters import cursor_agent
from momo.config import get_settings
from momo.services import ai_news_summary


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_extract_text_from_html_strips_tags_and_scripts():
    html = """
    <html><head><style>body{color:red}</style></head>
    <body><h1>Hello</h1><script>alert(1)</script><p>World &amp; Co</p></body>
    </html>
    """
    text = ai_news_summary._extract_text_from_html(html)
    assert "Hello" in text
    assert "World" in text
    assert "alert" not in text
    assert "color:red" not in text


def test_run_prompt_returns_agent_result(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_test_key")
    get_settings.cache_clear()

    result = SimpleNamespace(
        status="finished",
        result="  Verdict: Noise  ",
        id="run-1",
        agent_id="agent-1",
    )

    with patch("momo.adapters.cursor_agent.Agent.prompt", return_value=result) as prompt:
        text = cursor_agent.run_prompt(system="sys", user="usr")

    assert text == "Verdict: Noise"
    options = prompt.call_args.args[1]
    assert options.api_key == "cursor_test_key"
    assert options.model == get_settings().cursor_model
    assert options.tools == []
    assert "sys" in prompt.call_args.args[0]
    assert "usr" in prompt.call_args.args[0]


def test_run_prompt_requires_api_key(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(cursor_agent.CursorAgentAdapterError, match="CURSOR_API_KEY"):
        cursor_agent.run_prompt(system="sys", user="usr")


def test_run_prompt_maps_startup_and_run_errors(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_test_key")
    get_settings.cache_clear()

    with patch(
        "momo.adapters.cursor_agent.Agent.prompt",
        side_effect=CursorAgentError("bad auth", code="unauthenticated"),
    ):
        with pytest.raises(cursor_agent.CursorAgentAdapterError, match="bad auth"):
            cursor_agent.run_prompt(system="sys", user="usr")

    failed = SimpleNamespace(
        status="error",
        result="",
        id="run-err",
        agent_id="agent-err",
    )
    with patch("momo.adapters.cursor_agent.Agent.prompt", return_value=failed):
        with pytest.raises(cursor_agent.CursorAgentAdapterError, match="run failed"):
            cursor_agent.run_prompt(system="sys", user="usr")


def test_summarize_stock_saves_summary(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_test_key")
    get_settings.cache_clear()

    news_row = SimpleNamespace(
        title="Company raises dividend",
        url="https://example.com/a",
        source="Reuters",
        publish_time="2026-08-01 10:00:00",
    )
    saved = {}

    class FakeSession:
        def close(self):
            return None

    def fake_get_session():
        return FakeSession()

    def fake_list_news(session, symbol, limit=8):
        return [news_row]

    def fake_get_ai_summary(session, symbol):
        return None

    def fake_upsert(session, item):
        saved.update(item)
        return SimpleNamespace(
            symbol=item["symbol"],
            stock_code=item["stock_code"],
            stock_name=item["stock_name"],
            summary=item["summary"],
            source_urls=item["source_urls"],
            model=item["model"],
            updated_at=None,
        )

    with (
        patch("momo.services.ai_news_summary.get_session", fake_get_session),
        patch("momo.services.ai_news_summary.list_news_for_symbol", fake_list_news),
        patch("momo.services.ai_news_summary.get_ai_summary", fake_get_ai_summary),
        patch("momo.services.ai_news_summary.upsert_ai_summary", fake_upsert),
        patch(
            "momo.services.ai_news_summary.resolve_stock",
            return_value={
                "code": "QCOM",
                "symbol": "US.QCOM",
                "name": "Qualcomm",
                "market": "US",
            },
        ),
        patch(
            "momo.services.ai_news_summary._fetch_article_text",
            return_value="Dividend increased by 10%.",
        ),
        patch(
            "momo.services.ai_news_summary.run_prompt",
            return_value="1. EXECUTIVE SUMMARY\nDividend hike.",
        ) as prompt,
    ):
        result = ai_news_summary.summarize_stock("US.QCOM", force=True)

    assert result["symbol"] == "US.QCOM"
    assert "Dividend hike" in result["summary"]
    assert saved["source_urls"] == "https://example.com/a"
    assert saved["model"] == get_settings().cursor_model
    assert "Qualcomm" in prompt.call_args.kwargs["user"]
    assert "US.QCOM" in prompt.call_args.kwargs["user"]


def test_summarize_stock_returns_cached_unless_forced(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_test_key")
    get_settings.cache_clear()

    cached = SimpleNamespace(
        symbol="US.QCOM",
        stock_code="QCOM",
        stock_name="Qualcomm",
        summary="Cached summary text that is short.",
        source_urls="https://example.com/a",
        model="composer-2.5",
        updated_at=None,
    )

    class FakeSession:
        def close(self):
            return None

    with (
        patch("momo.services.ai_news_summary.get_session", return_value=FakeSession()),
        patch(
            "momo.services.ai_news_summary.resolve_stock",
            return_value={
                "code": "QCOM",
                "symbol": "US.QCOM",
                "name": "Qualcomm",
                "market": "US",
            },
        ),
        patch("momo.services.ai_news_summary.get_ai_summary", return_value=cached),
        patch("momo.services.ai_news_summary.run_prompt") as prompt,
    ):
        result = ai_news_summary.summarize_stock("US.QCOM", force=False)

    assert result["summary"] == cached.summary
    prompt.assert_not_called()


def test_summary_preview_truncates():
    long = "x" * 400
    row = SimpleNamespace(
        symbol="US.QCOM",
        stock_code="QCOM",
        stock_name="Qualcomm",
        summary=long,
        source_urls="",
        model="m",
        updated_at=None,
    )
    data = ai_news_summary._summary_dict(row)
    assert data["truncated"] is True
    assert data["preview"].endswith("…")
    assert len(data["preview"]) == ai_news_summary.SUMMARY_PREVIEW_CHARS
    assert data["section_headers"] == []


def test_parse_section_headers_extracts_badges():
    summary = """
### 1. EXECUTIVE SUMMARY | 🟡 NEUTRAL / UNVERIFIED NOISE
Some body.

### 2. INTRINSIC VALUE & CASH FLOW IMPACT | 🟡 NEUTRAL
Cash flow note.

3. COMPETITIVE MOAT CHECK | 🟢 MOAT WIDENING
Still ok without hashes.

### 4. CAPITAL ALLOCATION & MANAGEMENT EVALUATION | 🔴 POOR ALLOCATION
Bad.

### 5. KEY RISKS & MARGIN OF SAFETY RED FLAGS | 🟡 MODERATE RISK
Risks.

### 6. THE VERDICT | 🟢 BUY
Buy.
"""
    headers = ai_news_summary._parse_section_headers(summary)
    assert [h["num"] for h in headers] == [1, 2, 3, 4, 5, 6]
    assert headers[0]["line"] == "### 1. EXECUTIVE SUMMARY | 🟡 NEUTRAL / UNVERIFIED NOISE"
    assert headers[0]["tone"] == "yellow"
    assert headers[2]["tone"] == "green"
    assert headers[3]["tone"] == "red"
    assert headers[5]["status"] == "🟢 BUY"


def test_summary_dict_includes_section_headers():
    row = SimpleNamespace(
        symbol="US.QCOM",
        stock_code="QCOM",
        stock_name="Qualcomm",
        summary="### 1. EXECUTIVE SUMMARY | 🟡 NEUTRAL\nBody\n### 2. INTRINSIC VALUE & CASH FLOW IMPACT | 🟢 POSITIVE\nMore",
        source_urls="",
        model="m",
        updated_at=None,
    )
    data = ai_news_summary._summary_dict(row)
    assert len(data["section_headers"]) == 2
    assert data["section_headers"][1]["tone"] == "green"


_GLUED_PASTE = (
    "1. EXECUTIVE SUMMARY | 🟢 POSITIVECore Fact: Alpha fact here.  "
    "Reality vs. Narrative: Narrative note."
    "2. INTRINSIC VALUE & CASH FLOW IMPACT | 🟢 POSITIVEOwner Earnings Impact: "
    "Positive cash flow.  Duration Analysis: Structural moat."
    "3. COMPETITIVE MOAT CHECK | 🟢 MOAT WIDENINGMoat Vector Analysis:"
    "Switching Costs: Extremely High. Barriers to Entry: High. "
    "Pricing Power: Strong. Network Effects: High.  "
    "Moat Trajectory: Strengthening."
    "4. CAPITAL ALLOCATION & MANAGEMENT EVALUATION | 🟡 NEUTRAL"
    "Capital Discipline: Prudent.  Alignment: Owner-aligned."
    "5. KEY RISKS & MARGIN OF SAFETY RED FLAGS | 🟡 RE-EVALUATE"
    "Accounting & Balance Sheet Risk:Over-reliance on Valuation Gains: Watch NAV. "
    "Interest Rate Exposure: Refi risk. Downside Protection: Need 25% MOS."
    "6. THE VERDICT | 🟢 CATALYST FOR BUYINGSignal: Catalyst for Buying. "
    "Verification Checklist:Income Statement / NPI: Check NPI. "
    "Cash Flow Statement: Check FCF. Balance Sheet / Notes: Check WALE."
)


def test_format_pasted_summary_splits_glued_sections_and_bullets():
    formatted = ai_news_summary.format_pasted_summary(_GLUED_PASTE)
    assert formatted.count("### ") == 6
    assert "### 1. EXECUTIVE SUMMARY | 🟢 POSITIVE" in formatted
    assert "**Core Fact:** Alpha fact here." in formatted
    assert "- **Switching Costs:** Extremely High." in formatted
    assert "- **Income Statement / NPI:** Check NPI." in formatted
    assert "**Moat Trajectory:** Strengthening." in formatted
    assert ai_news_summary.format_pasted_summary(formatted) == formatted


def test_format_pasted_summary_empty():
    assert ai_news_summary.format_pasted_summary("   ") == ""


def test_format_pasted_summary_keeps_analyst_ratings_preamble():
    raw = (
        "Analyst Ratings Breakdown: (Based on 14 covering analysts)  "
        "Strong Buy: 0.0%  Buy: 21.4% (3 analysts)  Hold: 78.6% (11 analysts)  "
        "Underperform: 0.0%  Sell: 0.0%  "
        "1. EXECUTIVE SUMMARY | 🟢 POSITIVECore Fact: Alpha."
    )
    formatted = ai_news_summary.format_pasted_summary(raw)
    assert formatted.startswith("**Analyst Ratings Breakdown:** (Based on 14 covering analysts)")
    assert "- **Strong Buy:** 0.0%" in formatted
    assert "- **Buy:** 21.4% (3 analysts)" in formatted
    assert "- **Hold:** 78.6% (11 analysts)" in formatted
    assert "### 1. EXECUTIVE SUMMARY | 🟢 POSITIVE" in formatted
    assert "**Core Fact:** Alpha." in formatted
    assert ai_news_summary.format_pasted_summary(formatted) == formatted


def test_parse_analyst_ratings_preview_from_formatted_summary():
    formatted = ai_news_summary.format_pasted_summary(
        "Analyst Ratings Breakdown: (Based on 14 covering analysts)  "
        "Strong Buy: 0.0%  Buy: 21.4% (3 analysts)  Hold: 78.6% (11 analysts)  "
        "Underperform: 0.0%  Sell: 0.0%  "
        "1. EXECUTIVE SUMMARY | 🟢 STABLE COMPOUNDERCore Fact: Alpha."
    )
    preview = ai_news_summary._parse_analyst_ratings_preview(formatted)
    assert preview == (
        "*Strong Buy*: 0.0%, *Buy*: 21.4% (3 analysts), "
        "*Hold*: 78.6% (11 analysts), *Underperform*: 0.0%, *Sell*: 0.0%"
    )


def test_summary_dict_includes_analyst_ratings_preview():
    formatted = (
        "**Analyst Ratings Breakdown:** (Based on 3 covering analysts)\n"
        "- **Strong Buy:** 0.0%\n"
        "- **Buy:** 100.0% (3 analysts)\n"
        "- **Hold:** 0.0%\n"
        "- **Underperform:** 0.0%\n"
        "- **Sell:** 0.0%\n\n"
        "### 1. EXECUTIVE SUMMARY | 🟢 STABLE COMPOUNDER\n"
        "Body"
    )
    row = SimpleNamespace(
        symbol="MY.5176",
        stock_code="5176",
        stock_name="Sunway REIT",
        summary=formatted,
        source_urls="",
        model="m",
        updated_at=None,
    )
    data = ai_news_summary._summary_dict(row)
    assert data["analyst_ratings_preview"].startswith("*Strong Buy*: 0.0%")
    assert "*Buy*: 100.0% (3 analysts)" in data["analyst_ratings_preview"]
    assert data["section_headers"][0]["line"].startswith("### 1. EXECUTIVE SUMMARY")


def test_parse_analyst_ratings_preview_absent():
    assert (
        ai_news_summary._parse_analyst_ratings_preview(
            "### 1. EXECUTIVE SUMMARY | 🟢 POSITIVE\nBody"
        )
        is None
    )

"""Issue #770 regression tests (offline, deterministic).

1. Per-payment equality tolerance hides small monthly raises.
2. TTM window ends at the latest payment, not the evaluation date.
3. Per-ticker price date is not surfaced.
"""

from datetime import date, datetime, timedelta, timezone

from build_entry_signals import build_entry_row, render_markdown
from dividend_basis import analyze_dividends, step1_decision


def _monthly(amounts, start=date(2024, 6, 3)):
    out = []
    for i, amt in enumerate(amounts):
        out.append(
            {
                "date": (start + timedelta(days=30 * i)).isoformat(),
                "dividend": amt,
                "label": "cash",
            }
        )
    return out


# --- Finding 1: a ~0.9% monthly raise is detected, not frozen ---
def test_monthly_raise_detected_not_frozen():
    # Issue repro at 13079ab4: 0.2695 x6, 0.2715 x6, 0.2740 froze with no
    # increase detected (YoY +$0.0045/mo, +$0.054/yr).
    hist = _monthly([0.2695] * 6 + [0.2715] * 6 + [0.2740])
    last = date(2024, 6, 3) + timedelta(days=30 * 12)
    b = analyze_dividends(
        hist, price=60.0, floor_pct=4.0, as_of_date=(last + timedelta(days=5)).isoformat()
    )
    assert b.cadence == "monthly"
    assert b.freeze_flag is False
    assert b.last_increase_date == last.isoformat()
    assert step1_decision(b, 4.0)[0] == "STEP1-PASS"


def test_subcent_wobble_still_equal_not_an_increase():
    # Noise within the tolerance must stay "equal": alternating $0.001
    # wobbles are not increases, so a flat old series still freezes.
    hist = _monthly([0.270, 0.271] * 6 + [0.270])
    last = date(2024, 6, 3) + timedelta(days=30 * 12)
    b = analyze_dividends(
        hist, price=60.0, floor_pct=4.0, as_of_date=(last + timedelta(days=10)).isoformat()
    )
    assert b.last_increase_date is None
    assert b.freeze_flag is True


# --- Finding 2: TTM sums use as_of_date as the window end ---
def test_ttm_window_ends_at_evaluation_date():
    # Five quarterly pays; the oldest is inside the window anchored at the
    # latest pay (2026-11-10) but outside the one ending 2026-11-15.
    pays = [
        ("2025-11-12", 0.70),
        ("2026-02-12", 0.70),
        ("2026-05-12", 0.70),
        ("2026-08-12", 0.70),
        ("2026-11-10", 0.70),
    ]
    hist = [{"date": d, "dividend": a, "label": "cash"} for d, a in pays]
    b = analyze_dividends(hist, price=40.0, floor_pct=3.0, as_of_date="2026-11-15")
    assert b.regular_annual_dividend == 2.8
    assert b.ttm_dividend_incl_special == 2.8
    # Without an evaluation date the window still ends at the latest pay.
    b_old = analyze_dividends(hist, price=40.0, floor_pct=3.0)
    assert b_old.regular_annual_dividend == 3.5


# --- Finding 3: each row records its price date ---
def _epoch(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def _row(price_date: date | None, as_of: str):
    quote = {"price": 100.0}
    if price_date is not None:
        quote["timestamp"] = _epoch(price_date)
    return build_entry_row(
        ticker="CFR",
        alpha_pp=0.5,
        quote=quote,
        profile=None,
        key_metrics=[],
        as_of=as_of,
    )


def test_price_date_recorded_and_mismatch_flagged():
    row = _row(date(2026, 11, 11), "2026-11-13")
    assert row["price_date"] == "2026-11-11"
    assert row["price_date_mismatch"] is True
    assert "price_date_mismatch" in row["notes"]
    md = render_markdown([row], as_of="2026-11-13", alpha_pp=0.5)
    assert "price as of `2026-11-11`" in md
    assert "run as_of `2026-11-13`" in md


def test_price_date_matching_run_date_not_flagged():
    row = _row(date(2026, 11, 13), "2026-11-13")
    assert row["price_date"] == "2026-11-13"
    assert row["price_date_mismatch"] is False
    assert "price_date_mismatch" not in row["notes"]


def test_price_date_missing_when_quote_has_no_timestamp():
    row = _row(None, "2026-11-13")
    assert row["price_date"] is None
    assert row["price_date_mismatch"] is False

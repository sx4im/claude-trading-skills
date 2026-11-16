"""Regression tests for issue #770 (kanchi-dividend-sop).

1. A fixed 0.005 absolute per-payment tolerance hides small monthly raises:
   0.2695 x6, 0.2715 x6, 0.2740 was reported freeze_flag=True with no
   increase detected (YoY +$0.0045/mo, +$0.054/yr). The tolerance is now
   max(absolute floor, relative tol * prior amount).
2. The TTM window ended at the latest payment instead of the evaluation
   date (VZ: five quarterly pays summed $3.5025 instead of $2.8125).
3. The per-ticker price carried no date: rows now record price_date and
   flag a mismatch against the evaluation date.
"""

from datetime import date, timedelta

from build_entry_signals import build_entry_row
from dividend_basis import analyze_dividends


def _monthly_history(amounts, start=date(2025, 1, 15)):
    history = []
    day = start
    for amt in amounts:
        history.append({"date": day.isoformat(), "dividend": amt})
        # Next month, same day-of-month (all months used have a 15th).
        year = day.year + (day.month // 12)
        month = day.month % 12 + 1
        day = date(year, month, 15)
    return history


def _quarterly_history(amounts, start=date(2025, 2, 10)):
    history = []
    day = start
    for amt in amounts:
        history.append({"date": day.isoformat(), "dividend": amt})
        month = day.month + 3
        year = day.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = date(year, month, 10)
    return history


def test_monthly_small_raise_detected_not_frozen():
    history = _monthly_history([0.2695] * 6 + [0.2715] * 6 + [0.2740])
    basis = analyze_dividends(history, 45.0, as_of_date="2026-02-01")
    assert basis.status == "OK"
    assert basis.last_increase_date == "2026-01-15"
    assert basis.freeze_flag is False


def test_sub_cent_noise_stays_equal_no_phantom_increase():
    history = _monthly_history([0.2700] * 7 + [0.2704] * 6)
    basis = analyze_dividends(history, 45.0, as_of_date="2026-02-01")
    assert basis.status == "OK"
    assert basis.last_increase_date is None
    assert basis.freeze_flag is True


def test_ttm_window_ends_at_evaluation_date():
    history = _quarterly_history([0.70] * 5)
    without_eval = analyze_dividends(history, 45.0)
    assert without_eval.regular_annual_dividend == 3.50
    with_eval = analyze_dividends(history, 45.0, as_of_date="2026-02-20")
    # The oldest pay is 375 days before the evaluation date, outside the
    # 366-day window: only the latest four pays sum.
    assert with_eval.regular_annual_dividend == 2.80
    assert with_eval.ttm_dividend_incl_special == 2.80


def test_price_date_recorded_and_matches_evaluation_date():
    row = build_entry_row(
        "VZ",
        0.5,
        {"price": 100.0, "timestamp": 1768435200},  # 2026-01-15 UTC
        None,
        [],
        as_of="2026-01-15",
    )
    assert row["price_date"] == "2026-01-15"
    assert row["price_date_mismatch"] is False
    assert "price_date_mismatch" not in row["notes"]
    assert row["provenance"]["price_date"] == "2026-01-15"
    assert row["provenance"]["price_date_source"] == "fmp_quote_timestamp"


def test_price_date_mismatch_flagged():
    row = build_entry_row(
        "VZ",
        0.5,
        {"price": 100.0, "timestamp": 1768435200},  # 2026-01-15 UTC
        None,
        [],
        as_of="2026-01-20",
    )
    assert row["price_date"] == "2026-01-15"
    assert row["price_date_mismatch"] is True
    assert "price_date_mismatch" in row["notes"]


def test_price_date_missing_without_quote_timestamp():
    row = build_entry_row("VZ", 0.5, {"price": 100.0}, None, [], as_of="2026-01-15")
    assert row["price_date"] is None
    assert row["price_date_mismatch"] is False
    assert "price_date_mismatch" not in row["notes"]

"""Issue #771: payout-safety routing must use FMP industry, not sector alone.

FMP reports insurers under "Financial Services" and REITs under "Real
Estate", so the sector-specific insurer and REIT paths were never reached
on real data. Routing precedence: kind_override > industry > sector.
A broad sector ("Financial Services", "Real Estate") with an industry that
does not resolve it is ambiguous and must HOLD-REVIEW instead of silently
applying another sector's rules. A broad sector with NO industry keeps the
established sector fallback (existing goldens pin it).
"""

from build_entry_signals import build_entry_row
from payout_safety import assess_payout_safety


def _ori():
    return dict(
        sector="Financial Services",
        industry="Insurance - Property & Casualty",
        annual_dividend=1.26,
        gaap_eps=3.0,
    )


def test_ori_insurer_routes_by_industry_not_sector():
    a = assess_payout_safety(**_ori())
    assert a.sector_kind == "insurer"
    assert a.sector_kind_source == "industry"
    assert not [b for b in a.blockers if b.startswith("bank_")]
    assert "insurer_combined_ratio_unavailable" in a.blockers
    assert a.safety_verdict == "CAUTION"


def test_bank_routes_by_industry():
    a = assess_payout_safety(
        sector="Financial Services",
        industry="Banks - Regional",
        annual_dividend=1.88,
        gaap_eps=6.17,
        adjusted_eps=6.17,
        adjusted_eps_source="FMP",
        bank_metrics={"cet1": 0.11, "cre_concentration": "low"},
    )
    assert a.sector_kind == "bank"
    assert a.sector_kind_source == "industry"
    assert a.safety_verdict == "PASS"


def test_reit_routes_by_industry_with_affo_anchor():
    a = assess_payout_safety(
        sector="Real Estate",
        industry="REIT - Retail",
        annual_dividend=2.0,
        gaap_eps=2.0,
        affo_per_share=4.0,
    )
    assert a.sector_kind == "reit"
    assert a.sector_kind_source == "industry"
    assert a.safety_verdict == "PASS"


def test_reit_affo_payout_above_ceiling_fails():
    a = assess_payout_safety(
        sector="Real Estate",
        industry="REIT - Residential",
        annual_dividend=3.6,
        gaap_eps=2.0,
        affo_per_share=4.0,  # 90% AFFO payout > 85% ceiling
    )
    assert a.sector_kind == "reit"
    assert a.safety_verdict == "FAIL"
    assert "reit_affo_payout_high" in a.reasons


def test_reit_affo_unavailable_cautions_not_passes():
    a = assess_payout_safety(
        sector="Real Estate",
        industry="REIT - Office",
        annual_dividend=2.0,
        gaap_eps=2.0,
    )
    assert a.sector_kind == "reit"
    assert "reit_affo_unavailable" in a.blockers
    assert a.safety_verdict == "CAUTION"


def test_broad_sector_unresolved_industry_holds_review():
    a = assess_payout_safety(
        sector="Financial Services",
        industry="Semiconductors",
        annual_dividend=1.0,
        gaap_eps=4.0,
    )
    assert a.sector_kind == "unknown"
    assert a.sector_kind_source == "unknown"
    assert "sector_kind_ambiguous" in a.blockers
    assert a.safety_verdict == "HOLD-REVIEW"


def test_broad_sector_no_industry_keeps_sector_fallback():
    # Existing goldens pin sector-only "Financial Services" -> bank.
    a = assess_payout_safety(
        sector="financial services",
        annual_dividend=1.88,
        gaap_eps=6.17,
        adjusted_eps=6.17,
        adjusted_eps_source="FMP",
        bank_metrics={"cet1": 0.11, "cre_concentration": "low"},
    )
    assert a.sector_kind == "bank"
    assert a.sector_kind_source == "sector"
    assert a.safety_verdict == "PASS"


def test_kind_override_wins_over_industry_and_sector():
    a = assess_payout_safety(
        sector="Technology",
        industry="Semiconductors",
        kind_override="reit",
        annual_dividend=2.0,
        gaap_eps=2.0,
        affo_per_share=4.0,
    )
    assert a.sector_kind == "reit"
    assert a.sector_kind_source == "override"


def test_invalid_kind_override_holds_review():
    a = assess_payout_safety(
        sector="Technology",
        kind_override="hotel",
        annual_dividend=1.0,
        gaap_eps=4.0,
    )
    assert "sector_kind_override_invalid" in a.blockers
    assert a.safety_verdict == "HOLD-REVIEW"


def test_utility_routes_by_industry():
    a = assess_payout_safety(
        sector="Utilities",
        industry="Utilities - Electric",
        annual_dividend=1.68,
        gaap_eps=2.74,
        adjusted_eps=2.74,
        adjusted_eps_source="FMP",
        utility_metrics={"ffo_to_debt": 0.16, "rate_case_status": "constructive"},
    )
    assert a.sector_kind == "utility"
    assert a.sector_kind_source == "industry"
    assert a.safety_verdict == "PASS"


def test_consumer_unchanged_by_industry():
    a = assess_payout_safety(
        sector="consumer staples",
        industry="Packaged Foods",
        annual_dividend=1.0,
        gaap_eps=3.0,
        adjusted_eps=3.0,
        adjusted_eps_source="FMP",
        fcf_per_share=2.5,
    )
    assert a.sector_kind == "consumer"
    assert a.safety_verdict == "PASS"


def test_build_entry_row_records_industry_routing():
    row = build_entry_row(
        ticker="ORI",
        alpha_pp=0.5,
        quote={"price": 32.0},
        profile={"sector": "Financial Services", "industry": "Insurance - Property & Casualty"},
        key_metrics=[{"dividendYield": 0.04}],
        dividend_history=None,
        floor_pct=3.0,
        financials={
            "sector": "Financial Services",
            "gaap_eps": 3.0,
            "adjusted_eps": None,
            "adjusted_eps_source": "UNAVAILABLE",
            "fcf_per_share": None,
        },
    )
    assert row["payout_safety"]["sector_kind"] == "insurer"
    assert row["payout_safety"]["sector_kind_source"] == "industry"
    assert not [b for b in row["pre_order_blockers"] if b.startswith("bank_")]


def test_build_entry_row_reit_from_profile_industry_only():
    # financials carries no industry; the FMP profile does.
    row = build_entry_row(
        ticker="O",
        alpha_pp=0.5,
        quote={"price": 52.0},
        profile={"sector": "Real Estate", "industry": "REIT - Retail"},
        key_metrics=[{"dividendYield": 0.06}],
        dividend_history=None,
        floor_pct=3.0,
        financials={
            "sector": "Real Estate",
            "gaap_eps": 2.0,
            "adjusted_eps": None,
            "adjusted_eps_source": "UNAVAILABLE",
            "fcf_per_share": None,
        },
    )
    assert row["payout_safety"]["sector_kind"] == "reit"
    assert row["payout_safety"]["sector_kind_source"] == "industry"

"""WS-2 sector-aware payout-safety tests (offline, deterministic).

# DATA-DATE: 2026-05-17
# REVIEW-TRIGGER: MKC Unilever close, FITB post-Comerica normalization
"""

from payout_safety import assess_payout_safety


# --- D2: MKC GAAP EPS inflated by non-cash gain; adjusted is the anchor ---
def test_mkc_gaap_distorted_flags_one_off_uses_adjusted():
    # GAAP EPS TTM inflated to ~12.9 by a non-cash remeasurement gain;
    # adjusted ~3.09 (FY guide). Div 1.92.
    a = assess_payout_safety(
        sector="consumer staples",
        annual_dividend=1.92,
        gaap_eps=12.9,
        adjusted_eps=3.09,
        adjusted_eps_source="MANUAL",
    )
    assert a.one_off_flag is True
    assert a.gaap_adj_divergence is not None and a.gaap_adj_divergence > 0.25
    # Adjusted-EPS payout ~62%, FCF unknown -> CAUTION band, not a fake "PASS"
    assert round(a.adjusted_eps_payout, 2) == round(1.92 / 3.09, 2)
    assert a.safety_verdict in ("CAUTION", "PASS")


def test_adjusted_eps_unavailable_caps_hold_review():
    a = assess_payout_safety(
        sector="consumer",
        annual_dividend=2.0,
        gaap_eps=5.0,
        adjusted_eps=None,
        adjusted_eps_source="UNAVAILABLE",
    )
    assert "adjusted_eps_unavailable" in a.blockers
    assert a.safety_verdict == "HOLD-REVIEW"  # fail-safe, never silent PASS


# --- FITB/Comerica: completed merger -> GAAP distorted -> HOLD-REVIEW ---
def test_completed_merger_without_adjusted_holds_review():
    a = assess_payout_safety(
        sector="banks",
        annual_dividend=1.60,
        gaap_eps=0.60,  # Q1-2026 GAAP $0.15-style distortion (annualized small)
        adjusted_eps=None,
        completed_merger_within_4q=True,
    )
    assert a.safety_verdict == "HOLD-REVIEW"
    assert a.one_off_flag is True
    assert "adjusted_eps_unavailable" in a.blockers


# --- banks: FCF ignored; NPL/NCO deterioration -> CAUTION (OZK golden) ---
def test_bank_npl_nco_deterioration_caution():
    a = assess_payout_safety(
        sector="financial services",
        annual_dividend=1.88,
        gaap_eps=6.17,
        adjusted_eps=6.17,
        adjusted_eps_source="FMP",
        fcf_per_share=-50.0,  # must be ignored for a bank
        bank_metrics={
            "cet1": 0.11,
            "cre_concentration": "high",
            "npl_trend": "deteriorating",
            "nco_trend": "deteriorating",
        },
    )
    assert a.sector_kind == "bank"
    assert a.safety_verdict == "CAUTION"
    assert "bank_npl_nco_deteriorating" in a.blockers


# --- utilities: negative FCF must NOT auto-FAIL ---
def test_utility_negative_fcf_not_auto_fail():
    a = assess_payout_safety(
        sector="utilities",
        annual_dividend=1.68,
        gaap_eps=2.74,
        adjusted_eps=2.74,
        adjusted_eps_source="FMP",
        fcf_per_share=-3.0,  # structurally negative -> ignored for utilities
        utility_metrics={"ffo_to_debt": 0.16, "rate_case_status": "constructive"},
    )
    assert a.sector_kind == "utility"
    assert a.safety_verdict == "PASS"


def test_utility_missing_ffo_debt_blocks():
    a = assess_payout_safety(
        sector="utilities",
        annual_dividend=1.68,
        gaap_eps=2.74,
        adjusted_eps=2.74,
        adjusted_eps_source="FMP",
        utility_metrics={"rate_case_status": "constructive"},
    )
    assert "utility_ffo_debt_unavailable" in a.blockers
    assert a.safety_verdict in ("CAUTION", "HOLD-REVIEW")


def test_utility_adverse_rate_case_holds_review():
    a = assess_payout_safety(
        sector="utilities",
        annual_dividend=1.68,
        gaap_eps=2.74,
        adjusted_eps=2.74,
        adjusted_eps_source="FMP",
        utility_metrics={"ffo_to_debt": 0.15, "rate_case_status": "adverse"},
    )
    assert a.safety_verdict == "HOLD-REVIEW"
    assert "utility_rate_case_adverse" in a.blockers


# --- consumer: clean name passes; over-100% FCF payout fails ---
def test_consumer_clean_pass():
    a = assess_payout_safety(
        sector="consumer staples",
        annual_dividend=1.0,
        gaap_eps=3.0,
        adjusted_eps=3.0,
        adjusted_eps_source="FMP",
        fcf_per_share=2.5,
    )
    assert a.safety_verdict == "PASS"
    assert not a.blockers


def test_consumer_fcf_payout_over_100_fails():
    a = assess_payout_safety(
        sector="consumer",
        annual_dividend=3.0,
        gaap_eps=4.0,
        adjusted_eps=4.0,
        adjusted_eps_source="FMP",
        fcf_per_share=2.0,  # 3.0/2.0 = 150% FCF payout
    )
    assert a.safety_verdict == "FAIL"


# --- #771: industry-aware routing (FMP sector alone misroutes) ---
def test_financial_services_insurance_industry_routes_insurer():
    # ORI repro from #771: FMP reports sector "Financial Services" with
    # industry "Insurance - Property & Casualty".
    a = assess_payout_safety(
        sector="Financial Services",
        industry="Insurance - Property & Casualty",
        annual_dividend=1.26,
        gaap_eps=3,
        insurer_metrics={"combined_ratio": 0.95},
    )
    assert a.sector_kind == "insurer"
    assert a.routing_source == "industry"
    assert "bank_capital_unavailable" not in a.blockers
    assert "bank_cre_concentration_unavailable" not in a.blockers
    assert a.safety_verdict == "PASS"


def test_insurance_industry_variants_route_insurer():
    for industry in (
        "Insurance - Life",
        "Insurance - Diversified",
        "Insurance - Specialty",
        "Insurance Brokers",
    ):
        a = assess_payout_safety(
            sector="Financial Services",
            industry=industry,
            annual_dividend=1.0,
            gaap_eps=3.0,
            insurer_metrics={"combined_ratio": 0.95},
        )
        assert a.sector_kind == "insurer", industry
        assert a.routing_source == "industry"


def test_reit_industry_routes_reit_and_affo_unavailable_cautions():
    # O-like REIT: sector "Real Estate", industry "REIT - Residential".
    # AFFO is not in FMP, so the REIT path must fail safe (CAUTION for
    # manual fill) instead of silently taking the consumer triad.
    a = assess_payout_safety(
        sector="Real Estate",
        industry="REIT - Residential",
        annual_dividend=2.0,
        gaap_eps=3.0,
    )
    assert a.sector_kind == "reit"
    assert a.routing_source == "industry"
    assert a.safety_verdict == "CAUTION"
    assert "reit_affo_unavailable" in a.blockers


def test_reit_affo_payout_bands():
    # AFFO payout is the REIT anchor; GAAP EPS is depreciation-distorted.
    base = dict(sector="Real Estate", industry="REIT - Retail", annual_dividend=2.0)
    healthy = assess_payout_safety(**base, gaap_eps=1.0, affo_per_share=3.0)  # 67%
    assert healthy.safety_verdict == "PASS"
    thin = assess_payout_safety(**base, gaap_eps=1.0, affo_per_share=2.1)  # 95%
    assert thin.safety_verdict == "CAUTION"
    assert "reit_affo_payout_caution_band" in thin.reasons
    unsustainable = assess_payout_safety(**base, gaap_eps=1.0, affo_per_share=1.5)  # 133%
    assert unsustainable.safety_verdict == "FAIL"
    assert "reit_affo_payout_above_100pct" in unsustainable.reasons


def test_reit_negative_gaap_eps_with_healthy_affo_passes():
    # GAAP EPS can be negative for a healthy REIT (depreciation); AFFO rules.
    a = assess_payout_safety(
        sector="Real Estate",
        industry="REIT - Office",
        annual_dividend=2.0,
        gaap_eps=-0.5,
        affo_per_share=3.0,
    )
    assert a.sector_kind == "reit"
    assert a.safety_verdict == "PASS"


def test_bank_industry_routes_bank():
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
    assert a.routing_source == "industry"
    assert a.safety_verdict == "PASS"


def test_utility_industry_routes_utility():
    a = assess_payout_safety(
        sector="Utilities",
        industry="Utilities - Regulated Electric",
        annual_dividend=1.68,
        gaap_eps=2.74,
        adjusted_eps=2.74,
        adjusted_eps_source="FMP",
        utility_metrics={"ffo_to_debt": 0.16, "rate_case_status": "constructive"},
    )
    assert a.sector_kind == "utility"
    assert a.routing_source == "industry"
    assert a.safety_verdict == "PASS"


def test_sector_only_fallback_unchanged_without_industry():
    # Existing behaviour preserved when FMP gives no industry.
    a = assess_payout_safety(
        sector="Financial Services",
        annual_dividend=1.88,
        gaap_eps=6.17,
        adjusted_eps=6.17,
        adjusted_eps_source="FMP",
        bank_metrics={"cet1": 0.11, "cre_concentration": "low"},
    )
    assert a.sector_kind == "bank"
    assert a.routing_source == "sector"


def test_real_estate_developer_stays_consumer():
    a = assess_payout_safety(
        sector="Real Estate",
        industry="Real Estate - Development",
        annual_dividend=1.0,
        gaap_eps=3.0,
        adjusted_eps=3.0,
        adjusted_eps_source="FMP",
        fcf_per_share=2.5,
    )
    assert a.sector_kind == "consumer"
    assert a.routing_source == "sector"


def test_missing_sector_and_industry_holds_review():
    a = assess_payout_safety(
        sector=None,
        industry=None,
        annual_dividend=1.0,
        gaap_eps=3.0,
        adjusted_eps=3.0,
        adjusted_eps_source="FMP",
        fcf_per_share=2.5,
    )
    assert a.sector_kind == "unknown"
    assert a.safety_verdict == "HOLD-REVIEW"
    assert "sector_industry_unknown" in a.blockers


def test_broad_financial_sector_unrecognized_industry_holds_review():
    # An asset manager under "Financial Services" is not a bank; do not
    # silently apply bank capital rules.
    a = assess_payout_safety(
        sector="Financial Services",
        industry="Asset Management",
        annual_dividend=1.0,
        gaap_eps=3.0,
        adjusted_eps=3.0,
        adjusted_eps_source="FMP",
    )
    assert a.sector_kind == "unknown"
    assert a.safety_verdict == "HOLD-REVIEW"
    assert "sector_industry_ambiguous" in a.blockers


def test_kind_override_wins_over_sector_and_industry():
    a = assess_payout_safety(
        sector="Financial Services",
        industry="Banks - Regional",
        kind_override="insurer",
        annual_dividend=1.26,
        gaap_eps=3,
        insurer_metrics={"combined_ratio": 0.95},
    )
    assert a.sector_kind == "insurer"
    assert a.routing_source == "kind_override"


def test_invalid_kind_override_holds_review():
    a = assess_payout_safety(
        sector="Consumer Staples",
        kind_override="bogus",
        annual_dividend=1.0,
        gaap_eps=3.0,
        adjusted_eps=3.0,
        adjusted_eps_source="FMP",
        fcf_per_share=2.5,
    )
    assert a.sector_kind == "unknown"
    assert a.safety_verdict == "HOLD-REVIEW"
    assert "kind_override_invalid" in a.blockers

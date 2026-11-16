"""WS-2: sector-aware payout-safety triad (GAAP / Adjusted / FCF).

Pure, dependency-free, offline-testable. Closes defect D2 (MKC payout
understated because a single GAAP-TTM proxy was inflated by a non-cash
acquisition remeasurement gain) and the over-strict utility -FCF problem.

Key rules (improvement-plan v2.1 R-3 + 4th-review):
- FCF payout is meaningless for banks and structurally negative for
  regulated utilities -> sector dispatch, NOT one uniform triad.
- Routing uses the FMP industry first (#771): insurers and REITs hide
  under broad sectors ("Financial Services", "Real Estate"), so
  sector alone misroutes them. Precedence: kind_override > industry
  > sector. A broad sector with an industry that does not resolve it
  is ambiguous -> HOLD-REVIEW; a broad sector with no industry keeps
  the established sector fallback (goldens pin it).
- Adjusted EPS is not in FMP; `adjusted_eps_source` is recorded and
  UNAVAILABLE -> verdict capped HOLD-REVIEW (fail-safe), never silent PASS.
- A merger completed within COMPLETED_MNA_LOOKBACK_QUARTERS presumes GAAP
  EPS is distorted -> force the adjusted path or HOLD-REVIEW
  (FITB/Comerica Q1-2026 GAAP EPS $0.15 is the golden case).
- Emits machine-checkable pre_order_blocker codes for WS-6 (4th-review #6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from thresholds import (
    ADJ_EPS_PAYOUT_CAUTION,
    ADJ_EPS_PAYOUT_MAX,
    BANK_CET1_MIN,
    BANK_DEPOSIT_BETA_HIGH,
    FCF_PAYOUT_HIGH_RISK,
    FCF_PAYOUT_MAX,
    GAAP_ADJ_DIVERGENCE,
    INSURER_OP_EPS_PAYOUT_MAX,
    REIT_AFFO_PAYOUT_MAX,
    UTILITY_FFO_DEBT_MIN,
)

_CONSUMER_SECTORS = {
    "consumer",
    "consumer defensive",
    "consumer staples",
    "consumer cyclical",
    "industrials",
    "industrial",
    "communication",
    "communication services",
    "technology",
    "healthcare",
    "energy",
    "materials",
    "basic materials",
    "real estate",  # treated as consumer-style triad unless flagged a REIT upstream
}
_BANK_SECTORS = {"bank", "banks", "financial", "financials", "financial services"}
_UTILITY_SECTORS = {"utility", "utilities"}
_INSURER_SECTORS = {"insurance", "insurer", "insurers"}


# Broad sectors that hide more than one specific kind: they need a resolving
# industry to disambiguate (bank vs insurer, REIT vs consumer real estate).
_BROAD_SECTORS = {"financial", "financials", "financial services", "real estate"}
_VALID_KINDS = {"bank", "utility", "insurer", "reit", "consumer"}


def _industry_kind(industry: str | None) -> str | None:
    """Map an FMP industry string to a sector kind, or None if it does not resolve."""
    ind = (industry or "").strip().lower()
    if not ind:
        return None
    if "insurance" in ind:
        return "insurer"
    if "reit" in ind:
        return "reit"
    if "bank" in ind:
        return "bank"
    if "utilit" in ind:  # utilities / utility
        return "utility"
    if "real estate" in ind:
        return "consumer"  # non-REIT real estate (development, services, ...)
    return None


def _sector_kind(sector: str | None, industry: str | None = None) -> tuple[str, str, bool]:
    """Resolve the payout-safety kind. Returns (kind, source, ambiguous).

    Precedence: industry > sector. A broad sector with an industry that does
    not resolve it is ambiguous (the two fields disagree in a way that cannot
    be resolved from the data). A broad sector with NO industry keeps the
    established sector fallback.
    """
    ind_kind = _industry_kind(industry)
    if ind_kind is not None:
        return ind_kind, "industry", False
    s = (sector or "").strip().lower()
    if industry and s in _BROAD_SECTORS:
        return "unknown", "unknown", True
    if s in _BANK_SECTORS:
        return "bank", "sector", False
    if s in _UTILITY_SECTORS:
        return "utility", "sector", False
    if s in _INSURER_SECTORS:
        return "insurer", "sector", False
    if s in _CONSUMER_SECTORS:
        return "consumer", "sector", False
    return "consumer", "unknown", False


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if not _finite_number(numerator) or not _finite_number(denominator) or denominator <= 0:
        return None
    return numerator / denominator


@dataclass
class SafetyAssessment:
    sector_kind: str
    safety_verdict: str  # PASS | CAUTION | FAIL | HOLD-REVIEW
    sector_kind_source: str = "unknown"  # industry | sector | override | unknown
    gaap_eps_payout: float | None = None
    adjusted_eps_payout: float | None = None
    fcf_payout: float | None = None
    adjusted_eps_source: str = "UNAVAILABLE"  # FMP | MANUAL | UNAVAILABLE
    gaap_adj_divergence: float | None = None
    one_off_flag: bool = False
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


def assess_payout_safety(
    *,
    sector: str | None,
    annual_dividend: float | None,
    gaap_eps: float | None = None,
    adjusted_eps: float | None = None,
    adjusted_eps_source: str = "UNAVAILABLE",
    fcf_per_share: float | None = None,
    industry: str | None = None,
    kind_override: str | None = None,
    affo_per_share: float | None = None,
    completed_merger_within_4q: bool = False,
    bank_metrics: dict | None = None,
    utility_metrics: dict | None = None,
    insurer_metrics: dict | None = None,
) -> SafetyAssessment:
    reasons: list[str] = []
    blockers: list[str] = []

    gaap_payout = _ratio(annual_dividend, gaap_eps)
    adj_payout = _ratio(annual_dividend, adjusted_eps)
    fcf_payout = _ratio(annual_dividend, fcf_per_share)
    affo_payout = _ratio(annual_dividend, affo_per_share)

    def _hold(kind: str, source: str, why: str) -> SafetyAssessment:
        return SafetyAssessment(
            sector_kind=kind,
            sector_kind_source=source,
            safety_verdict="HOLD-REVIEW",
            gaap_eps_payout=_round(gaap_payout),
            adjusted_eps_payout=_round(adj_payout),
            fcf_payout=_round(fcf_payout),
            adjusted_eps_source=adjusted_eps_source,
            reasons=[why],
            blockers=[why],
        )

    # Explicit kind override wins over every signal; an unknown override is
    # a caller error, not a silent default.
    if kind_override is not None:
        override = kind_override.strip().lower()
        if override not in _VALID_KINDS:
            return _hold("unknown", "unknown", "sector_kind_override_invalid")
        kind, kind_source = override, "override"
    else:
        kind, kind_source, ambiguous = _sector_kind(sector, industry)
        if ambiguous:
            return _hold(
                "unknown",
                "unknown",
                "sector_kind_ambiguous",
            )

    # Reject invalid income anchors before division/divergence can hide losses.
    # FCF is deliberately not an anchor for banks, insurers, REITs or utilities.
    anchors = {"gaap_eps": gaap_eps}
    if kind == "consumer":
        anchors.update(adjusted_eps=adjusted_eps, fcf_per_share=fcf_per_share)
    elif kind == "insurer" and adjusted_eps is not None:
        anchors["adjusted_eps"] = adjusted_eps
    for name, value in anchors.items():
        if value is not None and (not _finite_number(value) or value <= 0):
            blockers.append(name + "_non_positive_or_unavailable")
    if not _finite_number(annual_dividend) or annual_dividend < 0:
        blockers.append("annual_dividend_invalid")
    if blockers:
        return SafetyAssessment(
            sector_kind=kind,
            sector_kind_source=kind_source,
            safety_verdict="HOLD-REVIEW",
            gaap_eps_payout=_round(gaap_payout),
            adjusted_eps_payout=_round(adj_payout),
            fcf_payout=_round(fcf_payout),
            adjusted_eps_source=adjusted_eps_source,
            reasons=list(blockers),
            blockers=sorted(blockers),
        )

    # --- GAAP vs Adjusted divergence -> one-off flag (D2) ---
    one_off = False
    divergence = None
    if _finite_number(gaap_eps) and _finite_number(adjusted_eps) and adjusted_eps > 0:
        divergence = abs(gaap_eps - adjusted_eps) / abs(adjusted_eps)
        if divergence > GAAP_ADJ_DIVERGENCE:
            one_off = True
            reasons.append(f"gaap_adj_divergence_{round(divergence * 100)}pct")
            blockers.append("gaap_adjusted_divergence_gt_25pct")

    # --- completed-merger linkage: GAAP presumed distorted (FITB/Comerica) ---
    if completed_merger_within_4q:
        reasons.append("completed_merger_within_4q_gaap_distorted")
        if adjusted_eps is None:
            blockers.append("adjusted_eps_unavailable")
            return SafetyAssessment(
                sector_kind=kind,
                sector_kind_source=kind_source,
                safety_verdict="HOLD-REVIEW",
                gaap_eps_payout=_round(gaap_payout),
                adjusted_eps_payout=None,
                fcf_payout=_round(fcf_payout),
                adjusted_eps_source=adjusted_eps_source,
                gaap_adj_divergence=_round(divergence, 4),
                one_off_flag=True,
                reasons=reasons + ["use_adjusted_eps_after_merger"],
                blockers=blockers,
            )
        one_off = True

    # Adjusted EPS is the safety ANCHOR only for the consumer path. For
    # banks/utilities/insurers/REITs the sector module is the anchor, so a
    # missing adjusted EPS must NOT force HOLD-REVIEW there (5th-review F2).
    # The completed-merger GAAP-distortion path above still HOLDs regardless.
    adj_missing = adjusted_eps is None or adjusted_eps_source == "UNAVAILABLE"

    if kind == "bank":
        verdict = _assess_bank(bank_metrics or {}, gaap_payout, reasons, blockers)
    elif kind == "utility":
        verdict = _assess_utility(utility_metrics or {}, gaap_payout, reasons, blockers)
    elif kind == "insurer":
        verdict = _assess_insurer(insurer_metrics or {}, gaap_payout, adj_payout, reasons, blockers)
    elif kind == "reit":
        verdict = _assess_reit(affo_payout, gaap_payout, reasons, blockers)
    else:
        if adj_missing:
            blockers.append("adjusted_eps_unavailable")
            reasons.append("adjusted_eps_unavailable_failsafe_hold")
        verdict = _assess_consumer(adj_payout, fcf_payout, adjusted_eps, reasons, blockers)
        if "adjusted_eps_unavailable" in blockers and verdict in ("PASS", "CAUTION"):
            verdict = "HOLD-REVIEW"

    return SafetyAssessment(
        sector_kind=kind,
        sector_kind_source=kind_source,
        safety_verdict=verdict,
        gaap_eps_payout=_round(gaap_payout),
        adjusted_eps_payout=_round(adj_payout),
        fcf_payout=_round(fcf_payout),
        adjusted_eps_source=adjusted_eps_source,
        gaap_adj_divergence=_round(divergence, 4),
        one_off_flag=one_off,
        reasons=reasons,
        blockers=sorted(set(blockers)),
    )


def _round(value: float | None, ndigits: int = 4) -> float | None:
    return round(value, ndigits) if value is not None else None


def _assess_consumer(adj_payout, fcf_payout, adjusted_eps, reasons, blockers) -> str:
    # Primary: adjusted-EPS payout + FCF payout.
    if fcf_payout is not None and fcf_payout > FCF_PAYOUT_HIGH_RISK:
        reasons.append("fcf_payout_above_100pct")
        return "FAIL"
    if adj_payout is not None and adj_payout > ADJ_EPS_PAYOUT_MAX:
        reasons.append("adj_eps_payout_above_85pct")
        return "FAIL"
    caution = False
    if adj_payout is not None and adj_payout > ADJ_EPS_PAYOUT_CAUTION:
        reasons.append("adj_eps_payout_caution_band")
        caution = True
    if fcf_payout is not None and fcf_payout > FCF_PAYOUT_MAX:
        reasons.append("fcf_payout_above_80pct")
        caution = True
    if adjusted_eps is None:
        return "HOLD-REVIEW"
    return "CAUTION" if caution else "PASS"


def _assess_bank(m: dict, eps_payout, reasons, blockers) -> str:
    # FCF payout is meaningless for banks; use EPS payout + credit + capital.
    npl_trend = str(m.get("npl_trend", "")).lower()
    nco_trend = str(m.get("nco_trend", "")).lower()
    cet1 = m.get("cet1")
    cre_concentration = m.get("cre_concentration")
    verdict = "PASS"
    if not _finite_number(cet1) or not 0 <= cet1 <= 1:
        blockers.append("bank_capital_unavailable")
        reasons.append("bank_cet1_invalid_or_unavailable_fraction_required")
        verdict = "CAUTION"
    elif cet1 < BANK_CET1_MIN:
        blockers.append("bank_capital_weak")
        reasons.append("bank_cet1_below_screening_floor")
        verdict = "CAUTION"
    cre = str(cre_concentration).strip().lower()
    if cre not in ("low", "moderate", "medium", "high"):
        blockers.append("bank_cre_concentration_unavailable")
        reasons.append("bank_cre_concentration_invalid_or_unavailable")
        verdict = "CAUTION"
    elif cre == "high":
        blockers.append("bank_cre_concentration_high")
        reasons.append("bank_cre_concentration_high")
        verdict = "CAUTION"
    if npl_trend == "deteriorating" or nco_trend == "deteriorating":
        reasons.append("bank_npl_nco_deteriorating")
        blockers.append("bank_npl_nco_deteriorating")
        verdict = "CAUTION"
    deposit_beta = m.get("deposit_beta")
    db_str = str(deposit_beta).lower()
    if (isinstance(deposit_beta, (int, float)) and deposit_beta > BANK_DEPOSIT_BETA_HIGH) or (
        db_str in ("high", "rising")
    ):
        reasons.append("bank_deposit_beta_elevated")
        if verdict == "PASS":
            verdict = "CAUTION"
    if eps_payout is not None and eps_payout > ADJ_EPS_PAYOUT_MAX:
        reasons.append("bank_eps_payout_high")
        verdict = "FAIL"
    return verdict


def _assess_utility(m: dict, eps_payout, reasons, blockers) -> str:
    # Regulated utilities run structurally negative FCF (rate-base capex);
    # judge on FFO/debt + allowed ROE + rate-case + equity issuance instead.
    ffo_debt = m.get("ffo_to_debt")
    rate_case = str(m.get("rate_case_status", "")).lower()
    equity_issuance = str(m.get("equity_issuance_risk", "")).lower()
    allowed_roe_trend = str(m.get("allowed_roe_trend", "")).lower()
    verdict = "PASS"
    if ffo_debt is None:
        blockers.append("utility_ffo_debt_unavailable")
        verdict = "CAUTION"
    elif ffo_debt < UTILITY_FFO_DEBT_MIN:
        reasons.append(f"utility_ffo_debt_below_{UTILITY_FFO_DEBT_MIN}")
        blockers.append("utility_ffo_debt_weak")
        verdict = "CAUTION"
    if allowed_roe_trend in ("falling", "adverse"):
        reasons.append("utility_allowed_roe_falling")
        if verdict == "PASS":
            verdict = "CAUTION"
    if rate_case in ("adverse", "pending_adverse"):
        reasons.append("utility_rate_case_adverse")
        blockers.append("utility_rate_case_adverse")
        verdict = "HOLD-REVIEW"
    if equity_issuance in ("high", "dilutive"):
        reasons.append("utility_equity_issuance_risk")
        if verdict == "PASS":
            verdict = "CAUTION"
    if eps_payout is not None and eps_payout > 1.0:
        reasons.append("utility_eps_payout_above_100pct")
        verdict = "FAIL"
    return verdict


def _assess_insurer(m: dict, gaap_payout, op_eps_payout, reasons, blockers) -> str:
    combined_ratio = m.get("combined_ratio")
    reserve_dev = str(m.get("reserve_development", "")).lower()
    statutory = str(m.get("statutory_capital", "")).lower()
    verdict = "PASS"
    # Operating-EPS payout is the insurer income anchor (noisy GAAP).
    op_payout = op_eps_payout if op_eps_payout is not None else gaap_payout
    if op_payout is not None and op_payout > INSURER_OP_EPS_PAYOUT_MAX:
        reasons.append("insurer_operating_eps_payout_high")
        verdict = "FAIL"
    if combined_ratio is None:
        blockers.append("insurer_combined_ratio_unavailable")
        if verdict == "PASS":
            verdict = "CAUTION"
    if reserve_dev == "adverse":
        reasons.append("insurer_reserve_development_adverse")
        blockers.append("insurer_reserve_development_adverse")
        if verdict != "FAIL":  # never de-escalate a FAIL
            verdict = "HOLD-REVIEW"
    if combined_ratio is not None and combined_ratio > 1.0:
        reasons.append("insurer_combined_ratio_above_100")
        if verdict == "PASS":
            verdict = "CAUTION"
    if statutory == "weak":
        reasons.append("insurer_statutory_capital_weak")
        if verdict != "FAIL":
            verdict = "HOLD-REVIEW"
    return verdict


def _assess_reit(affo_payout, gaap_payout, reasons, blockers) -> str:
    # REITs pay from AFFO, not FCF/EPS: the AFFO payout is the anchor.
    # AFFO is not in FMP, so a missing AFFO per share fail-safes to CAUTION
    # with an explicit blocker (manual fill) rather than a silent PASS.
    verdict = "PASS"
    if affo_payout is None:
        blockers.append("reit_affo_unavailable")
        reasons.append("reit_affo_per_share_unavailable")
        verdict = "CAUTION"
    elif affo_payout > REIT_AFFO_PAYOUT_MAX:
        reasons.append("reit_affo_payout_high")
        verdict = "FAIL"
    elif affo_payout > ADJ_EPS_PAYOUT_CAUTION:
        reasons.append("reit_affo_payout_caution_band")
        verdict = "CAUTION"
    if gaap_payout is not None and gaap_payout > 1.0:
        reasons.append("reit_gaap_payout_above_100pct")
        verdict = "FAIL"
    return verdict

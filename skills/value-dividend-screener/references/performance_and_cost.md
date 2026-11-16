# Performance and Cost Optimization

Moved verbatim from `SKILL.md`.

### API Call Comparison

**Two-Stage Screening (FINVIZ + FMP):**
- FINVIZ: 1 API call
- FMP Quote API: ~30-50 calls (one per pre-screened symbol)
- FMP Financial Data: ~150-250 calls (5 endpoints × 30-50 symbols)
- **Total FMP calls: ~180-300**

**FMP-Only Screening:**
- FMP Stock Screener: 1 call (returns 100-1000 stocks)
- FMP Financial Data: ~500-5000 calls (5 endpoints × 100-1000 symbols)
- **Total FMP calls: ~500-5000**

**Savings: 60-94% reduction in FMP API usage**

### Cost Analysis

**FINVIZ Elite:**
- Monthly: $39.50
- Annual: $299.50 (~$24.96/month)

**FMP API:**
- Free tier: 250 calls/day rate limit. The stock-screener endpoint used by FMP-only screening is not on the free plan (2026-11); use two-stage screening or a plan that includes it. The per-symbol endpoints used for detailed analysis (income-statement, dividends, historical prices, ratios) returned the same 402 refusal in the synthetic repro for #777, so a free-tier run can still end plan-gated; the screeners report that instead of an empty result. See `docs/en/getting-started.md#fmp-plans`.
- Paid plans: prices and limits change; see `site.financialmodelingprep.com/pricing-plans` in a browser

**Recommendation:**
- **For free FMP tier users**: Use two-stage screening (FINVIZ + FMP free tier); FMP-only needs the plan-gated stock screener. Per-symbol endpoints can still be plan-gated on a Basic key (see the FMP note above); a run that hits that reports plan-gating instead of an empty result
- **For paid FMP tier users**: Either approach works; two-stage is faster
- **Budget option**: FMP-only on a plan that includes the stock screener (the free plan does not)
- **Optimal option**: FINVIZ Elite ($330/year) + FMP free tier for two-stage screening

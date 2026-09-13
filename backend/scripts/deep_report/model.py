"""
The numbers half of a deep-dive report. Every figure here is either read from
screener.in's statements ([F]) or computed from them with the method written
next to it ([E]); the writer never makes a number up, it only interprets these.

  statements()  P&L / balance sheet / cash flow / ratios, last 10 years + TTM
  quarterly()   last 12 quarters with YoY growth
  derived()     returns, leverage, cash conversion, capex, growth CAGRs
  valuation()   multiples, 5-year FCFF DCF (bear / base / bull), WACC x g
                sensitivity, and a reverse DCF: the growth the price implies
  red_flags()   rule-based green / amber / red checks
"""
from __future__ import annotations

import math
import statistics

RF = 7.0          # India 10-year G-sec, % (override with the REPORT_RF env var)
ERP = 7.0         # India equity risk premium, % (mature market + country risk)
BETA = 1.15       # small / micro-cap default; the board has no reliable per-stock beta
KD_PRE_TAX = 10.0 # typical small-cap working-capital / term loan rate, %
TAX = 25.2        # new-regime corporate tax incl. surcharge and cess, %
TERMINAL_G = 5.5  # below India's long-run nominal GDP growth, %
YEARS = 5


def _r(v, d=1):
    return None if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else round(v, d)


def _row(t, *names):
    for n in names:
        if n in t["rows"]:
            return t["rows"][n]
    return [None] * len(t["columns"])


def _cagr(a, b, years):
    if a is None or b is None or years <= 0 or a <= 0 or b <= 0:
        return None
    return ((b / a) ** (1 / years) - 1) * 100


def _div(a, b):
    return None if a is None or b in (None, 0) else a / b


def statements(c: dict) -> dict:
    pl, bs, cf, rt = c["profit_loss"], c["balance_sheet"], c["cash_flow"], c["ratios"]
    cols = pl["columns"]
    ttm = "TTM" in cols
    annual_cols = [x for x in cols if x != "TTM"][-10:]

    def series(t, *names):
        vals = dict(zip(t["columns"], _row(t, *names)))
        return [vals.get(col) for col in annual_cols]

    sales = series(pl, "Sales", "Revenue")
    op = series(pl, "Operating Profit", "Financing Profit")
    years = {
        "columns": annual_cols,
        "sales": sales,
        "expenses": series(pl, "Expenses"),
        "ebitda": op,
        "opm": series(pl, "OPM %", "Financing Margin %"),
        "other_income": series(pl, "Other Income"),
        "interest": series(pl, "Interest"),
        "depreciation": series(pl, "Depreciation"),
        "pbt": series(pl, "Profit before tax"),
        "tax_rate": series(pl, "Tax %"),
        "pat": series(pl, "Net Profit"),
        "eps": series(pl, "EPS in Rs"),
        "payout": series(pl, "Dividend Payout %"),
        "equity_capital": series(bs, "Equity Capital"),
        "reserves": series(bs, "Reserves"),
        "borrowings": series(bs, "Borrowings"),
        "other_liabilities": series(bs, "Other Liabilities"),
        "total_assets": series(bs, "Total Assets"),
        "fixed_assets": series(bs, "Fixed Assets"),
        "cwip": series(bs, "CWIP"),
        "investments": series(bs, "Investments"),
        "other_assets": series(bs, "Other Assets"),
        "cfo": series(cf, "Cash from Operating Activity"),
        "cfi": series(cf, "Cash from Investing Activity"),
        "cff": series(cf, "Cash from Financing Activity"),
        "fcf": series(cf, "Free Cash Flow"),
        "debtor_days": series(rt, "Debtor Days"),
        "inventory_days": series(rt, "Inventory Days"),
        "payable_days": series(rt, "Days Payable"),
        "ccc": series(rt, "Cash Conversion Cycle"),
        "wc_days": series(rt, "Working Capital Days"),
        "roce": series(rt, "ROCE %"),
    }
    years["equity"] = [None if a is None and b is None else (a or 0) + (b or 0) for a, b in zip(years["equity_capital"], years["reserves"])]
    years["capex"] = [None if o is None or f is None else o - f for o, f in zip(years["cfo"], years["fcf"])]
    if ttm:
        i = cols.index("TTM")
        years["ttm"] = {k: _row(pl, *names)[i] for k, names in {
            "sales": ("Sales", "Revenue"), "ebitda": ("Operating Profit", "Financing Profit"), "opm": ("OPM %", "Financing Margin %"),
            "other_income": ("Other Income",), "interest": ("Interest",), "depreciation": ("Depreciation",),
            "pbt": ("Profit before tax",), "pat": ("Net Profit",), "eps": ("EPS in Rs",)}.items()}
    return years


def quarterly(c: dict) -> dict:
    q = c["quarters"]
    cols = q["columns"][-12:]
    n = len(q["columns"])

    def s(*names):
        return _row(q, *names)[n - len(cols):]

    sales, pat = s("Sales", "Revenue"), s("Net Profit")
    out = {"columns": cols, "sales": sales, "ebitda": s("Operating Profit", "Financing Profit"), "opm": s("OPM %", "Financing Margin %"),
           "other_income": s("Other Income"), "interest": s("Interest"), "depreciation": s("Depreciation"),
           "pbt": s("Profit before tax"), "tax_rate": s("Tax %"), "pat": pat, "eps": s("EPS in Rs")}
    out["sales_yoy"] = [_r(_div((sales[i] - sales[i - 4]), abs(sales[i - 4])) * 100) if i >= 4 and sales[i] is not None and sales[i - 4] not in (None, 0) else None for i in range(len(cols))]
    out["pat_yoy"] = [_r(_div((pat[i] - pat[i - 4]), abs(pat[i - 4])) * 100) if i >= 4 and pat[i] is not None and pat[i - 4] not in (None, 0) else None for i in range(len(cols))]
    return out


def derived(y: dict, q: dict, sh: dict) -> dict:
    cols = y["columns"]
    last = len(cols) - 1

    def at(key, i=None):
        arr = y[key]
        i = last if i is None else i
        return arr[i] if 0 <= i < len(arr) else None

    roe = [_r(_div(y["pat"][i], ((y["equity"][i] or 0) + (y["equity"][i - 1] or y["equity"][i] or 0)) / 2) * 100) if i > 0 and y["pat"][i] is not None and y["equity"][i] else None for i in range(len(cols))]
    de = [_r(_div(y["borrowings"][i], y["equity"][i]), 2) for i in range(len(cols))]
    icov = [_r(_div((y["pbt"][i] or 0) + (y["interest"][i] or 0), y["interest"][i]), 1) if y["interest"][i] else None for i in range(len(cols))]
    cfo_pat = [_r(_div(y["cfo"][i], y["pat"][i]), 2) if (y["pat"][i] or 0) > 0 else None for i in range(len(cols))]
    capex_sales = [_r(_div(y["capex"][i], y["sales"][i]) * 100) if y["capex"][i] is not None and y["sales"][i] else None for i in range(len(cols))]
    fat = [_r(_div(y["sales"][i], y["fixed_assets"][i]), 2) for i in range(len(cols))]
    other_inc_pbt = [_r(_div(y["other_income"][i], y["pbt"][i]) * 100) if (y["pbt"][i] or 0) > 0 and y["other_income"][i] is not None else None for i in range(len(cols))]

    def window_sum(key, n):
        vals = [v for v in y[key][-n:] if v is not None]
        return sum(vals) if len(vals) == n else None

    cum = {}
    for n in (3, 5):
        cfo, pat = window_sum("cfo", n), window_sum("pat", n)
        cum[f"cfo_pat_{n}y"] = _r(_div(cfo, pat), 2) if pat and pat > 0 else None
        cum[f"fcf_{n}y"] = _r(window_sum("fcf", n))
    growth = {}
    for key in ("sales", "ebitda", "pat"):
        for n in (3, 5, 9):
            if last - n >= 0:
                growth[f"{key}_{n}y"] = _r(_cagr(y[key][last - n], y[key][last], n))
    ttm = y.get("ttm") or {}
    if ttm.get("sales") and at("sales"):
        growth["sales_ttm_vs_fy"] = _r((ttm["sales"] / at("sales") - 1) * 100)
    promoter = _row(sh, "Promoters")
    promoter_chg_1y = _r(promoter[-1] - promoter[-5], 2) if len(promoter) >= 5 and promoter[-1] is not None and promoter[-5] is not None else None
    return {"roe": roe, "debt_equity": de, "interest_cover": icov, "cfo_pat": cfo_pat, "capex_sales": capex_sales,
            "fixed_asset_turnover": fat, "other_income_pct_pbt": other_inc_pbt, "cumulative": cum, "growth": growth,
            "promoter_chg_1y": promoter_chg_1y}


# ------------------------------------------------------------------ valuation
def _wacc(mcap, debt):
    ke = RF + BETA * ERP
    kd = KD_PRE_TAX * (1 - TAX / 100)
    total = (mcap or 0) + (debt or 0)
    wd = (debt or 0) / total if total else 0
    return ke, kd, wd, ke * (1 - wd) + kd * wd


def _project(base_sales, g0, margin, da_pct, fat, wc_days, wacc, tg, years=YEARS, fade_to=10.0):
    """FCFF for `years` years with growth fading linearly from g0 to fade_to; returns (rows, EV).
    Capex = depreciation (keeping the asset base) + the fixed assets the extra
    sales need at the company's own fixed-asset turnover `fat`."""
    rows, sales_prev, pv = [], base_sales, 0.0
    nwc_prev = base_sales * wc_days / 365
    for t in range(1, years + 1):
        g = g0 + (fade_to - g0) * (t - 1) / (years - 1) if years > 1 else g0
        sales = sales_prev * (1 + g / 100)
        ebitda = sales * margin / 100
        da = sales * da_pct / 100
        tax = max(ebitda - da, 0) * TAX / 100
        capex = da + max(0.0, sales - sales_prev) / fat
        nwc = sales * wc_days / 365
        fcff = ebitda - tax - capex - (nwc - nwc_prev)
        disc = (1 + wacc / 100) ** t
        pv += fcff / disc
        rows.append({"year": t, "growth": _r(g), "sales": _r(sales), "ebitda": _r(ebitda), "fcff": _r(fcff), "pv": _r(fcff / disc)})
        sales_prev, nwc_prev = sales, nwc
    if wacc <= tg:
        return rows, None
    # terminal year: growth tg, capex and working capital re-based to that growth
    s_n = sales_prev * (1 + tg / 100)
    e_n, da_n = s_n * margin / 100, s_n * da_pct / 100
    fcff_n = e_n - max(e_n - da_n, 0) * TAX / 100 - (da_n + (s_n - sales_prev) / fat) - (s_n - sales_prev) * wc_days / 365
    tv = fcff_n / ((wacc - tg) / 100)
    pv_tv = tv / (1 + wacc / 100) ** years
    return rows, {"pv_explicit": _r(pv), "terminal_value": _r(tv), "pv_terminal": _r(pv_tv), "ev": _r(pv + pv_tv)}


def valuation(c: dict, y: dict, d: dict) -> dict:
    tr = c["top_ratios"]
    price, mcap = tr.get("Current Price"), tr.get("Market Cap")
    ttm = y.get("ttm") or {}
    if not ttm.get("sales"):
        # half-yearly (SME) filers have no TTM column: use the last full year
        li = len(y["columns"]) - 1
        ttm = {k: y[k][li] for k in ("sales", "ebitda", "pat", "eps")} if li >= 0 else {}
        out_basis = f"last full year ({y['columns'][li]})" if li >= 0 else None
    else:
        out_basis = "trailing twelve months"
    debt = next((v for v in reversed(y["borrowings"]) if v is not None), 0) or 0
    investments = next((v for v in reversed(y["investments"]) if v is not None), 0) or 0
    shares_cr = _div(mcap, price)  # crore shares
    ev = (mcap or 0) + debt
    out = {
        "price": price, "market_cap": mcap, "high_52w": tr.get("High"), "low_52w": tr.get("Low"),
        "from_high_pct": _r((price / tr["High"] - 1) * 100) if price and tr.get("High") else None,
        "book_value": tr.get("Book Value"), "dividend_yield": tr.get("Dividend Yield"),
        "pe_ttm": _r(_div(mcap, ttm.get("pat")) if (ttm.get("pat") or 0) > 0 else None),
        "ev": _r(ev), "ev_ebitda_ttm": _r(_div(ev, ttm.get("ebitda")) if (ttm.get("ebitda") or 0) > 0 else None),
        "ev_sales_ttm": _r(_div(ev, ttm.get("sales")), 2),
        "pb": _r(_div(price, tr.get("Book Value")), 2),
        "net_debt_note": "EV = market cap + latest borrowings. Screener's public page does not split cash out of other assets, so cash is not netted off; investments are shown separately.",
        "debt": _r(debt), "investments": _r(investments), "earnings_basis": out_basis,
    }
    base_sales = ttm.get("sales") or next((v for v in reversed(y["sales"]) if v is not None), None)
    opms = [v for v in y["opm"][-3:] if v is not None]
    if not base_sales or base_sales <= 0 or not opms or not price or not mcap:
        out["dcf"] = {"available": False, "reason": "Not enough history for a DCF (sales, margins or market price missing)."}
        return out
    margin = statistics.median(opms)
    if margin <= 0:
        out["dcf"] = {"available": False, "reason": f"Operating margin is {margin:.1f}% (median of the last three years); a cash-flow DCF is not meaningful for a loss-making business."}
        return out
    g_candidates = [v for v in (d["growth"].get("sales_3y"), d["growth"].get("sales_5y"), d["growth"].get("sales_ttm_vs_fy")) if v is not None]
    g0 = max(0.0, min(30.0, statistics.median(g_candidates))) if g_candidates else 10.0
    last = len(y["columns"]) - 1
    da_pct = (_div(y["depreciation"][last], y["sales"][last]) or 0.03) * 100
    fat_hist = [v for v in d["fixed_asset_turnover"][-3:] if v]
    fat = min(10.0, max(0.5, statistics.median(fat_hist))) if fat_hist else 2.0
    wc_hist = [v for v in y["wc_days"][-3:] if v is not None]
    wc_days = max(0.0, statistics.mean(wc_hist)) if wc_hist else 60.0
    ke, kd, wd, wacc = _wacc(mcap, debt)

    scenarios = {
        "bear": {"g0": g0 * 0.4, "margin": max(1.0, margin - 4), "fade_to": 5.0},
        "base": {"g0": g0, "margin": margin, "fade_to": 10.0},
        "bull": {"g0": min(45.0, g0 * 1.35 + 3), "margin": margin + 2, "fade_to": 13.0},
    }
    results = {}
    for name, s in scenarios.items():
        rows, ev_ = _project(base_sales, s["g0"], s["margin"], da_pct, fat, wc_days, wacc, TERMINAL_G, fade_to=s["fade_to"])
        eq = (ev_["ev"] - debt) if ev_ and ev_["ev"] is not None else None
        results[name] = {"assumptions": {"start_growth": _r(s["g0"]), "growth_by_year5": s["fade_to"], "ebitda_margin": _r(s["margin"])},
                         "projection": rows, **(ev_ or {}), "equity_value": _r(eq),
                         "per_share": _r(_div(eq, shares_cr)) if eq is not None and shares_cr else None}
    base = results["base"]
    sens = {"wacc": [], "g": [4.5, 5.5, 6.5], "values": []}
    for w in (wacc - 1.5, wacc, wacc + 1.5):
        sens["wacc"].append(_r(w))
        row = []
        for tg in sens["g"]:
            _, ev_ = _project(base_sales, g0, margin, da_pct, fat, wc_days, w, tg)
            row.append(_r(_div(ev_["ev"] - debt, shares_cr)) if ev_ and ev_["ev"] is not None and shares_cr else None)
        sens["values"].append(row)

    # reverse DCF: the constant 5-year growth (base margin) at which the DCF equals today's EV
    def ev_at(g):
        _, ev_ = _project(base_sales, g, margin, da_pct, fat, wc_days, wacc, TERMINAL_G, fade_to=g)
        return ev_["ev"] if ev_ else None
    implied, lo, hi = None, -30.0, 150.0
    if ev_at(lo) is not None and ev_at(hi) is not None and ev_at(lo) <= ev <= ev_at(hi):
        for _ in range(60):
            mid = (lo + hi) / 2
            if ev_at(mid) < ev:
                lo = mid
            else:
                hi = mid
        implied = (lo + hi) / 2
    out["dcf"] = {
        "available": True,
        "meaningful": (base.get("per_share") or 0) > 0,
        "method": "5-year FCFF: EBITDA - tax on EBIT - capex - change in net working capital; growth fades linearly from the starting rate to the year-5 rate; Gordon terminal value; equity = EV - borrowings.",
        "inputs": {"base_sales_ttm": _r(base_sales), "ebitda_margin_base": _r(margin), "starting_growth_base": _r(g0),
                   "growth_basis": "median of 3-year, 5-year and TTM sales growth, capped 0-30%",
                   "da_pct_sales": _r(da_pct), "fixed_asset_turnover": _r(fat, 2),
                   "capex_rule": "depreciation + new sales / fixed-asset turnover (median of the last three years)", "wc_days": _r(wc_days), "tax_rate": TAX,
                   "rf": RF, "erp": ERP, "beta": BETA, "cost_of_equity": _r(ke), "cost_of_debt_post_tax": _r(kd),
                   "debt_weight": _r(wd * 100), "wacc": _r(wacc), "terminal_growth": TERMINAL_G, "shares_cr": _r(shares_cr, 3)},
        "scenarios": results,
        "sensitivity": sens,
        "reverse_dcf": {"implied_5y_sales_cagr": _r(implied), "base_case_5y_cagr_equivalent": _r(
            (math.prod(1 + (r["growth"] or 0) / 100 for r in base["projection"]) ** (1 / YEARS) - 1) * 100),
            "note": "Constant sales growth for five years, at the base margin and the same WACC and terminal growth, that makes the DCF equal today's EV." if implied is not None
            else "The market EV sits outside what any growth rate between -30% and +150% a year produces at these margins."},
        "upside_pct": {k: _r((v["per_share"] / price - 1) * 100) if v["per_share"] and price else None for k, v in results.items()},
    }
    return out


# ------------------------------------------------------------------ red flags
def red_flags(y: dict, q: dict, d: dict, v: dict, sh: dict) -> list[dict]:
    flags = []

    def add(level, title, detail, label="[F]"):
        flags.append({"level": level, "title": title, "detail": detail, "label": label})

    last = len(y["columns"]) - 1
    cp3 = d["cumulative"].get("cfo_pat_3y")
    if cp3 is not None:
        if cp3 < 0.5:
            add("red", "Weak cash conversion", f"Operating cash flow was {cp3:.2f}x reported profit over the last three years.", "[E]")
        elif cp3 < 0.8:
            add("amber", "Cash conversion below profit", f"Operating cash flow was {cp3:.2f}x reported profit over the last three years.", "[E]")
        else:
            add("green", "Profits turn into cash", f"Operating cash flow was {cp3:.2f}x reported profit over the last three years.", "[E]")
    dd = [x for x in y["debtor_days"] if x is not None]
    if len(dd) >= 4:
        prior = statistics.mean(dd[-4:-1])
        if dd[-1] > 90 and dd[-1] > prior * 1.5:
            add("red", "Receivables stretching", f"Debtor days {dd[-1]:.0f} vs a {prior:.0f}-day average over the prior three years.")
        elif dd[-1] > prior * 1.3 and dd[-1] > 60:
            add("amber", "Debtor days rising", f"Debtor days {dd[-1]:.0f} vs a {prior:.0f}-day average over the prior three years.")
    wcd = [x for x in y["wc_days"] if x is not None]
    if len(wcd) >= 4 and wcd[-1] > 120 and wcd[-1] > statistics.mean(wcd[-4:-1]) * 1.4:
        add("amber", "Working capital absorbing cash", f"Working-capital days {wcd[-1]:.0f}, up from a {statistics.mean(wcd[-4:-1]):.0f}-day average.")
    de = d["debt_equity"][last] if d["debt_equity"] else None
    b = y["borrowings"]
    if de is not None:
        if de > 1:
            add("red", "High leverage", f"Borrowings are {de:.2f}x equity.")
        elif de > 0.5:
            add("amber", "Meaningful leverage", f"Borrowings are {de:.2f}x equity.")
        elif de < 0.1:
            add("green", "Little or no debt", f"Borrowings are {de:.2f}x equity.")
    if last >= 1 and b[last] and b[last - 1] and b[last] > b[last - 1] * 1.5 and b[last] - b[last - 1] > 10:
        add("amber", "Borrowings jumped", f"Borrowings rose from Rs {b[last-1]:,.0f} cr to Rs {b[last]:,.0f} cr in a year.")
    ic = d["interest_cover"][last] if d["interest_cover"] else None
    if ic is not None and ic < 3:
        add("red" if ic < 1.5 else "amber", "Thin interest cover", f"Profit before interest and tax covers interest {ic:.1f}x.", "[E]")
    oi = d["other_income_pct_pbt"][last]
    if oi is not None and oi > 25:
        add("amber", "Profit leans on other income", f"Other income is {oi:.0f}% of profit before tax.", "[E]")
    tax = y["tax_rate"][last]
    if tax is not None and (tax < 15 or tax > 40) and (y["pbt"][last] or 0) > 0:
        add("amber", "Unusual tax rate", f"Effective tax rate {tax:.0f}% last year.")
    fcf = [x for x in y["fcf"][-5:] if x is not None]
    if len(fcf) == 5 and sum(1 for x in fcf if x < 0) >= 3:
        add("amber", "Free cash flow mostly negative", f"Free cash flow was negative in {sum(1 for x in fcf if x < 0)} of the last five years.")
    ec = y["equity_capital"]
    if last >= 1 and ec[last] and ec[last - 1] and ec[last] > ec[last - 1] * 1.05:
        add("amber", "Share capital increased", f"Equity capital rose from Rs {ec[last-1]:,.2f} cr to Rs {ec[last]:,.2f} cr (new shares, bonus or a face-value change - check which).")
    opm = [x for x in y["opm"] if x is not None]
    if len(opm) >= 4 and opm[-1] < statistics.mean(opm[-4:-1]) - 5:
        add("amber", "Margins compressing", f"Operating margin {opm[-1]:.0f}% vs a {statistics.mean(opm[-4:-1]):.0f}% three-year average.")
    sy = [x for x in q["sales_yoy"] if x is not None]
    if sy and sy[-1] < -15:
        add("amber", "Latest quarter sales fell", f"Sales down {abs(sy[-1]):.0f}% year on year in {q['columns'][-1]}.")
    pc = d.get("promoter_chg_1y")
    if pc is not None and pc <= -3:
        add("amber", "Promoters selling", f"Promoter holding down {abs(pc):.1f} points in four quarters.")
    elif pc is not None and pc >= 1:
        add("green", "Promoters adding", f"Promoter holding up {pc:.1f} points in four quarters.")
    roce = y["roce"][last]
    if roce is not None and roce >= 20:
        add("green", "High returns on capital", f"ROCE {roce:.0f}% last year.")
    elif roce is not None and roce < 10:
        add("amber", "Low returns on capital", f"ROCE {roce:.0f}% last year.")
    pe = v.get("pe_ttm")
    if pe is not None and pe > 60:
        add("amber", "Rich valuation", f"{pe:.0f}x trailing earnings.", "[E]")
    dcf = v.get("dcf") or {}
    rev = (dcf.get("reverse_dcf") or {}).get("implied_5y_sales_cagr")
    if rev is not None and dcf.get("available"):
        base_eq = dcf["reverse_dcf"]["base_case_5y_cagr_equivalent"]
        if base_eq is not None and rev > base_eq + 15:
            add("red", "Price needs growth well above trend", f"Today's price implies about {rev:.0f}% a year sales growth for five years, against {base_eq:.0f}% in the base case.", "[E]")
    order = {"red": 0, "amber": 1, "green": 2}
    return sorted(flags, key=lambda f: order[f["level"]])


def build(c: dict) -> dict:
    y = statements(c)
    q = quarterly(c)
    sh = c["shareholding"]
    d = derived(y, q, sh)
    v = valuation(c, y, d)
    return {
        "basis": "consolidated" if c["consolidated"] else "standalone",
        "units": "Rs crore unless stated",
        "annual": y, "quarterly": q, "derived": d,
        "shareholding": {"columns": sh["columns"], "rows": sh["rows"]},
        "valuation": v,
        "red_flags": red_flags(y, q, d, v, sh),
        "latest_quarter": q["columns"][-1] if q["columns"] else None,
    }

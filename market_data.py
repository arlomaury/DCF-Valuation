"""
Market inputs for the cost of capital: industry betas and margins, default
spreads by synthetic rating, the equity risk premium, and the mapping from a
company's SEC industry code (SIC) to an industry.

Source for every table: Aswath Damodaran, NYU Stern, US data sets dated
January 2026 (pages.stern.nyu.edu/~adamodar).  He refreshes them every
January; update DATA_DATE and the tables below when he does.
"""
from __future__ import annotations

DATA_DATE = "January 2026"

# Implied equity risk premium for the S&P 500 at the start of 2026
# (Damodaran, "Data Update 2 for 2026"), against a 4.18% 10-year T-bond.
IMPLIED_ERP = 0.0423

# The same estimate's inputs, so it can be brought up to date the way
# Damodaran updates it each month: hold the index's expected cash flows, roll
# them forward, and re-solve for the premium at today's index level and
# today's Treasury yield. (The result barely depends on GROWTH_5Y: 4%-12%
# moves it by about 0.2 points.)
ERP_ANCHOR = {"date": "2026-01-01", "sp500": 6845.5, "rf": 0.0418, "erp": IMPLIED_ERP,
              "growth5": 0.08}


def _index_value(cf0, g, rf, r, years=5):
    """Value of the index: `years` of cash flows growing at g, then growing at
    the risk-free rate forever (Damodaran's two-stage implied-ERP model)."""
    v, cf = 0.0, cf0
    for t in range(1, years + 1):
        cf *= 1 + g
        v += cf / (1 + r) ** t
    return v + cf * (1 + rf) / (r - rf) / (1 + r) ** years


def _solve(f, lo, hi):
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def implied_erp_now(sp500, rf, years_since):
    """Implied ERP at today's S&P 500 level and risk-free rate, or None if
    the inputs are unusable or the answer falls outside a sane 2%-8% band."""
    a = ERP_ANCHOR
    if not (sp500 and sp500 > 0 and 0 < rf < 0.2 and 0 <= years_since < 2):
        return None
    g = a["growth5"]
    cf0 = _solve(lambda c: a["sp500"] - _index_value(c, g, a["rf"], a["rf"] + a["erp"]), 1e-6, a["sp500"])
    cf = cf0 * (1 + g) ** years_since
    if _index_value(cf, g, rf, rf + 0.5) > sp500:      # no solution in range
        return None
    r = _solve(lambda x: _index_value(cf, g, rf, x) - sp500, rf + 1e-4, rf + 0.5)
    erp = r - rf
    return erp if 0.02 <= erp <= 0.08 else None

# Long-run US marginal corporate tax rate (21% federal + state), the rate
# Damodaran uses for US firms.  Used for the debt tax shield, for re-levering
# beta, and as the tax rate the projection converges to.
MARGINAL_TAX_RATE = 0.25

# Fallback 10-year Treasury yield if the live Treasury / FRED fetch fails.
FALLBACK_RISK_FREE = {"rate": 0.0509, "date": "2026-09-30",
                      "source": "US Treasury 10-year constant-maturity yield (built-in fallback)"}

# industry -> (unlevered beta corrected for cash, pre-tax unadjusted operating
# margin - i.e. after stock-based compensation, matching reported EBIT)
INDUSTRIES = {
    "Advertising": (1.01, 0.1007),
    "Aerospace/Defense": (0.87, 0.0865),
    "Air Transport": (0.76, 0.0532),
    "Apparel": (0.79, 0.0911),
    "Auto & Truck": (1.31, 0.0232),
    "Auto Parts": (1.13, 0.0567),
    "Bank (Money Center)": (0.44, None),
    "Banks (Regional)": (0.37, None),
    "Beverage (Alcoholic)": (0.63, 0.2276),
    "Beverage (Soft)": (0.58, 0.2053),
    "Broadcasting": (0.32, 0.1233),
    "Brokerage & Investment Banking": (0.68, None),
    "Building Materials": (0.96, 0.1264),
    "Business & Consumer Services": (0.81, 0.1227),
    "Cable TV": (0.36, 0.1849),
    "Chemical (Basic)": (0.64, 0.0267),
    "Chemical (Diversified)": (0.41, 0.0329),
    "Chemical (Specialty)": (0.82, 0.1217),
    "Coal & Related Energy": (1.18, -0.0402),
    "Computer Services": (0.96, 0.0741),
    "Computers/Peripherals": (1.32, 0.2248),
    "Construction Supplies": (1.05, 0.1523),
    "Diversified": (0.84, 0.2273),
    "Drugs (Biotechnology)": (1.08, 0.0897),
    "Drugs (Pharmaceutical)": (0.92, 0.2954),
    "Education": (0.72, 0.1401),
    "Electrical Equipment": (1.19, 0.0953),
    "Electronics (Consumer & Office)": (0.93, -0.0449),
    "Electronics (General)": (0.94, 0.1042),
    "Engineering/Construction": (1.14, 0.0649),
    "Entertainment": (0.76, 0.1060),
    "Environmental & Waste Services": (0.82, 0.1461),
    "Farming/Agriculture": (0.85, 0.0545),
    "Financial Svcs. (Non-bank & Insurance)": (0.33, 0.1848),
    "Food Processing": (0.47, 0.1063),
    "Food Wholesalers": (0.65, 0.0261),
    "Furn/Home Furnishings": (0.65, 0.0659),
    "Green & Renewable Energy": (0.47, 0.1987),
    "Healthcare Products": (0.86, 0.1534),
    "Healthcare Support Services": (0.74, 0.0300),
    "Healthcare Information and Technology": (1.02, 0.1471),
    "Homebuilding": (0.85, 0.1257),
    "Hospitals/Healthcare Facilities": (0.56, 0.1336),
    "Hotel/Gaming": (0.88, 0.1939),
    "Household Products": (0.74, 0.1862),
    "Information Services": (0.76, 0.1189),
    "Insurance (General)": (0.58, 0.2131),
    "Insurance (Life)": (0.53, 0.1061),
    "Insurance (Prop/Cas.)": (0.46, 0.1525),
    "Investments & Asset Management": (0.59, 0.2550),
    "Machinery": (0.89, 0.1586),
    "Metals & Mining": (1.01, 0.2385),
    "Office Equipment & Services": (1.04, 0.1073),
    "Oil/Gas (Integrated)": (0.28, 0.1125),
    "Oil/Gas (Production and Exploration)": (0.58, 0.2542),
    "Oil/Gas Distribution": (0.47, 0.2578),
    "Oilfield Svcs/Equip.": (0.79, 0.0465),
    "Packaging & Container": (0.75, 0.0958),
    "Paper/Forest Products": (0.77, 0.0634),
    "Power": (0.31, 0.2147),
    "Precious Metals": (0.83, 0.4039),
    "Publishing & Newspapers": (0.51, 0.0998),
    "R.E.I.T.": (0.40, 0.2464),
    "Real Estate (Development)": (0.56, 0.2153),
    "Real Estate (General/Diversified)": (0.63, 0.2145),
    "Real Estate (Operations & Services)": (0.86, 0.0285),
    "Recreation": (0.74, 0.0969),
    "Reinsurance": (0.58, 0.0721),
    "Restaurant/Dining": (0.78, 0.1579),
    "Retail (Automotive)": (0.71, 0.0624),
    "Retail (Building Supply)": (1.32, 0.1194),
    "Retail (Distributors)": (0.80, 0.1010),
    "Retail (General)": (0.78, 0.0680),
    "Retail (Grocery and Food)": (0.85, 0.0229),
    "Retail (REITs)": (0.44, 0.4025),
    "Retail (Special Lines)": (1.00, 0.0773),
    "Rubber & Tires": (0.15, 0.0224),
    "Semiconductor": (1.50, 0.3533),
    "Semiconductor Equip": (1.39, 0.2617),
    "Shipbuilding & Marine": (0.66, 0.1260),
    "Shoe": (1.00, 0.0903),
    "Software (Entertainment)": (1.02, 0.3385),
    "Software (Internet)": (1.59, 0.0369),
    "Software (System & Application)": (1.25, 0.3298),
    "Steel": (0.94, 0.0410),
    "Telecom (Wireless)": (0.39, 0.2098),
    "Telecom. Equipment": (0.89, 0.2070),
    "Telecom. Services": (0.38, 0.2047),
    "Tobacco": (0.69, 0.4354),
    "Transportation": (0.71, 0.0757),
    "Transportation (Railroads)": (0.81, 0.3741),
    "Trucking": (0.87, 0.0689),
    "Utility (General)": (0.15, 0.2349),
    "Utility (Water)": (0.28, 0.3372),
    "Total Market (without financials)": (0.90, 0.1314),
}
DEFAULT_INDUSTRY = "Total Market (without financials)"

# Industries where a free-cash-flow-to-the-firm DCF is not meaningful: for
# banks and insurers debt is raw material, not financing.
FINANCIAL_INDUSTRIES = {
    "Bank (Money Center)", "Banks (Regional)", "Brokerage & Investment Banking",
    "Financial Svcs. (Non-bank & Insurance)", "Insurance (General)", "Insurance (Life)",
    "Insurance (Prop/Cas.)", "Investments & Asset Management", "Reinsurance",
}

# Synthetic rating from interest coverage (EBIT / interest expense).
# (coverage lower bound, rating, default spread over the risk-free rate)
# Large firms: market cap above $5 billion.
RATINGS_LARGE = [
    (8.50, "AAA", 0.0040), (6.50, "AA", 0.0055), (5.50, "A+", 0.0070),
    (4.25, "A", 0.0078), (3.00, "A-", 0.0089), (2.50, "BBB", 0.0111),
    (2.25, "BB+", 0.0138), (2.00, "BB", 0.0184), (1.75, "B+", 0.0275),
    (1.50, "B", 0.0321), (1.25, "B-", 0.0509), (0.80, "CCC", 0.0885),
    (0.65, "CC", 0.1261), (0.20, "C", 0.1600), (float("-inf"), "D", 0.1900),
]
# Smaller / riskier firms need more coverage for the same rating.
RATINGS_SMALL = [
    (12.5, "AAA", 0.0040), (9.5, "AA", 0.0055), (7.5, "A+", 0.0070),
    (6.0, "A", 0.0078), (4.5, "A-", 0.0089), (4.0, "BBB", 0.0111),
    (3.5, "BB+", 0.0138), (3.0, "BB", 0.0184), (2.5, "B+", 0.0275),
    (2.0, "B", 0.0321), (1.5, "B-", 0.0509), (1.25, "CCC", 0.0885),
    (0.8, "CC", 0.1261), (0.5, "C", 0.1600), (float("-inf"), "D", 0.1900),
]
LARGE_FIRM_CUTOFF = 5e9


def synthetic_rating(ebit, interest, market_cap):
    """(rating, spread, coverage) from interest coverage.

    No interest expense means no meaningful debt: treated as AAA."""
    if not interest or interest <= 0:
        return "AAA", 0.0040, None
    coverage = (ebit or 0) / interest
    table = RATINGS_LARGE if (market_cap or 0) >= LARGE_FIRM_CUTOFF else RATINGS_SMALL
    for lower, rating, spread in table:
        if coverage >= lower:
            return rating, spread, coverage
    return "D", 0.19, coverage


# SIC code -> industry.  Ranges are checked most-specific first.
# (low, high, industry) - inclusive.
_SIC_RANGES = [
    (100, 999, "Farming/Agriculture"),
    (1040, 1049, "Precious Metals"),
    (1000, 1099, "Metals & Mining"),
    (1200, 1299, "Coal & Related Energy"),
    (1311, 1311, "Oil/Gas (Production and Exploration)"),
    (1380, 1389, "Oilfield Svcs/Equip."),
    (1400, 1499, "Metals & Mining"),
    (1520, 1531, "Homebuilding"),
    (1500, 1799, "Engineering/Construction"),
    # 2080 is the generic "Beverages" code. Coca-Cola and PepsiCo file under
    # it, so it maps to soft drinks; brewers (2082), wineries (2084) and
    # distillers (2085) have their own codes. The industry can be changed in
    # the app for the few alcohol companies that also use 2080.
    (2080, 2080, "Beverage (Soft)"),
    (2082, 2085, "Beverage (Alcoholic)"),
    (2086, 2087, "Beverage (Soft)"),
    (2000, 2099, "Food Processing"),
    (2100, 2199, "Tobacco"),
    (2200, 2399, "Apparel"),
    (2400, 2499, "Paper/Forest Products"),
    (2500, 2599, "Furn/Home Furnishings"),
    (2670, 2670, "Diversified"),          # converted paper: 3M files here
    (2650, 2679, "Packaging & Container"),
    (2600, 2699, "Paper/Forest Products"),
    (2700, 2799, "Publishing & Newspapers"),
    (2836, 2836, "Drugs (Biotechnology)"),
    (2830, 2835, "Drugs (Pharmaceutical)"),
    (2840, 2844, "Household Products"),
    (2800, 2829, "Chemical (Basic)"),
    (2850, 2899, "Chemical (Specialty)"),
    (2900, 2999, "Oil/Gas (Integrated)"),
    (3010, 3011, "Rubber & Tires"),
    (3021, 3021, "Shoe"),                 # rubber & plastic footwear: Nike
    (3000, 3099, "Chemical (Specialty)"),
    (3140, 3149, "Shoe"),
    (3100, 3199, "Apparel"),
    (3240, 3241, "Construction Supplies"),
    (3200, 3299, "Building Materials"),
    (3310, 3317, "Steel"),
    (3300, 3399, "Metals & Mining"),
    (3400, 3499, "Machinery"),
    (3576, 3576, "Telecom. Equipment"),
    (3570, 3579, "Computers/Peripherals"),
    (3500, 3599, "Machinery"),
    (3630, 3639, "Electronics (Consumer & Office)"),
    (3651, 3652, "Electronics (Consumer & Office)"),
    (3660, 3669, "Telecom. Equipment"),
    (3674, 3674, "Semiconductor"),
    (3670, 3679, "Electronics (General)"),
    (3600, 3699, "Electrical Equipment"),
    (3714, 3714, "Auto Parts"),
    (3710, 3716, "Auto & Truck"),
    (3720, 3729, "Aerospace/Defense"),
    (3730, 3732, "Shipbuilding & Marine"),
    (3760, 3769, "Aerospace/Defense"),
    (3790, 3799, "Recreation"),
    (3700, 3799, "Machinery"),
    (3812, 3812, "Aerospace/Defense"),
    (3820, 3829, "Electronics (General)"),
    (3840, 3851, "Healthcare Products"),
    (3861, 3861, "Office Equipment & Services"),
    (3940, 3949, "Recreation"),
    (3900, 3999, "Diversified"),
    (4000, 4099, "Transportation (Railroads)"),
    (4100, 4199, "Transportation"),
    (4210, 4210, "Transportation"),       # trucking & courier: UPS
    (4215, 4215, "Transportation"),       # courier services
    (4200, 4299, "Trucking"),
    (4400, 4499, "Shipbuilding & Marine"),
    (4500, 4599, "Air Transport"),
    (4600, 4699, "Oil/Gas Distribution"),
    (4700, 4799, "Transportation"),
    (4812, 4812, "Telecom (Wireless)"),
    (4830, 4833, "Broadcasting"),
    (4840, 4841, "Cable TV"),
    (4800, 4899, "Telecom. Services"),
    (4911, 4911, "Power"),
    (4920, 4925, "Oil/Gas Distribution"),
    (4941, 4941, "Utility (Water)"),
    (4950, 4959, "Environmental & Waste Services"),
    (4991, 4991, "Green & Renewable Energy"),
    (4900, 4999, "Utility (General)"),
    (5122, 5122, "Healthcare Support Services"),
    (5140, 5149, "Food Wholesalers"),
    (5000, 5199, "Retail (Distributors)"),
    (5200, 5299, "Retail (Building Supply)"),
    (5300, 5399, "Retail (General)"),
    (5400, 5499, "Retail (Grocery and Food)"),
    (5500, 5599, "Retail (Automotive)"),
    (5800, 5899, "Restaurant/Dining"),
    (5961, 5961, "Retail (General)"),
    (5600, 5999, "Retail (Special Lines)"),
    (6020, 6029, "Banks (Regional)"),
    (6000, 6099, "Banks (Regional)"),
    (6100, 6199, "Financial Svcs. (Non-bank & Insurance)"),
    (6282, 6282, "Investments & Asset Management"),
    (6200, 6299, "Brokerage & Investment Banking"),
    (6311, 6311, "Insurance (Life)"),
    (6321, 6324, "Healthcare Support Services"),
    (6331, 6331, "Insurance (Prop/Cas.)"),
    (6300, 6411, "Insurance (General)"),
    (6798, 6798, "R.E.I.T."),
    (6500, 6599, "Real Estate (Operations & Services)"),
    (6770, 6770, "Financial Svcs. (Non-bank & Insurance)"),
    (6700, 6799, "Investments & Asset Management"),
    (7000, 7099, "Hotel/Gaming"),
    (7310, 7319, "Advertising"),
    (7370, 7370, "Software (Internet)"),
    (7372, 7372, "Software (System & Application)"),
    (7371, 7379, "Computer Services"),
    (7200, 7399, "Business & Consumer Services"),
    (7500, 7599, "Business & Consumer Services"),
    (7800, 7899, "Entertainment"),
    (7900, 7999, "Recreation"),
    (8050, 8069, "Hospitals/Healthcare Facilities"),
    (8000, 8099, "Healthcare Support Services"),
    (8200, 8299, "Education"),
    (8731, 8731, "Drugs (Biotechnology)"),
    (8711, 8711, "Engineering/Construction"),
    (8700, 8799, "Business & Consumer Services"),
]


def industry_for_sic(sic):
    """Map a 4-digit SIC code to an industry name (or the market default)."""
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return DEFAULT_INDUSTRY
    for low, high, name in _SIC_RANGES:
        if low <= code <= high:
            return name
    return DEFAULT_INDUSTRY


# Industries whose margins swing with a commodity price or the economic
# cycle. For these, one year's margin is a point on the cycle, not the
# business: valuation practice (McKinsey's "Valuation", ch. on cyclical
# companies; Damodaran's normalized earnings) uses the average across a full
# cycle instead. Semiconductors are left out on purpose: their swings sit on
# top of a secular shift that a six-year average would erase.
CYCLICAL_INDUSTRIES = {
    "Oil/Gas (Integrated)", "Oil/Gas (Production and Exploration)", "Oilfield Svcs/Equip.",
    "Coal & Related Energy", "Metals & Mining", "Precious Metals", "Steel",
    "Chemical (Basic)", "Paper/Forest Products", "Homebuilding", "Building Materials",
    "Auto & Truck", "Auto Parts", "Rubber & Tires", "Air Transport", "Machinery",
    "Trucking", "Shipbuilding & Marine",
}


def industry_table():
    return [{"name": k, "unleveredBeta": b, "operatingMargin": m, "cyclical": k in CYCLICAL_INDUSTRIES}
            for k, (b, m) in INDUSTRIES.items()]

"""NSE symbol → corporate website domain mapping for logo lookups.

Used by the front-end avatar component to fetch real company logos
(via logo.clearbit.com / similar). The gradient initials avatar is the
fallback whenever a domain is missing or the logo CDN doesn't have it.

Only domains we're confident about are listed — for the rest the front-end
gracefully falls back to gradient initials.
"""

SYMBOL_DOMAIN_MAP = {
    # FMCG
    "HINDUNILVR": "hul.co.in",
    "ITC": "itcportal.com",
    "NESTLEIND": "nestle.in",
    "BRITANNIA": "britannia.co.in",
    "DABUR": "dabur.com",

    # Banking
    "HDFCBANK": "hdfcbank.com",
    "ICICIBANK": "icicibank.com",
    "SBIN": "sbi.co.in",
    "KOTAKBANK": "kotak.com",
    "AXISBANK": "axisbank.com",

    # IT
    "TCS": "tcs.com",
    "INFY": "infosys.com",
    "HCLTECH": "hcltech.com",
    "WIPRO": "wipro.com",
    "TECHM": "techmahindra.com",

    # Pharma
    "SUNPHARMA": "sunpharma.com",
    "DIVISLAB": "divislabs.com",
    "CIPLA": "cipla.com",
    "DRREDDY": "drreddys.com",
    "LUPIN": "lupin.com",

    # Steel / Metals
    "TATASTEEL": "tatasteel.com",
    "JSWSTEEL": "jsw.in",
    "JINDALSTEL": "jindalsteelpower.com",
    "SAIL": "sail.co.in",
    "JSL": "jindalstainless.com",
    "HINDALCO": "hindalco.com",
    "VEDL": "vedantalimited.com",
    "NMDC": "nmdc.co.in",
    "NATIONALUM": "nalcoindia.com",

    # Auto
    "MARUTI": "marutisuzuki.com",
    "M&M": "mahindra.com",
    "TATAMOTORS": "tatamotors.com",
    "BAJAJ-AUTO": "bajajauto.com",
    "EICHERMOT": "eicher.in",

    # Energy
    "RELIANCE": "ril.com",
    "ONGC": "ongcindia.com",
    "COALINDIA": "coalindia.in",
    "IOC": "iocl.com",
    "BPCL": "bharatpetroleum.in",

    # Power
    "NTPC": "ntpc.co.in",
    "POWERGRID": "powergrid.in",
    "ADANIPOWER": "adanipower.com",
    "TATAPOWER": "tatapower.com",
    "JSWENERGY": "jsw.in",

    # Cement
    "ULTRACEMCO": "ultratechcement.com",
    "AMBUJACEM": "ambujacement.com",
    "SHREECEM": "shreecement.com",
    "ACC": "acclimited.com",
    "DALBHARAT": "dalmiabharat.com",
    "JKCEMENT": "jkcement.com",
    "RAMCOCEM": "ramcocements.com",
    "NUVOCO": "nuvoco.com",
    "INDIACEM": "indiacements.co.in",
    "BIRLACORPN": "birlacorporation.com",

    # Telecom
    "BHARTIARTL": "airtel.in",
    "IDEA": "myvi.in",
    "INDUSTOWER": "industowers.com",
    "TATACOMM": "tatacommunications.com",
    "HFCL": "hfcl.com",
    "ROUTE": "routemobile.com",
    "TANLA": "tanla.com",
    "RAILTEL": "railtelindia.com",

    # Real Estate
    "DLF": "dlf.in",
    "LODHA": "lodhagroup.in",
    "GODREJPROP": "godrejproperties.com",
    "OBEROIRLTY": "oberoirealty.com",
    "PRESTIGE": "prestigeconstructions.com",

    # Finance / NBFC
    "BAJFINANCE": "bajajfinserv.in",
    "BAJAJFINSV": "bajajfinserv.in",
    "CHOLAFIN": "cholamandalam.com",
    "SHRIRAMFIN": "shriramfinance.in",
    "MUTHOOTFIN": "muthootfinance.com",

    # Insurance
    "HDFCLIFE": "hdfclife.com",
    "SBILIFE": "sbilife.co.in",
    "ICICIGI": "icicilombard.com",
    "ICICIPRULI": "iciciprulife.com",
    "LICI": "licindia.in",

    # Consumer Durables
    "TITAN": "titancompany.in",
    "HAVELLS": "havells.com",
    "VOLTAS": "voltas.com",
    "CROMPTON": "crompton.co.in",
    "BLUESTARCO": "bluestarindia.com",

    # Chemicals
    "PIDILITIND": "pidilite.com",
    "SRF": "srf.com",
    "UPL": "upl-ltd.com",
    "PIIND": "piindustries.com",
    "SOLARINDS": "solarindustries.com",
    "AARTIIND": "aarti-industries.com",
    "DEEPAKNTR": "godeepak.com",
    "ATUL": "atul.co.in",
    "NAVINFLUOR": "nfil.in",
    "COROMANDEL": "coromandel.biz",
    "CHAMBLFERT": "chambalfertilisers.com",
    "TATACHEM": "tatachemicals.com",
    "ALKYLAMINE": "alkylamines.com",
    "CLEAN": "cleanscience.co.in",
    "FINEORG": "fineorganics.com",
    "GNFC": "gnfc.in",
    "VINATIORGA": "vinatiorganics.com",
    "SUMICHEM": "sumichem.co.in",
    "BAYERCROP": "bayer.in",
    "LINDEINDIA": "lindeindia.com",
}


def logo_url(symbol: str) -> str:
    """Return a logo CDN URL for a symbol, or empty string if unmapped."""
    domain = SYMBOL_DOMAIN_MAP.get(symbol)
    if not domain:
        return ""
    return f"https://logo.clearbit.com/{domain}"


# TradingView hosts vector logos for every listed company at
# https://s3-symbol-logo.tradingview.com/<slug>--big.svg
# The slug is the lowercase hyphenated full company name. This is the
# most reliable free source for Indian company logos in good quality.
SYMBOL_TV_SLUG_MAP = {
    # FMCG
    "HINDUNILVR": "hindustan-unilever",
    "ITC": "itc",
    "NESTLEIND": "nestle-india",
    "BRITANNIA": "britannia-industries",
    "DABUR": "dabur-india",

    # Banking
    "HDFCBANK": "hdfc-bank",
    "ICICIBANK": "icici-bank",
    "SBIN": "state-bank-of-india",
    "KOTAKBANK": "kotak-mahindra-bank",
    "AXISBANK": "axis-bank",

    # IT
    "TCS": "tata-consultancy-services",
    "INFY": "infosys",
    "HCLTECH": "hcl-technologies",
    "WIPRO": "wipro",
    "TECHM": "tech-mahindra",

    # Pharma
    "SUNPHARMA": "sun-pharmaceutical-industries",
    "DIVISLAB": "divis-laboratories",
    "CIPLA": "cipla",
    "DRREDDY": "drreddys-laboratories",
    "LUPIN": "lupin",

    # Steel / Metals
    "TATASTEEL": "tata-steel",
    "JSWSTEEL": "jsw-steel",
    "JINDALSTEL": "jindal-steel-and-power",
    "SAIL": "steel-authority-of-india",
    "JSL": "jindal-stainless",
    "HINDALCO": "hindalco-industries",
    "VEDL": "vedanta",
    "NMDC": "nmdc",
    "NATIONALUM": "national-aluminium-company",

    # Auto
    "MARUTI": "maruti-suzuki-india",
    "M&M": "mahindra-and-mahindra",
    "TATAMOTORS": "tata-motors",
    "BAJAJ-AUTO": "bajaj-auto",
    "EICHERMOT": "eicher-motors",

    # Energy
    "RELIANCE": "reliance-industries",
    "ONGC": "oil-and-natural-gas-corporation",
    "COALINDIA": "coal-india",
    "IOC": "indian-oil-corporation",
    "BPCL": "bharat-petroleum-corporation",

    # Power
    "NTPC": "ntpc",
    "POWERGRID": "power-grid-corporation-of-india",
    "ADANIPOWER": "adani-power",
    "TATAPOWER": "tata-power-company",
    "JSWENERGY": "jsw-energy",

    # Cement
    "ULTRACEMCO": "ultratech-cement",
    "AMBUJACEM": "ambuja-cements",
    "SHREECEM": "shree-cement",
    "ACC": "acc",
    "DALBHARAT": "dalmia-bharat",
    "JKCEMENT": "jk-cement",
    "RAMCOCEM": "the-ramco-cements",
    "NUVOCO": "nuvoco-vistas-corporation",
    "INDIACEM": "the-india-cements",
    "BIRLACORPN": "birla-corporation",

    # Telecom
    "BHARTIARTL": "bharti-airtel",
    "IDEA": "vodafone-idea",
    "INDUSTOWER": "indus-towers",
    "TATACOMM": "tata-communications",
    "HFCL": "hfcl",
    "ROUTE": "route-mobile",
    "TANLA": "tanla-platforms",
    "RAILTEL": "railtel-corporation-of-india",

    # Real Estate
    "DLF": "dlf",
    "LODHA": "macrotech-developers",
    "GODREJPROP": "godrej-properties",
    "OBEROIRLTY": "oberoi-realty",
    "PRESTIGE": "prestige-estates-projects",

    # Finance / NBFC
    "BAJFINANCE": "bajaj-finance",
    "BAJAJFINSV": "bajaj-finserv",
    "CHOLAFIN": "cholamandalam-investment-and-finance-company",
    "SHRIRAMFIN": "shriram-finance",
    "MUTHOOTFIN": "muthoot-finance",

    # Insurance
    "HDFCLIFE": "hdfc-life-insurance-company",
    "SBILIFE": "sbi-life-insurance-company",
    "ICICIGI": "icici-lombard-general-insurance-company",
    "ICICIPRULI": "icici-prudential-life-insurance-company",
    "LICI": "life-insurance-corporation-of-india",

    # Consumer Durables
    "TITAN": "titan-company",
    "HAVELLS": "havells-india",
    "VOLTAS": "voltas",
    "CROMPTON": "crompton-greaves-consumer-electricals",
    "BLUESTARCO": "blue-star",

    # Chemicals
    "PIDILITIND": "pidilite-industries",
    "SRF": "srf",
    "UPL": "upl",
    "PIIND": "pi-industries",
    "SOLARINDS": "solar-industries-india",
    "AARTIIND": "aarti-industries",
    "DEEPAKNTR": "deepak-nitrite",
    "ATUL": "atul",
    "NAVINFLUOR": "navin-fluorine-international",
    "COROMANDEL": "coromandel-international",
    "CHAMBLFERT": "chambal-fertilizers-and-chemicals",
    "TATACHEM": "tata-chemicals",
    "ALKYLAMINE": "alkyl-amines-chemicals",
    "CLEAN": "clean-science-and-technology",
    "FINEORG": "fine-organic-industries",
    "GNFC": "gujarat-narmada-valley-fertilizers-and-chemicals",
    "VINATIORGA": "vinati-organics",
    "SUMICHEM": "sumitomo-chemical-india",
    "BAYERCROP": "bayer-cropscience",
    "LINDEINDIA": "linde-india",
}


def tradingview_logo_url(symbol: str) -> str:
    slug = SYMBOL_TV_SLUG_MAP.get(symbol)
    if not slug:
        return ""
    return f"https://s3-symbol-logo.tradingview.com/{slug}--big.svg"


# Manual overrides — when the scraped logo is wrong (a factory photo, a tiny
# favicon, a generic icon) we point here directly. Wins over scraping +
# every fallback in the cascade. Add a symbol → URL row as you spot bad ones
# on /admin/logos.
SYMBOL_LOGO_OVERRIDES = {
    # PIDILITIND: scraped apple-touch-icon is a tiny sun-over-water icon — use TV's vector mark instead
    "PIDILITIND": "https://s3-symbol-logo.tradingview.com/pidilite-industries--big.svg",
    # AARTIIND: og:image was a factory hero photo — use TV's brand mark
    "AARTIIND":   "https://s3-symbol-logo.tradingview.com/aarti-industries--big.svg",
}

"""Industry-level intelligence — curated profiles + live news + AI weekly briefing.

For each of our 16 supported industries this module returns:
  - A curated profile: overview, market structure, top players (with promoter
    group), regulators, key drivers, key risks. Hand-written, conservative —
    only facts that are well-established in public reporting.
  - Live news in four buckets: sector, M&A / deals, regulatory / policy, global.
  - An AI-generated weekly briefing tying the news together (Gemini, free tier).

Cached per-industry for 30 minutes so the page loads instantly on revisit and
we don't hammer Google News / Gemini.
"""

import html
import os
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import requests


_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_INTEL_CACHE: Dict[str, Tuple[float, Dict]] = {}
_INTEL_TTL_SEC = 30 * 60

_FREE_TIER_MODELS = {"gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"}
_DEFAULT_MODEL = "gemini-2.5-flash"


# ── Curated industry profiles ────────────────────────────────────────────
# Hand-written. Kept conservative — promoter groups, regulator names, and
# structural descriptions are the kind of facts that don't drift week to week.
# Live numbers (market share, price, M&A) come from the news feed below, not
# from this dict.

INDUSTRY_PROFILES: Dict[str, Dict] = {
    "FMCG": {
        "tagline": "Fast-moving consumer goods — packaged foods, personal care, household products.",
        "overview": (
            "India's FMCG sector is the fourth-largest in the economy. Demand splits "
            "roughly evenly between urban and rural India, so rural wage recovery and "
            "monsoon trends are key swing factors. Distribution depth — kirana, modern "
            "trade, e-commerce, and increasingly quick-commerce — is the primary moat."
        ),
        "structure": "Concentrated at the top — five listed players dominate shelf share across most categories, with rising D2C and private-label pressure at the value end.",
        "leaders": [
            {"symbol": "HINDUNILVR", "name": "Hindustan Unilever", "promoter": "Unilever PLC (UK)", "note": "Soaps, detergents, personal care, foods"},
            {"symbol": "ITC", "name": "ITC Ltd", "promoter": "Diversified institutional; BAT residual stake", "note": "Cigarettes, foods, paperboards, hotels"},
            {"symbol": "NESTLEIND", "name": "Nestlé India", "promoter": "Nestlé S.A. (Switzerland)", "note": "Maggi, Nescafe, dairy"},
            {"symbol": "BRITANNIA", "name": "Britannia Industries", "promoter": "Wadia group", "note": "Biscuits and dairy"},
            {"symbol": "DABUR", "name": "Dabur India", "promoter": "Burman family", "note": "Ayurvedic / herbal personal care, foods"},
        ],
        "regulators": ["FSSAI", "Ministry of Consumer Affairs", "CCI", "Legal Metrology"],
        "drivers": ["Rural consumption recovery", "Premiumization in urban", "Quick-commerce expansion", "Raw material costs (palm oil, milk, crude derivatives)"],
        "risks": ["Erratic monsoon", "Crude / palm-oil spikes hitting margins", "D2C and private-label encroachment", "Inflation eroding mass-segment volumes"],
    },
    "Banking": {
        "tagline": "Scheduled commercial banks — private, public-sector, and small finance.",
        "overview": (
            "Indian banking is split between private banks (deposit-rich, retail-led) "
            "and PSU banks (large branch network, higher exposure to corporate / "
            "infrastructure). Net interest margin and credit-deposit ratio are the "
            "cycle indicators investors track most closely; RBI policy moves directly "
            "shape the read-through."
        ),
        "structure": "Top-heavy private bank cluster (HDFC, ICICI, Kotak, Axis) plus dominant SBI on the PSU side.",
        "leaders": [
            {"symbol": "HDFCBANK", "name": "HDFC Bank", "promoter": "Diversified institutional (post HDFC Ltd merger)", "note": "Largest private bank by assets"},
            {"symbol": "ICICIBANK", "name": "ICICI Bank", "promoter": "Diversified institutional", "note": "Retail and corporate; tech-forward"},
            {"symbol": "SBIN", "name": "State Bank of India", "promoter": "Government of India (~57%)", "note": "Largest bank by branches and deposits"},
            {"symbol": "KOTAKBANK", "name": "Kotak Mahindra Bank", "promoter": "Uday Kotak family", "note": "Premium retail, wealth management"},
            {"symbol": "AXISBANK", "name": "Axis Bank", "promoter": "SUUTI / LIC / GIC institutional anchors", "note": "Mid-corporate and retail"},
        ],
        "regulators": ["RBI", "SEBI (for listed entities)", "DICGC", "Ministry of Finance"],
        "drivers": ["Repo rate trajectory", "Credit growth (retail, MSME, corporate)", "Asset quality / GNPA", "Deposit competition"],
        "risks": ["NIM compression in rate-cut cycles", "Unsecured retail credit stress", "Corporate slippage cycles", "Regulatory tightening on capital / provisioning"],
    },
    "IT": {
        "tagline": "IT services and software — global delivery from India.",
        "overview": (
            "Indian IT services derive ~80% of revenue from North America and Europe, "
            "with BFSI as the largest vertical. Growth is now driven less by labour "
            "arbitrage and more by digital transformation, cloud migration, and "
            "GenAI-led productivity. USD/INR, US discretionary IT spend, and visa "
            "policy are the dominant macro variables."
        ),
        "structure": "Tier-1 oligopoly (TCS, Infosys, HCL, Wipro, Tech Mahindra) plus fast-scaling mid-tier (LTIMindtree, Persistent, Coforge).",
        "leaders": [
            {"symbol": "TCS", "name": "Tata Consultancy Services", "promoter": "Tata Sons", "note": "Largest IT services by revenue; BFSI-heavy"},
            {"symbol": "INFY", "name": "Infosys", "promoter": "Diversified institutional (founder stake reduced)", "note": "Digital, cloud, ER&D"},
            {"symbol": "HCLTECH", "name": "HCL Technologies", "promoter": "Shiv Nadar family", "note": "Engineering, products, infra services"},
            {"symbol": "WIPRO", "name": "Wipro", "promoter": "Azim Premji family / trusts", "note": "Consulting + services"},
            {"symbol": "TECHM", "name": "Tech Mahindra", "promoter": "Mahindra group", "note": "Telecom-heavy mix; CME vertical"},
        ],
        "regulators": ["MeitY", "STPI", "SEBI", "RBI (for SEZ / forex)"],
        "drivers": ["US BFSI tech spend", "GenAI deal pipelines", "USD/INR", "H-1B and US immigration policy", "EU GCC build-outs"],
        "risks": ["US recession / discretionary cuts", "Pricing pressure from GenAI deflation", "Margin pressure from wage inflation", "Visa restrictions"],
    },
    "Pharma": {
        "tagline": "Generics, formulations, APIs, and specialty pharmaceuticals.",
        "overview": (
            "Indian pharma is the world's largest supplier of generic medicines by "
            "volume. Revenue mix splits between regulated markets (US, EU — high "
            "margin, USFDA-driven) and emerging markets / domestic formulations. "
            "USFDA inspection outcomes, generic price erosion in the US, and complex "
            "generic / biosimilar pipelines drive multi-quarter direction."
        ),
        "structure": "Fragmented — top 10 players hold ~40% of listed market cap; CDMOs (Divis, Laurus) and specialty (Sun, DRL, Cipla) operate parallel models.",
        "leaders": [
            {"symbol": "SUNPHARMA", "name": "Sun Pharmaceutical", "promoter": "Dilip Shanghvi family", "note": "Largest by market cap; specialty derma"},
            {"symbol": "DIVISLAB", "name": "Divi's Laboratories", "promoter": "Murali Divi family", "note": "API and CDMO"},
            {"symbol": "CIPLA", "name": "Cipla", "promoter": "Hamied family", "note": "Inhalation, US generics, India branded"},
            {"symbol": "DRREDDY", "name": "Dr. Reddy's Laboratories", "promoter": "Reddy family", "note": "US generics, biosimilars, OTC"},
            {"symbol": "LUPIN", "name": "Lupin", "promoter": "Gupta family", "note": "US complex generics, respiratory"},
        ],
        "regulators": ["CDSCO (India)", "USFDA (US exports)", "EMA (EU)", "NPPA (price control)", "DCGI"],
        "drivers": ["USFDA approvals on complex generics / biosimilars", "Price stability in US generics", "Domestic chronic-therapy growth", "API self-reliance (PLI)"],
        "risks": ["USFDA Form 483 / warning letters / import alerts", "US generic price erosion", "China API supply shocks", "NPPA price control extensions"],
    },
    "Steel": {
        "tagline": "Crude steel, flat & long products, and downstream rolled steel.",
        "overview": (
            "India is the world's second-largest steel producer. The cycle tracks "
            "global steel prices (anchored to China demand) and domestic infrastructure "
            "/ auto demand. Iron ore and coking coal — both partially imported — set "
            "the cost floor; safeguard duties and Chinese export quotas swing the price ceiling."
        ),
        "structure": "Oligopoly — Tata Steel, JSW Steel, JSPL, SAIL together hold the bulk of organised capacity.",
        "leaders": [
            {"symbol": "TATASTEEL", "name": "Tata Steel", "promoter": "Tata Sons", "note": "India + Europe operations"},
            {"symbol": "JSWSTEEL", "name": "JSW Steel", "promoter": "Sajjan Jindal family", "note": "Largest private steelmaker by capacity in India"},
            {"symbol": "JINDALSTEL", "name": "Jindal Steel & Power", "promoter": "Naveen Jindal family", "note": "Long products, captive power"},
            {"symbol": "SAIL", "name": "Steel Authority of India", "promoter": "Government of India", "note": "Integrated PSU; flat and long products"},
            {"symbol": "JSL", "name": "Jindal Stainless", "promoter": "Ratan Jindal family", "note": "Largest stainless steel producer in India"},
        ],
        "regulators": ["Ministry of Steel", "DGFT", "DGTR (anti-dumping)", "MoEFCC (environment)"],
        "drivers": ["Chinese steel exports / demand", "Domestic infra and auto demand", "Iron ore and coking coal prices", "Safeguard / anti-dumping duties"],
        "risks": ["Chinese dumping cycles", "Coking coal spikes", "Carbon-border adjustments (EU CBAM)", "Mining / environment clearances"],
    },
    "Auto": {
        "tagline": "Passenger vehicles, two-wheelers, commercial vehicles, and tractors.",
        "overview": (
            "The Indian auto market is the third-largest globally by units. Two-wheelers "
            "dominate volumes; PVs are increasingly SUV-led. EV adoption is growing "
            "fastest in 2W/3W segments, while PV EV penetration remains low but rising. "
            "Tractor demand tracks rural / monsoon cycles."
        ),
        "structure": "Segment-wise oligopolies — Maruti dominates entry PVs, M&M leads SUVs and tractors, Tata Motors is the EV leader, Bajaj/Hero/TVS split 2Ws.",
        "leaders": [
            {"symbol": "MARUTI", "name": "Maruti Suzuki", "promoter": "Suzuki Motor Corp (Japan)", "note": "Largest PV maker by units"},
            {"symbol": "M&M", "name": "Mahindra & Mahindra", "promoter": "Mahindra family / group", "note": "SUVs, tractors, commercial"},
            {"symbol": "TATAMOTORS", "name": "Tata Motors", "promoter": "Tata Sons", "note": "JLR + India PV (incl. EVs) + CV"},
            {"symbol": "BAJAJ-AUTO", "name": "Bajaj Auto", "promoter": "Bajaj family", "note": "2W and 3W; export-led"},
            {"symbol": "EICHERMOT", "name": "Eicher Motors", "promoter": "Lal family", "note": "Royal Enfield motorcycles + VECV trucks"},
        ],
        "regulators": ["Ministry of Heavy Industries", "MoRTH", "ARAI", "BEE (fuel norms)", "MoEFCC"],
        "drivers": ["Rural recovery (2W and tractors)", "SUV mix and premiumization", "EV adoption curve", "FAME / state EV subsidies", "Steel and aluminium prices"],
        "risks": ["Semiconductor and rare-earth supply", "Stricter emission norms (BS-VII / CAFE)", "Slowdown in JLR (Tata Motors)", "Commodity inflation"],
    },
    "Energy": {
        "tagline": "Oil & gas — exploration, refining, marketing, and city gas.",
        "overview": (
            "India is the world's third-largest oil consumer and ~85% import-dependent "
            "for crude. Refining is dominated by Reliance and the OMC trio (IOC, BPCL, "
            "HPCL); upstream is led by ONGC and OIL. Marketing margins are politically "
            "sensitive ahead of elections and around crude spikes."
        ),
        "structure": "PSU-dominated refining/marketing + private major (Reliance) + LNG/CGD specialists (PLNG, GAIL, IGL, GUJGAS).",
        "leaders": [
            {"symbol": "RELIANCE", "name": "Reliance Industries", "promoter": "Mukesh Ambani family", "note": "Largest refiner; also retail and Jio"},
            {"symbol": "ONGC", "name": "Oil & Natural Gas Corp", "promoter": "Government of India", "note": "Largest upstream"},
            {"symbol": "COALINDIA", "name": "Coal India", "promoter": "Government of India", "note": "Dominant coal producer"},
            {"symbol": "IOC", "name": "Indian Oil Corporation", "promoter": "Government of India", "note": "Largest fuel retailer"},
            {"symbol": "BPCL", "name": "Bharat Petroleum", "promoter": "Government of India", "note": "Refining + marketing"},
        ],
        "regulators": ["MoPNG", "PNGRB", "DGH (upstream)", "PPAC", "CCI"],
        "drivers": ["Brent crude price", "Refining (GRM) and marketing margins", "Rupee level (import bill)", "City gas / EV penetration on demand"],
        "risks": ["Crude spikes squeezing OMC marketing margins", "Windfall tax on upstream", "EV / renewables demand erosion", "Geopolitics (Russia, Middle East)"],
    },
    "Power": {
        "tagline": "Power generation, transmission, and distribution.",
        "overview": (
            "India's power sector is rebalancing from coal-led generation toward "
            "renewables (solar, wind, hybrid) while keeping thermal as base load. "
            "Transmission (PowerGrid) is largely PSU; distribution discoms remain the "
            "weak link financially. Renewable capex and PPA tariff trends drive equity "
            "performance."
        ),
        "structure": "PSU base (NTPC, PowerGrid, NHPC) + private renewables and integrated players (Adani Power, Tata Power, JSW Energy).",
        "leaders": [
            {"symbol": "NTPC", "name": "NTPC", "promoter": "Government of India", "note": "Largest generator (thermal + scaling renewables)"},
            {"symbol": "POWERGRID", "name": "Power Grid Corporation", "promoter": "Government of India", "note": "Inter-state transmission monopoly"},
            {"symbol": "ADANIPOWER", "name": "Adani Power", "promoter": "Adani family", "note": "Largest private thermal IPP"},
            {"symbol": "TATAPOWER", "name": "Tata Power", "promoter": "Tata Sons", "note": "Integrated — generation, T&D, renewables, EV charging"},
            {"symbol": "JSWENERGY", "name": "JSW Energy", "promoter": "Sajjan Jindal family", "note": "Hydro + thermal + renewables"},
        ],
        "regulators": ["CERC", "SERCs", "Ministry of Power", "MNRE (renewables)", "POSOCO / Grid-India"],
        "drivers": ["Peak demand growth", "Renewable capex pipeline", "Coal price / availability", "Discom payment cycles", "PLI for solar manufacturing"],
        "risks": ["Discom payment delays", "Land / transmission permitting for renewables", "Coal supply shocks", "Tariff renegotiation risk"],
    },
    "Cement": {
        "tagline": "Cement and clinker manufacturers.",
        "overview": (
            "India is the second-largest cement market globally. Demand is split "
            "roughly evenly between housing, infrastructure, and commercial. The market "
            "has consolidated sharply in recent years — UltraTech, Adani's Ambuja-ACC, "
            "Shree, and Dalmia together control most listed capacity. Pricing is "
            "regional and cycle-sensitive."
        ),
        "structure": "Consolidating oligopoly — top 4 players hold ~60%+ of capacity; ongoing M&A is reshaping regional shares.",
        "leaders": [
            {"symbol": "ULTRACEMCO", "name": "UltraTech Cement", "promoter": "Aditya Birla group", "note": "Largest by capacity in India"},
            {"symbol": "AMBUJACEM", "name": "Ambuja Cements", "promoter": "Adani group (acquired from Holcim, 2022)", "note": "Pan-India"},
            {"symbol": "SHREECEM", "name": "Shree Cement", "promoter": "Bangur family", "note": "North/East-heavy, low-cost producer"},
            {"symbol": "ACC", "name": "ACC Ltd", "promoter": "Adani group (via Ambuja)", "note": "One of the oldest cement brands in India"},
            {"symbol": "DALBHARAT", "name": "Dalmia Bharat", "promoter": "Dalmia family", "note": "South & East focus"},
        ],
        "regulators": ["CCI", "BIS (standards)", "MoEFCC", "Ministry of Mines"],
        "drivers": ["Real-estate and infra capex cycle", "Pet coke / coal / power costs", "Regional supply-demand balance", "Government housing schemes"],
        "risks": ["Petcoke / coal price spikes", "Overcapacity and price wars", "CCI scrutiny on coordinated pricing", "Slow infra execution"],
    },
    "Telecom": {
        "tagline": "Telecom services, towers, and equipment.",
        "overview": (
            "Indian telecom has consolidated to a 3-player private market (Jio, Airtel, "
            "VI) plus PSU BSNL/MTNL. ARPU recovery, 5G monetization, and tower / fibre "
            "infrastructure ownership are the main equity drivers. Spectrum debt and "
            "AGR liabilities still cap VI's competitiveness."
        ),
        "structure": "Concentrated — Jio + Airtel hold the bulk of subscribers and revenue; VI is the distant third with stretched balance sheet.",
        "leaders": [
            {"symbol": "BHARTIARTL", "name": "Bharti Airtel", "promoter": "Bharti / Mittal family + Singtel", "note": "Pan-India + Africa"},
            {"symbol": "RELIANCE", "name": "Reliance Industries (Jio)", "promoter": "Mukesh Ambani family", "note": "Jio is the largest telecom by subscribers"},
            {"symbol": "IDEA", "name": "Vodafone Idea", "promoter": "Vodafone Group + Aditya Birla group", "note": "Distant #3; AGR liabilities"},
            {"symbol": "INDUSTOWER", "name": "Indus Towers", "promoter": "Bharti Airtel + Vodafone (residual)", "note": "Largest tower company in India"},
            {"symbol": "TATACOMM", "name": "Tata Communications", "promoter": "Tata Sons", "note": "Enterprise / international connectivity"},
        ],
        "regulators": ["TRAI", "DoT", "WPC (spectrum)", "Cabinet (spectrum auctions)"],
        "drivers": ["Tariff hikes / ARPU trajectory", "5G monetization (FWA, enterprise)", "Spectrum auction cycles", "Subscriber port-ins from VI"],
        "risks": ["Tariff war risk if VI revives", "AGR / regulatory levies", "Capex burden of 5G build-out", "Satellite (Starlink) competition emerging"],
    },
    "Metals & Mining": {
        "tagline": "Non-ferrous metals, ferrous, and mining.",
        "overview": (
            "Indian non-ferrous metals are heavily integrated (alumina/aluminium, "
            "copper, zinc, lead). Hindalco and Vedanta are the diversified majors; "
            "Hindustan Zinc dominates zinc-lead. Pricing is set on LME globally — "
            "domestic equity performance tracks LME prices and USD/INR more than "
            "Indian demand."
        ),
        "structure": "Concentrated — a handful of integrated producers; strong overlap with the steel and energy ecosystems.",
        "leaders": [
            {"symbol": "HINDALCO", "name": "Hindalco Industries", "promoter": "Aditya Birla group", "note": "Aluminium + Novelis (US copper / aluminium)"},
            {"symbol": "VEDL", "name": "Vedanta", "promoter": "Anil Agarwal / Vedanta Resources", "note": "Diversified — zinc, aluminium, oil & gas"},
            {"symbol": "COALINDIA", "name": "Coal India", "promoter": "Government of India", "note": "Dominant coal miner"},
            {"symbol": "NMDC", "name": "NMDC", "promoter": "Government of India", "note": "Largest iron-ore miner"},
            {"symbol": "NATIONALUM", "name": "National Aluminium (NALCO)", "promoter": "Government of India", "note": "Integrated alumina-aluminium PSU"},
        ],
        "regulators": ["Ministry of Mines", "IBM", "MoEFCC", "DGFT (export controls)"],
        "drivers": ["LME prices (aluminium, copper, zinc)", "Chinese demand", "Coal / power costs", "USD/INR"],
        "risks": ["LME price collapse", "Carbon border taxes (EU CBAM)", "Mining lease / environment clearances", "Rising power tariffs"],
    },
    "Real Estate": {
        "tagline": "Residential, commercial, and REITs.",
        "overview": (
            "Listed Indian real estate has consolidated post-RERA (2016) and IBC "
            "shake-out. Demand is concentrated in tier-1 cities (MMR, Bengaluru, NCR, "
            "Hyderabad, Pune). Premium / luxury residential is leading the current "
            "cycle; commercial leasing tracks GCC build-out and IT hiring."
        ),
        "structure": "Top 5 listed developers lead organised market share; long tail of regional builders has been squeezed since RERA.",
        "leaders": [
            {"symbol": "DLF", "name": "DLF", "promoter": "K.P. Singh family", "note": "NCR-centric; residential + commercial / DCCDL"},
            {"symbol": "LODHA", "name": "Macrotech (Lodha)", "promoter": "Lodha family", "note": "MMR-led residential; expanding to Pune, Bengaluru"},
            {"symbol": "GODREJPROP", "name": "Godrej Properties", "promoter": "Godrej group", "note": "Pan-India, asset-light"},
            {"symbol": "OBEROIRLTY", "name": "Oberoi Realty", "promoter": "Vikas Oberoi family", "note": "Premium MMR residential and retail"},
            {"symbol": "PRESTIGE", "name": "Prestige Estates", "promoter": "Razack family", "note": "Bengaluru-led; expanding to MMR, NCR"},
        ],
        "regulators": ["RERA (state-level)", "Ministry of Housing & Urban Affairs", "SEBI (REITs)", "RBI (housing finance)"],
        "drivers": ["Residential price cycle (especially premium)", "Mortgage rates", "Office leasing / GCC demand", "REIT tax regime"],
        "risks": ["Mortgage rate spikes", "Inventory build-up in mid-segment", "Approval / RERA delays", "Slowdown in IT hiring (offices)"],
    },
    "Finance (NBFC)": {
        "tagline": "Non-banking financial companies — retail, MSME, gold, infra finance.",
        "overview": (
            "NBFCs fill credit gaps that scheduled banks underserve — vehicle, gold, "
            "MSME, microfinance, housing. Asset-quality cycles and cost of borrowing "
            "(linked to G-Sec yields and bank lending rates) are the dominant drivers. "
            "RBI's 2022 scale-based regulation has tightened upper-layer NBFC oversight."
        ),
        "structure": "Diversified universe — retail giants (Bajaj Finance, Shriram), gold (Muthoot, Manappuram), housing (LIC HF, PNB HF), and infra/MSME players.",
        "leaders": [
            {"symbol": "BAJFINANCE", "name": "Bajaj Finance", "promoter": "Bajaj group", "note": "Largest retail NBFC by AUM"},
            {"symbol": "BAJAJFINSV", "name": "Bajaj Finserv", "promoter": "Bajaj group", "note": "Holding co — finance, life and general insurance"},
            {"symbol": "CHOLAFIN", "name": "Cholamandalam Investment & Finance", "promoter": "Murugappa group", "note": "Vehicle, home equity, MSME"},
            {"symbol": "SHRIRAMFIN", "name": "Shriram Finance", "promoter": "Shriram group", "note": "Used CV finance, MSME"},
            {"symbol": "MUTHOOTFIN", "name": "Muthoot Finance", "promoter": "Muthoot family", "note": "Largest gold-loan NBFC"},
        ],
        "regulators": ["RBI", "SEBI (for listed)", "NHB (housing finance)", "IRDAI (insurance arms)"],
        "drivers": ["Credit-cycle expansion", "Bank-NBFC funding spread", "Gold price (for gold-loan NBFCs)", "Asset-quality in unsecured retail"],
        "risks": ["Unsecured retail credit stress", "Bank funding squeeze", "RBI tightening risk weights", "Gold price collapse"],
    },
    "Insurance": {
        "tagline": "Life, general, and health insurance.",
        "overview": (
            "Indian insurance is among the world's most under-penetrated relative to "
            "GDP. Life is dominated by LIC (PSU) on premium share but by private "
            "majors on profitability. General insurance is fragmented; health is the "
            "fastest-growing segment. IRDAI's 'Insurance for All by 2047' push is "
            "shaping product and distribution rules."
        ),
        "structure": "Life: LIC dominant by premium, private majors lead VNB margins. General/health: long tail with HDFC Ergo, ICICI Lombard, Star Health, Bajaj Allianz at the top.",
        "leaders": [
            {"symbol": "HDFCLIFE", "name": "HDFC Life Insurance", "promoter": "HDFC Bank + Standard Life Aberdeen", "note": "Among the largest private life insurers by VNB"},
            {"symbol": "SBILIFE", "name": "SBI Life Insurance", "promoter": "State Bank of India + BNP Paribas Cardif", "note": "Largest private life insurer by APE"},
            {"symbol": "ICICIGI", "name": "ICICI Lombard General", "promoter": "ICICI Bank", "note": "Largest private general insurer"},
            {"symbol": "ICICIPRULI", "name": "ICICI Prudential Life", "promoter": "ICICI Bank + Prudential plc", "note": "ULIP + protection mix"},
            {"symbol": "LICI", "name": "Life Insurance Corporation of India", "promoter": "Government of India", "note": "Largest insurer by premium and AUM"},
        ],
        "regulators": ["IRDAI", "SEBI (for listed)", "Ministry of Finance"],
        "drivers": ["Protection / non-PAR mix", "Tax regime on insurance products", "Channel productivity (banca, agency, online)", "Health insurance penetration"],
        "risks": ["Tax changes hitting high-ticket savings products", "Loss-ratio cycles in motor / health", "IRDAI surrender-value rule changes", "Distribution disruption"],
    },
    "Consumer Durables": {
        "tagline": "Consumer electronics, appliances, lighting, and electricals.",
        "overview": (
            "Consumer durables in India is split between traditional appliances "
            "(ACs, refrigerators, washing machines) and electricals (wires, fans, "
            "switchgear, lighting). Premiumization, summer length (AC sales), and "
            "input commodity costs (copper, aluminium, plastics) drive quarterly "
            "performance. PLI for white goods is reshaping local manufacturing."
        ),
        "structure": "Brand-led — Indian players (Havells, Voltas, Crompton, Polycab) compete with MNCs (LG, Samsung, Whirlpool) and rising D2C / private labels.",
        "leaders": [
            {"symbol": "TITAN", "name": "Titan Company", "promoter": "Tata Sons + Tamil Nadu government (residual)", "note": "Watches, jewellery (Tanishq), eyewear"},
            {"symbol": "HAVELLS", "name": "Havells India", "promoter": "Anil Rai Gupta family", "note": "Switchgear, cables, fans, appliances (Lloyd)"},
            {"symbol": "VOLTAS", "name": "Voltas", "promoter": "Tata Sons", "note": "AC market leader"},
            {"symbol": "CROMPTON", "name": "Crompton Greaves Consumer", "promoter": "Diversified institutional (post Avantha exit)", "note": "Fans, pumps, appliances"},
            {"symbol": "BLUESTARCO", "name": "Blue Star", "promoter": "Vir S. Advani / Suneel M. Advani family", "note": "AC, commercial refrigeration"},
        ],
        "regulators": ["BIS (standards)", "BEE (energy efficiency labels)", "Ministry of Consumer Affairs", "DGTR (anti-dumping)"],
        "drivers": ["Summer length and AC volumes", "Real estate completion (appliances)", "PLI for ACs and components", "Copper / aluminium / plastics"],
        "risks": ["Mild summer / weak primary AC sales", "Commodity inflation", "Dumping from China", "MNC pricing pressure"],
    },
    "Chemicals": {
        "tagline": "Specialty chemicals, agrochemicals, fertilizers, and intermediates.",
        "overview": (
            "Indian chemicals span fragmented specialty / fluorochem / agrochem "
            "producers benefiting from China+1 supply diversification. Fertilizers "
            "remain partly subsidy-driven. Margin direction depends on global "
            "intermediate prices (often China-anchored) and the rupee. The sector saw "
            "a multi-year derating after the 2022 China dumping cycle reversed earlier "
            "tailwinds."
        ),
        "structure": "Highly fragmented. Specialty leaders (Pidilite, SRF, PI Industries, Solar Industries, Linde) plus agrochem majors (UPL, Coromandel, Bayer Cropscience) and fertilizer players (Chambal, GNFC).",
        "leaders": [
            {"symbol": "PIDILITIND", "name": "Pidilite Industries", "promoter": "Parekh family", "note": "Adhesives leader (Fevicol); construction chem"},
            {"symbol": "SRF", "name": "SRF", "promoter": "Bharat Ram family", "note": "Specialty fluorochem, packaging films, technical textiles"},
            {"symbol": "UPL", "name": "UPL", "promoter": "Shroff family", "note": "Global agrochem player"},
            {"symbol": "PIIND", "name": "PI Industries", "promoter": "Salil Singhal family", "note": "Custom synthesis / contract manufacturing for agrochem majors"},
            {"symbol": "SOLARINDS", "name": "Solar Industries", "promoter": "Satyanarayan Nuwal family", "note": "Industrial explosives, defence explosives"},
        ],
        "regulators": ["MoCF (Ministry of Chemicals & Fertilizers)", "CIBRC (agrochem)", "FSSAI (food-grade)", "MoEFCC", "PESO (explosives)"],
        "drivers": ["China+1 supply diversification", "Crude / petrochemical input prices", "USFDA / EU agrochem registrations", "Subsidy disbursements (fertilizer)"],
        "risks": ["China dumping cycles", "Crude / energy spikes", "Agrochem price erosion globally", "Monsoon (agrochem)"],
    },
}


# ── News fetching ────────────────────────────────────────────────────────

# Bias news searches toward the colloquial sector term Google News understands.
_QUERY_TERMS = {
    "FMCG": "FMCG",
    "Banking": "banking",
    "IT": "IT services",
    "Pharma": "pharma",
    "Steel": "steel",
    "Auto": "auto",
    "Energy": "oil and gas",
    "Power": "power",
    "Cement": "cement",
    "Telecom": "telecom",
    "Metals & Mining": "metals",
    "Real Estate": "real estate",
    "Finance (NBFC)": "NBFC",
    "Insurance": "insurance",
    "Consumer Durables": "consumer durables",
    "Chemicals": "chemicals",
}


def _gnews_rss(query: str, max_items: int, scope: str) -> List[Dict]:
    url = (
        "https://news.google.com/rss/search"
        f"?q={quote_plus(query)}"
        "&hl=en-IN&gl=IN&ceid=IN:en"
    )
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": _USER_AGENT})
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
    except Exception as e:
        print(f"[INDUSTRY-INTEL] gnews ({scope}) failed: {e}")
        return []

    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        source = ""
        if " - " in title:
            head, _, src = title.rpartition(" - ")
            if len(src) < 60:
                title = head.strip()
                source = src.strip()
        items.append({
            "title": html.unescape(title),
            "url": link,
            "published": pub_date,
            "source": source,
            "scope": scope,
        })
        if len(items) >= max_items:
            break
    return items


def _fetch_buckets(industry: str) -> Dict[str, List[Dict]]:
    """Fetch the four news buckets in parallel."""
    term = _QUERY_TERMS.get(industry, industry.lower())

    queries = {
        "sector":     (f"Indian {term} sector when:7d", 8),
        "ma":         (f"{term} India M&A OR acquisition OR merger when:14d", 6),
        "regulatory": (f"{term} India regulation OR policy OR SEBI OR RBI when:14d", 5),
        "global":     (f"global {term} demand OR prices when:14d", 4),
    }

    out = {k: [] for k in queries}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            scope: pool.submit(_gnews_rss, q, n, scope)
            for scope, (q, n) in queries.items()
        }
        for scope, fut in futures.items():
            try:
                out[scope] = fut.result()
            except Exception as e:
                print(f"[INDUSTRY-INTEL] bucket {scope} failed: {e}")
                out[scope] = []

    # Dedupe by URL across buckets — sector news often overlaps with M&A.
    seen = set()
    for scope in ("sector", "ma", "regulatory", "global"):
        deduped = []
        for it in out[scope]:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            deduped.append(it)
        out[scope] = deduped

    return out


# ── AI weekly briefing ───────────────────────────────────────────────────

def _gemini_briefing(industry: str, profile: Dict, buckets: Dict[str, List[Dict]],
                     api_key: str, model: str = _DEFAULT_MODEL) -> Optional[str]:
    """One-paragraph AI summary of the past week in the industry."""
    try:
        from google import genai
    except Exception as e:
        print(f"[INDUSTRY-INTEL] genai import failed: {e}")
        return None

    if model not in _FREE_TIER_MODELS:
        model = _DEFAULT_MODEL

    today = datetime.now().strftime("%d %b %Y")
    parts = [
        f"DATE: {today}",
        f"INDUSTRY: {industry}",
        f"OVERVIEW: {profile.get('overview', '')}",
    ]

    for scope, label in [
        ("sector", "SECTOR HEADLINES (last 7 days)"),
        ("ma",     "M&A / DEAL ACTIVITY (last 14 days)"),
        ("regulatory", "REGULATORY / POLICY (last 14 days)"),
        ("global",  "GLOBAL SIGNALS (last 14 days)"),
    ]:
        items = buckets.get(scope, [])
        if items:
            parts.append(f"\n{label}:")
            for n in items[:6]:
                parts.append(f"  - {n['title']} ({n.get('source') or 'news'})")

    prompt = (
        "You are a senior India equity research analyst writing a one-paragraph "
        f"week-in-review for the Indian {industry} sector for an investor. In "
        "4–6 crisp sentences cover: (1) the dominant theme of the week, "
        "(2) any notable M&A or deal activity in India, (3) any regulatory "
        "or policy moves, and (4) global signals that could affect the sector. "
        "Plain prose, no bullets, no preamble, no emojis, no 'not financial "
        "advice'. Cite at most one or two specific headlines if they materially "
        "explain the week. If evidence is thin, say so plainly.\n\n"
        "EVIDENCE:\n" + "\n".join(parts)
    )

    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"[INDUSTRY-INTEL] gemini init failed: {e}")
        return None

    model_chain = [model]
    for fallback in ("gemini-2.0-flash", "gemini-2.5-flash-lite"):
        if fallback != model and fallback not in model_chain:
            model_chain.append(fallback)

    last_err = None
    for m in model_chain:
        for attempt in range(2):
            try:
                resp = client.models.generate_content(
                    model=m,
                    contents=prompt,
                    config={
                        "max_output_tokens": 700,
                        "temperature": 0.25,
                        "thinking_config": {"thinking_budget": 0},
                    },
                )
                text = (resp.text or "").strip()
                if text:
                    return text
            except Exception as e:
                last_err = e
                msg = str(e)
                if "503" in msg or "UNAVAILABLE" in msg or "overloaded" in msg.lower():
                    if attempt == 0:
                        time.sleep(1.2)
                        continue
                print(f"[INDUSTRY-INTEL] gemini {m} failed: {msg[:160]}")
                break
    if last_err:
        print(f"[INDUSTRY-INTEL] all gemini models failed: {last_err}")
    return None


# ── Public orchestrator ──────────────────────────────────────────────────

def get_industry_intel(industry: str, gemini_key: str = "") -> Dict:
    """Build the full intel payload for an industry. Cached 30 min."""
    if not industry:
        return {"error": True, "message": "Industry not specified."}

    now = time.time()
    cached = _INTEL_CACHE.get(industry)
    if cached and (now - cached[0]) < _INTEL_TTL_SEC:
        return cached[1]

    profile = INDUSTRY_PROFILES.get(industry)
    if not profile:
        return {"error": True, "message": f"No profile available for '{industry}'."}

    buckets = _fetch_buckets(industry)

    api_key = (gemini_key or os.environ.get("GEMINI_API_KEY", "")).strip()
    summary = None
    ai_powered = False
    if api_key:
        summary = _gemini_briefing(industry, profile, buckets, api_key)
        ai_powered = bool(summary)

    if not summary:
        # Heuristic fallback: stitch the top sector headline + count signals.
        top = (buckets.get("sector") or buckets.get("ma") or [None])[0]
        deal_n = len(buckets.get("ma", []))
        reg_n = len(buckets.get("regulatory", []))
        bits = [f"Past week in Indian {industry}:"]
        if top:
            bits.append(f"top headline — \"{top['title']}\" ({top.get('source','news')}).")
        if deal_n:
            bits.append(f"{deal_n} deal-related stories tracked in the last 14 days.")
        if reg_n:
            bits.append(f"{reg_n} regulatory / policy updates noted.")
        if len(bits) == 1:
            bits.append("No major sector-level news surfaced this week.")
        summary = " ".join(bits)

    payload = {
        "industry": industry,
        "profile": profile,
        "news": buckets,
        "summary": summary,
        "ai_powered": ai_powered,
        "fetched_at": now,
    }
    _INTEL_CACHE[industry] = (now, payload)
    return payload


def list_supported_industries() -> List[str]:
    return list(INDUSTRY_PROFILES.keys())

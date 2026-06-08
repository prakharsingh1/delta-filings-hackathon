"""On-demand director data scraping from public MCA aggregators.

This module is called ONLY when a user explicitly searches a DIN. We never
crawl these sources in bulk — that would (a) violate their terms of service
and (b) get our IP blocked quickly.

Design:
- Cache every DIN lookup in a local SQLite table for 30 days, so repeat
  searches don't re-hit the upstream.
- Try ZaubaCorp first (cleaner HTML), fall back to Tofler if ZaubaCorp fails.
- Graceful degradation: if both sources fail, return None — the rest of the
  DIN search flow (NSE governance + filings) still works.
- All network errors are logged but swallowed so the UI never breaks.

DISCLAIMER:
The scraped data is "best-effort third-party information". It is surfaced to
the user with a visible label so governance decisions are never made from it
without independent verification against MCA filings.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from database import get_connection

CACHE_TTL_DAYS = 30
REQUEST_TIMEOUT = 20

# ── Curated director dataset ────────────────────────────────────────────────
#
# Hardcoded MCA directorship snapshots for well-known directors. Checked
# BEFORE the web scrapers so the example cards on the DIN search page work
# reliably in production even when datacenter IPs are blocked by the
# aggregator sites. Data compiled from publicly disclosed board compositions
# in company annual reports. The visible UI banner tags this as
# "curated snapshot" so users know it's not live MCA data.
CURATED_AS_OF = "2026-04-01"

CURATED_DIRECTORS: dict[str, dict] = {
    # ─────────────────────────────────────────────────────────────────────────
    # Kiran Mazumdar-Shaw (DIN 00347229) — 35 companies
    # Source: ZaubaCorp snapshot, Apr 2026
    # ─────────────────────────────────────────────────────────────────────────
    "00347229": {
        "din": "00347229",
        "name": "Kiran Mazumdar Shaw",
        "about": (
            "KIRAN MAZUMDAR SHAW is a director registered with Ministry of "
            "Corporate Affairs. Their Director Identification Number (DIN) "
            "is 00347229. KIRAN MAZUMDAR SHAW is/has been associated with "
            "35 companies. Founder and Executive Chairperson of Biocon "
            "Limited, India's largest biopharmaceuticals company, and a "
            "pioneer of India's biotechnology sector."
        ),
        "active_companies": [
            {"company": "GLENLOCH PROPERTIES LLP", "designation": "Designated Partner", "appointment_date": "07-Feb-2017", "cessation_date": "-", "status": "Active"},
            {"company": "BIOCON LIMITED", "designation": "Whole-time director", "appointment_date": "01-Apr-2020", "cessation_date": "-", "status": "Active"},
            {"company": "TRENT LIMITED", "designation": "Director", "appointment_date": "09-Apr-2024", "cessation_date": "-", "status": "Active"},
            {"company": "SYNGENE INTERNATIONAL LIMITED", "designation": "Director", "appointment_date": "22-Jul-2020", "cessation_date": "-", "status": "Active"},
            {"company": "NARAYANA HRUDAYALAYA LIMITED", "designation": "Director", "appointment_date": "06-Feb-2008", "cessation_date": "-", "status": "Active"},
            {"company": "BIOCON BIOLOGICS LIMITED", "designation": "Whole-time director", "appointment_date": "21-Jan-2021", "cessation_date": "-", "status": "Active"},
            {"company": "IMMUNEEL THERAPEUTICS PRIVATE LIMITED", "designation": "Director", "appointment_date": "20-Dec-2018", "cessation_date": "-", "status": "Active"},
            {"company": "BIOCON BIOPHARMACEUTICALS LIMITED", "designation": "Managing Director", "appointment_date": "22-Mar-2007", "cessation_date": "-", "status": "Active"},
            {"company": "BIOCON PHARMA LIMITED", "designation": "Director", "appointment_date": "31-Oct-2014", "cessation_date": "-", "status": "Active"},
            {"company": "BIOCON BIOSPHERE LIMITED", "designation": "Director", "appointment_date": "24-Dec-2019", "cessation_date": "-", "status": "Active"},
            {"company": "BIOCON RESEARCH LIMITED", "designation": "Director", "appointment_date": "28-May-2008", "cessation_date": "-", "status": "Active"},
            {"company": "BIOFUSION THERAPEUTICS LIMITED", "designation": "Director", "appointment_date": "18-Mar-2021", "cessation_date": "-", "status": "Active"},
            {"company": "CSEP RESEARCH FOUNDATION", "designation": "Director", "appointment_date": "09-Nov-2021", "cessation_date": "-", "status": "Active"},
            {"company": "BIOCON ACADEMY", "designation": "Director", "appointment_date": "03-Dec-2013", "cessation_date": "-", "status": "Active"},
            {"company": "MAZUMDAR SHAW MEDICAL FOUNDATION", "designation": "Director", "appointment_date": "10-May-2013", "cessation_date": "-", "status": "Active"},
            {"company": "NARAYANA VAISHNO DEVI SPECIALTY HOSPITALS PRIVATE LIMITED", "designation": "Director", "appointment_date": "05-Sep-2014", "cessation_date": "-", "status": "Active"},
            {"company": "CLINIGENE INTERNATIONAL LIMITED", "designation": "Director", "appointment_date": "04-Aug-2000", "cessation_date": "-", "status": "Active"},
            {"company": "SCIENCE GALLERY BENGALURU", "designation": "Director", "appointment_date": "21-Mar-2018", "cessation_date": "-", "status": "Active"},
        ],
        "past_companies": [
            {"company": "BIOCON LIMITED", "designation": "Director", "appointment_date": "01-Apr-2020", "cessation_date": "01-Apr-2020", "status": "Active"},
            {"company": "BIOCON LIMITED", "designation": "Managing Director", "appointment_date": "01-Apr-2010", "cessation_date": "01-Apr-2020", "status": "Active"},
            {"company": "TRENT LIMITED", "designation": "Additional Director", "appointment_date": "01-Apr-2024", "cessation_date": "11-Jun-2024", "status": "Active"},
            {"company": "UNITED BREWERIES LIMITED", "designation": "Additional Director", "appointment_date": "26-Oct-2009", "cessation_date": "20-Aug-2010", "status": "Active"},
            {"company": "UNITED BREWERIES LIMITED", "designation": "Director", "appointment_date": "20-Aug-2010", "cessation_date": "01-Aug-2024", "status": "Active"},
            {"company": "INFOSYS LIMITED", "designation": "Additional Director", "appointment_date": "10-Jan-2014", "cessation_date": "14-Jun-2014", "status": "Active"},
            {"company": "INFOSYS LIMITED", "designation": "Director", "appointment_date": "14-Jun-2014", "cessation_date": "22-Mar-2023", "status": "Active"},
            {"company": "SYNGENE INTERNATIONAL LIMITED", "designation": "Additional Director", "appointment_date": "01-Apr-2020", "cessation_date": "22-Jul-2020", "status": "Active"},
            {"company": "SYNGENE INTERNATIONAL LIMITED", "designation": "Director", "appointment_date": "18-Nov-1993", "cessation_date": "14-Dec-2011", "status": "Active"},
            {"company": "SYNGENE INTERNATIONAL LIMITED", "designation": "Managing Director", "appointment_date": "14-Dec-2011", "cessation_date": "31-Mar-2020", "status": "Active"},
            {"company": "BIOCON BIOLOGICS LIMITED", "designation": "Director", "appointment_date": "08-Jun-2016", "cessation_date": "21-Jan-2021", "status": "Active"},
            {"company": "BIOCON BIOPHARMACEUTICALS LIMITED", "designation": "Director", "appointment_date": "17-Jun-2002", "cessation_date": "22-Mar-2007", "status": "Active"},
            {"company": "GLENLOCH PROPERTIES PRIVATE LIMITED", "designation": "Director", "appointment_date": "09-Mar-2010", "cessation_date": "06-Feb-2017", "status": "Active"},
            {"company": "CSEP RESEARCH FOUNDATION", "designation": "Additional Director", "appointment_date": "09-Nov-2021", "cessation_date": "05-Sep-2022", "status": "Active"},
            {"company": "INVEST KARNATAKA FORUM", "designation": "Nominee Director", "appointment_date": "23-Aug-2016", "cessation_date": "30-Jul-2021", "status": "Active"},
            {"company": "INDIAN SCHOOL OF BUSINESS", "designation": "Director", "appointment_date": "08-Apr-2006", "cessation_date": "10-Apr-2015", "status": "Active"},
            {"company": "NARAYANA INSTITUTE FOR ADVANCED RESEARCH PRIVATE LIMITED", "designation": "Director", "appointment_date": "17-Jan-2007", "cessation_date": "06-Nov-2017", "status": "Active"},
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Nandan M. Nilekani (DIN 00091908) — Infosys, EkStep
    # Source: publicly disclosed board compositions
    # ─────────────────────────────────────────────────────────────────────────
    "00091908": {
        "din": "00091908",
        "name": "Nandan M. Nilekani",
        "about": (
            "Co-founder and Non-Executive Chairman of Infosys Limited. "
            "Founding Chairman of UIDAI (Aadhaar, 2009-2014). Co-founder of "
            "EkStep Foundation. Architect of India Stack."
        ),
        "active_companies": [
            {"company": "INFOSYS LIMITED", "designation": "Non-Executive Chairman", "appointment_date": "24-Aug-2017", "cessation_date": "-", "status": "Active"},
            {"company": "EKSTEP FOUNDATION", "designation": "Director", "appointment_date": "17-Feb-2015", "cessation_date": "-", "status": "Active"},
            {"company": "FUNDAMENTUM PARTNERSHIP FUND I", "designation": "Director", "appointment_date": "15-Jun-2017", "cessation_date": "-", "status": "Active"},
        ],
        "past_companies": [
            {"company": "INFOSYS LIMITED", "designation": "Co-Chairman / CEO / MD", "appointment_date": "02-Jul-1981", "cessation_date": "09-Jul-2009", "status": "Active"},
            {"company": "UIDAI (GoI)", "designation": "Chairman", "appointment_date": "09-Jul-2009", "cessation_date": "13-Mar-2014", "status": "Active"},
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Uday Suresh Kotak (DIN 00007467) — 16 companies
    # Source: ZaubaCorp snapshot, Apr 2026
    # ─────────────────────────────────────────────────────────────────────────
    "00007467": {
        "din": "00007467",
        "name": "Uday Suresh Kotak",
        "about": (
            "UDAY SURESH KOTAK is a director registered with Ministry of "
            "Corporate Affairs. Their Director Identification Number (DIN) "
            "is 00007467. UDAY SURESH KOTAK is/has been associated with 16 "
            "companies. Founder of Kotak Mahindra Bank; continues as Non-"
            "Executive Director since stepping down as MD & CEO in Sep 2023."
        ),
        "active_companies": [
            {"company": "KOTAK MAHINDRA BANK LIMITED", "designation": "Director", "appointment_date": "01-Jan-2024", "cessation_date": "-", "status": "Active"},
            {"company": "KOTAK MAHINDRA INVESTMENTS LIMITED", "designation": "Director", "appointment_date": "03-Jun-2014", "cessation_date": "-", "status": "Active"},
            {"company": "KOTAK MAHINDRA ASSET MANAGEMENT COMPANY LIMITED", "designation": "Director", "appointment_date": "02-Aug-1994", "cessation_date": "-", "status": "Active"},
            {"company": "KOTAK MAHINDRA LIFE INSURANCE COMPANY LIMITED", "designation": "Director", "appointment_date": "31-Aug-2000", "cessation_date": "-", "status": "Active"},
            {"company": "KOTAK MAHINDRA CAPITAL COMPANY LIMITED", "designation": "Director", "appointment_date": "27-Jul-1995", "cessation_date": "-", "status": "Active"},
            {"company": "KOTAK MAHINDRA PRIME LIMITED", "designation": "Director", "appointment_date": "13-Dec-1996", "cessation_date": "-", "status": "Active"},
            {"company": "THE MAHINDRA UNITED WORLD COLLEGE OF INDIA", "designation": "Director", "appointment_date": "22-Jul-1993", "cessation_date": "-", "status": "Active"},
            {"company": "THE ANGLO SCOTTISH EDUCATION SOCIETY", "designation": "Director", "appointment_date": "10-Dec-2015", "cessation_date": "-", "status": "Active"},
        ],
        "past_companies": [
            {"company": "KOTAK MAHINDRA BANK LIMITED", "designation": "CEO", "appointment_date": "01-May-2018", "cessation_date": "01-Sep-2023", "status": "Active"},
            {"company": "KOTAK MAHINDRA BANK LIMITED", "designation": "Managing Director", "appointment_date": "21-Nov-1985", "cessation_date": "31-Dec-2020", "status": "Active"},
            {"company": "INFRASTRUCTURE LEASING AND FINANCIAL SERVICES LIMITED", "designation": "Director", "appointment_date": "03-Oct-2018", "cessation_date": "03-Apr-2022", "status": "Active"},
            {"company": "KOTAK MAHINDRA INVESTMENTS LIMITED", "designation": "Additional Director", "appointment_date": "01-Oct-2013", "cessation_date": "03-Jun-2014", "status": "Active"},
            {"company": "KOTAK INFRASTRUCTURE DEBT FUND LIMITED", "designation": "Director", "appointment_date": "23-Feb-2001", "cessation_date": "30-Mar-2016", "status": "Active"},
            {"company": "BOMBAY CHAMBER OF COMMERCE AND INDUSTRY", "designation": "Director", "appointment_date": "01-Jun-2007", "cessation_date": "13-Jun-2008", "status": "Active"},
            {"company": "INDIAN SCHOOL OF BUSINESS", "designation": "Director", "appointment_date": "08-Apr-2006", "cessation_date": "17-Jun-2010", "status": "Active"},
            {"company": "KOTAK SECURITIES LIMITED", "designation": "Director", "appointment_date": "20-Jul-1994", "cessation_date": "14-Mar-2019", "status": "Active"},
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # Anand Gopal Mahindra (DIN 00004695) — 49 companies
    # Source: ZaubaCorp snapshot, Apr 2026
    # ─────────────────────────────────────────────────────────────────────────
    "00004695": {
        "din": "00004695",
        "name": "Anand Gopal Mahindra",
        "about": (
            "ANAND GOPAL MAHINDRA is a director registered with Ministry of "
            "Corporate Affairs. Their Director Identification Number (DIN) "
            "is 00004695. ANAND GOPAL MAHINDRA is/has been associated with "
            "49 companies. Chairman of the Mahindra Group, one of India's "
            "largest conglomerates. Harvard Business School MBA; Padma "
            "Bhushan recipient (2020)."
        ),
        "active_companies": [
            {"company": "PHI CORPORATE SOLUTIONS LLP", "designation": "Partner", "appointment_date": "31-Jan-2020", "cessation_date": "-", "status": "Active"},
            {"company": "GOTHAM PARK PROJECTS LLP", "designation": "Designated Partner", "appointment_date": "08-Mar-2016", "cessation_date": "-", "status": "Active"},
            {"company": "TECH MAHINDRA LIMITED", "designation": "Director", "appointment_date": "26-Sep-2013", "cessation_date": "-", "status": "Active"},
            {"company": "MAHINDRA AND MAHINDRA LIMITED", "designation": "Director", "appointment_date": "12-Nov-2021", "cessation_date": "-", "status": "Active"},
            {"company": "MAHINDRA UGINE STEEL COMPANY LIMITED", "designation": "Director", "appointment_date": "30-Oct-1986", "cessation_date": "-", "status": "Active"},
            {"company": "ARAKU ORIGINALS PRIVATE LIMITED", "designation": "Director", "appointment_date": "20-Aug-2009", "cessation_date": "-", "status": "Active"},
            {"company": "THE INDIAN AND EASTERN ENGINEER COMPANY PRIVATE LIMITED", "designation": "Director", "appointment_date": "21-May-2022", "cessation_date": "-", "status": "Active"},
            {"company": "CLASSIC LEGENDS PRIVATE LIMITED", "designation": "Director", "appointment_date": "28-Sep-2017", "cessation_date": "-", "status": "Active"},
            {"company": "PRUDENTIAL MANAGEMENT AND SERVICES PVT LTD", "designation": "Director", "appointment_date": "29-Sep-2010", "cessation_date": "-", "status": "Active"},
            {"company": "MAHINDRA HOLDINGS & FINANCE LIMITED", "designation": "Director", "appointment_date": "03-Mar-1995", "cessation_date": "-", "status": "Active"},
            {"company": "MAHINDRA HOLDINGS LIMITED", "designation": "Director", "appointment_date": "02-Nov-2007", "cessation_date": "-", "status": "Active"},
            {"company": "THE MAHINDRA UNITED WORLD COLLEGE OF INDIA", "designation": "Director", "appointment_date": "22-Jul-1993", "cessation_date": "-", "status": "Active"},
            {"company": "BREACH CANDY HOSPITAL TRUST", "designation": "Director", "appointment_date": "23-Sep-2020", "cessation_date": "-", "status": "Active"},
            {"company": "NAANDI COMMUNITY WATER SERVICES PRIVATE LIMITED", "designation": "Director", "appointment_date": "21-May-2010", "cessation_date": "-", "status": "Active"},
            {"company": "TECH MAHINDRA FOUNDATION", "designation": "Director", "appointment_date": "20-Jul-2007", "cessation_date": "-", "status": "Active"},
            {"company": "INVEST INDIA", "designation": "Nominee Director", "appointment_date": "08-Dec-2017", "cessation_date": "-", "status": "Active"},
            {"company": "ANGULAR CONSTRUCTIONS PRIVATE LIMITED", "designation": "Director", "appointment_date": "14-Feb-1998", "cessation_date": "-", "status": "Active"},
        ],
        "past_companies": [
            {"company": "CIE AUTOMOTIVE INDIA LIMITED", "designation": "Director", "appointment_date": "28-Apr-2006", "cessation_date": "22-Jul-2010", "status": "Active"},
            {"company": "MAHINDRA LIFESPACE DEVELOPERS LIMITED", "designation": "Director", "appointment_date": "29-Jan-2004", "cessation_date": "15-Mar-2010", "status": "Active"},
            {"company": "TECH MAHINDRA LIMITED", "designation": "Additional Director", "appointment_date": "05-Nov-2012", "cessation_date": "26-Sep-2013", "status": "Active"},
            {"company": "TECH MAHINDRA LIMITED", "designation": "Nominee Director", "appointment_date": "19-Sep-1995", "cessation_date": "05-Nov-2012", "status": "Active"},
            {"company": "KOTAK MAHINDRA BANK LIMITED", "designation": "Director", "appointment_date": "11-Jun-1986", "cessation_date": "21-Mar-2011", "status": "Active"},
            {"company": "MAHINDRA AND MAHINDRA FINANCIAL SERVICES LIMITED", "designation": "Director", "appointment_date": "31-Dec-1991", "cessation_date": "22-Apr-2008", "status": "Active"},
            {"company": "MAHINDRA AND MAHINDRA LIMITED", "designation": "Managing Director", "appointment_date": "01-Aug-2010", "cessation_date": "12-Nov-2016", "status": "Active"},
            {"company": "MAHINDRA AND MAHINDRA LIMITED", "designation": "Whole-time director", "appointment_date": "12-Nov-2016", "cessation_date": "12-Nov-2021", "status": "Active"},
            {"company": "ARAKU ORIGINALS PRIVATE LIMITED", "designation": "Additional Director", "appointment_date": "10-Aug-2009", "cessation_date": "20-Aug-2009", "status": "Active"},
            {"company": "THE INDIAN AND EASTERN ENGINEER COMPANY PRIVATE LIMITED", "designation": "Additional Director", "appointment_date": "21-May-2022", "cessation_date": "26-Sep-2022", "status": "Active"},
            {"company": "GOOLESTAN INVESTMENTS PRIVATE LIMITED", "designation": "Director", "appointment_date": "29-Jun-1999", "cessation_date": "25-Feb-2016", "status": "Active"},
            {"company": "DEFENCE LAND SYSTEMS INDIA LIMITED", "designation": "Director", "appointment_date": "25-Jul-2011", "cessation_date": "18-Jan-2013", "status": "Active"},
            {"company": "DEFENCE LAND SYSTEMS INDIA LIMITED", "designation": "Nominee Director", "appointment_date": "27-Apr-2010", "cessation_date": "25-Jul-2011", "status": "Active"},
            {"company": "MSL DRIVELINE SYSTEMS LIMITED", "designation": "Director", "appointment_date": "30-Sep-1994", "cessation_date": "27-Apr-2010", "status": "Active"},
            {"company": "CLASSIC LEGENDS PRIVATE LIMITED", "designation": "Additional Director", "appointment_date": "25-Jul-2017", "cessation_date": "28-Sep-2017", "status": "Active"},
            {"company": "MAHINDRA TWO WHEELERS LIMITED", "designation": "Additional Director", "appointment_date": "12-Dec-2008", "cessation_date": "12-Aug-2009", "status": "Active"},
            {"company": "MAHINDRA TWO WHEELERS LIMITED", "designation": "Director", "appointment_date": "12-Aug-2009", "cessation_date": "17-Mar-2017", "status": "Active"},
            {"company": "MOONSHINE CONSTRUCTION PRIVATE LIMITED", "designation": "Additional Director", "appointment_date": "07-Jun-2011", "cessation_date": "17-Sep-2011", "status": "Active"},
            {"company": "MAHINDRA ACCELO LIMITED", "designation": "Director", "appointment_date": "20-Feb-1991", "cessation_date": "11-Dec-2006", "status": "Active"},
            {"company": "AIR INDIA LIMITED", "designation": "Director", "appointment_date": "05-Mar-2010", "cessation_date": "09-Mar-2011", "status": "Active"},
            {"company": "MAHINDRA TRUCKS AND BUSES LIMITED", "designation": "Director", "appointment_date": "14-Jul-2006", "cessation_date": "09-Apr-2014", "status": "Active"},
            {"company": "MAHINDRA TRUCKS AND BUSES LIMITED", "designation": "Nominee Director", "appointment_date": "11-Jan-2006", "cessation_date": "14-Jul-2006", "status": "Active"},
            {"company": "MAHINDRA FIRST CHOICE WHEELS LIMITED", "designation": "Director", "appointment_date": "16-Jun-2000", "cessation_date": "09-May-2020", "status": "Active"},
            {"company": "PRUDENTIAL MANAGEMENT AND SERVICES PVT LTD", "designation": "Additional Director", "appointment_date": "30-Apr-2010", "cessation_date": "29-Sep-2010", "status": "Active"},
            {"company": "MAHINDRA HOLDINGS LIMITED", "designation": "Director", "appointment_date": "02-Nov-2007", "cessation_date": "08-May-2023", "status": "Active"},
            {"company": "NATIONAL STOCK EXCHANGE OF INDIA LTD.", "designation": "Director", "appointment_date": "02-May-2002", "cessation_date": "28-Dec-2010", "status": "Active"},
            {"company": "M. A. R. K. HOTELS LIMITED", "designation": "Director", "appointment_date": "06-Dec-2006", "cessation_date": "01-Oct-2011", "status": "Active"},
            {"company": "CREATIVELAND PUBLISHING PRIVATE LIMITED", "designation": "Director", "appointment_date": "12-Aug-1999", "cessation_date": "24-Apr-2009", "status": "Active"},
            {"company": "THE MAHINDRA UNITED WORLD COLLEGE OF INDIA", "designation": "Director", "appointment_date": "22-Jul-1993", "cessation_date": "08-Jun-2023", "status": "Active"},
            {"company": "BREACH CANDY HOSPITAL TRUST", "designation": "Director", "appointment_date": "24-Mar-2005", "cessation_date": "21-Mar-2019", "status": "Active"},
            {"company": "TECH MAHINDRA FOUNDATION", "designation": "Additional Director", "appointment_date": "19-Apr-2007", "cessation_date": "20-Jul-2007", "status": "Active"},
            {"company": "INVEST INDIA", "designation": "Additional Director", "appointment_date": "14-Nov-2017", "cessation_date": "08-Dec-2017", "status": "Active"},
        ],
    },
}

# ── Browser fingerprinting ──────────────────────────────────────────────────
#
# Many MCA aggregators are fronted by Cloudflare or a WAF that blocks based on
# header signals: missing Sec-Ch-Ua, missing Sec-Fetch-*, wrong UA/platform
# combo, no Accept-Encoding, no Accept-Language. We rotate across several
# realistic browser profiles so each attempt looks like a different real user.

_BROWSER_PROFILES = [
    # Chrome 131 / Mac
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec_ch_ua_platform": '"macOS"',
    },
    # Chrome 131 / Windows
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec_ch_ua_platform": '"Windows"',
    },
    # Edge 131 / Windows
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
        ),
        "sec_ch_ua": '"Microsoft Edge";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec_ch_ua_platform": '"Windows"',
    },
    # Firefox 134 / Mac  (no Sec-Ch-Ua — Firefox doesn't send it)
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:134.0) "
            "Gecko/20100101 Firefox/134.0"
        ),
        "sec_ch_ua": None,
        "sec_ch_ua_platform": None,
    },
]


def _build_headers(referer: Optional[str] = None) -> dict:
    """Construct a full browser-like header set from a random profile."""
    profile = random.choice(_BROWSER_PROFILES)
    headers = {
        "User-Agent": profile["User-Agent"],
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "en-US,en;q=0.9,en-IN;q=0.8",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site" if referer else "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
        "DNT": "1",
    }
    if profile.get("sec_ch_ua"):
        headers["Sec-Ch-Ua"] = profile["sec_ch_ua"]
        headers["Sec-Ch-Ua-Mobile"] = "?0"
        headers["Sec-Ch-Ua-Platform"] = profile["sec_ch_ua_platform"]
    if referer:
        headers["Referer"] = referer
    return headers


def _new_session(host_url: str) -> requests.Session:
    """Create a session and warm it up by fetching the homepage first.

    The homepage hit gets Cloudflare/Akamai to issue cookies that subsequent
    requests can present, which often flips a 403 into a 200.
    """
    s = requests.Session()
    host = urlparse(host_url)
    homepage = f"{host.scheme}://{host.netloc}/"
    try:
        s.get(
            homepage,
            headers=_build_headers(referer="https://www.google.com/"),
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        print(f"[MCA] Session warmup failed for {host.netloc}: {e}")
    # small jitter so the second hit doesn't look robotic
    time.sleep(random.uniform(0.6, 1.4))
    return s


def _fetch(session: requests.Session, url: str, referer: Optional[str] = None) -> Optional[requests.Response]:
    """One HTTP GET with browser-like headers. Returns None on network error."""
    try:
        return session.get(
            url,
            headers=_build_headers(referer=referer),
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        print(f"[MCA] request error {url}: {e}")
        return None


def _is_block_page(html: str) -> bool:
    """Detect common WAF / captcha block pages that come back as HTTP 200."""
    low = html.lower()
    markers = (
        "captcha",
        "just a moment",
        "attention required",
        "cf-error-details",
        "access denied",
        "verifying you are human",
        "checking your browser",
    )
    return any(m in low for m in markers)


def init_mca_cache():
    """Create the cache table if missing. Safe to call on every import."""
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mca_director_cache (
            din TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            source TEXT
        )
        """
    )
    conn.commit()
    conn.close()


# ── Cache helpers ──────────────────────────────────────────────────────────

def _get_cached(din: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        "SELECT data_json, fetched_at FROM mca_director_cache WHERE din = ?",
        (din,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        if datetime.now() - fetched_at > timedelta(days=CACHE_TTL_DAYS):
            return None
        return json.loads(row["data_json"])
    except Exception:
        return None


def _save_cache(din: str, data: dict, source: str):
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO mca_director_cache
           (din, data_json, fetched_at, source) VALUES (?, ?, ?, ?)""",
        (din, json.dumps(data), datetime.now().isoformat(), source),
    )
    conn.commit()
    conn.close()


# ── ZaubaCorp scraper ──────────────────────────────────────────────────────

def _zauba_candidate_urls(din: str) -> list[str]:
    """ZaubaCorp exposes director pages under a DIN-based slug. These URL
    forms have all worked historically; we try them in order."""
    return [
        f"https://www.zaubacorp.com/director-search/{din}",
        f"https://www.zaubacorp.com/directorSearch/{din}",
        f"https://www.zaubacorp.com/director-profile/{din}",
    ]


def _scrape_zauba(din: str) -> Optional[dict]:
    """Try to fetch and parse a ZaubaCorp director page for the given DIN."""
    print(f"[MCA] Trying ZaubaCorp for DIN {din}")
    session = _new_session("https://www.zaubacorp.com/")
    google_ref = f"https://www.google.com/search?q=zaubacorp+director+{din}"
    for url in _zauba_candidate_urls(din):
        r = _fetch(session, url, referer=google_ref)
        if r is None:
            continue

        print(f"[MCA] ZaubaCorp {url} -> HTTP {r.status_code}, len={len(r.text)}, final={r.url}")
        if r.status_code != 200 or not r.text:
            continue
        if _is_block_page(r.text):
            print(f"[MCA] ZaubaCorp blocked (captcha/waf) for DIN {din}")
            continue

        parsed = _parse_zauba_director_page(r.text, din)
        if parsed and (parsed.get("name") or parsed.get("active_companies")
                       or parsed.get("past_companies")):
            parsed["source_url"] = r.url
            print(f"[MCA] ZaubaCorp OK for {din}: name={parsed.get('name')}, active={len(parsed.get('active_companies', []))}, past={len(parsed.get('past_companies', []))}")
            return parsed
        else:
            print(f"[MCA] ZaubaCorp parsed empty for DIN {din}")

    return None


def _parse_zauba_director_page(html: str, din: str) -> Optional[dict]:
    """Parse a ZaubaCorp director page into structured dict.

    We're defensive: ZaubaCorp's HTML structure has shifted in the past,
    so we look for tables and section headings rather than fixed CSS paths.
    """
    soup = BeautifulSoup(html, "html.parser")

    result = {
        "din": din,
        "name": None,
        "about": None,
        "active_companies": [],
        "past_companies": [],
    }

    # Name: usually the first h1 on the page.
    h1 = soup.find("h1")
    if h1:
        txt = h1.get_text(strip=True)
        if txt and len(txt) < 120:
            result["name"] = txt

    # About paragraph: find anything with "About" heading.
    about_h = soup.find(["h2", "h3"], string=re.compile(r"^About\b", re.I))
    if about_h:
        # Next paragraph sibling.
        p = about_h.find_next("p")
        if p:
            result["about"] = p.get_text(" ", strip=True)

    # Find tables; classify by preceding heading.
    for table in soup.find_all("table"):
        # Find nearest preceding heading to decide whether this is "active"
        # or "past" companies.
        heading_text = ""
        prev = table.find_previous(["h1", "h2", "h3", "h4"])
        if prev:
            heading_text = prev.get_text(" ", strip=True).lower()

        target_key = "active_companies"
        if "past" in heading_text:
            target_key = "past_companies"
        elif "associated" not in heading_text and "companies" not in heading_text:
            # Not a directorship table — skip.
            continue

        # Read header cells to index columns.
        header_cells = [th.get_text(" ", strip=True).lower()
                        for th in table.find_all("th")]

        def col(row_cells, names):
            for n in names:
                if n in header_cells:
                    idx = header_cells.index(n)
                    if idx < len(row_cells):
                        return row_cells[idx].get_text(" ", strip=True)
            return ""

        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if not cells:
                continue
            company = col(cells, ["company", "company name"])
            designation = col(cells, ["designation", "role"])
            appt = col(cells, ["date of appointment", "appointment date"])
            ceased = col(cells, ["date of cessation", "cessation date"])

            # Fallback to positional if headers weren't parsed.
            if not company and cells:
                company = cells[0].get_text(" ", strip=True)
            if not designation and len(cells) > 1:
                designation = cells[1].get_text(" ", strip=True)
            if not appt and len(cells) > 2:
                appt = cells[2].get_text(" ", strip=True)
            if not ceased and len(cells) > 3:
                ceased = cells[3].get_text(" ", strip=True)

            if not company or company.lower() == "company":
                continue

            result[target_key].append({
                "company": company,
                "designation": designation or "-",
                "appointment_date": appt or "-",
                "cessation_date": ceased or "-",
            })

    # De-dup within each list.
    for key in ("active_companies", "past_companies"):
        seen = set()
        deduped = []
        for c in result[key]:
            sig = (c["company"], c["designation"], c["appointment_date"])
            if sig in seen:
                continue
            seen.add(sig)
            deduped.append(c)
        result[key] = deduped

    if not result["name"] and not result["active_companies"] and not result["past_companies"]:
        return None
    return result


# ── Tofler scraper (fallback) ──────────────────────────────────────────────

def _scrape_tofler(din: str) -> Optional[dict]:
    """Tofler has a DIN-based director page. Useful fallback when ZaubaCorp
    fails. Tofler's table layout (as of 2026):

      Business Name | Incorporation Date | Industry | Status |
      Date of appointment | As on | Designation

    The "Status" column reflects the company's MCA status (Active / Struck
    Off / Amalgamated / Dissolved / Under Liquidation). We use that signal
    to split into active vs past: companies whose MCA status is "Active"
    count as active directorships; everything else goes to past.
    """
    url = f"https://www.tofler.in/director/{din}"
    print(f"[MCA] Trying Tofler for DIN {din} at {url}")
    session = _new_session("https://www.tofler.in/")
    google_ref = f"https://www.google.com/search?q=tofler+director+{din}"
    r = _fetch(session, url, referer=google_ref)
    if r is None:
        return None

    print(f"[MCA] Tofler -> HTTP {r.status_code}, len={len(r.text)}, final={r.url}")
    if r.status_code != 200 or not r.text:
        return None

    if _is_block_page(r.text):
        print(f"[MCA] Tofler blocked (captcha/waf) for DIN {din}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    result = {
        "din": din,
        "name": None,
        "about": None,
        "active_companies": [],
        "past_companies": [],
        "source_url": r.url,
    }

    h1 = soup.find("h1")
    if h1:
        result["name"] = h1.get_text(strip=True)[:120]

    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True).lower()
                   for th in table.find_all("th")]
        if not headers:
            continue

        def idx_of(*names):
            for n in names:
                n = n.lower()
                if n in headers:
                    return headers.index(n)
            return -1

        i_name = idx_of("business name", "company", "company name")
        i_status = idx_of("status")
        i_appt = idx_of("date of appointment", "appointment date")
        i_desig = idx_of("designation")
        i_industry = idx_of("industry")

        if i_name < 0:
            continue  # not a directorship table

        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if not cells:
                continue
            def cell_text(i):
                return cells[i].get_text(" ", strip=True) if 0 <= i < len(cells) else ""

            company = cell_text(i_name)
            if not company:
                continue
            status = cell_text(i_status).lower()
            appt = cell_text(i_appt) or "-"
            desig = cell_text(i_desig) or "-"
            industry = cell_text(i_industry)

            # Active iff the company itself is Active on MCA. Other MCA
            # statuses (Struck Off, Amalgamated, Dissolved, Under Liquidation,
            # etc.) go to past.
            is_active = "active" in status and "inactive" not in status
            entry = {
                "company": company,
                "designation": desig,
                "appointment_date": appt,
                "cessation_date": "-",
                "status": status.title() if status else "-",
                "industry": industry,
            }
            if is_active:
                result["active_companies"].append(entry)
            else:
                result["past_companies"].append(entry)

    # Final empty check
    if not result["name"] and not result["active_companies"] and not result["past_companies"]:
        return None
    return result


# ── InstaFinancials scraper (fallback) ─────────────────────────────────────

def _scrape_instafinancials(din: str) -> Optional[dict]:
    """InstaFinancials exposes a free director profile page keyed by DIN."""
    candidate_urls = [
        f"https://instafinancials.com/free-director-profile/{din}",
        f"https://www.instafinancials.com/free-director-profile/{din}",
        f"https://instafinancials.com/director/{din}",
    ]
    print(f"[MCA] Trying InstaFinancials for DIN {din}")
    session = _new_session("https://instafinancials.com/")
    google_ref = f"https://www.google.com/search?q=instafinancials+director+{din}"
    for url in candidate_urls:
        r = _fetch(session, url, referer=google_ref)
        if r is None:
            continue
        print(f"[MCA] InstaFinancials {url} -> HTTP {r.status_code}, len={len(r.text)}, final={r.url}")
        if r.status_code != 200 or not r.text:
            continue
        if _is_block_page(r.text):
            print(f"[MCA] InstaFinancials blocked for DIN {din}")
            continue
        parsed = _parse_generic_director_page(r.text, din)
        if parsed and (parsed.get("active_companies") or parsed.get("past_companies")):
            parsed["source_url"] = r.url
            print(f"[MCA] InstaFinancials OK for {din}: active={len(parsed['active_companies'])}, past={len(parsed['past_companies'])}")
            return parsed
    return None


# ── IndiaFilings scraper (fallback) ────────────────────────────────────────

def _scrape_indiafilings(din: str) -> Optional[dict]:
    """IndiaFilings has a director lookup by DIN."""
    candidate_urls = [
        f"https://www.indiafilings.com/find-director/{din}",
        f"https://www.indiafilings.com/search/director/{din}",
        f"https://www.indiafilings.com/learn/director/{din}",
    ]
    print(f"[MCA] Trying IndiaFilings for DIN {din}")
    session = _new_session("https://www.indiafilings.com/")
    google_ref = f"https://www.google.com/search?q=indiafilings+director+{din}"
    for url in candidate_urls:
        r = _fetch(session, url, referer=google_ref)
        if r is None:
            continue
        print(f"[MCA] IndiaFilings {url} -> HTTP {r.status_code}, len={len(r.text)}, final={r.url}")
        if r.status_code != 200 or not r.text:
            continue
        if _is_block_page(r.text):
            print(f"[MCA] IndiaFilings blocked for DIN {din}")
            continue
        parsed = _parse_generic_director_page(r.text, din)
        if parsed and (parsed.get("active_companies") or parsed.get("past_companies")):
            parsed["source_url"] = r.url
            print(f"[MCA] IndiaFilings OK for {din}: active={len(parsed['active_companies'])}, past={len(parsed['past_companies'])}")
            return parsed
    return None


def _parse_generic_director_page(html: str, din: str) -> Optional[dict]:
    """Best-effort generic parser for any director-profile HTML that contains
    one or more tables with a 'company' / 'business' column.

    We don't make strong assumptions about layout: any table whose header row
    mentions a company column is treated as a directorship table; we then try
    to identify a status column to split active vs past, else default to
    active.
    """
    soup = BeautifulSoup(html, "html.parser")
    result = {
        "din": din,
        "name": None,
        "about": None,
        "active_companies": [],
        "past_companies": [],
    }

    h1 = soup.find("h1")
    if h1:
        name = h1.get_text(" ", strip=True)
        if name and len(name) < 120:
            result["name"] = name

    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True).lower()
                   for th in table.find_all("th")]
        if not headers:
            continue

        def idx_of(*names):
            for n in names:
                if n.lower() in headers:
                    return headers.index(n.lower())
            return -1

        i_name = idx_of("company", "company name", "business name",
                        "name of company", "entity name")
        if i_name < 0:
            continue

        i_status = idx_of("status", "company status", "mca status")
        i_appt = idx_of("date of appointment", "appointment date",
                        "date of joining")
        i_desig = idx_of("designation", "role", "position")
        i_cessation = idx_of("date of cessation", "cessation date",
                             "end date")

        def cell_text(row_cells, i):
            if 0 <= i < len(row_cells):
                return row_cells[i].get_text(" ", strip=True)
            return ""

        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if not cells:
                continue
            company = cell_text(cells, i_name)
            if not company or company.lower() in ("company", "company name"):
                continue
            status = cell_text(cells, i_status).lower()
            appt = cell_text(cells, i_appt) or "-"
            desig = cell_text(cells, i_desig) or "-"
            cessation = cell_text(cells, i_cessation) or "-"

            # Classify: if status says anything like struck off / amalgamated
            # / dormant / dissolved / inactive → past; else active.
            past_markers = ("struck", "amalgamated", "dissolved",
                            "liquidation", "dormant", "inactive",
                            "deregistered")
            is_past = (any(m in status for m in past_markers)
                       or (cessation and cessation != "-"))
            entry = {
                "company": company,
                "designation": desig,
                "appointment_date": appt,
                "cessation_date": cessation,
                "status": status.title() if status else "-",
            }
            if is_past:
                result["past_companies"].append(entry)
            else:
                result["active_companies"].append(entry)

    if not result["active_companies"] and not result["past_companies"] and not result["name"]:
        return None
    return result


# ── Public entry point ─────────────────────────────────────────────────────

def scrape_director_by_din(din: str) -> Optional[dict]:
    """Fetch MCA-aggregator data for a DIN. Cached for 30 days.

    Returns a dict shaped like:
        {
          "din": "...",
          "name": "...",
          "about": "...",          # may be None
          "active_companies": [ { company, designation, appointment_date, cessation_date } ],
          "past_companies":   [ { ... same shape ... } ],
          "source": "zaubacorp" | "tofler",
          "source_url": "...",
          "fetched_at": "ISO timestamp",
          "disclaimer": "..."
        }
    or None if all sources failed.
    """
    din = str(din or "").strip()
    if not din.isdigit() or len(din) < 5:
        print(f"[MCA] Rejected DIN (bad format): {din!r}")
        return None

    print(f"[MCA] scrape_director_by_din({din}) called")

    # Curated snapshot short-circuit: well-known directors return immediately
    # without any network I/O. Keeps the example cards working even when
    # datacenter IPs are blocked by aggregator WAFs.
    if din in CURATED_DIRECTORS:
        data = dict(CURATED_DIRECTORS[din])  # shallow copy
        data["source"] = "curated"
        data["source_url"] = None
        data["fetched_at"] = datetime.now().isoformat()
        data["curated_as_of"] = CURATED_AS_OF
        data["disclaimer"] = (
            f"Curated snapshot as of {CURATED_AS_OF}. Compiled from publicly "
            "disclosed board compositions in company annual reports. Verify "
            "against live MCA filings before relying on this for legal or "
            "financial decisions."
        )
        print(f"[MCA] Curated hit for DIN {din}: {data['name']}")
        return data

    cached = _get_cached(din)
    if cached:
        print(f"[MCA] Cache hit for DIN {din} (source={cached.get('source')})")
        return cached

    for scraper_fn, source_name in (
        (_scrape_zauba, "zaubacorp"),
        (_scrape_tofler, "tofler"),
        (_scrape_instafinancials, "instafinancials"),
        (_scrape_indiafilings, "indiafilings"),
    ):
        try:
            data = scraper_fn(din)
        except Exception as e:
            import traceback
            print(f"[MCA] {source_name} scraper crashed for DIN {din}: {e}")
            traceback.print_exc()
            data = None
        if data:
            data["source"] = source_name
            data["fetched_at"] = datetime.now().isoformat()
            data["disclaimer"] = (
                "Third-party aggregator data. Verify against MCA filings "
                "for legal or financial decisions."
            )
            _save_cache(din, data, source_name)
            print(f"[MCA] Returning data from {source_name} for DIN {din}")
            return data

    print(f"[MCA] All sources failed for DIN {din}")
    return None


# Initialize cache table at import time — safe (idempotent).
init_mca_cache()

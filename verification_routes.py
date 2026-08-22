"""Where each obligation is actually filed, and therefore how it gets verified.

Delta ingests NSE/BSE corporate announcements. That covers SEBI LODR, PIT and
SAST submissions — but not MCA forms (MSME-1, PAS-6, DPT-3), not TDS returns,
and not RBI/FEMA filings. Those go to MCA21, TRACES and FLAIR respectively.

Presenting all of them as "no filing data" reads as a failure of the platform,
when for most of them there is no exchange record to find. This maps each
regime to how it can be confirmed and where, so the UI can ask the right
question instead of implying something is missing.
"""

from typing import Any, Dict

# regime -> verification route
ROUTES: Dict[str, Dict[str, Any]] = {
    "SEBI": {
        "key": "exchange",
        "label": "Exchange filing",
        "auto": True,
        "where": "NSE / BSE corporate announcements",
        "how": ("Delta reads exchange announcements directly, so these can be "
                "confirmed automatically. Some are submitted as XBRL or as a "
                "certificate rather than a titled announcement, which is why a "
                "match is not always found."),
        "url": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
        # The in-app button is already "Cross-check"; this link is the manual
        # route, so it needs a distinct label.
        "action": "Open NSE filings",
    },
    "MCA": {
        "key": "mca",
        "label": "Registrar of Companies",
        "auto": False,
        "where": "MCA21 portal",
        "how": ("Filed with the Registrar of Companies, not the exchanges. There "
                "is no exchange record for Delta to match — confirm from the "
                "company's MCA filing history or your own records."),
        "url": "https://www.mca.gov.in/mcafoportal/viewPublicDocumentsFilter.do",
        "action": "Open MCA21",
    },
    "FEMA": {
        "key": "rbi",
        "label": "RBI / FEMA",
        "auto": False,
        "where": "RBI FIRMS / FLAIR portal",
        "how": ("Filed with the Reserve Bank of India. Not visible in exchange "
                "announcements — confirm from the FLAIR portal acknowledgement."),
        "url": "https://firms.rbi.org.in/firms/faces/pages/login.xhtml",
        "action": "Open FLAIR",
    },
    "Tax & Labour": {
        "key": "tax",
        "label": "Tax / Labour",
        "auto": False,
        "where": "Income Tax portal / TRACES",
        "how": ("Filed with the tax authorities. Not visible in exchange "
                "announcements — confirm from the TRACES acknowledgement or "
                "your tax advisor's records."),
        "url": "https://www.tdscpc.gov.in/app/login",
        "action": "Open TRACES",
    },
}

DEFAULT = {
    "key": "other", "label": "Other", "auto": False,
    "where": "the relevant authority",
    "how": "Confirm from the filing acknowledgement.",
    "url": "", "action": "Mark confirmed",
}


def route_for(regime: str) -> Dict[str, Any]:
    return ROUTES.get(regime or "", DEFAULT)


def route_for_mandate(mandate_id: str) -> Dict[str, Any]:
    try:
        from compliance import SEBI_MANDATES
        by_id = {m["id"]: m for m in SEBI_MANDATES}
    except Exception:
        return DEFAULT
    return route_for((by_id.get(mandate_id) or {}).get("regime", ""))


def summarise(obligations) -> Dict[str, Any]:
    """Split a set of unverified obligations by how each can be confirmed."""
    buckets: Dict[str, Dict[str, Any]] = {}
    for ob in obligations:
        r = route_for_mandate(ob.get("mandate_id", ""))
        b = buckets.setdefault(r["key"], {**r, "items": []})
        b["items"].append(ob)
    # Exchange-verifiable first — that is the bucket Delta can actually close.
    order = {"exchange": 0, "mca": 1, "rbi": 2, "tax": 3, "other": 4}
    return {
        "buckets": sorted(buckets.values(), key=lambda b: order.get(b["key"], 9)),
        "auto_count": sum(len(b["items"]) for b in buckets.values() if b["auto"]),
        "manual_count": sum(len(b["items"]) for b in buckets.values() if not b["auto"]),
    }

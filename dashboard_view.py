"""Read-only presentation data for the warm dashboard design."""
from collections import Counter
from datetime import date, datetime, timedelta
from database import get_connection

COMPANIES = {
    "HINDUNILVR": {"name": "Hindustan Unilever", "logo": "HINDUNILVR.png"},
    "ITC": {"name": "ITC", "logo": "ITC.svg"},
    "NESTLEIND": {"name": "Nestlé India", "logo": "NESTLEIND.png"},
    "BRITANNIA": {"name": "Britannia Industries", "logo": "BRITANNIA.svg"},
    "DABUR": {"name": "Dabur India", "logo": "DABUR.png"},
}


def build_dashboard_data(symbols=None):
    symbols = list(dict.fromkeys(symbols if symbols is not None else COMPANIES))
    if not symbols:
        return {"total": 0, "companies": [], "filings": [], "months": [], "days": [],
                "tags": [], "latest": None, "latest_label": "No cached filings", "window_label": "No data"}
    connection = get_connection()
    try:
        rows = [dict(row) for row in connection.execute(
            "SELECT id,company,title,date,sort_date,pdf_link,tags,has_xbrl "
            "FROM filings WHERE company IN (" + ",".join("?" for _ in symbols) + ") "
            "ORDER BY sort_date DESC, id DESC", symbols
        )]
    finally:
        connection.close()
    dated = []
    for row in rows:
        try:
            parsed = date.fromisoformat(row["sort_date"][:10])
        except (ValueError, TypeError):
            continue
        dated.append((parsed, row))
    anchor = max((item[0] for item in dated), default=date.today())
    months = []
    for offset in range(11, -1, -1):
        index = anchor.year * 12 + anchor.month - 1 - offset
        year, month = divmod(index, 12)
        stamp = date(year, month + 1, 1)
        months.append({"key": stamp.strftime("%Y-%m"), "label": stamp.strftime("%b"),
                       "full": stamp.strftime("%B %Y"), "count": 0})
    month_map = {item["key"]: item for item in months}
    day_counts = Counter(parsed.isoformat() for parsed, _ in dated)
    for parsed, _ in dated:
        month = month_map.get(parsed.strftime("%Y-%m"))
        if month:
            month["count"] += 1
    days = []
    for offset in range(89, -1, -1):
        day = anchor - timedelta(days=offset)
        days.append({"date": day.isoformat(), "label": day.strftime("%d %b"),
                     "count": day_counts[day.isoformat()]})
    tag_counts = Counter(tag.strip() for row in rows for tag in (row["tags"] or "").split(",") if tag.strip())
    company_counts = Counter(row["company"] for row in rows)
    filings = []
    for row in rows[:240]:
        try:
            short_date = datetime.fromisoformat(row["sort_date"]).strftime("%d %b %Y")
        except (ValueError, TypeError):
            short_date = row["date"]
        filings.append({"id": row["id"], "company": row["company"], "title": row["title"],
                        "date": short_date, "iso": (row["sort_date"] or "")[:10],
                        "url": row["pdf_link"] or "", "tags": [tag.strip() for tag in (row["tags"] or "").split(",") if tag.strip()],
                        "xbrl": bool(row["has_xbrl"])})
    companies = [{"symbol": symbol, "name": COMPANIES.get(symbol, {}).get("name", symbol),
                  "logo": COMPANIES.get(symbol, {}).get("logo", ""), "count": company_counts[symbol]}
                 for symbol in symbols]
    return {"total": len(rows), "companies": companies, "filings": filings,
            "months": months, "days": days,
            "tags": [{"name": tag, "count": count} for tag, count in tag_counts.most_common(5)],
            "latest": anchor.isoformat() if dated else None,
            "latest_label": anchor.strftime("%d %b %Y") if dated else "No cached filings",
            "window_label": f"{months[0]['full']} – {months[-1]['full']}",
            "window_count": sum(item["count"] for item in months)}

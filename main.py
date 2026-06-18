from flask import Flask, render_template, jsonify, redirect, url_for, request, session, flash, send_from_directory, send_file, abort
from functools import wraps
from database import (
    init_db, get_cached_filings, filing_exists, insert_filing,
    get_previous_snapshot_ids, save_snapshot, get_filing_by_id, find_best_match,
    update_filing_summary, search_filings, count_filings, get_paged_filings,
    filter_filings_by_tag, search_din_across_companies,
    get_all_known_companies,
    init_watchlist, get_watchlist, add_to_watchlist, remove_from_watchlist,
    get_setting, set_setting, get_all_settings,
    replace_watchlist_with_industry, INDUSTRY_WATCHLISTS,
    set_user_industry,
    get_filings_last_fetched, set_filings_last_fetched,
    get_oldest_filing_date, get_newest_filing_date,
    batch_insert_filings, get_existing_filing_keys,
    seed_default_referral_codes, can_redeem_referral, redeem_referral,
    record_payment_intent, mark_paid, get_payment_status, is_user_paid,
)
from payments import (
    razorpay_configured, public_key_id, create_order,
    verify_payment_signature, ACCESS_FEE_PAISE,
)
from supabase_client import (
    supabase,
    supabase_admin,
    get_user_by_id as sb_get_user_by_id,
    update_user_metadata as sb_update_user_metadata,
)
import math
import threading
from fetch import fetch_filings, search_nse_symbols
from governance import fetch_governance_reports_list, fetch_governance_report, parse_governance_report, compare_governance_reports, search_din_in_governance
from mca_scraper import scrape_director_by_din
from pdf_parser import download_and_extract, extract_xbrl_from_pdf, parse_xbrl_content, _cache_path
from ai import detect_tags, summarize_text, GEMINI_MODELS
from compliance import (
    init_compliance_db, refresh_compliance, get_compliance_dashboard,
    get_company_compliance, get_all_mandates, get_last_synced,
)
from sebi_enforcement import (
    init_enforcement_db, refresh_enforcement_for_company,
    refresh_enforcement_async,
    get_enforcement_for_company, get_enforcement_summaries,
)
from price_alerts import (
    init_alerts_db, create_alert, list_alerts, delete_alert, toggle_alert,
    evaluate_user_alerts, fire_test_alert, telegram_configured,
    telegram_bot_username, verify_telegram_auth, send_telegram,
)
from draft_engine import (
    get_filing_types, find_past_examples, generate_draft, improve_text,
    build_letter_data, render_pdf, render_docx, export_filename,
    learn_company_style, get_cached_style, ai_fill_placeholders,
)
from drafts_store import (
    init_drafts_db, save_draft, update_draft, get_draft,
    list_user_drafts, delete_draft,
    list_team, add_collaborator, update_collaborator_role,
    remove_collaborator, create_invite, get_invite, revoke_invite,
    claim_invite, get_effective_role, get_teams_overview,
)
from database import (
    set_user_telegram as db_set_user_telegram,
    get_user_telegram as db_get_user_telegram,
    unlink_user_telegram as db_unlink_user_telegram,
)
from diff_engine import compute_diff, diff_to_html, side_by_side_html
from stock_prices import (
    fetch_watchlist_prices, fetch_stock_quote, detect_price_alerts, analyze_price_movement,
    fetch_pre_open_data, detect_pre_open_alerts,
    fetch_sector_data, detect_sector_alerts, analyze_sector_movement,
)
from industry_intel import (
    get_industry_intel, list_supported_industries, INDUSTRY_PROFILES,
)
from company_logos import SYMBOL_DOMAIN_MAP, SYMBOL_TV_SLUG_MAP
from logo_scraper import discover_logo, discover_logo_with_meta
import os

app = Flask(__name__)
app.secret_key = os.environ.get("DELTA_SECRET_KEY", "delta-filings-dev-secret-change-in-prod-7k2j9x")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ── Auth helpers ──────────────────────────────────────────

def current_user():
    """Return the currently signed-in user as a dict, or None.

    We cache the minimal user profile in the Flask session after sign-in so
    every request doesn't hit Supabase. When the session cache is missing
    (e.g. after deploy), we fall back to Supabase admin API.
    """
    uid = session.get("user_id")
    if not uid:
        return None

    cached = session.get("user_cache")
    if cached and cached.get("id") == uid:
        return cached

    user = sb_get_user_by_id(uid)
    if user:
        session["user_cache"] = user
    return user


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("signin_page", next=request.path))
        if not user.get("onboarded"):
            return redirect(url_for("onboarding_page"))
        # Payment gate — every authed feature route is paywalled.
        # Bypass: the checkout page itself, payment APIs, logout.
        if not is_user_paid(user["id"]):
            return redirect(url_for("checkout_page"))
        return f(*args, **kwargs)
    return wrapper


@app.context_processor
def inject_user():
    # ui_version: 'legacy' (sidebar layout, default) or 'new' (top-nav redesign).
    return {
        "current_user": current_user(),
        "ui_version": get_setting("ui_version", "legacy"),
    }


def _current_user_id():
    """Return the signed-in user's id (from session) or None."""
    u = current_user()
    return u["id"] if u else None


def get_companies():
    """Return list of watchlist symbols for the CURRENT signed-in user.

    Safety net: if the user has an industry set but their watchlist is empty
    (e.g. they cleared it, or the seed failed at onboarding), auto-seed the
    top 5 companies for their industry so the dashboard is never empty.
    """
    uid = _current_user_id()
    if not uid:
        return []
    symbols = [w["symbol"] for w in get_watchlist(uid)]
    if symbols:
        return symbols

    # Watchlist is empty — try to re-seed from the user's stored industry.
    user = current_user() or {}
    industry = (user.get("industry") or "").strip()
    if industry and industry in INDUSTRY_WATCHLISTS:
        try:
            seeded = replace_watchlist_with_industry(uid, industry)
            if seeded:
                print(f"[DASHBOARD] Auto-seeded watchlist for user {uid} from industry '{industry}': {seeded}")
                return seeded
        except Exception as e:
            print(f"[DASHBOARD] Auto-seed failed for {uid}/{industry}: {e}")
    return []


# Track in-flight filing backfills so a user mashing refresh / opening the
# company page in two tabs doesn't fire two parallel NSE scrapes for the
# same symbol.
_filings_inflight = set()
_filings_inflight_lock = threading.Lock()


def maybe_backfill_filings_async(symbol):
    """Kick off a full filings re-fetch in the background if we either have
    a thin cache (<50 rows) or haven't refreshed in 6+ hours. Safe to call
    on every page view — the in-flight lock + the meta timestamp keep us
    well under NSE's rate limit."""
    sym = symbol.upper()

    # Decide whether to refresh.
    cached_count = count_filings(sym)
    meta = get_filings_last_fetched(sym)
    should_refresh = False
    if not meta:
        should_refresh = True
    else:
        try:
            last = datetime.fromisoformat(meta["last_fetched_at"])
            age_hours = (datetime.now() - last).total_seconds() / 3600.0
        except Exception:
            age_hours = 999
        if cached_count < 50 and age_hours > 0.5:
            # Thin cache + at least 30 min since the last attempt → try again.
            should_refresh = True
        elif age_hours > 6:
            # Otherwise refresh hourly-ish for already-populated symbols.
            should_refresh = True

    # Shallow-history trigger: caches built before the windowed fetch were
    # truncated by NSE's cloud-IP cap (~1000 most-recent rows). If our oldest
    # cached filing is shallower than the configured depth, we're missing
    # history — do a one-time deep re-pull (throttled to once per 2h).
    target_years = _history_years()
    if not should_refresh and cached_count > 0:
        try:
            oldest = get_oldest_filing_date(sym)
            recently_tried = False
            if meta:
                try:
                    last = datetime.fromisoformat(meta["last_fetched_at"])
                    recently_tried = (datetime.now() - last).total_seconds() < 2 * 3600
                except Exception:
                    recently_tried = False
            if oldest and not recently_tried:
                oldest_dt = datetime.strptime(oldest[:10], "%Y-%m-%d")
                years_deep = (datetime.now() - oldest_dt).days / 365.0
                if years_deep < (target_years - 0.5):
                    print(f"[MAIN] {sym} cache only {years_deep:.1f}y deep "
                          f"(want {target_years}y) — deep re-pull")
                    should_refresh = True
        except Exception as e:
            print(f"[MAIN] shallow-history check failed for {sym}: {e}")

    if not should_refresh:
        return False

    with _filings_inflight_lock:
        if sym in _filings_inflight:
            return False
        _filings_inflight.add(sym)

    def _worker():
        try:
            process_filings(sym)
        except Exception as e:
            print(f"[MAIN] async filings backfill failed for {sym}: {e}")
        finally:
            with _filings_inflight_lock:
                _filings_inflight.discard(sym)

    threading.Thread(target=_worker, name=f"filings-backfill-{sym}",
                     daemon=True).start()
    return True


def _history_years():
    """Default fetch depth (years) from global settings; clamped 1–15.
    Defaults to 3 years — a CS mostly works the recent cycle; deeper history
    is one click away in Settings or the Disclosures depth control."""
    try:
        y = int(get_setting("filings_history_years", "3"))
        return max(1, min(y, 15))
    except (TypeError, ValueError):
        return 3


def process_filings(symbol, years=None):
    """Fetch and store filings. Returns (new_ids, removed_ids) compared to last snapshot."""
    if years is None:
        years = _history_years()
    print(f"\n[MAIN] Processing filings for {symbol} ({years}y depth)...")

    old_ids = get_previous_snapshot_ids(symbol)

    # allow_fallback=False so a transient NSE outage never silently inserts
    # the hardcoded INFY sample data under another company's symbol.
    raw_filings = fetch_filings(symbol, years=years, allow_fallback=False)
    if not raw_filings:
        print(f"[MAIN] NSE returned no filings for {symbol} — skipping insert")
        return set(), set()
    # Stamp the successful fetch so the company page knows when we last
    # touched NSE for this symbol and can avoid redundant backfills.
    try:
        set_filings_last_fetched(symbol, len(raw_filings))
    except Exception as e:
        print(f"[MAIN] filings_meta upsert failed for {symbol}: {e}")

    # Dedupe in memory (one query) instead of N filing_exists() round-trips.
    existing_keys = get_existing_filing_keys(symbol)

    to_insert = []
    pdf_extract_count = 0
    for filing in raw_filings:
        title = filing["title"]
        date = filing["date"]
        if (title, date) in existing_keys:
            continue

        # Only extract PDF text for the first 20 new filings to keep this
        # path fast; the rest get extracted on-demand when the user opens
        # them. Network round-trip per PDF dominates everything else here.
        raw_text = filing.get("raw_text", "")
        if not raw_text and filing.get("pdf_link") and pdf_extract_count < 20:
            raw_text = download_and_extract(filing["pdf_link"])
            if raw_text:
                pdf_extract_count += 1

        description = filing.get("description", "")
        tags_str = ",".join(detect_tags(title, raw_text or description))

        to_insert.append({
            "company": symbol,
            "title": title,
            "description": description,
            "date": date,
            "sort_date": filing.get("sort_date", date),
            "pdf_link": filing.get("pdf_link", ""),
            "raw_text": raw_text,
            "tags": tags_str,
            "has_xbrl": filing.get("has_xbrl", False),
        })

    inserted = batch_insert_filings(to_insert) if to_insert else 0
    print(f"[MAIN] {symbol}: {inserted} new filings inserted in 1 transaction "
          f"({len(raw_filings)} fetched, {len(raw_filings) - len(to_insert)} dedupe-skip).")

    current_filings = get_cached_filings(symbol)
    current_ids = {f["id"] for f in current_filings}

    new_ids = current_ids - old_ids
    removed_ids = old_ids - current_ids

    save_snapshot(symbol)
    return new_ids, removed_ids


@app.route("/")
def index():
    """Public landing page. Authenticated users go straight to their dashboard."""
    user = current_user()
    if user and user.get("onboarded"):
        return redirect(url_for("dashboard"))
    if user and not user.get("onboarded"):
        return redirect(url_for("onboarding_page"))
    return render_template("home.html")


@app.route("/about")
def about_page():
    """Public about page describing every platform capability."""
    return render_template("about.html")


# ── Public: Blog ──────────────────────────────────────────

@app.route("/blog")
def blog_index_page():
    """Public blog index. Anyone can read, no auth needed."""
    from blog_posts import (
        all_posts, all_categories, modules_with_posts, featured_post,
        articles_only, news_only,
    )
    articles = sorted(articles_only(), key=lambda p: p["published"], reverse=True)
    news = news_only()  # already sorted newest first
    return render_template(
        "blog_index.html",
        posts=sorted(all_posts(), key=lambda p: p["published"], reverse=True),
        articles=articles,
        news=news,
        categories=all_categories(),
        modules=modules_with_posts(),
        featured=featured_post(),
    )


@app.route("/blog/<slug>")
def blog_article_page(slug):
    """Public blog article page."""
    from blog_posts import post_by_slug, all_posts, modules_with_posts, featured_post, all_categories
    post = post_by_slug(slug)
    if not post:
        from blog_posts import articles_only, news_only
        articles = sorted(articles_only(), key=lambda p: p["published"], reverse=True)
        news = news_only()
        return render_template(
            "blog_index.html",
            posts=sorted(all_posts(), key=lambda p: p["published"], reverse=True),
            articles=articles,
            news=news,
            categories=all_categories(),
            modules=modules_with_posts(),
            featured=featured_post(),
            not_found=slug,
        ), 404
    # Related posts: same category, exclude self
    related = [p for p in all_posts() if p["category"] == post["category"] and p["slug"] != slug][:3]
    return render_template("blog_article.html", post=post, related=related)


# ── Public: Tools ─────────────────────────────────────────

@app.route("/tools")
def tools_index_page():
    """Public tools index — list of CS calculators."""
    from tools_data import TOOLS
    return render_template("tools_index.html", tools=TOOLS)


@app.route("/tools/<slug>")
def tools_detail_page(slug):
    """Public single-tool calculator page. Each tool has its own template."""
    from tools_data import (
        tool_by_slug, ROC_FORMS, GST_RETURNS, GST_INTEREST_RATE,
        MSME_LIMITS, COMPANY_REG_COST, TDS_SECTIONS,
        SHARE_TRANSFER_RATE, STAMP_DUTY_STATES,
        GST_RATES, GRATUITY, HRA, INCOME_TAX,
    )
    tool = tool_by_slug(slug)
    if not tool:
        from tools_data import TOOLS
        return render_template("tools_index.html", tools=TOOLS, not_found=slug), 404

    # Each tool template gets a uniform context plus its own data subset.
    ctx = {"tool": tool}
    if slug == "roc-late-fee":
        ctx["forms"] = ROC_FORMS
        template = "tools/roc_late_fee.html"
    elif slug == "gst-late-fee":
        ctx["returns"] = GST_RETURNS
        ctx["interest_rate"] = GST_INTEREST_RATE
        template = "tools/gst_late_fee.html"
    elif slug == "din-kyc-late-fee":
        template = "tools/din_kyc_late_fee.html"
    elif slug == "msme-classification":
        ctx["limits"] = MSME_LIMITS
        template = "tools/msme_classification.html"
    elif slug == "company-registration-cost":
        ctx["cost_table"] = COMPANY_REG_COST
        template = "tools/company_registration_cost.html"
    elif slug == "tds-rate-finder":
        ctx["sections"] = TDS_SECTIONS
        template = "tools/tds_rate.html"
    elif slug == "stamp-duty":
        ctx["share_rate"] = SHARE_TRANSFER_RATE
        ctx["states"] = STAMP_DUTY_STATES
        template = "tools/stamp_duty.html"
    elif slug == "gst-calculator":
        ctx["rates"] = GST_RATES
        template = "tools/gst_calculator.html"
    elif slug == "gratuity":
        ctx["cfg"] = GRATUITY
        template = "tools/gratuity.html"
    elif slug == "hra-exemption":
        ctx["cfg"] = HRA
        template = "tools/hra_exemption.html"
    elif slug == "income-tax":
        ctx["cfg"] = INCOME_TAX
        template = "tools/income_tax.html"
    else:
        from tools_data import TOOLS
        return render_template("tools_index.html", tools=TOOLS, not_found=slug), 404

    return render_template(template, **ctx)


@app.route("/favicon.ico")
def favicon():
    """Serve the SVG spark mark for the browser's default favicon request
    (covers any page whose <head> doesn't include the explicit <link>)."""
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "favicon.svg", mimetype="image/svg+xml",
    )


# ── Auth: Sign Up ─────────────────────────────────────────

@app.route("/signup", methods=["GET", "POST"])
def signup_page():
    # Already logged in? send onwards.
    user = current_user()
    if user:
        return redirect(url_for("onboarding_page") if not user.get("onboarded") else url_for("dashboard"))

    form = {"name": "", "email": ""}
    error = None

    if request.method == "POST":
        form["name"] = (request.form.get("name") or "").strip()
        form["email"] = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        # Basic validation mirroring the old flow.
        if not form["name"] or not form["email"] or not password:
            error = "All fields are required."
        elif "@" not in form["email"] or "." not in form["email"].split("@")[-1]:
            error = "Please enter a valid email address."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        else:
            try:
                result = supabase.auth.sign_up({
                    "email": form["email"],
                    "password": password,
                    "options": {"data": {"name": form["name"], "onboarded": False}},
                })
                if not result or not result.user:
                    error = "Sign up failed. Please try again."
                else:
                    # Preserve any pending draft-invite token across the
                    # session.clear() so the user lands in their first
                    # collaborative draft right after signup.
                    pending_invite = session.get("pending_invite")
                    session.clear()
                    if pending_invite:
                        session["pending_invite"] = pending_invite
                    session["user_id"] = result.user.id
                    session["user_cache"] = {
                        "id": result.user.id,
                        "email": result.user.email,
                        "name": form["name"],
                        "industry": "",
                        "onboarded": False,
                    }
                    session.permanent = True
                    # Claim the invite synchronously so onboarding skip + a
                    # fresh redirect lands the user on the right draft.
                    invited_draft = _claim_pending_invite_if_any(result.user.id)
                    if invited_draft:
                        # Fast-track: invite implies they're a working
                        # member, no need for the industry-onboarding gate.
                        session["user_cache"]["onboarded"] = True
                        try:
                            sb_update_user_metadata(result.user.id, {"onboarded": True})
                        except Exception as e:
                            print(f"[SIGNUP] meta update warn: {e}")
                        return redirect(url_for("draft_page"))
                    return redirect(url_for("onboarding_page"))
            except Exception as e:
                msg = str(e).lower()
                if "already" in msg or "exists" in msg or "registered" in msg:
                    error = "This email is already registered. Please sign in instead."
                else:
                    error = "Sign up failed: " + str(e)

    return render_template("signup.html", form=form, error=error)


# ── Auth: Sign In ─────────────────────────────────────────

@app.route("/signin", methods=["GET", "POST"])
def signin_page():
    user = current_user()
    if user:
        return redirect(url_for("onboarding_page") if not user.get("onboarded") else url_for("dashboard"))

    form = {"email": ""}
    error = None
    notice = request.args.get("notice")

    if request.method == "POST":
        form["email"] = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        authed = None
        auth_unreachable = False
        try:
            result = supabase.auth.sign_in_with_password({
                "email": form["email"],
                "password": password,
            })
            if result and result.user:
                meta = result.user.user_metadata or {}
                authed = {
                    "id": result.user.id,
                    "email": result.user.email,
                    "name": meta.get("name", ""),
                    "industry": meta.get("industry", ""),
                    "onboarded": bool(meta.get("onboarded", False)),
                }
        except Exception as e:
            msg = str(e).lower()
            # Distinguish "auth backend down/unreachable" from "wrong password".
            # A bad credential surfaces as a Supabase AuthApiError (400); a
            # network/DNS failure surfaces as a connection error — those must
            # NOT be reported to the user as an invalid password.
            if any(s in msg for s in ("resolve", "connection", "timed out", "timeout",
                                      "nodename", "max retries", "getaddrinfo",
                                      "name or service", "temporarily")):
                auth_unreachable = True
            print(f"[SIGNIN] supabase error ({'unreachable' if auth_unreachable else 'auth'}): {e}")

        if auth_unreachable:
            error = ("Sign-in service is temporarily unreachable. This is on our side, "
                     "not your password — please try again in a few minutes.")
        elif not authed:
            error = "Invalid email or password. Please try again."
        else:
            # Preserve a pending draft-invite token across session.clear()
            # so the user lands directly in the shared draft after signin.
            pending_invite = session.get("pending_invite")
            session.clear()
            if pending_invite:
                session["pending_invite"] = pending_invite
            session["user_id"] = authed["id"]
            session["user_cache"] = authed
            session.permanent = True
            invited_draft = _claim_pending_invite_if_any(authed["id"])
            if invited_draft:
                return redirect(url_for("draft_page"))
            nxt = request.args.get("next") or request.form.get("next")
            if authed.get("onboarded"):
                return redirect(nxt or url_for("dashboard"))
            return redirect(url_for("onboarding_page"))

    return render_template("signin.html", form=form, error=error, notice=notice)


# ── Auth: Logout ──────────────────────────────────────────

@app.route("/logout")
def logout():
    try:
        supabase.auth.sign_out()
    except Exception as e:
        print(f"[LOGOUT] supabase sign_out warning: {e}")
    session.clear()
    # Land on the public homepage (`/`) instead of the sign-in page so the
    # marketing landing is the first thing the user sees after signing out.
    # The session is already cleared, so the index route will render
    # home.html (it only redirects to /dashboard when a user is authed).
    return redirect(url_for("index"))


# ── Onboarding: Industry → Watchlist ──────────────────────

@app.route("/onboarding", methods=["GET", "POST"])
def onboarding_page():
    user = current_user()
    if not user:
        return redirect(url_for("signin_page"))

    # Already onboarded? skip the picker — but still gate on payment.
    if user.get("onboarded"):
        if not is_user_paid(user["id"]):
            return redirect(url_for("checkout_page"))
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        industry = (request.form.get("industry") or "").strip()
        company = (request.form.get("company") or "").strip()[:120]
        designation = (request.form.get("designation") or "").strip()[:120]
        if industry not in INDUSTRY_WATCHLISTS:
            error = "Please choose one of the listed industries to continue."
        else:
            symbols = replace_watchlist_with_industry(user["id"], industry)
            sb_update_user_metadata(user["id"], {
                "industry": industry,
                "company": company,
                "designation": designation,
                "onboarded": True,
            })
            session["user_cache"] = {
                **user, "industry": industry, "company": company,
                "designation": designation, "onboarded": True,
            }

            try:
                if symbols:
                    process_filings(symbols[0])
            except Exception as e:
                print(f"[ONBOARDING] pre-load warning: {e}")
            try:
                refresh_compliance()
            except Exception as e:
                print(f"[ONBOARDING] compliance refresh warning: {e}")

            # Payment gate — every new user must clear checkout before
            # the dashboard. Existing paid users get fast-tracked.
            if is_user_paid(user["id"]):
                return redirect(url_for("dashboard"))
            return redirect(url_for("checkout_page"))

    return render_template(
        "onboarding.html",
        user=user,
        industries=INDUSTRY_WATCHLISTS,
        error=error,
    )


# ── Payment gate (Razorpay) ────────────────────────────────

@app.route("/checkout", methods=["GET"])
def checkout_page():
    """SaaS-style pricing/checkout page. Shown right after onboarding for
    new users; also accessible to anyone signed in but not yet paid."""
    user = current_user()
    if not user:
        return redirect(url_for("signin_page", next="/checkout"))
    if not user.get("onboarded"):
        return redirect(url_for("onboarding_page"))
    if is_user_paid(user["id"]):
        return redirect(url_for("dashboard"))
    return render_template(
        "checkout.html",
        user=user,
        amount_paise=ACCESS_FEE_PAISE,
        amount_inr=ACCESS_FEE_PAISE / 100,
        razorpay_enabled=razorpay_configured(),
        razorpay_key=public_key_id(),
    )


@app.route("/api/payment/create-order", methods=["POST"])
def api_payment_create_order():
    user = current_user()
    if not user:
        return jsonify({"error": True, "message": "Not signed in"}), 401
    if is_user_paid(user["id"]):
        return jsonify({"error": True, "message": "Already paid."}), 400
    if not razorpay_configured():
        return jsonify({
            "error": True,
            "message": "Payment gateway is not configured. Use a referral code instead.",
        }), 503
    receipt = f"delta_{user['id'][:18]}"
    order = create_order(
        amount_paise=ACCESS_FEE_PAISE,
        receipt=receipt,
        notes={"user_id": user["id"], "email": user.get("email", "")},
    )
    if not order or "id" not in order:
        return jsonify({"error": True, "message": "Couldn't create order. Try again."}), 502
    record_payment_intent(user["id"], order["id"], ACCESS_FEE_PAISE)
    return jsonify({
        "status": "ok",
        "order_id": order["id"],
        "amount": order.get("amount", ACCESS_FEE_PAISE),
        "currency": order.get("currency", "INR"),
        "key_id": public_key_id(),
        "name": "Delta Filings",
        "description": "Annual access — Corporate Governance Intelligence",
        "prefill": {
            "name": user.get("name", ""),
            "email": user.get("email", ""),
        },
    })


@app.route("/api/payment/verify", methods=["POST"])
def api_payment_verify():
    user = current_user()
    if not user:
        return jsonify({"error": True, "message": "Not signed in"}), 401
    data = request.get_json() or {}
    order_id = (data.get("razorpay_order_id") or "").strip()
    payment_id = (data.get("razorpay_payment_id") or "").strip()
    sig = (data.get("razorpay_signature") or "").strip()
    if not (order_id and payment_id and sig):
        return jsonify({"error": True, "message": "Missing payment fields"}), 400
    if not verify_payment_signature(order_id, payment_id, sig):
        return jsonify({"error": True, "message": "Signature verification failed."}), 400
    mark_paid(user["id"], payment_id=payment_id, order_id=order_id,
              amount=ACCESS_FEE_PAISE)
    try:
        sb_update_user_metadata(user["id"], {"paid": True})
    except Exception as e:
        print(f"[PAY] supabase paid metadata sync failed: {e}")
    cached = session.get("user_cache") or {}
    session["user_cache"] = {**cached, "paid": True}
    return jsonify({"status": "ok", "paid": True})


@app.route("/api/payment/redeem-referral", methods=["POST"])
def api_payment_redeem_referral():
    user = current_user()
    if not user:
        return jsonify({"error": True, "message": "Not signed in"}), 401
    if is_user_paid(user["id"]):
        return jsonify({"error": True, "message": "Already paid."}), 400
    data = request.get_json() or {}
    code = (data.get("code") or "").strip().upper()
    if not code:
        return jsonify({"error": True, "message": "Enter a referral code."}), 400
    if not redeem_referral(user["id"], code):
        return jsonify({"error": True,
                        "message": "Invalid, exhausted, or already-used code."}), 400
    mark_paid(user["id"], referral_code=code, amount=0)
    try:
        sb_update_user_metadata(user["id"], {"paid": True, "referral_code": code})
    except Exception as e:
        print(f"[PAY] supabase referral metadata sync failed: {e}")
    cached = session.get("user_cache") or {}
    session["user_cache"] = {**cached, "paid": True, "referral_code": code}
    return jsonify({"status": "ok", "paid": True, "via_referral": True})


@app.route("/dashboard")
@login_required
def dashboard():
    """Main dashboard with corporate governance calendar across all watchlist companies."""
    # Get current month/year from query params or default to now
    from datetime import datetime, date
    import calendar as cal_mod

    year = request.args.get("year", None, type=int)
    month = request.args.get("month", None, type=int)
    today = date.today()
    if not year or not month:
        year, month = today.year, today.month

    # Clamp
    if month < 1:
        month, year = 12, year - 1
    elif month > 12:
        month, year = 1, year + 1

    # Get all filings for all companies
    all_filings = []
    company_counts = {}
    companies = get_companies()
    for sym in companies:
        filings = get_cached_filings(sym)
        company_counts[sym] = len(filings)
        for f in filings:
            all_filings.append(f)

    # Group filings by date (YYYY-MM-DD) for the calendar
    from collections import defaultdict
    day_map = defaultdict(list)  # "2026-03-20" -> [filing, ...]
    month_filings = []

    for f in all_filings:
        sd = f.get("sort_date", "")
        if not sd:
            continue
        try:
            dt = datetime.strptime(sd[:10], "%Y-%m-%d")
        except ValueError:
            continue

        if dt.year == year and dt.month == month:
            day_map[dt.day].append({
                "id": f["id"],
                "company": f["company"],
                "title": f["title"],
                "date": f["date"],
                "tags": f.get("tags", ""),
                "sort_date": sd,
            })
            month_filings.append({
                "id": f["id"],
                "company": f["company"],
                "title": f["title"],
                "date": f["date"],
                "tags": f.get("tags", ""),
                "sort_date": sd,
            })

    # Sort month_filings by sort_date desc
    month_filings.sort(key=lambda x: x["sort_date"], reverse=True)

    # ── Extra stats for the enriched dashboard ─────────────
    from datetime import timedelta
    week_ago = today - timedelta(days=7)
    days_90_ago = today - timedelta(days=90)

    filings_this_week = 0
    last_filing_date = None
    tag_counts = defaultdict(int)

    for f in all_filings:
        sd = f.get("sort_date", "")
        if not sd:
            continue
        try:
            fd = datetime.strptime(sd[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        if fd >= week_ago:
            filings_this_week += 1
        if last_filing_date is None or fd > last_filing_date:
            last_filing_date = fd

        for tag in (f.get("tags") or "").split(","):
            t = tag.strip()
            if t:
                tag_counts[t] += 1

    # Last 10 filings across the whole watchlist for the "Recent activity" feed
    recent_filings = [f for f in all_filings if f.get("sort_date")]
    recent_filings.sort(key=lambda x: x["sort_date"], reverse=True)
    recent_filings = [{
        "id": f["id"], "company": f["company"], "title": f["title"],
        "date": f["date"], "tags": f.get("tags", ""), "sort_date": f["sort_date"],
    } for f in recent_filings[:10]]

    # Top tags for the breakdown chart
    top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:6]
    total_tags = sum(tag_counts.values()) or 1

    # ── 12-month filing-activity trend (for the dashboard area chart) ──
    import calendar as _cal
    months_seq = []
    _y, _m = today.year, today.month
    for i in range(11, -1, -1):
        mm, yy = _m - i, _y
        while mm <= 0:
            mm += 12
            yy -= 1
        months_seq.append((yy, mm))
    counts_map = {key: 0 for key in months_seq}
    for f in all_filings:
        sd = f.get("sort_date", "")
        if not sd:
            continue
        try:
            dt = datetime.strptime(sd[:7], "%Y-%m")
        except ValueError:
            continue
        key = (dt.year, dt.month)
        if key in counts_map:
            counts_map[key] += 1
    filing_trend = [
        {"label": _cal.month_abbr[mm], "full": f"{_cal.month_abbr[mm]} {yy}", "count": counts_map[(yy, mm)]}
        for (yy, mm) in months_seq
    ]

    # ── Daily filing activity (last 60 days) for the dot-matrix chart ──
    # `actual`    = exact filing count for that day
    # `projected` = same day-of-week average across the window (unbiased — Mon/Tue/Wed
    #               etc each get their own baseline; no fudge multiplier)
    # Dates are bucketed in IST because NSE/MCA timestamps are wall-clock IST and the
    # Railway host runs UTC; without this, a 23:00 IST filing on day N would land on
    # day N-1 of the chart's local-time grid.
    from datetime import timedelta as _td
    try:
        from zoneinfo import ZoneInfo as _ZI
        _IST = _ZI("Asia/Kolkata")
    except Exception:  # noqa: BLE001 — pre-3.9 fallback (unlikely)
        _IST = None
    today_ist = (datetime.now(_IST).date() if _IST else date.today())

    DAILY_WINDOW = 60
    window_start = today_ist - _td(days=DAILY_WINDOW - 1)
    daily_actual = {window_start + _td(days=i): 0 for i in range(DAILY_WINDOW)}
    _dropped_dates = 0
    for f in all_filings:
        sd = f.get("sort_date", "")
        if not sd:
            continue
        try:
            d = datetime.strptime(sd[:10], "%Y-%m-%d").date()
        except ValueError:
            _dropped_dates += 1
            continue
        if d in daily_actual:
            daily_actual[d] += 1
    if _dropped_dates:
        print(f"[filing_activity] dropped {_dropped_dates} rows with unparseable sort_date")

    # Day-of-week averages across the window (0 = Mon)
    by_dow = {i: [] for i in range(7)}
    for d, c in daily_actual.items():
        by_dow[d.weekday()].append(c)
    dow_avg = {dow: (sum(vs) / len(vs)) if vs else 0.0 for dow, vs in by_dow.items()}

    # Heaviest day-of-week label, used in the AI insight bubble
    _dow_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    _busiest_dow = max(dow_avg.items(), key=lambda kv: kv[1])[0] if any(dow_avg.values()) else 4
    busiest_day_name = _dow_names[_busiest_dow]
    busiest_day_avg = round(dow_avg.get(_busiest_dow, 0), 1)

    filing_activity = []
    for d in sorted(daily_actual.keys()):
        filing_activity.append({
            "iso": d.isoformat(),
            "label": d.strftime("%b %d"),
            "full": d.strftime("%a, %b %d"),
            "dow": _dow_names[d.weekday()][:3],
            "actual": daily_actual[d],
            "projected": round(dow_avg[d.weekday()], 1),  # honest DOW average, no bias
        })

    # Freshness signals for the panel header / insight bubble.
    _activity_total = sum(daily_actual.values())
    _latest_day_with_data = max((d for d, c in daily_actual.items() if c > 0), default=None)
    activity_total = _activity_total
    activity_freshness_label = (
        _latest_day_with_data.strftime("%b %d, %Y") if _latest_day_with_data else None
    )
    activity_stale_days = (
        (today_ist - _latest_day_with_data).days if _latest_day_with_data else None
    )

    # Detail-view dataset: every filing that lands inside the chart window, so the
    # "Expand" modal can drill down by company / tag / day. Cap at 1,000 to keep
    # the payload reasonable on huge watchlists.
    activity_filings = []
    for f in all_filings:
        sd = f.get("sort_date", "")
        if not sd:
            continue
        try:
            d = datetime.strptime(sd[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (window_start <= d <= today_ist):
            continue
        activity_filings.append({
            "id": f.get("id"),
            "company": f.get("company") or "",
            "title": f.get("title") or "",
            "date": f.get("date") or sd[:10],
            "iso": d.isoformat(),
            "sort_date": sd,
            "time": sd[11:16] if len(sd) >= 16 else "",
            "tags": [t.strip() for t in (f.get("tags") or "").split(",") if t.strip()],
            "url": f.get("url") or f.get("link") or "",
        })
    # Newest first
    activity_filings.sort(key=lambda x: x["sort_date"], reverse=True)
    activity_filings = activity_filings[:1000]

    # Per-company counts for the company filter chips inside the modal.
    from collections import Counter as _Counter
    _company_counter = _Counter(f["company"] for f in activity_filings if f["company"])
    activity_companies = [
        {"symbol": sym, "count": cnt} for sym, cnt in _company_counter.most_common()
    ]
    _tag_counter = _Counter(t for f in activity_filings for t in f["tags"])
    activity_tags = [
        {"tag": t, "count": cnt} for t, cnt in _tag_counter.most_common(20)
    ]

    # Build calendar grid
    first_weekday, num_days = cal_mod.monthrange(year, month)
    # first_weekday: 0=Mon
    cal_weeks = []
    current_week = [None] * first_weekday
    for d in range(1, num_days + 1):
        events = day_map.get(d, [])
        is_today = (d == today.day and month == today.month and year == today.year)
        current_week.append({"day": d, "events": events, "is_today": is_today})
        if len(current_week) == 7:
            cal_weeks.append(current_week)
            current_week = []
    if current_week:
        while len(current_week) < 7:
            current_week.append(None)
        cal_weeks.append(current_week)

    month_name = cal_mod.month_name[month]
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    user = current_user()
    user_industry = (user or {}).get("industry") or "FMCG"
    if user_industry not in INDUSTRY_PROFILES:
        user_industry = "FMCG"

    days_since_last = None
    if last_filing_date:
        days_since_last = (today - last_filing_date).days

    _ui = get_setting("ui_version", "legacy")
    _dash_template = "dashboard.html" if _ui == "new" else "dashboard_legacy.html"
    return render_template(
        _dash_template,
        companies=companies,
        watchlist=get_watchlist(_current_user_id()),
        company_counts=company_counts,
        cal_weeks=cal_weeks,
        month_name=month_name,
        year=year,
        month=month,
        prev_month=prev_month, prev_year=prev_year,
        next_month=next_month, next_year=next_year,
        month_filings=month_filings,
        total_filings=len(all_filings),
        month_count=len(month_filings),
        day_map=day_map,
        now_hour=datetime.now().hour,
        # Enriched dashboard data
        filings_this_week=filings_this_week,
        days_since_last=days_since_last,
        recent_filings=recent_filings,
        top_tags=top_tags,
        total_tags=total_tags,
        filing_trend=filing_trend,
        filing_activity=filing_activity,
        busiest_day_name=busiest_day_name,
        busiest_day_avg=busiest_day_avg,
        activity_total=activity_total,
        activity_freshness_label=activity_freshness_label,
        activity_stale_days=activity_stale_days,
        activity_filings=activity_filings,
        activity_companies=activity_companies,
        activity_tags=activity_tags,
        user_industry=user_industry,
        symbol_domains=SYMBOL_DOMAIN_MAP,
        symbol_tv_slugs=SYMBOL_TV_SLUG_MAP,
    )


PER_PAGE = 15


# ── Local company logos ──────────────────────────────────────────
# Two-tier storage so a logo is fetched at most once, ever:
#   1. static/logos/<SYM>.<ext>  — logos committed to the repo (authoritative).
#   2. <volume>/logos/<SYM>.<ext> — logos downloaded at runtime, persisted on
#      Railway's /data volume so they survive deploys and are never re-pulled.
# Never hot-link external favicons in the UI.

_LOGO_EXTS = ("svg", "png", "webp", "jpg", "jpeg")
_logo_fetch_inflight = set()
_logo_fetch_lock = threading.Lock()

# Persistent volume for runtime-downloaded logos (Railway mounts one at /data).
# Falls back to a local dir when no volume is present (dev).
_VOLUME_ROOT = "/data" if os.path.isdir("/data") and os.access("/data", os.W_OK) else os.path.join(os.path.dirname(__file__), ".logocache")
LOGO_VOLUME_DIR = os.path.join(_VOLUME_ROOT, "logos")
try:
    os.makedirs(LOGO_VOLUME_DIR, exist_ok=True)
except Exception as e:  # noqa: BLE001
    print(f"[LOGO] could not create volume logo dir: {e}")

# Deterministic letter-avatar colors (indigo→pink span, AA-contrast on white text)
_AVATAR_COLORS = [
    "#4f46e5", "#0891b2", "#7c3aed", "#0d9488", "#db2777",
    "#ea580c", "#2563eb", "#059669", "#9333ea", "#e11d48",
]


def avatar_color(symbol):
    return _AVATAR_COLORS[sum(ord(c) for c in symbol) % len(_AVATAR_COLORS)]


def local_logo_url(symbol):
    sym = symbol.upper()
    # 1) Committed repo logos win (curated, e.g. TCS).
    logo_dir = os.path.join(app.static_folder, "logos")
    for ext in _LOGO_EXTS:
        if os.path.exists(os.path.join(logo_dir, f"{sym}.{ext}")):
            return f"/static/logos/{sym}.{ext}"
    # 2) Then volume-persisted downloads (served via /companylogo route).
    for ext in _LOGO_EXTS:
        if os.path.exists(os.path.join(LOGO_VOLUME_DIR, f"{sym}.{ext}")):
            return f"/companylogo/{sym}.{ext}"
    return None


@app.route("/companylogo/<path:fname>")
def serve_volume_logo(fname):
    """Serve a runtime-downloaded logo from the persistent volume. Cached hard
    by the browser since these never change once written."""
    # Guard against path traversal — only allow simple <SYM>.<ext> names.
    safe = os.path.basename(fname)
    path = os.path.join(LOGO_VOLUME_DIR, safe)
    if not os.path.exists(path):
        abort(404)
    resp = send_file(path)
    resp.headers["Cache-Control"] = "public, max-age=2592000"  # 30 days
    return resp


def ensure_local_logo_async(symbol):
    """If no local logo exists, try to download one in the background."""
    sym = symbol.upper()
    if local_logo_url(sym):
        return
    with _logo_fetch_lock:
        if sym in _logo_fetch_inflight:
            return
        _logo_fetch_inflight.add(sym)

    def _save_logo(content, ctype):
        ext = ("svg" if "svg" in ctype else
               "png" if "png" in ctype else
               "webp" if "webp" in ctype else
               "jpg" if ("jpeg" in ctype or "jpg" in ctype) else None)
        if not ext or not content:
            return False
        # Persist to the volume so it survives deploys and is never re-pulled.
        os.makedirs(LOGO_VOLUME_DIR, exist_ok=True)
        with open(os.path.join(LOGO_VOLUME_DIR, f"{sym}.{ext}"), "wb") as fh:
            fh.write(content)
        print(f"[LOGO] saved {sym} logo to volume ({ext}, {len(content)} bytes)")
        return True

    def _worker():
        try:
            import requests
            UA = {"User-Agent": "Mozilla/5.0"}

            # 1) TradingView CDN — clean vector logos for nearly every NSE
            #    listed company. Resolve the logoid via symbol search, then
            #    pull the SVG from s3-symbol-logo.tradingview.com.
            try:
                from fetch import search_nse_symbols
                hits = search_nse_symbols(sym, max_results=5)
                logoid = next((h.get("logoid") for h in hits
                               if h.get("symbol", "").upper() == sym and h.get("logoid")), "")
                if not logoid and hits:
                    logoid = hits[0].get("logoid", "")
                if logoid:
                    tv = requests.get(
                        f"https://s3-symbol-logo.tradingview.com/{logoid}--big.svg",
                        timeout=10, headers=UA)
                    if tv.status_code == 200 and tv.content and _save_logo(tv.content, "svg"):
                        return
            except Exception as e:
                print(f"[LOGO] TradingView logo path failed for {sym}: {e}")

            # 2) Fallback: scrape the company's own site for a brand image.
            from logo_scraper import discover_logo_with_meta
            meta = discover_logo_with_meta(sym) or {}
            url = meta.get("url")
            if not url:
                return
            resp = requests.get(url, timeout=10, headers=UA)
            if resp.status_code != 200 or not resp.content:
                return
            _save_logo(resp.content, (resp.headers.get("Content-Type") or "").lower())
        except Exception as e:  # noqa: BLE001 — best-effort fetch
            print(f"[LOGO] fetch failed for {sym}: {e}")
        finally:
            with _logo_fetch_lock:
                _logo_fetch_inflight.discard(sym)

    threading.Thread(target=_worker, name=f"logo-fetch-{sym}", daemon=True).start()


@app.route("/company/<symbol>")
@login_required
def company_view(symbol):
    symbol = symbol.upper()
    companies = get_companies()
    # Allow viewing any company (even if not on watchlist yet — they may have just added it)

    # Kick off a background filings backfill so the user doesn't have to
    # hit refresh manually. Cheap when the cache is fresh; only hits NSE
    # when the cache is thin or stale (see maybe_backfill_filings_async).
    backfill_started = maybe_backfill_filings_async(symbol)

    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    total = count_filings(symbol)
    total_pages = max(1, math.ceil(total / PER_PAGE))
    if page > total_pages:
        page = total_pages

    filings = get_paged_filings(symbol, page=page, per_page=PER_PAGE)
    prev_ids = get_previous_snapshot_ids(symbol)

    for f in filings:
        f["is_new"] = (f["id"] not in prev_ids) if prev_ids else False
        f["has_text"] = bool(f.get("raw_text", "").strip())

    company_counts = {sym: count_filings(sym) for sym in companies}

    # Coverage: the actual date span of cached filings, for the header readout.
    oldest = get_oldest_filing_date(symbol)
    newest = get_newest_filing_date(symbol)
    coverage_years = None
    if oldest and newest:
        try:
            o = datetime.strptime(oldest[:10], "%Y-%m-%d")
            n = datetime.strptime(newest[:10], "%Y-%m-%d")
            coverage_years = round(max(0.0, (n - o).days / 365.0), 1)
        except Exception:
            coverage_years = None

    # Local logo map for the switcher; background-fetch any that are missing.
    _logo_syms = set(companies) | {symbol}
    logo_map = {}
    for _s in _logo_syms:
        u = local_logo_url(_s)
        if u:
            logo_map[_s] = u
        else:
            ensure_local_logo_async(_s)
    avatar_colors = {_s: avatar_color(_s) for _s in _logo_syms}

    return render_template(
        "index.html", companies=companies, selected=symbol, filings=filings,
        page=page, total_pages=total_pages, total=total, per_page=PER_PAGE,
        watchlist=get_watchlist(_current_user_id()), company_counts=company_counts,
        active_page="company",
        backfill_started=backfill_started,
        symbol_domains=SYMBOL_DOMAIN_MAP,
        logo_map=logo_map,
        avatar_colors=avatar_colors,
        cov_oldest=(oldest[:10] if oldest else None),
        cov_newest=(newest[:10] if newest else None),
        cov_years=coverage_years,
        history_years=_history_years(),
    )


@app.route("/api/filing/<int:filing_id>")
def api_filing(filing_id):
    """Return filing details including full extracted text.
    If text not yet extracted, extract on demand."""
    filing = get_filing_by_id(filing_id)
    if not filing:
        return jsonify({"error": "Filing not found"}), 404

    text = (filing.get("raw_text") or "").strip()

    # On-demand PDF extraction if text is missing
    if not text and filing.get("pdf_link"):
        print(f"[MAIN] On-demand extraction for filing #{filing_id}")
        text = download_and_extract(filing["pdf_link"])
        if text:
            from database import get_connection
            conn = get_connection()
            conn.execute("UPDATE filings SET raw_text = ? WHERE id = ?", (text, filing_id))
            conn.commit()
            conn.close()

    return jsonify({
        "id": filing["id"],
        "title": filing["title"],
        "description": filing.get("description", ""),
        "date": filing["date"],
        "company": filing["company"],
        "tags": filing.get("tags", ""),
        "pdf_link": filing.get("pdf_link", ""),
        "has_text": bool(text.strip()),
        "has_xbrl": bool(filing.get("has_xbrl")),
        "summary": filing.get("summary", ""),
        "text": text,
    })


@app.route("/api/tags/<symbol>")
def api_tags(symbol):
    """Return all unique tags and their counts for a company."""
    symbol = symbol.upper()
    filings = get_cached_filings(symbol)
    tag_counts = {}
    for f in filings:
        for tag in (f.get("tags") or "").split(","):
            tag = tag.strip()
            if tag:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
    # Sort by count descending
    sorted_tags = sorted(tag_counts.items(), key=lambda x: -x[1])
    return jsonify({"tags": [{"name": t, "count": c} for t, c in sorted_tags]})


@app.route("/api/summarize/<int:filing_id>", methods=["POST"])
def api_summarize(filing_id):
    """Generate or return cached AI summary for a filing."""
    filing = get_filing_by_id(filing_id)
    if not filing:
        return jsonify({"error": "Filing not found"}), 404

    # Return cached summary if available (unless force refresh requested)
    force = request.args.get("force", "0") == "1"
    existing = (filing.get("summary") or "").strip()
    api_key = get_setting("gemini_api_key", "")

    # Auto-regenerate if API key is now set but cached summary was from fallback
    is_fallback_summary = existing and existing.startswith("•")
    if existing and not force and not (api_key and is_fallback_summary):
        return jsonify({"summary": existing, "cached": True})

    text = (filing.get("raw_text") or "").strip()
    if not text:
        return jsonify({"error": "no_text", "message": "No text available to summarize."})

    model = get_setting("gemini_model", "gemini-2.5-flash")
    temperature = 0.7 if force else 0.3
    summary = summarize_text(text, filing["title"], filing.get("description", ""),
                             api_key=api_key, model=model, temperature=temperature)
    update_filing_summary(filing_id, summary)

    is_ai = bool(api_key)
    return jsonify({"summary": summary, "cached": False, "ai": is_ai, "model": model})


@app.route("/api/xbrl/<int:filing_id>")
def api_xbrl(filing_id):
    """Extract and return XBRL data from a filing's PDF if available."""
    filing = get_filing_by_id(filing_id)
    if not filing:
        return jsonify({"error": "Filing not found"}), 404

    if not filing.get("has_xbrl"):
        return jsonify({"error": "no_xbrl", "message": "This filing has no XBRL data."})

    pdf_url = filing.get("pdf_link", "")
    if not pdf_url:
        return jsonify({"error": "no_pdf", "message": "No PDF available for XBRL extraction."})

    cached = _cache_path(pdf_url)
    if not os.path.exists(cached):
        # Try downloading
        download_and_extract(pdf_url)

    if not os.path.exists(cached):
        return jsonify({"error": "download_failed", "message": "Could not download PDF."})

    xbrl_files = extract_xbrl_from_pdf(cached)
    if not xbrl_files:
        # NSE flags `hasXbrl` on the announcement, but the structured XBRL is
        # filed on NSE's separate XBRL module — it is NOT embedded in the
        # announcement PDF. So there is nothing to extract here. Rather than a
        # dead-end error, return the filing's own readable content + links.
        text = (filing.get("raw_text") or "").strip()
        if not text and pdf_url:
            text = (download_and_extract(pdf_url) or "").strip()
        return jsonify({
            "info": True,
            "title": filing.get("title", ""),
            "date": filing.get("date", ""),
            "company": filing.get("company", ""),
            "pdf_link": pdf_url,
            "text": text[:6000],
            "nse_portal": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
        })

    all_facts = []
    for xf in xbrl_files:
        facts = parse_xbrl_content(xf["content"])
        all_facts.append({
            "file_name": xf["name"],
            "facts": facts,
            "raw_preview": xf["content"][:1000],
        })

    return jsonify({"xbrl_data": all_facts})


@app.route("/api/search/<symbol>")
def api_search(symbol):
    """Search filings for a company. Supports ?q=text, ?tag=TagName, ?page=N."""
    symbol = symbol.upper()
    query = request.args.get("q", "").strip()
    tag_filter = request.args.get("tag", "").strip()
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1

    if query:
        filings, total = search_filings(symbol, query, tag=tag_filter or None, page=page, per_page=PER_PAGE)
    elif tag_filter:
        filings, total = filter_filings_by_tag(symbol, tag_filter, page=page, per_page=PER_PAGE)
    else:
        total = count_filings(symbol)
        filings = get_paged_filings(symbol, page=page, per_page=PER_PAGE)

    total_pages = max(1, math.ceil(total / PER_PAGE))

    prev_ids = get_previous_snapshot_ids(symbol)
    results = []
    for f in filings:
        results.append({
            "id": f["id"],
            "title": f["title"],
            "description": f.get("description", ""),
            "date": f["date"],
            "sort_date": f.get("sort_date", ""),
            "tags": f.get("tags", ""),
            "has_text": bool((f.get("raw_text") or "").strip()),
            "has_xbrl": bool(f.get("has_xbrl")),
            "is_new": (f["id"] not in prev_ids) if prev_ids else False,
            "summary": f.get("summary", ""),
        })

    return jsonify({
        "results": results, "query": query, "tag": tag_filter,
        "count": total, "page": page, "total_pages": total_pages, "per_page": PER_PAGE,
    })


@app.route("/api/compare/<int:filing_id>")
def api_compare(filing_id):
    """Smart-match a previous filing and return the diff."""
    filing = get_filing_by_id(filing_id)
    if not filing:
        return jsonify({"error": "Filing not found"}), 404

    current_text = (filing.get("raw_text") or "").strip()
    if not current_text:
        return jsonify({
            "error": "no_text",
            "message": "Document not available — no text could be extracted from this filing.",
        })

    match, match_reason = find_best_match(filing_id)
    if not match:
        return jsonify({
            "error": "no_match",
            "message": "No previous filing available for comparison.",
        })

    match_text = (match.get("raw_text") or "").strip()
    if not match_text:
        return jsonify({
            "error": "no_text",
            "message": f"Previous filing \"{match['title']}\" has no extractable text.",
        })

    inline_html = diff_to_html(compute_diff(match_text, current_text))
    sbs_html = side_by_side_html(match_text, current_text)

    return jsonify({
        "current": {
            "id": filing["id"],
            "title": filing["title"],
            "date": filing["date"],
        },
        "previous": {
            "id": match["id"],
            "title": match["title"],
            "date": match["date"],
        },
        "match_reason": match_reason or "",
        "inline_diff": inline_html,
        "side_by_side_diff": sbs_html,
    })


@app.route("/api/compare/<int:id_a>/<int:id_b>")
def api_compare_manual(id_a, id_b):
    """Compare two specific filings chosen by the user."""
    filing_a = get_filing_by_id(id_a)
    filing_b = get_filing_by_id(id_b)
    if not filing_a or not filing_b:
        return jsonify({"error": True, "message": "One or both filings not found."}), 404

    text_a = (filing_a.get("raw_text") or "").strip()
    text_b = (filing_b.get("raw_text") or "").strip()

    # On-demand extraction if needed
    if not text_a and filing_a.get("pdf_link"):
        text_a = download_and_extract(filing_a["pdf_link"])
        if text_a:
            from database import get_connection
            conn = get_connection()
            conn.execute("UPDATE filings SET raw_text = ? WHERE id = ?", (text_a, id_a))
            conn.commit()
            conn.close()

    if not text_b and filing_b.get("pdf_link"):
        text_b = download_and_extract(filing_b["pdf_link"])
        if text_b:
            from database import get_connection
            conn = get_connection()
            conn.execute("UPDATE filings SET raw_text = ? WHERE id = ?", (text_b, id_b))
            conn.commit()
            conn.close()

    if not text_a:
        return jsonify({"error": True, "message": f"\"{filing_a['title']}\" has no extractable text."})
    if not text_b:
        return jsonify({"error": True, "message": f"\"{filing_b['title']}\" has no extractable text."})

    # Older filing is "previous", newer is "current" by sort_date
    if filing_a.get("sort_date", "") >= filing_b.get("sort_date", ""):
        current, previous = filing_a, filing_b
        current_text, prev_text = text_a, text_b
    else:
        current, previous = filing_b, filing_a
        current_text, prev_text = text_b, text_a

    inline_html = diff_to_html(compute_diff(prev_text, current_text))
    sbs_html = side_by_side_html(prev_text, current_text)

    return jsonify({
        "current": {"id": current["id"], "title": current["title"], "date": current["date"]},
        "previous": {"id": previous["id"], "title": previous["title"], "date": previous["date"]},
        "match_reason": "Manual comparison",
        "inline_diff": inline_html,
        "side_by_side_diff": sbs_html,
    })


@app.route("/api/filings_list/<symbol>")
def api_filings_list(symbol):
    """Return a compact list of all filings for the picker, grouped by month."""
    symbol = symbol.upper()
    filings = get_cached_filings(symbol)
    result = []
    for f in filings:
        has_text = bool((f.get("raw_text") or "").strip())
        result.append({
            "id": f["id"],
            "title": f["title"],
            "date": f["date"],
            "sort_date": f.get("sort_date", ""),
            "tags": f.get("tags", ""),
            "has_text": has_text,
        })
    return jsonify({"filings": result})


@app.route("/api/refresh/<symbol>", methods=["POST"])
def refresh(symbol):
    symbol = symbol.upper()
    if symbol not in get_companies():
        return jsonify({"status": "error", "message": "Company not in watchlist"}), 400
    # Optional ?years=N (or JSON {years}) to pull a deeper/shallower span for
    # just this company — used by the Disclosures-page depth control.
    years = request.args.get("years", type=int)
    if years is None:
        years = (request.get_json(silent=True) or {}).get("years")
    try:
        new_ids, removed_ids = process_filings(symbol, years=years)
        return jsonify({
            "status": "ok",
            "new_count": len(new_ids),
            "removed_count": len(removed_ids),
            "total": count_filings(symbol),
        })
    except Exception as e:
        print(f"[MAIN] Refresh error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/symbol_search")
def api_symbol_search():
    """Search NSE for company symbols. Returns top matches."""
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"results": []})
    try:
        results = search_nse_symbols(q)
        return jsonify({"results": results})
    except Exception as e:
        print(f"[MAIN] Symbol search error: {e}")
        return jsonify({"results": []})


@app.route("/api/watchlist", methods=["GET"])
def api_watchlist_get():
    """Return the current user's watchlist."""
    uid = _current_user_id()
    if not uid:
        return jsonify({"error": True, "message": "Not signed in"}), 401
    wl = get_watchlist(uid)
    for w in wl:
        w["filing_count"] = count_filings(w["symbol"])
    return jsonify({"watchlist": wl})


@app.route("/api/watchlist", methods=["POST"])
def api_watchlist_add():
    """Add a company to the current user's watchlist and start fetching its filings."""
    uid = _current_user_id()
    if not uid:
        return jsonify({"error": True, "message": "Not signed in"}), 401

    data = request.get_json() or {}
    symbol = (data.get("symbol") or "").strip().upper()
    name = (data.get("name") or "").strip()
    if not symbol:
        return jsonify({"error": True, "message": "Symbol is required"}), 400

    added = add_to_watchlist(uid, symbol, name)
    if not added:
        return jsonify({"error": True, "message": f"{symbol} is already on your watchlist"})

    # Proactively download a local logo so it's ready before the user navigates
    # to the company / disclosures / compliance pages (best-effort, background).
    ensure_local_logo_async(symbol)

    # Start fetching filings for the new company in background
    new_count = 0
    warning = None
    try:
        new_ids, _ = process_filings(symbol)
        new_count = len(new_ids)
    except Exception as e:
        warning = str(e)

    # Generate compliance obligations + verify against SEBI/NSE/BSE so the
    # Compliance dashboard isn't empty for the newly-added company.
    try:
        refresh_compliance(symbol)
    except Exception as e:
        print(f"[WATCHLIST] compliance refresh failed for {symbol}: {e}")
        warning = warning or str(e)

    # Kick off SEBI enforcement scrape in a background thread. Hitting all
    # five SEBI order categories takes ~5-10s; we don't want to block the
    # watchlist-add response on it. The dashboard polls /api/compliance which
    # will pick up the data once the worker writes it.
    refresh_enforcement_async(symbol, name or symbol)

    resp = {"status": "ok", "symbol": symbol, "name": name, "new_count": new_count}
    if warning:
        resp["warning"] = warning
    return jsonify(resp)


@app.route("/api/watchlist/<symbol>", methods=["DELETE"])
def api_watchlist_remove(symbol):
    """Remove a company from the current user's watchlist."""
    uid = _current_user_id()
    if not uid:
        return jsonify({"error": True, "message": "Not signed in"}), 401
    symbol = symbol.upper()
    remove_from_watchlist(uid, symbol)
    return jsonify({"status": "ok", "removed": symbol})


@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    """Return current settings, available models, and industry options."""
    settings = get_all_settings()
    # Mask API key for display
    key = settings.get("gemini_api_key", "")
    masked = ""
    if key:
        masked = key[:4] + "•" * (len(key) - 8) + key[-4:] if len(key) > 8 else "•" * len(key)

    user = current_user() or {}
    return jsonify({
        "gemini_api_key_set": bool(key),
        "gemini_api_key_masked": masked,
        "gemini_model": settings.get("gemini_model", "gemini-2.5-flash"),
        "models": GEMINI_MODELS,
        "industry": user.get("industry", ""),
        "industries": sorted(INDUSTRY_WATCHLISTS.keys()),
        "ui_version": settings.get("ui_version", "legacy"),
        "filings_history_years": _history_years(),
    })


@app.route("/api/settings/industry", methods=["POST"])
@login_required
def api_settings_industry():
    """Switch the user's tracked industry. Replaces their watchlist with the
    new industry's default companies and kicks off background data refreshes
    (filings, compliance obligations, SEBI enforcement) for each."""
    uid = _current_user_id()
    if not uid:
        return jsonify({"error": True, "message": "Not signed in"}), 401

    data = request.get_json() or {}
    industry = (data.get("industry") or "").strip()
    if industry not in INDUSTRY_WATCHLISTS:
        return jsonify({"error": True, "message": f"Unknown industry: {industry}"}), 400

    # Replace watchlist atomically with the industry's defaults.
    symbols = replace_watchlist_with_industry(uid, industry)
    set_user_industry(uid, industry)

    # Persist to Supabase metadata + refresh session cache so the next
    # request sees the change without a re-login.
    try:
        sb_update_user_metadata(uid, {"industry": industry, "onboarded": True})
    except Exception as e:
        print(f"[SETTINGS] supabase metadata sync failed: {e}")
    cached = session.get("user_cache") or {}
    session["user_cache"] = {**cached, "industry": industry, "onboarded": True}

    # Pull each new company's data in the background — filings, compliance
    # obligations, SEBI enforcement. The dashboard polls /api/compliance and
    # will pick everything up as the workers finish.
    industry_companies = INDUSTRY_WATCHLISTS.get(industry, [])
    name_lookup = {sym: nm for sym, nm in industry_companies}
    for sym in symbols:
        nm = name_lookup.get(sym, sym)

        def _bg_pull(sym=sym, nm=nm):
            try:
                process_filings(sym)
            except Exception as e:
                print(f"[SETTINGS] filings refresh failed for {sym}: {e}")
            try:
                refresh_compliance(sym)
            except Exception as e:
                print(f"[SETTINGS] compliance refresh failed for {sym}: {e}")
            try:
                refresh_enforcement_for_company(sym, nm)
            except Exception as e:
                print(f"[SETTINGS] enforcement refresh failed for {sym}: {e}")

        threading.Thread(target=_bg_pull, name=f"industry-switch-{sym}",
                         daemon=True).start()

    return jsonify({
        "status": "ok",
        "industry": industry,
        "symbols": symbols,
    })


@app.route("/api/settings", methods=["POST"])
def api_settings_update():
    """Update settings."""
    data = request.get_json() or {}
    if "ui_version" in data:
        v = data["ui_version"] if data["ui_version"] in ("legacy", "new") else "legacy"
        set_setting("ui_version", v)
    if "filings_history_years" in data:
        try:
            y = max(1, min(int(data["filings_history_years"]), 15))
            set_setting("filings_history_years", str(y))
        except (TypeError, ValueError):
            return jsonify({"error": True, "message": "Invalid history years"}), 400
    if "gemini_api_key" in data:
        set_setting("gemini_api_key", data["gemini_api_key"].strip())
    if "gemini_model" in data:
        model = data["gemini_model"]
        if model in GEMINI_MODELS:
            set_setting("gemini_model", model)
        else:
            return jsonify({"error": True, "message": f"Unknown model: {model}"}), 400
    return jsonify({"status": "ok"})


@app.route("/api/settings/test-key", methods=["POST"])
def api_settings_test_key():
    """Test if the AI API key works. Detects the provider by key prefix:
    sk-or-…  → OpenRouter;  anything else → Google Gemini."""
    data = request.get_json() or {}
    api_key = data.get("api_key", "").strip()
    if not api_key:
        return jsonify({"valid": False, "message": "No API key provided."})

    if api_key.startswith("sk-or-"):
        # ── OpenRouter ──
        try:
            import requests as _rq
            r = _rq.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
            if r.status_code != 200:
                return jsonify({"valid": False, "message": f"OpenRouter rejected the key (HTTP {r.status_code})."})
            info = (r.json() or {}).get("data", {})
            tier = "free tier" if info.get("is_free_tier") else "paid"
            return jsonify({"valid": True, "message": f"OpenRouter key is valid ({tier})."})
        except Exception as e:
            return jsonify({"valid": False, "message": f"OpenRouter check failed: {e}"})

    # ── Google Gemini ──
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        client.models.generate_content(
            model="gemini-2.5-flash",
            contents="Say 'OK' in one word.",
            config={"max_output_tokens": 10},
        )
        return jsonify({"valid": True, "message": "Gemini key is valid!"})
    except Exception as e:
        return jsonify({"valid": False, "message": str(e)})


@app.route("/api/governance/<symbol>")
def api_governance_list(symbol):
    """Return list of available governance reports for a company."""
    symbol = symbol.upper()
    reports = fetch_governance_reports_list(symbol)
    return jsonify({"symbol": symbol, "reports": reports})


@app.route("/api/governance/<symbol>/<rec_id>")
def api_governance_report(symbol, rec_id):
    """Fetch and return a parsed governance report."""
    symbol = symbol.upper()
    raw = fetch_governance_report(symbol, rec_id)
    if not raw:
        return jsonify({"error": True, "message": "Could not fetch governance report."})
    parsed = parse_governance_report(raw)
    return jsonify({"symbol": symbol, "rec_id": rec_id, "report": parsed})


@app.route("/api/governance/compare/<symbol>/<rec_id_a>/<rec_id_b>")
def api_governance_compare(symbol, rec_id_a, rec_id_b):
    """Compare two governance reports."""
    symbol = symbol.upper()
    raw_a = fetch_governance_report(symbol, rec_id_a)
    raw_b = fetch_governance_report(symbol, rec_id_b)
    if not raw_a or not raw_b:
        return jsonify({"error": True, "message": "Could not fetch one or both reports."})
    parsed_a = parse_governance_report(raw_a)
    parsed_b = parse_governance_report(raw_b)
    changes = compare_governance_reports(parsed_a, parsed_b)
    return jsonify({
        "symbol": symbol,
        "report_a": {"rec_id": rec_id_a, "report": parsed_a},
        "report_b": {"rec_id": rec_id_b, "report": parsed_b},
        "changes": changes,
    })


# ── Stock Prices ──────────────────────────────────────────

@app.route("/prices")
@login_required
def prices_page():
    """Stock prices dashboard."""
    companies = get_companies()
    watchlist = get_watchlist(_current_user_id())
    user = current_user()
    user_industry = (user or {}).get("industry") or "FMCG"
    if user_industry not in INDUSTRY_PROFILES:
        user_industry = "FMCG"
    return render_template(
        "prices.html",
        companies=companies, watchlist=watchlist,
        user_industry=user_industry,
        symbol_domains=SYMBOL_DOMAIN_MAP,
        symbol_tv_slugs=SYMBOL_TV_SLUG_MAP,
    )


@app.route("/watchlist")
@login_required
def watchlist_page():
    """Full-page view of the user's entire watchlist (linked from the sidebar
    'View all' when the watchlist has more than 3 companies)."""
    companies = get_companies()
    watchlist = get_watchlist(_current_user_id())
    company_counts = {sym: count_filings(sym) for sym in companies}
    return render_template(
        "watchlist.html",
        companies=companies, watchlist=watchlist,
        company_counts=company_counts, active_page="watchlist",
    )


@app.route("/api/prices")
def api_prices():
    """Get live prices for all watchlist stocks. Also evaluates the user's
    custom alerts and includes any newly-fired ones in `fired_alerts` so
    the front-end can play a sound + show a browser notification (Telegram
    delivery already happened server-side if linked)."""
    symbols = get_companies()
    user = current_user()
    industry = (user or {}).get("industry")
    prices = fetch_watchlist_prices(symbols, industry=industry)

    # Built-in 3% movers (existing behavior)
    alerts = detect_price_alerts(prices, threshold=3.0)
    for alert in alerts:
        sym = alert["symbol"]
        recent = get_cached_filings(sym)[:10] if sym in symbols else []
        alert["reasons"] = analyze_price_movement(
            sym, alert["direction"], alert["pChange"], recent
        )

    # User-defined custom alerts — evaluate against current snapshot,
    # respecting per-alert cooldown and dispatching Telegram if linked.
    fired_alerts = []
    uid = _current_user_id()
    if uid:
        try:
            fired_alerts = evaluate_user_alerts(uid, prices)
        except Exception as e:
            print(f"[ALERT] evaluate_user_alerts failed for {uid}: {e}")

    return jsonify({
        "prices": prices,
        "alerts": alerts,
        "fired_alerts": fired_alerts,
        "telegram_configured": telegram_configured(),
        "symbols": symbols,
    })


@app.route("/api/alerts", methods=["GET"])
@login_required
def api_alerts_list():
    """Return all of the current user's price alerts plus their Telegram
    link status (for the 'Connect Telegram' widget in the alert modal)."""
    uid = _current_user_id()
    link = db_get_user_telegram(uid) or {}
    return jsonify({
        "alerts": list_alerts(uid),
        "telegram_configured": telegram_configured(),
        "telegram_bot_username": telegram_bot_username(),
        "telegram_link": {
            "linked": bool(link.get("chat_id")),
            "first_name": link.get("first_name", ""),
            "username": link.get("username", ""),
            "linked_at": link.get("linked_at", ""),
        },
    })


@app.route("/api/alerts/link-telegram", methods=["POST"])
@login_required
def api_alerts_link_telegram():
    """Verify a Telegram Login Widget callback and bind the user's chat_id.
    Frontend posts the entire widget payload (id, first_name, hash, etc.).
    We HMAC-verify it (so a malicious user can't paste someone else's id),
    then save the link. Future alerts auto-deliver to that chat_id."""
    uid = _current_user_id()
    payload = request.get_json() or {}
    ok, reason = verify_telegram_auth(payload)
    if not ok:
        return jsonify({"error": True, "message": reason}), 400

    chat_id = str(payload.get("id", ""))
    first_name = payload.get("first_name", "")
    username = payload.get("username", "")
    db_set_user_telegram(uid, chat_id, first_name, username)

    # Send a confirmation DM so the user immediately sees it worked.
    send_telegram(
        chat_id,
        f"✅ Linked to <b>Delta Filings</b>. You'll receive price alerts here.\n"
        f"<i>Hi {first_name or 'there'}!</i>",
    )
    return jsonify({
        "status": "ok",
        "linked": True,
        "first_name": first_name,
        "username": username,
    })


@app.route("/api/alerts/unlink-telegram", methods=["POST"])
@login_required
def api_alerts_unlink_telegram():
    uid = _current_user_id()
    db_unlink_user_telegram(uid)
    return jsonify({"status": "ok", "linked": False})


@app.route("/api/alerts", methods=["POST"])
@login_required
def api_alerts_create():
    """Create or upsert a price alert for the current user."""
    uid = _current_user_id()
    data = request.get_json() or {}
    symbol = (data.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": True, "message": "symbol is required"}), 400
    try:
        threshold = float(data.get("threshold_pct", 3.0))
    except (TypeError, ValueError):
        return jsonify({"error": True, "message": "threshold_pct must be a number"}), 400
    direction = (data.get("direction") or "either").strip().lower()
    chat_id = (data.get("telegram_chat_id") or "").strip()

    try:
        alert = create_alert(uid, symbol, threshold, direction, chat_id)
    except ValueError as e:
        return jsonify({"error": True, "message": str(e)}), 400
    return jsonify({"status": "ok", "alert": alert})


@app.route("/api/alerts/<int:alert_id>", methods=["DELETE"])
@login_required
def api_alerts_delete(alert_id):
    uid = _current_user_id()
    deleted = delete_alert(uid, alert_id)
    if not deleted:
        return jsonify({"error": True, "message": "Alert not found"}), 404
    return jsonify({"status": "ok", "id": alert_id})


@app.route("/api/alerts/<int:alert_id>/toggle", methods=["POST"])
@login_required
def api_alerts_toggle(alert_id):
    uid = _current_user_id()
    data = request.get_json() or {}
    enabled = bool(data.get("enabled", True))
    ok = toggle_alert(uid, alert_id, enabled)
    if not ok:
        return jsonify({"error": True, "message": "Alert not found"}), 404
    return jsonify({"status": "ok", "id": alert_id, "enabled": enabled})


@app.route("/api/alerts/test", methods=["POST"])
@login_required
def api_alerts_test():
    """Fire a synthetic alert so the user can verify sound + browser
    notification + (optionally) their Telegram link before real alerts hit."""
    uid = _current_user_id()
    data = request.get_json() or {}
    symbol = (data.get("symbol") or "TEST").strip().upper()
    chat_id = (data.get("telegram_chat_id") or "").strip()
    try:
        threshold = float(data.get("threshold_pct", 3.0))
    except (TypeError, ValueError):
        threshold = 3.0
    fired = fire_test_alert(uid, symbol, chat_id, threshold)
    return jsonify({"status": "ok", "fired_alerts": [fired]})


@app.route("/api/price/<symbol>")
def api_price_detail(symbol):
    """Get detailed quote for a single stock."""
    symbol = symbol.upper()
    quote = fetch_stock_quote(symbol)
    if not quote:
        return jsonify({"error": True, "message": "Could not fetch quote."})

    # Yahoo's chart meta carries no industry — fill from the user's watchlist
    # sector so the modal doesn't render "Industry: N/A".
    if not quote.get("industry"):
        user = current_user()
        quote["industry"] = (user or {}).get("industry") or ""

    # Get recent filings for context
    recent = get_cached_filings(symbol)[:10]
    pchange = quote.get("pChange", 0) or 0
    direction = "up" if pchange > 0 else "down"
    reasons = analyze_price_movement(symbol, direction, pchange, recent)

    return jsonify({
        "quote": quote,
        "reasons": reasons,
        "alert": abs(pchange) >= 3.0,
    })


# ── Draft (SEBI letter wizard) ─────────────────────────────

@app.route("/draft")
@login_required
def draft_page():
    """SEBI letter drafting wizard."""
    watchlist = get_watchlist(_current_user_id())
    return render_template(
        "draft.html",
        watchlist=watchlist,
        active_page="draft",
    )


@app.route("/api/draft/filing-types")
@login_required
def api_draft_filing_types():
    return jsonify({"types": get_filing_types()})


@app.route("/api/draft/examples")
@login_required
def api_draft_examples():
    """Return past filings of this type for the chosen company.
    Used both for display (full list) and as references for the AI draft.

    For arbitrary symbols (not in the user's watchlist) we still serve
    examples by fetching+caching filings on-demand — so users can draft
    for any listed company, not just their watchlist."""
    symbol = (request.args.get("symbol") or "").strip().upper()
    filing_type = (request.args.get("filing_type") or "").strip()
    if not symbol or not filing_type:
        return jsonify({"error": True, "message": "symbol and filing_type required"}), 400

    # If we don't have any filings cached for this symbol yet, pull them
    # synchronously so the user gets examples on first try (one-time cost).
    if count_filings(symbol) == 0:
        try:
            process_filings(symbol)
        except Exception as e:
            print(f"[DRAFT] background fetch failed for {symbol}: {e}")

    examples = find_past_examples(symbol, filing_type, max_examples=8)
    cached_style = get_cached_style(symbol, filing_type)
    return jsonify({
        "examples": examples,
        "total_filings": count_filings(symbol),
        "style_fingerprint": (cached_style or {}).get("fingerprint", ""),
        "style_learned_at": (cached_style or {}).get("learned_at", ""),
    })


@app.route("/api/draft/recommendations")
@login_required
def api_draft_recommendations():
    """Smart 'what should I draft next' for a company — deadline-driven +
    event-driven, each with a plain-English reason."""
    symbol = (request.args.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"recommendations": []})
    # Ensure we have filings + obligations to reason over (one-time on cold cache).
    try:
        if count_filings(symbol) == 0:
            process_filings(symbol)
        from compliance import get_company_compliance
        if get_company_compliance(symbol)["history"]["total"] == 0:
            refresh_compliance(symbol)
    except Exception as e:
        print(f"[DRAFT-REC] warm-up failed for {symbol}: {e}")
    industry = (request.args.get("industry") or "").strip()
    if not industry:
        industry = (current_user() or {}).get("industry") or ""
    try:
        from draft_recommend import recommend_filings
        return jsonify({"recommendations": recommend_filings(symbol, industry=industry)})
    except Exception as e:
        print(f"[DRAFT-REC] error for {symbol}: {e}")
        return jsonify({"recommendations": []})


@app.route("/api/draft/learn-style", methods=["POST"])
@login_required
def api_draft_learn_style():
    """Force-run the deep style-learning pass for a company+filing-type.
    Returns the fingerprint. Cached server-side for 24h."""
    data = request.get_json() or {}
    symbol = (data.get("symbol") or "").strip().upper()
    filing_type = (data.get("filing_type") or "").strip()
    if not symbol or not filing_type:
        return jsonify({"error": True, "message": "symbol + filing_type required"}), 400
    key = _gemini_key_for_request()
    if not key:
        return jsonify({"error": True, "message": "Gemini API key not configured."}), 503

    examples = find_past_examples(symbol, filing_type, max_examples=8)
    if not examples:
        return jsonify({"error": True,
                        "message": "No past filings of this type for this company yet."}), 404
    fp = learn_company_style(key, symbol, filing_type, examples)
    if not fp:
        return jsonify({"error": True, "message": "Style extraction failed."}), 502
    return jsonify({
        "status": "ok",
        "fingerprint": fp,
        "example_count": len(examples),
    })


def _gemini_key_for_request():
    """User key first, then server-wide GEMINI_API_KEY env var."""
    key = (get_setting("gemini_api_key", "") or "").strip()
    if not key:
        key = (os.environ.get("GEMINI_API_KEY", "") or "").strip()
    return key


@app.route("/api/draft/generate", methods=["POST"])
@login_required
def api_draft_generate():
    """Generate a fresh draft body using past examples + new info."""
    data = request.get_json() or {}
    company_name = (data.get("company_name") or "").strip()
    symbol = (data.get("symbol") or "").strip().upper()
    filing_type = (data.get("filing_type") or "").strip()
    new_info = (data.get("new_info") or "").strip()
    subject_override = (data.get("subject") or "").strip()
    examples = data.get("examples") or []
    if not company_name or not filing_type or not new_info:
        return jsonify({"error": True,
                        "message": "company_name, filing_type, new_info required"}), 400
    key = _gemini_key_for_request()
    if not key:
        return jsonify({"error": True,
                        "message": "Gemini API key not configured on server."}), 503
    result = generate_draft(key, company_name, filing_type, new_info,
                            past_examples=examples,
                            subject_override=subject_override,
                            symbol=symbol)
    if not result.get("body"):
        return jsonify({"error": True,
                        "message": "AI returned empty draft. Try again."}), 502

    # Auto-save the freshly generated draft to the user's history. The UI
    # gets the new draft_id back so subsequent edits / exports update the
    # same record instead of creating duplicates.
    uid = _current_user_id()
    draft_id = None
    try:
        draft_id = save_draft(
            uid, symbol, company_name, filing_type,
            subject=result.get("subject", ""),
            body=result.get("body", ""),
            annexures=result.get("annexures", []),
            metadata={},
            new_info=new_info,
        )
    except Exception as e:
        print(f"[DRAFT] auto-save on generate failed: {e}")

    return jsonify({"status": "ok", "draft_id": draft_id, **result})


@app.route("/api/draft/ai-fill", methods=["POST"])
@login_required
def api_draft_ai_fill():
    """Ask Gemini to pre-fill placeholders it can answer confidently from
    the user's description + public knowledge of the company. Returns a
    list of {pattern, value} entries the front-end pastes into the
    sidebar inputs (user reviews before applying)."""
    data = request.get_json() or {}
    placeholders = data.get("placeholders") or []
    if not placeholders:
        return jsonify({"error": True, "message": "No placeholders to fill."}), 400

    key = _gemini_key_for_request()
    if not key:
        return jsonify({"error": True,
                        "message": "Gemini API key not configured."}), 503

    fills = ai_fill_placeholders(
        api_key=key,
        company_name=data.get("company_name", ""),
        symbol=data.get("symbol", ""),
        filing_type=data.get("filing_type", ""),
        new_info=data.get("new_info", ""),
        body=data.get("body", ""),
        annexures=data.get("annexures") or [],
        placeholders=placeholders,
    )
    return jsonify({"status": "ok", "fills": fills})


@app.route("/api/draft/improve", methods=["POST"])
@login_required
def api_draft_improve():
    """Polish / Match-tone / Make-formal a user-edited draft."""
    data = request.get_json() or {}
    text = data.get("text") or ""
    mode = (data.get("mode") or "polish").strip()
    instruction = (data.get("instruction") or "").strip()
    examples = data.get("examples") or []
    if not text.strip():
        return jsonify({"error": True, "message": "text is required"}), 400
    if mode not in ("polish", "tone", "formal", "custom"):
        mode = "polish"
    if mode == "custom" and not instruction:
        return jsonify({"error": True, "message": "instruction is required for custom mode"}), 400
    key = _gemini_key_for_request()
    if not key:
        return jsonify({"error": True,
                        "message": "Gemini API key not configured."}), 503
    improved = improve_text(key, text, mode=mode, past_examples=examples,
                            instruction=instruction)
    if not improved:
        return jsonify({"error": True,
                        "message": "AI rewrite failed. Try again."}), 502
    return jsonify({"status": "ok", "text": improved})


def _build_letter_from_payload(data):
    # Resolve the document kind from the filing type so MCA/governance docs
    # (resolutions, notices, minutes) render as standalone documents rather
    # than exchange letters. Falls back to any doc_kind the client sent.
    from draft_engine import get_filing_type
    doc_kind = data.get("doc_kind", "")
    ft = get_filing_type(data.get("filing_type", "")) or {}
    if ft.get("doc_kind"):
        doc_kind = ft["doc_kind"]
    return build_letter_data(
        company_name=data.get("company_name", ""),
        registered_office=data.get("registered_office", ""),
        cin=data.get("cin", ""),
        subject=data.get("subject", ""),
        body=data.get("body", ""),
        signer_name=data.get("signer_name", ""),
        signer_designation=data.get("signer_designation", ""),
        bse_code=data.get("bse_code", ""),
        nse_symbol=data.get("nse_symbol", ""),
        letter_date=data.get("letter_date", ""),
        annexures=data.get("annexures") or [],
        doc_kind=doc_kind,
        pdf_meta=data.get("pdf_meta") or {},
    )


def _persist_on_export(data):
    """Save or update the user's draft so the exact letter they
    downloaded is in their history. If `draft_id` is provided we update;
    otherwise we create a fresh row."""
    uid = _current_user_id()
    if not uid:
        return None
    metadata = {
        "registered_office": data.get("registered_office", ""),
        "cin": data.get("cin", ""),
        "bse_code": data.get("bse_code", ""),
        "nse_symbol": data.get("nse_symbol", ""),
        "signer_name": data.get("signer_name", ""),
        "signer_designation": data.get("signer_designation", ""),
        "letter_date": data.get("letter_date", ""),
    }
    payload = {
        "company_symbol": data.get("nse_symbol") or data.get("symbol", ""),
        "company_name": data.get("company_name", ""),
        "filing_type": data.get("filing_type", ""),
        "subject": data.get("subject", ""),
        "body": data.get("body", ""),
        "annexures": data.get("annexures") or [],
        "metadata": metadata,
        "new_info": data.get("new_info", ""),
    }
    draft_id = data.get("draft_id")
    user_email = (current_user() or {}).get("email", "")
    try:
        if draft_id:
            ok = update_draft(int(draft_id), uid, user_email=user_email, **payload)
            if ok:
                return int(draft_id)
        return save_draft(uid, **payload)
    except Exception as e:
        print(f"[DRAFT] persist on export failed: {e}")
        return None


@app.route("/api/draft/export/pdf", methods=["POST"])
@login_required
def api_draft_export_pdf():
    data = request.get_json() or {}
    letter = _build_letter_from_payload(data)
    try:
        pdf_bytes = render_pdf(letter)
    except Exception as e:
        print(f"[DRAFT] PDF render failed: {e}")
        return jsonify({"error": True, "message": "PDF render failed."}), 500
    _persist_on_export(data)
    fname = export_filename(letter, "pdf")
    from flask import Response
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.route("/api/draft/pdf-tools", methods=["POST"])
@login_required
def api_draft_pdf_tools():
    """Make an uploaded filing PDF exchange-ready:
      • set the Document Properties (Title/Author/Subject/Keywords) that the
        NSE/SEBI portal & reviewers read, and
      • optionally OCR a scanned/signed copy so it's text-searchable
        (NEAPS rejects image-only PDFs).
    Returns the processed PDF; an X-Delta-Notice header flags OCR fallbacks."""
    import io as _io
    from flask import Response

    f = request.files.get("pdf")
    if not f:
        return jsonify({"error": True, "message": "No PDF uploaded."}), 400
    raw = f.read()
    if not raw or raw[:4] != b"%PDF":
        return jsonify({"error": True, "message": "That doesn't look like a PDF file."}), 400

    do_ocr = (request.form.get("ocr") == "1")
    meta = {
        "/Title": (request.form.get("title") or "").strip()[:200],
        "/Author": (request.form.get("author") or "").strip()[:120],
        "/Subject": (request.form.get("subject") or "").strip()[:200],
        "/Keywords": (request.form.get("keywords") or "").strip()[:300],
        "/Producer": "Delta Filings",
    }
    meta = {k: v for k, v in meta.items() if v}

    notice = ""
    working = raw

    # 1) OCR (optional) — needs ocrmypdf + tesseract. Degrade gracefully.
    if do_ocr:
        try:
            import tempfile, ocrmypdf
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tin, \
                 tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tout:
                tin.write(working); tin.flush()
                # skip_text: only OCR pages that have no text layer already.
                ocrmypdf.ocr(tin.name, tout.name, skip_text=True,
                             optimize=1, progress_bar=False, language="eng")
                with open(tout.name, "rb") as fh:
                    working = fh.read()
            notice = "OCR applied — PDF is now text-searchable."
        except ImportError:
            notice = "OCR engine not available on this server; metadata was still applied."
        except Exception as e:  # noqa: BLE001
            print(f"[PDF-TOOLS] OCR failed: {e}")
            notice = "OCR could not be completed (the file may already have a text layer); metadata was applied."

    # 2) Set Document Properties via PyPDF2 (always reliable).
    try:
        from PyPDF2 import PdfReader, PdfWriter
        reader = PdfReader(_io.BytesIO(working))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return jsonify({"error": True,
                                "message": "PDF is password-protected — remove the password and retry."}), 400
        writer = PdfWriter()
        writer.append(reader)
        existing = dict(reader.metadata or {})
        existing.update(meta)
        writer.add_metadata(existing)
        out = _io.BytesIO()
        writer.write(out)
        working = out.getvalue()
    except Exception as e:  # noqa: BLE001
        print(f"[PDF-TOOLS] metadata write failed: {e}")
        return jsonify({"error": True, "message": "Could not process this PDF."}), 500

    fname = (request.form.get("title") or "filing").strip() or "filing"
    fname = re.sub(r"[^A-Za-z0-9_-]+", "_", fname)[:60] + "_compliant.pdf"
    return Response(working, mimetype="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{fname}"',
        "X-Delta-Notice": notice or "Document properties applied.",
    })


@app.route("/api/draft/export/docx", methods=["POST"])
@login_required
def api_draft_export_docx():
    data = request.get_json() or {}
    letter = _build_letter_from_payload(data)
    try:
        docx_bytes = render_docx(letter)
    except Exception as e:
        print(f"[DRAFT] DOCX render failed: {e}")
        return jsonify({"error": True, "message": "Word render failed."}), 500
    _persist_on_export(data)
    fname = export_filename(letter, "docx")
    from flask import Response
    return Response(
        docx_bytes,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ── Per-user draft history ────────────────────────────────

@app.route("/api/drafts", methods=["GET"])
@login_required
def api_drafts_list():
    """List the current user's drafts. Strictly filtered by user_id."""
    uid = _current_user_id()
    return jsonify({"drafts": list_user_drafts(uid)})


@app.route("/api/drafts/<int:draft_id>", methods=["GET"])
@login_required
def api_drafts_get(draft_id):
    """Load a specific draft. Returns 404 if not owned by the user —
    deliberately the same as 'doesn't exist' so we don't leak metadata
    about other users' drafts."""
    uid = _current_user_id()
    d = get_draft(draft_id, uid)
    if not d:
        return jsonify({"error": True, "message": "Draft not found"}), 404
    return jsonify({"draft": d})


@app.route("/api/drafts", methods=["POST"])
@login_required
def api_drafts_save():
    """Manual save (Save-draft button in the editor). Creates if no id,
    updates in place if `id` given AND the user owns it."""
    uid = _current_user_id()
    data = request.get_json() or {}
    metadata = {
        "registered_office": data.get("registered_office", ""),
        "cin": data.get("cin", ""),
        "bse_code": data.get("bse_code", ""),
        "nse_symbol": data.get("nse_symbol", ""),
        "signer_name": data.get("signer_name", ""),
        "signer_designation": data.get("signer_designation", ""),
        "letter_date": data.get("letter_date", ""),
    }
    payload = {
        "company_symbol": data.get("nse_symbol") or data.get("company_symbol", ""),
        "company_name": data.get("company_name", ""),
        "filing_type": data.get("filing_type", ""),
        "subject": data.get("subject", ""),
        "body": data.get("body", ""),
        "annexures": data.get("annexures") or [],
        "metadata": metadata,
        "new_info": data.get("new_info", ""),
    }
    draft_id = data.get("id")
    user_email = (current_user() or {}).get("email", "")
    if draft_id:
        if not update_draft(int(draft_id), uid, user_email=user_email, **payload):
            return jsonify({"error": True, "message": "Draft not found"}), 404
        return jsonify({"status": "ok", "id": int(draft_id)})
    new_id = save_draft(uid, **payload)
    return jsonify({"status": "ok", "id": new_id})


# ── Team management routes ────────────────────────────────

@app.route("/api/drafts/teams-overview", methods=["GET"])
@login_required
def api_drafts_teams_overview():
    """One-shot summary of every draft the current user is on, with
    flattened collaborator info. Powers the 'Teams & sharing' panel on
    the Draft home page (step 1)."""
    uid = _current_user_id()
    return jsonify({"teams": get_teams_overview(uid)})



def _user_meta_for_collab():
    u = current_user() or {}
    meta = u.get("user_metadata") or {}
    return {
        "email": u.get("email", "") or meta.get("email", ""),
        "name": meta.get("full_name") or meta.get("name") or u.get("email", "").split("@")[0],
    }


@app.route("/api/drafts/<int:draft_id>/team", methods=["GET"])
@login_required
def api_drafts_team_list(draft_id):
    uid = _current_user_id()
    team = list_team(draft_id, uid)
    if team is None:
        return jsonify({"error": True, "message": "Draft not found"}), 404
    return jsonify({"team": team, "your_role": team["viewer_role"]})


@app.route("/api/drafts/<int:draft_id>/team/invite", methods=["POST"])
@login_required
def api_drafts_team_invite(draft_id):
    """Owner-only: generate an invite link the owner can share manually."""
    uid = _current_user_id()
    data = request.get_json() or {}
    role = (data.get("role") or "editor").strip()
    designation = (data.get("designation") or "").strip()
    email = (data.get("email") or "").strip()
    inv = create_invite(draft_id, uid, role=role,
                        designation=designation, invited_email=email)
    if not inv:
        return jsonify({"error": True,
                        "message": "Only the owner can invite collaborators."}), 403
    inv["url"] = url_for("draft_invite_accept", token=inv["token"], _external=True)
    return jsonify({"status": "ok", "invite": inv})


@app.route("/api/drafts/<int:draft_id>/team/<member_user_id>", methods=["PATCH"])
@login_required
def api_drafts_team_role(draft_id, member_user_id):
    """Owner-only: change role and/or designation of an existing member."""
    uid = _current_user_id()
    data = request.get_json() or {}
    role = data.get("role")
    designation = data.get("designation")
    ok = update_collaborator_role(draft_id, uid, member_user_id,
                                  role=role, designation=designation)
    if not ok:
        return jsonify({"error": True,
                        "message": "Couldn't update — owner-only or member not found."}), 403
    return jsonify({"status": "ok"})


@app.route("/api/drafts/<int:draft_id>/team/<member_user_id>", methods=["DELETE"])
@login_required
def api_drafts_team_remove(draft_id, member_user_id):
    """Owner-only: remove a collaborator."""
    uid = _current_user_id()
    ok = remove_collaborator(draft_id, uid, member_user_id)
    if not ok:
        return jsonify({"error": True,
                        "message": "Couldn't remove — owner-only or member not found."}), 403
    return jsonify({"status": "ok"})


@app.route("/api/drafts/<int:draft_id>/invites/<token>/revoke", methods=["POST"])
@login_required
def api_drafts_invite_revoke(draft_id, token):
    """Owner-only: revoke a pending invite link."""
    uid = _current_user_id()
    ok = revoke_invite(token, uid)
    if not ok:
        return jsonify({"error": True, "message": "Couldn't revoke."}), 403
    return jsonify({"status": "ok"})


@app.route("/draft/invite/<token>")
def draft_invite_accept(token):
    """Public landing for an invite URL.
      - If signed in → claim immediately, redirect to /draft.
      - If not signed in → stash token in session, send to signin/signup.
        The signup/signin success path checks session and claims auto."""
    inv = get_invite(token)
    if not inv:
        # Render a friendly error rather than a bare flash, so a stale link
        # doesn't dump the user into the home page silently.
        return render_template("home.html") if False else (
            "<h2>Invite link is invalid or has expired.</h2>"
            "<p><a href='/'>Go to homepage</a></p>", 404
        )
    user = current_user()
    if user:
        meta = _user_meta_for_collab()
        ok, msg, draft_id = claim_invite(token, user["id"],
                                         user_email=meta["email"],
                                         user_name=meta["name"])
        if ok:
            session.pop("pending_invite", None)
            return redirect(url_for("draft_page") + (f"#draft-{draft_id}" if draft_id else ""))
        return (f"<h2>{msg}</h2><p><a href='/draft'>Back to drafts</a></p>", 400)
    # Not signed in — stash and redirect to signin (which renders signup CTA too).
    session["pending_invite"] = token
    return redirect(url_for("signin_page", notice="Sign in or sign up to accept this draft invite."))


def _claim_pending_invite_if_any(user_id):
    """Called from signin / signup success paths. If the session has a
    pending invite token, try to claim it now. Best-effort — failures
    are logged but don't block the auth flow."""
    token = session.pop("pending_invite", None)
    if not token or not user_id:
        return None
    meta = _user_meta_for_collab()
    try:
        ok, msg, draft_id = claim_invite(token, user_id,
                                          user_email=meta["email"],
                                          user_name=meta["name"])
        if ok:
            print(f"[DRAFT] auto-claimed invite for {user_id} → draft {draft_id}")
            return draft_id
        print(f"[DRAFT] auto-claim failed: {msg}")
    except Exception as e:
        print(f"[DRAFT] auto-claim error: {e}")
    return None


@app.route("/api/drafts/<int:draft_id>", methods=["DELETE"])
@login_required
def api_drafts_delete(draft_id):
    uid = _current_user_id()
    ok = delete_draft(draft_id, uid)
    if not ok:
        return jsonify({"error": True, "message": "Draft not found"}), 404
    return jsonify({"status": "ok", "id": draft_id})


@app.route("/api/price-intel/<symbol>")
@login_required
def api_price_intel(symbol):
    """Deep analysis of why a stock moved: news, Reddit chatter, recent
    filings, plus an AI summary that ties them together. Cached 15 min
    per symbol so back-to-back opens are instant."""
    from price_intel import get_price_intel
    symbol = symbol.upper()
    quote = fetch_stock_quote(symbol)
    if not quote:
        return jsonify({"error": True, "message": "Could not fetch quote."})

    filings = get_cached_filings(symbol)[:10]
    company_name = quote.get("companyName", symbol)

    # Prefer the per-user setting key if set; otherwise fall back to the
    # server-wide GEMINI_API_KEY env var so the deep-analysis works out
    # of the box for every user without each of them needing to paste a key.
    gemini_key = (get_setting("gemini_api_key", "") or "").strip()
    if not gemini_key:
        gemini_key = (os.environ.get("GEMINI_API_KEY", "") or "").strip()
    gemini_model = (get_setting("gemini_model", "gemini-2.5-flash") or "gemini-2.5-flash").strip()

    payload = get_price_intel(
        symbol, company_name, quote, filings,
        gemini_key=gemini_key, gemini_model=gemini_model,
    )
    return jsonify(payload)


# ── DIN Search ───────────────────────────────────────────

# Four prominent Indian directors shown as one-click examples on the DIN
# search page. Descriptions are drawn from widely-reported public information
# and do not assert anything not already in the public record. Legal-status
# fields only state what is publicly known — they never make allegations.
DIN_SEARCH_EXAMPLES = [
    {
        "din": "00347229",
        "name": "Kiran Mazumdar-Shaw",
        "title": "Executive Chairperson, Biocon",
        "bio": ("Founder of Biocon Limited and Executive Chairperson of "
                "Biocon and Biocon Biologics. One of India's foremost "
                "biotech entrepreneurs; sits on multiple listed biotech, "
                "pharma and research boards."),
        "linkedin": "https://www.linkedin.com/in/kiranmazumdarshaw/",
        "legal_status": ("No known major ongoing legal proceedings in the "
                         "public record (as of April 2026)."),
    },
    {
        "din": "00091908",
        "name": "Nandan Nilekani",
        "title": "Co-founder & Non-Executive Chairman, Infosys",
        "bio": ("Co-founder of Infosys, former Chairman of UIDAI (architect "
                "of Aadhaar) and currently Non-Executive Chairman of Infosys. "
                "Serves on several technology and public-infrastructure boards."),
        "linkedin": "https://www.linkedin.com/in/nandan-nilekani/",
        "legal_status": ("No known major ongoing legal proceedings in the "
                         "public record (as of April 2026)."),
    },
    {
        "din": "00007467",
        "name": "Uday Kotak",
        "title": "Founder, Kotak Mahindra Bank",
        "bio": ("Founder and Non-Executive Director of Kotak Mahindra Bank. "
                "Stepped down as MD & CEO in September 2023 under RBI's "
                "founder-tenure norms. Chaired SEBI's Committee on Corporate "
                "Governance (the 'Kotak Committee')."),
        "linkedin": "https://www.linkedin.com/in/uday-kotak-b0b6a64/",
        "legal_status": ("No known major ongoing personal legal proceedings "
                         "in the public record (as of April 2026)."),
    },
    {
        "din": "00004695",
        "name": "Anand Mahindra",
        "title": "Executive Chairman, Mahindra Group",
        "bio": ("Executive Chairman of the Mahindra Group, overseeing Mahindra "
                "& Mahindra, Tech Mahindra, Mahindra Finance and the group's "
                "many other listed entities. Prominent voice on Indian industry."),
        "linkedin": "https://www.linkedin.com/in/anand-mahindra-22773a8/",
        "legal_status": ("No known major ongoing legal proceedings in the "
                         "public record (as of April 2026)."),
    },
]


@app.route("/din-search")
@login_required
def din_search_page():
    """DIN search page."""
    companies = get_companies()
    watchlist = get_watchlist(_current_user_id())
    return render_template(
        "din_search.html",
        companies=companies,
        watchlist=watchlist,
        examples=DIN_SEARCH_EXAMPLES,
    )


@app.route("/api/din-search")
def api_din_search():
    """Search for a DIN across EVERY company the platform knows about.

    Scope (by design):
      1. Filing text search runs against the entire `filings` table already
         (not user-scoped), so it naturally covers every company we've ever
         ingested.
      2. Governance XBRL search runs across the union of all companies in
         our filings table + every user's watchlist, so a DIN lookup returns
         a cross-company director history rather than only the caller's
         personal watchlist.

    Performance: governance search makes HTTP calls to NSE, so a very large
    known-companies set can take tens of seconds. We cap to a sensible upper
    bound and parallelise fetches inside governance.search_din_in_governance.
    """
    din = request.args.get("din", "").strip()
    year = request.args.get("year", "all").strip()

    if not din or len(din) < 5:
        return jsonify({"error": True, "message": "Please enter a valid DIN (at least 5 digits)."})

    # Full platform coverage: every company we have data for + every company
    # on any user's watchlist. This is what the user asked for: "search all
    # companies and see where that director is".
    symbols = get_all_known_companies()

    # 1. Search filings text for the DIN (already DB-wide)
    filing_results = search_din_across_companies(din, year_filter=year)

    # 2. Search governance XBRL data for the DIN across all known companies
    gov_results = search_din_in_governance(din, symbols)

    # 3. Best-effort MCA-aggregator lookup (ZaubaCorp → Tofler) for
    #    cross-entity director history beyond NSE-listed companies.
    #    This is only triggered on an explicit user search and results are
    #    cached for 30 days, so upstream load is minimal.
    try:
        mca_data = scrape_director_by_din(din)
    except Exception as e:
        print(f"[DIN] MCA scrape failed for {din}: {e}")
        mca_data = None

    # If NSE governance didn't identify the director but MCA did, use MCA's
    # name in the director card so the UI has something to show.
    director = gov_results.get("director")
    if not director and mca_data and mca_data.get("name"):
        director = {
            "din": din,
            "name": mca_data["name"],
            "category": "-",
            "dateOfAppointment": "-",
            "dateOfCessation": "-",
            "tenure": "-",
            "directorships": str(
                len(mca_data.get("active_companies", []))
                + len(mca_data.get("past_companies", []))
            ),
            "memberships": "-",
            "linkedin": (
                "https://www.linkedin.com/search/results/all/?keywords="
                + mca_data["name"].replace(" ", "%20")
            ),
        }

    # Group filing results by company
    filings_by_company = {}
    for f in filing_results:
        sym = f["company"]
        if sym not in filings_by_company:
            filings_by_company[sym] = []
        filings_by_company[sym].append(f)

    # Company universe across all three sources
    company_set = set(
        [a["symbol"] for a in gov_results.get("appearances", [])]
        + list(filings_by_company.keys())
    )
    if mca_data:
        for c in mca_data.get("active_companies", []) + mca_data.get("past_companies", []):
            if c.get("company"):
                company_set.add(c["company"])

    return jsonify({
        "din": din,
        "year": year,
        "director": director,
        "governance": gov_results.get("appearances", []),
        "filings": filings_by_company,
        "totalFilings": len(filing_results),
        "companies": list(company_set),
        "mca": mca_data,  # may be None; UI decides whether to render.
    })


@app.route("/api/din-intel")
@login_required
def api_din_intel():
    """Director dossier: AI bio ('who they are'), summarised recent news, and
    legal/regulatory exposure (SEBI orders + litigation in the press).

    Called asynchronously by the DIN page after the main results render, so the
    initial lookup stays fast. Cached 24h per director inside director_intel.
    """
    name = (request.args.get("name") or "").strip()
    din = (request.args.get("din") or "").strip()
    roles = [r for r in (request.args.get("roles") or "").split("||") if r.strip()]
    if not name:
        return jsonify({"available": False, "message": "No director name resolved."})
    try:
        from director_intel import get_director_intel
        key = get_setting("gemini_api_key", "")
        model = get_setting("gemini_model", "gemini-2.5-flash")
        data = get_director_intel(name, din=din, roles=roles, api_key=key, model=model)
        return jsonify(data)
    except Exception as e:
        print(f"[DIN-INTEL] error for {name}: {e}")
        return jsonify({"available": False, "message": "Could not build dossier."})


@app.route("/api/pre-open")
def api_pre_open():
    """Get pre-open session data and alerts for watchlist stocks."""
    symbols = get_companies()
    pre_open = fetch_pre_open_data(symbols)
    alerts = detect_pre_open_alerts(pre_open, threshold=1.5)

    # For each alert, find recent filings and analyze reasons
    for alert in alerts:
        sym = alert["symbol"]
        recent = get_cached_filings(sym)[:10] if sym in symbols else []
        alert["reasons"] = analyze_price_movement(
            sym, alert["direction"], alert["pChange"], recent
        )

    return jsonify({
        "preOpen": pre_open,
        "alerts": alerts,
    })


@app.route("/api/sector")
def api_sector():
    """Get sector data for the user's industry and detect sector-wide alerts."""
    symbols = get_companies()
    user = current_user()
    industry = (user or {}).get("industry")
    sector_data = fetch_sector_data(industry=industry, watchlist_symbols=symbols)
    if not sector_data:
        return jsonify({"error": True, "message": "Could not fetch sector data."})

    sector_alerts = detect_sector_alerts(sector_data, symbols, threshold=3.0)

    # For each sector alert, get correlated movers' filings and analyze
    for alert in sector_alerts:
        # Analyze the main watchlist stock
        sym = alert["symbol"]
        recent = get_cached_filings(sym)[:10]
        alert["reasons"] = analyze_price_movement(
            sym, alert["direction"], alert["pChange"], recent
        )

        # Get filings for correlated movers and analyze sector movement
        correlated_filings = {}
        for mover in alert.get("correlatedMovers", []):
            msym = mover["symbol"]
            mover_filings = get_cached_filings(msym)[:5]
            if mover_filings:
                correlated_filings[msym] = mover_filings
            mover["reasons"] = analyze_price_movement(
                msym, alert["direction"], mover["pChange"], mover_filings
            )

        alert["sectorAnalysis"] = analyze_sector_movement(alert, correlated_filings)

    return jsonify({
        "sector": sector_data.get("sector", {}),
        "stocks": sector_data.get("stocks", []),
        "alerts": sector_alerts,
    })


# ── Company Logo Proxy ────────────────────────────────────

@app.route("/logo/<symbol>")
def company_logo(symbol):
    """Resolve and redirect to a company's published brand image.

    We scrape the company's homepage for apple-touch-icon / og:image /
    high-res favicon (in that priority) and 302 to the discovered URL.
    Result is cached server-side for a week so this is one cheap HEAD-ish
    redirect after warm-up. If nothing is found, returns 404 — the
    front-end then falls through to TradingView / Clearbit / gradient."""
    sym = (symbol or "").upper()
    url = discover_logo(sym)
    if not url:
        return ("", 404)
    # Cache the redirect itself in the browser so subsequent loads of the
    # same symbol skip even our redirect roundtrip.
    resp = redirect(url, code=302)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/admin/logos")
@login_required
def logos_diagnostic():
    """Visual diagnostic — every mapped symbol's resolved logo, source,
    pixel dimensions, and rejected candidates. Use this to spot bad logos
    so you know which slugs / domains to fix."""
    from concurrent.futures import ThreadPoolExecutor
    symbols = sorted(SYMBOL_DOMAIN_MAP.keys())
    with ThreadPoolExecutor(max_workers=12) as pool:
        rows = list(pool.map(discover_logo_with_meta, symbols))
    ok_count = sum(1 for r in rows if r.get("url"))
    return render_template(
        "logos_diagnostic.html",
        rows=rows,
        total=len(rows),
        ok_count=ok_count,
    )


# ── Industry Intelligence ─────────────────────────────────

@app.route("/industry")
@app.route("/industry/<industry>")
@login_required
def industry_page(industry=None):
    """Full industry briefing — curated profile + live news + AI weekly summary."""
    user = current_user()
    if not industry:
        industry = (user or {}).get("industry") or "FMCG"
    if industry not in INDUSTRY_PROFILES:
        # Fall back to the user's industry rather than 404 — better UX.
        industry = (user or {}).get("industry") or "FMCG"
        if industry not in INDUSTRY_PROFILES:
            industry = "FMCG"

    profile = INDUSTRY_PROFILES[industry]
    return render_template(
        "industry.html",
        industry=industry,
        profile=profile,
        all_industries=list_supported_industries(),
    )


@app.route("/api/industry-intel/<industry>")
@login_required
def api_industry_intel(industry):
    """JSON endpoint — returns curated profile + 4-bucket news + AI briefing."""
    if industry not in INDUSTRY_PROFILES:
        return jsonify({"error": True, "message": f"Unknown industry: {industry}"}), 404
    gemini_key = (
        get_setting("gemini_api_key", "")
        or os.environ.get("GEMINI_API_KEY", "")
    )
    return jsonify(get_industry_intel(industry, gemini_key=gemini_key))


# ── Compliance OS ─────────────────────────────────────────

@app.route("/compliance")
@login_required
def compliance_page():
    """Compliance OS dashboard."""
    companies = get_companies()
    watchlist = get_watchlist(_current_user_id())
    _syms = [w["symbol"] for w in watchlist]
    logo_map = {}
    for _s in _syms:
        u = local_logo_url(_s)
        if u:
            logo_map[_s] = u
        else:
            ensure_local_logo_async(_s)
    avatar_colors = {_s: avatar_color(_s) for _s in _syms}
    return render_template(
        "compliance.html", companies=companies, watchlist=watchlist,
        logo_map=logo_map, avatar_colors=avatar_colors,
    )


@app.route("/api/compliance")
@login_required
def api_compliance():
    """Return compliance dashboard data with reconciled metrics, scoped to the
    current user's watchlist."""
    watchlist = get_watchlist(_current_user_id())
    user_symbols = [w["symbol"] for w in watchlist]
    dashboard = get_compliance_dashboard(symbols=user_symbols)
    mandates = get_all_mandates()

    # Per-company scores with health tags + SEBI enforcement summary.
    # We auto-backfill two things for any watchlisted company that's missing
    # data (companies added before these features shipped, or in-flight):
    #   1. Compliance obligations (Reg 27/30/33/etc) — fired in a thread so
    #      the dashboard request doesn't block on NSE/BSE round-trips.
    #   2. SEBI enforcement history — same, in a daemon thread.
    # The `loading` flag tells the front-end whether to show a spinner and
    # poll back. Without this, MARICO-style "0 actions ever" companies would
    # loop forever because we couldn't tell "never scraped" from "scraped
    # and found nothing".
    enforcement_summaries = get_enforcement_summaries(user_symbols)
    company_scores = {}
    for w in watchlist:
        sym = w["symbol"]
        comp_data = get_company_compliance(sym)

        # Auto-backfill compliance obligations if missing.
        comp_loading = False
        if comp_data["history"]["total"] == 0:
            try:
                threading.Thread(
                    target=refresh_compliance, args=(sym,),
                    name=f"compliance-{sym}", daemon=True,
                ).start()
                comp_loading = True
            except Exception as e:
                print(f"[COMPLIANCE] async refresh failed for {sym}: {e}")

        enf_summary = enforcement_summaries.get(sym, {
            "total": 0, "high": 0, "medium": 0, "low": 0,
            "last_action_date": None, "last_checked_at": None,
        })
        # Only treat as "loading" if we've never checked. A timestamp here
        # means we checked and the result (possibly zero) is final.
        if enf_summary.get("last_checked_at") is None:
            refresh_enforcement_async(sym, w.get("name") or sym)
            enf_summary = {**enf_summary, "loading": True}

        company_scores[sym] = {
            "score": comp_data["score"],
            "history": comp_data["history"],
            "health_tag": comp_data["health_tag"],
            "compliance_loading": comp_loading,
            "enforcement": enf_summary,
        }

    return jsonify({
        "upcoming": dashboard["upcoming"],
        "overdue": dashboard["overdue"],
        "completed": dashboard["completed"],
        "future": dashboard.get("future", []),
        "unverified": dashboard.get("unverified", []),
        "all": dashboard["all"],
        "total": dashboard["total"],
        "validation": dashboard["validation"],
        "last_synced": dashboard["last_synced"],
        "mandates": mandates,
        "company_scores": company_scores,
    })


@app.route("/api/xbrl-utilities")
@login_required
def api_xbrl_utilities():
    """Latest NSE XBRL filing utilities / taxonomies, with update dates and a
    'recently updated' flag — so a CS never files with a stale utility."""
    from xbrl_utilities import get_xbrl_utilities
    force = request.args.get("refresh") == "1"
    try:
        return jsonify(get_xbrl_utilities(force=force))
    except Exception as e:
        print(f"[XBRL] endpoint error: {e}")
        return jsonify({"ok": False, "utilities": [], "count": 0,
                        "message": "Could not reach NSE right now."}), 200


@app.route("/api/compliance/refresh", methods=["POST"])
def api_compliance_refresh():
    """Refresh compliance data for all watchlisted companies."""
    try:
        symbol = request.args.get("symbol")
        refresh_compliance(symbol)
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"[COMPLIANCE] Refresh error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/compliance/<symbol>")
def api_compliance_company(symbol):
    """Return compliance data for a specific company."""
    symbol = symbol.upper()
    data = get_company_compliance(symbol)
    return jsonify(data)


@app.route("/api/compliance/profile/<symbol>")
@login_required
def api_compliance_profile(symbol):
    """A-to-Z per-company compliance profile: every filing this company must
    track, grouped by regime and tailored to its sector, merged with live
    obligation status."""
    from compliance import get_company_compliance_profile
    symbol = symbol.upper()
    # Sector: explicit ?industry= wins, else the user's current industry.
    industry = (request.args.get("industry") or "").strip()
    if not industry:
        user = current_user() or {}
        industry = user.get("industry") or ""
    # Make sure the company has generated obligations so statuses are populated.
    if get_company_compliance(symbol)["history"]["total"] == 0:
        try:
            threading.Thread(target=refresh_compliance, args=(symbol,),
                             name=f"compliance-{symbol}", daemon=True).start()
        except Exception as e:
            print(f"[COMPLIANCE] profile refresh failed for {symbol}: {e}")
    return jsonify(get_company_compliance_profile(symbol, industry))


@app.route("/api/compliance/enforcement/<symbol>")
@login_required
def api_compliance_enforcement(symbol):
    """Return SEBI enforcement history for a specific company.
    Triggers a refresh if we have nothing cached yet."""
    symbol = symbol.upper()
    data = get_enforcement_for_company(symbol)
    if data["total"] == 0:
        # First time we're being asked about this symbol — fetch from SEBI.
        watchlist = get_watchlist(_current_user_id())
        name = next((w.get("name") for w in watchlist if w["symbol"] == symbol), symbol)
        try:
            refresh_enforcement_for_company(symbol, name or symbol)
            data = get_enforcement_for_company(symbol)
        except Exception as e:
            print(f"[COMPLIANCE] enforcement refresh failed for {symbol}: {e}")
    return jsonify(data)


@app.route("/api/compliance/enforcement/<symbol>/refresh", methods=["POST"])
@login_required
def api_compliance_enforcement_refresh(symbol):
    """Force-refresh SEBI enforcement data for one company."""
    symbol = symbol.upper()
    watchlist = get_watchlist(_current_user_id())
    name = next((w.get("name") for w in watchlist if w["symbol"] == symbol), symbol)
    try:
        new_count = refresh_enforcement_for_company(symbol, name or symbol)
        return jsonify({"status": "ok", "new_count": new_count,
                        "data": get_enforcement_for_company(symbol)})
    except Exception as e:
        print(f"[COMPLIANCE] enforcement force-refresh failed for {symbol}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Startup: initialize DB tables ─────────────────────────
# These must run at import time so that gunicorn workers (production) have
# a ready DB. Heavy operations like pre-loading filings stay in __main__.
init_db()
init_compliance_db()
init_enforcement_db()
init_alerts_db()
init_drafts_db()
seed_default_referral_codes()
init_watchlist()

# Warm SEBI legal-link cache in the background so per-obligation "SEBI Source"
# buttons resolve to live URLs (SEBI rebuilds them on every amendment).
from sebi_link_resolver import warm_cache_async
warm_cache_async()

# Start the always-on alert poller. Safe across gunicorn workers thanks to
# the DB-backed lease in alert_poller.py — only one worker per minute
# actually hits NSE and dispatches Telegram messages.
from alert_poller import start_alert_poller
start_alert_poller()

print("[MAIN] Databases initialized.")


if __name__ == "__main__":
    print("[MAIN] Pre-loading first watchlist company filings...")
    companies = get_companies()
    if companies:
        try:
            process_filings(companies[0])
        except Exception as e:
            print(f"[MAIN] Pre-load warning: {e}")

    print("[MAIN] Initializing Compliance OS...")
    try:
        refresh_compliance()
    except Exception as e:
        print(f"[MAIN] Compliance warning: {e}")

    print("\n[MAIN] Starting Delta Filings on http://127.0.0.1:8080\n")
    app.run(debug=True, port=8080)

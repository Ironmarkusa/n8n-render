#!/usr/bin/env python3
"""
Unified GSC (CM/LM pages) + GA4 (CM & YoY) monthly fetch
- Can read Google OAuth creds from Postgres and refresh the access token if expired.
- Prints a single JSON object to stdout.

Examples:
  python fetch_monthly_metrics.py \
    --ga-property-id 401839338 \
    --encoded-site-url https%3A%2F%2Fwww.example.com%2F \
    --row-limit-gsc 100 \
    --db-token-key classone \
    --current-date 2025-08-18

If you already have an access token in env:
  GOOGLE_OAUTH_TOKEN=ya29.... python fetch_monthly_metrics.py --ga-property-id 401... --encoded-site-url ...

DB connection discovery order:
- --database-url (CLI)
- env DATABASE_URL (standard on Render etc.)
- env POSTGRES_HOST/PORT/DB/USER/PASSWORD (see get_db_conn_from_env)
"""

import os, sys, json, time, math, urllib.parse, argparse
from datetime import datetime, timezone, timedelta
import requests

# Optional: comment out if you prefer 'psycopg' (v3)
import psycopg2
from psycopg2.extras import RealDictCursor

# ---------- Helpers ----------
def ensure_trailing_slash(u: str) -> str:
    if not u:
        return u
    return u if u.endswith('/') else u + '/'

def encode_site_url(raw: str) -> str:
    if not raw:
        return None
    if '%2F' in raw or '%3A' in raw:
        return raw.strip()
    return urllib.parse.quote(ensure_trailing_slash(raw), safe='')

def first_day_utc(y, m):  # m = 1..12
    return datetime(y, m, 1, tzinfo=timezone.utc)

def last_full_month_anchor(today: datetime) -> datetime:
    y, m = today.year, today.month
    return first_day_utc(y-1, 12) if m == 1 else first_day_utc(y, m-1)

def eom_utc(y, m):
    if m == 12:
        return datetime(y+1, 1, 1, tzinfo=timezone.utc) - timedelta(days=1)
    return datetime(y, m+1, 1, tzinfo=timezone.utc) - timedelta(days=1)

def month_info_from_anchor(anchor_dt: datetime):
    y, m = anchor_dt.year, anchor_dt.month
    start = f"{y}-{m:02d}-01"
    end = eom_utc(y, m).strftime("%Y-%m-%d")
    return {"year": y, "month": m, "start": start, "end": end}

def safe_float(x):
    try: return float(x)
    except: return 0.0

def safe_int(x):
    try: return int(x)
    except: return 0

def retry_request(method, url, headers=None, json_body=None, timeout=60, max_attempts=3, backoff=0.8):
    for attempt in range(1, max_attempts+1):
        try:
            if method.upper() == "POST":
                r = requests.post(url, headers=headers, json=json_body, timeout=timeout)
            else:
                r = requests.get(url, headers=headers, timeout=timeout)
            # backoff on transient
            if r.status_code in (429, 500, 502, 503, 504) and attempt < max_attempts:
                time.sleep(backoff * (2 ** (attempt-1))); continue
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            if attempt >= max_attempts:
                try: err_payload = r.json()
                except: err_payload = {"status_code": getattr(r, "status_code", None), "text": getattr(r, "text", None)}
                raise RuntimeError(f"HTTP error {r.status_code}: {err_payload}") from e
            time.sleep(backoff * (2 ** (attempt-1)))
        except Exception:
            if attempt >= max_attempts: raise
            time.sleep(backoff * (2 ** (attempt-1)))

# ---------- DB token helpers ----------
def get_db_conn_from_env(database_url_cli=None):
    url = database_url_cli or os.environ.get("DATABASE_URL")
    if url:
        return psycopg2.connect(url)

    host = os.environ.get("POSTGRES_HOST") or os.environ.get("DB_HOST")
    port = os.environ.get("POSTGRES_PORT") or os.environ.get("DB_PORT") or "5432"
    db   = os.environ.get("POSTGRES_DB")   or os.environ.get("DB_POSTGRESDB_DATABASE") or os.environ.get("DB_NAME")
    user = os.environ.get("POSTGRES_USER") or os.environ.get("DB_USER")
    pwd  = os.environ.get("POSTGRES_PASSWORD") or os.environ.get("DB_PASSWORD")

    if not all([host, db, user, pwd]):
        return None
    return psycopg2.connect(host=host, port=port, dbname=db, user=user, password=pwd)

# Expect a table like:
#   oauth_tokens (
#     id serial primary key,
#     provider text,           -- 'google'
#     token_key text,          -- e.g., client_slug or account name
#     access_token text,
#     refresh_token text,
#     expires_at timestamptz,  -- access token expiry
#     client_id text,
#     client_secret text,
#     scopes text[],
#     updated_at timestamptz default now()
#   )
#
# You can adapt SELECT/UPDATE below to your schema.
def load_google_token_bundle(conn, token_key: str):
    sql = """
      SELECT access_token, refresh_token, expires_at, client_id, client_secret
      FROM oauth_tokens
      WHERE provider = 'google' AND token_key = %s
      ORDER BY updated_at DESC
      LIMIT 1
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (token_key,))
        row = cur.fetchone()
        return row

def update_access_token(conn, token_key: str, access_token: str, expires_at_iso: str):
    sql = """
      UPDATE oauth_tokens
         SET access_token = %s,
             expires_at   = %s,
             updated_at   = NOW()
       WHERE provider = 'google' AND token_key = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (access_token, expires_at_iso, token_key))
    conn.commit()

def mint_access_token_from_refresh(client_id, client_secret, refresh_token):
    url = "https://oauth2.googleapis.com/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    r = requests.post(url, data=payload, timeout=60)
    r.raise_for_status()
    return r.json()  # { access_token, expires_in, scope, token_type, ... }

def get_access_token_from_db(database_url_cli, token_key):
    conn = get_db_conn_from_env(database_url_cli)
    if not conn:
        raise RuntimeError("Database connection not configured. Provide --database-url or set DATABASE_URL/POSTGRES_* envs.")
    bundle = load_google_token_bundle(conn, token_key)
    if not bundle:
        conn.close()
        raise RuntimeError(f"No google token bundle found for token_key='{token_key}'")

    access_token = bundle.get("access_token")
    refresh_token = bundle.get("refresh_token")
    client_id = bundle.get("client_id")
    client_secret = bundle.get("client_secret")
    expires_at = bundle.get("expires_at")  # may be None

    now = datetime.now(timezone.utc)
    # Refresh if missing or expiring within 2 minutes
    needs_refresh = (not access_token) or (not expires_at) or (expires_at <= now + timedelta(seconds=120))

    if needs_refresh:
        if not (refresh_token and client_id and client_secret):
            conn.close()
            raise RuntimeError("Token refresh required but refresh_token/client_id/client_secret missing in DB row.")
        token_resp = mint_access_token_from_refresh(client_id, client_secret, refresh_token)
        access_token = token_resp["access_token"]
        # Compute new expiry
        expires_in = int(token_resp.get("expires_in", 3600))
        new_exp = now + timedelta(seconds=expires_in)
        update_access_token(conn, token_key, access_token, new_exp.isoformat())
    conn.close()
    return access_token

# ---------- GSC ----------
def gsc_by_page(start_date, end_date, row_limit, start_row, endpoint, headers, dimension_filter_groups=None):
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["page"],
        "rowLimit": row_limit,
        "startRow": start_row
    }
    if dimension_filter_groups:
        body["dimensionFilterGroups"] = dimension_filter_groups
    data = retry_request("POST", endpoint, headers=headers, json_body=body)
    return data.get("rows", []) or [], body

def aggregate_gsc_by_page(rows):
    clicks = impressions = 0
    wpos = 0.0
    clean_rows = []
    for r in rows:
        k = r.get("keys", [])
        page = k[0] if k else None
        c = safe_int(r.get("clicks", 0))
        i = safe_int(r.get("impressions", 0))
        pos = safe_float(r.get("position", 0))
        clicks += c; impressions += i; wpos += pos * i
        clean_rows.append({
            "page": page,
            "clicks": c,
            "impressions": i,
            "ctr": safe_float(r.get("ctr", 0.0)),
            "position": pos
        })
    ctr = (clicks / impressions) * 100 if impressions > 0 else 0.0
    avg_pos = (wpos / impressions) if impressions > 0 else 0.0
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": round(ctr, 2),        # percent
        "avg_position": round(avg_pos, 1)
    }, clean_rows

def pct_change(curr, prev):
    if prev == 0: return None
    return round((curr - prev) / prev * 100.0, 2)

# ---------- GA ----------
def ga_run_report(start_date, end_date, property_id, headers):
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
    metrics = [{"name": n} for n in [
        "sessions","totalUsers","newUsers","screenPageViews","eventCount","userEngagementDuration","bounceRate","engagementRate"
    ]]
    body = {
        "dateRanges": [{"startDate": start_date, "endDate": end_date, "name": "report"}],
        "metrics": metrics,
        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
        "limit": 1000
    }
    data = retry_request("POST", url, headers=headers, json_body=body)
    return data, body

def parse_ga_rows(report_json):
    rows = report_json.get("rows", []) or []
    out = []
    for r in rows:
        dim_vals = [dv.get("value") for dv in r.get("dimensionValues", [])]
        met_vals = [mv.get("value") for mv in r.get("metricValues", [])]
        out.append({
            "sessionDefaultChannelGroup": dim_vals[0] if dim_vals else "(not set)",
            "sessions": safe_float(met_vals[0] if len(met_vals)>0 else 0),
            "totalUsers": safe_float(met_vals[1] if len(met_vals)>1 else 0),
            "newUsers": safe_float(met_vals[2] if len(met_vals)>2 else 0),
            "screenPageViews": safe_float(met_vals[3] if len(met_vals)>3 else 0),
            "eventCount": safe_float(met_vals[4] if len(met_vals)>4 else 0),
            "userEngagementDuration": safe_float(met_vals[5] if len(met_vals)>5 else 0),
            "bounceRate": safe_float(met_vals[6] if len(met_vals)>6 else 0),
            "engagementRate": safe_float(met_vals[7] if len(met_vals)>7 else 0),
        })
    return out

def aggregate_ga(rows):
    sessions = sum(r["sessions"] for r in rows)
    total_users = sum(r["totalUsers"] for r in rows)
    new_users = sum(r["newUsers"] for r in rows)
    pageviews = sum(r["screenPageViews"] for r in rows)
    event_count = sum(r["eventCount"] for r in rows)
    engagement_seconds = sum(r["userEngagementDuration"] for r in rows)

    if sessions > 0:
        w_bounce = sum((r["bounceRate"] or 0) * r["sessions"] for r in rows) / sessions
        w_engage = sum((r["engagementRate"] or 0) * r["sessions"] for r in rows) / sessions
        pages_per_session = pageviews / sessions if pageviews else 0.0
        avg_secs = engagement_seconds / sessions
    else:
        w_bounce = w_engage = pages_per_session = avg_secs = 0.0

    org = [r for r in rows if (r.get("sessionDefaultChannelGroup") or "").lower() == "organic search"]
    if org:
        org_sessions = sum(r["sessions"] for r in org)
        org_users = sum(r["totalUsers"] for r in org)
        org_pageviews = sum(r["screenPageViews"] for r in org)
        org_bounce = sum((r["bounceRate"] or 0) * r["sessions"] for r in org) / org_sessions if org_sessions>0 else 0.0
        org_engage = sum((r["engagementRate"] or 0) * r["sessions"] for r in org) / org_sessions if org_sessions>0 else 0.0
    else:
        org_sessions = org_users = org_pageviews = 0.0
        org_bounce = org_engage = 0.0

    return {
        "sessions": int(round(sessions)),
        "totalUsers": int(round(total_users)),
        "newUsers": int(round(new_users)),
        "pageviews": int(round(pageviews)),
        "eventCount": int(round(event_count)),
        "engagementSeconds": int(round(engagement_seconds)),
        "bounceRate": round(w_bounce, 2),
        "engagementRate": round(w_engage, 2),
        "pagesPerSession": round(pages_per_session, 2),
        "avgSessionSeconds": int(round(avg_secs)),
        "organic": {
            "sessions": int(round(org_sessions)),
            "users": int(round(org_users)),
            "pageviews": int(round(org_pageviews)),
            "bounceRate": round(org_bounce, 2),
            "engagementRate": round(org_engage, 2)
        }
    }

def pct_change_safe(curr, prev):
    if prev is None or prev == 0:
        return None
    return round((curr - prev) / prev * 100.0, 2)

# ---------- Main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--encoded-site-url", help="GSC encoded site URL (preferred)")
    ap.add_argument("--site-url", help="GSC site URL (will be URL-encoded if provided)")
    ap.add_argument("--ga-property-id", required=True, help="GA4 property id (numeric)")

    # Token sources
    ap.add_argument("--access-token", help="Access token (if provided, DB not used)")
    ap.add_argument("--ga-access-token", help="GA access token (defaults to --access-token)")
    ap.add_argument("--db-token-key", help="Lookup key in oauth_tokens.token_key (uses DB to mint/refresh token)")
    ap.add_argument("--database-url", help="Postgres connection URL (optional, or use env)")

    ap.add_argument("--row-limit-gsc", type=int, default=100, help="Top N pages per month (default 100)")
    ap.add_argument("--current-date", help="YYYY-MM-DD (default: now UTC)")
    args = ap.parse_args()

    encoded_site_url = args.encoded_site_url or encode_site_url(args.site_url)
    if not encoded_site_url:
        print(json.dumps({"error":"missing_encoded_site_url"})); sys.exit(1)

    # Resolve tokens
    access_token = args.access_token or os.environ.get("GOOGLE_OAUTH_TOKEN")
    ga_access_token = args.ga_access_token

    if not access_token and args.db_token_key:
        try:
            access_token = get_access_token_from_db(args.database_url, args.db_token_key)
        except Exception as e:
            print(json.dumps({"error":"db_token_error", "detail": str(e)})); sys.exit(1)

    if not ga_access_token:
        ga_access_token = access_token

    if not access_token:
        print(json.dumps({"error":"missing_access_token"})); sys.exit(1)
    if not ga_access_token:
        print(json.dumps({"error":"missing_ga_access_token"})); sys.exit(1)

    # Dates
    try:
        if args.current_date:
            today = datetime.fromisoformat(args.current_date.replace("Z","")).astimezone(timezone.utc)
        else:
            today = datetime.now(timezone.utc)
    except Exception:
        today = datetime.now(timezone.utc)

    cm_anchor  = last_full_month_anchor(today)
    lm_anchor  = first_day_utc((cm_anchor - timedelta(days=1)).year, (cm_anchor - timedelta(days=1)).month)
    yoy_anchor = first_day_utc(cm_anchor.year - 1, cm_anchor.month)

    CM  = month_info_from_anchor(cm_anchor)
    LM  = month_info_from_anchor(lm_anchor)
    YOY = month_info_from_anchor(yoy_anchor)

    # ----- GSC -----
    gsc_endpoint = f"https://searchconsole.googleapis.com/webmasters/v3/sites/{encoded_site_url}/searchAnalytics/query"
    gsc_headers  = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    cm_rows, cm_body = gsc_by_page(CM["start"], CM["end"], args.row_limit_gsc, 0, gsc_endpoint, gsc_headers)
    lm_rows, lm_body = gsc_by_page(LM["start"], LM["end"], args.row_limit_gsc, 0, gsc_endpoint, gsc_headers)

    cm_totals, cm_rows_clean = aggregate_gsc_by_page(cm_rows)
    lm_totals, lm_rows_clean = aggregate_gsc_by_page(lm_rows)

    mom = {
        "clicks_change_pct":       pct_change(cm_totals["clicks"],      lm_totals["clicks"]),
        "impressions_change_pct":  pct_change(cm_totals["impressions"], lm_totals["impressions"]),
        "ctr_change_pct":          pct_change(cm_totals["ctr"],         lm_totals["ctr"]) if lm_totals["ctr"] else None,
        "avg_position_delta":      round(cm_totals["avg_position"] - lm_totals["avg_position"], 1)
    }

    # ----- GA -----
    ga_headers = {"Authorization": f"Bearer {ga_access_token}", "Content-Type": "application/json"}
    ga_cm_json, ga_cm_body   = ga_run_report(CM["start"],  CM["end"],  args.ga_property_id, ga_headers)
    ga_yoy_json, ga_yoy_body = ga_run_report(YOY["start"], YOY["end"], args.ga_property_id, ga_headers)

    ga_cm_rows   = parse_ga_rows(ga_cm_json)
    ga_yoy_rows  = parse_ga_rows(ga_yoy_json)
    ga_cm_totals  = aggregate_ga(ga_cm_rows)
    ga_yoy_totals = aggregate_ga(ga_yoy_rows)

    ga_yoy_deltas = {
        "sessions_change_pct":            pct_change_safe(ga_cm_totals["sessions"],        ga_yoy_totals["sessions"]),
        "users_change_pct":               pct_change_safe(ga_cm_totals["totalUsers"],      ga_yoy_totals["totalUsers"]),
        "pageviews_change_pct":           pct_change_safe(ga_cm_totals["pageviews"],       ga_yoy_totals["pageviews"]),
        "bounce_rate_delta_pp":           round(ga_cm_totals["bounceRate"] - ga_yoy_totals["bounceRate"], 2),
        "engagement_rate_delta_pp":       round(ga_cm_totals["engagementRate"] - ga_yoy_totals["engagementRate"], 2),
        "pages_per_session_change_pct":   pct_change_safe(ga_cm_totals["pagesPerSession"], ga_yoy_totals["pagesPerSession"]),
        "avg_session_seconds_change_pct": pct_change_safe(ga_cm_totals["avgSessionSeconds"], ga_yoy_totals["avgSessionSeconds"]),
        "events_change_pct":              pct_change_safe(ga_cm_totals["eventCount"],      ga_yoy_totals["eventCount"]),
        "organic_sessions_change_pct":    pct_change_safe(ga_cm_totals["organic"]["sessions"],  ga_yoy_totals["organic"]["sessions"]),
        "organic_users_change_pct":       pct_change_safe(ga_cm_totals["organic"]["users"],     ga_yoy_totals["organic"]["users"]),
        "organic_pageviews_change_pct":   pct_change_safe(ga_cm_totals["organic"]["pageviews"], ga_yoy_totals["organic"]["pageviews"]),
        "organic_bounce_delta_pp":        round(ga_cm_totals["organic"]["bounceRate"] - ga_yoy_totals["organic"]["bounceRate"], 2),
        "organic_engagement_delta_pp":    round(ga_cm_totals["organic"]["engagementRate"] - ga_yoy_totals["organic"]["engagementRate"], 2),
    }

    out = {
        "meta": {
            "site_url": urllib.parse.unquote(encoded_site_url),
            "encoded_site_url": encoded_site_url,
            "ga_property_id": args.ga_property_id,
            "date_ranges": {"cm": CM, "lm": LM, "yoy": YOY}
        },
        "gsc": {
            "requests": {"cm_body": cm_body, "lm_body": lm_body},
            "mom": {"cm_totals": cm_totals, "lm_totals": lm_totals, "deltas": mom},
            "cm_data": {"rows": cm_rows_clean},
            "lm_data": {"rows": lm_rows_clean}
        },
        "ga": {
            "requests": {"cm_body": ga_cm_body, "yoy_body": ga_yoy_body},
            "cm":  {"rows": ga_cm_rows,  "totals": ga_cm_totals},
            "yoy": {"rows": ga_yoy_rows, "totals": ga_yoy_totals},
            "yoy_deltas": ga_yoy_deltas
        }
    }

    print(json.dumps(out, ensure_ascii=False))

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Sync LoL esports matches (lolesports + Leaguepedia) into a Google Calendar via API.

Colors (Google's 11 event colorIds):
  - Favorite team OR playoff/final/seeding match -> Tomato red (đỏ đậm)
  - International (MSI/Worlds/EWC/First Stand/Asiad/Nations/KeSPA) -> Grape purple
  - LCK -> Tangerine, LCP -> Blueberry (đậm; LCP có đội VN)
  - LEC -> Sage, LPL -> Lavender (nhạt hơn)
  - other leagues -> Graphite
Status icon in title: ⚪ sắp đấu · 🔴 đang đấu · ✅ đã xong.
Times stored in UTC (Google renders in the viewer's zone). VN time also in description.

Config (no secret):
  FAVORITE_TEAMS env / repo Variable -> teams highlighted red (default below).
Secrets: GOOGLE_SA_KEY (service-account JSON), GCAL_ID (target calendar id).
"""
import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

import lol_sources as src

DEFAULT_FAVORITES = "T1, HLE, GEN, KT, GAM, TSW"

# Lọc giải: giải quốc tế (tier 'intl') luôn có; FULL_LEAGUES có mọi trận;
# LATE_ONLY chỉ lấy trận playoff/cuối mùa; còn lại (Prime League, LRS...) bỏ.
# Chỉnh bằng repo Variable LOL_LEAGUES / LOL_LATE_ONLY nếu muốn.
FULL_LEAGUES_DEFAULT = "LCK, LCP"
LATE_ONLY_DEFAULT = "LPL, LEC"

# league (normalized) -> tier
LEAGUE_TIER = {
    "lck": "lck", "lpl": "lpl", "lec": "lec", "lcp": "lcp",
    "msi": "intl", "worlds": "intl", "worldchampionship": "intl", "firststand": "intl",
    "esportsworldcup": "intl", "ewc": "intl", "nationscup": "intl",
    "asiangames": "intl", "kespacup": "intl",
}
TIER_COLOR = {
    "lck": "6",    # Tangerine
    "lcp": "9",    # Blueberry
    "lec": "2",    # Sage
    "lpl": "1",    # Lavender
    "intl": "3",   # Grape
    "other": "8",  # Graphite
}
HOT_COLOR = "11"   # Tomato — playoff/final/seeding OR favorite team

LEAGUE_SHORT = {
    "worldchampionship": "Worlds", "worlds": "Worlds", "esportsworldcup": "EWC",
    "firststand": "First Stand", "asiangames": "Asiad", "nationscup": "Nations Cup",
    "kespacup": "KeSPA",
}
PLAYOFF_RE = re.compile(
    r"playoff|final|knockout|bracket|grand|tiebreak|seeding|seed|promotion|elimination|"
    r"semifinal|quarterfinal", re.I)

STATE_ICON = {"unstarted": "⚪", "inProgress": "🔴", "completed": "✅"}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def lkey(s):
    """Khoá so khớp tên giải: bỏ luôn chữ số để 'Esports World Cup 2026' == 'esportsworldcup'."""
    return re.sub(r"\d", "", norm(s))


def league_short(name):
    return LEAGUE_SHORT.get(lkey(name), name)


def tier_of(league):
    return LEAGUE_TIER.get(lkey(league), "other")


def is_playoff(m):
    return bool(PLAYOFF_RE.search(m.get("block") or "")) or bool(PLAYOFF_RE.search(m.get("league") or ""))


def is_fav(m, favs):
    for t in (m["a"], m["b"]):
        if norm(t["code"]) in favs or norm(t["name"]) in favs:
            return True
    return False


def color_of(m, favs):
    if is_fav(m, favs) or is_playoff(m):
        return HOT_COLOR
    return TIER_COLOR[tier_of(m["league"])]


def keep_match(m, full, late_only):
    """Giữ giải quốc tế + FULL_LEAGUES (mọi trận) + LATE_ONLY (chỉ playoff)."""
    if tier_of(m["league"]) == "intl":
        return True
    nl = lkey(m["league"])
    if nl in full:
        return True
    if nl in late_only:
        return is_playoff(m)
    return False


def event_id(m):
    return "lol" + hashlib.sha1(m["id"].encode()).hexdigest()   # [0-9a-f], valid base32hex


def vn_str(utc):
    return (utc + timedelta(hours=7)).strftime("%d/%m %H:%M")


def render(m):
    icon = STATE_ICON.get(m["state"], "⚪")
    a, b = m["a"], m["b"]
    ls = league_short(m["league"])
    wa, wb = a["wins"], b["wins"]
    if m["state"] == "unstarted" or wa is None or wb is None:
        mid = "vs"
    else:
        mid = f"{wa}-{wb}"
    summary = f"{icon} [{ls}] {a['code']} {mid} {b['code']}"

    bo = f"Bo{m['bo']}" if m.get("bo") else ""
    block = m.get("block") or ""
    head = " · ".join(x for x in [m["league"], block, bo] if x)
    lines = [head, f"{a['name']} vs {b['name']}", f"Giờ VN: {vn_str(m['utc'])}"]
    if mid != "vs":
        lines.append(f"Tỷ số: {a['code']} {wa}-{wb} {b['code']}")
    lines.append("LoL Esports")
    desc = "\\n".join(x for x in lines if x)
    return summary, desc


def gcal_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = json.loads(os.environ["GOOGLE_SA_KEY"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/calendar"])
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def upsert(svc, cal_id, m, favs, dry=False):
    summary, desc = render(m)
    start = m["utc"]
    # BoX matches can run long; give a generous 3h block as a marker.
    body = {
        "id": event_id(m),
        "summary": summary,
        "description": desc.replace("\\n", "\n"),
        "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "Etc/UTC"},
        "end": {"dateTime": (start + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "Etc/UTC"},
        "colorId": color_of(m, favs),
        "reminders": {"useDefault": True, "overrides": []},
    }
    if dry:
        print(f"[dry] {body['summary']:44} color={body['colorId']:>2} {start:%Y-%m-%d %H:%MZ}")
        return "dry"
    from googleapiclient.errors import HttpError
    try:
        ex = svc.events().get(calendarId=cal_id, eventId=body["id"]).execute()
        if ex.get("status") == "cancelled":
            svc.events().insert(calendarId=cal_id, body=body).execute(); return "reinsert"
        changed = any(ex.get(k) != body[k] for k in ("summary", "colorId", "description")) \
            or (ex.get("start", {}).get("dateTime", "")[:16] != body["start"]["dateTime"][:16])
        if changed:
            svc.events().patch(calendarId=cal_id, eventId=body["id"], body=body).execute()
            return "update"
        return "nochange"
    except HttpError as e:
        if e.resp.status == 404:
            svc.events().insert(calendarId=cal_id, body=body).execute()
            return "insert"
        raise


def purge(svc, cal_id, keep_ids):
    """Xoá event của app (id bắt đầu 'lol') không còn trong danh sách đã lọc — dọn rác."""
    now = datetime.now(timezone.utc)
    tmin = (now - timedelta(days=30)).isoformat()
    tmax = (now + timedelta(days=90)).isoformat()
    deleted, page = 0, None
    while True:
        resp = svc.events().list(calendarId=cal_id, timeMin=tmin, timeMax=tmax,
                                  maxResults=2500, singleEvents=True, showDeleted=False,
                                  pageToken=page).execute()
        for ev in resp.get("items", []):
            eid = ev.get("id", "")
            if eid.startswith("lol") and eid not in keep_ids:
                try:
                    svc.events().delete(calendarId=cal_id, eventId=eid).execute()
                    deleted += 1
                except Exception:  # noqa
                    pass
        page = resp.get("nextPageToken")
        if not page:
            break
    return deleted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check-active", action="store_true",
                    help="exit 0 if a match is live or within ±90 min, else exit 1")
    ap.add_argument("--no-extra", action="store_true", help="skip Leaguepedia extra events")
    args = ap.parse_args()

    matches = src.fetch_all(include_extra=not args.no_extra)
    full = {lkey(x) for x in os.environ.get("LOL_LEAGUES", FULL_LEAGUES_DEFAULT).split(",") if x.strip()}
    late = {lkey(x) for x in os.environ.get("LOL_LATE_ONLY", LATE_ONLY_DEFAULT).split(",") if x.strip()}
    matches = [m for m in matches if keep_match(m, full, late)]

    if args.check_active:
        now = datetime.now(timezone.utc)
        live = any(m["state"] == "inProgress" for m in matches)
        near = any(abs((m["utc"] - now).total_seconds()) <= 90 * 60 for m in matches)
        print(f"{'active' if (live or near) else 'idle'} (live={live}, near={near})")
        sys.exit(0 if (live or near) else 1)

    favs = {norm(t) for t in os.environ.get("FAVORITE_TEAMS", DEFAULT_FAVORITES).split(",") if t.strip()}
    print(f"matches: {len(matches)} | favorites: {sorted(favs)}")
    svc = None if args.dry_run else gcal_service()
    cal_id = os.environ.get("GCAL_ID", "DRY").strip()   # bỏ newline/space thừa từ secret
    stats, keep = {}, set()
    for m in matches:
        res = upsert(svc, cal_id, m, favs, dry=args.dry_run)
        stats[res] = stats.get(res, 0) + 1
        keep.add(event_id(m))
    if svc and not args.dry_run:
        print("purged (rác/quá cũ):", purge(svc, cal_id, keep))
    print("done:", stats)


if __name__ == "__main__":
    main()

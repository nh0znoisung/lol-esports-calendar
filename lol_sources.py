#!/usr/bin/env python3
"""
Data sources for the LoL esports calendar.

Primary : Leaguepedia (lol.fandom.com) Cargo API — covers the WHOLE 2026 season
          across every league/event (LCK, LCP, LPL, LEC, MSI, Worlds, EWC, Asian
          Games, KeSPA Cup, Nations Cup, First Stand ...), including future/TBD
          bracket matches and round names. Auto-discovers tournaments by Year +
          League, then pulls their MatchSchedule (plain GET, no key).
Future  : lolesports API (getSchedule) — kept for a live-state overlay later.
          Needs the public x-api-key header (a well-known constant, not a secret).

Everything is normalized to a common match dict:
  {id, league, tournament, block(round), utc, state, bo, a{code,name,wins,outcome}, b{...}, source}
"""
import os
from datetime import datetime, timezone

import requests

LEAGUEPEDIA_API = "https://lol.fandom.com/api.php"
UA = {"User-Agent": "lol-esports-calendar/1.0"}

YEAR_DEFAULT = "2026"
# Leaguepedia "League" values to include. LCK/LCP full; LPL/LEC discovered too
# but filtered to playoffs downstream; the rest are international events.
LEAGUES_DEFAULT = ("LCK, LCP, LPL, LEC, First Stand, Mid-Season Invitational, "
                   "World Championship, Esports World Cup, Asian Games, "
                   "KeSPA Cup, Nations Cup")

# --- lolesports (future live overlay) -------------------------------------
LOLESPORTS_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
SCHEDULE_URL = "https://esports-api.lolesports.com/persisted/gw/getSchedule?hl=en-US"


def _dt(s):
    if not s:
        return None
    s = s.strip().replace(" ", "T", 1) if "T" not in s else s
    s = s.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _cargo(params):
    r = requests.get(LEAGUEPEDIA_API,
                     params={**params, "action": "cargoquery", "format": "json"},
                     timeout=30, headers=UA)
    r.raise_for_status()
    return [row.get("title", {}) for row in r.json().get("cargoquery", [])]


def _q(items):
    return ",".join("'%s'" % str(x).replace("'", "") for x in items)


def discover_pages(year, leagues):
    """Return {OverviewPage: League} for tournaments of the given year+leagues."""
    where = f"T.Year='{year}' AND T.League IN ({_q(leagues)})"
    try:
        rows = _cargo({
            "tables": "Tournaments=T",
            "fields": "T.OverviewPage=page,T.League=league",
            "where": where, "limit": "500",
        })
    except Exception as e:  # noqa
        print(f"WARN: discover failed: {e} | where={where}")
        return {}
    pages = {r["page"]: (r.get("league") or r["page"]) for r in rows if r.get("page")}
    print(f"discover: {len(pages)} tournaments for {year} | sample: {list(pages)[:5]}")
    return pages


def _row_to_match(t, page_league):
    utc = _dt(t.get("dt"))
    if not utc:
        return None
    t1 = (t.get("t1") or "").strip() or "TBD"
    t2 = (t.get("t2") or "").strip() or "TBD"
    s1, s2, win = t.get("s1"), t.get("s2"), t.get("win")
    done = win in ("1", "2")
    now = datetime.now(timezone.utc)
    state = "completed" if done else ("inProgress" if utc <= now else "unstarted")
    op = t.get("op") or ""
    return {
        "id": t.get("mid") or f"{op}|{t1}|{t2}|{t.get('dt')}",
        "league": page_league.get(op, op), "tournament": op,
        "block": (t.get("tab") or "").strip(),
        "utc": utc, "state": state,
        "bo": int(t["bo"]) if str(t.get("bo", "")).isdigit() else None,
        "a": {"code": t1, "name": t1, "wins": int(s1) if str(s1).isdigit() else None,
              "outcome": ("win" if win == "1" else "loss" if win == "2" else None)},
        "b": {"code": t2, "name": t2, "wins": int(s2) if str(s2).isdigit() else None,
              "outcome": ("win" if win == "2" else "loss" if win == "1" else None)},
        "source": "leaguepedia",
    }


def fetch_leaguepedia_season(year=None, leagues=None):
    year = year or os.environ.get("LOL_YEAR", YEAR_DEFAULT)
    leagues = leagues or [x.strip() for x in
                          os.environ.get("LOL_LEAGUES", LEAGUES_DEFAULT).split(",") if x.strip()]
    page_league = discover_pages(year, leagues)
    # allow manual extra OverviewPages (comma-separated)
    for p in os.environ.get("LOL_EXTRA_PAGES", "").split(","):
        p = p.strip()
        if p:
            page_league.setdefault(p, p)
    pages = list(page_league)
    fields = ("MS.Team1=t1,MS.Team2=t2,MS.DateTime_UTC=dt,MS.Team1Score=s1,"
              "MS.Team2Score=s2,MS.BestOf=bo,MS.Winner=win,MS.Tab=tab,"
              "MS.OverviewPage=op,MS.MatchId=mid")
    out = []
    for i in range(0, len(pages), 8):          # batch pages to keep queries small
        batch = pages[i:i + 8]
        offset = 0
        while True:
            try:
                rows = _cargo({
                    "tables": "MatchSchedule=MS", "fields": fields,
                    "where": f"MS.OverviewPage IN ({_q(batch)})",
                    "order_by": "MS.DateTime_UTC", "limit": "500", "offset": str(offset),
                })
            except Exception as e:  # noqa
                print(f"WARN: MatchSchedule batch failed: {e}")
                break
            for t in rows:
                m = _row_to_match(t, page_league)
                if m:
                    out.append(m)
            if len(rows) < 500:
                break
            offset += 500
    print(f"leaguepedia: {len(out)} matches from {len(pages)} tournaments")
    return out


def fetch_all(**_):
    matches = fetch_leaguepedia_season()
    seen = {}
    for m in matches:
        if m["utc"]:
            seen.setdefault(m["id"], m)
    return sorted(seen.values(), key=lambda m: m["utc"])

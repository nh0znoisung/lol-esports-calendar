#!/usr/bin/env python3
"""
Data sources for the LoL esports calendar.

Primary : lolesports unofficial API (getSchedule) — Riot leagues (LCK, LPL, LEC,
          LCP, MSI, Worlds, First Stand, EWC if listed). One GET, gives state
          (unstarted/inProgress/completed) + series score (gameWins) + blockName.
          Needs the public x-api-key header (a well-known constant, not a secret).
Extra   : Leaguepedia (lol.fandom.com) Cargo API — non-Riot events that lolesports
          usually lacks (KeSPA Cup, Asian Games, Nations Cup). Best-effort: any
          failure/timeout is swallowed so the primary source still syncs.

Both are normalized to a common match dict (see `to_match`).
"""
import os
from datetime import datetime, timezone

import requests

LOLESPORTS_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"  # public API key used by lolesports.com
SCHEDULE_URL = "https://esports-api.lolesports.com/persisted/gw/getSchedule?hl=en-US"
HEADERS = {"x-api-key": LOLESPORTS_KEY, "User-Agent": "lol-esports-calendar/1.0"}

LEAGUEPEDIA_API = "https://lol.fandom.com/api.php"
# Extra tournaments to pull from Leaguepedia (OverviewPage names). Override via
# env LOL_EXTRA_PAGES (comma-separated). Names must match Leaguepedia exactly.
DEFAULT_EXTRA_PAGES = [
    "Esports World Cup 2026",
    "2026 Asian Games",
    "KeSPA Cup 2026",
    "Nations Cup 2026",
]


def _dt(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00").replace(" ", "T", 1) if "T" not in s else s.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def fetch_lolesports():
    """Return list of normalized matches from lolesports getSchedule (rolling window)."""
    try:
        r = requests.get(SCHEDULE_URL, headers=HEADERS, timeout=25)
        r.raise_for_status()
        events = (r.json().get("data", {}).get("schedule", {}) or {}).get("events") or []
    except Exception as e:  # noqa
        print(f"WARN: lolesports fetch failed ({e})")
        return []
    out = []
    for ev in events:
        if ev.get("type") != "match":
            continue
        m = ev.get("match") or {}
        teams = m.get("teams") or []
        if len(teams) < 2:
            continue
        league = (ev.get("league") or {}).get("name", "")
        out.append(to_match(
            mid=m.get("id") or ev.get("startTime", "") + league,
            league=league,
            block=ev.get("blockName") or "",
            utc=_dt(ev.get("startTime")),
            state=ev.get("state", "unstarted"),
            bo=(m.get("strategy") or {}).get("count"),
            a=teams[0], b=teams[1],
            source="lolesports",
        ))
    return out


def to_match(mid, league, block, utc, state, bo, a, b, source):
    def team(t):
        res = t.get("result") or {}
        return {
            "code": t.get("code") or t.get("name", "?"),
            "name": t.get("name", "?"),
            "wins": res.get("gameWins"),
            "outcome": res.get("outcome"),
        }
    return {
        "id": str(mid), "league": league, "block": block, "utc": utc,
        "state": state,  # unstarted / inProgress / completed
        "bo": bo, "a": team(a), "b": team(b), "source": source,
    }


def fetch_leaguepedia(pages=None):
    """Best-effort pull of extra tournaments from Leaguepedia MatchSchedule."""
    pages = pages or [p.strip() for p in
                      os.environ.get("LOL_EXTRA_PAGES", ",".join(DEFAULT_EXTRA_PAGES)).split(",")
                      if p.strip()]
    out = []
    for page in pages:
        try:
            params = {
                "action": "cargoquery", "format": "json", "limit": "200",
                "tables": "MatchSchedule=MS",
                "fields": ("MS.Team1=t1,MS.Team2=t2,MS.DateTime_UTC=dt,"
                           "MS.Team1Score=s1,MS.Team2Score=s2,MS.BestOf=bo,"
                           "MS.Winner=win,MS.Tab=tab,MS.MatchId=mid"),
                "where": f'MS.OverviewPage="{page}"',
                "order_by": "MS.DateTime_UTC",
            }
            r = requests.get(LEAGUEPEDIA_API, params=params, timeout=20,
                             headers={"User-Agent": "lol-esports-calendar/1.0"})
            r.raise_for_status()
            rows = r.json().get("cargoquery", [])
        except Exception as e:  # noqa
            print(f"WARN: Leaguepedia '{page}' failed ({e})")
            continue
        for row in rows:
            t = row.get("title", {})
            utc = _dt(t.get("dt"))
            if not (t.get("t1") and t.get("t2") and utc):
                continue
            s1, s2 = t.get("s1"), t.get("s2")
            win = t.get("win")
            done = win in ("1", "2")
            now = datetime.now(timezone.utc)
            state = "completed" if done else ("inProgress" if utc <= now else "unstarted")
            out.append({
                "id": t.get("mid") or f"{page}|{t.get('t1')}|{t.get('t2')}|{t.get('dt')}",
                "league": page, "block": t.get("tab") or "", "utc": utc, "state": state,
                "bo": int(t["bo"]) if str(t.get("bo", "")).isdigit() else None,
                "a": {"code": t["t1"], "name": t["t1"],
                      "wins": int(s1) if str(s1).isdigit() else None,
                      "outcome": ("win" if win == "1" else "loss" if win == "2" else None)},
                "b": {"code": t["t2"], "name": t["t2"],
                      "wins": int(s2) if str(s2).isdigit() else None,
                      "outcome": ("win" if win == "2" else "loss" if win == "1" else None)},
                "source": "leaguepedia",
            })
    return out


def fetch_all(include_extra=True):
    matches = fetch_lolesports()
    if include_extra:
        matches += fetch_leaguepedia()
    # de-dup by id, keep the richest (lolesports preferred)
    seen = {}
    for m in matches:
        if m["utc"] is None:
            continue
        seen.setdefault(m["id"], m)
    return sorted(seen.values(), key=lambda m: m["utc"])

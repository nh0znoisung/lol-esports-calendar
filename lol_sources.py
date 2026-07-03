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
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

CACHE_FILE = Path("_cache/schedule.json")   # cache lịch Leaguepedia (làm mới ~1h qua Actions cache key)


def _n(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", s.lower())

_SESSION = None


def _session():
    """Requests session, logged in with a Fandom bot password if provided
    (LEAGUEPEDIA_USER / LEAGUEPEDIA_PASS) — gives a much higher rate limit."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    s = requests.Session()
    s.headers.update(UA)
    user, pw = os.environ.get("LEAGUEPEDIA_USER"), os.environ.get("LEAGUEPEDIA_PASS")
    if user and pw:
        try:
            tok = s.get(LEAGUEPEDIA_API, params={
                "action": "query", "meta": "tokens", "type": "login", "format": "json"},
                timeout=30).json()["query"]["tokens"]["logintoken"]
            res = s.post(LEAGUEPEDIA_API, data={
                "action": "login", "lgname": user, "lgpassword": pw,
                "lgtoken": tok, "format": "json"}, timeout=30).json()
            print("leaguepedia login:", res.get("login", {}).get("result"))
        except Exception as e:  # noqa
            print(f"WARN: leaguepedia login failed ({e})")
    _SESSION = s
    return s

LEAGUEPEDIA_API = "https://lol.fandom.com/api.php"
UA = {"User-Agent": "lol-esports-calendar/1.0"}

YEAR_DEFAULT = "2026"
# Domestic leagues: match by OverviewPage prefix ("LCK/2026 Season/...") because
# their Tournaments.League field is the FULL name ("LoL Champions Korea").
PREFIXES_DEFAULT = "LCK, LPL, LEC, LCP"
# International events: match by Tournaments.League (giá trị League THẬT trên Leaguepedia).
EVENT_LEAGUES_DEFAULT = ("Mid-Season Invitational, World Championship, Esports World Cup, "
                         "First Stand, Asian Games 2018, KeSPA, Esports Nations Cup")

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


def _cargo(params, _tries=5):
    s = _session()
    q = {**params, "action": "cargoquery", "format": "json", "maxlag": "5"}
    for attempt in range(_tries):
        r = s.get(LEAGUEPEDIA_API, params=q, timeout=30)
        r.raise_for_status()
        j = r.json()
        err = j.get("error", {})
        code = err.get("code", "")
        if code in ("ratelimited", "maxlag"):     # đợi (backoff tăng dần) rồi thử lại
            time.sleep(10 * (attempt + 1))
            continue
        if err:
            raise RuntimeError(err.get("info", str(err)))
        time.sleep(2)                              # nhẹ tay giữa các request
        return [row.get("title", {}) for row in j.get("cargoquery", [])]
    raise RuntimeError("rate limited (hết số lần thử) — kiểm tra bot password có tick 'High API limits'")


def _q(items):
    return ",".join("'%s'" % str(x).replace("'", "") for x in items)


def discover_pages(year, prefixes, events):
    """Return {OverviewPage: league_label}. Domestic by page prefix, events by League name.
    Gộp tất cả prefix vào 1 query để giảm số request (né rate limit)."""
    pages = {}
    if prefixes:                                # 1 query cho mọi giải nội địa
        like = " OR ".join(f"T.OverviewPage LIKE '{p}/{year}%'" for p in prefixes)
        try:
            rows = _cargo({"tables": "Tournaments=T", "fields": "T.OverviewPage=page",
                           "where": like, "limit": "300"})
            for r in rows:
                pg = r.get("page")
                if pg:
                    pages[pg] = next((p for p in prefixes if pg.startswith(p + "/")),
                                     pg.split("/")[0])
        except Exception as e:  # noqa
            print(f"WARN: discover domestic failed: {e}")
    if events:                                  # 1 query cho giải quốc tế
        try:
            rows = _cargo({"tables": "Tournaments=T", "fields": "T.OverviewPage=page,T.League=league",
                           "where": f"T.Year='{year}' AND T.League IN ({_q(events)})", "limit": "300"})
            for r in rows:
                if r.get("page"):
                    pages[r["page"]] = r.get("league") or r["page"]
        except Exception as e:  # noqa
            print(f"WARN: discover events failed: {e}")
    print(f"discover: {len(pages)} tournaments for {year} | sample: {list(pages)[:6]}")
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
        "block": (t.get("srnd") or t.get("rnd") or t.get("tab") or "").strip(),
        "venue": (t.get("venue") or "").strip(),
        "utc": utc, "state": state,
        "bo": int(t["bo"]) if str(t.get("bo", "")).isdigit() else None,
        "a": {"code": t1, "name": t1, "wins": int(s1) if str(s1).isdigit() else None,
              "outcome": ("win" if win == "1" else "loss" if win == "2" else None)},
        "b": {"code": t2, "name": t2, "wins": int(s2) if str(s2).isdigit() else None,
              "outcome": ("win" if win == "2" else "loss" if win == "1" else None)},
        "source": "leaguepedia",
    }


def fetch_leaguepedia_season(year=None, prefixes=None, events=None):
    year = year or os.environ.get("LOL_YEAR") or YEAR_DEFAULT
    prefixes = prefixes or [x.strip() for x in
                            (os.environ.get("LOL_PREFIXES") or PREFIXES_DEFAULT).split(",") if x.strip()]
    events = events or [x.strip() for x in
                        (os.environ.get("LOL_EVENT_LEAGUES") or EVENT_LEAGUES_DEFAULT).split(",") if x.strip()]
    page_league = discover_pages(year, prefixes, events)
    # allow manual extra OverviewPages (comma-separated)
    for p in os.environ.get("LOL_EXTRA_PAGES", "").split(","):
        p = p.strip()
        if p:
            page_league.setdefault(p, p)
    pages = list(page_league)
    fields = ("MS.Team1=t1,MS.Team2=t2,MS.DateTime_UTC=dt,MS.Team1Score=s1,"
              "MS.Team2Score=s2,MS.BestOf=bo,MS.Winner=win,MS.Tab=tab,MS.Round=rnd,"
              "MS.ShownRound=srnd,MS.Venue=venue,MS.OverviewPage=op,MS.MatchId=mid")
    out, ok = [], True
    for i in range(0, len(pages), 40):         # batch pages -> ít request (né rate limit)
        batch = pages[i:i + 40]
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
                ok = False           # đánh dấu fetch KHÔNG hoàn tất -> đừng purge
                break
            for t in rows:
                m = _row_to_match(t, page_league)
                if m:
                    out.append(m)
            if len(rows) < 500:
                break
            offset += 500
    print(f"leaguepedia: {len(out)} matches from {len(pages)} tournaments (ok={ok})")
    return out, ok


def _short_fallback(name):
    base = re.sub(r"\s*\(.*?\)", "", name).strip()      # bỏ "(2024 American Team)" ...
    if len(base) <= 5:
        return base
    ac = "".join(w[0] for w in re.split(r"\s+", base) if w and w[0].isalnum()).upper()
    return ac or base[:4]


def fetch_team_shorts(names):
    names = sorted({n for n in names if n and n != "TBD"})
    shorts = {}
    for i in range(0, len(names), 50):
        batch = names[i:i + 50]
        try:
            rows = _cargo({"tables": "Teams=T", "fields": "T.Name=name,T.Short=short",
                           "where": f"T.Name IN ({_q(batch)})", "limit": "500"})
        except Exception as e:  # noqa
            print(f"WARN: team shorts failed ({e})")
            continue
        for r in rows:
            if r.get("name") and r.get("short"):
                shorts[r["name"]] = r["short"]
    return shorts


def _enrich_shorts(matches):
    names = {m["a"]["name"] for m in matches} | {m["b"]["name"] for m in matches}
    shorts = fetch_team_shorts(list(names))
    for m in matches:
        for side in ("a", "b"):
            nm = m[side]["name"]
            m[side]["code"] = "TBD" if nm == "TBD" else (shorts.get(nm) or _short_fallback(nm))


def _load_cache():
    """Đọc cache lịch (nếu file có). Freshness do Actions cache key (theo giờ) quản."""
    try:
        d = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        for m in d["matches"]:
            m["utc"] = datetime.fromisoformat(m["utc"])
        print(f"cache HIT: {len(d['matches'])} matches")
        return d["matches"]
    except Exception:
        return None


def _save_cache(matches):
    try:
        CACHE_FILE.parent.mkdir(exist_ok=True)
        ser = [{**m, "utc": m["utc"].isoformat()} for m in matches]
        CACHE_FILE.write_text(json.dumps({"at": time.time(), "matches": ser}), encoding="utf-8")
    except Exception as e:  # noqa
        print(f"WARN: cache save failed ({e})")


def fetch_lolesports_live():
    """Trạng thái LIVE thật từ lolesports (giải Riot). 1 GET, cần header key, không rate-limit."""
    try:
        r = requests.get(SCHEDULE_URL, timeout=25,
                         headers={"x-api-key": LOLESPORTS_KEY, "User-Agent": "lol-esports-calendar/1.0"})
        r.raise_for_status()
        events = (r.json().get("data", {}).get("schedule", {}) or {}).get("events") or []
    except Exception as e:  # noqa
        print(f"WARN: lolesports live failed ({e})")
        return []
    out = []
    for ev in events:
        if ev.get("type") != "match":
            continue
        tms = (ev.get("match") or {}).get("teams") or []
        if len(tms) < 2:
            continue
        def keys(t):
            return {_n(t.get("code", "")), _n(t.get("name", ""))} - {""}
        out.append({
            "utc": _dt(ev.get("startTime")), "state": ev.get("state"),
            "sides": [(keys(tms[0]), (tms[0].get("result") or {}).get("gameWins")),
                      (keys(tms[1]), (tms[1].get("result") or {}).get("gameWins"))],
            "all": keys(tms[0]) | keys(tms[1]),
        })
    return out


def overlay_live(matches, live):
    """Đè trạng thái/tỷ số LIVE của lolesports lên các trận khớp (theo đội + giờ ±3h)."""
    for m in matches:
        mk = {_n(m["a"]["code"]), _n(m["a"]["name"]), _n(m["b"]["code"]), _n(m["b"]["name"])} - {""}
        for e in live:
            if not e["utc"] or abs((e["utc"] - m["utc"]).total_seconds()) > 3 * 3600:
                continue
            if not (mk & e["all"]):
                continue
            if e["state"] in ("inProgress", "completed"):
                m["state"] = e["state"]
                for side in ("a", "b"):
                    sk = {_n(m[side]["code"]), _n(m[side]["name"])} - {""}
                    for (ek, wins) in e["sides"]:
                        if wins is not None and (sk & ek):
                            m[side]["wins"] = wins
            break
    return matches


def fetch_all(**_):
    """Return (matches, ok). Lịch lấy từ cache (nếu có) hoặc Leaguepedia; live đè bằng lolesports."""
    matches = _load_cache()
    ok = True
    if matches is None:                       # cache miss -> lấy Leaguepedia + lưu cache
        matches, ok = fetch_leaguepedia_season()
        if ok:
            _enrich_shorts(matches)
            _save_cache(matches)
        else:
            matches = []
    # LIVE overlay (luôn chạy, nhẹ) — cho 🔴 + tỷ số ván thật của giải Riot mỗi 5 phút
    if matches:
        overlay_live(matches, fetch_lolesports_live())
    seen = {}
    for m in matches:
        if m["utc"]:
            seen.setdefault(m["id"], m)
    return sorted(seen.values(), key=lambda m: m["utc"]), ok

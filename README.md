# LoL Esports → Google Calendar (live)

A self-updating Google Calendar for League of Legends esports — LCK, LPL, LEC, LCP,
international events (MSI, Worlds, First Stand, EWC) plus best-effort extras
(KeSPA Cup, Asian Games, Nations Cup). Writes events straight into a calendar you own
via the Google Calendar API, refreshing ~every 5 minutes during match windows.

Features:

- **Live series score + status icon** — ⚪ upcoming · 🔴 live · ✅ finished (`🔴 [LCK] T1 1-0 GEN`).
- **Per-league colors** (LCK/LCP bold, LEC/LPL lighter, international standout).
- **Playoffs / finals / seeding matches → red**, and **favorite teams → red** (config).
- Times stored in UTC → shown in your local zone; VN time also in the description.

---

## Data sources

- **lolesports API** (`getSchedule`) — Riot leagues: LCK, LPL, LEC, LCP, MSI, Worlds,
  First Stand (and EWC when Riot lists it). One request, gives state + series score +
  block name. Uses the public `x-api-key` (a well-known constant, not a secret).
- **Leaguepedia Cargo API** — best-effort for non-Riot events (KeSPA Cup, Asian Games,
  Nations Cup). Any failure is swallowed so the primary source still syncs. Set the exact
  `OverviewPage` names via the `LOL_EXTRA_PAGES` repo Variable.

> lolesports is an **unofficial** API with no SLA; it can change without notice. If the
> schedule stops updating, the response shape likely changed — check `lol_sources.py`.

---

## Setup (one-time)

**A. Service account** — reuse the one from the WC calendar, or make a new one:
Google Cloud Console → enable **Google Calendar API** → create a **Service account** →
**Keys → JSON** (download) → copy its email.

**B. Calendar** — Google Calendar → **+ → Create new calendar** → name it `LoL Esports`
→ Settings → **Share with specific people** → add the service-account email with
**Make changes to events** → copy the **Calendar ID** (`...@group.calendar.google.com`).

**C. GitHub secrets** — new repo → Settings → Secrets and variables → Actions:
- Secret `GOOGLE_SA_KEY` = the service-account JSON.
- Secret `GCAL_ID` = the `LoL Esports` calendar ID.

**D. Repo must be public** (unlimited Actions minutes for the self-loop). Then **Actions →
Run workflow**.

**E. Phone notifications** — set a default notification on the `LoL Esports` calendar
(Settings → Event notifications → e.g. 10 minutes). Per-event API reminders don't reach
the owner's devices on a shared calendar.

---

## Configuration (no secrets)

Settings → Secrets and variables → Actions → **Variables** tab:

- `FAVORITE_TEAMS` — teams highlighted **red** (by team code or name). Default:
  `T1, HLE, GEN, KT, GAM, TSW`. Example: `T1, GAM, TSW, GEN, HLE, KT, DK`.
- `LOL_EXTRA_PAGES` — comma-separated Leaguepedia OverviewPage names for extra events,
  e.g. `Esports World Cup 2026, KeSPA Cup 2026`. Leave empty to use the defaults in
  `lol_sources.py` (or set to a single space to effectively disable).

### Colors
Google's API allows only 11 preset event colors. Mapping (edit `TIER_COLOR` / `HOT_COLOR`
in `sync_gcal.py`):

| Bucket | Color |
|--------|-------|
| Favorite team · playoffs · finals · seeding | Tomato (red) |
| International (MSI/Worlds/EWC/First Stand/Asiad/Nations/KeSPA) | Grape (purple) |
| LCK | Tangerine (orange) |
| LCP | Blueberry (blue) |
| LEC | Sage (light green) |
| LPL | Lavender (light purple) |
| other leagues | Graphite (grey) |

> "Seeding / seed 1-2" matches are detected from the block/round name (`Playoffs`,
> `Finals`, `Seeding`, `Promotion`, …) — a best-effort approximation, since no source
> flags "this game decides the MSI/Worlds seed" explicitly.

---

## Update cadence
GitHub scheduled cron is best-effort, so the workflow **self-loops**: one run syncs every
5 minutes for ~1 hour, then re-dispatches itself — only while a match is live or within
±90 min. Outside match windows it exits immediately.

## Local run (no writes)
```bash
pip install requests google-api-python-client google-auth
python sync_gcal.py --dry-run          # prints what would be written
python sync_gcal.py --check-active      # exit 0 if a match is near
```

## Files
| File | Role |
|------|------|
| `lol_sources.py` | Fetch + normalize lolesports (+ Leaguepedia) matches |
| `sync_gcal.py` | Colors, favorites, upsert to Google Calendar |
| `.github/workflows/update.yml` | Self-looping 5-min sync |

Unofficial project; not affiliated with Riot Games.

"""
CLV Closing Line Capture Script
================================
Hits The Odds API at game time each day to capture closing odds for active sports.
Appends results to clv_closing_lines.csv in this folder.

Runs via GitHub Actions (.github/workflows/capture.yml) at ~11:55am CT and 6:55pm CT
daily. Captures: MLB (year-round), NFL (Sep-Feb), NCAAF (Aug-Jan), NBA (Oct-Jun).

Setup: Requires ODDS_API_KEY environment variable (set via GitHub Actions secret).
"""

import os, csv, json, datetime, pathlib, urllib.request, urllib.error

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = pathlib.Path(__file__).parent
API_KEY_FILE = SCRIPT_DIR / "api_keys.env"
OUTPUT_CSV = SCRIPT_DIR / "clv_closing_lines.csv"

# Sports to capture and their season months
SPORT_SEASONS = {
    "baseball_mlb":    {"name": "MLB",   "months": list(range(3, 11))},   # Mar-Oct
    "americanfootball_nfl": {"name": "NFL",   "months": [9,10,11,12,1,2]},
    "americanfootball_ncaaf": {"name": "NCAAF", "months": [8,9,10,11,12,1]},
    "basketball_nba":  {"name": "NBA",   "months": [10,11,12,1,2,3,4,5,6]},
}

# Markets to capture for each sport
MARKETS = "h2h,spreads,totals"

# Books to capture (no Pinnacle since API closed July 2025)
BOOKMAKERS = "draftkings,fanduel,caesars,bet365,betmgm"

CSV_HEADERS = ["captured_at", "date", "sport", "home_team", "away_team",
               "market", "book", "home_odds", "away_odds", "over_odds", "under_odds", "total_line"]

# ── Load API Key ──────────────────────────────────────────────────────────────
def load_api_key():
    key = os.environ.get("ODDS_API_KEY") or os.environ.get("THE_ODDS_API_KEY")
    if key:
        return key
    if API_KEY_FILE.exists():
        for line in API_KEY_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("ODDS_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
            if line.startswith("THE_ODDS_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"ODDS_API_KEY not found. Set env var or add to {API_KEY_FILE}")

# ── Fetch odds ────────────────────────────────────────────────────────────────
def fetch_odds(sport_key, api_key):
    url = (
        f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
        f"?apiKey={api_key}&regions=us&markets={MARKETS}&bookmakers={BOOKMAKERS}&oddsFormat=american"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 422:
            return []  # No games today
        raise

# ── Parse & flatten ───────────────────────────────────────────────────────────
def parse_game(game, sport_name, captured_at):
    rows = []
    home = game.get("home_team", "")
    away = game.get("away_team", "")
    commence = game.get("commence_time", "")
    date = commence[:10] if commence else datetime.date.today().isoformat()

    for bm in game.get("bookmakers", []):
        book = bm.get("key", "")
        for mkt in bm.get("markets", []):
            mkt_key = mkt.get("key", "")
            outcomes = {o["name"]: o["price"] for o in mkt.get("outcomes", [])}

            if mkt_key == "h2h":
                rows.append({
                    "captured_at": captured_at, "date": date, "sport": sport_name,
                    "home_team": home, "away_team": away, "market": "moneyline", "book": book,
                    "home_odds": outcomes.get(home, ""), "away_odds": outcomes.get(away, ""),
                    "over_odds": "", "under_odds": "", "total_line": ""
                })
            elif mkt_key == "spreads":
                rows.append({
                    "captured_at": captured_at, "date": date, "sport": sport_name,
                    "home_team": home, "away_team": away, "market": "spread", "book": book,
                    "home_odds": outcomes.get(home, ""), "away_odds": outcomes.get(away, ""),
                    "over_odds": "", "under_odds": "", "total_line": ""
                })
            elif mkt_key == "totals":
                rows.append({
                    "captured_at": captured_at, "date": date, "sport": sport_name,
                    "home_team": home, "away_team": away, "market": "total", "book": book,
                    "home_odds": "", "away_odds": "",
                    "over_odds": outcomes.get("Over", ""), "under_odds": outcomes.get("Under", ""),
                    "total_line": next((o.get("point","") for o in mkt.get("outcomes",[]) if "point" in o), "")
                })
    return rows

# ── Write to CSV ──────────────────────────────────────────────────────────────
def append_to_csv(rows):
    is_new = not OUTPUT_CSV.exists() or OUTPUT_CSV.stat().st_size == 0
    with open(OUTPUT_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)
    return len(rows)

# ── Deduplicate ───────────────────────────────────────────────────────────────
def dedup_csv():
    """Remove duplicate rows (same date+teams+market+book) keeping latest capture."""
    if not OUTPUT_CSV.exists():
        return
    rows = []
    with open(OUTPUT_CSV, newline="") as f:
        rows = list(csv.DictReader(f))

    seen = {}
    for r in rows:
        key = (r["date"], r["home_team"], r["away_team"], r["market"], r["book"])
        seen[key] = r  # keep latest (last write wins)

    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(seen.values())

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    api_key = load_api_key()
    now = datetime.datetime.now()
    captured_at = now.isoformat(timespec="seconds")
    month = now.month

    total_written = 0
    requests_used = 0

    for sport_key, meta in SPORT_SEASONS.items():
        if month not in meta["months"]:
            continue

        print(f"  Fetching {meta['name']}...")
        try:
            games = fetch_odds(sport_key, api_key)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue

        requests_used += 1

        if not games:
            print(f"    No games found")
            continue

        rows = []
        for game in games:
            rows.extend(parse_game(game, meta["name"], captured_at))

        n = append_to_csv(rows)
        total_written += n
        print(f"    {len(games)} games → {n} rows written")

    if total_written > 0:
        dedup_csv()
        print(f"\n✓ Total: {total_written} rows captured, CSV deduped")
        print(f"  Output: {OUTPUT_CSV}")
        # Check remaining quota
        print(f"  API requests used this run: {requests_used} (free tier: 500/month)")
    else:
        print("  No data captured (off-season or no games today)")

if __name__ == "__main__":
    main()

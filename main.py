import requests
import time
import json
import os
from datetime import datetime
import pytz

# ═══════════════════════════════════════
# KONFIGURATION
# ═══════════════════════════════════════
FD_TOKEN   = os.environ.get("FD_TOKEN", "ce2aaecf111c442e885f33c5495635d2")
TG_TOKEN   = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "1599474063")

FD_BASE = "https://api.football-data.org/v4"
TG_BASE = f"https://api.telegram.org/bot{TG_TOKEN}"
HEADERS = {"X-Auth-Token": FD_TOKEN}
ZURICH  = pytz.timezone("Europe/Zurich")

MAX_MINUTE          = 80
GRENZE_WETT_WECHSEL = 60

# ═══════════════════════════════════════
# LIGEN
# ═══════════════════════════════════════
LEAGUES = {
    "PL":  {"name": "Premier League", "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 83},
    "BL1": {"name": "Bundesliga",     "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 75},
    "PD":  {"name": "La Liga",        "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 91},
    "SA":  {"name": "Serie A",        "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 87},
    "FL1": {"name": "Ligue 1",        "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 80},
    "DED": {"name": "Eredivisie",     "tier": 1, "tier_str": "⭐⭐⭐ Tier 1", "hit_rate": 83},
    "ELC": {"name": "Championship",   "tier": 2, "tier_str": "⭐⭐ Tier 2",   "hit_rate": 55},
    "PPL": {"name": "Primeira Liga",  "tier": 2, "tier_str": "⭐⭐ Tier 2",   "hit_rate": 68},
}

# ═══════════════════════════════════════
# STATE
# ═══════════════════════════════════════
standings_cache = {}
alerted_matches = set()
prev_scores     = {}
team_stats_cache = {}  # {team_id: {form, avg_goals, avg_goals_home, avg_goals_away}}

# ═══════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════
def send_telegram(text):
    if not TG_TOKEN:
        print(f"[TELEGRAM N/A]\n{text}")
        return
    try:
        r = requests.post(
            f"{TG_BASE}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
        if r.status_code == 200:
            print(f"✅ Alert gesendet")
        else:
            print(f"❌ Telegram {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"❌ Telegram: {e}")

# ═══════════════════════════════════════
# MINUTE ERMITTELN
# ═══════════════════════════════════════
def get_minute(match):
    api_minute = match.get("minute")
    if api_minute and int(api_minute) > 0:
        return int(api_minute)
    utc_date_str = match.get("utcDate")
    if utc_date_str:
        try:
            utc   = pytz.utc
            start = datetime.strptime(utc_date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=utc)
            diff  = (datetime.now(utc) - start).total_seconds() / 60
            if diff > 45:
                diff -= 15
            return max(1, min(90, int(diff)))
        except Exception:
            pass
    return 1

# ═══════════════════════════════════════
# TEAM STATS LADEN (Form + Tore/Spiel)
# ═══════════════════════════════════════
def get_team_stats(team_id, team_name):
    # Cache prüfen (max 6h alt)
    cached = team_stats_cache.get(team_id)
    if cached and (time.time() - cached["ts"]) < 21600:
        return cached

    stats = {
        "ts":              time.time(),
        "form":            "?????",
        "avg_goals":       0.0,
        "avg_home_goals":  0.0,
        "avg_away_goals":  0.0,
        "games_played":    0,
    }

    try:
        r = requests.get(
            f"{FD_BASE}/teams/{team_id}/matches?status=FINISHED&limit=10",
            headers=HEADERS, timeout=10
        )
        if r.status_code == 200:
            matches = r.json().get("matches", [])
            if not matches:
                team_stats_cache[team_id] = stats
                return stats

            # Form (letzte 5)
            form_chars = []
            total_goals_list = []
            home_goals_list  = []
            away_goals_list  = []

            for m in matches[:10]:
                h_score = m["score"]["fullTime"].get("home") or 0
                a_score = m["score"]["fullTime"].get("away") or 0
                total_goals_list.append(h_score + a_score)

                is_home = m["homeTeam"]["id"] == team_id
                if is_home:
                    home_goals_list.append(h_score + a_score)
                    if h_score > a_score:   form_chars.append("W")
                    elif h_score == a_score: form_chars.append("D")
                    else:                    form_chars.append("L")
                else:
                    away_goals_list.append(h_score + a_score)
                    if a_score > h_score:   form_chars.append("W")
                    elif a_score == h_score: form_chars.append("D")
                    else:                    form_chars.append("L")

            # Form-Emojis (letzte 5)
            def to_emoji(c):
                return "🟢" if c == "W" else ("⚪" if c == "D" else "🔴")

            form_5    = form_chars[:5]
            form_str  = " ".join(to_emoji(c) for c in form_5)
            form_text = " ".join(form_5)  # W W D L W

            stats["form"]           = form_str
            stats["form_text"]      = form_text
            stats["avg_goals"]      = round(sum(total_goals_list) / len(total_goals_list), 1) if total_goals_list else 0.0
            stats["avg_home_goals"] = round(sum(home_goals_list)  / len(home_goals_list),  1) if home_goals_list  else 0.0
            stats["avg_away_goals"] = round(sum(away_goals_list)  / len(away_goals_list),  1) if away_goals_list  else 0.0
            stats["games_played"]   = len(matches)

            print(f"  📊 {team_name}: Form {form_text} | Ø {stats['avg_goals']} Tore/Spiel")

        elif r.status_code == 429:
            print(f"  ⚠️ Team Stats Rate-Limit für {team_name}")
            time.sleep(8)
        else:
            print(f"  ❌ Team Stats {team_name}: HTTP {r.status_code}")

    except Exception as e:
        print(f"  ❌ Team Stats {team_name}: {e}")

    team_stats_cache[team_id] = stats
    return stats

# ═══════════════════════════════════════
# HALBZEIT KONTEXT
# ═══════════════════════════════════════
def get_halfzeit_context(minute):
    if minute <= 45:
        verbleibend = 45 - minute
        if verbleibend <= 7:
            return f"⚠️ 1. HZ — nur noch ~{verbleibend}' bis Pause (Riegel-Risiko!)"
        else:
            return f"1. Halbzeit ({verbleibend}' bis Pause)"
    else:
        return f"2. Halbzeit"

# ═══════════════════════════════════════
# SIGNALSTAERKE
# ═══════════════════════════════════════
def get_signal(diff, minute, tier):
    if diff >= 10 and minute <= 25 and tier == 1:
        return "⭐⭐⭐ STARK"
    elif diff >= 5 or minute <= 45 or tier == 2:
        return "⭐⭐ MITTEL"
    else:
        return "⭐ SCHWACH"

# ═══════════════════════════════════════
# KELLER vs. KELLER
# ═══════════════════════════════════════
def is_bottom_vs_bottom(home_rank, away_rank, total):
    return home_rank >= (total - 3) and away_rank >= (total - 3)

# ═══════════════════════════════════════
# WETT-EMPFEHLUNG JE NACH MINUTE
# ═══════════════════════════════════════
def get_wett_info(minute):
    if minute > GRENZE_WETT_WECHSEL:
        left = 80 - minute
        return {
            "empfehlung": "LIVE Over 0.5 weitere Tore",
            "deadline":   f"⏰ Over 0.5 → noch {left}' möglich (bis Min 80)",
            "hinweis":    "⚠️ Spätes Tor — 1 weiteres Tor reicht"
        }
    else:
        left_15 = GRENZE_WETT_WECHSEL - minute
        left_05 = 80 - minute
        return {
            "empfehlung": "LIVE Over 1.5 Tore",
            "deadline":   (
                f"⏰ Over 1.5 → noch {left_15}' möglich (bis Min 60)\n"
                f"   Over 0.5 → noch {left_05}' möglich (bis Min 80)"
            ),
            "hinweis":    ""
        }

# ═══════════════════════════════════════
# ALERT AUFBAUEN UND SENDEN
# ═══════════════════════════════════════
def send_alert(match, scorer_team, home_rank, away_rank, minute, league_code):
    league    = LEAGUES[league_code]
    home_name = match["homeTeam"]["name"]
    away_name = match["awayTeam"]["name"]
    home_id   = match["homeTeam"]["id"]
    away_id   = match["awayTeam"]["id"]
    score     = match["score"]["fullTime"]
    diff      = abs(home_rank - away_rank)
    signal    = get_signal(diff, minute, league["tier"])
    wett      = get_wett_info(minute)
    halbzeit  = get_halfzeit_context(minute)

    # Team-Stats laden (2 extra Calls)
    time.sleep(6)   # Rate-limit schützen
    home_stats = get_team_stats(home_id, home_name)
    time.sleep(6)
    away_stats = get_team_stats(away_id, away_name)

    # Tore-Kontext: Heim-Ø zuhause, Away-Ø auswärts
    home_avg = home_stats.get("avg_home_goals") or home_stats.get("avg_goals", 0)
    away_avg = away_stats.get("avg_away_goals") or away_stats.get("avg_goals", 0)
    avg_combined = round((home_avg + away_avg) / 2, 1)
    tore_bewertung = "🔥 Torreich" if avg_combined >= 2.5 else ("⚖️ Mittel" if avg_combined >= 1.8 else "🧱 Torarm")

    if scorer_team == "away":
        header   = "🟢 <b>ERSTE WAHL — LIVE ALERT</b>"
        trigger  = f"🎯 {away_name} erzielte das 1. Tor"
        rang_txt = f"📈 Rang-Diff:  {diff} Plätze  (Away schlechter)"
        extra    = ""
    else:
        header   = "🟡 <b>ZWEITE WAHL — LIVE ALERT</b>"
        trigger  = f"🎯 {home_name} erzielte das 1. Tor"
        rang_txt = f"📈 Rang-Diff:  {diff} Plätze  (Heim schlechter)"
        extra    = "⚡ Heimteam ist schlechter platziert\n"

    text = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 Liga:       {league['name']}  {league['tier_str']}\n"
        f"⚽ Spiel:      {home_name} {score['home']}–{score['away']} {away_name}\n"
        f"⏱ Minute:     {minute}'  |  {halbzeit}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{trigger}\n"
        f"{extra}"
        f"📊 Tabelle:    Heim Platz {home_rank}  |  Away Platz {away_rank}\n"
        f"{rang_txt}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 FORM (letzte 5 Spiele):\n"
        f"   {home_name[:18]}: {home_stats.get('form', '?????')}\n"
        f"   {away_name[:18]}: {away_stats.get('form', '?????')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚽ TORE/SPIEL (Saison):\n"
        f"   {home_name[:18]}: Ø {home_avg} (Heimspiele)\n"
        f"   {away_name[:18]}: Ø {away_avg} (Auswärtsspiele)\n"
        f"   Tendenz: {tore_bewertung}  (Ø {avg_combined})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Wette:      {wett['empfehlung']}\n"
        f"🔥 Signal:     {signal}\n"
        f"📉 Liga HR:    {league['hit_rate']}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    if wett["hinweis"]:
        text += f"{wett['hinweis']}\n"
    text += wett["deadline"]

    print(f"\n{'='*55}\n{text}\n{'='*55}\n")
    send_telegram(text)

# ═══════════════════════════════════════
# STANDINGS LADEN
# ═══════════════════════════════════════
def load_standings(league_code):
    try:
        r = requests.get(
            f"{FD_BASE}/competitions/{league_code}/standings",
            headers=HEADERS, timeout=15
        )
        if r.status_code == 200:
            ranks = {}
            for s in r.json().get("standings", []):
                if s.get("type") == "TOTAL":
                    for e in s.get("table", []):
                        ranks[e["team"]["id"]] = e["position"]
            standings_cache[league_code] = ranks
            print(f"  ✅ {LEAGUES[league_code]['name']}: {len(ranks)} Teams")
            return len(ranks)
        elif r.status_code == 429:
            print(f"  ⚠️ {league_code} Rate-limit")
            time.sleep(12)
    except Exception as e:
        print(f"  ❌ {league_code}: {e}")
    return 0

def load_all_standings():
    print(f"\n[{now_str()}] Lade Tabellenränge...")
    for code in LEAGUES:
        load_standings(code)
        time.sleep(7)

# ═══════════════════════════════════════
# MATCH PRÜFEN
# ═══════════════════════════════════════
def check_match(match):
    match_id    = match["id"]
    status      = match.get("status", "")
    league_code = match.get("competition", {}).get("code", "")

    if status not in ["IN_PLAY", "PAUSED"]: return
    if league_code not in LEAGUES:          return
    if match_id in alerted_matches:         return

    minute = get_minute(match)
    if minute > MAX_MINUTE:
        alerted_matches.add(match_id)
        return

    score      = match.get("score", {})
    home_score = score.get("fullTime", {}).get("home") or 0
    away_score = score.get("fullTime", {}).get("away") or 0

    prev      = prev_scores.get(match_id, {"home": 0, "away": 0})
    prev_home = prev["home"]
    prev_away = prev["away"]
    prev_scores[match_id] = {"home": home_score, "away": away_score}

    if home_score == prev_home and away_score == prev_away: return
    if prev_home != 0 or prev_away != 0:
        alerted_matches.add(match_id)
        return

    if   home_score == 1 and away_score == 0: scorer_team = "home"
    elif away_score == 1 and home_score == 0: scorer_team = "away"
    else: return

    standings = standings_cache.get(league_code, {})
    if not standings: return

    home_id   = match["homeTeam"]["id"]
    away_id   = match["awayTeam"]["id"]
    home_rank = standings.get(home_id, 99)
    away_rank = standings.get(away_id, 99)
    total     = len(standings)
    diff      = abs(home_rank - away_rank)

    if diff < 3:
        alerted_matches.add(match_id)
        return
    if is_bottom_vs_bottom(home_rank, away_rank, total):
        alerted_matches.add(match_id)
        return

    heim = match["homeTeam"]["name"]
    away = match["awayTeam"]["name"]

    if scorer_team == "away" and away_rank > home_rank:
        print(f"  🟢 ERSTE WAHL: {heim} vs {away} Min {minute}' Diff {diff}")
        send_alert(match, "away", home_rank, away_rank, minute, league_code)
        alerted_matches.add(match_id)
    elif scorer_team == "home" and home_rank > away_rank:
        print(f"  🟡 ZWEITE WAHL: {heim} vs {away} Min {minute}' Diff {diff}")
        send_alert(match, "home", home_rank, away_rank, minute, league_code)
        alerted_matches.add(match_id)
    else:
        alerted_matches.add(match_id)

# ═══════════════════════════════════════
# LIVE MATCHES ABRUFEN
# ═══════════════════════════════════════
def fetch_live_matches():
    try:
        r = requests.get(f"{FD_BASE}/matches?status=IN_PLAY", headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json().get("matches", [])
        print(f"  ❌ Live Matches: HTTP {r.status_code}")
    except Exception as e:
        print(f"  ❌ Live Matches: {e}")
    return []

# ═══════════════════════════════════════
# HILFSFUNKTIONEN
# ═══════════════════════════════════════
def now_str():
    return datetime.now(ZURICH).strftime("%H:%M:%S")

def should_poll():
    return 11 <= datetime.now(ZURICH).hour <= 23

def send_startup():
    send_telegram(
        "🚀 <b>Fussball Alert Bot v3.0 gestartet!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ 8 Ligen aktiv überwacht\n"
        "📋 Form + Tore/Spiel in jedem Alert\n"
        f"🎯 Alerts: Erstes Tor bis Minute {MAX_MINUTE}\n"
        f"💰 Over 1.5 bis Min {GRENZE_WETT_WECHSEL}  |  Over 0.5 bis Min {MAX_MINUTE}"
    )

# ═══════════════════════════════════════
# HAUPTSCHLEIFE
# ═══════════════════════════════════════
def main():
    print(f"\n{'='*55}")
    print(f"  FUSSBALL ALERT BOT v3.0")
    print(f"  {now_str()}")
    print(f"{'='*55}\n")

    send_startup()
    load_all_standings()

    last_refresh   = time.time()
    polling_active = False

    print(f"\n[{now_str()}] Hauptschleife startet...\n")

    while True:
        if time.time() - last_refresh > 43200:
            load_all_standings()
            last_refresh = time.time()

        if not should_poll():
            if polling_active:
                print(f"[{now_str()}] Ausserhalb Spielzeit — Agent schläft")
                polling_active = False
            time.sleep(60)
            continue

        polling_active = True
        matches = fetch_live_matches()

        if matches:
            relevant = [m for m in matches if m.get("competition", {}).get("code") in LEAGUES]
            if relevant:
                print(f"[{now_str()}] {len(relevant)} Spiele live:", end=" ")
                for m in relevant:
                    min_ = get_minute(m)
                    hs   = m["score"]["fullTime"]["home"]
                    as_  = m["score"]["fullTime"]["away"]
                    print(f"{m['homeTeam']['name'][:10]} {hs}:{as_} {m['awayTeam']['name'][:10]} ({min_}')", end=" | ")
                print()
                for match in relevant:
                    check_match(match)

        time.sleep(10)

if __name__ == "__main__":
    main()

import requests
import time
import json
import os
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import pytz

# ═══════════════════════════════════════
# KONFIGURATION
# ═══════════════════════════════════════
FD_TOKEN       = os.environ.get("FD_TOKEN", "")
TG_TOKEN       = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID     = os.environ.get("TG_CHAT_ID", "")
GSHEET_KEY     = os.environ.get("GSHEET_KEY", "")   # Google Service Account JSON
GSHEET_ID      = os.environ.get("GSHEET_ID", "")    # Google Sheet ID

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
standings_cache  = {}
alerted_matches  = set()
prev_scores      = {}
team_stats_cache = {}
# Tracking: {match_id: {alert_data + laufend aktualisiert}}
tracked_matches  = {}

# ═══════════════════════════════════════
# GOOGLE SHEETS SETUP
# ═══════════════════════════════════════
sheet_client = None
alert_sheet  = None
SHEET_HEADERS = [
    # Basis-Info
    "Datum", "Uhrzeit", "Liga", "Tier",
    "Heimteam", "Auswärtsteam",
    # Tabelle
    "Platz_Heim", "Platz_Away", "Rang_Diff",
    # Trigger
    "Tor_Minute", "Tor_Team", "Regel",
    "Signal", "Halbzeit_Kontext",
    # Form
    "Form_Heim", "Form_Away",
    # Tore/Spiel
    "Avg_Goals_Heim", "Avg_Goals_Away", "Avg_Combined",
    "Tore_Tendenz",
    # Wette
    "Wett_Empfehlung",
    # Ergebnis (wird nach Spielende befüllt)
    "Final_Heim", "Final_Away", "Total_Tore",
    "Tore_nach_Trigger",
    "2tes_Tor_Minute", "3tes_Tor_Minute",
    # Wett-Resultate
    "Over_05_Hit", "Over_15_Hit", "Over_25_Hit",
    # Pattern-Felder
    "Minuten_Bucket",   # 1-15 / 16-30 / 31-45 / 46-60 / 61-80
    "Rang_Bucket",      # 3-5 / 6-9 / 10+
    "Form_Score_Heim",  # Anzahl W in letzten 5
    "Form_Score_Away",
    "Match_ID",
]

def init_sheets():
    global sheet_client, alert_sheet
    if not GSHEET_KEY or not GSHEET_ID:
        print("⚠️ Google Sheets nicht konfiguriert — nur Telegram-Alerts")
        return False
    try:
        creds_dict = json.loads(GSHEET_KEY)
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        creds        = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        sheet_client = gspread.authorize(creds)
        spreadsheet  = sheet_client.open_by_key(GSHEET_ID)

        # Sheet "ALERTS" holen oder erstellen
        try:
            alert_sheet = spreadsheet.worksheet("ALERTS")
            print("✅ Google Sheet 'ALERTS' verbunden")
        except gspread.WorksheetNotFound:
            alert_sheet = spreadsheet.add_worksheet("ALERTS", rows=1000, cols=40)
            alert_sheet.append_row(SHEET_HEADERS)
            # Header formatieren
            alert_sheet.format("A1:AJ1", {
                "backgroundColor": {"red": 0.12, "green": 0.34, "blue": 0.60},
                "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            })
            print("✅ Google Sheet 'ALERTS' erstellt mit Headers")

        # Sheet "ANALYSE" holen oder erstellen
        try:
            spreadsheet.worksheet("ANALYSE")
        except gspread.WorksheetNotFound:
            ana_sheet = spreadsheet.add_worksheet("ANALYSE", rows=50, cols=20)
            setup_analyse_sheet(ana_sheet)
            print("✅ Google Sheet 'ANALYSE' erstellt")

        return True
    except Exception as e:
        print(f"❌ Google Sheets Fehler: {e}")
        return False

def setup_analyse_sheet(ws):
    """Analyse-Sheet mit Formeln befüllen"""
    ws.update("A1", [["=== FUSSBALL ALERT BOT — AUSWERTUNG ==="]])
    ws.update("A3", [
        ["Metrik", "Wert", "Formel"],
        ["Totale Alerts", "=COUNTA(ALERTS!A2:A)", ""],
        ["Over 0.5 Hit-Rate", "=IFERROR(COUNTIF(ALERTS!AB:AB,\"JA\")/COUNTA(ALERTS!AB2:AB),0)", ""],
        ["Over 1.5 Hit-Rate", "=IFERROR(COUNTIF(ALERTS!AC:AC,\"JA\")/COUNTA(ALERTS!AC2:AC),0)", ""],
        ["Over 2.5 Hit-Rate", "=IFERROR(COUNTIF(ALERTS!AD:AD,\"JA\")/COUNTA(ALERTS!AD2:AD),0)", ""],
        ["Ø Tore nach Trigger", "=IFERROR(AVERAGE(ALERTS!X2:X),0)", ""],
        ["Ø Tor-Minute", "=IFERROR(AVERAGE(ALERTS!J2:J),0)", ""],
        [],
        ["=== NACH MINUTEN-BUCKET ==="],
        ["Bucket", "Anzahl", "Over 1.5 %"],
        ["1-15", "=COUNTIF(ALERTS!AE:AE,\"1-15\")", "=IFERROR(COUNTIFS(ALERTS!AE:AE,\"1-15\",ALERTS!AC:AC,\"JA\")/COUNTIF(ALERTS!AE:AE,\"1-15\"),0)"],
        ["16-30", "=COUNTIF(ALERTS!AE:AE,\"16-30\")", "=IFERROR(COUNTIFS(ALERTS!AE:AE,\"16-30\",ALERTS!AC:AC,\"JA\")/COUNTIF(ALERTS!AE:AE,\"16-30\"),0)"],
        ["31-45", "=COUNTIF(ALERTS!AE:AE,\"31-45\")", "=IFERROR(COUNTIFS(ALERTS!AE:AE,\"31-45\",ALERTS!AC:AC,\"JA\")/COUNTIF(ALERTS!AE:AE,\"31-45\"),0)"],
        ["46-60", "=COUNTIF(ALERTS!AE:AE,\"46-60\")", "=IFERROR(COUNTIFS(ALERTS!AE:AE,\"46-60\",ALERTS!AC:AC,\"JA\")/COUNTIF(ALERTS!AE:AE,\"46-60\"),0)"],
        ["61-80", "=COUNTIF(ALERTS!AE:AE,\"61-80\")", "=IFERROR(COUNTIFS(ALERTS!AE:AE,\"61-80\",ALERTS!AC:AC,\"JA\")/COUNTIF(ALERTS!AE:AE,\"61-80\"),0)"],
        [],
        ["=== NACH RANG-DIFFERENZ ==="],
        ["Bucket", "Anzahl", "Over 1.5 %"],
        ["3-5",  "=COUNTIF(ALERTS!AF:AF,\"3-5\")",  "=IFERROR(COUNTIFS(ALERTS!AF:AF,\"3-5\",ALERTS!AC:AC,\"JA\")/COUNTIF(ALERTS!AF:AF,\"3-5\"),0)"],
        ["6-9",  "=COUNTIF(ALERTS!AF:AF,\"6-9\")",  "=IFERROR(COUNTIFS(ALERTS!AF:AF,\"6-9\",ALERTS!AC:AC,\"JA\")/COUNTIF(ALERTS!AF:AF,\"6-9\"),0)"],
        ["10+",  "=COUNTIF(ALERTS!AF:AF,\"10+\")",  "=IFERROR(COUNTIFS(ALERTS!AF:AF,\"10+\",ALERTS!AC:AC,\"JA\")/COUNTIF(ALERTS!AF:AF,\"10+\"),0)"],
        [],
        ["=== NACH LIGA ==="],
        ["Liga", "Anzahl", "Over 1.5 %"],
    ])

def write_alert_to_sheet(data):
    """Alert-Zeile in Google Sheet schreiben"""
    if not alert_sheet:
        return None
    try:
        row = [
            data.get("datum", ""),
            data.get("uhrzeit", ""),
            data.get("liga", ""),
            data.get("tier", ""),
            data.get("heim", ""),
            data.get("away", ""),
            data.get("platz_heim", ""),
            data.get("platz_away", ""),
            data.get("rang_diff", ""),
            data.get("tor_minute", ""),
            data.get("tor_team", ""),
            data.get("regel", ""),
            data.get("signal", ""),
            data.get("halbzeit_kontext", ""),
            data.get("form_heim", ""),
            data.get("form_away", ""),
            data.get("avg_goals_heim", ""),
            data.get("avg_goals_away", ""),
            data.get("avg_combined", ""),
            data.get("tore_tendenz", ""),
            data.get("wett_empfehlung", ""),
            "", "", "", "", "", "",  # Ergebnis-Felder leer (werden später gefüllt)
            "", "", "",              # Over-Hits leer
            data.get("minuten_bucket", ""),
            data.get("rang_bucket", ""),
            data.get("form_score_heim", ""),
            data.get("form_score_away", ""),
            data.get("match_id", ""),
        ]
        result = alert_sheet.append_row(row, value_input_option="USER_ENTERED")
        # Zeilennummer zurückgeben für späteres Update
        all_rows = alert_sheet.get_all_values()
        row_num  = len(all_rows)
        print(f"  ✅ Sheet Zeile {row_num} geschrieben")
        return row_num
    except Exception as e:
        print(f"  ❌ Sheet schreiben: {e}")
        return None

def update_result_in_sheet(row_num, final_heim, final_away, goals_after,
                            min_2nd, min_3rd, over05, over15, over25):
    """Ergebnis nach Spielende in Sheet updaten"""
    if not alert_sheet or not row_num:
        return
    try:
        # Spalten V bis AD (22-30)
        alert_sheet.update(f"V{row_num}:AD{row_num}", [[
            final_heim, final_away,
            final_heim + final_away,
            goals_after,
            min_2nd if min_2nd else "",
            min_3rd if min_3rd else "",
            "JA" if over05 else "NEIN",
            "JA" if over15 else "NEIN",
            "JA" if over25 else "NEIN",
        ]])
        print(f"  ✅ Ergebnis in Sheet Zeile {row_num} aktualisiert")
    except Exception as e:
        print(f"  ❌ Sheet Update: {e}")

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
        if r.status_code != 200:
            print(f"❌ Telegram {r.status_code}: {r.text[:100]}")
    except Exception as e:
        print(f"❌ Telegram: {e}")

# ═══════════════════════════════════════
# MINUTE ERMITTELN
# ═══════════════════════════════════════
def get_minute(match):
    api_min = match.get("minute")
    if api_min and int(api_min) > 0:
        return int(api_min)
    utc_date = match.get("utcDate")
    if utc_date:
        try:
            utc   = pytz.utc
            start = datetime.strptime(utc_date, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=utc)
            diff  = (datetime.now(utc) - start).total_seconds() / 60
            if diff > 45:
                diff -= 15
            return max(1, min(90, int(diff)))
        except Exception:
            pass
    return 1

# ═══════════════════════════════════════
# PATTERN-BUCKETS
# ═══════════════════════════════════════
def minuten_bucket(minute):
    if minute <= 15:  return "1-15"
    if minute <= 30:  return "16-30"
    if minute <= 45:  return "31-45"
    if minute <= 60:  return "46-60"
    return "61-80"

def rang_bucket(diff):
    if diff <= 5:  return "3-5"
    if diff <= 9:  return "6-9"
    return "10+"

def form_score(form_text):
    """Anzahl Siege in letzten 5 Spielen"""
    return form_text.count("W") if form_text else 0

# ═══════════════════════════════════════
# TEAM STATS (Form + Tore)
# ═══════════════════════════════════════
def get_team_stats(team_id, team_name):
    cached = team_stats_cache.get(team_id)
    if cached and (time.time() - cached["ts"]) < 21600:
        return cached

    stats = {"ts": time.time(), "form": "?????", "form_text": "? ? ? ? ?",
             "avg_goals": 0.0, "avg_home_goals": 0.0, "avg_away_goals": 0.0}
    try:
        r = requests.get(f"{FD_BASE}/teams/{team_id}/matches?status=FINISHED&limit=10",
                         headers=HEADERS, timeout=10)
        if r.status_code == 200:
            matches = r.json().get("matches", [])
            if not matches:
                team_stats_cache[team_id] = stats
                return stats

            form_chars, total_g, home_g, away_g = [], [], [], []
            for m in matches[:10]:
                hs = m["score"]["fullTime"].get("home") or 0
                as_ = m["score"]["fullTime"].get("away") or 0
                total_g.append(hs + as_)
                is_home = m["homeTeam"]["id"] == team_id
                if is_home:
                    home_g.append(hs + as_)
                    form_chars.append("W" if hs > as_ else ("D" if hs == as_ else "L"))
                else:
                    away_g.append(hs + as_)
                    form_chars.append("W" if as_ > hs else ("D" if as_ == hs else "L"))

            def emoji(c): return "🟢" if c=="W" else ("⚪" if c=="D" else "🔴")
            stats["form"]           = " ".join(emoji(c) for c in form_chars[:5])
            stats["form_text"]      = " ".join(form_chars[:5])
            stats["avg_goals"]      = round(sum(total_g)/len(total_g), 1) if total_g else 0.0
            stats["avg_home_goals"] = round(sum(home_g)/len(home_g), 1)   if home_g  else 0.0
            stats["avg_away_goals"] = round(sum(away_g)/len(away_g), 1)   if away_g  else 0.0
        elif r.status_code == 429:
            time.sleep(8)
    except Exception as e:
        print(f"  ❌ Stats {team_name}: {e}")

    team_stats_cache[team_id] = stats
    return stats

# ═══════════════════════════════════════
# MATCH EVENTS ABRUFEN (für Nachverfolgung)
# ═══════════════════════════════════════
def get_match_events(match_id):
    """Alle Tor-Events eines Spiels abrufen"""
    try:
        r = requests.get(f"{FD_BASE}/matches/{match_id}", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            data = r.json()
            goals = []
            for g in data.get("goals", []):
                goals.append({
                    "minute":   g.get("minute", 0),
                    "team":     g.get("team", {}).get("id"),
                    "scorer":   g.get("scorer", {}).get("name", "?"),
                })
            return data.get("score", {}), goals, data.get("status", "")
    except Exception as e:
        print(f"  ❌ Match Events {match_id}: {e}")
    return {}, [], ""

# ═══════════════════════════════════════
# HALBZEIT + WETT-INFO
# ═══════════════════════════════════════
def get_halfzeit_context(minute):
    if minute <= 45:
        left = 45 - minute
        if left <= 7:
            return f"⚠️ 1. HZ — {left}' bis Pause (Riegel-Risiko!)"
        return f"1. Halbzeit ({left}' bis Pause)"
    return "2. Halbzeit"

def get_signal(diff, minute, tier):
    if diff >= 10 and minute <= 25 and tier == 1: return "⭐⭐⭐ STARK"
    if diff >= 5  or minute <= 45 or tier == 2:   return "⭐⭐ MITTEL"
    return "⭐ SCHWACH"

def get_wett_info(minute):
    if minute > GRENZE_WETT_WECHSEL:
        left = 80 - minute
        return {
            "empfehlung": "LIVE Over 0.5 weitere Tore",
            "deadline": f"⏰ Over 0.5 → noch {left}' möglich (bis Min 80)",
            "hinweis":  "⚠️ Spätes Tor — 1 weiteres Tor reicht"
        }
    left_15 = GRENZE_WETT_WECHSEL - minute
    left_05 = 80 - minute
    return {
        "empfehlung": "LIVE Over 1.5 Tore",
        "deadline": (
            f"⏰ Over 1.5 → noch {left_15}' möglich (bis Min 60)\n"
            f"   Over 0.5 → noch {left_05}' möglich (bis Min 80)"
        ),
        "hinweis": ""
    }

def is_bottom_vs_bottom(hr, ar, total):
    return hr >= (total - 3) and ar >= (total - 3)

# ═══════════════════════════════════════
# ERGEBNIS NACHVERFOLGEN
# ═══════════════════════════════════════
def follow_up_match(match_id, trigger_data):
    """Nach Spielende finales Ergebnis in Sheet schreiben"""
    try:
        score, goals, status = get_match_events(match_id)
        if status != "FINISHED":
            return False  # Noch nicht fertig

        final_heim = score.get("fullTime", {}).get("home", 0) or 0
        final_away = score.get("fullTime", {}).get("away", 0) or 0
        total_tore = final_heim + final_away

        # Tore nach Trigger (1. Tor war bereits gefallen)
        goals_after = total_tore - 1

        # 2. und 3. Tor-Minute
        sorted_goals = sorted(goals, key=lambda g: g["minute"])
        min_2nd = sorted_goals[1]["minute"] if len(sorted_goals) >= 2 else None
        min_3rd = sorted_goals[2]["minute"] if len(sorted_goals) >= 3 else None

        over05 = goals_after >= 1
        over15 = total_tore >= 2   # inkl. Trigger-Tor
        over25 = total_tore >= 3

        # Sheet updaten
        row_num = trigger_data.get("sheet_row")
        update_result_in_sheet(row_num, final_heim, final_away,
                               goals_after, min_2nd, min_3rd,
                               over05, over15, over25)

        # Kurze Telegram-Nachricht
        result_emoji = "✅" if over15 else "❌"
        heim  = trigger_data["heim"]
        away  = trigger_data["away"]
        min_t = trigger_data["tor_minute"]
        send_telegram(
            f"{result_emoji} <b>SPIEL BEENDET</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚽ {heim} {final_heim}–{final_away} {away}\n"
            f"🎯 Trigger war Minute {min_t}'\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Tore gesamt:     {total_tore}\n"
            f"📊 Tore nach Tor:   {goals_after}\n"
            f"{'⏱ 2. Tor: ' + str(min_2nd) + chr(39) if min_2nd else ''}\n"
            f"{'⏱ 3. Tor: ' + str(min_3rd) + chr(39) if min_3rd else ''}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Over 0.5: {'✅ JA' if over05 else '❌ NEIN'}\n"
            f"Over 1.5: {'✅ JA' if over15 else '❌ NEIN'}\n"
            f"Over 2.5: {'✅ JA' if over25 else '❌ NEIN'}"
        )
        print(f"  ✅ Follow-up {heim} vs {away}: {final_heim}:{final_away} Over1.5={'JA' if over15 else 'NEIN'}")
        return True

    except Exception as e:
        print(f"  ❌ Follow-up {match_id}: {e}")
        return False

# ═══════════════════════════════════════
# ALERT AUFBAUEN + SENDEN + TRACKEN
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
    now       = datetime.now(ZURICH)

    # Team-Stats
    time.sleep(6)
    home_stats = get_team_stats(home_id, home_name)
    time.sleep(6)
    away_stats = get_team_stats(away_id, away_name)

    home_avg     = home_stats.get("avg_home_goals") or home_stats.get("avg_goals", 0)
    away_avg     = away_stats.get("avg_away_goals") or away_stats.get("avg_goals", 0)
    avg_combined = round((home_avg + away_avg) / 2, 1)
    tendenz      = "🔥 Torreich" if avg_combined >= 2.5 else ("⚖️ Mittel" if avg_combined >= 1.8 else "🧱 Torarm")

    if scorer_team == "away":
        header  = "🟢 <b>ERSTE WAHL — LIVE ALERT</b>"
        trigger = f"🎯 {away_name} erzielte das 1. Tor"
        rang_tx = f"📈 Rang-Diff:  {diff} Plätze  (Away schlechter)"
        extra   = ""
        regel   = "1 - Erste Wahl"
    else:
        header  = "🟡 <b>ZWEITE WAHL — LIVE ALERT</b>"
        trigger = f"🎯 {home_name} erzielte das 1. Tor"
        rang_tx = f"📈 Rang-Diff:  {diff} Plätze  (Heim schlechter)"
        extra   = "⚡ Heimteam ist schlechter platziert\n"
        regel   = "2 - Zweite Wahl"

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
        f"{rang_tx}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 FORM (letzte 5):\n"
        f"   {home_name[:18]}: {home_stats.get('form','?????')}\n"
        f"   {away_name[:18]}: {away_stats.get('form','?????')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚽ TORE/SPIEL (Saison):\n"
        f"   {home_name[:18]}: Ø {home_avg} (Heimspiele)\n"
        f"   {away_name[:18]}: Ø {away_avg} (Auswärtsspiele)\n"
        f"   Tendenz: {tendenz}  (Ø {avg_combined})\n"
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

    # ── Google Sheet Tracking ──
    track_data = {
        "datum":           now.strftime("%d.%m.%Y"),
        "uhrzeit":         now.strftime("%H:%M"),
        "liga":            league["name"],
        "tier":            league["tier"],
        "heim":            home_name,
        "away":            away_name,
        "platz_heim":      home_rank,
        "platz_away":      away_rank,
        "rang_diff":       diff,
        "tor_minute":      minute,
        "tor_team":        "Away" if scorer_team=="away" else "Heim",
        "regel":           regel,
        "signal":          signal,
        "halbzeit_kontext": halbzeit,
        "form_heim":       home_stats.get("form_text", "?"),
        "form_away":       away_stats.get("form_text", "?"),
        "avg_goals_heim":  home_avg,
        "avg_goals_away":  away_avg,
        "avg_combined":    avg_combined,
        "tore_tendenz":    tendenz,
        "wett_empfehlung": wett["empfehlung"],
        "minuten_bucket":  minuten_bucket(minute),
        "rang_bucket":     rang_bucket(diff),
        "form_score_heim": form_score(home_stats.get("form_text", "")),
        "form_score_away": form_score(away_stats.get("form_text", "")),
        "match_id":        match["id"],
    }

    row_num = write_alert_to_sheet(track_data)
    track_data["sheet_row"] = row_num
    tracked_matches[match["id"]] = track_data

# ═══════════════════════════════════════
# STANDINGS
# ═══════════════════════════════════════
def load_standings(code):
    try:
        r = requests.get(f"{FD_BASE}/competitions/{code}/standings",
                         headers=HEADERS, timeout=15)
        if r.status_code == 200:
            ranks = {}
            for s in r.json().get("standings", []):
                if s.get("type") == "TOTAL":
                    for e in s["table"]:
                        ranks[e["team"]["id"]] = e["position"]
            standings_cache[code] = ranks
            print(f"  ✅ {LEAGUES[code]['name']}: {len(ranks)} Teams")
            return len(ranks)
        if r.status_code == 429:
            time.sleep(12)
    except Exception as e:
        print(f"  ❌ {code}: {e}")
    return 0

def load_all_standings():
    print(f"\n[{now_str()}] Lade Standings...")
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
    prev       = prev_scores.get(match_id, {"home": 0, "away": 0})
    prev_scores[match_id] = {"home": home_score, "away": away_score}

    if home_score == prev["home"] and away_score == prev["away"]: return
    if prev["home"] != 0 or prev["away"] != 0:
        alerted_matches.add(match_id)
        return

    if   home_score == 1 and away_score == 0: scorer_team = "home"
    elif away_score == 1 and home_score == 0: scorer_team = "away"
    else: return

    standings  = standings_cache.get(league_code, {})
    if not standings: return

    home_id    = match["homeTeam"]["id"]
    away_id    = match["awayTeam"]["id"]
    home_rank  = standings.get(home_id, 99)
    away_rank  = standings.get(away_id, 99)
    total      = len(standings)
    diff       = abs(home_rank - away_rank)

    if diff < 3 or is_bottom_vs_bottom(home_rank, away_rank, total):
        alerted_matches.add(match_id)
        return

    if   scorer_team == "away" and away_rank > home_rank:
        send_alert(match, "away", home_rank, away_rank, minute, league_code)
        alerted_matches.add(match_id)
    elif scorer_team == "home" and home_rank > away_rank:
        send_alert(match, "home", home_rank, away_rank, minute, league_code)
        alerted_matches.add(match_id)
    else:
        alerted_matches.add(match_id)

# ═══════════════════════════════════════
# FOLLOW-UP LOOP (abgeschlossene Spiele)
# ═══════════════════════════════════════
def check_followups():
    finished = []
    for match_id, data in tracked_matches.items():
        if data.get("sheet_row") and not data.get("result_done"):
            done = follow_up_match(match_id, data)
            if done:
                data["result_done"] = True
                finished.append(match_id)
            time.sleep(7)  # Rate-limit
    return finished

# ═══════════════════════════════════════
# LIVE MATCHES
# ═══════════════════════════════════════
def fetch_live_matches():
    try:
        r = requests.get(f"{FD_BASE}/matches?status=IN_PLAY",
                         headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.json().get("matches", [])
    except Exception as e:
        print(f"  ❌ Live: {e}")
    return []

# ═══════════════════════════════════════
# HILFSFUNKTIONEN
# ═══════════════════════════════════════
def now_str():
    return datetime.now(ZURICH).strftime("%H:%M:%S")

def should_poll():
    return 11 <= datetime.now(ZURICH).hour <= 23

def send_startup():
    sheet_status = "✅ Google Sheets aktiv" if alert_sheet else "⚠️ Nur Telegram (kein Sheet)"
    send_telegram(
        "🚀 <b>Fussball Alert Bot v4.0</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ 8 Ligen überwacht\n"
        "📋 Form + Tore/Spiel in Alerts\n"
        "📊 Vollständiges Tracking in Google Sheets\n"
        f"🎯 Alerts bis Minute {MAX_MINUTE}\n"
        f"💰 Over 1.5 bis Min {GRENZE_WETT_WECHSEL} | Over 0.5 bis Min {MAX_MINUTE}\n"
        f"{sheet_status}"
    )

# ═══════════════════════════════════════
# HAUPTSCHLEIFE
# ═══════════════════════════════════════
def main():
    print(f"\n{'='*55}")
    print(f"  FUSSBALL ALERT BOT v4.0 — MIT TRACKING")
    print(f"  {now_str()}")
    print(f"{'='*55}\n")

    init_sheets()
    send_startup()
    load_all_standings()

    last_refresh  = time.time()
    last_followup = time.time()

    print(f"\n[{now_str()}] Hauptschleife startet...\n")

    while True:
        # Standings alle 12h neu laden
        if time.time() - last_refresh > 43200:
            load_all_standings()
            last_refresh = time.time()

        if not should_poll():
            time.sleep(60)
            continue

        # Live Matches prüfen
        matches = fetch_live_matches()
        if matches:
            relevant = [m for m in matches if m.get("competition", {}).get("code") in LEAGUES]
            if relevant:
                print(f"[{now_str()}] {len(relevant)} live:", end=" ")
                for m in relevant:
                    min_ = get_minute(m)
                    hs   = m["score"]["fullTime"]["home"]
                    as_  = m["score"]["fullTime"]["away"]
                    print(f"{m['homeTeam']['name'][:10]} {hs}:{as_} ({min_}')", end=" | ")
                print()
                for match in relevant:
                    check_match(match)

        # Follow-ups alle 5 Minuten prüfen
        if time.time() - last_followup > 300 and tracked_matches:
            pending = {k: v for k, v in tracked_matches.items() if not v.get("result_done")}
            if pending:
                print(f"[{now_str()}] Prüfe {len(pending)} Follow-ups...")
                check_followups()
            last_followup = time.time()

        time.sleep(10)

if __name__ == "__main__":
    main()

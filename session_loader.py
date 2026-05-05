import json
import pandas as pd
from datetime import datetime

def get_session_context(week_num):
    # Load the 3 Data Sources
    with open('configurations/8ball-matches.json', 'r') as f:
        matches_data = json.load(f)

    with open('configurations/8ball-rosters.json', 'r') as f:
        division_data = json.load(f)

    avl_schedule_csv = pd.read_csv('configurations/AVL-schedule.csv')

    # Identify the Match
    all_matches = matches_data[3]['data']['team']['matches']
    match = next((m for m in all_matches if m.get('week') == week_num), None)
    if not match: return None

    # Determine Dynamics
    is_away = "Anti Villian League" in match['away']['name']
    opp_name = match['home']['name'] if is_away else match['away']['name']
    match_date = datetime.fromisoformat(match['startTime']).strftime("%d-%b")

    # Extract Opponent Roster
    teams = division_data[0]['data']['division']['teams']
    opp_team_obj = next((t for t in teams if t['name'] == opp_name), None)
    opp_roster = {p['displayName']: p['skillLevel'] for p in opp_team_obj['roster']} if opp_team_obj else {}

    # Extract AVL Full Roster
    avl_obj = next((t for t in teams if "Anti Villian League" in t['name']), None)
    full_avl_roster = {p['displayName']: p['skillLevel'] for p in avl_obj['roster']} if avl_obj else {}

    # Build first-name -> display-name lookup for CSV matching
    first_to_display = {dn.split()[0]: dn for dn in full_avl_roster}

    # Read scheduled 8-ball players for the week (col index 1 = Week, col index 4 = 8ball)
    col_week  = avl_schedule_csv.columns[1]   # "Week"
    col_8ball = avl_schedule_csv.columns[4]   # "8 ball"

    week_rows = avl_schedule_csv[
        avl_schedule_csv[col_week].astype(str).str.strip() == str(week_num)
    ]

    scheduled_avl = {}
    schedule_first_names = []   # ordered list for display
    guest_first_names    = []   # scheduled names not found in roster (need SL prompt in engine)

    if not week_rows.empty:
        week_start_idx = week_rows.index[0]
        # The week header row + 4 continuation rows hold the 5 players
        for row_idx in range(week_start_idx, week_start_idx + 5):
            if row_idx >= len(avl_schedule_csv):
                break
            cell = str(avl_schedule_csv.iloc[row_idx][col_8ball]).strip()
            if cell and cell.lower() != 'nan':
                schedule_first_names.append(cell)
                display_name = first_to_display.get(cell)
                if display_name:
                    scheduled_avl[display_name] = full_avl_roster[display_name]
                else:
                    # Name in schedule but not in roster — guest/new player
                    guest_first_names.append(cell)

    return {
        "week": week_num,
        "date": match_date,
        "opponent_name": opp_name,
        "opponent_roster": opp_roster,
        "avl_scheduled": scheduled_avl,
        "avl_schedule_names": schedule_first_names,
        "avl_guest_names": guest_first_names,   # unresolved: engine will prompt for SL
        "full_avl_roster": full_avl_roster,
        "home_team": match['home']['name'],
        "away_team": match['away']['name'],
        "location": match['location']['name'] if match.get('location') else "Unknown"
    }
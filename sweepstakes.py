import requests
import json
import csv
import smtplib
import ssl
import os
import time
import yaml
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

# I set the configuration variables.
API_KEY = os.environ.get('API_KEY', 'YOUR_NEW_TOKEN_HERE')
HEADERS = {'X-Auth-Token': API_KEY}
BASE_URL = 'https://api.football-data.org/v4'
STATE_FILE = 'state.json'
MILESTONE_MESSAGES_FILE = 'milestone_messages.yaml'
DEFAULT_SCHEDULE_HOUR_UTC = 8
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_INTERVAL_SECONDS = 6.5
_LAST_REQUEST_MONOTONIC = None

DEFAULT_MILESTONE_MESSAGES = {
    "Red Card": "{team} saw red in the latest match.",
    "90+ Minute Goal": "{team} scored in stoppage time.",
    "Own Goal": "{team} benefited from an own goal by the opposition.",
    "First Two Teams to Extra Time": "{team} featured in the first extra-time game.",
    "Won Penalty Shootout": "{team} won a penalty shootout.",
    "Giant Killer (Beat Top 5)": "{team} pulled off a giant-killing result against a top-5 team.",
    "0-0 Boring Draw": "{team} was involved in a 0-0 draw.",
    "Scored 4+ Goals": "{team} hit four or more goals.",
    "Early Goal (First 5 mins)": "{team} scored inside the first five minutes.",
    "First Goal of Tournament": "{team} scored the first goal of the tournament.",
    "First Knockout Stage Goal": "{team} scored the first knockout-stage goal.",
    "Arsenal Player Goal": "{team} scored through an Arsenal player.",
    "Max Group Points (9)": "{team} finished the group stage with maximum points.",
    "Zero Group Points (0)": "{team} finished the group stage with zero points.",
}

# I define the exact API spelling of the Arsenal squad.
ARSENAL_PLAYERS = ['Ben White', 'Bukayo Saka', 'Christian Nørgaard', 'Cristhian Mosquera', 'David Raya',
                    'Declan Rice', 'Eberechi Eze', 'Gabriel Jesus', 'Gabriel Magalhães', 
                    'Jurrien Timber', 'Kai Havertz', 'Kepa Arrizabalaga', 'Leandro Trossard',
                    'Marli Salmon', 'Martin Ødegaard', 'Martinelli', 'Martín Zubimendi', 
                    'Max Dowman', 'Mikel Merino', 'Myles Lewis-Skelly', 'Noni Madueke', 
                    'Piero Hincapié', 'Riccardo Calafiori', 'Tommy Setford', 'Viktor Gyökeres',
                    'William Saliba']

def load_state():
    # I load the state file to track the one-off milestones.
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            state = {}
    else:
        state = {}

    state.setdefault("first_goal_awarded", False)
    state.setdefault("first_ko_goal_awarded", False)
    state.setdefault("first_extra_time_awarded", False)
    state.setdefault("processed_match_ids", [])

    if not isinstance(state["processed_match_ids"], list):
        state["processed_match_ids"] = []

    return state

def save_state(state):
    # I save the updated state back to the file.
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, sort_keys=True)

def parse_utc_datetime(value):
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)

def load_milestone_messages(file_path=MILESTONE_MESSAGES_FILE):
    # I load custom milestone messages from YAML and fall back to defaults.
    messages = DEFAULT_MILESTONE_MESSAGES.copy()

    if not os.path.exists(file_path):
        return messages

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            loaded = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as exc:
        print(f"Warning: Could not read milestone messages file '{file_path}': {exc}")
        return messages

    if not isinstance(loaded, dict):
        print(
            f"Warning: Milestone messages file '{file_path}' must be a YAML map of milestone to message. "
            "Using defaults."
        )
        return messages

    for milestone, template in loaded.items():
        if isinstance(milestone, str) and isinstance(template, str) and template.strip():
            messages[milestone] = template.strip()

    return messages

def render_milestone_message(milestone_messages, milestone, team, name):
    # I render a message template safely for each milestone line.
    template = milestone_messages.get(milestone, "{team} triggered this milestone.")
    try:
        return template.format(team=team, milestone=milestone, name=name)
    except (KeyError, ValueError):
        return f"{team} triggered {milestone}."

def rate_limited_get(url, headers=None, params=None, timeout=REQUEST_TIMEOUT_SECONDS):
    # I throttle all API calls to stay under the free-tier request policy.
    global _LAST_REQUEST_MONOTONIC

    if _LAST_REQUEST_MONOTONIC is not None:
        elapsed = time.monotonic() - _LAST_REQUEST_MONOTONIC
        if elapsed < REQUEST_INTERVAL_SECONDS:
            time.sleep(REQUEST_INTERVAL_SECONDS - elapsed)

    try:
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
    except requests.RequestException:
        _LAST_REQUEST_MONOTONIC = time.monotonic()
        return None

    _LAST_REQUEST_MONOTONIC = time.monotonic()
    return response

def get_time_window_utc(now_utc=None):
    # I determine the UTC processing window for this run.
    override_start = os.environ.get('WINDOW_START_UTC')
    override_end = os.environ.get('WINDOW_END_UTC')

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    now_utc = now_utc.astimezone(timezone.utc)

    if override_start or override_end:
        if not (override_start and override_end):
            raise ValueError('Both WINDOW_START_UTC and WINDOW_END_UTC must be provided together.')

        start = parse_utc_datetime(override_start)
        end = parse_utc_datetime(override_end)

        if not start or not end:
            raise ValueError('Invalid override window. Use ISO timestamps, e.g. 2026-06-12T08:00:00Z.')
        if start >= end:
            raise ValueError('WINDOW_START_UTC must be earlier than WINDOW_END_UTC.')

        return start, end

    event_name = os.environ.get('GITHUB_EVENT_NAME', '').lower()

    try:
        schedule_hour_utc = int(os.environ.get('SCHEDULE_HOUR_UTC', str(DEFAULT_SCHEDULE_HOUR_UTC)))
    except ValueError:
        schedule_hour_utc = DEFAULT_SCHEDULE_HOUR_UTC

    schedule_hour_utc = max(0, min(23, schedule_hour_utc))

    if event_name == 'schedule' and now_utc.weekday() == 0:
        # Monday scheduled run: catch everything since Friday's scheduled run.
        start = (now_utc - timedelta(days=3)).replace(
            hour=schedule_hour_utc,
            minute=0,
            second=0,
            microsecond=0
        )
    else:
        start = now_utc - timedelta(hours=24)

    return start, now_utc

def get_date_range_for_window(window_start, window_end):
    # I convert the UTC window into date boundaries for the API query.
    return window_start.strftime('%Y-%m-%d'), window_end.strftime('%Y-%m-%d')

def fetch_detailed_matches(start_date, end_date, window_start, window_end, processed_match_ids):
    # I fetch the bulk matches for the date range.
    response = rate_limited_get(f"{BASE_URL}/competitions/WC/matches", headers=HEADERS, params={
        'dateFrom': start_date,
        'dateTo': end_date
    })
    
    if response is None or response.status_code != 200:
        return []

    matches = response.json().get('matches', [])
    detailed_matches = []
    
    for m in matches:
        match_id = m.get('id')
        if match_id is None or match_id in processed_match_ids:
            continue

        res = rate_limited_get(f"{BASE_URL}/matches/{match_id}", headers=HEADERS)
        if res is not None and res.status_code == 200:
            match_data = res.json()
            kickoff_time = parse_utc_datetime(match_data.get('utcDate'))
            if kickoff_time and window_start <= kickoff_time < window_end:
                detailed_matches.append(match_data)
        
    return detailed_matches

def update_processed_match_ids(state, matches):
    # I keep track of processed matches so reruns do not send duplicate emails.
    processed_ids = set(state.get('processed_match_ids', []))

    for match in matches:
        match_id = match.get('id')
        if match_id is not None:
            processed_ids.add(match_id)

    state['processed_match_ids'] = sorted(processed_ids)
    return state

def process_milestones(matches, state):
    # I store the winning teams.
    results = {
        "Red Card": [], "90+ Minute Goal": [], "Own Goal": [],
        "First Two Teams to Extra Time": [], "Won Penalty Shootout": [], "Giant Killer (Beat Top 5)": [],
        "0-0 Boring Draw": [], "Scored 4+ Goals": [], "Early Goal (First 5 mins)": [],
        "First Goal of Tournament": [], "First Knockout Stage Goal": [], "Arsenal Player Goal": []
    }
    top_5 = ["Argentina", "Spain", "England", "France", "Brazil"]

    # I sort the matches chronologically to ensure the 'firsts' are accurate.
    matches.sort(key=lambda x: x.get('utcDate', ''))

    for match in matches:
        home_team = match['homeTeam']['name']
        away_team = match['awayTeam']['name']
        score = match.get('score', {})
        stage = match.get('stage', 'GROUP_STAGE')

        ft_home = score.get('fullTime', {}).get('home')
        ft_away = score.get('fullTime', {}).get('away')
        
        # I check full-time milestones.
        if ft_home == 0 and ft_away == 0:
            results["0-0 Boring Draw"].extend([home_team, away_team])
        if ft_home is not None and ft_home >= 4:
            results["Scored 4+ Goals"].append(home_team)
        if ft_away is not None and ft_away >= 4:
            results["Scored 4+ Goals"].append(away_team)

        # I check extra time and penalties.
        duration = score.get('duration', 'REGULAR')
        if duration in ['EXTRA_TIME', 'PENALTY_SHOOTOUT']:
            if not state["first_extra_time_awarded"]:
                results["First Two Teams to Extra Time"].extend([home_team, away_team])
                state["first_extra_time_awarded"] = True
            
        if duration == 'PENALTY_SHOOTOUT':
            pen_home = score.get('penalties', {}).get('home', 0)
            pen_away = score.get('penalties', {}).get('away', 0)
            if pen_home > pen_away:
                results["Won Penalty Shootout"].append(home_team)
            elif pen_away > pen_home:
                results["Won Penalty Shootout"].append(away_team)

        # I check for giant killers.
        if stage != 'GROUP_STAGE':
            winner_enum = score.get('winner')
            winner, loser = None, None
            if winner_enum == 'HOME_TEAM':
                winner, loser = home_team, away_team
            elif winner_enum == 'AWAY_TEAM':
                winner, loser = away_team, home_team
                
            if winner and loser in top_5:
                results["Giant Killer (Beat Top 5)"].append(winner)

        # I check in-game events.
        for goal in match.get('goals', []):
            minute = goal.get('minute', 0)
            scorer_name = goal.get('scorer', {}).get('name')
            awarded_team = goal.get('team', {}).get('name')
            
            if goal.get('type') in ['OWN', 'OWN_GOAL']:
                harmed_team = None
                if awarded_team == home_team:
                    harmed_team = away_team
                elif awarded_team == away_team:
                    harmed_team = home_team

                if harmed_team:
                    results["Own Goal"].append(harmed_team)
                
            if minute >= 90 and awarded_team:
                results["90+ Minute Goal"].append(awarded_team)
                
            if minute <= 5 and awarded_team:
                results["Early Goal (First 5 mins)"].append(awarded_team)
                
            if scorer_name in ARSENAL_PLAYERS and awarded_team:
                results["Arsenal Player Goal"].append(awarded_team)
                
            if awarded_team and not state["first_goal_awarded"]:
                results["First Goal of Tournament"].append(awarded_team)
                state["first_goal_awarded"] = True
                
            if awarded_team and stage != 'GROUP_STAGE' and not state["first_ko_goal_awarded"]:
                results["First Knockout Stage Goal"].append(awarded_team)
                state["first_ko_goal_awarded"] = True

        for booking in match.get('bookings', []):
            if booking.get('card') in ['RED', 'RED_CARD', 'YELLOW_RED']:
                team_name = booking.get('team', {}).get('name')
                if team_name: results["Red Card"].append(team_name)

    return results, state

def check_standings():
    # I fetch group standings for max or zero points.
    response = rate_limited_get(f"{BASE_URL}/competitions/WC/standings", headers=HEADERS)
    if response is None or response.status_code != 200:
        return {"Max Group Points (9)": [], "Zero Group Points (0)": []}
    
    standings_data = response.json().get('standings', [])
    results = {"Max Group Points (9)": [], "Zero Group Points (0)": []}
    
    for group in standings_data:
        if group.get('type') == 'TOTAL':
            for table_row in group.get('table', []):
                played = table_row.get('playedGames', 0)
                points = table_row.get('points', 0)
                team_name = table_row.get('team', {}).get('name')
                
                if played == 3:
                    if points == 9:
                        results["Max Group Points (9)"].append(team_name)
                    elif points == 0:
                        results["Zero Group Points (0)"].append(team_name)
    return results

def get_team_milestone_summary(final_report):
    # I map milestones directly to the teams.
    team_summary = {}
    for milestone, teams in final_report.items():
        if teams:
            for team in set(teams):
                if team not in team_summary:
                    team_summary[team] = []
                team_summary[team].append(milestone)
    return team_summary

def get_notification_targets(team_summary, milestone_messages, tsv_file_path):
    # I map participants to winning milestones, grouped by email for one message per run.
    targets_by_email = {}

    if not os.path.exists(tsv_file_path):
        raise FileNotFoundError(
            f"Participants file '{tsv_file_path}' was not found. "
            "If this is a GitHub Actions run, set secret PARTICIPANTS_REAL_TSV_B64 "
            "or use PARTICIPANTS_FILE=assigned_participants.tsv for dry runs."
        )

    with open(tsv_file_path, mode='r', encoding='utf-8') as file:
        reader = csv.reader(file, delimiter='\t')
        next(reader, None)

        for row in reader:
            if len(row) < 6:
                continue

            email = row[1].strip()
            name = row[2].strip()
            wants_updates = row[4].strip().lower().startswith('yes')
            assigned_teams = [t.strip() for t in row[5].split(',')]

            if not wants_updates or not email:
                continue

            if email not in targets_by_email:
                targets_by_email[email] = {
                    'email': email,
                    'name': name,
                    'entries': [],
                    'seen': set(),
                }

            target = targets_by_email[email]
            if not target['name'] and name:
                target['name'] = name

            for team in assigned_teams:
                milestones = team_summary.get(team, [])
                for milestone in milestones:
                    key = (team, milestone)
                    if key in target['seen']:
                        continue

                    target['seen'].add(key)
                    target['entries'].append({
                        'team': team,
                        'milestone': milestone,
                        'message': render_milestone_message(milestone_messages, milestone, team, target['name']),
                    })

    targets = []
    for email in sorted(targets_by_email.keys()):
        target = targets_by_email[email]
        if target['entries']:
            target.pop('seen', None)
            targets.append(target)

    return targets

def process_participants_and_email(team_summary, milestone_messages, tsv_file_path='assigned_participants.tsv', dry_run=False):
    # I read the TSV and send (or simulate) emails to winners.
    targets = get_notification_targets(team_summary, milestone_messages, tsv_file_path)
    bcc_email = os.environ.get('BCC_EMAIL', '').strip()

    if dry_run:
        print(f"DRY RUN enabled. Would send {len(targets)} notification(s).")
        if bcc_email:
            print(f"BCC copy enabled for: {bcc_email}")
        for target in targets:
            print(f"- {target['email']} ({target['name']}):")
            for entry in target['entries']:
                print(f"  • {entry['milestone']} [{entry['team']}]: {entry['message']}")
        return targets

    if not targets:
        return targets

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as server:
        server.login(os.environ.get('SENDER_EMAIL'), os.environ.get('SENDER_PASSWORD'))

        for target in targets:
            msg = EmailMessage()
            msg['Subject'] = 'World Cup Sweepstakes: You won a biscuit!'
            msg['From'] = os.environ.get('SENDER_EMAIL')
            msg['To'] = target['email']
            if bcc_email:
                msg['Bcc'] = bcc_email

            body = (
                f"Hi {target['name']},\n\n"
                "Good news! Your team(s) triggered sweepstakes milestones in the latest run, "
                "which means you're entitled to a sweet treat.\n\n"
                "Here is what happened this run:\n"
            )
            body += '\n'.join(
                f"• {entry['milestone']} [{entry['team']}]: {entry['message']}"
                for entry in target['entries']
            )
            body += "\n\nI'll see you in the office to hand over your winnings.\n\nCheers,\nTobi"

            msg.set_content(body)
            server.send_message(msg)

    return targets

if __name__ == "__main__":
    # I execute the full pipeline.
    dry_run = os.environ.get('DRY_RUN', '').lower() in {'1', 'true', 'yes'}
    participants_file = os.environ.get('PARTICIPANTS_FILE', 'assigned_participants.tsv')
    milestone_messages_file = os.environ.get('MILESTONE_MESSAGES_FILE', MILESTONE_MESSAGES_FILE)

    window_start, window_end = get_time_window_utc()
    start_date, end_date = get_date_range_for_window(window_start, window_end)

    print(f"Processing window UTC: ({window_start.isoformat()}, {window_end.isoformat()}]")
    state = load_state()
    processed_match_ids = set(state.get('processed_match_ids', []))
    
    matches = fetch_detailed_matches(start_date, end_date, window_start, window_end, processed_match_ids)
    print(f"Fetched {len(matches)} new match(es) inside the processing window.")

    daily_results, new_state = process_milestones(matches, state)
    new_state = update_processed_match_ids(new_state, matches)
    standings_results = check_standings()
    
    # I save the state for the next run.
    save_state(new_state)
    
    final_report = {**daily_results, **standings_results}
    team_summary = get_team_milestone_summary(final_report)
    milestone_messages = load_milestone_messages(milestone_messages_file)
    
    if team_summary:
        notifications = process_participants_and_email(
            team_summary,
            milestone_messages,
            participants_file,
            dry_run=dry_run,
        )
        print(f"Prepared {len(notifications)} participant notification(s).")
    else:
        print('No milestone winners in this run.')

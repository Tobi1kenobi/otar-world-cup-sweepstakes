import requests
import json
import csv
import smtplib
import ssl
import os
import random
import yaml
from collections import Counter
from datetime import datetime, timezone
from email.message import EmailMessage

# I set the configuration variables.
API_KEY = os.environ.get('API_KEY', 'YOUR_NEW_TOKEN_HERE')
HEADERS = {'X-Auth-Token': API_KEY}
BASE_URL = 'https://api.football-data.org/v4'
DEFAULT_STATE_FILE = 'state.json'
DEFAULT_LEADERBOARD_STATE_FILE = 'leaderboard_state_primary.json'
SECOND_GROUP_PARTICIPANTS_FILE = 'assigned_participants_second_group.tsv'
SECOND_GROUP_LEADERBOARD_STATE_FILE = 'leaderboard_state_second_group.json'
MILESTONE_MESSAGES_FILE = 'milestone_messages.yaml'
REQUEST_TIMEOUT_SECONDS = 30

MATCH_UNFOLD_HEADERS = {
    'X-Unfold-Goals': 'true',
    'X-Unfold-Bookings': 'true',
}

DEFAULT_MILESTONE_MESSAGES = {
    "Red Card": "{team} saw red in the latest match.",
    "90+ Minute Goal": "{team} scored in stoppage time.",
    "Own Goal": "{team} had a player score an own goal in the latest match.",
    "Hat Trick": "{team} had a player score a hat trick.",
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

EVENT_BASED_MILESTONES = [
    "Red Card",
    "90+ Minute Goal",
    "Own Goal",
    "Hat Trick",
    "Early Goal (First 5 mins)",
    "First Goal of Tournament",
    "First Knockout Stage Goal",
    "Arsenal Player Goal",
]

# I define the exact API spelling of the Arsenal squad.
ARSENAL_PLAYERS = ['Ben White', 'Bukayo Saka', 'Christian Nørgaard', 'Cristhian Mosquera', 'David Raya',
                    'Declan Rice', 'Eberechi Eze', 'Gabriel Jesus', 'Gabriel Magalhães', 
                    'Jurrien Timber', 'Kai Havertz', 'Kepa Arrizabalaga', 'Leandro Trossard',
                    'Marli Salmon', 'Martin Ødegaard', 'Martinelli', 'Martín Zubimendi', 
                    'Max Dowman', 'Mikel Merino', 'Myles Lewis-Skelly', 'Noni Madueke', 
                    'Piero Hincapié', 'Riccardo Calafiori', 'Tommy Setford', 'Viktor Gyökeres',
                    'William Saliba']

def get_team_name(team_payload):
    # I normalize team names from supported API payload fields.
    if not isinstance(team_payload, dict):
        return None

    for key in ('name', 'shortName', 'tla'):
        value = team_payload.get(key)
        if isinstance(value, str):
            value = value.strip()
            if value:
                return value

    return None

def parse_score_value(value):
    # I parse score values that may arrive as strings or null.
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def is_own_goal(goal):
    return goal.get('type') in ['OWN', 'OWN_GOAL']

def get_opponent_team_name(team_name, home_team, away_team):
    if team_name == home_team:
        return away_team
    if team_name == away_team:
        return home_team
    return None

def get_goal_credit_team_name(goal, home_team, away_team):
    goal_team = get_team_name(goal.get('team', {}))
    if is_own_goal(goal):
        return get_opponent_team_name(goal_team, home_team, away_team)
    return goal_team

def normalize_milestone_templates(template_value):
    # I normalize a milestone template value into a non-empty list of strings.
    if isinstance(template_value, str):
        cleaned = template_value.strip()
        return [cleaned] if cleaned else []

    if isinstance(template_value, list):
        templates = []
        for item in template_value:
            if isinstance(item, str):
                cleaned = item.strip()
                if cleaned:
                    templates.append(cleaned)
        return templates

    return []

def load_state(state_file_path=DEFAULT_STATE_FILE, force_blank=False):
    # I load the state file to track the one-off milestones.
    if force_blank:
        state = {}
    elif os.path.exists(state_file_path):
        try:
            with open(state_file_path, 'r') as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            state = {}
    else:
        state = {}

    state.setdefault("first_goal_awarded", False)
    state.setdefault("first_ko_goal_awarded", False)
    state.setdefault("first_extra_time_awarded", False)
    state.setdefault("max_group_points_awarded_teams", [])
    state.setdefault("zero_group_points_awarded_teams", [])
    state.setdefault("processed_match_ids", [])

    if not isinstance(state["processed_match_ids"], list):
        state["processed_match_ids"] = []

    if not isinstance(state["max_group_points_awarded_teams"], list):
        state["max_group_points_awarded_teams"] = []
    if not isinstance(state["zero_group_points_awarded_teams"], list):
        state["zero_group_points_awarded_teams"] = []

    state["max_group_points_awarded_teams"] = sorted({
        team_name.strip()
        for team_name in state["max_group_points_awarded_teams"]
        if isinstance(team_name, str) and team_name.strip()
    })
    state["zero_group_points_awarded_teams"] = sorted({
        team_name.strip()
        for team_name in state["zero_group_points_awarded_teams"]
        if isinstance(team_name, str) and team_name.strip()
    })

    return state

def save_state(state, state_file_path=DEFAULT_STATE_FILE):
    # I save the updated state back to the file.
    with open(state_file_path, 'w') as f:
        json.dump(state, f, indent=2, sort_keys=True)

def load_milestone_messages(file_path=MILESTONE_MESSAGES_FILE):
    # I load custom milestone messages from YAML and fall back to defaults.
    messages = {
        milestone: normalize_milestone_templates(template)
        for milestone, template in DEFAULT_MILESTONE_MESSAGES.items()
    }

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
            f"Warning: Milestone messages file '{file_path}' must be a YAML map of milestone to message/list. "
            "Using defaults."
        )
        return messages

    for milestone, template in loaded.items():
        if not isinstance(milestone, str):
            continue

        normalized_templates = normalize_milestone_templates(template)
        if normalized_templates:
            messages[milestone] = normalized_templates

    return messages

def render_milestone_message(milestone_messages, milestone, team, name, used_templates_by_milestone=None):
    # I render one random milestone template, avoiding repeats within a recipient email when possible.
    templates = normalize_milestone_templates(milestone_messages.get(milestone))
    if not templates:
        templates = ["{team} triggered this milestone."]

    if isinstance(used_templates_by_milestone, dict):
        used_templates = used_templates_by_milestone.setdefault(milestone, set())
        available_templates = [template for template in templates if template not in used_templates]
        template_pool = available_templates if available_templates else templates
        template = random.choice(template_pool)
        used_templates.add(template)
    else:
        template = random.choice(templates)

    try:
        return template.format(team=team, milestone=milestone, name=name)
    except (KeyError, ValueError):
        return f"{team} triggered {milestone}."

def api_get(url, headers=None, params=None, timeout=REQUEST_TIMEOUT_SECONDS):
    # I wrap GET requests to centralize timeout and network-error handling.
    try:
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
    except requests.RequestException:
        return None
    return response

def fetch_unprocessed_finished_matches(processed_match_ids):
    # I fetch all finished WC matches, then keep only those not processed yet.
    match_headers = {**HEADERS, **MATCH_UNFOLD_HEADERS}
    response = api_get(
        f"{BASE_URL}/competitions/WC/matches",
        headers=match_headers,
        params={'status': 'FINISHED'}
    )
    
    if response is None:
        print('Warning: Failed to fetch competition matches (network error or timeout).')
        return []

    if response.status_code != 200:
        body_preview = (response.text or '').replace('\n', ' ')[:200]
        print(
            f"Warning: Failed to fetch competition matches ({response.status_code}). "
            f"Response: {body_preview}"
        )
        return []

    matches = response.json().get('matches', [])
    unprocessed_matches = []
    detail_fallback_count = 0
    
    for m in matches:
        match_id = m.get('id')
        if match_id is None or match_id in processed_match_ids:
            continue

        match_to_use = m
        if ('goals' not in m) and ('bookings' not in m):
            res = api_get(f"{BASE_URL}/matches/{match_id}", headers=match_headers)
            if res is not None and res.status_code == 200:
                match_to_use = res.json()
            else:
                detail_fallback_count += 1
                status = 'no response' if res is None else str(res.status_code)
                body_preview = '' if res is None else (res.text or '').replace('\n', ' ')[:180]
                print(
                    f"Warning: Detailed match data unavailable for match {match_id} ({status}). "
                    "Using competition list payload fallback. "
                    f"Response: {body_preview}"
                )

        if match_to_use.get('status') != 'FINISHED':
            # I skip unfinished matches so milestones are evaluated on final data.
            detail_fallback_count += 1
            continue

        unprocessed_matches.append(match_to_use)

    # I keep processing deterministic without relying on date windows.
    unprocessed_matches.sort(key=lambda x: x.get('id', 0))

    if detail_fallback_count > 0:
        print(
            f"Warning: Used fallback or skipped non-finished data for {detail_fallback_count} match(es). "
            "Event-based milestones may be incomplete for those matches."
        )

    return unprocessed_matches

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
        "Hat Trick": [],
        "First Two Teams to Extra Time": [], "Won Penalty Shootout": [], "Giant Killer (Beat Top 5)": [],
        "0-0 Boring Draw": [], "Scored 4+ Goals": [], "Early Goal (First 5 mins)": [],
        "First Goal of Tournament": [], "First Knockout Stage Goal": [], "Arsenal Player Goal": []
    }
    top_5 = ["Argentina", "Spain", "England", "France", "Brazil"]

    # I sort the matches chronologically to ensure the 'firsts' are accurate.
    matches.sort(key=lambda x: x.get('utcDate', ''))

    for match in matches:
        home_team = get_team_name(match.get('homeTeam', {}))
        away_team = get_team_name(match.get('awayTeam', {}))
        if not home_team or not away_team:
            # I skip malformed team payloads to avoid partial milestone awards.
            continue

        score = match.get('score', {})
        stage = match.get('stage', 'GROUP_STAGE')

        ft_home = parse_score_value(score.get('fullTime', {}).get('home'))
        ft_away = parse_score_value(score.get('fullTime', {}).get('away'))
        if ft_home is None and ft_away is None:
            # I fall back to regularTime when fullTime is missing in some payloads.
            ft_home = parse_score_value(score.get('regularTime', {}).get('home'))
            ft_away = parse_score_value(score.get('regularTime', {}).get('away'))
        
        # I check full-time milestones.
        if ft_home == 0 and ft_away == 0:
            results["0-0 Boring Draw"].append(home_team)
            results["0-0 Boring Draw"].append(away_team)
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
        goals_by_scorer_and_team = {}
        for goal in match.get('goals', []):
            minute = goal.get('minute', 0)
            scorer_name = goal.get('scorer', {}).get('name')
            goal_team = get_team_name(goal.get('team', {}))
            credit_team = get_goal_credit_team_name(goal, home_team, away_team)
            own_goal = is_own_goal(goal)
            
            if own_goal and goal_team:
                # I award own goals to the team of the player who scored the own goal.
                results["Own Goal"].append(goal_team)
                
            if minute >= 90 and credit_team:
                results["90+ Minute Goal"].append(credit_team)
                
            if minute <= 5 and credit_team:
                results["Early Goal (First 5 mins)"].append(credit_team)
                
            if not own_goal and scorer_name in ARSENAL_PLAYERS and credit_team:
                results["Arsenal Player Goal"].append(credit_team)
                
            if credit_team and not state["first_goal_awarded"]:
                results["First Goal of Tournament"].append(credit_team)
                state["first_goal_awarded"] = True
                
            if credit_team and stage != 'GROUP_STAGE' and not state["first_ko_goal_awarded"]:
                results["First Knockout Stage Goal"].append(credit_team)
                state["first_ko_goal_awarded"] = True

            if not own_goal and scorer_name and credit_team:
                scorer_key = (credit_team, scorer_name)
                goals_by_scorer_and_team[scorer_key] = goals_by_scorer_and_team.get(scorer_key, 0) + 1

        for (team_name, _scorer_name), goal_count in goals_by_scorer_and_team.items():
            if goal_count >= 3:
                results["Hat Trick"].append(team_name)

        for booking in match.get('bookings', []):
            if booking.get('card') in ['RED', 'RED_CARD', 'YELLOW_RED']:
                team_name = booking.get('team', {}).get('name')
                if team_name: results["Red Card"].append(team_name)

    return results, state

def check_standings(state):
    # I fetch group standings for max or zero points.
    response = api_get(f"{BASE_URL}/competitions/WC/standings", headers=HEADERS)
    results = {"Max Group Points (9)": [], "Zero Group Points (0)": []}
    max_points_awarded = set(state.get("max_group_points_awarded_teams", []))
    zero_points_awarded = set(state.get("zero_group_points_awarded_teams", []))

    if response is None:
        print('Warning: Failed to fetch standings (network error or timeout).')
        return results, state

    if response.status_code != 200:
        body_preview = (response.text or '').replace('\n', ' ')[:200]
        print(
            f"Warning: Failed to fetch standings ({response.status_code}). "
            f"Response: {body_preview}"
        )
        return results, state
    
    standings_data = response.json().get('standings', [])
    
    for group in standings_data:
        if group.get('type') == 'TOTAL':
            for table_row in group.get('table', []):
                played = table_row.get('playedGames', 0)
                points = table_row.get('points', 0)
                team_name = table_row.get('team', {}).get('name')
                
                if played == 3:
                    if points == 9 and team_name and team_name not in max_points_awarded:
                        results["Max Group Points (9)"].append(team_name)
                        max_points_awarded.add(team_name)
                    elif points == 0 and team_name and team_name not in zero_points_awarded:
                        results["Zero Group Points (0)"].append(team_name)
                        zero_points_awarded.add(team_name)

    state["max_group_points_awarded_teams"] = sorted(max_points_awarded)
    state["zero_group_points_awarded_teams"] = sorted(zero_points_awarded)
    return results, state

def get_team_milestone_summary(final_report):
    # I map milestones directly to the teams, preserving repeated event occurrences.
    team_summary = {}
    for milestone, teams in final_report.items():
        if teams:
            for team in teams:
                if team not in team_summary:
                    team_summary[team] = []
                team_summary[team].append(milestone)
    return team_summary

def get_event_payload_coverage(matches):
    # I estimate how many matches include event-level fields (goals/bookings).
    with_event_payload = 0
    without_event_payload = 0

    for match in matches:
        if ('goals' in match) or ('bookings' in match):
            with_event_payload += 1
        else:
            without_event_payload += 1

    return with_event_payload, without_event_payload

def print_milestone_debug_summary(final_report, team_summary):
    # I print a concise detection summary to debug milestone matching.
    non_empty = []

    for milestone, teams in final_report.items():
        unique_teams = sorted(set(teams))
        if unique_teams:
            non_empty.append((milestone, unique_teams))

    print('DEBUG: Milestone summary for this run')
    if not non_empty:
        print('DEBUG: No milestones were triggered in this run.')
    else:
        for milestone, teams in non_empty:
            print(f"DEBUG: {milestone}: {', '.join(teams)}")

    print(f"DEBUG: Teams with at least one milestone: {len(team_summary)}")

def get_notification_targets(team_summary, milestone_messages, tsv_file_path):
    # I map participants to winning milestones, grouped by email for one message per run.
    targets_by_email = {}

    if not os.path.exists(tsv_file_path):
        raise FileNotFoundError(
            f"Participants file '{tsv_file_path}' was not found. "
            "If this is a GitHub Actions run, set secret PARTICIPANTS_REAL_TSV or "
            "PARTICIPANTS_SECOND_GROUP_TSV as needed, or use PARTICIPANTS_FILE=assigned_participants.tsv "
            "for dry runs."
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
            assigned_teams = [t.strip() for t in row[5].split(',') if t.strip()]
            unique_assigned_teams = list(dict.fromkeys(assigned_teams))

            if not wants_updates or not email:
                continue

            if email not in targets_by_email:
                targets_by_email[email] = {
                    'email': email,
                    'name': name,
                    'entries': [],
                    'used_templates_by_milestone': {},
                }

            target = targets_by_email[email]
            if not target['name'] and name:
                target['name'] = name

            for team in unique_assigned_teams:
                milestones = team_summary.get(team, [])
                for milestone in milestones:
                    target['entries'].append({
                        'team': team,
                        'milestone': milestone,
                        'message': render_milestone_message(
                            milestone_messages,
                            milestone,
                            team,
                            target['name'],
                            target['used_templates_by_milestone'],
                        ),
                    })

    targets = []
    for email in sorted(targets_by_email.keys()):
        target = targets_by_email[email]
        if target['entries']:
            target.pop('used_templates_by_milestone', None)
            targets.append(target)

    return targets

def get_default_leaderboard_state_file(tsv_file_path):
    participant_file_name = os.path.basename((tsv_file_path or '').strip().lower())
    if participant_file_name == SECOND_GROUP_PARTICIPANTS_FILE.lower():
        return SECOND_GROUP_LEADERBOARD_STATE_FILE
    return DEFAULT_LEADERBOARD_STATE_FILE

def get_milestone_counter(raw_counts):
    counts = Counter()
    if not isinstance(raw_counts, dict):
        return counts

    for milestone, value in raw_counts.items():
        if not isinstance(milestone, str):
            continue
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if count > 0:
            counts[milestone] = count

    return counts

def get_participant_slot_id(slot_number):
    return f"slot:{slot_number}"

def get_cookie_leaderboard(team_summary, tsv_file_path):
    # I rank participants by cookie count and keep every team+milestone reason.
    leaderboard = []

    if not os.path.exists(tsv_file_path):
        raise FileNotFoundError(
            f"Participants file '{tsv_file_path}' was not found. "
            "If this is a GitHub Actions run, set secret PARTICIPANTS_REAL_TSV or "
            "PARTICIPANTS_SECOND_GROUP_TSV as needed, or use PARTICIPANTS_FILE=assigned_participants.tsv "
            "for dry runs."
        )

    with open(tsv_file_path, mode='r', encoding='utf-8') as file:
        reader = csv.reader(file, delimiter='\t')
        next(reader, None)

        participant_slot = 0
        for row in reader:
            if len(row) < 6:
                continue

            participant_slot += 1
            email = row[1].strip()
            name = row[2].strip()
            assigned_teams = [t.strip() for t in row[5].split(',') if t.strip()]
            unique_assigned_teams = list(dict.fromkeys(assigned_teams))

            reasons = []
            milestone_counts = Counter()
            for team in unique_assigned_teams:
                for milestone in team_summary.get(team, []):
                    reasons.append(f"{team} [{milestone}]")
                    milestone_counts[milestone] += 1

            if name:
                sort_name = name.lower()
            elif email:
                sort_name = email.lower()
            else:
                sort_name = get_participant_slot_id(participant_slot)

            leaderboard.append({
                'participant_slot_id': get_participant_slot_id(participant_slot),
                'name': name,
                'email': email,
                'cookie_count': len(reasons),
                'reasons': reasons,
                'milestone_counts': dict(milestone_counts),
                'sort_name': sort_name,
            })

    leaderboard.sort(
        key=lambda participant: (
            -participant['cookie_count'],
            participant['sort_name'],
            participant['email'].lower(),
        )
    )
    return leaderboard

def load_leaderboard_snapshot(file_path=DEFAULT_LEADERBOARD_STATE_FILE):
    if not os.path.exists(file_path):
        return {}

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            snapshot = json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}

    return snapshot if isinstance(snapshot, dict) else {}

def save_leaderboard_snapshot(snapshot, file_path=DEFAULT_LEADERBOARD_STATE_FILE):
    with open(file_path, 'w', encoding='utf-8') as file:
        json.dump(snapshot, file, indent=2, sort_keys=True)

def get_rank_movement_text(participant):
    movement = participant.get('movement', 'new')
    movement_delta = participant.get('movement_delta', 0)
    previous_rank = participant.get('previous_rank')

    if movement == 'climbed':
        return f"climbed {movement_delta} place(s) from #{previous_rank}"
    if movement == 'slipped':
        return f"slipped {movement_delta} place(s) from #{previous_rank}"
    if movement == 'unchanged':
        return f"unchanged from #{previous_rank}"
    return "new to leaderboard"

def format_milestone_delta_entries(milestone_deltas):
    formatted = []
    for entry in milestone_deltas:
        count_suffix = f" x{entry['count']}" if entry['count'] > 1 else ''
        formatted.append(f"{entry['milestone']}{count_suffix}")
    return ', '.join(formatted) if formatted else 'none.'

def build_leaderboard_report(leaderboard, previous_snapshot=None):
    previous_snapshot = previous_snapshot if isinstance(previous_snapshot, dict) else {}
    previous_participants = previous_snapshot.get('participants', [])
    previous_by_key = {}

    if isinstance(previous_participants, list):
        for participant in previous_participants:
            if not isinstance(participant, dict):
                continue
            participant_slot_id = participant.get('participant_slot_id')
            if participant_slot_id:
                previous_by_key[participant_slot_id] = participant

    report_participants = []
    climbers = []
    slippers = []

    for rank, participant in enumerate(leaderboard, start=1):
        participant_slot_id = participant.get('participant_slot_id')
        previous_participant = previous_by_key.get(participant_slot_id, {})
        previous_rank = previous_participant.get('rank')

        movement = 'new'
        movement_delta = 0
        if isinstance(previous_rank, int):
            if rank < previous_rank:
                movement = 'climbed'
                movement_delta = previous_rank - rank
            elif rank > previous_rank:
                movement = 'slipped'
                movement_delta = rank - previous_rank
            else:
                movement = 'unchanged'

        current_milestone_counts = get_milestone_counter(participant.get('milestone_counts', {}))
        previous_milestone_counts = get_milestone_counter(previous_participant.get('milestone_counts', {}))
        milestone_additions = []
        milestone_removals = []

        for milestone in sorted(set(current_milestone_counts) | set(previous_milestone_counts)):
            delta = current_milestone_counts[milestone] - previous_milestone_counts[milestone]
            if delta > 0:
                milestone_additions.append({'milestone': milestone, 'count': delta})
            elif delta < 0:
                milestone_removals.append({'milestone': milestone, 'count': abs(delta)})

        display_name = participant.get('name') or participant.get('email') or participant_slot_id
        if movement == 'climbed':
            climbers.append({'name': display_name, 'moved_by': movement_delta})
        elif movement == 'slipped':
            slippers.append({'name': display_name, 'moved_by': movement_delta})

        report_participant = dict(participant)
        report_participant['rank'] = rank
        report_participant['previous_rank'] = previous_rank
        report_participant['movement'] = movement
        report_participant['movement_delta'] = movement_delta
        report_participant['milestone_additions'] = milestone_additions
        report_participant['milestone_removals'] = milestone_removals
        report_participants.append(report_participant)

    generated_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    return {
        'generated_at_utc': generated_at_utc,
        'compared_to_generated_at_utc': previous_snapshot.get('generated_at_utc'),
        'climbers': climbers,
        'slippers': slippers,
        'participants': report_participants,
    }

def build_leaderboard_snapshot(leaderboard_report):
    participants = []
    for participant in leaderboard_report.get('participants', []):
        participants.append({
            'participant_slot_id': participant.get('participant_slot_id'),
            'rank': participant.get('rank'),
            'cookie_count': participant.get('cookie_count', 0),
            'milestone_counts': dict(get_milestone_counter(participant.get('milestone_counts', {}))),
        })

    return {
        'generated_at_utc': leaderboard_report.get('generated_at_utc'),
        'participants': participants,
    }

def format_leaderboard_lines(leaderboard_report):
    compared_to = leaderboard_report.get('compared_to_generated_at_utc')
    climbers = leaderboard_report.get('climbers', [])
    slippers = leaderboard_report.get('slippers', [])
    participants = leaderboard_report.get('participants', [])

    if climbers:
        climbed_text = ', '.join(f"{entry['name']} (+{entry['moved_by']})" for entry in climbers)
    else:
        climbed_text = 'none.'

    if slippers:
        slipped_text = ', '.join(f"{entry['name']} (-{entry['moved_by']})" for entry in slippers)
    else:
        slipped_text = 'none.'

    lines = []
    if compared_to:
        lines.append(f"Compared with leaderboard generated at: {compared_to}")
    else:
        lines.append("Compared with leaderboard generated at: none (first leaderboard snapshot).")
    lines.append(f"Climbed this week: {climbed_text}")
    lines.append(f"Slipped this week: {slipped_text}")

    if not participants:
        lines.append("No participants found in the selected participants file.")
        return lines

    for participant in participants:
        cookie_count = participant.get('cookie_count', 0)
        cookie_label = 'World Cup Cookie' if cookie_count == 1 else 'World Cup Cookies'
        display_name = participant.get('name') or participant.get('email') or 'Unknown participant'
        email = participant.get('email', '')
        email_suffix = f" ({email})" if participant.get('name') and email else ''
        movement_text = get_rank_movement_text(participant)

        lines.append(
            f"{participant['rank']}. {display_name}{email_suffix}: {cookie_count} {cookie_label} ({movement_text})"
        )

        reasons = participant.get('reasons', [])
        if reasons:
            lines.append(f"   Reasons: {', '.join(reasons)}")
        else:
            lines.append("   Reasons: none yet.")

        milestone_additions = participant.get('milestone_additions', [])
        milestone_removals = participant.get('milestone_removals', [])
        if milestone_additions:
            lines.append(
                f"   Milestones added this week: {format_milestone_delta_entries(milestone_additions)}"
            )
        if milestone_removals:
            lines.append(
                f"   Milestones removed this week: {format_milestone_delta_entries(milestone_removals)}"
            )
        if not milestone_additions and not milestone_removals:
            lines.append("   Milestone changes this week: none.")

    return lines

def print_cookie_leaderboard(leaderboard_report):
    participants = leaderboard_report.get('participants', [])
    print(f"LEADERBOARD mode enabled. Ranked {len(participants)} participant(s) by total cookies.")

    for line in format_leaderboard_lines(leaderboard_report):
        print(line)

    return participants

def send_weekly_leaderboard_email(leaderboard_report, dry_run=False):
    bcc_email = os.environ.get('BCC_EMAIL', '').strip()
    if not bcc_email:
        if dry_run:
            print('LEADERBOARD DRY RUN: BCC_EMAIL is not set, skipping leaderboard email send.')
            return False
        raise ValueError('BCC_EMAIL must be set when LEADERBOARD mode is enabled.')

    if dry_run:
        print('LEADERBOARD DRY RUN: would email leaderboard to BCC recipient(s) only.')
        return False

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as server:
        server.login(os.environ.get('SENDER_EMAIL'), os.environ.get('SENDER_PASSWORD'))

        msg = EmailMessage()
        msg['Subject'] = 'World Cup Sweepstakes: Weekly Cookie Leaderboard'
        msg['From'] = os.environ.get('SENDER_EMAIL')
        msg['To'] = 'undisclosed-recipients:;'
        msg['Bcc'] = bcc_email

        body = (
            "Hi,\n\n"
            "Here is this week's cookie leaderboard and week-over-week movement summary.\n\n"
        )
        body += '\n'.join(format_leaderboard_lines(leaderboard_report))
        body += "\n\nCheers,\nTobi"

        msg.set_content(body)
        server.send_message(msg)

    print('Sent weekly leaderboard email to BCC recipient(s) only.')
    return True

def process_participants_and_email(team_summary, milestone_messages, tsv_file_path='assigned_participants.tsv', dry_run=False):
    # I read the TSV and send (or simulate) emails to winners.
    targets = get_notification_targets(team_summary, milestone_messages, tsv_file_path)
    bcc_email = os.environ.get('BCC_EMAIL', '').strip()

    if dry_run:
        print(f"DRY RUN enabled. Would send {len(targets)} notification(s).")
        if bcc_email:
            print(f"BCC copy enabled for: {bcc_email}")
        for target in targets:
            cookie_count = len(target['entries'])
            cookie_label = 'World Cup Cookie' if cookie_count == 1 else 'World Cup Cookies'
            print(f"- {target['email']} ({target['name']}): {cookie_count} {cookie_label}")
            for entry in target['entries']:
                print(f"  • {entry['milestone']} [{entry['team']}]: {entry['message']}")
        return targets

    if not targets:
        return targets

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as server:
        server.login(os.environ.get('SENDER_EMAIL'), os.environ.get('SENDER_PASSWORD'))

        for target in targets:
            cookie_count = len(target['entries'])
            cookie_label = 'World Cup Cookie' if cookie_count == 1 else 'World Cup Cookies'

            msg = EmailMessage()
            msg['Subject'] = f'World Cup Sweepstakes: You won {cookie_count} {cookie_label}!'
            msg['From'] = os.environ.get('SENDER_EMAIL')
            msg['To'] = target['email']
            if bcc_email:
                msg['Bcc'] = bcc_email

            body = (
                f"Hi {target['name']},\n\n"
                "Good news! Your team(s) triggered sweepstakes milestones in the latest run, "
                f"which means you're entitled to {cookie_count} {cookie_label}.\n\n"
                "Here is what happened this run:\n"
            )
            body += '\n'.join(
                f"• {entry['milestone']} [{entry['team']}]: {entry['message']}"
                for entry in target['entries']
            )
            body += "\n\nNext time you're in the office, proceed to Flamingo to collect your hard-earned sweet treat.\n\nCheers,\nTobi"

            msg.set_content(body)
            server.send_message(msg)

    return targets

if __name__ == "__main__":
    # I execute the full pipeline.
    dry_run = os.environ.get('DRY_RUN', '').lower() in {'1', 'true', 'yes'}
    blank_state = os.environ.get('BLANK_STATE', '').lower() in {'1', 'true', 'yes'}
    persist_state_in_dry_run = os.environ.get('PERSIST_STATE_IN_DRY_RUN', '').lower() in {'1', 'true', 'yes'}
    debug_milestones = os.environ.get('DEBUG_MILESTONES', '').lower() in {'1', 'true', 'yes'}
    leaderboard_mode = os.environ.get('LEADERBOARD', '').lower() in {'1', 'true', 'yes'}
    participants_file = os.environ.get('PARTICIPANTS_FILE', 'assigned_participants.tsv')
    milestone_messages_file = os.environ.get('MILESTONE_MESSAGES_FILE', MILESTONE_MESSAGES_FILE)
    state_file = os.environ.get('STATE_FILE', DEFAULT_STATE_FILE).strip() or DEFAULT_STATE_FILE
    default_leaderboard_state_file = get_default_leaderboard_state_file(participants_file)
    leaderboard_state_file = (
        os.environ.get('LEADERBOARD_STATE_FILE', default_leaderboard_state_file).strip()
        or default_leaderboard_state_file
    )

    if leaderboard_mode:
        if not blank_state:
            print('LEADERBOARD enabled: forcing BLANK_STATE behavior (full-tournament scoring).')
        blank_state = True
        persist_state_in_dry_run = False

    should_persist_state = (not leaderboard_mode) and (not dry_run or persist_state_in_dry_run)

    if API_KEY in {'', 'YOUR_NEW_TOKEN', 'YOUR_NEW_TOKEN_HERE'}:
        print(
            'Warning: API_KEY is not set. Set API_KEY to your football-data.org token; '
            'otherwise no live milestones can be fetched.'
        )

    if blank_state:
        print(f'BLANK_STATE enabled: ignoring existing {state_file} and starting from empty state for this run.')
        if should_persist_state:
            print(
                'Warning: This run will persist a new state snapshot. '
                'Use DRY_RUN=1 to keep this as a non-persistent test.'
            )

    state = load_state(state_file_path=state_file, force_blank=blank_state)
    processed_match_ids = set(state.get('processed_match_ids', []))
    print(f"Loaded {len(processed_match_ids)} previously processed match id(s) from {state_file}.")
    
    matches = fetch_unprocessed_finished_matches(processed_match_ids)
    print(f"Fetched {len(matches)} new finished match(es) not yet in state.")

    with_event_payload, without_event_payload = get_event_payload_coverage(matches)
    if without_event_payload > 0:
        print(
            f"Warning: Event-level data (goals/bookings) is unavailable for {without_event_payload} "
            f"fetched match(es). These milestones cannot be evaluated from this API response: "
            f"{', '.join(EVENT_BASED_MILESTONES)}"
        )

    daily_results, new_state = process_milestones(matches, state)
    standings_results, new_state = check_standings(new_state)

    final_report = {**daily_results, **standings_results}
    team_summary = get_team_milestone_summary(final_report)
    milestone_messages = load_milestone_messages(milestone_messages_file)

    if debug_milestones:
        print_milestone_debug_summary(final_report, team_summary)
    
    if leaderboard_mode:
        leaderboard = get_cookie_leaderboard(team_summary, participants_file)
        previous_leaderboard_snapshot = load_leaderboard_snapshot(leaderboard_state_file)
        leaderboard_report = build_leaderboard_report(leaderboard, previous_leaderboard_snapshot)
        print_cookie_leaderboard(leaderboard_report)
        send_weekly_leaderboard_email(leaderboard_report, dry_run=dry_run)
        if not dry_run:
            save_leaderboard_snapshot(
                build_leaderboard_snapshot(leaderboard_report),
                leaderboard_state_file,
            )
            print(f"Saved leaderboard snapshot to {leaderboard_state_file}.")
        print(f"Prepared leaderboard for {len(leaderboard_report.get('participants', []))} participant(s).")
    elif team_summary:
        notifications = process_participants_and_email(
            team_summary,
            milestone_messages,
            participants_file,
            dry_run=dry_run,
        )
        print(f"Prepared {len(notifications)} participant notification(s).")
    else:
        print('No milestone winners in this run.')

    if should_persist_state:
        # I save state only after the full run succeeds.
        new_state = update_processed_match_ids(new_state, matches)
        save_state(new_state, state_file_path=state_file)
    elif leaderboard_mode:
        print(f'LEADERBOARD: {state_file} was not updated.')
    else:
        print(f'DRY RUN: {state_file} was not updated. Set PERSIST_STATE_IN_DRY_RUN=1 to override.')

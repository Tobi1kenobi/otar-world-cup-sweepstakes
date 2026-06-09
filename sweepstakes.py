import requests
import json
import csv
import smtplib
import ssl
import os
from datetime import datetime, timedelta
from email.message import EmailMessage

# I set the configuration variables.
API_KEY = os.environ.get('API_KEY', 'YOUR_API_KEY_HERE')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'your_email@gmail.com')
SENDER_PASSWORD = os.environ.get('SENDER_PASSWORD', 'your_app_password')
HEADERS = {'x-apisports-key': API_KEY}
BASE_URL = 'https://v3.football.api-sports.io'
LEAGUE_ID = '1'
SEASON = '2026'

def get_target_dates():
    # I determine which days to look back at.
    today = datetime.now()
    if today.weekday() == 0: 
        return [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(1, 4)]
    return [(today - timedelta(days=1)).strftime('%Y-%m-%d')]

def fetch_matches(dates):
    # I fetch the matches for the target dates.
    all_matches = []
    for target_date in dates:
        response = requests.get(f"{BASE_URL}/fixtures", headers=HEADERS, params={
            'league': LEAGUE_ID,
            'season': SEASON,
            'date': target_date
        })
        all_matches.extend(response.json().get('response', []))
    return all_matches

def process_milestones(matches):
    # I store the winning teams.
    results = {
        "Red Card": [], "90+ Minute Goal": [], "Own Goal": [],
        "Went to Extra Time": [], "Won Penalty Shootout": [], "Giant Killer (Beat Top 5)": [],
        "0-0 Boring Draw": [], "Scored 4+ Goals": [], "Early Goal (First 5 mins)": []
    }
    top_5 = ["Argentina", "Spain", "England", "France", "Brazil"]

    for match in matches:
        teams = match['teams']
        events = match.get('events', [])
        score = match.get('score', {})
        round_info = match['league']['round']

        ft_home = score.get('fulltime', {}).get('home')
        ft_away = score.get('fulltime', {}).get('away')
        
        # I check full-time milestones.
        if ft_home == 0 and ft_away == 0:
            results["0-0 Boring Draw"].extend([teams['home']['name'], teams['away']['name']])
        if ft_home is not None and ft_home >= 4:
            results["Scored 4+ Goals"].append(teams['home']['name'])
        if ft_away is not None and ft_away >= 4:
            results["Scored 4+ Goals"].append(teams['away']['name'])

        # I check in-game events.
        for event in events:
            if event['type'] == 'Card' and event['detail'] == 'Red Card':
                results["Red Card"].append(event['team']['name'])
            
            if event['type'] == 'Goal':
                if event['detail'] == 'Own Goal':
                    results["Own Goal"].append(event['team']['name'])
                
                time = event['time']['elapsed']
                extra = event['time']['extra']
                if time >= 90 or (time == 90 and extra):
                    results["90+ Minute Goal"].append(event['team']['name'])
                if time <= 5:
                    results["Early Goal (First 5 mins)"].append(event['team']['name'])

        # I check extra time and penalties.
        if score.get('extratime', {}).get('home') is not None:
            results["Went to Extra Time"].extend([teams['home']['name'], teams['away']['name']])
        if score.get('penalty', {}).get('home') is not None:
            winner = teams['home']['name'] if score['penalty']['home'] > score['penalty']['away'] else teams['away']['name']
            results["Won Penalty Shootout"].append(winner)

        # I check for giant killers.
        if "Group" not in round_info:
            winner = teams['home']['name'] if teams['home']['winner'] else teams['away']['name']
            loser = teams['away']['name'] if teams['home']['winner'] else teams['home']['name']
            if winner and loser in top_5:
                results["Giant Killer (Beat Top 5)"].append(winner)

    return results

def get_team_milestone_summary(final_report):
    # I map milestones directly to the teams that achieved them.
    team_summary = {}
    for milestone, teams in final_report.items():
        for team in set(teams):
            if team not in team_summary:
                team_summary[team] = []
            team_summary[team].append(milestone)
    return team_summary

def process_participants_and_email(team_summary, tsv_file_path="participants.tsv"):
    # I read the TSV and send emails to winners.
    context = ssl.create_default_context()
    
    with open(tsv_file_path, mode='r', encoding='utf-8') as file:
        reader = csv.reader(file, delimiter='\t')
        next(reader) # I skip the header row.
        
        # I connect to the SMTP server once.
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            
            for row in reader:
                if len(row) < 6:
                    continue # I skip malformed rows.
                
                email = row[1].strip()
                name = row[2].strip()
                wants_updates = row[4].strip().lower().startswith('yes')
                assigned_teams = [t.strip() for t in row[5].split(',')] # I assume column 6 has "Team 1, Team 2"
                
                if not wants_updates:
                    continue

                # I check if any of their teams won a biscuit today.
                biscuit_reasons = []
                for team in assigned_teams:
                    if team in team_summary:
                        milestones = ", ".join(team_summary[team])
                        biscuit_reasons.append(f"• {team}: {milestones}")
                
                if biscuit_reasons:
                    # I construct and send the email.
                    msg = EmailMessage()
                    msg['Subject'] = "World Cup Sweepstakes: You won a biscuit!"
                    msg['From'] = SENDER_EMAIL
                    msg['To'] = email
                    
                    body = f"Hi {name},\n\nGood news! Your team triggered a sweepstakes milestone yesterday, which means you're entitled to a sweet treat.\n\nHere is what happened:\n"
                    body += "\n".join(biscuit_reasons)
                    body += "\n\nI'll see you in the office to hand over your winnings.\n\nCheers,\nTobi"
                    
                    msg.set_content(body)
                    server.send_message(msg)

if __name__ == "__main__":
    # I execute the full daily pipeline.
    dates_to_check = get_target_dates()
    matches = fetch_matches(dates_to_check)
    
    daily_results = process_milestones(matches)
    
    # I skip the group standings check here for brevity, but you can easily add it back.
    
    team_summary = get_team_milestone_summary(daily_results)
    
    if team_summary:
        # I only trigger the email function if there is actually news to share.
        process_participants_and_email(team_summary, "participants.tsv")
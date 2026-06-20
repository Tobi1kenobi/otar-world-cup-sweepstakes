# otar-world-cup-sweepstakes

Automated World Cup sweepstakes scoring and winner notifications using the football-data.org v4 API.

## What It Does

- Fetches World Cup matches from a UTC time window.
- Applies milestone rules (red cards, own goals, first goal, penalties, and more).
- Maps winning teams to participants.
- Sends one consolidated winner email per participant per run (or prints in dry-run mode).
- Supports a leaderboard mode that ranks participants by cookies and lists all team/milestone reasons.
- Supports milestone-specific message templates via milestone_messages.yaml.
- Persists one-off milestone flags and processed match IDs in state.json.

## Exact Rules Checked

The rules below are the exact checks currently implemented in [sweepstakes.py](sweepstakes.py).

### Match Inclusion Before Scoring

1. Query competition matches from /v4/competitions/WC/matches with dateFrom and dateTo derived from the UTC run window.
2. Skip any match whose id is already in processed_match_ids.
3. Fetch each remaining match from /v4/matches/{id}.
4. Include the match only when utcDate parses successfully and window_start <= utcDate < window_end.
5. Sort included matches by utcDate before milestone processing.
6. After processing, add included match IDs to processed_match_ids.

### Match Milestones

1. Red Card
- For each booking, if booking.card is RED, RED_CARD, or YELLOW_RED, award booking.team.name.

2. 90+ Minute Goal
- For each goal, if goal.minute >= 90 and a credited scoring team can be determined, award the credited scoring team.
- For own goals, this means the opponent of the team whose player scored the own goal; the conceding team does not receive this milestone.

3. Own Goal
- For each goal, if goal.type is OWN or OWN_GOAL and goal.team.name exists, award goal.team.name.
- This treats goal.team.name as the team of the player who scored the own goal.
- If goal.team.name is missing, no own-goal award is recorded.

4. Hat Trick
- For each match, count non-own goals by scorer and credited team.
- If one player scores 3 or more non-own goals for the same team in a match, award that team once.
- Own goals do not count toward a player's hat-trick total.

5. First Two Teams to Extra Time
- If score.duration is EXTRA_TIME or PENALTY_SHOOTOUT and first_extra_time_awarded is false, award both teams.
- Then set first_extra_time_awarded to true.

6. Won Penalty Shootout
- If score.duration is PENALTY_SHOOTOUT and penalties.home > penalties.away, award home team.
- If score.duration is PENALTY_SHOOTOUT and penalties.away > penalties.home, award away team.

7. Giant Killer (Beat Top 5)
- Only for stage != GROUP_STAGE.
- Determine winner/loser from score.winner (HOME_TEAM or AWAY_TEAM).
- If loser is one of Argentina, Spain, England, France, Brazil, award winner.

8. 0-0 Boring Draw
- If fullTime.home == 0 and fullTime.away == 0, award both teams.

9. Scored 4+ Goals
- If fullTime.home >= 4, award home team.
- If fullTime.away >= 4, award away team.
- This is based on the final score, so own goals still count toward the benefiting team's match total.

10. Early Goal (First 5 mins)
- For each goal, if goal.minute <= 5 and a credited scoring team can be determined, award the credited scoring team.

11. First Goal of Tournament
- For each goal in chronological match order, if a credited scoring team can be determined and first_goal_awarded is false, award that team.
- Then set first_goal_awarded to true.

12. First Knockout Stage Goal
- For each goal, if stage != GROUP_STAGE, a credited scoring team can be determined, and first_ko_goal_awarded is false, award that team.
- Then set first_ko_goal_awarded to true.

13. Arsenal Player Goal
- For each non-own goal, if goal.scorer.name is in ARSENAL_PLAYERS and a credited scoring team can be determined, award that team.

### Standings Milestones

1. Max Group Points (9)
- In standings entries where type == TOTAL, for each table row with playedGames == 3 and points == 9, award that team.

2. Zero Group Points (0)
- In standings entries where type == TOTAL, for each table row with playedGames == 3 and points == 0, award that team.

### One-Off Tournament Flags

1. first_goal_awarded, first_ko_goal_awarded, and first_extra_time_awarded are persisted in state.json.
2. These prevent re-awarding the same one-off milestone in later runs.
3. They reset only if state.json is edited/reset.

## Schedule Logic

- Scheduled workflow runs at 08:00 UTC Monday-Friday.
- Tuesday-Friday scheduled runs process the preceding 24 hours.
- Monday scheduled run catches up from Friday 08:00 UTC to Monday 08:00 UTC.
- Manual runs default to the preceding 24 hours unless you provide WINDOW_START_UTC and WINDOW_END_UTC.

## GitHub Setup

Required repository secrets:

- API_KEY
- SENDER_EMAIL
- SENDER_PASSWORD
- PARTICIPANTS_REAL_TSV

Optional repository secret:

- BCC_EMAIL (if set, every winner email includes this address in Bcc)

Workflow file: [.github/workflows/sweepstakes.yml](.github/workflows/sweepstakes.yml)

## Keeping Participant Data Private (Public Repo Safe)

Recommended approach:

1. Do not commit [assigned_participants_real.tsv](assigned_participants_real.tsv) to a public repository.
2. Store the raw TSV content in a GitHub Secret named PARTICIPANTS_REAL_TSV.
3. Let the workflow materialize [assigned_participants_real.tsv](assigned_participants_real.tsv) at runtime from that secret.
4. Keep only non-sensitive sample data in [assigned_participants.tsv](assigned_participants.tsv).

To set the secret value, open [assigned_participants_real.tsv](assigned_participants_real.tsv) and paste the raw file contents directly into the GitHub secret named PARTICIPANTS_REAL_TSV. No encoding is required.

If personal data was committed previously, make sure to rotate any exposed credentials and consider rewriting git history before making the repository public.

## Manual Workflow Dispatch Inputs

Use the Run workflow button in GitHub Actions and set optional inputs:

- dry_run: choose `true` (default, preview only) or `false` (live send).
- leaderboard: choose `true` to force an empty-state dry run and print a ranked cookie leaderboard with full reasons.
- dry_run does not update state.json by default, so you can rerun previews without consuming matches.
- To force state updates during dry runs, set PERSIST_STATE_IN_DRY_RUN=1.
- participants_file: defaults to assigned_participants.tsv for safer manual tests.
- participants_file can be switched to assigned_participants_real.tsv for real notifications.
- window_start_utc: optional ISO UTC timestamp, for example 2026-06-12T08:00:00Z.
- window_end_utc: optional ISO UTC timestamp, for example 2026-06-13T08:00:00Z.

## Custom Milestone Messages (YAML)

The script reads milestone message templates from [milestone_messages.yaml](milestone_messages.yaml).

- Set each milestone key to either a single string or a YAML list of strings.
- If a milestone has multiple templates, the script picks one at random for each event.
- Within one recipient email, the script avoids reusing the same template for a milestone until all templates for that milestone have been used.
- Supported placeholders in each template: {team}, {milestone}, {name}.
- If a milestone key is missing in the YAML file, the script falls back to a built-in default.
- You can override the file path with environment variable MILESTONE_MESSAGES_FILE.

Important:

- If you provide one window override, you must provide both.
- window_start_utc must be earlier than window_end_utc.

## Local Runs

Dry run with sample participants:

```bash
pip install requests pyyaml
DRY_RUN=1 PARTICIPANTS_FILE=assigned_participants.tsv python sweepstakes.py
```

Useful debug flags:

- Set DEBUG_MILESTONES=1 to print which milestones and teams were detected before participant filtering.
- Set PERSIST_STATE_IN_DRY_RUN=1 if you want dry runs to update state.json (default is no state updates in dry run).
- Set BLANK_STATE=1 to ignore existing state.json and treat the run as a fresh tournament-state simulation.
- Set LEADERBOARD=1 to force a non-persistent empty-state run and print ranked cookie totals with reasons like `Netherlands [Red Card]`.

Dry run from blank state (helpful when all recent matches are already marked processed):

```bash
DRY_RUN=1 \
BLANK_STATE=1 \
DEBUG_MILESTONES=1 \
PARTICIPANTS_FILE=assigned_participants.tsv \
python sweepstakes.py
```

Leaderboard mode (auto-forces dry-run + empty-state behavior):

```bash
LEADERBOARD=1 \
PARTICIPANTS_FILE=assigned_participants.tsv \
python sweepstakes.py
```

Important API note:

- If match responses do not include goals/bookings data, event-based milestones cannot be scored.
- The script will print a warning and list the affected milestones when this happens.

Dry run with explicit UTC window:

```bash
DRY_RUN=1 \
WINDOW_START_UTC=2026-06-12T08:00:00Z \
WINDOW_END_UTC=2026-06-13T08:00:00Z \
PARTICIPANTS_FILE=assigned_participants_real.tsv \
python sweepstakes.py
```

Live local run (sends real email):

```bash
PARTICIPANTS_FILE=assigned_participants_real.tsv python sweepstakes.py
```

## API Cross-Reference (Quickstart + v4 Reference)

Quickstart page: https://www.football-data.org/documentation/quickstart

The script calls were checked against the official v4 docs:

1. Authentication header
- Script uses X-Auth-Token in [sweepstakes.py](sweepstakes.py#L12).
- Docs show X-Auth-Token for v4 requests: https://docs.football-data.org/general/v4/coding/python.html and https://docs.football-data.org/general/v4/lookup_tables.html.

2. Competition matches endpoint
- Script calls /v4/competitions/WC/matches in [sweepstakes.py](sweepstakes.py#L143).
- Docs list Competition/Matches and filters including dateFrom and dateTo: https://docs.football-data.org/general/v4/competition.html and https://docs.football-data.org/general/v4/lookup_tables.html.

3. Match detail endpoint
- Script calls /v4/matches/{id} in [sweepstakes.py](sweepstakes.py#L159).
- Docs list Match by ID endpoint and match fields (utcDate, score, goals, penalties, bookings): https://docs.football-data.org/general/v4/match.html.

4. Competition standings endpoint
- Script calls /v4/competitions/WC/standings in [sweepstakes.py](sweepstakes.py#L273).
- Docs list Competition/Standings endpoint: https://docs.football-data.org/general/v4/competition.html.

5. World Cup competition code
- Script uses WC in [sweepstakes.py](sweepstakes.py#L143) and [sweepstakes.py](sweepstakes.py#L273).
- Docs lookup table maps WC to FIFA World Cup: https://docs.football-data.org/general/v4/lookup_tables.html.

6. Request throttling
- Free-tier policy is 10 requests per minute: https://docs.football-data.org/general/v4/policies.html.
- Script globally throttles API calls via rate_limited_get in [sweepstakes.py](sweepstakes.py#L70).

Conclusion: the endpoint paths, auth header, competition code, filters, and field usage align with documented v4 behavior for FIFA World Cup calls.

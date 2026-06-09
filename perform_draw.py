import csv
import random
import smtplib
import ssl
import os
from email.message import EmailMessage

# I set the configuration variables.
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'worldcup.biscuits26@gmail.com')
SENDER_PASSWORD = os.environ.get('SENDER_PASSWORD', 'your_app_password')

# I define the official 48 qualified teams.
TEAMS = [
    "USA", "Mexico", "Canada", "Argentina", "Brazil", "Uruguay", "Colombia", "Ecuador",
    "Venezuela", "France", "England", "Spain", "Germany", "Portugal", "Italy", "Netherlands",
    "Croatia", "Belgium", "Switzerland", "Denmark", "Serbia", "Poland", "Ukraine", "Sweden",
    "Austria", "Morocco", "Senegal", "Egypt", "Algeria", "Nigeria", "Ivory Coast", "Cameroon",
    "Ghana", "Mali", "Japan", "South Korea", "Iran", "Saudi Arabia", "Australia", "Qatar",
    "UAE", "Uzbekistan", "Panama", "Costa Rica", "Jamaica", "New Zealand", "Peru", "Honduras"
]

def perform_draw(input_tsv, output_tsv):
    # I shuffle the teams randomly.
    random.shuffle(TEAMS)
    
    assigned_rows = []
    
    with open(input_tsv, mode='r', encoding='utf-8') as infile:
        reader = csv.reader(infile, delimiter='\t')
        header = next(reader)
        
        # I add the new Teams column to the header.
        header.append("Assigned Teams")
        assigned_rows.append(header)
        
        for row in reader:
            if len(row) < 4:
                continue
            
            # I pop two teams from the list for this person.
            team1 = TEAMS.pop()
            team2 = TEAMS.pop()
            assigned_teams = f"{team1}, {team2}"
            
            row.append(assigned_teams)
            assigned_rows.append(row)
            
    # I save the new TSV with the assigned teams.
    with open(output_tsv, mode='w', encoding='utf-8', newline='') as outfile:
        writer = csv.writer(outfile, delimiter='\t')
        writer.writerows(assigned_rows)
        
    return assigned_rows

def send_notification_emails(assigned_rows):
    # I connect to the email server.
    context = ssl.create_default_context()
    
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        
        # I skip the header row.
        for row in assigned_rows[1:]:
            email = row[1].strip()
            name = row[2].strip()
            teams = row[5].strip()
            
            # I construct the email.
            msg = EmailMessage()
            msg['Subject'] = "World Cup Sweepstakes: Your Teams!"
            msg['From'] = SENDER_EMAIL
            msg['To'] = email
            
            body = f"Hi {name},\n\nThe draw has been made! Your two teams for the 2026 World Cup Sweepstakes are:\n\n{teams}\n\nGood luck! I'll email you if either team wins you a biscuit.\n\nCheers,\nTobi"
            
            msg.set_content(body)
            server.send_message(msg)

if __name__ == "__main__":
    print("Starting the draw...")
    updated_rows = perform_draw("participants.tsv", "assigned_participants.tsv")
    print("Draw complete. Saved to assigned_participants.tsv.")
    
    print("Sending emails...")
    send_notification_emails(updated_rows)
    print("All emails sent!")
import csv
import random
import smtplib
import ssl
import os
from email.message import EmailMessage

# I set the configuration variables.
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'worldcup.biscuits26@gmail.com')
SENDER_PASSWORD = os.environ.get('SENDER_PASSWORD', 'your_app_password')

# I define the official 48 qualified teams for the 2026 World Cup.
TEAMS = ['Algeria', 'Argentina', 'Australia', 'Austria', 'Belgium', 
        'Bosnia-Herzegovina', 'Brazil', 'Canada', 'Cape Verde Islands', 
        'Colombia', 'Congo DR', 'Croatia', 'Curaçao', 'Czechia', 
        'Ecuador', 'Egypt', 'England', 'France', 'Germany', 'Ghana', 
        'Haiti', 'Iran', 'Iraq', 'Ivory Coast', 'Japan', 'Jordan', 
        'Mexico', 'Morocco', 'Netherlands', 'New Zealand', 'Norway', 
        'Panama', 'Paraguay', 'Portugal', 'Qatar', 'Saudi Arabia', 
        'Scotland', 'Senegal', 'South Africa', 'South Korea', 'Spain', 
        'Sweden', 'Switzerland', 'Tunisia', 'Turkey', 'United States', 
        'Uruguay', 'Uzbekistan']

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
    output_file = "assigned_participants.tsv"
    
    if os.path.exists(output_file):
        print(f"'{output_file}' already exists. Skipping draw and reading existing data...")
        # I load the existing team assignments.
        updated_rows = []
        with open(output_file, mode='r', encoding='utf-8') as infile:
            reader = csv.reader(infile, delimiter='\t')
            for row in reader:
                updated_rows.append(row)
    else:
        print("Starting the draw...")
        updated_rows = perform_draw("participants.tsv", output_file)
        print(f"Draw complete. Saved to {output_file}.")
    
    print("Sending emails...")
    send_notification_emails(updated_rows)
    print("All emails sent!")
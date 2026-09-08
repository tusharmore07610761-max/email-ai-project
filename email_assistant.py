"""
AI Email Reply Assistant
-------------------------
Reads unread emails from Gmail, generates a reply draft using Gemini,
shows it to you, and sends it only if you approve.
"""

import os
import json
import base64
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import google.generativeai as genai

# ---------- SETTINGS ----------
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
CONFIG_FILE = 'config.json'
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'

DEFAULTS = {
    'gemini_api_key': os.environ.get('GEMINI_API_KEY', ''),
    'gemini_model': os.environ.get('GEMINI_MODEL', 'gemini-3.6-flash'),
    # The pasted content of a user's own Google Cloud OAuth client JSON,
    # which lets any user use their own Gmail account.
    'gmail_credentials': '',
}


def load_config():
    """Reads user settings from config.json, falling back to defaults."""
    config = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config.update(json.load(f))
        except (ValueError, OSError):
            pass
    return config


def save_config(config):
    """Persists user settings to config.json (kept out of git)."""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)


def _get_model():
    """Creates a GenerativeModel from the currently configured settings.

    genai is re-configured each call so a user can change their API key and
    model from the UI without restarting the server.
    """
    config = load_config()
    key = config.get('gemini_api_key')
    model_name = config.get('gemini_model') or DEFAULTS['gemini_model']
    if key:
        genai.configure(api_key=key)
    return genai.GenerativeModel(model_name)


def _write_credentials_from_config():
    """If the user pasted their own OAuth client JSON, write it to disk."""
    config = load_config()
    pasted = (config.get('gmail_credentials') or '').strip()
    if not pasted:
        return
    try:
        json.loads(pasted)  # validate it's JSON before writing
        with open(CREDENTIALS_FILE, 'w', encoding='utf-8') as f:
            f.write(pasted)
    except (ValueError, OSError):
        pass


def gmail_authenticate():
    """Logs into Gmail using credentials.json (opens browser first time).

    Uses the user's own OAuth client if they saved one in settings, otherwise
    the credentials.json already present in the project.
    """
    _write_credentials_from_config()
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                # The refresh token was revoked/expired. Clear it and force a
                # fresh interactive login from scratch.
                creds = None
        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)


def parse_address(raw):
    """Extracts a bare email address from a 'Name <email>' header value."""
    if '<' in raw and '>' in raw:
        return raw[raw.index('<') + 1:raw.index('>')]
    return raw.strip()


def extract_body(msg_data):
    """Recursively pull plain-text body out of a message payload."""
    payload = msg_data['payload']

    def walk(p):
        if p.get('mimeType') == 'text/plain' and 'data' in p.get('body', {}):
            return base64.urlsafe_b64decode(p['body']['data']).decode('utf-8', errors='ignore')
        for part in p.get('parts', []):
            text = walk(part)
            if text:
                return text
        return ''

    return walk(payload)


def get_unread_emails(service, max_results=5):
    """Fetches a few unread emails from inbox."""
    results = service.users().messages().list(
        userId='me', labelIds=['INBOX', 'UNREAD'], maxResults=max_results
    ).execute()
    messages = results.get('messages', [])
    emails = []
    for msg in messages:
        msg_data = service.users().messages().get(
            userId='me', id=msg['id'], format='full'
        ).execute()
        headers = msg_data['payload']['headers']
        hval = lambda name, default='': next(
            (h['value'] for h in headers if h['name'] == name), default)

        sender = hval('From', '(Unknown sender)')
        emails.append({
            'id': msg['id'],
            'threadId': msg_data['threadId'],
            'subject': hval('Subject', '(No subject)'),
            'sender': sender,
            'sender_email': parse_address(sender),
            'snippet': msg_data.get('snippet', ''),
            'body': extract_body(msg_data)[:1500],  # keep it short for the AI prompt
        })
    return emails


def mark_as_read(service, email_id):
    """Removes the UNREAD label so the email no longer shows as unread."""
    try:
        service.users().messages().modify(
            userId='me', id=email_id, body={'removeLabelIds': ['UNREAD']}
        ).execute()
        return True
    except Exception:
        return False


def generate_reply(email):
    """Asks Gemini to draft a reply for this email."""
    prompt = f"""You are helping draft a short, polite email reply.

Original email from: {email['sender']}
Subject: {email['subject']}
Body:
{email['body']}

Write a short, professional reply (3-5 sentences). Only output the reply text, nothing else."""

    response = _get_model().generate_content(prompt)
    return response.text.strip()


def send_reply(service, email, reply_text):
    """Sends the reply as a message in the same thread."""
    message = MIMEText(reply_text)
    to = email.get('sender_email') or parse_address(email['sender'])
    message['to'] = to
    message['subject'] = "Re: " + email['subject']
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    service.users().messages().send(
        userId='me',
        body={'raw': raw, 'threadId': email['threadId']}
    ).execute()
    # Once we've sent our reply, mark the thread's original unread.
    mark_as_read(service, email['id'])


def main():
    print("Connecting to Gmail...")
    service = gmail_authenticate()

    print("Fetching unread emails...")
    emails = get_unread_emails(service)

    if not emails:
        print("No unread emails found.")
        return

    for email in emails:
        print("\n" + "=" * 50)
        print(f"From: {email['sender']}")
        print(f"Subject: {email['subject']}")
        print(f"Preview: {email['body'][:200]}")

        print("\nGenerating AI reply draft...")
        reply = generate_reply(email)
        print("\n--- Suggested Reply ---")
        print(reply)
        print("------------------------")

        choice = input("\nSend this reply? (y/n/skip): ").strip().lower()
        if choice == 'y':
            send_reply(service, email, reply)
            print("Reply sent!")
        else:
            print("Skipped.")


if __name__ == '__main__':
    main()

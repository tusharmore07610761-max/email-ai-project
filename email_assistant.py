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

# Data files live under DATA_DIR when set (e.g. Render persistent disk /data),
# otherwise next to the project (local dev).
DATA_DIR = os.environ.get('DATA_DIR', '').strip()


def data_path(name):
    """Resolves a data-file path: DATA_DIR/<name> or ./<name> when unset."""
    if not DATA_DIR:
        return name
    return os.path.abspath(os.path.join(DATA_DIR, name))


def _ensure_data_dir():
    if DATA_DIR:
        os.makedirs(DATA_DIR, exist_ok=True)


CONFIG_FILE = data_path('config.json')
CREDENTIALS_FILE = data_path('credentials.json')
TOKEN_FILE = data_path('token.json')

# Providers the app can route AI drafts through. The user picks one in the
# Settings panel and pastes their own API key + model name. OpenAI, Qwen and
# OpenRouter all speak the OpenAI-compatible API, so they share the same client.
SUPPORTED_PROVIDERS = ('openai', 'qwen', 'openrouter', 'gemini')

# Preset base URLs for each OpenAI-compatible provider. A user can also paste
# their own base_url to talk to any compatible endpoint (DeepSeek, Ollama, etc.).
PROVIDER_PRESETS = {
    'openai': {'base_url': 'https://api.openai.com/v1'},
    'qwen': {'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1'},
    'openrouter': {'base_url': 'https://openrouter.ai/api/v1'},
    'gemini': {},
}

DEFAULTS = {
    'provider': os.environ.get('AI_PROVIDER', 'openai'),
    # Legacy fallbacks keep old config.json files working after the switch.
    'api_key': os.environ.get(
        'AI_API_KEY',
        os.environ.get('OPENAI_API_KEY', os.environ.get('GEMINI_API_KEY', '')),
    ),
    'model': os.environ.get(
        'AI_MODEL',
        os.environ.get('OPENAI_MODEL', os.environ.get('GEMINI_MODEL', 'gpt-4o-mini')),
    ),
    'base_url': os.environ.get('AI_BASE_URL', ''),
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
    # Migrate old Gemini-only config to the provider-agnostic shape.
    if not config.get('api_key') and config.get('gemini_api_key'):
        config['api_key'] = config['gemini_api_key']
        config['provider'] = 'gemini'
    if not config.get('model') and config.get('gemini_model'):
        config['model'] = config['gemini_model']
    return config


def save_config(config):
    """Persists user settings to config.json (kept out of git)."""
    _ensure_data_dir()
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)


def get_ai_settings():
    """Returns the current (provider, api_key, model, base_url) tuple."""
    config = load_config()
    provider = (config.get('provider') or 'openai').strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        provider = 'openai'
    base_url = config.get('base_url') or PROVIDER_PRESETS[provider].get('base_url', '')
    return (
        provider,
        config.get('api_key', ''),
        config.get('model') or '',
        base_url,
    )


def _generate_openai(prompt, api_key, model, base_url, extra_headers=None):
    """Calls an OpenAI-compatible chat completion API."""
    from openai import OpenAI
    client = OpenAI(
        api_key=api_key,
        base_url=base_url or None,
        default_headers=extra_headers or None,
    )
    response = client.chat.completions.create(
        model=model,
        messages=[{'role': 'user', 'content': prompt}],
    )
    return response.choices[0].message.content


def _generate_gemini(prompt, api_key, model):
    """Calls the Google Gemini generative API."""
    if api_key:
        genai.configure(api_key=api_key)
    response = genai.GenerativeModel(model).generate_content(prompt)
    return response.text


def _write_credentials_from_config():
    """If the user pasted their own OAuth client JSON, write it to disk."""
    config = load_config()
    pasted = (config.get('gmail_credentials') or '').strip()
    if not pasted:
        return
    try:
        json.loads(pasted)  # validate it's JSON before writing
        _ensure_data_dir()
        with open(CREDENTIALS_FILE, 'w', encoding='utf-8') as f:
            f.write(pasted)
    except (ValueError, OSError):
        pass


class OAuthRequired(Exception):
    """Raised when Gmail needs to be (re)authorized through the web flow."""


# The in-progress OAuth flow, kept between /auth/start and /oauth2callback.
# Safe with a single web worker (see the Render start command).
_oauth_flow = None
_oauth_redirect_uri = None


def _registered_redirect_uri():
    """Returns the first registered redirect URI from the client config."""
    try:
        with open(CREDENTIALS_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        web = (cfg.get('web') or {}).get('redirect_uris') or []
        installed = (cfg.get('installed') or {}).get('redirect_uris') or []
        uris = web or installed
        return uris[0] if uris else None
    except (ValueError, OSError, IndexError):
        return None


def get_auth_url(redirect_uri=None):
    """Builds the Google authorization URL for the web redirect flow.

    google_auth_oauthlib does not inject redirect_uri on its own, and calling
    Google with a missing/mismatched one fails the callback — so we always pass
    it explicitly. If the caller doesn't supply one (e.g. the app's own
    /oauth2callback), we fall back to the client config's first registered URI.
    """
    global _oauth_flow, _oauth_redirect_uri
    _write_credentials_from_config()
    if not os.path.exists(CREDENTIALS_FILE):
        raise ValueError(
            'No Gmail OAuth client found. Paste your credentials.json in '
            'Settings → Gmail first.'
        )
    _oauth_flow = InstalledAppFlow.from_client_secrets_file(
        CREDENTIALS_FILE, SCOPES)
    _oauth_redirect_uri = (
        redirect_uri
        or _registered_redirect_uri()
        or os.environ.get('OAUTH_REDIRECT_URI', '')
    )
    if not _oauth_redirect_uri:
        raise ValueError(
            'No redirect URI available in your credentials.json. Recreate the '
            'OAuth client and add the /oauth2callback URL under "redirect URIs".'
        )
    # Assign on the session (not as a keyword arg) — threading it through
    # authorization_url() makes oauthlib reject the call with a duplicate.
    _oauth_flow.oauth2session.redirect_uri = _oauth_redirect_uri
    auth_url, _ = _oauth_flow.authorization_url(
        access_type='offline',
        prompt='consent',
        include_granted_scopes='true',
    )
    return auth_url


def exchange_oauth_code(code=None, error=None):
    """Completes the OAuth exchange from /oauth2callback and saves the token."""
    global _oauth_flow, _oauth_redirect_uri
    if error or not code:
        _oauth_flow = None
        _oauth_redirect_uri = None
        raise ValueError(error or 'Gmail authorization rejected.')
    flow = _oauth_flow
    redirect_uri = _oauth_redirect_uri
    _oauth_flow = None
    _oauth_redirect_uri = None
    if flow is None or not redirect_uri:
        raise ValueError(
            'OAuth session expired. Start again from Settings → Connect Gmail.'
        )
    flow.fetch_token(code=code)
    _ensure_data_dir()
    with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
        f.write(flow.credentials.to_json())


def gmail_authenticate():
    """Returns the Gmail service, raising OAuthRequired if a login is needed.

    Authorization now happens in the browser via /auth/start (web UI) instead
    of the old localhost-only run_local_server flow.
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
            raise OAuthRequired(
                'Gmail authorization needed. Connect Gmail from the web UI.'
            )
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


def generate_reply(email, overrides=None):
    """Asks the configured AI provider to draft a reply for this email.

    overrides (optional): a dict with provider/api_key/model/base_url keys that
    temporarily replaces the saved settings — used by the "Test AI key" button
    so the pasted key is tested even before saving.
    """
    prompt = f"""You are helping draft a short, polite email reply.

Original email from: {email['sender']}
Subject: {email['subject']}
Body:
{email['body']}

Write a short, professional reply (3-5 sentences). Only output the reply text, nothing else."""

    provider, api_key, model, base_url = get_ai_settings()
    if overrides:
        provider = (overrides.get('provider') or provider).strip().lower()
        api_key = overrides.get('api_key') or api_key
        model = overrides.get('model') or model
        base_url = overrides.get('base_url') or base_url

    if not api_key:
        raise ValueError(
            'No API key set. Open Settings and paste your API key.'
        )
    if not model:
        raise ValueError(
            'No model set. Open Settings and pick a model.'
        )

    if provider in ('openai', 'qwen', 'openrouter'):
        headers = None
        if provider == 'openrouter':
            # Lets OpenRouter attribute/rank the app on their site.
            headers = {
                'HTTP-Referer': os.environ.get(
                    'APP_URL', 'http://localhost:5000/'),
                'X-Title': 'Reply Desk',
            }
        draft = _generate_openai(prompt, api_key, model, base_url, headers)
    elif provider == 'gemini':
        draft = _generate_gemini(prompt, api_key, model)
    else:
        raise ValueError(f'Unknown provider: {provider}')

    return draft.strip()


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
    try:
        service = gmail_authenticate()
    except OAuthRequired as exc:
        print(f"\n{exc}")
        print("Open the web UI (python app.py) and connect Gmail there.")
        return

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

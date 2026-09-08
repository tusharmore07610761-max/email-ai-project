"""
Reply Desk — Web UI for the AI Email Assistant.

A thin Flask layer around email_assistant.py that serves a live inbox UI.
The terminal y/n/skip approval gate becomes Send / Edit / Skip buttons.

Users configure their own credentials (Gemini API key / model, and their own
Gmail OAuth client) from a Settings panel — no hardcoded secrets in code.
"""

import functools
import json

from flask import Flask, jsonify, render_template, request

from email_assistant import (
    load_config,
    save_config,
    gmail_authenticate,
    get_unread_emails,
    generate_reply,
    send_reply,
    mark_as_read,
)

app = Flask(__name__)

# Force re-auth whenever settings change (new Gmail account / credentials).
_last_creds_signature = None
_service = None


def _creds_signature():
    return json.dumps(load_config().get('gmail_credentials', ''), sort_keys=True)


def get_service():
    """Returns a cached Gmail service, re-authenticating if credentials change."""
    global _service, _last_creds_signature
    sig = _creds_signature()
    if _service is None or sig != _last_creds_signature:
        _last_creds_signature = sig
        _service = gmail_authenticate()
    return _service


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/health')
def health():
    config = load_config()
    return jsonify({
        'ok': True,
        'gemini_key_set': bool(config.get('gemini_api_key')),
        'gmail_configured': bool(config.get('gmail_credentials')) or _has_file('credentials.json'),
    })


@app.route('/api/settings', methods=['GET'])
def get_settings():
    config = load_config()
    # Never send the real key back to the browser; send a masked preview.
    key = config.get('gemini_api_key', '')
    return jsonify({
        'gemini_model': config.get('gemini_model'),
        'gemini_key_set': bool(key),
        'gemini_key_masked': _mask(key),
        'gmail_configured': bool(config.get('gmail_credentials')) or _has_file('credentials.json'),
        'gmail_email': _current_account(),
    })


@app.route('/api/settings', methods=['POST'])
def save_settings():
    data = request.get_json(silent=True) or {}
    config = load_config()

    if 'gemini_model' in data and data['gemini_model']:
        config['gemini_model'] = data['gemini_model'].strip()

    # Only overwrite the key if the user typed a new one (a blank/masked value
    # means "keep what we already have").
    key = (data.get('gemini_api_key') or '').strip()
    if key and key != _mask(config.get('gemini_api_key', '')):
        config['gemini_api_key'] = key

    gmail_creds = (data.get('gmail_credentials') or '').strip()
    if gmail_creds:
        try:
            parsed = json.loads(gmail_creds)
            # Must look like an OAuth client descriptor.
            if 'installed' not in parsed and 'web' not in parsed:
                raise ValueError('not an OAuth client JSON')
            config['gmail_credentials'] = gmail_creds
        except (ValueError, json.JSONDecodeError) as exc:
            return jsonify({'error': f'Invalid Gmail credentials JSON: {exc}'}), 400

    save_config(config)
    return jsonify({'ok': True})


@app.route('/api/reconnect', methods=['POST'])
def reconnect():
    """Deletes the saved token so the next inbox load triggers a fresh login."""
    global _service
    import os
    from email_assistant import TOKEN_FILE
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    _service = None
    _last_creds_signature = None
    return jsonify({'ok': True})


@app.route('/api/inbox')
def inbox():
    try:
        service = get_service()
    except Exception as exc:  # noqa: BLE001
        return jsonify({
            'error': 'Gmail auth failed. Open Settings and follow the Gmail setup.',
            'detail': str(exc),
        }), 401
    emails = get_unread_emails(service, max_results=10)
    for email in emails:
        email['draft'] = None  # no AI draft yet
    return jsonify(emails)


@app.route('/api/reply', methods=['POST'])
def reply():
    """Generates an AI draft for a given email id."""
    data = request.get_json(silent=True) or {}
    email = data.get('email') or {}
    if not email.get('id'):
        return jsonify({'error': 'Missing email data'}), 400
    try:
        draft = generate_reply(email)
    except Exception as exc:  # noqa: BLE001
        return jsonify({'error': str(exc)}), 500
    return jsonify({'id': email['id'], 'draft': draft})


@app.route('/api/send', methods=['POST'])
def send():
    """Sends the approved reply back in the same thread."""
    data = request.get_json(silent=True) or {}
    email = data.get('email') or {}
    if not email.get('id') or not email.get('threadId'):
        return jsonify({'error': 'Incomplete email data'}), 400

    reply_text = (data.get('reply') or '').strip()
    if not reply_text:
        return jsonify({'error': 'Empty reply'}), 400

    try:
        send_reply(get_service(), email, reply_text)
    except Exception as exc:  # noqa: BLE001
        return jsonify({'error': str(exc)}), 500
    return jsonify({'ok': True, 'id': email['id']})


@app.route('/api/skip', methods=['POST'])
def skip():
    """Marks an email as read without replying."""
    data = request.get_json(silent=True) or {}
    email_id = (data.get('id') or '').strip()
    if not email_id:
        return jsonify({'error': 'Missing email id'}), 400
    mark_as_read(get_service(), email_id)
    return jsonify({'ok': True, 'id': email_id})


# ---------- helpers ----------

def _has_file(name):
    import os
    return os.path.exists(name)


def _mask(secret):
    if not secret:
        return ''
    return secret[:6] + '••••' + secret[-4:] if len(secret) > 12 else '••••••'


def _current_account():
    """Best-effort read of the Gmail account from token.json."""
    import os
    if not _has_file('token.json'):
        return None
    try:
        from google.oauth2.credentials import Credentials as GCreds
        from email_assistant import SCOPES
        creds = GCreds.from_authorized_user_file('token.json', SCOPES)
        return creds.account if creds else None
    except Exception:  # noqa: BLE001
        return None


if __name__ == '__main__':
    app.run(debug=True)

"""Amy's email module — Google (Gmail / Workspace) or ProtonMail via Bridge.

Google: set EMAIL_PROVIDER=google in .env.amy. AMY_EMAIL_PASSWORD must be a
Google *App Password* (myaccount.google.com/apppasswords), not the normal one.

Proton modes:
  PROTON_USE_BRIDGE=true  — connects to Proton Bridge running on localhost
  PROTON_USE_BRIDGE=false — stubs out; Amy knows her address but can't send yet

To set up Bridge:
  1. Install Proton Mail Bridge for Mac: https://proton.me/mail/bridge
  2. Sign in with Amy's ProtonMail credentials
  3. Bridge generates a separate Bridge password — put it in .env.amy as PROTON_BRIDGE_PASSWORD
  4. Set PROTON_USE_BRIDGE=true in .env.amy
"""
import os
import imaplib
import smtplib
import email as _email_lib
from email.message import EmailMessage
from email.header import decode_header
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
_ENV = HERE / ".env.amy"


def _load_env():
    config = {}
    if _ENV.exists():
        for line in _ENV.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                config[k.strip()] = v.strip()
    # Environment variables override file
    for key in ["AMY_EMAIL", "AMY_EMAIL_PASSWORD", "PROTON_BRIDGE_IMAP_PORT",
                 "PROTON_BRIDGE_SMTP_PORT", "PROTON_BRIDGE_PASSWORD", "PROTON_USE_BRIDGE",
                 "EMAIL_PROVIDER"]:
        if os.environ.get(key):
            config[key] = os.environ[key]
    return config


def _cfg():
    return _load_env()


def amy_address() -> str:
    return _cfg().get("AMY_EMAIL", "")


def _is_google() -> bool:
    return _cfg().get("EMAIL_PROVIDER", "").lower() == "google"


def _use_bridge() -> bool:
    """True when email is usable at all (Google, or Proton with Bridge running)."""
    return _is_google() or _cfg().get("PROTON_USE_BRIDGE", "false").lower() == "true"


def _imap_conn():
    cfg = _cfg()
    if _is_google():
        conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        conn.login(cfg["AMY_EMAIL"], cfg.get("AMY_EMAIL_PASSWORD", "").replace(" ", ""))
        return conn
    port = int(cfg.get("PROTON_BRIDGE_IMAP_PORT", 1143))
    password = cfg.get("PROTON_BRIDGE_PASSWORD") or cfg.get("AMY_EMAIL_PASSWORD", "")
    conn = imaplib.IMAP4("127.0.0.1", port)
    conn.login(cfg["AMY_EMAIL"], password)
    return conn


def _decode_header(value: str) -> str:
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


# --- Public API ------------------------------------------------------------

def check_inbox(limit: int = 10, folder: str = "INBOX") -> list[dict]:
    """Return recent emails as a list of dicts. Newest first."""
    if not _use_bridge():
        return [{"error": "Proton Bridge not configured — set PROTON_USE_BRIDGE=true in .env.amy"}]
    try:
        conn = _imap_conn()
        conn.select(folder)
        _, data = conn.search(None, "ALL")
        ids = data[0].split()
        ids = ids[-limit:][::-1]  # newest first
        messages = []
        for mid in ids:
            _, msg_data = conn.fetch(mid, "(BODY.PEEK[])")
            msg = _email_lib.message_from_bytes(msg_data[0][1])
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        break
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
            messages.append({
                "id": mid.decode(),
                "from": _decode_header(msg.get("From", "")),
                "to": _decode_header(msg.get("To", "")),
                "subject": _decode_header(msg.get("Subject", "(no subject)")),
                "date": msg.get("Date", ""),
                "body": body[:2000],
            })
        conn.logout()
        return messages
    except Exception as e:
        return [{"error": str(e)}]


def send_email(to: str, subject: str, body: str) -> str:
    """Send an email as Amy. Returns 'sent' or an error string."""
    if not _use_bridge():
        return "Proton Bridge not configured — set PROTON_USE_BRIDGE=true in .env.amy to enable sending"
    cfg = _cfg()
    try:
        port = int(cfg.get("PROTON_BRIDGE_SMTP_PORT", 1025))
        password = cfg.get("PROTON_BRIDGE_PASSWORD") or cfg.get("AMY_EMAIL_PASSWORD", "")
        msg = EmailMessage()
        msg["From"] = cfg["AMY_EMAIL"]
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        if _is_google():
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
                s.login(cfg["AMY_EMAIL"], cfg.get("AMY_EMAIL_PASSWORD", "").replace(" ", ""))
                s.send_message(msg)
        else:
            with smtplib.SMTP("127.0.0.1", port) as s:
                s.login(cfg["AMY_EMAIL"], password)
                s.send_message(msg)
        print(f"  [email] sent → {to}: {subject}")
        return "sent"
    except Exception as e:
        return f"send failed: {e}"


def reply_to(original: dict, body: str) -> str:
    """Reply to an email from check_inbox."""
    to = original.get("from", "")
    subject = original.get("subject", "")
    if not subject.startswith("Re:"):
        subject = f"Re: {subject}"
    return send_email(to, subject, body)


def inbox_summary() -> str:
    """Short summary for Amy to surface to Brian."""
    if not _use_bridge():
        addr = amy_address()
        if addr:
            return f"My email is {addr}, but email isn't switched on yet (set EMAIL_PROVIDER=google in .env.amy)."
        return "Email not configured yet — fill in .env.amy."
    messages = check_inbox(limit=5)
    if not messages:
        return "Inbox is empty."
    if "error" in messages[0]:
        return f"Email check failed: {messages[0]['error']}"
    lines = [f"Inbox ({len(messages)} recent):"]
    for m in messages:
        lines.append(f"- From: {m['from']} | {m['subject']} | {m['date'][:16]}")
    return "\n".join(lines)

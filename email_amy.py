"""Amy's email module — Google Workspace (amyai@aimhigherineducation.org).

Required .env.amy settings:
  AMY_EMAIL=amyai@aimhigherineducation.org
  AMY_EMAIL_PASSWORD=<16-letter Google App Password>
  EMAIL_PROVIDER=google
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
                v = v.strip().strip('"').strip("'")
                config[k.strip()] = v
    # Environment variables override file
    for key in ["AMY_EMAIL", "AMY_EMAIL_PASSWORD", "EMAIL_PROVIDER"]:
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
    """True when email is configured and usable."""
    return _is_google()


def _imap_conn():
    cfg = _cfg()
    conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    conn.login(cfg["AMY_EMAIL"], cfg.get("AMY_EMAIL_PASSWORD", "").replace(" ", ""))
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
        return [{"error": "Email not configured — set EMAIL_PROVIDER=google in .env.amy"}]
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
        return "Email not configured — set EMAIL_PROVIDER=google in .env.amy"
    cfg = _cfg()
    try:
        msg = EmailMessage()
        msg["From"] = cfg["AMY_EMAIL"]
        msg["To"] = to
        msg["Subject"] = subject
        signature = "\n\n--\nWarm regards,\nAmy · amyai@aimhigherineducation.org\nAnthos Intelligence"
        msg.set_content(body + signature)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(cfg["AMY_EMAIL"], cfg.get("AMY_EMAIL_PASSWORD", "").replace(" ", ""))
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

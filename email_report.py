import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path


def send_email_with_attachment(subject: str, body: str, attachment_path: str) -> None:
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    email_from = os.getenv("EMAIL_FROM", "").strip()
    email_to_raw = os.getenv("EMAIL_TO", "").strip()

    if not all([smtp_host, smtp_username, smtp_password, email_from, email_to_raw]):
        raise ValueError("Missing SMTP/email environment variables.")

    recipients = [e.strip() for e in email_to_raw.split(",") if e.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    path = Path(attachment_path)
    data = path.read_bytes()
    msg.add_attachment(data, maintype="text", subtype="csv", filename=path.name)

    context = ssl.create_default_context()

    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=60, context=context) as server:
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        return

    with smtplib.SMTP(smtp_host, smtp_port, timeout=60) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(smtp_username, smtp_password)
        server.send_message(msg)
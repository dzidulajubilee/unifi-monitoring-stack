"""Sending reports by email."""

import html
import smtplib
from email.message import EmailMessage

from .config import load_settings, log, split_addresses
from .text import _join


def send_mail(to, subject, text, html_body=None, attachment=None, smtp=None):
    smtp = smtp or load_settings()["smtp"]
    if not smtp.get("host"):
        raise RuntimeError("no SMTP server is set up")
    to = split_addresses(to) if isinstance(to, str) else to
    if not to:
        raise RuntimeError("no recipients")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, smtp.get("from") or smtp.get("user"), ", ".join(to)
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    if attachment:
        msg.add_attachment(attachment.read_bytes(), maintype="application", subtype="pdf", filename=attachment.name)
    port, sec = int(smtp.get("port") or 587), smtp.get("security", "starttls")
    cls = smtplib.SMTP_SSL if sec == "ssl" else smtplib.SMTP
    with cls(smtp["host"], port, timeout=60) as c:
        if sec == "starttls":
            c.starttls()
        if smtp.get("user"):
            c.login(smtp["user"], smtp.get("password", ""))
        c.send_message(msg)
    log.info("emailed %r to %s", subject, ", ".join(to))


def mail_error(e):
    """Plain-language reason an email failed."""
    import socket

    if isinstance(e, smtplib.SMTPAuthenticationError):
        return "the mail server rejected the username or password (for Gmail, use an app password)"
    if isinstance(e, ConnectionRefusedError):
        return "the mail server refused the connection; check the server name and port"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "the mail server didn't answer; check the server name, port and security setting"
    if isinstance(e, socket.gaierror):
        return "the mail server name couldn't be found; check the SMTP server setting"
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return f"the mail server refused the address(es): {', '.join(e.recipients)}"
    if isinstance(e, (smtplib.SMTPNotSupportedError, ssl_error_types())):
        return "the security setting doesn't match the mail server; try STARTTLS on port 587 or SSL/TLS on 465"
    return str(e) or e.__class__.__name__


def ssl_error_types():
    import ssl

    return ssl.SSLError


def email_report(pdf, subject, lines, to, style="full"):
    if style == "summary":
        subject += ", summary"
    send_mail(
        to,
        subject,
        "\n\n".join(lines) + "\n\nThe full report is attached.",
        "".join(f"<p>{html.escape(x)}</p>" for x in lines)
        + "<p style='color:#6b6a66'>The full report is attached.</p>",
        pdf,
    )


def recipients(kind, settings=None):
    """[(style, addresses)] for a report type, from Settings; empty lists left out."""
    st = settings or load_settings()
    out = [("full", st["recipients"].get(kind, "")), ("summary", st["recipients_summary"].get(kind, ""))]
    return [(style, to) for style, to in out if to.strip()]


def deliver(pdfs, subject, lines, sends):
    """Email each style to its recipients. Returns [(style, to, error or None)]."""
    results = []
    for style, to in sends:
        try:
            email_report(pdfs[style], subject, lines, to, style)
            results.append((style, to, None))
        except Exception as e:  # noqa: BLE001 - the PDFs are saved either way
            log.exception("emailing the %s report to %s failed", style, to)
            results.append((style, to, mail_error(e)))
    return results


def describe_sends(results):
    """'emailed to a@x (full) and b@y (summary)' for the successful ones."""
    ok = [f"{to} ({'summary' if style == 'summary' else 'full'})" for style, to, err in results if not err]
    return ("emailed to " + _join(ok)) if ok else ""

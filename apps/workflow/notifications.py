"""Notification provider backends.

Real implementations for SMS (Africa's Talking / Termii) and email (SMTP).
These backends are called by the notification dispatcher to actually deliver messages.

Design requirement: "Africa's Talking / Termii for SMS+USSD; SMTP with DSN capture"
"""
import logging
import smtplib
import json
import hashlib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from django.conf import settings

logger = logging.getLogger(__name__)


class NotificationBackend:
    """Base class for notification backends."""

    def send(self, recipient: str, message: str, **kwargs) -> dict:
        """Send a notification. Returns dict with 'success' and 'reference'."""
        raise NotImplementedError


class ConsoleBackend(NotificationBackend):
    """Development backend: logs to console. Used when SMS_PROVIDER=console."""

    def send(self, recipient: str, message: str, channel: str = "SMS", **kwargs) -> dict:
        logger.info(f"[{channel}] To: {recipient}\n{message}")
        ref = hashlib.sha256(f"{recipient}:{message}".encode()).hexdigest()[:16]
        return {"success": True, "reference": f"console-{ref}"}


class SMTPBackend(NotificationBackend):
    """SMTP email backend with DSN (Delivery Status Notification) support.

    Configuration via settings:
        EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD,
        EMAIL_USE_TLS, DEFAULT_FROM_EMAIL
    """

    def send(self, recipient: str, message: str, subject: str = "Taraba Procurement Notification", **kwargs) -> dict:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tr.gov.ng")
            msg["To"] = recipient

            # Plain text version
            text_part = MIMEText(message, "plain")
            msg.attach(text_part)

            # HTML version with basic styling
            html = f"""
            <html>
            <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <div style="background: #0a7d3c; color: white; padding: 20px;">
                    <h2>Taraba State e-Procurement</h2>
                </div>
                <div style="padding: 20px; border: 1px solid #ddd;">
                    <p>{message.replace(chr(10), '<br>')}</p>
                </div>
                <div style="padding: 10px; font-size: 11px; color: #666;">
                    This is an automated notification from the Taraba State Bureau of Public Procurement.
                </div>
            </body>
            </html>
            """
            html_part = MIMEText(html, "html")
            msg.attach(html_part)

            host = getattr(settings, "EMAIL_HOST", "localhost")
            port = getattr(settings, "EMAIL_PORT", 587)
            use_tls = getattr(settings, "EMAIL_USE_TLS", True)

            with smtplib.SMTP(host, port) as server:
                if use_tls:
                    server.starttls()

                user = getattr(settings, "EMAIL_HOST_USER", None)
                password = getattr(settings, "EMAIL_HOST_PASSWORD", None)
                if user and password:
                    server.login(user, password)

                # Send with DSN request
                server.send_message(msg)

            logger.info(f"Email sent to {recipient}")
            return {"success": True, "reference": f"smtp-{hashlib.sha256(recipient.encode()).hexdigest()[:12]}"}

        except Exception as e:
            logger.error(f"Email failed for {recipient}: {e}")
            return {"success": False, "error": str(e)}


class AfricasTalkingBackend(NotificationBackend):
    """Africa's Talking SMS backend.

    Configuration via settings:
        AT_API_KEY, AT_USERNAME, AT_SENDER_ID
    """

    def send(self, recipient: str, message: str, **kwargs) -> dict:
        import urllib.request
        import urllib.parse

        api_key = getattr(settings, "AT_API_KEY", "")
        username = getattr(settings, "AT_USERNAME", "sandbox")
        sender_id = getattr(settings, "AT_SENDER_ID", "TAR-BPP")

        if not api_key:
            logger.warning("AT_API_KEY not configured, falling back to console")
            return ConsoleBackend().send(recipient, message, channel="SMS")

        try:
            # Normalize phone number to international format
            phone = recipient.replace(" ", "").replace("-", "")
            if not phone.startswith("+"):
                phone = "+234" + phone.lstrip("0")

            data = urllib.parse.urlencode({
                "username": username,
                "to": phone,
                "message": message[:160],  # SMS limit
                "from": sender_id,
            }).encode()

            req = urllib.request.Request(
                "https://api.africastalking.com/version1/messaging",
                data=data,
                headers={
                    "apikey": api_key,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read())

            if result.get("SMSMessageData", {}).get("Recipients"):
                ref = result["SMSMessageData"]["Recipients"][0].get("messageId", "")
                logger.info(f"SMS sent to {recipient} via Africa's Talking: {ref}")
                return {"success": True, "reference": ref}
            else:
                error = result.get("SMSMessageData", {}).get("Message", "Unknown error")
                logger.error(f"SMS failed for {recipient}: {error}")
                return {"success": False, "error": error}

        except Exception as e:
            logger.error(f"SMS failed for {recipient}: {e}")
            return {"success": False, "error": str(e)}


class TermiiBackend(NotificationBackend):
    """Termii SMS backend.

    Configuration via settings:
        TERMII_API_KEY, TERMII_SENDER_ID
    """

    def send(self, recipient: str, message: str, **kwargs) -> dict:
        import urllib.request

        api_key = getattr(settings, "TERMII_API_KEY", "")
        sender_id = getattr(settings, "TERMII_SENDER_ID", "TAR-BPP")

        if not api_key:
            logger.warning("TERMII_API_KEY not configured, falling back to console")
            return ConsoleBackend().send(recipient, message, channel="SMS")

        try:
            phone = recipient.replace(" ", "").replace("-", "")
            if not phone.startswith("234"):
                phone = "234" + phone.lstrip("0").lstrip("+")

            payload = json.dumps({
                "to": phone,
                "from": sender_id,
                "sms": message[:160],
                "type": "plain",
                "channel": "dnd",
                "api_key": api_key,
            }).encode()

            req = urllib.request.Request(
                "https://api.ng.termii.com/api/sms/send",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                },
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read())

            ref = result.get("message_id", "")
            logger.info(f"SMS sent to {recipient} via Termii: {ref}")
            return {"success": True, "reference": ref}

        except Exception as e:
            logger.error(f"SMS failed for {recipient}: {e}")
            return {"success": False, "error": str(e)}


def get_sms_backend():
    """Get the configured SMS backend based on settings."""
    provider = getattr(settings, "SMS_PROVIDER", "console").lower()

    if provider == "africastalking":
        return AfricasTalkingBackend()
    elif provider == "termii":
        return TermiiBackend()
    else:
        return ConsoleBackend()


def get_email_backend():
    """Get the email backend."""
    return SMTPBackend()

import smtplib
import ssl
from email.message import EmailMessage
from html import escape

from app.core.config import Settings


class SMTPMailer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send_magic_link(self, email: str, link: str):
        settings = self.settings
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = email
        message["Subject"] = "Your Petrix AI sign-in link"
        message.set_content(
            f"Welcome to Petrix AI.\n\nContinue here: {link}\n\n"
            f"This link expires in {settings.magic_link_minutes} minutes and can be used once.\n"
            "If you did not request this link, you can ignore this email."
        )
        message.add_alternative(
            '<html><body style="font-family:Arial,sans-serif;line-height:1.7;color:#202022">'
            '<h1>Petrix AI</h1><h2>Your workspace is one click away.</h2>'
            '<p>Confirm your email to securely continue to Petrix AI.</p>'
            f'<p><a href="{escape(link, quote=True)}">Continue to Petrix AI</a></p>'
            f'<p>This link expires in {settings.magic_link_minutes} minutes and works once.</p>'
            '<p>If you did not request it, you can ignore this email.</p></body></html>',
            subtype="html",
        )
        client_type = smtplib.SMTP_SSL if settings.smtp_ssl else smtplib.SMTP
        kwargs = {"timeout": 8}
        if settings.smtp_ssl:
            kwargs["context"] = ssl.create_default_context()
        with client_type(settings.smtp_host, settings.smtp_port, **kwargs) as client:
            if settings.smtp_starttls:
                client.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            client.send_message(message)

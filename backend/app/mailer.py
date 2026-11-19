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


    def send_ticket(self, delivery):
        """One recipient per durable outbox item; expose no SMTP response bodies."""
        from email.utils import formataddr
        from app.tickets.notifications import DeliveryError
        message=EmailMessage()
        message['From']=formataddr((delivery.sender_name,delivery.sender))
        message['To']=delivery.recipient
        if getattr(delivery,'cc_recipients',None):message['Cc']=', '.join(delivery.cc_recipients)
        previous=(getattr(delivery,'persona',None) or {}).get('in_reply_to')
        if previous and not any(ord(c)<32 for c in previous):
            message['In-Reply-To']=previous;message['References']=previous
        message['Subject']=delivery.subject
        message['Message-ID']=delivery.message_id
        message.set_content(delivery.body_text)
        message.add_alternative(delivery.body_html,subtype='html')
        for asset in getattr(delivery,'attachments',[]):
            main,sub=asset.content_type.split('/',1)
            message.add_attachment(asset.data,maintype=main,subtype=sub,filename=asset.file_name)
        settings=self.settings;transmitting=False
        client_type=smtplib.SMTP_SSL if settings.smtp_ssl else smtplib.SMTP
        kwargs={'timeout':8}
        if settings.smtp_ssl:kwargs['context']=ssl.create_default_context()
        try:
            with client_type(settings.smtp_host,settings.smtp_port,**kwargs) as client:
                if settings.smtp_starttls:client.starttls(context=ssl.create_default_context())
                if settings.smtp_username:client.login(settings.smtp_username,settings.smtp_password.get_secret_value())
                transmitting=True
                refused=client.send_message(message)
                if refused:raise DeliveryError(True)
        except DeliveryError:raise
        except (smtplib.SMTPRecipientsRefused,smtplib.SMTPSenderRefused,smtplib.SMTPDataError):raise DeliveryError(False) from None
        except Exception:raise DeliveryError(transmitting) from None

    def send_invitation(self, email, link, workspace_name):
        message=EmailMessage()
        message['From']=self.settings.smtp_from
        message['To']=email
        message['Subject']='Invitation to '+workspace_name[:100]
        message.set_content('You are invited to '+workspace_name+'. Sign in with this email address, then accept your invitation:\n\n'+link+'\n\nThis invitation expires in seven days.')
        settings=self.settings
        client_type=smtplib.SMTP_SSL if settings.smtp_ssl else smtplib.SMTP
        kwargs={'timeout':8}
        if settings.smtp_ssl:kwargs['context']=ssl.create_default_context()
        with client_type(settings.smtp_host,settings.smtp_port,**kwargs) as client:
            if settings.smtp_starttls:client.starttls(context=ssl.create_default_context())
            if settings.smtp_username:client.login(settings.smtp_username,settings.smtp_password.get_secret_value())
            client.send_message(message)

    def send_export(self,email,filename,data,identifier):
        message=EmailMessage();message['From']=self.settings.smtp_from;message['To']=email;message['Subject']='Your Petrix AI export';message['Message-ID']='<export-'+identifier+'@petrix.local>'
        message.set_content('Your requested export is attached. It is also available in Settings → Teamspace → Exports.')
        message.add_attachment(data,maintype='text',subtype='csv',filename=filename)
        settings=self.settings;client_type=smtplib.SMTP_SSL if settings.smtp_ssl else smtplib.SMTP;kwargs={'timeout':8}
        if settings.smtp_ssl:kwargs['context']=ssl.create_default_context()
        with client_type(settings.smtp_host,settings.smtp_port,**kwargs) as client:
            if settings.smtp_starttls:client.starttls(context=ssl.create_default_context())
            if settings.smtp_username:client.login(settings.smtp_username,settings.smtp_password.get_secret_value())
            client.send_message(message)

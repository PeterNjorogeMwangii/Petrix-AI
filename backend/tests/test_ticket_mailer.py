"""Actual SMTP dialogue against a local sink; no external mail delivery."""
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
import socketserver
import threading
import pytest
from app.mailer import SMTPMailer
from app.core.config import Settings
from app.tickets.notifications import DeliveryError


@contextmanager
def smtp_sink(reject=False,lose_ack=False):
    messages=[]
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b'220 localhost SMTP test\r\n')
            while True:
                line=self.rfile.readline()
                if not line:return
                command=line.split(b' ',1)[0].strip().upper()
                if command in {b'EHLO',b'HELO'}:self.wfile.write(b'250-localhost\r\n250 SIZE 1000000\r\n')
                elif command==b'MAIL':self.wfile.write(b'250 OK\r\n')
                elif command==b'RCPT':self.wfile.write(b'550 Rejected\r\n' if reject else b'250 OK\r\n')
                elif command==b'DATA':
                    self.wfile.write(b'354 Data\r\n');parts=[]
                    while True:
                        chunk=self.rfile.readline()
                        if not chunk or chunk==b'.\r\n':break
                        parts.append(chunk)
                    messages.append(b''.join(parts))
                    if lose_ack:return
                    self.wfile.write(b'250 Accepted\r\n')
                elif command==b'QUIT':self.wfile.write(b'221 Bye\r\n');return
                elif command==b'RSET':self.wfile.write(b'250 Reset\r\n')
                else:self.wfile.write(b'250 OK\r\n')
    server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield server.server_address[1],messages
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def mailer(port):
    return SMTPMailer(Settings(_env_file=None,database_url='postgresql+psycopg://test:test@localhost/test',session_signing_secret='mail-test-signing-secret-more-than-32-characters',smtp_host='127.0.0.1',smtp_port=port))


def delivery():
    return SimpleNamespace(sender_name='Petrix Support',sender='support@mail.example.com',recipient='customer@example.com',subject='[PTX-000001] Complaint received',message_id='<ticket-fixed@mail.example.com>',body_text='Your reference is PTX-000001.',body_html='<p>Your reference is <strong>PTX-000001</strong>.</p>')


def test_actual_smtp_sends_both_alternatives_and_stable_message_id():
    with smtp_sink() as (port,messages):mailer(port).send_ticket(delivery())
    assert len(messages)==1
    message=BytesParser(policy=policy.default).parsebytes(messages[0])
    assert message['Message-ID']=='<ticket-fixed@mail.example.com>' and message['To']=='customer@example.com'
    assert message.get_body(preferencelist=('html',)).get_content().strip()=='<p>Your reference is <strong>PTX-000001</strong>.</p>'
    assert 'PTX-000001' in message.get_body(preferencelist=('plain',)).get_content()


def test_explicit_smtp_rejection_is_a_failed_delivery():
    with smtp_sink(reject=True) as (port,messages):
        with pytest.raises(DeliveryError) as error:mailer(port).send_ticket(delivery())
    assert not error.value.uncertain and messages==[]


def test_lost_smtp_ack_is_uncertain_and_does_not_replay():
    with smtp_sink(lose_ack=True) as (port,messages):
        with pytest.raises(DeliveryError) as error:mailer(port).send_ticket(delivery())
    assert error.value.uncertain and len(messages)==1


def test_actual_smtp_cc_and_reply_thread_headers():
    item=delivery();item.cc_recipients=['copy@example.com','another@example.com'];item.persona={'in_reply_to':'<incoming@example.com>'}
    with smtp_sink() as (port,messages):mailer(port).send_ticket(item)
    message=BytesParser(policy=policy.default).parsebytes(messages[0])
    assert [address.addr_spec for address in message['Cc'].addresses]==item.cc_recipients
    assert message['In-Reply-To']=='<incoming@example.com>' and message['References']=='<incoming@example.com>'

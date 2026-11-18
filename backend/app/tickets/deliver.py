"""Run from backend: python -m app.tickets.deliver [--limit 100]."""
import argparse
from app.core.config import Settings
from app.database import create_database
from app.mailer import SMTPMailer
from app.tickets.notifications import dispatch


def main():
    parser=argparse.ArgumentParser(description='Send queued initial ticket emails once. Failed or unconfirmed emails require review.')
    parser.add_argument('--limit',type=int,default=100);args=parser.parse_args()
    if not 1<=args.limit<=1000:parser.error('--limit must be between 1 and 1000')
    settings=Settings();engine,factory=create_database(settings.database_url.get_secret_value())
    try:
        with factory() as db:print('Processed {} queued email(s).'.format(dispatch(db,SMTPMailer(settings),limit=args.limit)))
    finally:engine.dispose()


if __name__=='__main__':main()

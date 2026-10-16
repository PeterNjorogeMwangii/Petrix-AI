from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def create_database(url: str):
    engine = create_engine(url, pool_pre_ping=True)
    return engine, sessionmaker(engine, expire_on_commit=False)


def get_db(request: Request):
    with request.app.state.session_factory() as db:
        yield db

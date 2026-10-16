from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import Settings
from app.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata
url = config.attributes.get("database_url")


def run_online(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()

if context.is_offline_mode():
    url = url or Settings().database_url.get_secret_value()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    existing_connection = config.attributes.get("connection")
    if existing_connection is not None:
        run_online(existing_connection)
    else:
        engine = create_engine(url or Settings().database_url.get_secret_value(), poolclass=pool.NullPool)
        with engine.connect() as connection:
            run_online(connection)

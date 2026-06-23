from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

# Import models so autogenerate can see the full schema.
from app.database import Base  # noqa: E402
from app.config import settings  # noqa: E402
import app.models  # noqa: F401,E402

target_metadata = Base.metadata

# Allow an env override so we can generate the first migration against an empty DB.
import os  # noqa: E402

DB_URL = os.environ.get("ALEMBIC_DB_URL", settings.database_url)


def run_migrations_offline():
    context.configure(url=DB_URL, target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = DB_URL
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=True)  # batch mode = SQLite ALTER support
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

#!/usr/bin/env bash
# Create the dedicated Gold database/roles. Run as PostgreSQL superuser, for example:
#   sudo -u postgres GOLD_WRITER_PASSWORD='...' GOLD_READER_PASSWORD='...' ./create_gold_db.sh
set -euo pipefail

: "${GOLD_WRITER_PASSWORD:?Set GOLD_WRITER_PASSWORD in the invoking environment}"
: "${GOLD_READER_PASSWORD:?Set GOLD_READER_PASSWORD in the invoking environment}"

if psql --dbname=postgres -Atqc "SELECT 1 FROM pg_roles WHERE rolname='gold_writer'" | grep -qx 1; then
  psql -v ON_ERROR_STOP=1 --dbname=postgres --set=gold_writer_password="$GOLD_WRITER_PASSWORD" <<'SQL'
ALTER ROLE gold_writer LOGIN PASSWORD :'gold_writer_password';
SQL
else
  psql -v ON_ERROR_STOP=1 --dbname=postgres --set=gold_writer_password="$GOLD_WRITER_PASSWORD" <<'SQL'
CREATE ROLE gold_writer LOGIN PASSWORD :'gold_writer_password';
SQL
fi

if psql --dbname=postgres -Atqc "SELECT 1 FROM pg_roles WHERE rolname='gold_reader'" | grep -qx 1; then
  psql -v ON_ERROR_STOP=1 --dbname=postgres --set=gold_reader_password="$GOLD_READER_PASSWORD" <<'SQL'
ALTER ROLE gold_reader LOGIN PASSWORD :'gold_reader_password';
SQL
else
  psql -v ON_ERROR_STOP=1 --dbname=postgres --set=gold_reader_password="$GOLD_READER_PASSWORD" <<'SQL'
CREATE ROLE gold_reader LOGIN PASSWORD :'gold_reader_password';
SQL
fi

if ! psql --dbname=postgres -Atqc "SELECT 1 FROM pg_database WHERE datname='lakehouse'" | grep -qx 1; then
  createdb --owner=gold_writer lakehouse
fi

psql -v ON_ERROR_STOP=1 --dbname=lakehouse <<'SQL'
ALTER DATABASE lakehouse OWNER TO gold_writer;
GRANT CONNECT ON DATABASE lakehouse TO gold_writer, gold_reader;
GRANT CREATE ON DATABASE lakehouse TO gold_writer;
CREATE SCHEMA IF NOT EXISTS gold AUTHORIZATION gold_writer;
CREATE SCHEMA IF NOT EXISTS gold_control AUTHORIZATION gold_writer;
CREATE SCHEMA IF NOT EXISTS gold_staging AUTHORIZATION gold_writer;
GRANT USAGE ON SCHEMA gold, gold_control, gold_staging TO gold_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE gold_writer IN SCHEMA gold, gold_control, gold_staging
    GRANT SELECT ON TABLES TO gold_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE gold_writer IN SCHEMA gold, gold_control, gold_staging
    GRANT USAGE, SELECT ON SEQUENCES TO gold_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA gold, gold_control, gold_staging TO gold_reader;
SQL

printf '%s\n' 'Gold database and roles are ready; apply src/gold/ddl.sql with gold_writer next.'

#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v app_password="$POSTGRES_APP_PASSWORD" -v db_name="$POSTGRES_DB" <<'SQL'
CREATE ROLE reliefgrid_app LOGIN PASSWORD :'app_password';
GRANT CONNECT ON DATABASE :"db_name" TO reliefgrid_app;
GRANT USAGE ON SCHEMA public TO reliefgrid_app;
SQL

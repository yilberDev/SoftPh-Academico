#!/usr/bin/env bash
# Creates the development and test databases and the application user in the
# local MySQL server, using the values from the root .env. Idempotent and
# non-destructive: it never drops databases, users, or tables, and creates no
# tables (the schema is managed by Alembic).
#
# Usage (needs MySQL admin rights, e.g. root via auth_socket):
#   sudo bash database/scripts/create-local-databases.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy .env.example to .env and fill it in first." >&2
  exit 1
fi

read_env() {
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2-
}

DB_NAME="$(read_env DB_NAME)"
TEST_DB_NAME="$(read_env TEST_DB_NAME)"
DB_USER="$(read_env DB_USER)"
DB_PASSWORD="$(read_env DB_PASSWORD)"

for var in DB_NAME TEST_DB_NAME DB_USER DB_PASSWORD; do
  if [[ -z "${!var}" ]]; then
    echo "$var is empty in $ENV_FILE." >&2
    exit 1
  fi
done

if [[ "$TEST_DB_NAME" != *_test || "$TEST_DB_NAME" == "$DB_NAME" ]]; then
  echo "TEST_DB_NAME must end in '_test' and differ from DB_NAME." >&2
  exit 1
fi

for name in "$DB_NAME" "$TEST_DB_NAME" "$DB_USER"; do
  if [[ ! "$name" =~ ^[A-Za-z0-9_]+$ ]]; then
    echo "Database and user names may only contain letters, digits and '_': $name" >&2
    exit 1
  fi
done

ESCAPED_PASSWORD="${DB_PASSWORD//\\/\\\\}"
ESCAPED_PASSWORD="${ESCAPED_PASSWORD//\'/\\\'}"

# The SQL is sent through stdin so the password never appears in process arguments.
mysql <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS \`$TEST_DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$ESCAPED_PASSWORD';
CREATE USER IF NOT EXISTS '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$ESCAPED_PASSWORD';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost', '$DB_USER'@'127.0.0.1';
GRANT ALL PRIVILEGES ON \`$TEST_DB_NAME\`.* TO '$DB_USER'@'localhost', '$DB_USER'@'127.0.0.1';
SQL

echo "Databases '$DB_NAME' and '$TEST_DB_NAME' and user '$DB_USER' are ready."

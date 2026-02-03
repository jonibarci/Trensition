#!/usr/bin/env bash
set -euo pipefail

run_db_init() {
  local retries="${DB_INIT_RETRIES:-30}"
  local delay="${DB_INIT_DELAY_SECONDS:-2}"
  local i=1

  while true; do
    if uv run python -m app.database.db --init; then
      return 0
    fi

    if [[ "$i" -ge "$retries" ]]; then
      echo "Database init failed after ${retries} attempts." >&2
      return 1
    fi

    echo "Database not ready yet. Retry ${i}/${retries} in ${delay}s..."
    i=$((i + 1))
    sleep "$delay"
  done
}

if [[ "${DB_INIT_ON_STARTUP:-1}" == "1" ]]; then
  run_db_init
else
  echo "Skipping database init on startup (DB_INIT_ON_STARTUP=0)."
fi

exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

#!/bin/sh
set -eu

cd "$(dirname "$0")"
backup_dir="./backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"

archive_volume() {
  volume="$1"
  output="$2"
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    docker run --rm -v "$volume:/source:ro" -v "$(pwd)/$backup_dir:/backup" alpine:3.22 \
      tar -czf "/backup/$output" -C /source .
  fi
}

if [ -n "$(docker compose ps -q cobeb-mysql 2>/dev/null)" ]; then
  docker compose exec -T cobeb-mysql sh -c \
    'MYSQL_PWD="$MYSQL_PASSWORD" mysqldump --single-transaction --quick --no-tablespaces --host=127.0.0.1 --user="$MYSQL_USER" "$MYSQL_DATABASE"' \
    > "$backup_dir/cobeb-mysql.sql"
else
  echo "AVISO: cobeb-mysql ainda não está em execução; backup do banco principal ignorado." >&2
fi

if [ -n "$(docker compose ps -q evolution-postgres 2>/dev/null)" ]; then
  docker compose exec -T evolution-postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$backup_dir/evolution.sql"
fi

archive_volume cobeb-storage cobeb-storage.tar.gz
archive_volume cobeb_evolution-instances evolution-instances.tar.gz
archive_volume cobeb_evolution-postgres-data evolution-postgres-data.tar.gz
archive_volume cobeb_caddy-data caddy-data.tar.gz
archive_volume cobeb_caddy-config caddy-config.tar.gz

cp .env api.env evolution.env Caddyfile compose.yml "$backup_dir/"
chmod -R go-rwx "$backup_dir"
echo "Backup local criado em $backup_dir. Copie-o também para um armazenamento externo ao servidor."

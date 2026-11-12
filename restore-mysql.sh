#!/bin/sh
set -eu

cd "$(dirname "$0")"

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "Uso: ./restore-mysql.sh /caminho/para/cobeb-mysql.sql" >&2
  exit 1
fi

dump_file="$1"

docker compose config --quiet
docker compose up -d --wait cobeb-mysql

table_count="$(docker compose exec -T cobeb-mysql sh -c \
  'MYSQL_PWD="$MYSQL_PASSWORD" mysql --batch --skip-column-names --host=127.0.0.1 --user="$MYSQL_USER" --execute="SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()" "$MYSQL_DATABASE"')"

if [ "$table_count" -ne 0 ]; then
  echo "Restauração cancelada: o banco principal já possui $table_count tabela(s)." >&2
  echo "Use este script somente no banco novo e vazio para evitar sobrescrever dados." >&2
  exit 1
fi

docker compose exec -T cobeb-mysql sh -c \
  'MYSQL_PWD="$MYSQL_PASSWORD" mysql --host=127.0.0.1 --user="$MYSQL_USER" "$MYSQL_DATABASE"' \
  < "$dump_file"

echo "Banco importado com sucesso. Execute ./deploy.sh para aplicar migrations e subir a aplicação."

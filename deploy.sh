#!/bin/sh
set -eu

cd "$(dirname "$0")"

for required in .env api.env evolution.env Caddyfile compose.yml; do
  if [ ! -f "$required" ]; then
    echo "Arquivo obrigatório ausente: $required" >&2
    exit 1
  fi
done

docker compose config --quiet
chmod 600 .env api.env evolution.env
docker compose pull

if [ -x ./backup.sh ]; then
  ./backup.sh
else
  echo "AVISO: backup.sh não encontrado; confirme o backup do banco principal antes de produção." >&2
fi

docker compose up -d --wait cobeb-mysql
docker compose run --rm api php artisan migrate --force
docker compose up -d --remove-orphans
docker compose exec -T api php artisan storage:link --force
docker compose exec -T api php artisan optimize:clear
docker compose exec -T api php artisan queue:restart

docker compose exec -T frontend wget --quiet --tries=1 --spider http://127.0.0.1/
docker compose exec -T api php -r '$body=@file_get_contents("http://127.0.0.1/up"); exit($body === false ? 1 : 0);'
docker compose exec -T api php -r '$s=@fsockopen("reverb",8080,$e,$m,5); exit($s ? 0 : 1);'
docker compose exec -T api php -r '$s=@fsockopen("evolution-api",8080,$e,$m,5); exit($s ? 0 : 1);'
docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile

docker compose ps
echo "Deploy concluído. Front-end, API, Reverb, Evolution e Caddy responderam aos testes internos."

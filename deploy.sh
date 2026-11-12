#!/bin/sh
set -eu

export DOCKER_CONFIG="${DOCKER_CONFIG:-/etc/docker}"

cd "$(dirname "$0")"

for required in .env api.env evolution.env Caddyfile compose.yml; do
  if [ ! -f "$required" ]; then
    echo "Arquivo obrigatório ausente: $required" >&2
    exit 1
  fi
done

/usr/bin/docker compose config --quiet
chmod 600 .env api.env evolution.env
/usr/bin/docker  compose pull

if [ -x ./backup.sh ]; then
  ./backup.sh
else
  echo "AVISO: backup.sh não encontrado; confirme o backup do banco principal antes de produção." >&2
fi

/usr/bin/docker compose up -d --wait cobeb-mysql
/usr/bin/docker compose run --rm api php artisan migrate --force
/usr/bin/docker compose up -d --remove-orphans
/usr/bin/docker compose exec -T api php artisan storage:link --force
/usr/bin/docker compose exec -T api php artisan optimize:clear
/usr/bin/docker compose exec -T api php artisan queue:restart

/usr/bin/docker compose exec -T frontend wget --quiet --tries=1 --spider http://127.0.0.1/
/usr/bin/docker compose exec -T api php -r '$body=@file_get_contents("http://127.0.0.1/up"); exit($body === false ? 1 : 0);'
/usr/bin/docker compose exec -T api php -r '$s=@fsockopen("reverb",8080,$e,$m,5); exit($s ? 0 : 1);'
/usr/bin/docker compose exec -T api php -r '$s=@fsockopen("evolution-api",8080,$e,$m,5); exit($s ? 0 : 1);'
/usr/bin/docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile

/usr/bin/docker compose ps
echo "Deploy concluído. Front-end, API, Reverb, Evolution e Caddy responderam aos testes internos."

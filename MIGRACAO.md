# Migração da stack antiga (Dockhand) para a stack nova (Caddy)

Runbook para migrar a Cobeb da stack atual (`.env` + `compose.yaml`, portas 8150–8153) para o pacote deste repositório (`compose.yml`, `api.env`, `evolution.env`, `Caddyfile`, Caddy em 80/443).

> Execute tudo como o usuário `cobeb` no servidor Linux. **Não rode `deploy.sh` antes da etapa 6.**

## Visão geral

| Etapa | O que faz                                                  | Para o sistema antigo? |
|-------|------------------------------------------------------------|------------------------|
|   0   | Pré-requisitos e decisões                                  | Não                    |
|   1   | Inventário da stack antiga                                 | Não                    |
|   2   | Backup completo da stack antiga                            | Não                    |
|   3   | Montar `/opt/cobeb` com o pacote novo                      | Não                    |
|   4   | Login no GHCR                                              | Não                    | 
|   5   | Ensaio (subir a stack nova em paralelo com dados copiados) | Não                    |
|   6   | Janela de manutenção: cópia final dos dados e virada       | **Sim**                |
|   7   | Webhook de deploy automático                               | Não                    |
|   8   | Rollback                                                   | —                      |

A stack nova usa as portas 80/443 e a antiga usa 8150–8153, então as duas podem coexistir até a virada. **Atenção:** nunca deixe a Evolution API das duas stacks conectadas ao mesmo número de WhatsApp ao mesmo tempo.

## 0. Pré-requisitos (resolver antes de marcar a janela)

- [ ] DNS (registro A para o IP do servidor) dos 4 domínios: `APP_DOMAIN`, `API_DOMAIN`, `REVERB_DOMAIN`, `EVOLUTION_DOMAIN`.
- [ ] Portas 80 e 443 (TCP e UDP 443) livres no servidor e liberadas no firewall. Conferir: `sudo ss -ltnup | grep -E ':(80|443)\b'`.
- [ ] Token GitHub com `read:packages` (e SSO autorizado na organização).
- [ ] Nome do seeder do menu (o menu vazio não é resolvido pelo `deploy.sh`).
- [ ] Acesso ao painel da Meta (para trocar a URL do webhook do WhatsApp) e ao `.env` atual do Laravel.
- [ ] Combinar a janela de manutenção (sugestão: 30–60 min fora do horário de uso).
- [ ] Combinar com os usuários: o endereço de acesso muda de `IP:8150` para `https://APP_DOMAIN`.

## 1. Inventário da stack antiga

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
docker volume ls
docker network ls
docker inspect --format '{{.Name}}: {{range .Mounts}}{{.Name}}={{.Destination}} {{end}}' $(docker ps -q)
```

Anote:

1. **Project name** da stack antiga (prefixo dos volumes). Se for `cobeb`, os volumes `cobeb_*` e `cobeb-*` **colidirão** com os da stack nova (`cobeb-storage`, `cobeb-mysql-data`, `cobeb_evolution-*`, `cobeb_caddy-*`). Nesse caso, renomeie o project da stack antiga ou use as cópias (etapa 5) em volumes de nome diferente. **Não suba a stack nova no mesmo diretório/projeto da antiga.**
2. Container e volume do **MySQL** (ou outro banco) atual, e credenciais.
3. Volume do **storage** do Laravel (`/var/www/html/storage`: uploads, importações).
4. Container/volume da **Evolution API** (instances) e do **Postgres** dela.
5. As variáveis atuais: `APP_KEY`, `REVERB_APP_ID/KEY/SECRET`, `EVOLUTION_API_KEY`, token do webhook Meta, números de WhatsApp.

Guarde o `.env` e o `compose.yaml` antigos: `cp` para `~/cobeb-antigo/`.

## 2. Backup completo da stack antiga

Faça **antes de qualquer coisa** e copie para fora do servidor.

```bash
mkdir -p ~/cobeb-antigo/backup && cd ~/cobeb-antigo/backup

# Banco principal (ajuste container, usuário e banco)
docker exec <CONTAINER_MYSQL_ANTIGO> sh -c 'mysqldump --single-transaction --quick --no-tablespaces -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' > cobeb-mysql.sql
# Postgres da Evolution
docker exec <CONTAINER_PG_ANTIGO> sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > evolution.sql

# Volumes (storage da API e instances da Evolution)
for v in <VOLUME_STORAGE> <VOLUME_EVOLUTION_INSTANCES>; do
  docker run --rm -v "$v:/source:ro" -v "$PWD:/backup" alpine:3.22 tar -czf "/backup/$v.tar.gz" -C /source .
done

ls -lh && head -c 300 cobeb-mysql.sql
```

Validação: os `.sql` devem ter tamanho > 0 e terminar com `-- Dump completed`. Copie a pasta para um local externo (`scp`).

## 3. Montar `/opt/cobeb` com o pacote novo

```bash
sudo install -d -o cobeb -g cobeb /opt/cobeb
# copie do repositório: compose.yml Caddyfile deploy.sh backup.sh github_webhook.py
#   cobeb-webhook.service  .env.example api.env.example evolution.env.example cobeb-webhook.env.example
cd /opt/cobeb
cp .env.example .env; cp api.env.example api.env; cp evolution.env.example evolution.env
chmod +x deploy.sh backup.sh github_webhook.py
```

### 3.1 `api.env` — preservar o que já existe

Copie do `.env` atual do Laravel. **Obrigatório manter iguais:**

- `APP_KEY` (se mudar, dados criptografados e sessões ficam inválidos);
- `REVERB_APP_ID`, `REVERB_APP_KEY`, `REVERB_APP_SECRET`;
- demais variáveis de negócio (`EVOLUTION_DEFAULT_NUMBER`, `WHATSAPP_RATE_LIMIT_PER_MINUTE` etc.).

`REVERB_HOST=reverb`, `REVERB_PORT=8080`, `REVERB_SCHEME=http` são o tráfego interno (servidor → Reverb). O público (navegador) usa `REVERB_DOMAIN` na porta 443, definido pelo compose no frontend.

### 3.2 `.env` (compose)

- `FRONTEND_IMAGE_TAG` / `API_IMAGE_TAG`: use a tag que a Cobeb deve rodar. O padrão do compose é `develop`; o `.env.example` sugere `v1.0.0`. **Confirme que a tag existe no GHCR** antes de subir.
- Os 4 domínios e `ACME_EMAIL`.
- `REVERB_APP_KEY` **igual** ao do `api.env`.
- `EVOLUTION_MASTER_KEY` e `META_WEBHOOK_VERIFY_TOKEN`: usar os **mesmos valores atuais** se o webhook da Meta já está configurado com esse token.
- Senhas do banco: podem ser novas, pois o banco é recriado e restaurado do dump. Use apenas caracteres alfanuméricos (a senha do Postgres entra em uma URL).

### 3.3 `evolution.env`

Revise com a configuração atual da Evolution (`DATABASE_*`, `CONFIG_SESSION_PHONE_*`). Se a antiga usava outra versão da imagem, confirme compatibilidade: a nova fixa `evoapicloud/evolution-api:v2.3.7`. Rodar versão diferente sobre o mesmo banco pode exigir migração do schema; confira a versão antiga com `docker inspect`.

```bash
chmod 600 .env api.env evolution.env
docker compose config --quiet && echo OK
```

## 4. Login no GHCR

Conforme `README_DEPLOY.md`, seção 3:

```bash
sudo install -d -o cobeb -g docker -m 700 /etc/docker
read -s GHCR_TOKEN
printf '%s' "$GHCR_TOKEN" | sudo -u cobeb env HOME=/tmp DOCKER_CONFIG=/etc/docker /usr/bin/docker login ghcr.io -u USUARIO_GITHUB --password-stdin
unset GHCR_TOKEN
sudo -u cobeb env HOME=/tmp DOCKER_CONFIG=/etc/docker /usr/bin/docker pull ghcr.io/unify-solucoes-que-conectam/cobeb-gestao-reposicao:<TAG>
```

Isso também resolve a verificação de atualizações do Dockhand.

## 5. Ensaio (sem parar a stack antiga)

Objetivo: subir a stack nova com uma **cópia** dos dados e validar tudo, antes da janela.

```bash
cd /opt/cobeb
docker compose pull
docker compose up -d --wait cobeb-mysql

# Restaurar o dump no MySQL novo
docker compose exec -T cobeb-mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -u"$MYSQL_USER" "$MYSQL_DATABASE"' < ~/cobeb-antigo/backup/cobeb-mysql.sql

# Restaurar storage da API
docker volume create cobeb-storage
docker run --rm -v cobeb-storage:/target -v ~/cobeb-antigo/backup:/backup:ro alpine:3.22 \
  sh -c 'tar -xzf /backup/<VOLUME_STORAGE>.tar.gz -C /target'
```

**Evolution/WhatsApp no ensaio:** não restaure as instances nem o Postgres da Evolution no ensaio com a antiga ainda ligada; duas Evolutions com a mesma sessão derrubam a conexão do WhatsApp. Deixe a Evolution nova **vazia** no ensaio (ou não suba `evolution-api`) e restaure só na janela (etapa 6).

Subir o restante e aplicar migrations:

```bash
docker compose run --rm api php artisan migrate --force
docker compose run --rm api php artisan db:seed --class=<SeederDoMenu> --force   # só se o menu depende disso
docker compose up -d --remove-orphans
docker compose ps
docker compose logs caddy --tail=50     # emissão dos certificados Let's Encrypt
```

Validação do ensaio:

- [ ] `docker compose ps`: todos `healthy`/`running`.
- [ ] `https://APP_DOMAIN` abre o front com certificado válido e o login funciona.
- [ ] Menu aparece; dados migrados presentes (contagem de tabelas-chave bate com a antiga).
- [ ] Notificação em tempo real (Reverb) funciona (WebSocket em `wss://REVERB_DOMAIN`).
- [ ] `curl -I https://EVOLUTION_DOMAIN/` retorna 404 (esperado) e `/webhook/meta` responde.
- [ ] Workers: `docker compose logs worker-default worker-imports worker-whatsapp --tail=20` sem erros; uma importação de teste roda.

Se algo falhar, a produção antiga não foi afetada. Corrija e repita (`docker compose down -v` apaga apenas os volumes **da stack nova**, mas confirme com `docker volume ls` que não são os da antiga).

## 6. Janela de manutenção (virada)

1. Avisar usuários; **parar a stack antiga** (pare no Dockhand, sem apagar volumes). Daqui em diante o sistema está indisponível.
2. Backup final (repita a etapa 2 com nomes novos, ex.: `backup-final/`).
3. Recarregar os dados na stack nova:

   ```bash
   cd /opt/cobeb
   docker compose down            # mantém volumes
   docker volume rm cobeb-mysql-data cobeb-storage
   docker compose up -d --wait cobeb-mysql
   docker compose exec -T cobeb-mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -u"$MYSQL_USER" "$MYSQL_DATABASE"' < ~/cobeb-antigo/backup-final/cobeb-mysql.sql
   docker volume create cobeb-storage
   docker run --rm -v cobeb-storage:/target -v ~/cobeb-antigo/backup-final:/backup:ro alpine:3.22 sh -c 'tar -xzf /backup/<VOLUME_STORAGE>.tar.gz -C /target'
   ```

4. Evolution: restaurar Postgres (`evolution.sql` com `psql`) e o volume `cobeb_evolution-instances`, para manter as sessões do WhatsApp.
5. Rodar o deploy oficial (agora sim):

   ```bash
   ./deploy.sh
   ```

   Ele faz pull, `backup.sh`, migrations, recria os containers, limpa caches, reinicia filas e testa front, API, Reverb, Evolution e Caddy.
6. Rodar o seeder do menu (se aplicável) e conferir o menu.
7. **Meta/WhatsApp:** atualizar a URL do webhook para `https://EVOLUTION_DOMAIN/webhook/meta` com o `META_WEBHOOK_VERIFY_TOKEN`. Enviar uma mensagem de teste.
8. Atualizar qualquer app/integração que usava `IP:8150–8153` para os novos domínios.
9. Smoke test final com um usuário da Cobeb; só então liberar o acesso.

## 7. Deploy automático (webhook GitHub)

Seguir `README_DEPLOY.md`, seções 4 a 9 (`/etc/cobeb-webhook.env`, serviço systemd, webhook nos dois repositórios apontando para `https://API_DOMAIN/webhook/github`). Só ative depois que o primeiro deploy manual passar.

## 8. Rollback

Se a validação da etapa 6 falhar e não for corrigível na janela:

1. `cd /opt/cobeb && docker compose down` (não use `-v`).
2. Reiniciar a stack antiga no Dockhand (os volumes dela não foram alterados).
3. Reverter a URL do webhook da Meta para a antiga.
4. Se a Evolution nova chegou a conectar o número, desconecte-a antes de subir a antiga.

Mantenha a stack antiga parada (não apagada) por pelo menos alguns dias após a virada.

## Pontos de atenção

- O `compose.yml` não define `name` de volumes da Evolution/Caddy de forma fixa: eles viram `cobeb_evolution-instances`, `cobeb_evolution-postgres-data`, `cobeb_caddy-*` (project `cobeb`). O `backup.sh` usa esses nomes.
- O `backup.sh` roda antes das migrations em cada deploy; confirme que `backups/` tem espaço em disco e copie periodicamente para fora do servidor.
- O Caddy precisa de DNS correto e porta 80 aberta para emitir os certificados; sem isso, o HTTPS falha.
- Um domínio errado no `.env` só aparece como erro no front/Reverb; confira `docker compose config` e `docker compose logs caddy`.

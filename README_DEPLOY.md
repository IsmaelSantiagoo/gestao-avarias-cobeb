# Procedimento de deploy da Cobeb

Guia para o técnico instalar o ambiente e configurar o deploy automático por webhook do GitHub.

> Execute os comandos Linux dentro do Ubuntu/WSL ou em um servidor Linux. Não use o Git Bash para `sudo`, `systemctl` ou `journalctl`.

## Pré-requisitos

- Ubuntu/WSL2 com systemd ou servidor Linux.
- Docker em execução e `docker compose` disponível.
- Domínio público apontando para o servidor para webhooks reais.
- Portas `80` e `443` públicas; porta `9000` restrita ao host.
- Token GitHub com `read:packages` para imagens privadas do GHCR.

## 1. WSL e usuário

Configure `/etc/wsl.conf`:

```ini
[boot]
systemd=true

[user]
default=cobeb
```

No PowerShell, execute `wsl --shutdown` e reabra o Ubuntu. Confirme:

```bash
whoami
ps -p 1 -o comm=
```

O resultado esperado é `cobeb` e `systemd`.

```bash
id cobeb || sudo adduser cobeb
getent group docker || sudo groupadd docker
sudo usermod -aG docker cobeb
sudo chown -R cobeb:cobeb /opt/cobeb
sudo chmod +x /opt/cobeb/deploy.sh /opt/cobeb/github_webhook.py
```

## 2. Domínio e variáveis

Configure `.env`, `api.env` e `evolution.env`. Para webhooks reais, `API_DOMAIN` deve ser público, por exemplo `api.exemplo.com.br`; `api.cobeb.localhost` serve somente para testes locais.

## 3. Acesso ao GHCR

```bash
sudo install -d -o cobeb -g docker -m 700 /etc/docker
read -s GHCR_TOKEN
printf '%s' "$GHCR_TOKEN" | sudo -u cobeb env HOME=/tmp DOCKER_CONFIG=/etc/docker /usr/bin/docker login ghcr.io -u USUARIO_GITHUB --password-stdin
unset GHCR_TOKEN
```

O token precisa ter `read:packages` e autorização SSO da organização, quando aplicável.

```bash
sudo -u cobeb env HOME=/tmp DOCKER_CONFIG=/etc/docker /usr/bin/docker pull ghcr.io/unify-solucoes-que-conectam/cobeb-gestao-reposicao:develop
```

## 4. Configuração do webhook

```bash
sudo cp /opt/cobeb/cobeb-webhook.env.example /etc/cobeb-webhook.env
sudo chmod 600 /etc/cobeb-webhook.env
sudo nano /etc/cobeb-webhook.env
```

Exemplo:

```env
GITHUB_WEBHOOK_SECRET=SEGREDO_LONGO_E_ALEATORIO
GITHUB_REPOSITORIES=unify-solucoes-que-conectam/cobeb-gestao-reposicao,unify-solucoes-que-conectam/cobeb-gestao-reposicao-api
GITHUB_WORKFLOW_NAMES=Build and Push Docker Image
GITHUB_BRANCH=main
DEPLOY_SCRIPT=/opt/cobeb/deploy.sh
DEPLOY_TIMEOUT_SECONDS=1800
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=9000
```

Gere o segredo com `openssl rand -hex 32`. Os nomes dos workflows devem ser exatamente iguais ao campo `name:`; vários nomes são separados por vírgula.

## 5. Primeiro deploy

As chamadas do script devem usar `/usr/bin/docker compose ...`:

```bash
grep -n 'docker compose' /opt/cobeb/deploy.sh
cd /opt/cobeb
./deploy.sh
docker compose ps
```

Corrija qualquer erro antes de ativar o webhook.

## 6. Serviço systemd

Instale `/opt/cobeb/cobeb-webhook.service` em `/etc/systemd/system/`. Ele deve conter `User=cobeb`, `Group=docker`, `ProtectHome=true` e estas variáveis:

```ini
Environment=HOME=/tmp
Environment=DOCKER_CONFIG=/etc/docker
Environment=DOCKER_CLI_PLUGIN_EXTRA_DIRS=/usr/local/lib/docker/cli-plugins
```

O restante deve usar:

```ini
ExecStart=/usr/bin/python3 /opt/cobeb/github_webhook.py
WorkingDirectory=/opt/cobeb
EnvironmentFile=/etc/cobeb-webhook.env
```

Ative:

```bash
sudo cp /opt/cobeb/cobeb-webhook.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cobeb-webhook
sudo systemctl status cobeb-webhook --no-pager -l
```

Se necessário, crie `/etc/systemd/system/cobeb-webhook.service.d/override.conf` com:

```ini
[Service]
Environment=HOME=/tmp
Environment=DOCKER_CONFIG=/etc/docker
Environment=DOCKER_CLI_PLUGIN_EXTRA_DIRS=/usr/local/lib/docker/cli-plugins
```

Depois use `sudo systemctl daemon-reload` e `sudo systemctl restart cobeb-webhook`. Mantenha `ProtectHome=true`.

## 7. Caddy

No `Caddyfile`, a rota deve encaminhar o webhook para o serviço do host:

```caddy
{$API_DOMAIN} {
    encode zstd gzip
    @github_webhook path /webhook/github
    handle @github_webhook {
        reverse_proxy host.docker.internal:9000
    }
    handle {
        reverse_proxy api:80
    }
}
```

No serviço `caddy` do `compose.yml`:

```yaml
extra_hosts:
  - host.docker.internal:host-gateway
```

## 8. GitHub

Em cada repositório, abra `Settings > Webhooks > Add webhook`:

```text
Payload URL: https://SEU_API_DOMAIN/webhook/github
Content type: application/json
Secret: mesmo GITHUB_WEBHOOK_SECRET
SSL verification: habilitada
Evento: Workflow runs
Active: habilitado
```

O receptor aceita somente `workflow_run/completed` com sucesso na branch `main`, repositório autorizado e workflow autorizado.

## 9. Validação e logs

```bash
curl http://127.0.0.1:9000/health
sudo journalctl -u cobeb-webhook -f
```

O healthcheck esperado é `{"status": "ok", "queue_size": 0}`. Um `GET` em `/webhook/github` pode retornar `404`; o webhook aceita `POST`.

Em um deploy válido, o log deve mostrar:

```text
POST /webhook/github ... 202
Iniciando deploy repository=... commit=...
Deploy concluido repository=... commit=...
```

Use um `X-GitHub-Delivery` novo em cada teste manual; IDs repetidos são considerados duplicados.

## Diagnóstico rápido

- `217/USER`: confira `User=cobeb`, `Group=docker`, `id cobeb` e `getent group docker`.
- `unauthorized`: refaça o login no GHCR com token `read:packages` e SSO autorizado.
- `unknown flag: --quiet`: confirme `DOCKER_CONFIG`, `DOCKER_CLI_PLUGIN_EXTRA_DIRS` e `/usr/local/lib/docker/cli-plugins/docker-compose`.
- `502`: confira `systemctl status cobeb-webhook`, `curl http://127.0.0.1:9000/health` e `docker compose logs caddy --tail=100`.
- Deploy aceito sem conclusão: use `sudo journalctl -u cobeb-webhook --since '15 minutes ago' --no-pager`.

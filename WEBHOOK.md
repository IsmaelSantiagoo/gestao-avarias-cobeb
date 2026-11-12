# Webhook de deploy da Cobeb

O receptor executa `/opt/cobeb/deploy.sh` somente depois que um workflow que
publica uma imagem Docker terminar com sucesso na branch `main`. Isso evita
tentar baixar a nova imagem antes de ela existir.

O corpo é autenticado pela assinatura HMAC-SHA256 do GitHub. Também são
validados o repositório, a branch e o nome do workflow.

## 1. Preparar o serviço

Confira em `cobeb-webhook.service` se `User=ismael` corresponde ao usuário
que possui os arquivos e se ele pertence ao grupo `docker`. Depois:

```bash
sudo cp /opt/cobeb/cobeb-webhook.env.example /etc/cobeb-webhook.env
sudo chmod 600 /etc/cobeb-webhook.env
sudo editor /etc/cobeb-webhook.env

sudo cp /opt/cobeb/cobeb-webhook.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cobeb-webhook
sudo systemctl status cobeb-webhook
```

Gere o segredo com `openssl rand -hex 32`. O valor de
`GITHUB_WORKFLOW_NAMES` deve conter o campo `name:` exato de cada workflow
que gera e publica uma imagem. Separe vários nomes por vírgula.

Teste o serviço no host:

```bash
curl http://127.0.0.1:9000/health
journalctl -u cobeb-webhook -f
```

## 2. Publicar a rota com Caddy

O `Caddyfile` encaminha `/webhook/github` no domínio já configurado em
`API_DOMAIN` para o serviço do host. Após iniciar o serviço, aplique a
configuração:

```bash
cd /opt/cobeb
./deploy.sh
```

Garanta também que a porta TCP 9000 não esteja aberta no firewall externo. Ela
precisa ser acessível apenas pela rede Docker local; o tráfego público deve
entrar por HTTPS no Caddy.

## 3. Cadastrar em cada repositório do GitHub

Em **Settings > Webhooks > Add webhook**, configure:

- Payload URL: `https://SEU_API_DOMAIN/webhook/github`
- Content type: `application/json`
- Secret: o mesmo valor de `GITHUB_WEBHOOK_SECRET`
- SSL verification: habilitada
- Events: **Let me select individual events > Workflow runs**
- Active: marcado

Cadastre o webhook em cada repositório listado em `GITHUB_REPOSITORIES` que
deva iniciar um deploy.

Depois de salvar, confira **Recent Deliveries**. Eventos ignorados retornam HTTP
202 com `accepted: false`; um `workflow_run` válido retorna HTTP 202 com
`accepted: true` e entra na fila.

## Comportamento

- Somente `workflow_run/completed` com `conclusion=success`, branch `main`
  e workflow permitido dispara o deploy.
- Os deploys são serializados para impedir duas execuções simultâneas.
- O commit e o repositório ficam disponíveis ao script como
  `DEPLOY_COMMIT_SHA` e `DEPLOY_GITHUB_REPOSITORY`.
- O segredo do webhook não é repassado ao ambiente do `deploy.sh`.
- A deduplicação fica em memória. Por isso o `deploy.sh` deve continuar
  idempotente, como já ocorre com `docker compose pull/up`.

## Diagnóstico

```bash
systemctl status cobeb-webhook
journalctl -u cobeb-webhook --since today
docker compose logs caddy
```

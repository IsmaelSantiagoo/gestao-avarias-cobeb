# Instalando o Cobeb na VPS da empresa — passo a passo

Este guia leva você de uma VPS Linux vazia até o sistema funcionando com HTTPS e com atualização automática. Não presume experiência com servidores. Leia cada etapa inteira antes de executar.

## Visão geral: o que vai rodar na VPS

Tudo roda em containers Docker, controlados por um único arquivo (`compose.yml`):

| Peça | Para que serve |
|---|---|
| **Caddy** | Recebe o tráfego da internet e emite o certificado HTTPS sozinho |
| **frontend** | A tela do sistema (o que o usuário abre no navegador) |
| **api** | O servidor Laravel (regras de negócio) |
| **reverb** | Notificações em tempo real (WebSocket) |
| **scheduler / workers** | Tarefas agendadas e filas (importações, WhatsApp) |
| **cobeb-mysql** | Banco de dados do sistema |
| **evolution-api + postgres + redis** | Integração com WhatsApp |

As imagens do frontend e da API **não são construídas na VPS**: ela baixa imagens prontas do GitHub (`ghcr.io`). Por isso você precisa de um token do GitHub (Etapa 0).

Há também um pequeno serviço (`github_webhook.py`) que faz o servidor se atualizar sozinho quando uma versão nova é publicada.

---

## Etapa 0 — Reúna o que você precisa (antes de mexer na VPS)

**1. A VPS.** Recomendado: Ubuntu 22.04 ou 24.04, no mínimo 4 GB de RAM, 2 vCPU e 40 GB de disco. Anote o **IP público** e como entrar (usuário `root` e senha ou chave SSH, que a Hostinger mostra no painel).

**2. Quatro domínios (ou subdomínios).** Exemplo para a empresa `suaempresa.com.br`:

| Uso | Exemplo |
|---|---|
| Sistema | `cobeb.suaempresa.com.br` |
| API | `api.cobeb.suaempresa.com.br` |
| Tempo real | `reverb.cobeb.suaempresa.com.br` |
| Webhook do WhatsApp (Meta) | `whatsapp.cobeb.suaempresa.com.br` |

No painel onde o DNS do domínio é gerenciado, crie **4 registros do tipo A**, todos apontando para o **IP da VPS**. Domínios `.localhost` ou `.local` **não funcionam** em produção.

Confirme que propagou (no seu PC, PowerShell):

```powershell
nslookup cobeb.suaempresa.com.br
```

O IP que aparecer deve ser o da VPS. Se ainda não for, espere antes de continuar: o certificado HTTPS só é emitido depois que o DNS estiver certo.

**3. Token do GitHub para baixar as imagens.** No GitHub: *Settings > Developer settings > Personal access tokens > Tokens (classic) > Generate new token*. Marque **somente** `read:packages`. Se a organização usar SSO, clique em *Configure SSO > Authorize*. Guarde o token (ele só aparece uma vez). **Não envie esse token a ninguém, nem em chat.**

**4. Decisão sobre os dados.** Você vai começar com o banco vazio ou migrar um banco existente? Se for migrar, gere o dump (Etapa 7).

**5. Segurança antes de começar.** No painel da Hostinger, tire um **snapshot** da VPS vazia. Se algo der muito errado, você volta a este ponto.

---

## Etapa 1 — Entrar na VPS

No seu computador (PowerShell):

```powershell
ssh root@IP_DA_VPS
```

Na primeira vez, responda `yes`. Digite a senha. Você está dentro quando o prompt mudar para algo como `root@vps:~#`.

Atualize o sistema:

```bash
apt update && apt upgrade -y
```

---

## Etapa 2 — Instalar o Docker

```bash
curl -fsSL https://get.docker.com | sh
docker --version
docker compose version
```

Os dois últimos comandos devem mostrar versões. Se `docker compose version` falhar, o plugin Compose não foi instalado: rode `apt install -y docker-compose-plugin`.

---

## Etapa 3 — Criar o usuário `cobeb` e a pasta do sistema

Não rode o sistema como `root`. Crie um usuário dedicado:

```bash
adduser cobeb                      # defina uma senha forte; o resto pode dar Enter
usermod -aG docker cobeb           # permite usar o Docker
mkdir -p /opt/cobeb
chown -R cobeb:cobeb /opt/cobeb
```

(Opcional, recomendado) Copie sua chave SSH para o novo usuário, para entrar com `ssh cobeb@IP_DA_VPS`:

```powershell
ssh-copy-id cobeb@IP_DA_VPS
```

---

## Etapa 4 — Firewall

Libere só o necessário. **A porta 9000 NÃO deve ser aberta ao público.**

```bash
apt install -y ufw
ufw allow 22/tcp      # SSH (senão você se tranca para fora!)
ufw allow 80/tcp      # HTTP (necessário p/ emitir o certificado)
ufw allow 443/tcp     # HTTPS
ufw allow 443/udp     # HTTP/3
# Permite que o Caddy (container) fale com o webhook do host na porta 9000:
ufw allow from 172.16.0.0/12 to any port 9000 proto tcp
ufw enable
ufw status
```

Se o painel da Hostinger tiver um firewall próprio, mantenha lá também só 22, 80 e 443.

---

## Etapa 5 — Enviar os arquivos do projeto para a VPS

No seu computador, dentro da pasta do projeto (`gestao-avarias-cobeb`), no **PowerShell**:

```powershell
scp compose.yml Caddyfile deploy.sh backup.sh restore-mysql.sh github_webhook.py cobeb-webhook.service cobeb-webhook.env.example .env.production.example api.env.example evolution.env.example cobeb@IP_DA_VPS:/opt/cobeb/
```

**Não** envie `.git`, `caddy-root.crt` (é só de teste local) nem os `.env` locais: você vai criar os de produção no servidor.

Agora entre como `cobeb` e corrija o formato das quebras de linha (arquivos criados no Windows quebram no Linux com erros estranhos tipo `/bin/sh^M`):

```bash
ssh cobeb@IP_DA_VPS
cd /opt/cobeb
sed -i 's/\r$//' *.sh *.py *.yml Caddyfile *.example *.service
chmod 700 deploy.sh backup.sh restore-mysql.sh
chmod +x github_webhook.py
ls -la
```

---

## Etapa 6 — Configurar as variáveis (os "segredos")

### 6.1 Arquivo `.env`

```bash
cd /opt/cobeb
cp .env.production.example .env
nano .env
```

Preencha:

- Os 4 domínios e o `ACME_EMAIL`.
- Tags de imagem: `develop` acompanha a versão mais recente; se a empresa que forneceu o app indicar uma versão (ex.: `v1.0.0`), use-a.
- Gere cada chave/senha em um comando separado e cole no lugar:

```bash
openssl rand -hex 32     # REVERB_APP_KEY, EVOLUTION_MASTER_KEY, META_WEBHOOK_VERIFY_TOKEN (um valor diferente para cada)
openssl rand -hex 24     # COBEB_DB_PASSWORD, COBEB_DB_ROOT_PASSWORD, EVOLUTION_DB_PASSWORD
```

Use senhas **só com letras e números** (sem `@`, `#`, `$`), senão a conexão com o banco quebra. Salve no nano com `Ctrl+O`, `Enter`, `Ctrl+X`.

### 6.2 Arquivo `api.env` (configuração do Laravel)

```bash
cp api.env.example api.env
nano api.env
```

Pontos de atenção:

- `APP_ENV=production` e `APP_DEBUG=false`.
- `APP_KEY`: **instalação nova**, gere uma chave com o comando abaixo e cole o resultado (começa com `base64:`). **Migrando de uma instalação existente**, use a mesma `APP_KEY` dela, senão dados criptografados ficam ilegíveis.

```bash
docker run --rm ghcr.io/unify-solucoes-que-conectam/cobeb-gestao-reposicao-api:develop php artisan key:generate --show
```

(Este comando só funciona depois do login no GHCR, Etapa 6.4. Volte aqui se falhar.)

- `REVERB_APP_KEY` deve ser **igual** ao do `.env`. `REVERB_APP_ID` e `REVERB_APP_SECRET`: invente valores (`openssl rand -hex 16`). `REVERB_HOST` é o domínio de tempo real, `REVERB_PORT=443`, `REVERB_SCHEME=https`.
- `WHATSAPP_RATE_LIMIT_PER_MINUTE=10` (limite de envios).
- `AUTH_DEFAULT_SYS_PASSWORD`: defina uma senha forte para o usuário administrador inicial.
- As variáveis de banco (`DB_*`) o Compose sobrescreve, então não se preocupe com elas.

### 6.3 Arquivo `evolution.env`

```bash
cp evolution.env.example evolution.env
nano evolution.env
```

Normalmente só precisa conferir; as chaves principais vêm do `.env`.

### 6.4 Proteger os arquivos e logar no GitHub (GHCR)

```bash
chmod 600 .env api.env evolution.env

sudo install -d -o cobeb -g docker -m 700 /etc/docker
read -s GHCR_TOKEN        # cole o token e pressione Enter (nada aparece na tela)
printf '%s' "$GHCR_TOKEN" | sudo -u cobeb env HOME=/tmp DOCKER_CONFIG=/etc/docker /usr/bin/docker login ghcr.io -u SEU_USUARIO_GITHUB --password-stdin
unset GHCR_TOKEN
```

Deve aparecer `Login Succeeded`. Teste o download:

```bash
DOCKER_CONFIG=/etc/docker docker pull ghcr.io/unify-solucoes-que-conectam/cobeb-gestao-reposicao:develop
```

Se der `unauthorized`: o token não tem `read:packages`, o SSO não foi autorizado, ou o usuário não tem acesso ao pacote.

> Se `sudo` pedir senha e você não tiver, rode essas linhas como `root` (saia com `exit` e entre como `root`).

---

## Etapa 7 — (Opcional) Migrar um banco existente

Pule se for começar vazio (as tabelas são criadas automaticamente).

No computador de origem, gere o dump:

```bash
mysqldump --single-transaction --quick --no-tablespaces --result-file=cobeb-mysql.sql -h 127.0.0.1 -u SEU_USUARIO -p NOME_DO_BANCO
```

Envie e importe (a importação só funciona com banco **vazio** e nunca apaga nada):

```powershell
ssh cobeb@IP_DA_VPS "mkdir -p /opt/cobeb/import"
scp cobeb-mysql.sql cobeb@IP_DA_VPS:/opt/cobeb/import/
```

```bash
cd /opt/cobeb
docker compose up -d --wait cobeb-mysql
./restore-mysql.sh /opt/cobeb/import/cobeb-mysql.sql
```

Depois de conferir, apague o `.sql` do servidor.

---

## Etapa 8 — Primeiro deploy

```bash
cd /opt/cobeb
./deploy.sh
```

O script valida a configuração, baixa as imagens, faz backup, aplica as migrations, sobe tudo e roda testes internos. A primeira vez demora alguns minutos. Termina com `Deploy concluído...`.

Confira:

```bash
docker compose ps                       # todos "running"/"healthy"
docker compose logs --tail=100 caddy    # procure "certificate obtained"
```

No navegador (do seu computador):

- `https://api.cobeb.suaempresa.com.br/up` deve responder OK.
- `https://cobeb.suaempresa.com.br` deve abrir a tela de login.

**Acesso inicial:** o usuário de CPF `00000000000` vira administrador após a migration, com a senha definida em `AUTH_DEFAULT_SYS_PASSWORD`. **Troque a senha logo no primeiro acesso.**

**Se o certificado não sair:** quase sempre é DNS ainda não propagado ou porta 80/443 bloqueada. Corrija e rode `docker compose restart caddy`.

---

## Etapa 9 — Atualização automática (webhook do GitHub)

Faça isso **só depois** de o primeiro deploy funcionar. Se você prefere atualizar manualmente, pule esta etapa e veja a Etapa 12.

### 9.1 Configuração do webhook

```bash
sudo cp /opt/cobeb/cobeb-webhook.env.example /etc/cobeb-webhook.env
sudo chmod 600 /etc/cobeb-webhook.env
sudo nano /etc/cobeb-webhook.env
```

Defina `GITHUB_WEBHOOK_SECRET` com `openssl rand -hex 32` (guarde-o, você usará no GitHub). Mantenha `WEBHOOK_HOST=172.17.0.1` e confirme o nome do workflow em `GITHUB_WORKFLOW_NAMES` (deve ser idêntico ao campo `name:` do workflow nos repositórios).

### 9.2 Ativar o serviço

```bash
sudo cp /opt/cobeb/cobeb-webhook.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cobeb-webhook
sudo systemctl status cobeb-webhook --no-pager -l
curl http://172.17.0.1:9000/health
```

O esperado é `{"status": "ok", "queue_size": 0}`.

### 9.3 Cadastrar no GitHub (em **cada** repositório: frontend e API)

*Settings > Webhooks > Add webhook*:

- **Payload URL:** `https://api.cobeb.suaempresa.com.br/webhook/github`
- **Content type:** `application/json`
- **Secret:** o mesmo `GITHUB_WEBHOOK_SECRET`
- **Events:** *Let me select individual events* > **Workflow runs**
- **Active:** marcado

Depois de salvar, veja *Recent deliveries*: resposta `202` é sucesso.

---

## Etapa 10 — Configurar o WhatsApp

1. Entre no sistema como administrador e abra **Gerenciar > WhatsApp**.
2. **Conexão por QR Code:** escolha a filial, aba *QR Code*, crie a instância. No celular: *WhatsApp > Aparelhos conectados > Conectar aparelho*.
3. **API Oficial da Meta:** no painel da Meta, obtenha *Phone Number ID*, *WhatsApp Business Account ID* e um *token permanente*. Cadastre na tela da filial e copie de lá a **URL de callback** e o **token de verificação** para o painel da Meta.
4. Para um único número compartilhado por todas as filiais, ative **Instância geral** em uma filial (só uma pode ficar ativa).
5. Os templates (`avaria_approved`, `avaria_rejected`, `import_report`, `manual_notification`) precisam ser criados e aprovados na Meta e associados aos eventos no Cobeb.

---

## Etapa 11 — Backups (não pule)

O `deploy.sh` faz um backup a cada deploy em `/opt/cobeb/backups/`. Mas backup **só no mesmo servidor não protege** contra perda da VPS. Copie para outro lugar regularmente, do seu computador:

```powershell
scp -r cobeb@IP_DA_VPS:/opt/cobeb/backups C:\Backups\cobeb\
```

Para um backup diário automático no servidor (às 03:00), rode `crontab -e` como `cobeb` e adicione:

```
0 3 * * * cd /opt/cobeb && ./backup.sh >> /opt/cobeb/backups/cron.log 2>&1
```

Os backups incluem as senhas e o `APP_KEY`: **guarde-os em local protegido**. Teste uma restauração de vez em quando.

Para limpar backups antigos (mais de 14 dias) e não encher o disco:

```
30 3 * * * find /opt/cobeb/backups -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +
```

---

## Etapa 12 — Atualizar e voltar versão

- **Automático:** quando o GitHub publicar uma nova imagem na `main`, o webhook roda o `deploy.sh` sozinho (Etapa 9).
- **Manual:** `cd /opt/cobeb && ./deploy.sh`.
- **Voltar uma versão (rollback):** coloque a tag anterior em `FRONTEND_IMAGE_TAG` e `API_IMAGE_TAG` no `.env` e rode `./deploy.sh`. Nunca rode `migrate:rollback` em produção sem entender o impacto. Em caso de dúvida, restaure o backup.

---

## Comandos úteis do dia a dia

```bash
cd /opt/cobeb
docker compose ps                                  # estado dos serviços
docker compose logs -f --tail=100 api              # logs da API (Ctrl+C para sair)
docker compose logs --tail=100 caddy               # logs do HTTPS/proxy
docker compose restart api                         # reiniciar um serviço
sudo journalctl -u cobeb-webhook -f                # logs do webhook de deploy
df -h                                              # espaço em disco
```

## Problemas comuns

| Sintoma | Causa provável / solução |
|---|---|
| `/bin/sh^M: bad interpreter` | Quebras de linha do Windows. Rode `sed -i 's/\r$//' *.sh` (Etapa 5) |
| `unauthorized` ao baixar imagem | Refaça o login no GHCR; token sem `read:packages` ou SSO não autorizado |
| Navegador mostra erro de certificado | DNS não aponta para a VPS, ou portas 80/443 bloqueadas |
| `Defina COBEB_DB_...` ao subir | Variável vazia no `.env` |
| Erro de conexão com o banco | Senha com símbolos; use só letras e números |
| Webhook do GitHub retorna 502 | `systemctl status cobeb-webhook`; confirme `ufw allow from 172.16.0.0/12 to any port 9000` |
| Tela abre mas sem tempo real | `REVERB_*` do `api.env` e `.env` divergentes, ou DNS do domínio reverb |
| Webhook aceito mas nada atualiza | `sudo journalctl -u cobeb-webhook --since '15 minutes ago'` |

## Checklist final

- [ ] DNS dos 4 domínios aponta para a VPS
- [ ] Firewall com apenas 22, 80, 443 (9000 fechada ao público)
- [ ] `.env`, `api.env`, `evolution.env` com `chmod 600` e senhas fortes
- [ ] `APP_DEBUG=false`
- [ ] Login no sistema funciona e a senha do administrador inicial foi trocada
- [ ] Webhook do GitHub retorna `202`
- [ ] Backup copiado para fora do servidor

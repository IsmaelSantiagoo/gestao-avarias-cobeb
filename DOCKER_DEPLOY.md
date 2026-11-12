# Implantação do Cobeb com Evolution API

## 1. DNS e pré-requisitos

O servidor Linux precisa ter Docker Engine com o plugin Compose e as portas 80 e 443 livres. Crie quatro registros DNS apontando para o IP do servidor:

- aplicação (`APP_DOMAIN`);
- API Laravel (`API_DOMAIN`);
- WebSocket/Reverb (`REVERB_DOMAIN`);
- webhook da Meta (`EVOLUTION_DOMAIN`).

A API administrativa da Evolution não é publicada. No domínio da Evolution, o Caddy libera somente `/webhook/meta`.

## 2. Arquivos no servidor

Copie para `/opt/cobeb`:

- `compose.yml`;
- `compose.local.yml` apenas para testes locais;
- `Caddyfile`;
- `deploy.sh`, `backup.sh` e `restore-mysql.sh`;
- `.env.example` como `.env`;
- `api.env.example` como `api.env` e complete com as variáveis atuais do Laravel;
- `evolution.env.example` como `evolution.env`.

Não substitua o `APP_KEY` de uma instalação já existente. Gere chaves independentes para `EVOLUTION_MASTER_KEY` e `META_WEBHOOK_VERIFY_TOKEN`, por exemplo:

```bash
openssl rand -hex 32
```

Defina também `COBEB_DB_NAME`, `COBEB_DB_USER`, `COBEB_DB_PASSWORD` e `COBEB_DB_ROOT_PASSWORD`. Use senhas alfanuméricas fortes e diferentes para o usuário da aplicação e para o root. O MySQL principal roda somente na rede interna do Docker e não publica a porta 3306. Em seguida:

```bash
cd /opt/cobeb
chmod 700 deploy.sh backup.sh restore-mysql.sh
chmod 600 .env api.env evolution.env
```

O token usado em `docker login ghcr.io` precisa apenas de `read:packages` para baixar imagens privadas.

## 3. Migração do banco local

Antes do primeiro deploy, gere um dump do banco atualmente usado pela aplicação. Pelo MySQL instalado localmente:

```bash
mysqldump --single-transaction --quick --no-tablespaces --result-file=cobeb-mysql.sql -h 127.0.0.1 -u SEU_USUARIO -p NOME_DO_BANCO
```

Copie `cobeb-mysql.sql` para o servidor, por exemplo para `/opt/cobeb/import/`, e importe no container novo:

```bash
cd /opt/cobeb
./restore-mysql.sh /opt/cobeb/import/cobeb-mysql.sql
```

O script só aceita a restauração quando o banco está vazio. Ele nunca apaga nem substitui tabelas existentes. Depois de conferir a importação, remova o arquivo SQL do servidor ou mova-o para o armazenamento protegido de backups.

Se a instalação deve começar sem os dados locais, ignore essa etapa; o deploy criará as tabelas pelas migrations.

## 4. Primeiro deploy

Publique a mesma tag versionada nos repositórios do front-end e da API e coloque a tag em `FRONTEND_IMAGE_TAG` e `API_IMAGE_TAG`.

```bash
cd /opt/cobeb
./deploy.sh
```

O script valida o compose, cria o backup disponível, baixa imagens, aplica migrations, sobe os serviços, limpa caches e reinicia as filas. Confirme o resultado com:

```bash
docker compose ps
docker compose logs --tail=100 api evolution-api worker-whatsapp scheduler caddy
```

Depois da migration, o usuário de CPF `00000000000` passa a ser administrador. Acesse `Gerenciar > WhatsApp` para criar uma conexão por filial.

Para compartilhar o mesmo número, configure a instância em uma filial e ative **Instância geral**. Enquanto essa opção estiver ligada, todos os envios resolvem a mesma conexão; configurações individuais existentes são preservadas, mas ficam sem uso até a instância geral ser desativada. Apenas uma instância geral pode ficar ativa.

Mantenha `WHATSAPP_RATE_LIMIT_PER_MINUTE=10` no `api.env`. Os envios são espaçados em seis segundos por instância; quando a instância geral está ativa, todas as filiais compartilham esse mesmo limite. Depois de alterar o valor, recrie o worker com `docker compose up -d --force-recreate api worker-whatsapp scheduler`.

## 5. Configuração da API Oficial

No painel Meta, crie ou utilize um usuário do sistema com token permanente e obtenha:

- Phone Number ID;
- WhatsApp Business Account ID;
- token de acesso permanente.

Cadastre os três valores na tela da filial. Depois copie da própria tela a URL de callback e o token de verificação para o painel Meta. Crie e aguarde a aprovação dos templates e associe-os aos quatro eventos no Cobeb.

Os corpos dos templates devem usar as variáveis nesta ordem:

- `avaria_approved`: cliente, protocolo e filial;
- `avaria_rejected`: cliente, protocolo, motivo e filial;
- `import_report`: cabeçalho de documento, cliente e protocolo no corpo;
- `manual_notification`: mensagem informada pelo operador.

## 6. Conexão por QR Code

Escolha a filial, abra a aba `QR Code` e crie a instância. No telefone, use `WhatsApp > Aparelhos conectados > Conectar aparelho`. A tela consulta o status automaticamente e a sessão continua disponível após reinicializações.

## 7. Teste local

Para usar as portas locais sem Caddy, prepare os três arquivos de ambiente e execute:

```bash
docker compose -f compose.yml -f compose.local.yml up -d
```

Endereços locais:

- front-end: `http://localhost:3000`;
- API: `http://localhost:8000/up`;
- Evolution apenas para diagnóstico local: `http://localhost:8081`;
- Reverb: `ws://localhost:8080`.

PostgreSQL e Redis não possuem portas no host, inclusive no modo local.

O MySQL principal também não publica porta. Para importar um dump local antes do teste, execute `./restore-mysql.sh /caminho/para/cobeb-mysql.sql`; para um banco vazio, execute `docker compose -f compose.yml -f compose.local.yml run --rm api php artisan migrate --force`.

## 8. Atualização e rollback

Para atualizar, altere somente as tags em `.env` e rode `./deploy.sh`. Para rollback, restaure as tags anteriores e execute o mesmo script. Como as migrations deste recurso são aditivas, a versão anterior da aplicação continuará iniciando durante o primeiro rollback; não execute `migrate:rollback` automaticamente em produção.

O `backup.sh` salva um dump consistente do MySQL principal, PostgreSQL da Evolution, sessões, storage, certificados e arquivos de configuração. Copie o diretório gerado para um armazenamento externo ao servidor e valide periodicamente o procedimento de restauração. O `APP_KEY` fica preservado dentro do backup protegido; sem ele, os tokens criptografados não poderão ser recuperados.

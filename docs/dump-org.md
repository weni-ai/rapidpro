# dump_org — Exportação de dados de uma org

Management command Django que exporta todos os dados de uma única org como fixtures JSON, respeitando a ordem de dependência entre tabelas (FK-safe).

---

## Índice

- [Requisitos](#requisitos)
- [Sintaxe](#sintaxe)
- [Opções](#opções)
- [Grupos disponíveis](#grupos-disponíveis)
- [Exemplos de uso](#exemplos-de-uso)
  - [Dump completo](#dump-completo)
  - [Dump por grupo](#dump-por-grupo)
  - [Dump por modelo individual](#dump-por-modelo-individual)
  - [Dump em etapas (orgs grandes)](#dump-em-etapas-orgs-grandes)
  - [Retomar dump interrompido](#retomar-dump-interrompido)
  - [Upload automático ao concluir](#upload-automático-ao-concluir)
- [Restaurar](#restaurar)
- [Ferramentas auxiliares](#ferramentas-auxiliares)

---

## Requisitos

- Usuários referenciados em `created_by` / `modified_by` / `saved_by` precisam existir no banco de destino antes do `loaddata`.

---

## Sintaxe

```bash
python manage.py dump_org [org_id] [opções]
python manage.py dump_org --list-groups
```

---

## Opções

| Opção | Atalho | Padrão | Descrição |
|-------|--------|--------|-----------|
| `org_id` | — | obrigatório | ID da org a exportar |
| `--output <path>` | `-o` | `org_<id>_dump.json` | Arquivo de saída. Use `-` para stdout. Em modo `--split`, define o diretório. |
| `--split` | — | `false` | Um arquivo `.json` por modelo, nomeados `NNN_app.model.json` dentro do diretório `--output`. Gera também um `manifest.txt` com a ordem de carregamento. |
| `--groups <g> [g...]` | `-g` | todos | Exporta apenas os grupos informados. Ver [Grupos disponíveis](#grupos-disponíveis). |
| `--models <app.model> [...]` | `-m` | todos | Exporta apenas os modelos informados. Pode ser combinado com `--groups`. |
| `--exclude <app.model> [...]` | `-e` | — | Exclui modelos específicos do dump. |
| `--resume` | — | `false` | Retoma um `--split` interrompido. Requer `--split` e `--output`. Arquivos completos são pulados sem chamar `COUNT()`. |
| `--on-success <cmd>` | — | — | Comando shell a executar ao concluir com sucesso. Suporta os placeholders `{output}` e `{org_id}`. |
| `--indent <n>` | — | `2` | Indentação do JSON. Use `0` para saída compacta. |
| `--counts` | — | `false` | Mostra contagem de linhas por modelo na fase de scan. |
| `--list-groups` | — | — | Lista grupos e modelos disponíveis e sai. |

---

## Grupos disponíveis

```bash
python manage.py dump_org --list-groups
```

| Grupo | Modelos incluídos |
|-------|-------------------|
| `core` | `orgs.org`, `orgs.orgmembership`, `orgs.invitation`, `orgs.orgimport`, `orgs.export`, `orgs.itemcount`, `orgs.dailycount` |
| `contacts` | `contacts.contactfield`, `contacts.contactgroup`, `contacts.contact`, `contacts.contacturn`, `contacts.contactgroupcount`, `contacts.contactnote`, `contacts.contactfire`, `contacts.contactimport`, `contacts.contactimportbatch` |
| `channels` | `channels.channel`, `channels.channelevent`, `channels.syncevent`, `channels.channelcount` |
| `flows` | `flows.flowlabel`, `flows.flow`, `flows.flowrevision`, `flows.flowstart`, `flows.flowstartcount`, `flows.flowsession`, `flows.flowrun`, `flows.flowactivitycount`, `flows.flowresultcount` |
| `msgs` | `msgs.label`, `msgs.labelcount`, `msgs.optin`, `msgs.media`, `msgs.broadcast`, `msgs.broadcastmsgcount`, `msgs.msg` |
| `campaigns` | `campaigns.campaign`, `campaigns.campaignevent` |
| `tickets` | `tickets.topic`, `tickets.team`, `tickets.shortcut`, `tickets.ticket`, `tickets.ticketevent` |
| `triggers` | `triggers.trigger` |
| `templates` | `templates.template` |
| `api` | `api.resthook`, `api.resthooksubscriber`, `api.webhookevent`, `api.apitoken` |
| `globals` | `globals.global` |
| `schedules` | `schedules.schedule` |
| `classifiers` | `classifiers.classifier` |
| `ai` | `ai.llm` |
| `notifications` | `notifications.incident`, `notifications.notification` |
| `archives` | `archives.archive` |
| `airtime` | `airtime.airtimetransfer` |
| `ivr` | `ivr.call` |
| `request_logs` | `request_logs.httplog` |

---

## Exemplos de uso

### Dump completo

```bash
# Arquivo único → org_525_dump.json
python manage.py dump_org 525

# Com contagem de linhas por modelo
python manage.py dump_org 525 --counts

# Um arquivo por modelo → org_525_fixtures/
python manage.py dump_org 525 --split --output org_525_fixtures --counts

# Stdout (útil para comprimir em tempo real)
python manage.py dump_org 525 --output - | gzip > org_525.json.gz

# JSON compacto (menor em disco)
python manage.py dump_org 525 --indent 0
```

---

### Dump por grupo

```bash
# Apenas contacts
python manage.py dump_org 525 --groups contacts --split --output org_525_fixtures

# Contacts + flows + msgs
python manage.py dump_org 525 --groups contacts flows msgs --split --output org_525_fixtures

# Tudo menos flows.flowrun e msgs.msg (pesados)
python manage.py dump_org 525 --exclude flows.flowrun flows.flowsession msgs.msg \
  --split --output org_525_fixtures
```

---

### Dump por modelo individual

```bash
# Apenas msgs.msg
python manage.py dump_org 525 --models msgs.msg --output org_525_msgs.json

# Combinando grupos com modelos avulsos
python manage.py dump_org 525 --groups contacts --models flows.flow flows.flowrevision \
  --split --output org_525_fixtures
```

---

### Dump em etapas (orgs grandes)

Para orgs com centenas de milhões de registros, divida o dump em etapas.
Os arquivos gerados usam um **índice canônico fixo** (`NNN_app.model.json`), garantindo que não haja colisão entre etapas e que a ordem de `loaddata` seja sempre correta.

```bash
# Etapa 1: estrutura base (rápido)
python manage.py dump_org 525 \
  --groups core contacts channels campaigns templates globals schedules classifiers ai api \
  --exclude flows.flowrun flows.flowsession \
  --split --output org_525_fixtures --counts

# Etapa 2: flows sem runs (moderado)
python manage.py dump_org 525 \
  --groups flows \
  --exclude flows.flowrun flows.flowsession flows.flowactivitycount flows.flowresultcount \
  --split --output org_525_fixtures --counts

# Etapa 3: tickets, triggers, notificações (rápido)
python manage.py dump_org 525 \
  --groups tickets triggers notifications airtime ivr request_logs archives \
  --split --output org_525_fixtures --counts

# Etapa 4: mensagens (pesado — pode demorar horas)
python manage.py dump_org 525 --groups msgs \
  --split --output org_525_fixtures --counts

# Etapa 5: flow runs e sessions (pesado)
python manage.py dump_org 525 \
  --models flows.flowrun flows.flowsession flows.flowactivitycount flows.flowresultcount \
  --split --output org_525_fixtures --counts
```

Ao final de cada etapa o `manifest.txt` é atualizado automaticamente com todos os arquivos completos em ordem correta.

---

### Retomar dump interrompido

Funciona apenas em modo `--split`. Arquivos já completos são detectados lendo os últimos bytes (sem carregar o JSON inteiro) e pulados sem chamar `COUNT()` no banco.

```bash
# Retomar a etapa de msgs interrompida
python manage.py dump_org 525 --groups msgs \
  --split --output org_525_fixtures --resume

# Retomar um dump completo interrompido
python manage.py dump_org 525 \
  --split --output org_525_fixtures --resume
```

> **Atenção:** `--resume` requer `--output` explícito para localizar os arquivos existentes.

---

### Upload automático ao concluir

O argumento `--on-success` executa um comando shell após o dump ser concluído com sucesso. Dois placeholders estão disponíveis:

| Placeholder | Valor substituído |
|-------------|-------------------|
| `{output}` | Caminho do arquivo ou diretório gerado |
| `{org_id}` | ID da org |

```bash
# Dump + upload para S3 ao concluir
python manage.py dump_org 525 \
  --split --output org_525_fixtures \
  --on-success './scripts/upload_org_fixtures.sh {output} s3://meu-bucket'

# Com prefixo no bucket
python manage.py dump_org 525 \
  --split --output org_525_fixtures \
  --on-success './scripts/upload_org_fixtures.sh {output} s3://meu-bucket backups/orgs'

# Arquivo único + upload
python manage.py dump_org 525 --output org_525_dump.json \
  --on-success './scripts/upload_org_fixtures.sh {output} s3://meu-bucket'
```

Se o comando de `--on-success` retornar código de saída diferente de zero, um `CommandError` é lançado indicando que o dump foi bem-sucedido mas o pós-processamento falhou.

---

## Restaurar

O arquivo gerado é um fixture Django padrão compatível com `loaddata`.

### Arquivo único

```bash
python manage.py loaddata org_525_dump.json
```

### Arquivos split (via manifest)

O `manifest.txt` lista os arquivos na ordem correta de dependência de FK.

```bash
while read f; do
    echo "Loading $f..."
    python manage.py loaddata "org_525_fixtures/$f"
done < org_525_fixtures/manifest.txt
```

> **Atenção:** crie os usuários referenciados (`created_by`, `modified_by`, `saved_by`) no banco de destino antes de rodar o `loaddata`.

---

## Ferramentas auxiliares

### `scripts/upload_org_fixtures.sh`

Comprime o diretório de fixtures e envia para um bucket S3.

```bash
./scripts/upload_org_fixtures.sh <fixtures_dir> <s3_bucket> [s3_prefix]

# Exemplos
./scripts/upload_org_fixtures.sh org_525_fixtures s3://meu-bucket
./scripts/upload_org_fixtures.sh org_525_fixtures s3://meu-bucket backups/rapidpro

# Com perfil AWS específico
AWS_PROFILE=production ./scripts/upload_org_fixtures.sh org_525_fixtures s3://meu-bucket
```

O arquivo gerado tem o formato `org_525_fixtures_2026-02-26_14-30-00.tar.gz`.

### `scripts/fix_fixture_names.py`

Corrige nomes de arquivos que foram gerados com numeração sequencial antiga (antes do índice canônico ser introduzido), renomeando-os para os índices corretos e regenerando o `manifest.txt`.

```bash
# Visualizar o que seria renomeado (sem alterar nada)
python scripts/fix_fixture_names.py org_525_fixtures --dry-run

# Aplicar a correção
python scripts/fix_fixture_names.py org_525_fixtures
```

### `seed_dev_org`

Popula o banco com dados realistas para testar o `dump_org` localmente.

```bash
python manage.py seed_dev_org
python manage.py seed_dev_org --org-name "Teste" --contacts 100 --msgs 500
```

Ver [seed-dev-org.md](seed-dev-org.md) para detalhes.

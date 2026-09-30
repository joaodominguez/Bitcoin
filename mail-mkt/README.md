# mail-mkt

Pequena plataforma de **listas de email + campanhas** (fase 1: armazém de contactos).

## O que inclui

- Contactos em PostgreSQL (ou SQLite em local)
- Import CSV com streaming, dedupe e skip de suppressions
- Listas, pesquisa, paginação, suppression list
- **Envio via UniOne** (campanhas + worker em lotes ≤500)
- Link de unsubscribe assinado + webhook UniOne
- Login opcional (`DASHBOARD_PASSWORD`)

## Arranque rápido (local, SQLite)

```bash
cd mail-mkt
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app --port 8001
```

Abre http://127.0.0.1:8001 — sem password por omissão.

## Docker (Postgres)

```bash
cd mail-mkt
cp .env.example .env
# define DASHBOARD_PASSWORD
docker compose up -d --build
```

Dashboard: http://SERVIDOR:8001

## CSV

Colunas reconhecidas: `email`, `nome`/`name`, `tags`, `origem`/`source`, `consent`.

Exemplo em `examples/sample_contacts.csv`.

## Testes

```bash
cd mail-mkt
pytest -q
```

## UniOne

1. Cria API key no painel UniOne  
2. Verifica o domínio de envio (DKIM/SPF)  
3. Preenche no `.env`:

```bash
UNIONE_API_KEY=...
UNIONE_FROM_EMAIL=noreply@teu-dominio.com
UNIONE_FROM_NAME=A tua marca
UNIONE_BASE_URL=https://api.unione.io/en/transactional/api/v1
PUBLIC_BASE_URL=http://91.99.167.243:8001
```

4. `docker compose up -d --build` (sobe `web` + `worker` + `db`)  
5. Em **Campanhas**: envio de teste → depois enfileira a lista  

Webhook (opcional): `PUBLIC_BASE_URL/webhooks/unione`

## Próximas melhorias

- Métricas open/click na UI  
- Warm-up automático de volume  
- Templates guardados  

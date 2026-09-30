# mail-mkt

Pequena plataforma de **listas de email + campanhas** (fase 1: armazém de contactos).

## Fase 1 (agora)

- Guardar contactos em PostgreSQL (ou SQLite em local)
- Import CSV com streaming, dedupe e skip de suppressions
- Listas, pesquisa, paginação
- Suppression list + unsubscribe manual
- Login opcional (`DASHBOARD_PASSWORD`)

**Ainda sem envio de emails** — isso é a fase 2 (fila + SES/Resend).

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

## Próximas fases

2. Campanhas + worker Redis + provider SMTP/API  
3. Webhooks bounce/complaint + métricas  
4. DNS (SPF/DKIM/DMARC) e templates  

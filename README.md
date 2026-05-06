# banco-tg — Movimientos bancarios CL → Telegram

Sistema autónomo en una Mac que cada día a las 08:30 entra a Banco Falabella y
Banco de Chile, extrae los movimientos recientes, los deduplica contra una
base local SQLite, los clasifica con Claude Haiku 4.5 y manda los nuevos a
Diego por Telegram en un batch numerado para aprobar o corregir. Las
correcciones se aprenden como reglas para futuras clasificaciones.

Solo lectura. Nunca transferencias, pagos ni modificaciones del banco.

## Requisitos

- macOS
- Python 3.11+
- Bot de Telegram (creado con @BotFather)
- API key de Anthropic (https://console.anthropic.com)

## Instalación rápida

```bash
bash setup.sh
```

El script:
1. Crea `.venv` e instala las dependencias.
2. Instala Chromium para Playwright.
3. Inicializa la base de datos en `data/banco.db`.
4. Pide interactivamente `TG_BOT_TOKEN`, `TG_CHAT_ID`, `ANTHROPIC_API_KEY`.
5. Instala los `launchd` jobs (`daily` 08:30, `bot` permanente).

## Crear el bot de Telegram

1. Abre @BotFather y manda `/newbot`. Copia el token.
2. Manda `/start` a tu bot.
3. Abre `https://api.telegram.org/bot<TOKEN>/getUpdates` y copia tu `chat.id`.
4. Pega ambos en `setup.sh` cuando los pida.

## Configurar credenciales bancarias

Las credenciales **no van en `.env`**. Se configuran cifradas con Fernet
desde Telegram:

```
/cred falabella
   → te pide RUT (12345678-9) y luego Clave Internet (6 dígitos)
/cred bancochile
   → te pide RUT y Clave (≤8 caracteres)
```

Para borrar:

```
/forget falabella
```

## Comandos del bot

```
/start, /help    bienvenida y lista de comandos
/setup           guía paso a paso (siguiente banco a configurar)
/cred <banco>    wizard de credenciales
/forget <banco>  borra credenciales y sesión persistida
/cancel          aborta wizard activo
/test <banco>    scrape AHORA en background
/run             corre el daily completo
/status          bancos configurados, totales, pendientes, último error
/pending         re-envía pendientes no notificados
/last [N]        últimos N movimientos (default 10, máx 50)
```

## Responder al batch diario

Cuando llega el mensaje numerado:

```
Diego, movimientos nuevos para revisar:

1. 2026-05-04 · [falabella] JUMBO LAS CONDES
   -$23.450 → Alimentación/Supermercado (85%)
2. 2026-05-04 · [falabella] UBER TRIP
   -$4.500 → Transporte (90%)
```

Respondes:

- `1 ok` — acepta la sugerencia
- `2 supermercado` — corrige la categoría
- `2 alimentacion/restaurant` — corrige cat/subcat
- `3 ignorar` — marca como ignorado
- `todo ok` o `todos ok` — aprueba todo el batch

Cada corrección genera una regla automática para clasificar movimientos
similares en el futuro sin llamar a Haiku.

## 2FA

Si el banco pide código 2FA, el bot manda mensaje pidiéndolo y respondes:

```
otp 123456
```

Tienes 5 minutos. Si no llega, el flujo se aborta con `TwoFARequired`.

## Reportes

```sql
-- Gasto por categoría, mes actual
SELECT final_category, ROUND(SUM(amount)) AS total
FROM movements
WHERE status IN ('aprobado','corregido') AND amount < 0
  AND strftime('%Y-%m', date) = strftime('%Y-%m','now')
GROUP BY final_category ORDER BY total;

-- Personal vs empresa, mes actual
SELECT CASE WHEN final_category='Empresa' THEN 'empresa' ELSE 'personal' END
       AS bucket, ROUND(SUM(amount)) AS total
FROM movements
WHERE status IN ('aprobado','corregido') AND amount < 0
  AND strftime('%Y-%m', date) = strftime('%Y-%m','now')
GROUP BY bucket;

-- Pendientes acumulados
SELECT COUNT(*) FROM movements WHERE status='pendiente';
```

Corre con: `sqlite3 data/banco.db < query.sql`.

## Estructura

```
.
├── .env                                # secrets (no commitear)
├── data/                               # DB, master.key, state_*.json (no commitear)
├── logs/                               # daily.log, bot.log (no commitear)
├── requirements.txt
├── setup.sh
├── src/
│   ├── utils.py            # mask, hash, parseo CLP/fechas, logger
│   ├── db.py               # SQLite helpers
│   ├── secrets_store.py    # Fernet store/load/list/delete
│   ├── scraper.py          # orquestador Playwright
│   ├── classifier.py       # rule-first, Haiku-fallback
│   ├── telegram_notify.py  # send_message, send_daily_batch
│   ├── feedback.py         # parser de respuestas, aprende reglas
│   ├── run_daily.py        # entrada del cron (08:30)
│   └── bot.py              # wizard interactivo + long-poll
├── adapters/
│   ├── base.py             # excepciones y helpers Playwright
│   ├── falabella.py        # selectores reales y parseo Falabella
│   └── bancochile.py       # selectores reales y parseo BCh
└── scripts/
    ├── init_db.sql
    ├── com.diego.bancotg.daily.plist
    └── com.diego.bancotg.bot.plist
```

## launchd

```bash
# Estado
launchctl list | grep com.diego.bancotg

# Reiniciar manual
launchctl unload ~/Library/LaunchAgents/com.diego.bancotg.bot.plist
launchctl load   ~/Library/LaunchAgents/com.diego.bancotg.bot.plist

# Logs en vivo
tail -f logs/bot.log
tail -f logs/daily.log
```

## Variables de entorno (`.env`)

```
TG_BOT_TOKEN=<token de @BotFather>
TG_CHAT_ID=<tu chat.id>
ANTHROPIC_API_KEY=<key de console.anthropic.com>
DRY_RUN=false
HEADLESS=false      # ponlo true cuando confirmes que el flujo va sin intervención
LOG_LEVEL=INFO
DB_PATH=data/banco.db
```

NO agregues `BANK_*_RUT/PASS`. Las credenciales bancarias viven cifradas
en SQLite.

## Troubleshooting

- **`/test falabella` se cuelga pidiendo OTP**: el bot está corriendo
  pero no recibió tu `otp 123456`. Verifica `tail -f logs/bot.log`.
- **`No encontré tabla de movimientos`** o **`No encontré el botón Estado de cuenta`**:
  Falabella cambió su HTML. Inspecciona con `HEADLESS=false`.
  Quirks conocidos del sitio de Falabella (mayo 2026):
  - El botón "Estado de cuenta" es CSS-responsive: solo aparece con viewport ≥ 1920px.
    El viewport está configurado en `src/scraper.py` → `{"width": 1920, "height": 1080}`.
  - Muestra popups publicitarios con z-index alto que tapan el botón.
    Se ocultan vía JS en `_dismiss_popups()` (adapters/falabella.py).
  - La pestaña "Últimos Movimientos" es un `<label for="last-movements">`, no un `<a>`.
  - Las tablas están en el shadow DOM del componente Angular `<app-movements-table>`.
    Se leen con locators de Playwright (no con `document.querySelectorAll`).
- **`Banco de Chile mostró captcha`**: Si BCh pide captcha el flujo
  aborta. No lo bypaseamos. Espera unas horas y reintenta.
- **`Sesión persistida activa. Saltando login.`** + falla luego al
  navegar: borra `data/state_<banco>.json` y reintenta — la sesión
  expiró pero quedó cacheada.

## Reglas

- Solo lectura. Jamás transferencias, pagos ni modificaciones.
- Credenciales cifradas con Fernet, nunca en `.env`, nunca en logs.
- Si el banco pide captcha o un 2FA no resoluble con OTP pegable: aborta.

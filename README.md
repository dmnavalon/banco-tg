# banco-tg — Movimientos bancarios CL → Telegram

Sistema híbrido (Mac local + Railway cloud) que cada día entra a Banco
Falabella y Banco de Chile, extrae los movimientos recientes, los deduplica
contra **Firestore**, los clasifica con **Claude Haiku 4.5** y manda los
nuevos a Diego por Telegram en tarjetas con foto del detalle, botones de
aprobar/corregir/ignorar y batches paginados de a 5. Las correcciones son
texto libre que se interpreta con LLM y se aprenden como reglas.

Cada movimiento aprobado se pega además en un **Google Sheet** con 13
columnas (Fecha, Día/Mes/Año numéricos para fórmulas, Banco, Persona,
Descripción, Monto, Tipo, Saldo, Categoría, Subcategoría).

Solo lectura. Nunca transferencias, pagos ni modificaciones del banco.

## Arquitectura

| Pieza | Dónde corre | Por qué |
|---|---|---|
| Bot Telegram (`src/bot.py`, long-poll permanente) | **Railway** (`railway.toml` + `Dockerfile`) | 24/7 sin depender de la Mac. Servicio único: solo `bot`, sin daily-cron en Railway (eso pelea por `getUpdates` y rompe todo). |
| Daily de scrape (`src/run_daily.py`) | **Mac local** vía `launchd` (`com.diego.bancotg.daily.plist`, 08:00) | Necesita las cookies persistidas en `data/state_<banco>.json` para evitar 2FA en cada corrida. |
| Base de datos | **Firestore** (project `control-gastos-c53b6`) | Compartida entre Mac (escribe desde el daily) y Railway (lee desde el bot). El SQLite legacy `data/banco.db` está archivado y NO se usa. |
| Hoja de cálculo de salida | **Google Sheets** (id `1bcH0Hu...Pb6XM`, hoja `Movimientos`) | Service account `gastos@gastos-495422.iam.gserviceaccount.com`. La hoja debe estar compartida como Editor. |
| Clasificador | **Claude Haiku 4.5** (Anthropic API) | Toolcall con taxonomía como JSON Schema; rule-first via Firestore `rules` collection y fallback Haiku. |

## Requisitos

- macOS (para el daily)
- Cuenta de Railway con CLI (`brew install railway` o npm)
- Python 3.11+ (para correr scripts ad-hoc localmente)
- Bot de Telegram (creado con @BotFather)
- API key de Anthropic
- Service accounts:
  - Firebase Admin SDK (Firestore) → `data/firebase_service_account.json`
  - Google Sheets API → `data/gsheet_service_account.json`

## Instalación

### Mac (daily)

```bash
bash setup.sh
```

El script instala dependencias, Chromium para Playwright, registra el plist
del daily en `~/Library/LaunchAgents/` y carga el daemon. Para el bot
**no** uses el plist local — corre en Railway.

### Railway (bot)

1. Conecta el repo `dmnavalon/banco-tg` a un proyecto Railway.
2. Crea **un único servicio** llamado `bot` (Dockerfile auto-detectado).
3. Variables de entorno requeridas en Railway → tab **Variables**:
   - `TG_BOT_TOKEN`, `TG_CHAT_ID`
   - `ANTHROPIC_API_KEY`
   - `FIREBASE_KEY_JSON` (todo el JSON pegado como string)
   - `GSHEET_KEY_JSON` (todo el JSON pegado como string — **no** uses `GSHEET_KEY_PATH` en cloud)
4. Deploy: `railway up --service bot` desde `Gestión de Gastos/`.

⚠️ **No crees un segundo servicio "daily-cron" en Railway** que herede el
mismo `Dockerfile`: heredaría el `CMD ["python","-m","src.bot"]` y arrancaría
un segundo bot que pelearía por el long-poll de Telegram (409 Conflict en
loop). El daily vive en la Mac.

## Crear el bot de Telegram

1. Abre @BotFather y manda `/newbot`. Copia el token.
2. Manda `/start` a tu bot.
3. Abre `https://api.telegram.org/bot<TOKEN>/getUpdates` y copia tu `chat.id`.
4. Pega ambos en `.env` local y en Railway → Variables.

## Configurar credenciales bancarias

Las credenciales **no van en `.env`**. Se configuran cifradas con Fernet
desde Telegram, hablando con el bot:

```
/cred falabella
   → te pide RUT (12345678-9) y luego Clave Internet (6 dígitos)
/cred bancochile
   → te pide RUT y Clave (≤8 caracteres)
```

Quedan cifradas en Firestore `credentials/<banco>`. Para borrar:

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
/pending         re-envía pendientes no notificados (de a 5)
/next            siguientes 5 tarjetas si quedaron en cola
/last [N]        últimos N movimientos (default 10, máx 50)
```

## Responder a las tarjetas

Cuando llega una tarjeta, tienes 3 caminos:

### Botones (interactivo)
- **✅ Aprobar** — acepta la categoría sugerida, marca `aprobado` y pega en GSheet.
- **🚫 Ignorar** — descarta el movimiento (no aparece en reportes).
- **✏️ Corregir** — abre un prompt **Force Reply**: el campo de texto cita
  ese movimiento específico. Aunque tengas 5 tarjetas activas, no hay
  ambigüedad. Escribes texto libre (ej. "es del super", "esto va a Bodemall").
  El LLM re-clasifica con la pista y reenvía una tarjeta nueva con la
  sugerencia para confirmar/re-corregir.

### Texto plano (estilo viejo)
- `1 ok` — acepta la sugerencia del movimiento #1.
- `2 ignorar` — ignora el #2.
- `3 esto va a Bodemall` — texto libre, el LLM re-clasifica el #3 igual que el botón.
- `todo ok` o `todos ok` — aprueba todos los visibles.

### Paginación
Si llegan más de 5 movimientos nuevos, el bot manda los primeros 5 y avisa
"📦 Te mostré 5 de 30. /next para los próximos 5". `/next` avanza la cola
hasta vaciarla.

## Taxonomía y reglas de clasificación

La taxonomía está en `src/classifier.py:TAXONOMY`. Categorías especiales:

- **Educación / Educación Hijos** — para jardines y colegios privados de los hijos. Hay una regla preinstalada: "LEONCITO ESPANOL" → `Educación / Educación Hijos`.
- **Gastos por rendir** — gastos hechos con tarjeta personal que se rinden a un tercero. Subcategorías predefinidas: `Bodemall`, `Faind`, `Amplia`, `Papá`, `Mamá`, `Hermano`, `Hermana`. Si en tu hint mencionas otro nombre, el LLM lo acepta tal cual (categoría extensible).
- **Transferencias internas / Pago tarjeta mismo titular** — se aplica automático a pagos de CMR.

### Aprendizaje automático
Cuando corriges un movimiento, el bot intenta extraer un patrón
significativo de la descripción (ignorando stopwords como `COMPRA`,
`PAGO`, `WEBPAY`, `MERPAGO`, etc.) y crea una regla `contains` en Firestore
`rules`. La próxima vez que aparezca una descripción que matchee, se
clasifica directo sin llamar al LLM.

### Subcategorías nuevas
Si tu corrección sugiere una sub-categoría que **no** está en la taxonomía
(ej. "ponlo como Pádel competencia"), el LLM la propone tal cual y marca
`requiere_revision=true` con una pregunta sugerida en la tarjeta. Tú
confirmas con ✅ o re-corriges. El sistema NO mapea silenciosamente a la
"más parecida" — eso oculta tu intención.

## 2FA

Si el banco pide código 2FA, el bot manda mensaje pidiéndolo y respondes:

```
otp 123456
```

Tienes 5 minutos. Si no llega, el flujo se aborta con `TwoFARequired`.

## Reportes

La fuente de verdad es el Google Sheet. Día/Mes/Año son numéricos para
hacer fórmulas tipo `SUMIFS` o tablas dinámicas.

Para queries directas a Firestore (sin pasar por sheet):

```python
import firebase_admin
from firebase_admin import credentials, firestore
from collections import Counter
firebase_admin.initialize_app(credentials.Certificate('data/firebase_service_account.json'))
db = firestore.client()

# Pendientes
print("Pendientes:", len([d for d in db.collection('movements').get() if d.to_dict().get('status') == 'pendiente']))

# Gasto total del mes en una categoría
total = sum(
    abs(d.to_dict().get('amount', 0))
    for d in db.collection('movements').where('status', '==', 'aprobado').get()
    if d.to_dict().get('final_category') == 'Hogar y alimentación'
    and d.to_dict().get('date', '').startswith('2026-05')
)
print(f"Hogar y alimentación 2026-05: ${total:,.0f}")
```

## Estructura

```
.
├── .env                                # secrets locales (no commitear)
├── data/                               # service-accounts, master.key, state_*.json (no commitear)
├── logs/                               # daily.log, daily.stderr.log (Mac)
├── Dockerfile                          # imagen de Railway (bot)
├── railway.toml                        # configuración Railway
├── requirements.txt
├── setup.sh                            # bootstrap del daily local
├── src/
│   ├── utils.py            # mask, hash, parseo CLP/fechas, logger, normalize (sin tildes mayúsculas)
│   ├── db.py               # Firestore helpers (movements, rules, config, credentials, pending_corrections)
│   ├── secrets_store.py    # Fernet store/load/list/delete contra Firestore
│   ├── scraper.py          # orquestador Playwright (Falabella + BCh)
│   ├── classifier.py       # rule-first → Haiku con tool_use; soporta hint del usuario
│   ├── telegram_notify.py  # sendMessage, sendPhoto, force_reply, batch de 5, /next
│   ├── feedback.py         # parser de "1 ok"/"2 supermercado"/"todo ok"; usa classifier con hint
│   ├── gsheet.py           # 13 columnas (Día/Mes/Año numéricos, Persona, Cat/Subcat separadas)
│   ├── run_daily.py        # entrada del cron (08:00 hora Chile)
│   └── bot.py              # long-poll, callbacks, force_reply, wizards
├── adapters/
│   ├── base.py             # excepciones (LoginFailed, ScraperBroken, TwoFARequired, CaptchaPresent) y helpers
│   ├── falabella.py        # adapter Falabella: paginación + screenshot del modal de detalle
│   └── bancochile.py       # adapter BCh: tabla bch-table con clases cdk-column-* + paginación Material
└── scripts/
    ├── com.diego.bancotg.daily.plist   # cron del daily local (08:00)
    ├── com.diego.bancotg.bot.plist     # ⚠️ DEPRECATED, no cargar — el bot vive en Railway
    └── migrate_to_*.py                 # scripts one-shot de migraciones pasadas
```

## launchd (solo daily, en la Mac)

```bash
# Estado
launchctl list | grep com.diego.bancotg

# Recargar daily
launchctl unload ~/Library/LaunchAgents/com.diego.bancotg.daily.plist
launchctl load   ~/Library/LaunchAgents/com.diego.bancotg.daily.plist

# Logs en vivo
tail -f logs/daily.stdout.log
```

El plist `com.diego.bancotg.bot.plist` está renombrado a `.disabled-2026-05-07`
y NO debe cargarse — el bot vive solo en Railway.

## Variables de entorno (`.env` local)

```
TG_BOT_TOKEN=<token de @BotFather>
TG_CHAT_ID=<tu chat.id>
ANTHROPIC_API_KEY=<key de console.anthropic.com>
DRY_RUN=false
HEADLESS=false      # ponlo true cuando confirmes que el flujo va sin intervención
LOG_LEVEL=INFO
GSHEET_KEY_PATH=data/gsheet_service_account.json
```

NO agregues `BANK_*_RUT/PASS`. Las credenciales bancarias viven cifradas
en Firestore `credentials/<banco>`.

## Troubleshooting

- **`409 Conflict: terminated by other getUpdates request`** en logs del bot:
  Hay otra instancia del bot peleando por el long-poll. Causas frecuentes:
  - Otro servicio en Railway con el mismo Dockerfile (revisa `railway status`, el único servicio debe ser `bot`).
  - Bot local cargado en launchd (deshabilítalo: el plist `com.diego.bancotg.bot.plist` debe estar renombrado a `.disabled`).
  - Otro deploy histórico en otra plataforma (Render, Fly, otra cuenta) usando el mismo `TG_BOT_TOKEN`. Solución nuclear: rotar el token con @BotFather → `/mybots` → API Token → Revoke. Eso desconecta cualquier zombie.

- **`/test falabella` se cuelga pidiendo OTP**: el bot está corriendo
  pero no recibió tu `otp 123456`. Verifica los logs de Railway con `railway logs --service bot`.

- **`No encontré tabla de movimientos`** o **`No encontré el botón Estado de cuenta`**:
  El banco cambió su HTML. Inspecciona con `HEADLESS=false`. Quirks
  conocidos (mayo 2026):

  **Falabella:**
  - El botón "Estado de cuenta" es CSS-responsive: solo aparece con viewport ≥ 1920px. Configurado en `src/scraper.py` → `{"width": 1920, "height": 1080}`.
  - Popups publicitarios con z-index alto tapan el botón. `_dismiss_popups()` los oculta vía JS, pero **solo dentro de overlay/modal/popup/dialog explícitos**. Selectores genéricos como `button[aria-label*="cerrar" i]` matchean el botón "Cerrar sesión" del header — usar siempre selector contextualizado.
  - La pestaña "Últimos Movimientos" es un `<label for="last-movements">`, no un `<a>`.
  - Las tablas están en el shadow DOM del componente Angular `<app-movements-table>`. Se leen con locators de Playwright (no con `document.querySelectorAll`).
  - Hay DOS tablas: "Pendientes de confirmación" (sin fecha asentada, IGNORAR) y "Fecha de compras" (movimientos confirmados, USAR). `_select_confirmed_table()` filtra por el header `<th>` — si dice "pendiente", se salta.
  - Los movimientos detalle se abren clickeando la fila — modal con clase `modal-content`. Se captura screenshot del modal y se manda en la tarjeta de Telegram con `sendPhoto`.

  **Banco de Chile:**
  - Tabla con clases Angular Material/CDK: `table.bch-table`, columnas `td.cdk-column-fechaContable`, `cdk-column-descripcion`, `cdk-column-cargo`, `cdk-column-abono`. NO uses el selector genérico `"table"` — matchea el header u otros elementos antes que la tabla real.
  - Filas vacías de animación con clase `table-collapse-row` — saltarlas con `tr.bch-row:not(.table-collapse-row)`.
  - Cargo y abono van en columnas separadas; una de las dos viene vacía. El parser detecta cuál tiene monto y asigna signo.
  - Paginación con botones Material: `button.mat-paginator-navigation-next`. Iterar hasta que esté `disabled`.
  - **Sesión persistida con doble validación**: la cookie del portal home (`portalpersonas.bancochile.cl`) puede estar vigente pero la cookie de la pantalla de movimientos puede haber expirado por separado. Síntoma: `login()` reporta "Sesión persistida activa" pero después `fetch_movements()` falla porque la URL terminó en `login.portales.bancochile.cl/authorize?...` (Auth0 OAuth2 redirect). Por eso `login()` ahora navega a `MOVEMENTS_URL` para verificar el segundo nivel de auth — si rebota a Auth0, hace login fresh con RUT/clave.

- **`Banco de Chile mostró captcha`**: Si BCh pide captcha el flujo
  aborta. No lo bypaseamos. Espera unas horas y reintenta.

- **`Sesión persistida activa. Saltando login.`** + falla luego al
  navegar: borra `data/state_<banco>.json` y reintenta — la sesión
  expiró pero quedó cacheada.

- **GSheet falla silenciosamente**: ya no debería — `gsheet.append_movement()` ahora hace `log.exception` con stack trace y `raise`. El caller (`feedback.py:_try_append`, `bot.py`) captura y avisa al usuario en TG con `⚠️ GSheet falló`. Causas frecuentes: hoja no compartida con `gastos@gastos-495422.iam.gserviceaccount.com`, cuota agotada, header desincronizado con el código (debe coincidir con `gsheet.SHEET_HEADER`).

- **`DeadlineExceeded: 504 Deadline Exceeded`** en operaciones Firestore durante o
  después de un scrape: gRPC se ensucia con FDs heredados cuando Playwright
  forkea procesos hijos (Chromium). Mitigación aplicada en `src/db.py`:
  - Env vars `GRPC_ENABLE_FORK_SUPPORT=1` y `GRPC_POLL_STRATEGY=poll` se setean
    al top del módulo, antes del import de `firebase_admin` (gRPC inicializa
    estructuras al load-time según ellas).
  - Helper `_with_retry()` envuelve `insert_movement` y `update_classification`
    con backoff exponencial (3 intentos, ~1.5s/3s/6s) ante errores gRPC
    transient (`DeadlineExceeded`, `ServiceUnavailable`, `Aborted`,
    `InternalServerError`, `RetryError`).
  - Si vuelve a aparecer en operaciones distintas a esas, considerá envolver
    también esa operación con `_with_retry`.

## Reglas

- Solo lectura. Jamás transferencias, pagos ni modificaciones.
- Credenciales cifradas con Fernet, nunca en `.env`, nunca en logs.
- Si el banco pide captcha o un 2FA no resoluble con OTP pegable: aborta.
- El daily NUNCA debe correr en Railway si el bot también está ahí — heredan el `CMD` del Dockerfile y se atascan en `getUpdates` 409 sin scrapear nunca.

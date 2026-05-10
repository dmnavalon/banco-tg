# HANDOFF — banco-tg / Control de Gastos

> Documento de traspaso para que un agente nuevo retome este proyecto sin contexto previo.
> Última actualización: **2026-05-08**.
> Owner: Diego Martínez (`dmnavalon@gmail.com`, Telegram chat_id `8676856542`).

---

## 1. Qué es esto en 30 segundos

Sistema personal de control de gastos para Diego (Chile). Cada mañana scrapea tarjetas de banco, clasifica con LLM, manda tarjetas a Telegram para aprobar/corregir/ignorar, y pega los aprobados en una hoja de Google Sheets que alimenta un dashboard.

```
[Daily 08:00 en Mac]
    ↓ Playwright scrape
  Falabella + BCh
    ↓ classifier (Haiku)
  Firestore (movements)
    ↓ telegram_notify
  Tarjetas en Telegram (lotes de 5, /next para más)
    ↓ Diego responde (botones o texto)
  feedback / bot callbacks
    ↓ upsert
  Google Sheet (dashboard)
```

---

## 2. Topología (CRÍTICO)

| Componente | Dónde corre | Por qué |
|---|---|---|
| **Bot Telegram** (`src/bot.py`, long-poll) | Railway 24/7 (`railway.toml`, `Dockerfile`) | Siempre on. Procesa callbacks, force_reply, GSheet upsert. |
| **Daily de scrape** (`src/run_daily.py`, 08:00) | **Mac local** vía launchd (`com.diego.bancotg.daily.plist`) | Necesita las cookies de `state_<banco>.json` persistidas en disco; BCh tiene anti-bot que detecta IPs cloud. |
| **Firestore** (project `control-gastos-c53b6`) | Cloud (Firebase) | DB compartida Mac↔Railway. SQLite legacy (`data/banco.db.legacy-...`) NO se usa. |
| **Google Sheet** (id `1bcH0Hu2_z_yVxZY3BuTkGaDzlsQQZYRCD1ayY-Pb6XM`, tab `Movimientos`) | Cloud (Google) | Salida final + dashboard manual del usuario. |
| **Anthropic Haiku 4.5** | API | Clasificador de movimientos. |

⚠️ **NO crear un servicio "daily-cron" en Railway** que herede el `Dockerfile`: hereda `CMD ["python","-m","src.bot"]` y arranca un segundo bot que pelea por `getUpdates` (409 Conflict en loop). El daily vive solo en la Mac.

⚠️ **Bot de Telegram**: `@mis_gastos_diego_bot` (id 8553552770). El token original (`@controldegastos2_bot` id 8646118361) fue rotado el 2026-05-07 tras un incidente — no usarlo más.

---

## 3. Variables de entorno

### Local (`.env` en la raíz del proyecto)

```bash
TG_BOT_TOKEN=<token de @BotFather>
TG_CHAT_ID=8676856542
ANTHROPIC_API_KEY=<key>
DRY_RUN=false
HEADLESS=false      # local puede ser false para debug; Railway usa true via Dockerfile
LOG_LEVEL=INFO
GSHEET_KEY_PATH=data/gsheet_service_account.json
```

### Railway (tab Variables del servicio `bot`)

Mismas que .env **excepto** que en lugar de `GSHEET_KEY_PATH` se usa el JSON pegado entero como string:

```
TG_BOT_TOKEN=...
TG_CHAT_ID=8676856542
ANTHROPIC_API_KEY=...
GSHEET_KEY_JSON=<JSON pegado entero>
FIREBASE_KEY_JSON=<JSON pegado entero>
LOG_LEVEL=INFO
```

### Service accounts en `data/` (no commitear)

- `data/firebase_service_account.json` — Firebase Admin SDK (project `control-gastos-c53b6`).
- `data/gsheet_service_account.json` — Google Sheets API (`gastos@gastos-495422.iam.gserviceaccount.com`). La hoja debe estar compartida con ese email como Editor.
- `data/.master.key` — Fernet key para cifrar credenciales bancarias.

### Estado runtime en `data/`

- `data/state_falabella.json` — cookies de Playwright para Falabella (sesión persistida local).
- `data/state_bancochile.json` — idem BCh, también sincronizado a Firestore (collection `browser_state`) para que Railway lo use.
- `data/.tg_offset.legacy-...` — offset de Telegram viejo, ya no se usa (vive en Firestore `config/tg_offset`).

---

## 4. Flujo end-to-end (camino feliz)

```
1. Mac 08:00 — launchd dispara `python -m src.run_daily`.
2. run_daily.main() lee bancos configurados de Firestore (collection `credentials`).
3. Por cada banco, llama scraper.run_for_bank(...) que:
   - Lanza Playwright (HEADLESS=true).
   - Carga state desde Firestore (browser_state) o archivo local.
   - adapter.login(rut, password) — Falabella ok en headless, BCh requiere state válido.
   - adapter.fetch_movements() — itera todas las páginas, para cada fila:
     * Captura screenshot del modal de detalle (solo Falabella).
     * Devuelve dict con {date, description, amount, persona, cuotas_actual,
       cuotas_total, cuota_monto, screenshot_bytes}.
   - Por cada mov: db.insert_movement(...) — dedupe por mov_id (hash).
   - Sube state actualizado a Firestore (sync para Railway).
4. run_daily clasifica cada mov nuevo:
   - classifier.classify(desc, amount) — primero busca regla en Firestore
     (collection `rules`); si no hay match, llama a Haiku con el TAXONOMY.
   - db.update_classification(...) persiste suggested_category, etc.
5. telegram_notify.send_daily_batch(movs) — manda los primeros 5 con botones
   [Aprobar / Corregir / Ignorar] + foto del modal si está disponible.
   El resto va a config/last_batch_remaining para que /next los traiga.
6. Diego responde:
   a. Click ✅ Aprobar → callback `a:<mov_id>` → status='aprobado' →
      gsheet.upsert_movement(...) → tarjeta queda con
      "✅ Aprobado: cat/sub" + botón [✏️ Corregir nuevamente].
   b. Click ✏️ Corregir → callback `c:<mov_id>` → telegram_notify.send_correction_prompt
      manda mensaje con force_reply citando el mov → Diego escribe texto libre →
      classifier.classify_with_hint(...) → tarjeta nueva con la nueva sugerencia
      → Diego aprueba → upsert_movement (UPDATE de la fila existente, no append).
   c. Click 🚫 Ignorar → callback `i:<mov_id>` → telegram_notify.send_ignore_prompt
      manda force_reply pidiendo razón → Diego escribe razón (o "skip" / "cancelar")
      → status='ignorado' con ignore_reason → tarjeta queda con badge + botón
      [✏️ Corregir nuevamente].
   d. Texto plano "1 ok", "2 esto va a Bodemall", "todo ok" → feedback.apply(...)
      hace lo mismo pero por número de la tarjeta visible en el batch actual.
```

---

## 5. Modelo Firestore

### Collection `movements` (la principal)

```
{
  id: str,                     # mov_id, hash de (date+amount+desc+bank+account)
  date: str,                   # ISO YYYY-MM-DD
  description: str,            # ej. "COMPRA UBER (01/01)" — sufijo cuotas si aplica
  amount: float,               # monto total (negativo si es gasto)
  movement_type: str,          # "cargo" o "abono"
  account: str,                # "falabella" / "bancochile"
  bank: str,
  raw_blob: str,               # JSON serializado de lo que devolvió el adapter

  # Clasificación (sugerida por LLM, final tras aprobar)
  suggested_category: str|null,
  suggested_subcategory: str|null,
  confidence: float,
  classifier_source: str,      # "rule" / "agent" / "fallback"
  comercio: str|null,          # extraído por LLM
  tipo: str,                   # "Ingreso" / "Egreso" / "Transferencia interna"
  requiere_revision: bool,
  pregunta_sugerida: str|null,

  # Metadata bancaria
  persona: str|null,           # "Titular" / "Adicional" / nombre del adicional
  cuotas_actual: int|null,     # ej. 2 si "02/12"
  cuotas_total: int|null,      # ej. 12
  cuota_monto: float|null,     # mensualidad (negativo)

  # Decisión de Diego
  status: str,                 # "pendiente" / "aprobado" / "corregido" / "ignorado"
  final_category: str|null,
  final_subcategory: str|null,
  decided_by: str|null,        # chat_id de Telegram
  decided_at: str|null,
  ignore_reason: str|null,     # solo si status="ignorado"

  # Telegram
  notified_at: str|null,       # primera vez que se mandó la tarjeta
  tg_photo_file_id: str|null,  # para reusar la foto sin re-uploadear

  inserted_at: str,
}
```

### Otras collections

- **`credentials`** — `{bank}` doc con `blob` cifrado Fernet de RUT+clave.
- **`rules`** — `{auto-id}` doc con `{match_type: "contains"|"exact", pattern, category, subcategory, hits, created_at, last_used_at}`. Usado por `classifier.find_rule_for()`.
- **`config`** — k/v store. Docs importantes:
  - `tg_offset`: long-poll offset del bot.
  - `last_batch_payload`: CSV de mov_ids del batch actual visible en TG.
  - `last_batch_ids`: idem (legacy).
  - `last_batch_remaining`: CSV de mov_ids para el `/next`.
- **`pending_user_actions`** — mapping `{prompt_message_id} → {mov_id, action: "correct"|"ignore", chat_id, original_card_message_id}`. Para resolver Force Reply.
- **`pending_corrections`** — legacy, sigue como fallback en `get_pending_user_action`.
- **`browser_state`** — `{bank}` doc con `state_json` (cookies de Playwright). Sincroniza Mac↔Railway.
- **`telegram_log`** — historial de mensajes in/out.
- **`errors`** — record de excepciones del daily/bot.
- **`wizard_state`** — estado por chat_id del wizard `/cred`.

---

## 6. Modelo Google Sheet (24 columnas)

```
Pos | Columna           | Quién escribe | Notas
----|-------------------|---------------|------
1   | Fecha             | bot           | DD/MM/YYYY string
2   | Día               | bot           | número (para fórmulas)
3   | Mes               | bot           | número 1-12
4   | Año               | bot           | número
5   | Día Semana        | bot           | texto (Lunes, Martes, ...)
6   | Banco             | bot           | "Falabella" / "Bancochile"
7   | Persona           | bot           | "Titular" / "Adicional"
8   | Descripción       | bot           |
9   | Monto             | bot           | número absoluto (total compra)
10  | Tipo              | bot           | "Cargo" / "Abono"
11  | Saldo             | bot (vacío)   | no disponible aún
12  | Categoría         | bot           |
13  | Subcategoría      | bot           |
14  | Cuota actual      | bot           | int o vacío (vacío en 1/1)
15  | Cuotas total      | bot           | int o vacío
16  | Cuota a pagar     | bot           | float o vacío (mensualidad real)
17  | Moneda            | dashboard     | NO TOCAR
18  | MontoCLP          | dashboard     | NO TOCAR
19  | Esencial          | dashboard     | NO TOCAR
20  | Fijo              | dashboard     | NO TOCAR
21  | Recurrente        | dashboard     | NO TOCAR
22  | Extraordinario    | dashboard     | NO TOCAR
23  | Excluido          | dashboard     | NO TOCAR
24  | Notas             | dashboard     | NO TOCAR
```

`gsheet.upsert_movement(mov)`:
- Busca fila por (Fecha + Descripción + Monto absoluto). Si la encuentra → UPDATE solo cols L:P (12-16). Si no, append fila nueva con cols 17-24 vacías.
- **Nunca toca cols 17-24** — son del dashboard del usuario.

Para flujo de caja mensual, la fórmula correcta es:
```
=IF(O2>1, IF(P2="", I2/O2, P2), I2)
```
("si está en cuotas, usa la cuota mensual; si no, el monto total. Si la cuota mensual no se conoce — pendiente viejo —, deriva como total/cuotas").

---

## 7. Comandos del bot Telegram

| Comando | Qué hace |
|---|---|
| `/start`, `/help` | Lista de comandos. |
| `/setup` | Indica el siguiente banco a configurar. |
| `/cred <falabella\|bancochile>` | Wizard que pide RUT + clave (en TG, ⚠️ ver Sección 9). |
| `/forget <banco>` | Borra credenciales y estado del banco. |
| `/cancel` | Cancela el wizard activo. |
| `/test <banco>` | Scrape ad-hoc en background. |
| `/run` | Corre el daily completo (Falabella + BCh). |
| `/status` | Bancos configurados, total/pendientes, último error. |
| `/pending` | Reenvía pendientes (incluye ignoradas al final si no hay suficientes pendientes). De a 5. |
| `/next` | Siguientes 5 tarjetas si quedaron en cola. |
| `/last [N]` | Últimos N movimientos (default 10). |

**Respuestas a tarjetas**:
- Botones: `✅ Aprobar`, `✏️ Corregir`, `🚫 Ignorar` (en pendientes); `✏️ Corregir nuevamente` (en aprobadas/ignoradas).
- Texto: `1 ok`, `2 ignorar`, `3 esto va a Bodemall`, `todo ok`.
- Force Reply (al apretar Corregir o Ignorar): el campo de texto se abre citando ese mov específico, escribís texto libre.
- "skip" / "cancelar" / "abortar" en force_reply: comportamiento especial (saltear razón / abortar).

---

## 8. Adapters bancarios — quirks aprendidos

### Falabella (`adapters/falabella.py`)

- ✅ **Funciona en Railway** (headless OK).
- Viewport debe ser **1920x1080** (`src/scraper.py`) — el botón "Estado de cuenta" es CSS-responsive.
- Tras login, hay popups publicitarios. `_dismiss_popups()` los cierra **solo si están dentro de `[role="dialog"]`, `[class*="modal"]`, `[class*="popup"]`, `[class*="overlay"]`**. NO usar selectores genéricos como `button[aria-label*="cerrar" i]` — matchean "Cerrar sesión" del header y voltean la sesión.
- Pestaña "Últimos Movimientos" es un `<label for="last-movements">`, no un `<a>`.
- Tablas en shadow DOM de Angular `<app-movements-table>`. Usar locators de Playwright, no `document.querySelectorAll`.
- Hay 2 tablas: "Pendientes de confirmación" (IGNORAR — Diego no quiere ver pendientes del banco) y "Fecha de compras" (LEER). `_select_confirmed_table()` filtra por el primer `<th>`.
- Modal de detalle al click en una fila — `_capture_modal_screenshot()` captura screenshot que se manda con sendPhoto a TG.
- Paginación: `button.btn-pagination:has(img[alt="boton avanzar"])` con `disabled=""` cuando última página.

### Banco de Chile (`adapters/bancochile.py`)

- ⚠️ **NO funciona en Railway** por anti-bot que detecta headless. Solo corre desde la Mac.
- Login URL: `https://login.portales.bancochile.cl/login` (Auth0).
- ⚠️ `DASHBOARD_PATTERN` debe ser **callable** que parsea hostname con `urlparse`, NO un regex laxo. Las URLs de Auth0 tienen `redirect_uri=...portalpersonas.bancochile.cl...` URL-encoded en query params; un `re.search` daría falso positivo.
- Sesión persistida tiene **doble validación**: cookie home + cookie movimientos pueden expirar por separado. `login()` navega a `MOVEMENTS_URL` para verificar acceso de segundo nivel; si rebota a Auth0, hace login fresh.
- RUT debe ir formateado **con puntos** (`15.935.723-6`) — Angular tiene listener de keystroke que valida y `page.fill` no dispara los eventos. `_format_rut_chileno()` lo hace.
- Para el form usar `_type_human()` (page.type con delay 90ms), NO `fill_first()` — el botón submit viene `disabled=""` y solo se habilita con keystrokes reales.
- Mensaje de credenciales rechazadas: detectar `"datos ingresados no son correctos"` en el body y tirar `LoginFailed` con mensaje accionable.
- **Pantalla intermedia**: tras goto a `MOVEMENTS_URL`, hay que clickear `<p class="btn-link...">Cuenta Corriente</p>` para llegar a la tabla.
- **Popups post-login**: igual que Falabella, hay que cerrarlos antes de navegar.
- Tabla: `table.bch-table` con cols `td.cdk-column-fechaContable`, `cdk-column-descripcion`, `cdk-column-cargo`, `cdk-column-abono`. Saltar `tr.bch-row.table-collapse-row` (filas-fantasma de animación).
- Paginación Material: `button.mat-paginator-navigation-next`.
- **Sync de state Mac↔Railway** vía Firestore `browser_state`. Cuando el state expira, Diego corre `python -m scripts.debug_bch_login` desde la Mac para refrescar (login con HEADLESS=false). Ver Sección 11.

### gRPC + Playwright (Firestore)

- gRPC se ensucia con FDs heredados al fork de Playwright/Chromium → `DeadlineExceeded` en operaciones Firestore. Mitigado en `src/db.py` con env vars `GRPC_ENABLE_FORK_SUPPORT=1` + `GRPC_POLL_STRATEGY=poll` antes del import de `firebase_admin`, y `_with_retry()` con backoff exponencial en `insert_movement` y `update_classification`.

---

## 9. Seguridad de credenciales

- ⚠️ **Incidente 2026-05-08**: el script `check_credentials.py` antiguo expuso la clave de BCh en bytes plaintext (`b'Dm1823dm'`) en el chat con el agente. Diego cambió la clave en BCh y reconfiguró. El script ya está corregido para no exponer.
- **Forma correcta de configurar**: usar `python -m scripts.set_credentials <banco>` desde la Mac. Pide RUT + clave con `getpass()` (sin eco), cifra con Fernet (master.key local) y guarda en Firestore. Nunca pasa por Telegram.
- El comando `/cred` desde TG sigue funcionando pero expone la clave en el chat hasta que Telegram la sirva (~2s mínimo). Considerar implementar auto-delete del mensaje del usuario para mitigar.

---

## 10. Scripts útiles (`scripts/`)

| Script | Cuándo usarlo |
|---|---|
| `set_credentials.py <banco>` | Configurar/cambiar credenciales bancarias de manera segura (sin TG). |
| `check_credentials.py <banco>` | Verificar (con composición de la clave censurada) qué hay guardado. |
| `debug_bch_login.py` | Login fresh manual de BCh (HEADLESS=false). Refresca el state en Firestore. **Correrlo cuando el daily reporta `LoginFailed: BCh rechazó las credenciales`** — es lo único que arregla la sesión cuando expira. |
| `refresh_photos.py falabella` | Re-scrapea Falabella, matchea por `mov_id` y manda tarjetas con foto a los pendientes huérfanos sin `tg_photo_file_id`. |
| `extend_sheet_header.py` | Idempotente. Inserta las 3 columnas de cuotas (Cuota actual, Cuotas total, Cuota a pagar) en posiciones 14-16 si no están. **Ya se corrió** el 2026-05-08. |
| `migrate_to_firebase.py` / `migrate_to_supabase.py` | Migraciones one-shot legacy (SQLite → Firestore). Ya no aplican. |

Todos se corren desde la raíz del proyecto con el venv activado:
```bash
cd "/Users/diego/Desktop/Desarrollos DMN/Control de Gastos/Gestión de Gastos" && \
source .venv/bin/activate && \
python -m scripts.<nombre>
```

---

## 11. Lo que está roto / pendiente / decisiones explícitas

### BCh anti-bot detection

- BCh no funciona en Railway (headless). Solución actual: daily corre solo en Mac, state se sincroniza a Firestore.
- **El state de BCh va a expirar cada cierto tiempo (días/semanas)**. Cuando el daily falle con `LoginFailed: rechazó las credenciales` o equivalente, Diego corre `debug_bch_login.py` para refrescar.
- **Plan B (no implementado)**: pivot a Gmail/IMAP. BCh manda emails desde `serviciodetransferencias@bancochile.cl` a `dmnavalon@gmail.com` con cada movimiento. Implementar adapter `bancochile_gmail.py` que vía IMAP busca y parsea esos emails. App password de Gmail: ya se discutió pero no se implementó. Si BCh se vuelve insostenible, migrar a esto.

### Movimientos viejos sin foto / sin cuotas

- ~67 movimientos pendientes pre-feature de screenshots NO tienen `tg_photo_file_id`. Quedan sin foto cuando llegan en `/pending`. Para enriquecerlos: `python -m scripts.refresh_photos falabella`. Pendiente correr.
- Movimientos aprobados/corregidos pre-feature de cuotas: **decisión explícita de Diego** = quedan como están. NO retroactivar.
- Movimientos pendientes con cuotas en la descripción (ej. `(02/12)`): `_backfill_cuotas_from_description` infiere `cuotas_actual` y `cuotas_total` desde el sufijo. `cuota_monto` queda vacío (solo se obtiene re-scrapeando).

### Daily falla mid-loop con DeadlineExceeded

- Si gRPC se traba durante el clasificador del daily, los movs quedan insertados pero sin `suggested_category`. El siguiente `/pending` los clasifica on-demand vía `_ensure_classified`.

### `get_pending` vs `get_all_pending`

- `db.get_pending()` filtra por `notified_at=None` — solo devuelve los nunca notificados. Sirve para el daily.
- `db.get_all_pending()` ignora ese flag. Sirve para `/pending` y saludo.
- Ojo: si en algún momento se necesita mantener un "tablero" de pendientes ya vistos, hay que decidir qué hacer con `notified_at`.

### Force Reply y mensajes viejos en chat

- Si Diego apreta "Corregir nuevamente" en una tarjeta de hace varios días, el flujo funciona. Pero las tarjetas de antes del feature de "Corregir nuevamente" no tienen ese botón (no se actualizan retroactivamente — Telegram no permite editar mensajes >48h y no se guarda `message_id`).

---

## 12. Cómo deployar

```bash
cd "/Users/diego/Desktop/Desarrollos DMN/Control de Gastos/Gestión de Gastos" && \
git add <archivos> && \
git commit -m "<mensaje>" && \
git push && \
railway up --service bot
```

⚠️ **No usar `railway redeploy`** — solo redeploya el deployment activo SIN traer commits nuevos. Usar `railway up --service bot` que sube el código y rebuilda Docker (~3 min).

Verificar logs:
```bash
railway logs --service bot | tail -30
railway logs --service bot | grep -iE "bancochile|BCh|adapters\." | tail -50
```

Tener cuidado con los **rollouts**: durante un redeploy hay 2 containers vivos por ~10-15 segundos. Verás `409 Conflict` transitorios en logs — NO es un problema persistente.

---

## 13. Backlog / mejoras posibles

- **Auto-delete del mensaje de `/cred`** en TG para reducir ventana de exposición de la clave.
- **Comando `/last_approved [N]`** que liste los últimos N aprobados con botón "✏️ Corregir nuevamente" (alternativa al saludo / `/pending` para encontrar movs específicos).
- **BCh vía Gmail/IMAP** (Plan B). Setup: app password Gmail → script `adapter/bancochile_gmail.py` que parsea emails de `serviciodetransferencias@bancochile.cl`.
- **Notification de cuándo expira el state de BCh**: tracking de cuándo fue el último `debug_bch_login` exitoso, alerta proactiva 1 día antes de que expire (timing empírico, no documentado por BCh).
- **Refresh de fotos viejas**: correr `refresh_photos.py falabella` cuando esté tranquilo.
- **Comando `/retry_gsheet`** que reprocesa movs con status='aprobado' que no estén en el sheet (idempotente vía upsert).
- **Migración de movimientos viejos a campos de cuotas** vía re-scrape (decisión: NO hacer).

---

## 14. Histórico de bugs aprendidos

Para que el agente nuevo no caiga en estos:

| Bug | Lección |
|---|---|
| Daily-cron en Railway corría `python -m src.bot` heredando CMD del Dockerfile, peleaba con bot principal por `getUpdates` (409 desde 2026-05-06) | NO crear servicios secundarios sin override de `startCommand`. |
| `_dismiss_popups` Falabella matcheaba `button[aria-label*="cerrar" i]` y cerraba "Cerrar sesión" del header | Selectores genéricos SIEMPRE contextualizados a `[role="dialog"]` / `[class*="modal"]` / etc. |
| `DASHBOARD_PATTERN` de BCh era regex que matcheaba en query params URL-encoded | Nunca usar regex laxo sobre URLs cuando hay query params; usar `urlparse(url).hostname`. |
| `page.fill()` no dispara keystrokes que Angular Material requiere para validar | Para forms con validación en vivo, usar `page.type(value, delay=90)` con click previo. |
| RUT chileno sin puntos era rechazado por BCh | Pre-formatear el RUT a `12.345.678-9` antes del fill (Angular tiene listener de input que normalmente formatea, pero solo con keystrokes). |
| Movs sin clasificar llegando a TG porque daily falló mid-loop | `_ensure_classified()` clasifica on-demand antes de mandar tarjetas. |
| `/pending` no traía pendientes ya notificados | Usar `get_all_pending()`, no `get_pending()` (que filtra por `notified_at`). |
| Tarjetas en `/pending` sin foto/persona | Pasar el dict completo de Firestore a `send_movement_cards`, no reconstruirlo manualmente omitiendo campos. |
| Screenshot diag "blanco" en BCh | Esperar 2s antes del `screenshot()` + usar `full_page=False` + timeout 90s en upload. |
| Container Railway no toma el código pusheado | Usar `railway up`, no `railway redeploy`. |
| Headers de google-cloud-firestore deprecated warnings al usar `where("field", "==", val)` positional | Usar `where(filter=firestore.FieldFilter("field", "==", val))`. No bloqueante. |

---

## 15. Memoria del proyecto (Claude Code skills)

Hay 2 archivos en `~/.claude/projects/-Users-diego-Desktop-Desarrollos-DMN-Control-de-Gastos/memory/`:

- `MEMORY.md` — index.
- `project_falabella_scraper_quirks.md` — 6 quirks documentados de Falabella.
- `project_architecture.md` — topología del proyecto.

Si el agente nuevo es Claude Code, ya las va a leer automáticamente al entrar al directorio.

---

## 16. Contacto y debugging

- Diego responde por Telegram al bot `@mis_gastos_diego_bot` (chat_id 8676856542).
- Para debug visual de scrapers, correr local con `HEADLESS=false`:
  ```bash
  HEADLESS=false python -m src.run_daily
  ```
- Para inspeccionar Firestore directamente: scripts ad-hoc con `firebase_admin` cargando `data/firebase_service_account.json`.
- Logs de Railway: `railway logs --service bot | tail -100`.
- Logs locales del daily: `tail -f logs/daily.stdout.log`.

---

**Última auditoría hecha**: 2026-05-08, durante esta sesión. Todo lo de arriba está vigente al cierre. Si el agente nuevo encuentra divergencias, **el código manda** — actualizar este HANDOFF cuando se hagan cambios sustanciales.

---

## 17. Feature "Movimientos" (revisión masiva en dashboard)

Sección agregada 2026-05-09. Permite revisar/aprobar/corregir/ignorar movimientos en lote desde el dashboard, manteniendo Firestore como fuente única de verdad y Telegram funcionando igual.

### Capa central
- `src/services/movements.py` — funciones `approve_movement`, `correct_movement`, `approve_corrected_movement`, `ignore_movement`, `reopen_movement`, `sync_approved_movement_to_sheet`, `bulk_*`. Usa transacciones Firestore + version check. Cada acción dispara `services.audit.record_event` a la collection `movement_audit`.
- `src/services/exceptions.py` — `MovementNotFound`, `InvalidTransition`, `VersionConflict`, `ValidationError`.
- Bot Telegram (`src/bot.py`) y `src/feedback.py` ahora delegan a esta capa. NUNCA llaman directo a `db.update_decision` ni `gsheet.upsert_movement` para approve/correct/ignore.

### Modelo de estados (dual con legacy)
- `review_status`: `pending | approved | corrected_pending | corrected_approved | ignored | error`
- `sheet_sync_status`: `not_ready | pending_sync | synced | sync_error`
- `version`: int, optimistic locking. El bot pasa `expected_version=None` (skip check). Dashboard SIEMPRE pasa el version del payload — 409 si conflict.
- `status` legacy se sigue escribiendo (mapeo: `pending|corrected_pending`→`pendiente`, `approved|corrected_approved`→`aprobado`, `ignored`→`ignorado`) hasta que la feature esté validada en producción.

### API HTTP
- Corre en el MISMO proceso del bot Railway, en thread daemon separado. Activada por env var `ENABLE_MOVIMIENTOS_REVIEW=true`. Si está en false, el thread no levanta.
- Auth: header `Authorization: Bearer ${DASHBOARD_API_TOKEN}`.
- Endpoints (todos prefijados `/api`): `health`, `categories`, `movements` (GET list + filtros), `movements/<id>` (GET detail), `movements/<id>/audit`, `movements/<id>/{approve,correct,approve-correction,ignore,reopen,sync}`, `movements/bulk/{approve,categorize,ignore,comment,reopen}`.
- Smoke: `curl https://<railway>.up.railway.app/api/health` → 200.
- Implementado con Flask + werkzeug (`src/api/server.py`, `src/api/routes/`). Sin nuevos servicios Railway → no rompe el "no múltiples polling".

### Dashboard (Next.js)
- Sección en `dashboard/app/movimientos/page.tsx` (componente `MovimientosTable`). Pestañas, filtros, edición inline, acciones masivas, modal de ignore, drawer de audit. Auto-refresh 30s.
- Route handlers proxy en `dashboard/app/api/movimientos/`. NUNCA exponen `BACKEND_API_TOKEN` al navegador — el token solo se usa server-side.
- Reusa el Basic Auth existente de `dashboard/proxy.ts` (`DASHBOARD_PASSWORD`) para gating del frontend.
- Activación: env vars en Vercel `NEXT_PUBLIC_ENABLE_MOVIMIENTOS_REVIEW=true`, `BACKEND_API_URL=https://<railway>.up.railway.app`, `BACKEND_API_TOKEN=<mismo token que en Railway>`.

### Google Sheet
- Header extendido a 25 cols. Col 25 (Y) = `MovementId`. Lookup en `gsheet._find_existing_row` ahora prefiere `movement_id` si está; fallback al triple (fecha, desc, monto) para movs legacy. Idempotente — reintentar sync no duplica filas.
- Una fila approved en GSheet tiene `MovementId` automáticamente desde el primer upsert post-feature. Movs legacy se backfillean con `scripts/extend_sheet_movement_id.py`.

### Scripts
- `scripts/extend_sheet_movement_id.py [--dry-run]` — agrega col Y al header y backfilla `movement_id` por match (fecha, desc, monto). Idempotente.
- `scripts/backfill_movement_status.py [--dry-run]` — mapea `status` legacy → `review_status`/`sheet_sync_status`/`version`/`updated_at`. Para `aprobado`, marca `synced` si encontró fila en GSheet, `sync_error` si no. NO toca `status` legacy.

### Auditoría
- Collection `movement_audit` — un doc por evento. Campos: `movement_id, action, prev_review_status, new_review_status, prev_sheet_sync_status, new_sheet_sync_status, actor, source (telegram|dashboard|system), details, created_at`.
- Endpoint `GET /api/movements/<id>/audit` y drawer en el dashboard.
- TTL 90 días recomendado (configurar en Cloud Console o agregar script).

### Feature flag y rollback
- Backend: `ENABLE_MOVIMIENTOS_REVIEW=false` en Railway → API thread no levanta. Bot sigue funcionando con dual-write a `status` legacy.
- Frontend: `NEXT_PUBLIC_ENABLE_MOVIMIENTOS_REVIEW=false` en Vercel → link "Movimientos" se oculta y los route handlers responden 404.
- Tests: `cd "Gestión de Gastos" && .venv/bin/python -m pytest tests/` — 33 pasando al cierre.

### Activación inicial (lo que Diego tiene que hacer una vez)

```bash
# 1. Generar token compartido (un valor aleatorio largo).
openssl rand -hex 32

# 2. En Railway (variables del servicio bot):
#    ENABLE_MOVIMIENTOS_REVIEW=true
#    DASHBOARD_API_TOKEN=<token>
#    DASHBOARD_ORIGIN=https://<dashboard-url>.vercel.app   (opcional, para CORS)

# 3. En Vercel (settings → environment variables):
#    NEXT_PUBLIC_ENABLE_MOVIMIENTOS_REVIEW=true
#    BACKEND_API_URL=https://<railway-service>.up.railway.app
#    BACKEND_API_TOKEN=<mismo token>

# 4. Backfill desde la Mac de Diego (una sola vez):
cd "/Users/diego/Desktop/Desarrollos DMN/Control de Gastos/Gestión de Gastos" && .venv/bin/python -m scripts.extend_sheet_movement_id --dry-run
# Verificar el output, luego correr sin --dry-run:
cd "/Users/diego/Desktop/Desarrollos DMN/Control de Gastos/Gestión de Gastos" && .venv/bin/python -m scripts.extend_sheet_movement_id
cd "/Users/diego/Desktop/Desarrollos DMN/Control de Gastos/Gestión de Gastos" && .venv/bin/python -m scripts.backfill_movement_status --dry-run
cd "/Users/diego/Desktop/Desarrollos DMN/Control de Gastos/Gestión de Gastos" && .venv/bin/python -m scripts.backfill_movement_status

# 5. Redeploy: Railway hace auto-redeploy en push; en Vercel se
#    gatilla un build automático tras cambiar env vars.
```

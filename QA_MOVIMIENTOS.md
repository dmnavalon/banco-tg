# Prompt de QA — Feature "Movimientos"

> **Para el agente QA**: este documento es tu briefing completo. Léelo de punta a punta antes de empezar. No improvises pasos. No omitas tests. Reporta hallazgos en el formato exacto especificado al final.

---

## 1. Tu misión

Sos un agente senior de QA. Tu objetivo es validar la feature **"Movimientos"** del proyecto Control de Gastos de Diego en su entorno de **producción real** (Railway + Vercel + Firestore + Google Sheets), corriendo desde la Mac de Diego con acceso CLI a `railway`, `vercel`, `git`, `curl`, `python3`.

Vas a ejecutar **41 test cases** organizados en 5 capas:

```
L1 · Smoke         (5 TCs)   — el sistema responde
L2 · Auth          (4 TCs)   — controles de acceso
L3 · Funcional     (18 TCs)  — los 18 criterios de aceptación
L4 · Integración   (8 TCs)   — TG ↔ Dashboard ↔ GSheet ↔ Firestore
L5 · Edge & Regr   (6 TCs)   — concurrencia, errores, no-regresión
```

Al final entregás un **reporte único** (formato definido en §10). Si algún test falla, **no abortes**: marca FAIL, registra el defecto, sigue con el siguiente. Solo abortá si la capa L1 (Smoke) falla — sin sistema arriba no tiene sentido seguir.

### Reglas absolutas

- **NO ejecutes acciones destructivas sin cleanup**. Cada mutación debe revertirse o documentarse. Específicamente: nunca dejes movimientos en estado distinto al que tenían al empezar el test, salvo que el test lo declare explícitamente.
- **NO escribas en producción más de lo necesario**. Reusa el mismo mov de test para varios TCs cuando se pueda.
- **NO inventes datos**. Todo input debe ser real (un movimiento real, una categoría real de la taxonomía).
- **NO toques GSheet a mano**. Solo via la API o el script ya provisto.
- **NO commitees código nuevo**. Tu rol es solo verificar.
- **NO uses tokens en texto plano del reporte**. Si algo falla por token, redactá `Bearer ****`.

---

## 2. Contexto del sistema bajo prueba

**Arquitectura** (resumen — detalle completo en `HANDOFF.md` §17):

```
Bot Telegram (Railway, Python)              ←──┐
     ├─ long-poll a Telegram                   │ comparten capa
     └─ API HTTP Flask (thread, puerto $PORT)  │ src/services/movements.py
                  │                            │
                  │ Bearer token               │
                  ↓                            │
Dashboard Next.js (Vercel)                  ──┘
     └─ /movimientos (sección nueva)
              │
              └→ Firestore (collection movements + movement_audit)
              └→ Google Sheets (col Y = MovementId)
```

**URLs y datos de acceso**:

| Recurso | Cómo obtenerlo |
|---|---|
| Backend API URL | `railway variables --kv \| grep RAILWAY_PUBLIC_DOMAIN` (prefijar `https://`) |
| `DASHBOARD_API_TOKEN` | `railway variables --kv \| grep '^DASHBOARD_API_TOKEN='` |
| Dashboard URL primario | `https://dashboard-finanzas-personales-navy.vercel.app` |
| `DASHBOARD_PASSWORD` (Basic Auth) | `cd dashboard && vercel env pull /tmp/qa-env --environment=production --yes && grep DASHBOARD_PASSWORD /tmp/qa-env` |
| Bot username | `@mis_gastos_diego_bot` |
| Bot chat_id autorizado | `8676856542` (Diego) |

**Modelo de estados** (memorizá esto — los tests lo invocan continuamente):

```
review_status:        pending | corrected_pending | approved | corrected_approved | ignored | error
sheet_sync_status:    not_ready | pending_sync | synced | sync_error
status (legacy):      pendiente | aprobado | ignorado
version:              int (optimistic locking)
```

Transiciones legales:

| Acción | Estado actual válido | Nuevo `review_status` | Dispara sync? |
|---|---|---|---|
| approve | `pending` | `approved` | sí |
| approve_corrected | `corrected_pending` | `corrected_approved` | sí |
| correct | `pending`, `corrected_pending`, `approved`, `corrected_approved` | `corrected_pending` | no |
| ignore (con reason no vacío) | `pending`, `corrected_pending`, `approved`, `corrected_approved` | `ignored` | no |
| reopen | `approved`, `corrected_approved`, `ignored`, `error` | `pending` | no |

---

## 3. Precondiciones (ejecutar 1 vez antes de los tests)

```bash
# 0.1 Variables de entorno para tu sesión de QA
cd "/Users/diego/Desktop/Desarrollos DMN/Control de Gastos/Gestión de Gastos"
export QA_RUN_ID="qa-$(date +%Y%m%d-%H%M%S)"
mkdir -p /tmp/qa-movimientos && chmod 700 /tmp/qa-movimientos

# 0.2 Pull credenciales de producción
TOKEN=$(railway variables --kv 2>/dev/null | grep '^DASHBOARD_API_TOKEN=' | cut -d= -f2-)
DOMAIN=$(railway variables --kv 2>/dev/null | grep '^RAILWAY_PUBLIC_DOMAIN=' | cut -d= -f2-)
export BACKEND_URL="https://$DOMAIN"
export BACKEND_TOKEN="$TOKEN"
export DASHBOARD_URL="https://dashboard-finanzas-personales-navy.vercel.app"

cd dashboard && vercel env pull /tmp/qa-movimientos/env --environment=production --yes >/dev/null 2>&1
export DASHBOARD_PASSWORD=$(grep '^DASHBOARD_PASSWORD=' /tmp/qa-movimientos/env | cut -d= -f2- | tr -d '"')
chmod 600 /tmp/qa-movimientos/env
cd ..

# 0.3 Verificá que tenés acceso (debe ser 200 + JSON con status:ok)
curl -s "$BACKEND_URL/api/health" | python3 -m json.tool

# 0.4 Helpers para el reporte
qa_log() { echo "[$(date +%H:%M:%S)] $*" | tee -a /tmp/qa-movimientos/run.log; }
```

**Si alguna de estas falla — ABORTÁ y reportá**:
- Token vacío → Defect BLOCKER, código `QA-PRECOND-01`.
- `/api/health` no devuelve `status:ok` → Defect BLOCKER, código `QA-PRECOND-02`.
- `DASHBOARD_PASSWORD` vacío → seguí, pero los TCs L4-DASH se marcan SKIPPED.

---

## 4. Test data — selección y cleanup

Cada test se hace sobre **un movimiento real**, identificado por su `id`. Para no contaminar:

### Selección del mov de prueba

```bash
# Tomá UN solo mov en estado pending para usar como sujeto de pruebas mutables.
QA_MOV_ID=$(curl -s "$BACKEND_URL/api/movements?status=pending&limit=1" \
  -H "Authorization: Bearer $BACKEND_TOKEN" | \
  python3 -c "import json,sys; print(json.load(sys.stdin)['items'][0]['id'])")
echo "QA_MOV_ID=$QA_MOV_ID"
```

Si no hay pending disponibles, esperá al próximo daily o tomá uno `ignored` y reabrílo (eso sería el primer test).

### Snapshot inicial (para cleanup final)

```bash
curl -s "$BACKEND_URL/api/movements/$QA_MOV_ID" \
  -H "Authorization: Bearer $BACKEND_TOKEN" \
  > /tmp/qa-movimientos/snapshot-initial.json
```

### Cleanup obligatorio al final

Reabrí y restablecé al estado inicial:

```bash
# Restituye review_status=pending si quedó en otro estado.
FINAL_STATE=$(curl -s "$BACKEND_URL/api/movements/$QA_MOV_ID" -H "Authorization: Bearer $BACKEND_TOKEN" | python3 -c "import json,sys; print(json.load(sys.stdin)['movement']['review_status'])")
if [ "$FINAL_STATE" != "pending" ]; then
  V=$(curl -s "$BACKEND_URL/api/movements/$QA_MOV_ID" -H "Authorization: Bearer $BACKEND_TOKEN" | python3 -c "import json,sys; print(json.load(sys.stdin)['movement']['version'])")
  curl -s -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/reopen" \
    -H "Authorization: Bearer $BACKEND_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"version\": $V, \"actor\": \"$QA_RUN_ID\"}"
fi
```

> **Importante**: si por algún test el mov quedó `corrected_approved` y se sincronizó al GSheet, queda una fila en el sheet con `MovementId=$QA_MOV_ID`. Eso es residuo aceptable — el reopen no la borra. Documentá en el reporte: "post-test residuo en GSheet row N (mov $QA_MOV_ID), no destructivo".

---

## 5. Plan de pruebas — Capa L1 · Smoke (5 TCs)

> Si CUALQUIERA de estos falla con severidad BLOCKER, abortá la corrida y reporta solo L1.

### TC-L1-01 · Backend health responde 200

- **Severidad si falla**: BLOCKER
- **Pasos**:
  ```bash
  curl -s -o /tmp/qa-movimientos/tc-l1-01.json -w "HTTP=%{http_code}\n" "$BACKEND_URL/api/health"
  ```
- **Esperado**: `HTTP=200` y body `{"status":"ok","db":"ok",...}`
- **Verificación**: parsear JSON y assertar `status==ok` y `db==ok`. Si `db==error`, capturá el campo `db_error` en el reporte.

### TC-L1-02 · Dashboard root responde 200 (con basic auth)

- **Severidad**: BLOCKER
- **Pasos**:
  ```bash
  curl -s -o /dev/null -w "HTTP=%{http_code}\n" "$DASHBOARD_URL/" -u ":$DASHBOARD_PASSWORD"
  ```
- **Esperado**: `HTTP=200`. Si `HTTP=401` y password OK, defect en `proxy.ts`.

### TC-L1-03 · Página /movimientos renderiza

- **Severidad**: BLOCKER
- **Pasos**:
  ```bash
  curl -s "$DASHBOARD_URL/movimientos" -u ":$DASHBOARD_PASSWORD" -o /tmp/qa-movimientos/tc-l1-03.html
  grep -q "MovimientosTable\|Movimientos" /tmp/qa-movimientos/tc-l1-03.html && echo OK
  ```
- **Esperado**: contiene literal `Movimientos` y/o `MovimientosTable` (componente client) en el HTML SSR.

### TC-L1-04 · Bot Telegram operacional

- **Severidad**: CRITICAL (no BLOCKER porque la feature dashboard puede usarse aunque el bot esté down)
- **Pasos**:
  ```bash
  BOT_TOKEN=$(railway variables --kv | grep '^TG_BOT_TOKEN=' | cut -d= -f2-)
  curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getMe" | python3 -c "import json,sys; print(json.load(sys.stdin).get('ok'))"
  curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo" | python3 -c "import json,sys; d=json.load(sys.stdin)['result']; print('webhook_url=',d.get('url')); print('pending=',d.get('pending_update_count'))"
  ```
- **Esperado**: `True`, `webhook_url=` (vacío), `pending=0` o número bajo (<10).

### TC-L1-05 · Logs de Railway no muestran 409 conflict en últimos 60s

- **Severidad**: MAJOR (transitorios son aceptables, persistentes son bug)
- **Pasos**:
  ```bash
  railway logs --service bot 2>&1 | tail -30 | grep -c "409"
  ```
- **Esperado**: `0` 409s en los últimos 30 entries. Si >5, hay otro proceso polling el mismo bot — defect MAJOR `QA-409-CONFLICT`.

---

## 6. Plan de pruebas — Capa L2 · Auth (4 TCs)

### TC-L2-01 · API rechaza requests sin Authorization

- **Severidad**: CRITICAL
- **Pasos**:
  ```bash
  curl -s -o /dev/null -w "%{http_code}" "$BACKEND_URL/api/movements"
  ```
- **Esperado**: `401`

### TC-L2-02 · API rechaza Bearer con token incorrecto

- **Severidad**: CRITICAL
- **Pasos**:
  ```bash
  curl -s -o /dev/null -w "%{http_code}" "$BACKEND_URL/api/movements" -H "Authorization: Bearer wrong-token-aaaaaaaa"
  ```
- **Esperado**: `401`. **Si responde 200, defect BLOCKER** (auth roto).

### TC-L2-03 · Dashboard rechaza acceso sin basic auth

- **Severidad**: MAJOR
- **Pasos**:
  ```bash
  curl -s -o /dev/null -w "%{http_code}" "$DASHBOARD_URL/movimientos"
  ```
- **Esperado**: `401` (proxy.ts activo).

### TC-L2-04 · Token correcto retorna 200

- **Severidad**: CRITICAL
- **Pasos**:
  ```bash
  curl -s -o /dev/null -w "%{http_code}" "$BACKEND_URL/api/categories" -H "Authorization: Bearer $BACKEND_TOKEN"
  ```
- **Esperado**: `200`. Si 401 acá, el token rotado no quedó bien aplicado.

---

## 7. Plan de pruebas — Capa L3 · Funcional (los 18 criterios) (18 TCs)

> Cada TC mapea 1:1 con un criterio del `prompt_feature_movimientos.md` original.

**Convenciones**:
- `$BACKEND_URL`, `$BACKEND_TOKEN`, `$DASHBOARD_URL`, `$DASHBOARD_PASSWORD`, `$QA_MOV_ID` están en env.
- `H_AUTH='Authorization: Bearer '"$BACKEND_TOKEN"`, `H_JSON='Content-Type: application/json'`.
- `get_version()` helper:
  ```bash
  get_version() {
    curl -s "$BACKEND_URL/api/movements/$1" -H "$H_AUTH" | python3 -c "import json,sys; print(json.load(sys.stdin)['movement']['version'])"
  }
  ```

### TC-L3-01 · Mov nuevo aparece en /pending y en pestaña Pendientes (criterio 1)

- **Severidad**: CRITICAL
- **Setup**: `QA_MOV_ID` debe estar en `pending`.
- **Pasos**:
  1. `curl -s "$BACKEND_URL/api/movements?status=pending&limit=300" -H "$H_AUTH" | jq '.items[] | select(.id=="'$QA_MOV_ID'")'`
  2. Verificar status legacy: el mismo mov debe tener `status==pendiente` (dual-write).
- **Esperado**: el mov aparece en la respuesta filtrada y `status==pendiente`.

### TC-L3-02 · Aprobar en TG → desaparece de Pendientes en dashboard (criterio 2)

- **Severidad**: CRITICAL
- **Pasos**:
  1. Llamar al endpoint con `source=telegram` simulado: `curl -s -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/approve" -H "$H_AUTH" -H "$H_JSON" -d '{"actor":"qa-tg-sim"}'` *(omitiendo version simula bot Telegram)*.
  2. Esperar 1s.
  3. Re-listar: `curl -s "$BACKEND_URL/api/movimientos?status=pending&limit=300" -u ":$DASHBOARD_PASSWORD" "$DASHBOARD_URL/api/movimientos?status=pending"` y verificar que `$QA_MOV_ID` NO está.
  4. Verificar via dashboard proxy también: `curl -s "$DASHBOARD_URL/api/movimientos?status=approved,corrected_approved&limit=300" -u ":$DASHBOARD_PASSWORD" | jq '.items[].id' | grep $QA_MOV_ID`.
- **Esperado**: ya no aparece en pending, sí aparece en approved.

### TC-L3-03 · Aprobar en dashboard → desaparece de /pending TG (criterio 3)

- **Severidad**: CRITICAL
- **Setup**: necesitás otro mov en pending. Si no hay más, omití (SKIP) y documentá. Idealmente reabrí el de TC-L3-02 primero.
- **Pasos**:
  1. Reopen del mov: `V=$(get_version $QA_MOV_ID); curl -s -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/reopen" -H "$H_AUTH" -H "$H_JSON" -d "{\"version\":$V}"`
  2. Aprobar via dashboard proxy: `V=$(get_version $QA_MOV_ID); curl -s -X POST "$DASHBOARD_URL/api/movimientos/$QA_MOV_ID/approve" -u ":$DASHBOARD_PASSWORD" -H "$H_JSON" -d "{\"version\":$V,\"actor\":\"qa-dashboard\"}"`
  3. Verificar Firestore directamente via `/api/movements/$QA_MOV_ID`: `last_action_source==dashboard`, `review_status==approved`.
- **Esperado**: el endpoint del bot (`/api/movements?status=pending`) no incluye este mov.

### TC-L3-04 · Corregir sin aprobar → corrected_pending, NO va a GSheet (criterio 4)

- **Severidad**: CRITICAL
- **Setup**: reopen previo si está aprobado.
- **Pasos**:
  1. `V=$(get_version $QA_MOV_ID)`
  2. ```bash
     curl -s -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/correct" \
       -H "$H_AUTH" -H "$H_JSON" \
       -d "{\"version\":$V,\"actor\":\"qa\",\"final_category\":\"Otros\",\"final_subcategory\":\"Varios\",\"comment\":\"qa test L3-04\"}"
     ```
  3. Verificar en Firestore: `review_status==corrected_pending`, `sheet_sync_status==not_ready`.
  4. Verificar en GSheet (no debe haber row con MovementId == $QA_MOV_ID a menos que ya hubiera por TC anterior).
- **Esperado**: `corrected_pending`, `not_ready`, `final_category=Otros`, `final_subcategory=Varios`. **Sin nueva fila en GSheet** (a menos que ya existiera de un sync anterior).

### TC-L3-05 · Corregir y aprobar → corrected_approved, va a GSheet con datos finales (criterio 5)

- **Severidad**: CRITICAL
- **Setup**: el mov debe estar en `corrected_pending` (después de TC-L3-04).
- **Pasos**:
  1. `V=$(get_version $QA_MOV_ID)`
  2. `curl -s -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/approve-correction" -H "$H_AUTH" -H "$H_JSON" -d "{\"version\":$V}"`
  3. Esperar 2s (sync GSheet).
  4. Re-fetch: `review_status==corrected_approved`, `sheet_sync_status==synced`.
  5. Verificar GSheet: leer la columna Y en busca de `$QA_MOV_ID`. Debe existir UNA sola fila con esa categoría final.
- **Esperado**: estado correcto, fila única en GSheet con `Categoría=Otros`, `Subcategoría=Varios`.

### TC-L3-06 · Ignorar exige reason → 422 sin reason (criterio 6)

- **Severidad**: MAJOR
- **Pasos**:
  ```bash
  V=$(get_version $QA_MOV_ID)
  curl -s -o /tmp/qa-movimientos/tc-l3-06.json -w "%{http_code}\n" \
    -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/ignore" \
    -H "$H_AUTH" -H "$H_JSON" \
    -d "{\"version\":$V,\"reason\":\"   \"}"
  ```
- **Esperado**: `422`, body con `{"error":"validation_error",...}`.

### TC-L3-07 · Ignorar nunca va a GSheet (criterio 7)

- **Severidad**: CRITICAL
- **Setup**: el mov puede estar en cualquier estado mutable.
- **Pasos**:
  1. Marcar fila pre-test en GSheet (anotar count actual para `$QA_MOV_ID`).
  2. `V=$(get_version $QA_MOV_ID)`; `curl -s -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/ignore" -H "$H_AUTH" -H "$H_JSON" -d "{\"version\":$V,\"reason\":\"qa test ignore\"}"`
  3. Verificar Firestore: `review_status==ignored`, `sheet_sync_status==not_ready`, `ignore_reason==qa test ignore`.
  4. Verificar GSheet: count de filas con `MovementId==$QA_MOV_ID` no aumentó.
- **Esperado**: ignored, no se agregó fila nueva.

### TC-L3-08 · Reabrir aprobado → review_status=pending, sheet_sync_status=not_ready (criterio 8)

- **Severidad**: MAJOR
- **Pasos**: ya cubierto en setup de TCs anteriores. Marcar PASS si los reopens previos funcionaron.

### TC-L3-09 · Sync GSheet falla → sync_error con mensaje, mov no se pierde (criterio 9)

- **Severidad**: MAJOR
- **Cómo simularlo sin romper GSheet real**: este caso es difícil de gatillar sintéticamente sin tirar credenciales. Estrategia:
  1. Verificar que **ya** hay 45 movs en estado `(approved, sync_error)` (los legacy orphans del backfill).
  2. Confirmar que tienen `sync_error_message` no vacío.
  ```bash
  curl -s "$BACKEND_URL/api/movements?status=approved,corrected_approved&limit=300" -H "$H_AUTH" | python3 -c "
  import json, sys
  d = json.load(sys.stdin)
  err = [m for m in d['items'] if m['sheet_sync_status']=='sync_error']
  print(f'sync_error count: {len(err)}')
  if err: print(f'sample message: {err[0][\"sync_error_message\"]}')"
  ```
- **Esperado**: count > 0, mensaje no vacío. **Si count==0 y nunca hubo sync_error**, no se puede validar este criterio sin inducir un fallo controlado — marcar como **PARTIAL** y documentar.

### TC-L3-10 · Reintentar sync no duplica filas (criterio 10)

- **Severidad**: CRITICAL
- **Pasos**:
  1. Tomá un mov con `sheet_sync_status==synced` (ej. el de TC-L3-05).
  2. Llamar `POST $BACKEND_URL/api/movements/<id>/sync` (sin body).
  3. Re-fetch: `sheet_sync_status==synced`, `version` aumentó pero el mov sigue siendo el mismo.
  4. Verificar GSheet: count de filas con ese `MovementId` sigue siendo exactamente 1.
- **Esperado**: una sola fila en GSheet, no duplicado.

### TC-L3-11 · Telegram visualmente igual (criterio 11)

- **Severidad**: MAJOR
- **Cómo verificarlo automáticamente**: limitado, pero podés:
  1. Confirmar que `/help` retorna el mismo HELP_TEXT (cliente del bot, no API).
  2. Inspeccionar `src/telegram_notify.py:_movement_card_text` y verificar que el formato de tarjeta no cambió en `bot.py:_handle_callback action 'a'` post-refactor.
  3. Manual: pedirle a Diego que mande `/last 1` desde TG y screenshot. Si no es accesible, marcar **MANUAL_PENDING**.

### TC-L3-12 · Dashboard financiero existente sigue funcionando (criterio 12)

- **Severidad**: CRITICAL (regresión)
- **Pasos**:
  ```bash
  curl -s "$DASHBOARD_URL/" -u ":$DASHBOARD_PASSWORD" -o /tmp/qa-movimientos/tc-l3-12.html
  grep -oE "Mes [a-zA-Z]+|movimientos|Resumen|Flujo|Gastos" /tmp/qa-movimientos/tc-l3-12.html | head -10
  ```
- **Esperado**: HTML contiene los strings del dashboard original (Resumen, Flujo de caja, Gastos, etc.) — la página `/` no se rompió.

### TC-L3-13 · Aprobación masiva 5 movs → 5 synced + 5 audit events (criterio 13)

- **Severidad**: CRITICAL
- **Setup**: necesitás 5 movs en pending. Si no, ajustá a los disponibles (mínimo 2).
- **Pasos**:
  1. ```bash
     IDS=$(curl -s "$BACKEND_URL/api/movements?status=pending&limit=5" -H "$H_AUTH" | python3 -c "import json,sys; print(json.dumps([i['id'] for i in json.load(sys.stdin)['items'][:5]]))")
     VERS=$(curl -s "$BACKEND_URL/api/movements?status=pending&limit=5" -H "$H_AUTH" | python3 -c "import json,sys; d=json.load(sys.stdin)['items'][:5]; print(json.dumps({i['id']:i['version'] for i in d}))")
     ```
  2. ```bash
     curl -s -X POST "$BACKEND_URL/api/movements/bulk/approve" \
       -H "$H_AUTH" -H "$H_JSON" \
       -d "{\"ids\":$IDS,\"versions\":$VERS,\"actor\":\"qa-bulk\"}"
     ```
  3. Esperar 5s. Verificar cada uno: `review_status==approved`, `sheet_sync_status==synced` (o `sync_error` si GSheet falló).
  4. Verificar `/api/movements/<id>/audit` para 1 de ellos: debe tener evento `approved_from_dashboard` reciente.
- **Esperado**: todos OK, audit registrado, 0 conflicts.
- **Cleanup**: reabrí los 5 al final para no contaminar.

### TC-L3-14 · Edición masiva de categoría/subcategoría (criterio 14)

- **Severidad**: MAJOR
- **Pasos**: similar a TC-L3-13 pero con `/bulk/categorize`, payload con `final_category=Otros`, `final_subcategory=Varios`. Verificar que los movs quedaron en `corrected_pending` con esa categoría.
- **Cleanup**: reabrí.

### TC-L3-15 · Cada acción queda registrada en auditoría (criterio 15)

- **Severidad**: MAJOR
- **Pasos**:
  ```bash
  curl -s "$BACKEND_URL/api/movements/$QA_MOV_ID/audit" -H "$H_AUTH" | python3 -c "
  import json, sys
  d = json.load(sys.stdin)
  events = d['events']
  print(f'total events: {len(events)}')
  for e in events[:10]:
    print(f'  {e[\"created_at\"]}  {e[\"action\"]:30s}  {e[\"prev_review_status\"]} → {e[\"new_review_status\"]}  by={e[\"actor\"]} src={e[\"source\"]}')
  "
  ```
- **Esperado**: events incluyen las acciones que ejecutaste en TCs previos sobre `$QA_MOV_ID` (approve, correct, ignore, reopen).

### TC-L3-16 · Sin migraciones destructivas (criterio 16)

- **Severidad**: BLOCKER (verificación pasiva)
- **Pasos**:
  ```bash
  M=$(curl -s "$BACKEND_URL/api/movements/$QA_MOV_ID" -H "$H_AUTH" | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['movement']))")
  echo "$M" | python3 -c "
  import json, sys
  m = json.loads(sys.stdin.read())
  legacy = ['status', 'final_category', 'decided_by', 'decided_at', 'inserted_at', 'tg_photo_file_id']
  missing = [k for k in legacy if k not in m]
  print('legacy fields ausentes:', missing or 'NINGUNO ✓')
  print('status legacy:', m.get('status'))
  "
  ```
- **Esperado**: `legacy fields ausentes: NINGUNO ✓` y `status` tiene valor (no `None`).

### TC-L3-17 · No hay secretos expuestos en frontend ni logs (criterio 17)

- **Severidad**: BLOCKER (security)
- **Pasos**:
  1. Pull del HTML de la sección Movimientos:
     ```bash
     curl -s "$DASHBOARD_URL/movimientos" -u ":$DASHBOARD_PASSWORD" | grep -iE "BACKEND_API_TOKEN|DASHBOARD_API_TOKEN|FIREBASE_KEY|GSHEET_KEY|service_account" && echo "FOUND_SECRETS" || echo "OK"
     ```
  2. Pull del JS bundle (primer chunk):
     ```bash
     BUNDLE_URL=$(curl -s "$DASHBOARD_URL/movimientos" -u ":$DASHBOARD_PASSWORD" | grep -oE '/_next/static/chunks/[a-z0-9]+\.js' | head -1)
     curl -s "${DASHBOARD_URL}${BUNDLE_URL}" -u ":$DASHBOARD_PASSWORD" | grep -oE "[a-f0-9]{60,}" | head -5
     ```
  3. Logs de Railway:
     ```bash
     railway logs --service bot 2>&1 | tail -200 | grep -iE "Bearer [a-f0-9]{30,}|FIREBASE_KEY_JSON|service_account" && echo "LEAKED" || echo "OK"
     ```
- **Esperado**: HTML dice `OK`, bundle no contiene tokens hex de 60+ chars (excepto IDs de movs que son 16), logs dicen `OK`. **Cualquier hallazgo es BLOCKER**.

### TC-L3-18 · No hay cambios fuera del alcance (criterio 18)

- **Severidad**: MAJOR (review)
- **Pasos**:
  ```bash
  cd "/Users/diego/Desktop/Desarrollos DMN/Control de Gastos/Gestión de Gastos"
  git log --name-only origin/main~1..origin/main | grep -E "^\s*[a-zA-Z]" | sort -u
  ```
- **Esperado**: solo paths bajo `src/`, `dashboard/`, `scripts/`, `tests/`, `requirements*.txt`, `Dockerfile`, `.env.example`, `HANDOFF.md`, `README.md`. **Cualquier path no relacionado** con la feature → MAJOR defect.

---

## 8. Plan de pruebas — Capa L4 · Integración (8 TCs)

### TC-L4-01 · Concurrencia: dos updates simultáneos → uno gana, otro 409

- **Severidad**: CRITICAL
- **Setup**: `$QA_MOV_ID` en pending.
- **Pasos**:
  ```bash
  V=$(get_version $QA_MOV_ID)
  # Disparar dos approves en paralelo con la misma version
  (curl -s -o /tmp/qa-movimientos/c1.json -w "C1=%{http_code}\n" -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/approve" -H "$H_AUTH" -H "$H_JSON" -d "{\"version\":$V,\"actor\":\"c1\"}" &)
  (curl -s -o /tmp/qa-movimientos/c2.json -w "C2=%{http_code}\n" -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/approve" -H "$H_AUTH" -H "$H_JSON" -d "{\"version\":$V,\"actor\":\"c2\"}" &)
  wait
  ```
- **Esperado**: uno gana (200), el otro o también 200 (idempotencia post-approve) o 409 (race en la transacción).

### TC-L4-02 · Version stale → 409 con current_movement

- **Severidad**: CRITICAL
- **Pasos**:
  ```bash
  curl -s -o /tmp/qa-movimientos/tc-l4-02.json -w "%{http_code}\n" \
    -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/approve" \
    -H "$H_AUTH" -H "$H_JSON" -d '{"version":1,"actor":"qa"}'
  ```
  *(asumiendo que la version actual es > 1 después de los TCs previos)*
- **Esperado**: `409`, body con `error=version_conflict`, `expected=1`, `current=<N>`, `current_movement={...}`.

### TC-L4-03 · Mov inexistente → 404

- **Severidad**: MAJOR
- **Pasos**:
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" "$BACKEND_URL/api/movements/no-existe-foo-bar" -H "$H_AUTH"
  ```
- **Esperado**: `404`.

### TC-L4-04 · Acción inválida en bulk → 404

- **Severidad**: MAJOR
- **Pasos**:
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" -X POST "$BACKEND_URL/api/movements/bulk/foo" -H "$H_AUTH" -H "$H_JSON" -d '{"ids":[]}'
  ```
- **Esperado**: `404` (Flask blueprint no matchea).

### TC-L4-05 · Filtros combinados de listado funcionan

- **Severidad**: MAJOR
- **Pasos**:
  ```bash
  curl -s "$BACKEND_URL/api/movements?status=pending&bank=falabella&min_amount=1000&limit=50" -H "$H_AUTH" | python3 -c "
  import json, sys
  d = json.load(sys.stdin)
  for it in d['items']:
    assert it['review_status'] == 'pending', f'review wrong: {it}'
    assert it['bank'] == 'falabella', f'bank wrong: {it}'
    assert abs(float(it['amount'])) >= 1000, f'amount wrong: {it}'
  print(f'OK · {d[\"count\"]} items, todos cumplen filtros')
  "
  ```
- **Esperado**: 0 items violan los filtros, count > 0 (si hay datos que cumplan).

### TC-L4-06 · Endpoint /categories devuelve taxonomía esperada

- **Severidad**: MAJOR
- **Pasos**:
  ```bash
  curl -s "$BACKEND_URL/api/categories" -H "$H_AUTH" | python3 -c "
  import json, sys
  d = json.load(sys.stdin)
  required_categories = {'Sueldo', 'Hogar y alimentación', 'Transporte', 'Otros'}
  got = set(d['taxonomy'].keys())
  missing = required_categories - got
  assert not missing, f'faltan categorias: {missing}'
  assert 'Gastos por rendir' in d['extensible_categories'], 'falta extensible'
  print(f'OK · {len(got)} categorías, extensibles: {d[\"extensible_categories\"]}')"
  ```

### TC-L4-07 · Auto-refresh del dashboard no rompe la UI

- **Severidad**: MINOR (no automatizable sin browser)
- **Pasos**: SKIP automatización. Documentar como **MANUAL**: Diego debe abrir `/movimientos`, esperar 30s, verificar que la tabla se actualiza sin perder filtros.

### TC-L4-08 · Endpoint /audit devuelve eventos ordenados desc

- **Severidad**: MAJOR
- **Pasos**:
  ```bash
  curl -s "$BACKEND_URL/api/movements/$QA_MOV_ID/audit" -H "$H_AUTH" | python3 -c "
  import json, sys
  d = json.load(sys.stdin)
  events = d['events']
  ts = [e['created_at'] for e in events]
  assert ts == sorted(ts, reverse=True), f'no ordenado desc: {ts}'
  print(f'OK · {len(events)} events, orden desc')"
  ```

---

## 9. Plan de pruebas — Capa L5 · Edge & Regression (6 TCs)

### TC-L5-01 · Idempotencia: aprobar dos veces el mismo mov

- **Severidad**: CRITICAL
- **Pasos**:
  1. `V=$(get_version $QA_MOV_ID); curl -s -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/approve" -H "$H_AUTH" -H "$H_JSON" -d "{\"version\":$V}"`
  2. `curl -s -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/approve" -H "$H_AUTH" -H "$H_JSON" -d '{}'`  *(omito version para simular bot)*
  3. Verificar GSheet: solo UNA fila con ese MovementId.
- **Esperado**: ambos calls 200, una sola fila.

### TC-L5-02 · Transición inválida: aprobar un ya-ignored

- **Severidad**: MAJOR
- **Pasos**:
  1. Ignorá el mov: `V=$(get_version $QA_MOV_ID); curl -s -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/ignore" -H "$H_AUTH" -H "$H_JSON" -d "{\"version\":$V,\"reason\":\"qa\"}"`
  2. Intentá aprobarlo: `V=$(get_version $QA_MOV_ID); curl -s -o /tmp/qa-movimientos/tc-l5-02.json -w "%{http_code}\n" -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/approve" -H "$H_AUTH" -H "$H_JSON" -d "{\"version\":$V}"`
- **Esperado**: `422`, body con `error=invalid_transition`, `current=ignored`, `attempted=approve`.

### TC-L5-03 · Performance: listado de 200 movs <2s

- **Severidad**: MINOR
- **Pasos**:
  ```bash
  time curl -s -o /dev/null "$BACKEND_URL/api/movements?status=all&limit=200" -H "$H_AUTH"
  ```
- **Esperado**: real < 2.0s. Si > 5s, defect MAJOR.

### TC-L5-04 · Regresión: dashboard financiero KPIs no cambiaron

- **Severidad**: CRITICAL
- **Pasos**:
  ```bash
  curl -s "$DASHBOARD_URL/" -u ":$DASHBOARD_PASSWORD" | grep -oE "[0-9]+\$|[0-9]+ movimientos|alertas activas" | head -10
  ```
- **Esperado**: la página renderiza KPIs (números, conteos). Comparar con screenshot pre-feature si lo tenés. Si está vacío o blank, BLOCKER.

### TC-L5-05 · Tests automáticos del proyecto pasan

- **Severidad**: MAJOR
- **Pasos**:
  ```bash
  cd "/Users/diego/Desktop/Desarrollos DMN/Control de Gastos/Gestión de Gastos"
  .venv/bin/python -m pytest tests/ 2>&1 | tail -3
  ```
- **Esperado**: `33 passed` (o más si se agregaron tests).

### TC-L5-06 · TypeScript del dashboard sin errores

- **Severidad**: MAJOR
- **Pasos**:
  ```bash
  cd "/Users/diego/Desktop/Desarrollos DMN/Control de Gastos/Gestión de Gastos/dashboard" && npx tsc --noEmit 2>&1 | head -20
  ```
- **Esperado**: sin output (compilación limpia).

---

## 10. Cleanup post-QA

```bash
# Restituir QA_MOV_ID a pending
FINAL_STATE=$(curl -s "$BACKEND_URL/api/movements/$QA_MOV_ID" -H "$H_AUTH" | python3 -c "import json,sys; print(json.load(sys.stdin)['movement']['review_status'])")
if [ "$FINAL_STATE" != "pending" ]; then
  V=$(get_version $QA_MOV_ID)
  curl -s -X POST "$BACKEND_URL/api/movements/$QA_MOV_ID/reopen" \
    -H "$H_AUTH" -H "$H_JSON" \
    -d "{\"version\":$V,\"actor\":\"$QA_RUN_ID-cleanup\"}"
fi

# Borrar credenciales temporales
shred -uz /tmp/qa-movimientos/env 2>/dev/null || rm -f /tmp/qa-movimientos/env
```

> **Cualquier mov que tocaste y no quedó en su estado inicial debe quedar documentado** en el reporte como "residual state".

---

## 11. Formato de defecto (cuando un test falla)

Para CADA falla, completá este bloque:

```markdown
### DEF-<NN> · <título corto y específico>

- **Test ID**: TC-LX-NN
- **Severidad**: BLOCKER | CRITICAL | MAJOR | MINOR | TRIVIAL
- **Prioridad**: P0 | P1 | P2 | P3
- **Componente**: backend-api | frontend-dashboard | bot-telegram | gsheet | firestore | docs
- **Reproducción** (paso a paso, copy-paste-able):
  1. ...
  2. ...
- **Esperado**: <una frase>
- **Actual**: <una frase + http status / error message>
- **Evidencia**:
  ```
  <output exacto, request ID, timestamp UTC, cuerpo del response>
  ```
- **Hipótesis** (opcional): <causa raíz probable>
- **Workaround** (si existe): <cómo seguir trabajando hasta que se arregle>
```

### Tabla de severidades

| Severidad | Definición | Ejemplos |
|---|---|---|
| **BLOCKER** | Bloquea uso completo, sin workaround | API 500 sostenido, auth roto, secretos expuestos |
| **CRITICAL** | Falla funcional core, hay workaround feo | Aprobar no sincroniza a GSheet, doble fila duplicada |
| **MAJOR** | Bug claro, no bloquea | Filtro no respeta combinación, audit no registra una acción |
| **MINOR** | Cosmético o accesibilidad | Toast no aparece, tooltip mal escrito, color de badge erróneo |
| **TRIVIAL** | Nice-to-have | Falta logging extra, traducción imperfecta |

### Tabla de prioridades

| Prio | Significado |
|---|---|
| **P0** | Fix inmediato, parar release |
| **P1** | Fix antes del próximo deploy productivo |
| **P2** | Fix esta sprint |
| **P3** | Backlog, no urgente |

---

## 12. Reporte final (entregable obligatorio)

Al terminar, entregá UN solo markdown llamado `qa-report-<fecha>.md` con esta estructura:

```markdown
# QA Report · Feature Movimientos · <YYYY-MM-DD HH:MM TZ>

## Resumen ejecutivo

- **Run ID**: <QA_RUN_ID>
- **Duración**: <minutos>
- **Sistema bajo prueba**:
  - Backend: <BACKEND_URL>  (commit: <git rev-parse origin/main>)
  - Dashboard: <DASHBOARD_URL>
- **Mov de prueba**: <QA_MOV_ID>

| Capa | Total | Pass | Fail | Skip | Partial |
|---|---|---|---|---|---|
| L1 Smoke | 5 | X | X | X | X |
| L2 Auth | 4 | X | X | X | X |
| L3 Funcional | 18 | X | X | X | X |
| L4 Integración | 8 | X | X | X | X |
| L5 Edge & Regr | 6 | X | X | X | X |
| **TOTAL** | **41** | X | X | X | X |

**Veredicto**: ✅ APTO PARA PRODUCCIÓN | ⚠️ APTO CON RESERVAS | ❌ NO APTO

## Hallazgos por severidad

- BLOCKER: <count>
- CRITICAL: <count>
- MAJOR: <count>
- MINOR: <count>

## Detalle de cada test case

### TC-L1-01 — <título>
- **Resultado**: PASS | FAIL | SKIP | PARTIAL
- **Tiempo**: <s>
- **Notas**: <relevante si no fue PASS limpio>
[...]

## Defectos encontrados

[Pegá los bloques DEF-NN del §11 acá]

## Estado final del sistema

- Mov $QA_MOV_ID: review_status=<X>, sheet_sync_status=<Y>
- Filas residuales en GSheet creadas durante QA: <count> · IDs: [...]
- Cleanup completado: SÍ | NO (explicá qué quedó pendiente)

## Recomendaciones

[1-3 bullets accionables. Ejemplo: "Investigar TC-L3-09 manualmente con un GSheet de staging"]
```

---

## 13. Lo que NO debés hacer

- NO modifiques código de la feature.
- NO toques GSheet a mano (ni aunque "solo sea para verificar").
- NO ejecutes `scripts/extend_sheet_movement_id.py` ni `scripts/backfill_movement_status.py` (ya corrieron).
- NO uses producción para tests destructivos masivos. Si querés stress-test, pedí entorno staging.
- NO publiques tokens, passwords o IDs de chat en el reporte.
- NO marques PASS si el resultado es PARTIAL o ambiguo. Sé honesto.
- NO esperes confirmación entre TCs — si una capa pasa, seguí a la próxima sin pedir validación humana.

---

## 14. Glosario rápido

| Término | Definición |
|---|---|
| **Mov** | Documento Firestore en `movements`, una transacción bancaria |
| **Dual-write** | El servicio escribe `status` (legacy) y `review_status` (nuevo) simultáneamente |
| **Optimistic locking** | El cliente envía `version`, el servidor rechaza si no coincide (409) |
| **Sync** | Escritura del mov a Google Sheets |
| **Backfill** | Migración no destructiva de docs viejos al schema nuevo |
| **Capa L1-L5** | Niveles de testing: Smoke, Auth, Funcional, Integración, Edge & Regr |

---

**Fin del prompt.** Comenzá por la sección §3 (Precondiciones), luego §5 (L1 Smoke). Si todo pasa hasta L5, entregá el reporte de §12 y terminás. Si algo crítico falla, igualmente entregá el reporte parcial — no abortes silenciosamente.

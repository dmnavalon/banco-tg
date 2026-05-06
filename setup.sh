#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "═══════════════════════════════════════════════════════════"
echo "  banco-tg setup"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "  $PROJECT_DIR"
echo "═══════════════════════════════════════════════════════════"

# 1. venv
if [ ! -d ".venv" ]; then
  echo "→ Creando .venv (Python 3)…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# 2. dependencias
echo "→ Instalando dependencias Python…"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# 3. playwright chromium
echo "→ Instalando Chromium para Playwright…"
playwright install chromium

# 4. carpetas
mkdir -p data logs

# 5. inicializar DB
echo "→ Inicializando base de datos…"
python -c "from src.db import init_if_needed; init_if_needed()"

# 6. .env interactivo
if [ ! -f ".env" ]; then
  echo ""
  echo "─── Configuración inicial (.env) ───"
  read -r -p "TG_BOT_TOKEN: " TG_BOT_TOKEN
  read -r -p "TG_CHAT_ID: " TG_CHAT_ID
  read -r -p "ANTHROPIC_API_KEY: " ANTHROPIC_API_KEY
  cat > .env <<EOF
TG_BOT_TOKEN=$TG_BOT_TOKEN
TG_CHAT_ID=$TG_CHAT_ID
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
DRY_RUN=false
HEADLESS=false
LOG_LEVEL=INFO
DB_PATH=data/banco.db
EOF
  chmod 600 .env
  echo "✓ .env creado con permisos 0600."
else
  echo "→ .env ya existe, no se sobreescribe."
fi

# 7. plists con la ruta absoluta
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH_AGENTS"

for plist_name in com.diego.bancotg.daily.plist com.diego.bancotg.bot.plist; do
  src_plist="scripts/$plist_name"
  dst_plist="$LAUNCH_AGENTS/$plist_name"
  echo "→ Instalando $plist_name → $LAUNCH_AGENTS/"
  sed "s|PROJECT_DIR|$PROJECT_DIR|g" "$src_plist" > "$dst_plist"

  label="${plist_name%.plist}"
  launchctl unload "$dst_plist" 2>/dev/null || true
  launchctl load "$dst_plist"
done

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  ✓ Setup completo."
echo ""
echo "  Próximos pasos:"
echo "    1. Manda /start a tu bot en Telegram."
echo "    2. /cred falabella → wizard para guardar credenciales."
echo "    3. /cred bancochile → idem."
echo "    4. /test falabella  → primera ejecución (puede pedir OTP)."
echo ""
echo "  Logs en vivo:"
echo "    tail -f logs/bot.log"
echo "    tail -f logs/daily.log"
echo "═══════════════════════════════════════════════════════════"

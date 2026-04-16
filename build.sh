#!/usr/bin/env bash
# =============================================================================
#  build.sh — Build Autoclicker.app (Python 3.13, sin Apple Developer ID)
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'

info()    { echo -e "${BLUE}ℹ️  $*${NC}"; }
success() { echo -e "${GREEN}✅  $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠️   $*${NC}"; }
error()   { echo -e "${RED}❌  $*${NC}"; exit 1; }

APP_NAME="Autoclicker"
BUNDLE_ID="com.personal.autoclicker"
SPEC_FILE="Autoclicker.spec"
DIST_DIR="dist"
BUILD_DIR="build"
APP_PATH="${DIST_DIR}/${APP_NAME}.app"
LAUNCH_AGENT_PLIST="$HOME/Library/LaunchAgents/${BUNDLE_ID}.plist"

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║      Autoclicker — Build Script          ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# ─── 1. Verificar herramientas ───────────────────────────────────────────────
info "Verificando dependencias..."
command -v python3  >/dev/null 2>&1 || error "python3 no encontrado."
command -v codesign >/dev/null 2>&1 || error "codesign no encontrado. Ejecuta: xcode-select --install"

PYTHON=$(command -v python3)

$PYTHON -c "import PyInstaller" 2>/dev/null || { warn "Instalando PyInstaller..."; $PYTHON -m pip install pyinstaller; }
$PYTHON -c "import pynput"      2>/dev/null || { warn "Instalando pynput...";      $PYTHON -m pip install pynput; }
$PYTHON -c "import objc"        2>/dev/null || { warn "Instalando pyobjc...";       $PYTHON -m pip install pyobjc; }

success "Dependencias OK  (Python: $($PYTHON --version))"

# ─── 2. Descargar Launch Agent anterior si existe ────────────────────────────
if [ -f "$LAUNCH_AGENT_PLIST" ]; then
    info "Descargando Launch Agent anterior..."
    launchctl bootout "gui/$(id -u)/${BUNDLE_ID}" 2>/dev/null || \
    launchctl unload "$LAUNCH_AGENT_PLIST" 2>/dev/null || true
    rm -f "$LAUNCH_AGENT_PLIST"
    success "Launch Agent anterior eliminado"
fi

# ─── 3. Limpiar builds anteriores ───────────────────────────────────────────
info "Limpiando builds anteriores..."
rm -rf "$BUILD_DIR" "$DIST_DIR"
success "Limpieza OK"

# ─── 4. Compilar ────────────────────────────────────────────────────────────
info "Compilando con PyInstaller (modo onedir)..."
$PYTHON -m PyInstaller "$SPEC_FILE" --noconfirm
[ -d "$APP_PATH" ] || error "Compilación fallida: no se encontró $APP_PATH"
success "Compilación OK"

# ─── 5. Firma ad-hoc SIN Hardened Runtime ───────────────────────────────────
info "Firmando ad-hoc..."

INTERNAL_DIR="${APP_PATH}/Contents/MacOS/_internal"
if [ -d "$INTERNAL_DIR" ]; then
    find "$INTERNAL_DIR" \( -name "*.so" -o -name "*.dylib" \) | while read -r f; do
        codesign --force --sign "-" "$f" 2>/dev/null || true
    done
    find "$INTERNAL_DIR" -name "Python" -type f | while read -r f; do
        codesign --force --sign "-" "$f" 2>/dev/null || true
    done
fi

codesign --force --deep --sign "-" "$APP_PATH" 2>&1 | grep -v "replacing existing" || true
success "Firma ad-hoc OK"

# ─── 6. Quitar quarantine ────────────────────────────────────────────────────
info "Eliminando quarantine..."
xattr -rd com.apple.quarantine "$APP_PATH" 2>/dev/null || true
success "Quarantine eliminado"

# ─── 7. Limpiar TCC ──────────────────────────────────────────────────────────
info "Limpiando TCC (permisos de Accesibilidad anteriores)..."
TCC_USER_DB="$HOME/Library/Application Support/com.apple.TCC/TCC.db"
if [ -f "$TCC_USER_DB" ]; then
    if sqlite3 "$TCC_USER_DB" \
        "DELETE FROM access WHERE client='${BUNDLE_ID}' AND service='kTCCServiceAccessibility';" \
        2>/dev/null; then
        success "Entrada TCC borrada"
    else
        warn "No se pudo limpiar TCC (añade Terminal en Acceso total al disco si falla)"
    fi
fi

# ─── 8. Copiar a /Applications ───────────────────────────────────────────────
echo ""
read -rp "¿Copiar a /Applications/${APP_NAME}.app? [s/N]: " COPY_CHOICE
if [[ "${COPY_CHOICE,,}" == "s" ]]; then
    DEST="/Applications/${APP_NAME}.app"
    [ -d "$DEST" ] && rm -rf "$DEST"
    cp -R "$APP_PATH" /Applications/
    xattr -rd com.apple.quarantine "$DEST" 2>/dev/null || true
    success "Copiado a /Applications/${APP_NAME}.app"
fi

# ─── Resumen ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          Build completado ✅              ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Primera vez que abres la app:${NC}"
echo "  1. macOS pedirá permisos de Accesibilidad automáticamente"
echo "  2. Concédelos en Preferencias → Privacidad → Accesibilidad"
echo "  3. La app lo detecta sola en ≤2 segundos"
echo "  4. El Launch Agent se instala automáticamente — la app"
echo "     reaparecerá sola si el proceso muere o al reiniciar el Mac"
echo ""
echo -e "${YELLOW}Para desinstalar la persistencia sin abrir la app:${NC}"
echo "  launchctl unload ~/Library/LaunchAgents/${BUNDLE_ID}.plist"
echo "  rm ~/Library/LaunchAgents/${BUNDLE_ID}.plist"
echo ""
echo -e "${YELLOW}Si macOS bloquea la app:${NC}"
echo "  sudo xattr -rd com.apple.quarantine /Applications/${APP_NAME}.app"
echo ""
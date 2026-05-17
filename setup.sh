#!/usr/bin/env bash
# setup.sh — Enki ESP Environment first-time setup
# Installs Enki CLI and ESP-IDF v6.1 for ESP32 development on macOS.
#
# Usage:
#   chmod +x setup.sh && ./setup.sh
#
# After setup, start every session with:
#   source .venv/bin/activate
#   source esp-idf/export.sh

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}▸${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC} $*"; }
error()   { echo -e "${RED}✗ ERROR:${NC} $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}$*${NC}"; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IDF_DIR="$REPO_DIR/esp-idf"
IDF_VERSION="v6.1"
IDF_TARGET="esp32p4"
VENV_DIR="$REPO_DIR/.venv"

header "Enki ESP Environment — Setup"
echo "Repo:        $REPO_DIR"
echo "ESP-IDF:     $IDF_DIR ($IDF_VERSION)"
echo "Target:      $IDF_TARGET"
echo ""

# ── 1. Python ─────────────────────────────────────────────────────────────────
header "1/3  Python"
PYTHON=""
for candidate in python3.11 python3.12 python3.13 python3; do
    if command -v "$candidate" &>/dev/null; then
        VER=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        MAJOR=${VER%%.*}; MINOR=${VER##*.}
        if [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 9 ]; then
            PYTHON=$(command -v "$candidate")
            break
        fi
    fi
done
[ -z "$PYTHON" ] && error "Python 3.9+ not found. Install from https://python.org"
info "Using $("$PYTHON" --version) at $PYTHON"

# ── 2. Enki venv ──────────────────────────────────────────────────────────────
header "2/3  Enki"
if [ ! -d "$VENV_DIR" ]; then
    info "Creating virtual environment at .venv ..."
    "$PYTHON" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

info "Installing Enki and dependencies ..."
pip install -q --upgrade pip
pip install -q -e "$REPO_DIR"
success "Enki installed  →  $(enki --help 2>&1 | head -1)"

# ── 3. ESP-IDF ────────────────────────────────────────────────────────────────
header "3/3  ESP-IDF $IDF_VERSION"

if [ -n "${IDF_PATH:-}" ] && [ -f "$IDF_PATH/export.sh" ]; then
    EXISTING_VER=$(git -C "$IDF_PATH" describe --tags 2>/dev/null || echo "unknown")
    warn "ESP-IDF already active: IDF_PATH=$IDF_PATH  ($EXISTING_VER)"
    warn "Skipping ESP-IDF install. Make sure it is $IDF_VERSION for full compatibility."
    IDF_DIR="$IDF_PATH"

elif [ -f "$IDF_DIR/export.sh" ]; then
    EXISTING_VER=$(git -C "$IDF_DIR" describe --tags 2>/dev/null || echo "unknown")
    info "ESP-IDF found at esp-idf/  ($EXISTING_VER) — skipping clone."

else
    warn "ESP-IDF not found. Cloning $IDF_VERSION — this downloads ~1–2 GB and may take several minutes."
    echo ""
    git clone \
        --branch "$IDF_VERSION" \
        --depth 1 \
        --shallow-submodules \
        --recursive \
        https://github.com/espressif/esp-idf.git \
        "$IDF_DIR"

    info "Running ESP-IDF installer for target: $IDF_TARGET ..."
    "$IDF_DIR/install.sh" "$IDF_TARGET"
    success "ESP-IDF $IDF_VERSION installed at esp-idf/"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}Setup complete.${NC}"
echo ""
echo "Start every new terminal session with:"
echo ""
echo -e "  ${CYAN}source .venv/bin/activate${NC}"
echo -e "  ${CYAN}source esp-idf/export.sh${NC}   # activates idf.py, cmake, ninja, toolchains"
echo ""
echo "Then verify everything works:"
echo ""
echo -e "  ${CYAN}enki doctor${NC}"
echo ""

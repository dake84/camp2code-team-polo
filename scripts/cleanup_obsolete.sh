#!/usr/bin/env bash
# Entfernt die in DELETE_MANIFEST.txt aufgefuehrten, nachweislich obsoleten Dateien.
#
# Sicherheitsstufen:
#   1. Standard ist eine Vorschau (Dry-Run). Geloescht wird nur mit --apply.
#   2. Git-Pruefung: Ausfuehrung nur im Repository-Root; Dateien mit
#      uncommitteten Aenderungen werden nicht geloescht.
#   3. Referenzpruefung: Wird ein Dateiname noch in .py/.sh/.json-Dateien
#      erwaehnt, bricht das Skript ab.
#   4. Backup: Vor dem Loeschen wird ein tar.gz-Archiv ausserhalb des
#      Repositorys angelegt (Standard: ~/camp2code_backups/).
#
# Aufruf (im Repository-Root):
#   scripts/cleanup_obsolete.sh                 # nur Vorschau
#   scripts/cleanup_obsolete.sh --apply         # Backup + Loeschen
#   scripts/cleanup_obsolete.sh --apply --backup-dir /media/usb/backup
#
# Wiederherstellen:  tar -xzf <backup>.tar.gz   (im Repository-Root)
set -euo pipefail

MANIFEST="DELETE_MANIFEST.txt"
APPLY=0
BACKUP_DIR="${HOME}/camp2code_backups"
PROTECTED="BaseCar.py basisklassen.py basisklassen_cam.py Driving.py config.json pipeline_studio.json Camp2CodeDashboard.py car_application.py"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --manifest) MANIFEST="$2"; shift 2 ;;
    --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unbekannte Option: $1" >&2; exit 2 ;;
  esac
done

# --- Git-Pruefung -------------------------------------------------------------
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "FEHLER: Kein Git-Repository. Abbruch." >&2; exit 1
fi
REPO_ROOT="$(git rev-parse --show-toplevel)"
if [[ "$(pwd -P)" != "$(cd "$REPO_ROOT" && pwd -P)" ]]; then
  echo "FEHLER: Bitte im Repository-Root ausfuehren: cd $REPO_ROOT" >&2; exit 1
fi
if [[ ! -f "$MANIFEST" ]]; then
  echo "FEHLER: $MANIFEST nicht gefunden." >&2; exit 1
fi

# --- Manifest lesen -----------------------------------------------------------
mapfile -t ENTRIES < <(sed -e 's/#.*$//' -e 's/[[:space:]]*$//' -e 's/^[[:space:]]*//' "$MANIFEST" | grep -v '^$' || true)
if [[ ${#ENTRIES[@]} -eq 0 ]]; then
  echo "Manifest ist leer. Nichts zu tun."; exit 0
fi

TO_DELETE=()
BLOCKED=0
printf '%-48s %s\n' "DATEI" "STATUS"
printf '%-48s %s\n' "-----" "------"
for entry in "${ENTRIES[@]}"; do
  if [[ "$entry" == /* || "$entry" == *..* ]]; then
    printf '%-48s %s\n' "$entry" "ABGELEHNT (Pfad ausserhalb des Repositorys)"; BLOCKED=1; continue
  fi
  for p in $PROTECTED; do
    if [[ "$entry" == "$p" ]]; then
      printf '%-48s %s\n' "$entry" "ABGELEHNT (geschuetzte Datei)"; BLOCKED=1; continue 2
    fi
  done
  if [[ ! -e "$entry" ]]; then
    printf '%-48s %s\n' "$entry" "nicht vorhanden (bereits entfernt)"; continue
  fi
  if git ls-files --error-unmatch -- "$entry" >/dev/null 2>&1; then
    if ! git diff --quiet -- "$entry" || ! git diff --cached --quiet -- "$entry"; then
      printf '%-48s %s\n' "$entry" "ABGELEHNT (uncommittete Aenderungen)"; BLOCKED=1; continue
    fi
    state="versioniert -> git rm"
  else
    state="unversioniert -> rm"
  fi
  # Referenzpruefung: Dateiname in verbleibendem Code/Konfiguration erwaehnt?
  base="$(basename "$entry")"
  stem="${base%.*}"
  refs="$(grep -rIl --include='*.py' --include='*.sh' --include='*.json' \
           --exclude-dir=.git --exclude-dir=camp2code --exclude-dir=legacy \
           -e "$base" -e "import ${stem}\b" -e "from ${stem} import" . 2>/dev/null \
           | grep -v -x -F "./$entry" | grep -v 'cleanup_obsolete.sh' || true)"
  if [[ -n "$refs" ]]; then
    printf '%-48s %s\n' "$entry" "ABGELEHNT (noch referenziert in: $(echo "$refs" | tr '\n' ' '))"; BLOCKED=1; continue
  fi
  printf '%-48s %s\n' "$entry" "$state"
  TO_DELETE+=("$entry")
done

echo
if [[ $BLOCKED -ne 0 ]]; then
  echo "Mindestens ein Eintrag wurde abgelehnt. Bitte pruefen; es wird nichts geloescht." >&2
  exit 1
fi
if [[ ${#TO_DELETE[@]} -eq 0 ]]; then
  echo "Keine vorhandenen Dateien zu entfernen."; exit 0
fi
if [[ $APPLY -eq 0 ]]; then
  echo "VORSCHAU: ${#TO_DELETE[@]} Datei(en) wuerden entfernt. Ausfuehren mit: $0 --apply"
  exit 0
fi

# --- Backup -------------------------------------------------------------------
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP="$BACKUP_DIR/camp2code_cleanup_${STAMP}.tar.gz"
tar -czf "$BACKUP" -- "${TO_DELETE[@]}" "$MANIFEST"
if ! tar -tzf "$BACKUP" >/dev/null; then
  echo "FEHLER: Backup unlesbar ($BACKUP). Abbruch ohne Loeschen." >&2; exit 1
fi
echo "Backup erstellt: $BACKUP"

# --- Entfernen ----------------------------------------------------------------
for entry in "${TO_DELETE[@]}"; do
  if git ls-files --error-unmatch -- "$entry" >/dev/null 2>&1; then
    git rm --quiet -- "$entry"
  else
    rm -f -- "$entry"
  fi
  echo "entfernt: $entry"
done

echo
echo "Fertig. Pruefen und committen:"
echo "  python -m unittest discover -s tests -p 'test_*.py'"
echo "  git status --short && git commit -m 'Cleanup: obsolete Dateien entfernt (DELETE_MANIFEST)'"
echo "Wiederherstellen: tar -xzf $BACKUP"

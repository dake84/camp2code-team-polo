#!/usr/bin/env bash
set -Eeuo pipefail

# Camp2Code Deployment
# ZIP entpacken, testen, committen und Source exportieren.
#
# Aufruf:
#   ./scripts/deploy-package.sh ~/paket.zip

REPO="$HOME/camp2code-team-polo"
BACKUP_DIR="$HOME/camp2code_backups"
EXPORT="$HOME/camp2code-source-export.txt"

PACKAGE="${1:-}"

if [[ -z "$PACKAGE" || ! -f "$PACKAGE" ]]; then
    echo "Aufruf: $0 PAKET.zip"
    exit 2
fi

PACKAGE="$(realpath "$PACKAGE")"

cd "$REPO"

if [[ "$(git rev-parse --show-toplevel)" != "$PWD" ]]; then
    echo "FEHLER: Falsches Repository."
    exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
    echo "FEHLER: Git-Arbeitsbaum ist nicht sauber."
    git status --short
    echo "Bitte vorhandene Aenderungen zuerst sichern."
    exit 1
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
NAME="$(basename "$PACKAGE" .zip)"

mkdir -p "$BACKUP_DIR"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo
echo "========================================"
echo " Camp2Code Deployment"
echo "========================================"
echo "Paket: $PACKAGE"
echo "Branch: $(git branch --show-current)"
echo "Commit: $(git rev-parse --short HEAD)"
echo

# -------------------------------------------------
# 1. ZIP validieren und in temporaeres Verzeichnis
#    extrahieren.
# -------------------------------------------------

echo "[1/6] ZIP pruefen ..."

python3 - "$PACKAGE" "$TMP" <<'PY'
import pathlib
import stat
import sys
import zipfile

source = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])

with zipfile.ZipFile(source) as archive:
    if archive.testzip() is not None:
        raise SystemExit("ZIP ist beschaedigt")

    files = []
    seen = set()

    for item in archive.infolist():
        name = item.filename
        path = pathlib.PurePosixPath(name)

        if (
            not name
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or any(ord(c) < 32 for c in name)
        ):
            raise SystemExit(f"Ungueltiger Pfad: {name!r}")

        if item.is_dir():
            continue

        if name in seen:
            raise SystemExit(f"Doppelter Pfad: {name}")

        seen.add(name)

        mode = item.external_attr >> 16

        if stat.S_ISLNK(mode):
            raise SystemExit(f"Symlink verboten: {name}")

        if item.file_size > 5 * 1024 * 1024:
            raise SystemExit(f"Datei zu gross: {name}")

        if name.startswith(".git/"):
            raise SystemExit("Git-Metadaten im Paket verboten")

        target = pathlib.Path.cwd() / name

        if target.is_symlink() or any(
            parent.is_symlink()
            for parent in target.parents
            if parent != pathlib.Path("/")
        ):
            raise SystemExit(f"Symlink-Ziel verboten: {name}")

        files.append(item)

    if not files:
        raise SystemExit("Paket enthaelt keine Dateien")

    for item in files:
        target = destination / item.filename
        target.parent.mkdir(parents=True, exist_ok=True)

        with archive.open(item) as src:
            target.write_bytes(src.read())

print(f"ZIP validiert: {len(files)} Dateien")
PY

# -------------------------------------------------
# 2. Backup
# -------------------------------------------------

echo "[2/6] Backup erstellen ..."

BACKUP="$BACKUP_DIR/${NAME}_${STAMP}.tar.gz"

python3 - "$TMP" "$BACKUP" <<'PY'
import pathlib
import sys
import tarfile

source = pathlib.Path(sys.argv[1])
backup = pathlib.Path(sys.argv[2])

with tarfile.open(backup, "w:gz") as archive:
    for file in source.rglob("*"):
        if not file.is_file():
            continue

        relative = file.relative_to(source)
        original = pathlib.Path.cwd() / relative

        if original.is_file():
            archive.add(original, arcname=str(relative))

print(f"Backup: {backup}")
PY

# -------------------------------------------------
# 3. Dateien installieren
# -------------------------------------------------

echo "[3/6] Dateien installieren ..."

python3 - "$TMP" <<'PY'
import pathlib
import shutil
import sys

source = pathlib.Path(sys.argv[1])
root = pathlib.Path.cwd()

for file in sorted(source.rglob("*")):
    if not file.is_file():
        continue

    relative = file.relative_to(source)
    target = root / relative

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file, target)

    print(f"  {relative}")
PY

# -------------------------------------------------
# 4. Tests
# -------------------------------------------------

echo "[4/6] Python-Syntax pruefen ..."

python3 -m compileall -q \
    Camp2CodeDashboard.py \
    car_application.py \
    CameraCar.py \
    CameraSensor.py \
    driving \
    models \
    telemetry \
    tests

echo "Unit-Tests ausfuehren ..."

python3 -m unittest discover \
    -s tests \
    -p 'test_*.py' \
    -v

echo "Tests erfolgreich."

# -------------------------------------------------
# 5. Git Commit
# -------------------------------------------------

echo "[5/6] Git Commit ..."

# Nur Dateien aus dem Paket stagen.
while IFS= read -r -d '' file; do
    relative="${file#"$TMP"/}"
    git add -- "$relative"
done < <(find "$TMP" -type f -print0)

if git diff --cached --quiet; then
    echo "Keine neuen Aenderungen zum Committen."
else
    git commit -m "deploy: ${NAME}"
fi

# -------------------------------------------------
# 6. Source-Export
# -------------------------------------------------

echo "[6/6] Source-Export erstellen ..."

if [[ ! -f scripts/export-source.sh ]]; then
    echo "FEHLER: scripts/export-source.sh fehlt."
    exit 1
fi

bash scripts/export-source.sh "$EXPORT"

echo
echo "========================================"
echo " Deployment abgeschlossen"
echo "========================================"
echo
echo "Commit:"
git log -1 --oneline

echo
echo "Source-Export:"
ls -lh "$EXPORT"

echo
echo "Git-Status:"
git status --short

echo
echo "FERTIG!"
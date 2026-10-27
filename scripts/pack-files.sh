#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    cat <<'HELP'
Camp2Code Source Packager

Verwendung:
  pack-files.sh AUSGABE.txt [DATEI ...]
  pack-files.sh AUSGABE.txt --all

Optionen:
  --all       Alle relevanten Projektdateien rekursiv packen
  --help      Hilfe anzeigen

Beispiele:
  ./scripts/pack-files.sh camp2code-full-source.txt --all
  ./scripts/pack-files.sh source.txt BaseCar.py driving/base.py

Bei --all werden zusätzlich Git-Metadaten exportiert.

Ausgeschlossen werden:
  .git
  .venv
  venv
  __pycache__
  node_modules
  build
  dist
  generierte Daten und Caches
HELP
}

if (( $# < 1 )); then
    usage >&2
    exit 2
fi

if [[ "$1" == "--help" || "$1" == "-h" ]]; then
    usage
    exit 0
fi

OUTPUT="$1"
shift

if ! command -v python3 >/dev/null 2>&1; then
    echo "Fehler: python3 fehlt" >&2
    exit 1
fi

python3 - "$OUTPUT" "$@" <<'PY'

from pathlib import Path
import os
import sys
import subprocess
import fnmatch

# --------------------------------------------------
# Argumente
# --------------------------------------------------

output = Path(sys.argv[1])
arguments = sys.argv[2:]

all_mode = arguments == ["--all"]

if "--all" in arguments and not all_mode:
    sys.exit(
        "Fehler: --all darf nicht mit Dateinamen "
        "kombiniert werden."
    )

# Ohne Dateiliste ebenfalls alle Dateien packen.
if not arguments:
    all_mode = True

# --------------------------------------------------
# Konfiguration
# --------------------------------------------------

excluded_dirs = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".nox",
    "node_modules",
    "build",
    "dist",
    ".idea",
    ".vscode",
    ".unpack-backups",
    ".backup-paket-a",
    "Data_AI",
    "data",
    "logs",
}

allowed_extensions = {
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".css",
    ".js",
    ".html",
    ".md",
    ".ini",
    ".cfg",
}

allowed_patterns = {
    "requirements*.txt",
    "Dockerfile",
    "Makefile",
    ".gitignore",
}

resource_extensions = {
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".css",
    ".js",
    ".html",
    ".ini",
    ".cfg",
}

# Einzelne sehr große Dateien nicht versehentlich
# in den Text-Export übernehmen.
MAX_FILE_SIZE = 2 * 1024 * 1024

# --------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------

def is_allowed(path: Path) -> bool:

    if path.suffix.lower() in allowed_extensions:
        return True

    return any(
        fnmatch.fnmatch(path.name, pattern)
        for pattern in allowed_patterns
    )


def git_command(*args):

    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    if result.returncode != 0:
        return None

    return result.stdout


# --------------------------------------------------
# Zieldatei prüfen
# --------------------------------------------------

if output.exists():
    sys.exit(
        f"Fehler: Zieldatei existiert bereits: {output}"
    )

output.parent.mkdir(
    parents=True,
    exist_ok=True,
)

# --------------------------------------------------
# Dateien sammeln
# --------------------------------------------------

paths = []

if all_mode:

    for root, dirs, files in os.walk(
        ".",
        followlinks=False,
    ):

        dirs[:] = sorted(
            directory
            for directory in dirs
            if directory not in excluded_dirs
            and not directory.startswith(".")
        )

        for filename in sorted(files):

            path = Path(root) / filename

            if is_allowed(path):
                paths.append(path)

else:

    paths = [
        Path(argument)
        for argument in arguments
    ]

# --------------------------------------------------
# Dateien prüfen und einlesen
# --------------------------------------------------

entries = []
seen = set()
skipped = []

for path in paths:

    if (
        path.is_absolute()
        or ".." in path.parts
        or path.is_symlink()
        or not path.is_file()
    ):
        sys.exit(
            f"Ungueltiger Dateipfad: {path}"
        )

    name = path.as_posix()

    while name.startswith("./"):
        name = name[2:]

    if name in seen:
        sys.exit(
            f"Doppelter Dateiname: {name}"
        )

    seen.add(name)

    if path.stat().st_size > MAX_FILE_SIZE:

        skipped.append(
            (name, "Datei zu gross")
        )

        continue

    try:

        raw = path.read_bytes()

        if b"\x00" in raw:

            skipped.append(
                (name, "Binaerdatei")
            )

            continue

        text = raw.decode("utf-8")

    except UnicodeDecodeError:

        skipped.append(
            (name, "Nicht UTF-8")
        )

        continue

    if not text.endswith("\n"):
        text += "\n"

    entries.append(
        (name, text)
    )

if not entries:
    sys.exit(
        "Keine geeigneten Dateien gefunden."
    )

# --------------------------------------------------
# Quelldateien exportieren
# --------------------------------------------------

with output.open(
    "x",
    encoding="utf-8",
    newline="\n",
) as stream:

    for name, text in sorted(entries):

        stream.write(
            "\n" + "=" * 80 + "\n"
        )

        stream.write(
            f"FILE: {name}\n"
        )

        stream.write(
            "=" * 80 + "\n\n"
        )

        stream.write(text)

        stream.write(
            "\n" + "=" * 80 + "\n"
        )

        stream.write(
            f"END FILE: {name}\n"
        )

        stream.write(
            "=" * 80 + "\n"
        )

# --------------------------------------------------
# Git-Metadaten exportieren
# --------------------------------------------------

if all_mode:

    output_dir = output.parent

    git_status = git_command(
        "status",
        "--short",
    )

    git_files = git_command(
        "ls-files",
    )

    if git_status is not None and git_files is not None:

        metadata = {
            "camp2code-git-status.txt": git_status,
            "camp2code-file-list.txt": git_files,
        }

        resource_files = [
            name
            for name, _ in entries
            if (
                Path(name).suffix.lower()
                in resource_extensions
                or fnmatch.fnmatch(
                    Path(name).name,
                    "requirements*.txt",
                )
            )
        ]

        metadata[
            "camp2code-resources.txt"
        ] = "\n".join(
            sorted(resource_files)
        ) + "\n"

        for filename, content in metadata.items():

            target = output_dir / filename

            if target.exists():
                print(
                    f"Warnung: {target} existiert bereits, "
                    "wird nicht überschrieben."
                )
                continue

            target.write_text(
                content,
                encoding="utf-8",
            )

    else:

        print(
            "Warnung: Git-Metadaten konnten "
            "nicht vollständig gelesen werden."
        )

# --------------------------------------------------
# Zusammenfassung
# --------------------------------------------------

size = output.stat().st_size

print()
print("==========================================")
print(" Camp2Code Project Export")
print("==========================================")
print(f"Ausgabe:       {output}")
print(f"Dateien:       {len(entries)}")
print(f"Groesse:       {size / 1024:.1f} KiB")
print(f"Uebersprungen: {len(skipped)}")
print("==========================================")

if skipped:

    print()
    print("Uebersprungene Dateien:")

    for name, reason in skipped:
        print(f"  {name}: {reason}")

print()
print("Export erfolgreich!")

if all_mode:
    print("Git-Metadaten wurden, soweit verfügbar,")
    print("im Ausgabeverzeichnis erstellt.")

PY
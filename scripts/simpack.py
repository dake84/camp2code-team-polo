#!/usr/bin/env python3

from pathlib import Path
from datetime import datetime


ROOT = Path(__file__).resolve().parent.parent

OUT = Path.home() / (
    f'c2c-simpack-{datetime.now():%Y%m%d_%H%M%S}.txt'
)

# Explizit relevante Kerndateien.
FILES = [
    'car_application.py',
    'offline_application.py',
    'CameraSensor.py',
    'LenkwinkelKalkulator.py',
    'models.py',
    'config_store.py',
    'speed_policy.py',
    'mode_registry.py',
    'event_log.py',
    'help_texts.py',
    'requirements.txt',
    'pyproject.toml',
]

# Verzeichnisse, deren Python-Dateien für die Architektur relevant sind.
DIRECTORIES = [
    'dashboard',
    'driving',
]

# Zusätzlich anhand des Dateinamens aufnehmen.
KEYWORDS = (
    'simulation',
    'replay',
    'record',
    'session',
    'frame',
    'pipeline',
    'processor',
    'lane',
)

# Relevante Tests dienen dem Implementer hauptsächlich als Vertrag.
TEST_KEYWORDS = (
    'simulation',
    'replay',
    'record',
    'session',
    'paket_f',
    'paket_g',
    'paket_h1',
    'paket_h2',
    'paket_h3',
)

SKIP_DIRS = {
    '.git',
    '.venv',
    'venv',
    '__pycache__',
    '.pytest_cache',
    '.unpack-backups',
    'Data_AI',
    'legacy',
    'node_modules',
    'scripts',
}

MAX_SIZE = 250_000


def allowed(path: Path) -> bool:
    if not path.is_file():
        return False

    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return False

    if any(part in SKIP_DIRS for part in rel.parts[:-1]):
        return False

    if path.stat().st_size > MAX_SIZE:
        print(
            f'  SKIP gross: {rel} '
            f'({path.stat().st_size // 1024} KiB)'
        )
        return False

    return True


selected = set()

# 1. Feste Kerndateien.
for name in FILES:
    path = ROOT / name
    if allowed(path):
        selected.add(path)


# 2. Dashboard + Driving.
for directory in DIRECTORIES:
    base = ROOT / directory

    if not base.exists():
        continue

    for path in base.rglob('*.py'):
        if allowed(path):
            selected.add(path)


# 3. Relevante Python-Dateien im Projekt.
for path in ROOT.rglob('*.py'):
    if not allowed(path):
        continue

    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        continue

    # Tests behandeln wir separat.
    if rel.parts and rel.parts[0] == 'tests':
        continue

    name = path.name.lower()

    if any(keyword in name for keyword in KEYWORDS):
        selected.add(path)


# 4. Nur relevante Tests.
tests = ROOT / 'tests'

if tests.exists():
    for path in tests.glob('test_*.py'):
        if not allowed(path):
            continue

        name = path.name.lower()

        if any(keyword in name for keyword in TEST_KEYWORDS):
            selected.add(path)


selected = sorted(
    selected,
    key=lambda p: p.relative_to(ROOT).as_posix(),
)

if not selected:
    raise SystemExit('Keine passenden Dateien gefunden.')


DELIM = '=' * 80


with OUT.open(
    'w',
    encoding='utf-8',
    newline='\n',
) as target:

    for path in selected:
        rel = path.relative_to(ROOT).as_posix()

        data = path.read_bytes()

        if b'\x00' in data:
            print(f'  SKIP binaer: {rel}')
            continue

        text = data.decode(
            'utf-8',
            errors='replace',
        )

        target.write(DELIM + '\n')
        target.write(f'FILE: {rel}\n')
        target.write(DELIM + '\n\n')

        target.write(text)

        if not text.endswith('\n'):
            target.write('\n')

        target.write(DELIM + '\n')
        target.write(f'END FILE: {rel}\n')
        target.write(DELIM + '\n')


print()
print('SIMPACK FERTIG')
print('==============')
print(f'Datei:   {OUT}')
print(f'Groesse: {OUT.stat().st_size // 1024} KiB')
print(f'Dateien: {len(selected)}')
print()

for path in selected:
    print('  ' + path.relative_to(ROOT).as_posix())

print()
print('Zum Kopieren:')
print(
    f'scp dake84@raspberrypi:{OUT} .'
)

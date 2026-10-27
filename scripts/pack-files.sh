#!/usr/bin/env bash
set -Eeuo pipefail
if (( $# < 2 )); then
  echo "Aufruf: $0 AUSGABE.txt DATEI.py [WEITERE_DATEIEN ...]" >&2; exit 2
fi
output=$1; shift
command -v python3 >/dev/null || { echo 'python3 fehlt' >&2; exit 1; }
python3 - "$output" "$@" <<'PY'
import pathlib, sys
out=pathlib.Path(sys.argv[1]); paths=sys.argv[2:]
if not paths: sys.exit('Keine Dateien angegeben')
entries=[]; seen=set()
for raw in paths:
    p=pathlib.Path(raw)
    if p.is_absolute() or '..' in p.parts or not p.is_file() or p.is_symlink():
        sys.exit(f'Ungueltiger Dateipfad: {raw}')
    name=p.as_posix()
    if name in seen: sys.exit(f'Doppelter Dateiname: {name}')
    seen.add(name)
    data=p.read_bytes()
    if b'\x00' in data: sys.exit(f'Keine Textdatei: {name}')
    text=data.decode('utf-8')
    if not text.endswith('\n'): text+='\n'
    entries.append((name,text))
if out.exists(): sys.exit(f'Zieldatei existiert bereits: {out}')
with out.open('x',encoding='utf-8',newline='\n') as f:
    for name,text in entries:
        f.write('\n'+'='*80+'\nFILE: '+name+'\n'+'='*80+'\n\n')
        f.write(text)
        f.write('\n'+'='*80+'\nEND FILE: '+name+'\n'+'='*80+'\n')
print(f'Erstellt: {out} ({len(entries)} Dateien)')
PY

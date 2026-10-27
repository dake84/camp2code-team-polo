#!/usr/bin/env bash
set -Eeuo pipefail
usage() { echo "Aufruf: $0 QUELLE.txt [--apply]" >&2; echo 'Ohne --apply nur Pruefung und Vorschau.' >&2; }
(( $# >= 1 && $# <= 2 )) || { usage; exit 2; }
source_file=$1
mode=${2:-}
[[ -f "$source_file" ]] || { echo "Datei fehlt: $source_file" >&2; exit 1; }
[[ -z "$mode" || "$mode" == '--apply' ]] || { usage; exit 2; }
command -v git >/dev/null && command -v python3 >/dev/null || { echo 'git und python3 erforderlich' >&2; exit 1; }
[[ -d .git || -n "$(git rev-parse --show-toplevel 2>/dev/null || true)" ]] || { echo 'Kein Git-Repository' >&2; exit 1; }
repo=$(git rev-parse --show-toplevel)
[[ "$PWD" == "$repo" ]] || { echo "Bitte im Repository-Root starten: $repo" >&2; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || { echo 'Arbeitsbaum nicht sauber. Erst committen/stashen; Abbruch.' >&2; exit 1; }
[[ -z "$(git ls-files -u)" ]] || { echo 'Offene Merge-Konflikte; Abbruch.' >&2; exit 1; }
[[ -n "$(git log -1 --format=%H 2>/dev/null || true)" ]] || { echo 'Repository ohne Commit; Abbruch.' >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
python3 - "$source_file" "$tmp" <<'PY'
import pathlib,re,sys,py_compile
src=pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
root=pathlib.Path(sys.argv[2]); delim='='*80
pattern=re.compile(r'(?m)^'+delim+r'\nFILE: ([^\n]+)\n'+delim+r'\n(.*?)^'+delim+r'\nEND FILE: ([^\n]+)\n'+delim+r'(?:\n|\Z)',re.S|re.M)
found=list(pattern.finditer(src))
if not found: sys.exit('Keine FILE/END FILE-Bloecke erkannt')
remaining=pattern.sub('',src)
if remaining.strip(): sys.exit('Nicht interpretierbarer Inhalt ausserhalb der Bloecke; Abbruch')
seen=set()
for match in found:
    name,content,end=match.groups()
    path=pathlib.PurePosixPath(name)
    if (name != end or name in seen or not name or path.is_absolute() or
        '..' in path.parts or '\\' in name or path.as_posix()!=name or
        any(part in ('','.','..') for part in name.split('/')) or
        any(ord(c)<32 for c in name)):
        sys.exit(f'Ungueltiger/duplizierter Pfad oder Marker: {name!r}')
    seen.add(name)
    target=pathlib.Path.cwd()/name
    if target.is_symlink() or any(p.is_symlink() for p in target.parents if p!=pathlib.Path('/')):
        sys.exit(f'Symlink-Pfad nicht erlaubt: {name}')
    if target.exists() and not target.is_file(): sys.exit(f'Ziel ist keine Datei: {name}')
    data=content.lstrip('\n')
    # Ein einzelner Trenner-Zeilenumbruch wird entfernt.
    if data.endswith('\n\n'): data=data[:-1]
    dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(data,encoding='utf-8')
    if name.endswith('.py'):
        try: compile(data,name,'exec')
        except SyntaxError as e: sys.exit(f'Syntaxfehler in {name}: {e}')
print(f'Validiert: {len(found)} Dateien')
for name in sorted(seen): print('  '+name)
PY
if [[ "$mode" != '--apply' ]]; then
  echo 'Vorschau erfolgreich. Zum Ueberschreiben --apply angeben.'
  exit 0
fi
# Expliziter Git-Sicherungspunkt vor jeder Aenderung.
git -c user.name="$(git config user.name)" -c user.email="$(git config user.email)" commit --allow-empty -m "Checkpoint vor Import: $(basename "$source_file")"
backup="$repo/.unpack-backups/$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$backup"
# Kopiere nur gepruefte Dateien; alte Versionen sind im Git-Commit enthalten.
while IFS= read -r -d '' file; do
  relative=${file#"$tmp"/}
  target="$repo/$relative"
  if [[ -f "$target" ]]; then
    mkdir -p "$backup/$(dirname "$relative")"
    cp -p -- "$target" "$backup/$relative"
  fi
done < <(find "$tmp" -type f -print0)
while IFS= read -r -d '' file; do
  relative=${file#"$tmp"/}
  mkdir -p -- "$repo/$(dirname "$relative")"
  cp -- "$file" "$repo/$relative"
done < <(find "$tmp" -type f -print0)
echo "Import abgeschlossen. Backup vorhandener Dateien: $backup"
echo 'Aenderungen pruefen: git diff --stat && git status --short'

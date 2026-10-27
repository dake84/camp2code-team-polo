#!/usr/bin/env python3
# Bigpack v3: nur Lesen, keine Tests, kein Import von Projektcode. Option: --split
import subprocess, sys
from pathlib import Path
from datetime import datetime

EXT = {'.py','.md','.json','.yaml','.yml','.toml','.cfg','.ini','.css','.js','.html','.sh'}
SKIP = {'.git','.venv','venv','__pycache__','legacy','camp2code','Data_AI','node_modules','.pytest_cache','.unpack-backups','pipeline_presets'}
PKG = ('dash','plotly','flask','werkzeug','numpy','opencv','picamera2','pandas','psutil','pytest','simplejpeg','gpiozero')

def sh(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 'TIMEOUT: ' + cmd

out = Path.home() / f'c2c-bigpack-{datetime.now():%Y%m%d_%H%M%S}.txt'
f = out.open('w', encoding='utf-8', newline='\n')

def section(title, body):
    print('  ->', title, flush=True)
    f.write('\n' + '#'*80 + '\n# ' + title + '\n' + '#'*80 + '\n' + body.rstrip() + '\n')
    f.flush()

section('SYSTEM', sys.version + '\n' + sh('uname -a') + '\n' + sh('free -h'))
section('GIT', sh('git branch --show-current') + '\n' + sh('git log --oneline -10')
        + '\n--- status ---\n' + sh('git status --short') + '\n--- diff ---\n' + sh('git diff'))
pip = [l for l in sh('pip freeze').splitlines() if l.lower().startswith(PKG)]
section('PIP', '\n'.join(pip))
section('CV2', sh('python -c "import cv2; print(cv2.__version__, cv2.__file__)"'))
section('GIT-DATEIEN', sh('git ls-files'))

sd = Path('Data_AI/sessions')
sess = sorted(sd.glob('session_*')) if sd.exists() else []
if sess:
    s = sess[-1]
    parts = []
    for fn, n in (('manifest.json', 150), ('frames.csv', 20), ('events.csv', 40)):
        p = s / fn
        if p.exists():
            with p.open(encoding='utf-8', errors='replace') as h:
                parts.append('--- ' + fn + ' ---\n' + ''.join(h.readline() for _ in range(n)))
    section('SESSION ' + s.name, '\n'.join(parts))

print('  -> QUELLCODE', flush=True)
n, skipped = 0, []
for name in sorted(set(sh('git ls-files --cached --others --exclude-standard').splitlines())):
    p = Path(name)
    if not p.is_file() or any(d in SKIP for d in p.parts[:-1]):
        continue
    if p.suffix not in EXT and not (p.name.startswith('requirements') and p.suffix == '.txt'):
        continue
    if p.stat().st_size > 250_000:
        skipped.append(name); continue
    data = p.read_bytes()
    if b'\x00' in data:
        skipped.append(name); continue
    t = data.decode('utf-8', errors='replace')
    f.write('\n' + '='*80 + '\nFILE: ' + name + '\n' + '='*80 + '\n\n' + t + ('' if t.endswith('\n') else '\n'))
    f.write('='*80 + '\nEND FILE: ' + name + '\n' + '='*80 + '\n')
    n += 1
section('UEBERSPRUNGEN', '\n'.join(skipped) or '-')
f.write('\n#### BIGPACK ENDE ####\n')
f.close()
print(f'FERTIG: {out}  ({n} Dateien, {out.stat().st_size // 1024} KiB)')

if '--split' in sys.argv:
    part, buf, size = 1, [], 0
    def flush_part():
        global part, buf, size
        pf = out.with_name(f'{out.stem}.part{part:02d}.txt')
        pf.write_text(f'### TEIL {part} ###\n' + ''.join(buf), encoding='utf-8')
        print('  ', pf.name, pf.stat().st_size // 1024, 'KiB')
        part, buf, size = part + 1, [], 0
    for line in out.read_text(encoding='utf-8').splitlines(keepends=True):
        if size + len(line) > 90_000 and buf:
            flush_part()
        buf.append(line); size += len(line)
    if buf:
        flush_part()

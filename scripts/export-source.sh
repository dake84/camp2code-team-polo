#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

OUTPUT="${1:-$HOME/camp2code-source-export.txt}"

case "$OUTPUT" in
    /*) ;;
    *) OUTPUT="$PWD/$OUTPUT" ;;
esac

echo "Erstelle Quellcode-Export: $OUTPUT"

{
    echo "CAMP2CODE SOURCE EXPORT"
    echo "======================="
    echo
    echo "Exportzeitpunkt: $(date -Iseconds)"
    echo "Git-Branch: $(git branch --show-current)"
    echo "Git-Commit: $(git rev-parse HEAD)"
    echo

    echo "======================="
    echo "GIT STATUS"
    echo "======================="
    git status --short
    echo

    echo "======================="
    echo "LETZTE COMMITS"
    echo "======================="
    git log -10 --format='%h %ad %s' --date=short
    echo

    echo "======================="
    echo "DATEILISTE"
    echo "======================="

    git ls-files --cached --others --exclude-standard \
        | sort \
        | grep -E '\.(py|json|html|css|js|md|txt|sh|toml|yaml|yml|ini|cfg)$' \
        | grep -Ev '(^|/)(\.venv|venv|__pycache__|node_modules|\.git)/' \
        | grep -Ev '(^|/)(logs|recordings|datasets|fig_raw|fig_processed)/' \
        | grep -Ev '(^|/)(config\.json|\.env|secrets?\.|credentials?\.|.*\.key$|.*\.pem$)' \
        | while IFS= read -r file; do
            [ -f "$file" ] || continue

            # Keine Binärdateien oder sehr großen Einzeldateien exportieren.
            [ "$(stat -c%s "$file")" -le 500000 ] || continue
            grep -Iq . "$file" || continue

            printf '%s\n' "$file"
        done

    echo

    git ls-files --cached --others --exclude-standard \
        | sort \
        | grep -E '\.(py|json|html|css|js|md|txt|sh|toml|yaml|yml|ini|cfg)$' \
        | grep -Ev '(^|/)(\.venv|venv|__pycache__|node_modules|\.git)/' \
        | grep -Ev '(^|/)(logs|recordings|datasets|fig_raw|fig_processed)/' \
        | grep -Ev '(^|/)(config\.json|\.env|secrets?\.|credentials?\.|.*\.key$|.*\.pem$)' \
        | while IFS= read -r file; do
            [ -f "$file" ] || continue
            [ "$(stat -c%s "$file")" -le 500000 ] || continue
            grep -Iq . "$file" || continue

            echo
            echo "================================================================================"
            echo "FILE: $file"
            echo "================================================================================"

            cat "$file"

            echo
            echo "================================================================================"
            echo "END FILE: $file"
            echo "================================================================================"
        done

} > "$OUTPUT"

echo
echo "Export abgeschlossen."
ls -lh "$OUTPUT"

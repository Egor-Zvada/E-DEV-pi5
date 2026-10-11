#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
bash -n install.sh update.sh scripts/deploy.sh assets/completion
python3 - <<'PY'
import pathlib
for folder in ('src', 'scripts', 'tests'):
    for path in pathlib.Path(folder).iterdir():
        if path.is_file() and (path.suffix == '.py' or folder == 'src'):
            compile(path.read_text(), str(path), 'exec')
print('Python/Bash syntax: OK')
PY
python3 -m unittest discover -s tests -v
python3 scripts/check_secrets.py
python3 scripts/build_wiki.py
git diff --check

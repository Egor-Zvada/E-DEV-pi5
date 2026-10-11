#!/usr/bin/env python3
"""Check tracked/staged file content; never print the matched credential."""
import pathlib
import re
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[1]
paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
patterns = [r'(?mi)^\s*(?:PrivateKey|PresharedKey|HeaderProtectionKey)\s*=\s*[A-Za-z0-9+/]{43}=',
            r'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----',
            r'\bgh[pousr]_[A-Za-z0-9]{30,}\b', r'\bgithub_pat_[A-Za-z0-9_]{30,}\b']
errors = []
for relative in filter(None, paths):
    path = root/relative
    if path.suffix in ('.key', '.pfx', '.p12', '.pem', '.sqlite3', '.db', '.crt') or path.name == '.env':
        errors.append(relative+': private file type')
        continue
    content = path.read_text()
    if any(re.search(pattern, content) for pattern in patterns):
        errors.append(relative+': possible credential')
if errors:
    print('\n'.join(errors), file=sys.stderr)
    sys.exit(1)
print('Tracked files: no private certificates, database or recognized credentials.')

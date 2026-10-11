#!/usr/bin/env python3
"""Build the combined guide and man page from the installed chapter sources."""
from pathlib import Path

root=Path(__file__).resolve().parents[1]
chapters=['index','overview','lan','out','ap','hybrid','vpn','access','panels','containers','files','troubleshoot']
sections={name:(root/'docs/wiki'/(name+'.txt')).read_text() for name in chapters}
manual='# E-DEV 1.0 — инструкция\n\n'
manual+='\n\n'.join('## '+text.splitlines()[0]+'\n\n```text\n'+text.rstrip()+'\n```' for text in sections.values())+'\n'
(root/'docs/COMMANDS.md').write_text(manual)
roff='.TH E-DEV 1 "2026-10-12" "E-DEV 1.0" "User Commands"\n.SH NAME\ne-dev \\- домашний dev-сервер и маршрутизатор\n.SH SYNOPSIS\n.B e-dev settings\n.br\n.B e-dev manuals\n.br\n.B e-dev help\n'
for name,text in sections.items():
    escaped=text.replace('\\','\\e').replace('-','\\-')
    escaped='\n'.join('\\&'+line if line.startswith(('.',"'")) else line for line in escaped.splitlines())
    roff+='\n.SH '+name.upper()+'\n.nf\n'+escaped+'\n.fi\n'
(root/'docs/e-dev.1').write_text(roff)

#!/usr/bin/env python3
"""Initialize the pinned LXConsole in its working directory; password via stdin."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path.cwd()))
from lxconsole import app, db, bcrypt
from lxconsole.models import User, UserGroup, Group, Server

username = sys.argv[1]
password = sys.stdin.read()
if len(password) < 8:
    raise SystemExit('Нужен пароль минимум 8 символов')
with app.app_context():
    # The upstream first-visit handler seeds groups, ACLs and image registries.
    app.test_client().get('/')
    user = User.query.filter_by(username=username).first()
    if user is None:
        user = User(username=username, password='pending')
        db.session.add(user)
        db.session.flush()
    user.password = bcrypt.generate_password_hash(password).decode()
    administrators = Group.query.filter_by(name='Administrators').one()
    if not UserGroup.query.filter_by(user_id=user.id, group_id=administrators.id).first():
        db.session.add(UserGroup(user_id=user.id, group_id=administrators.id))
    if not Server.query.filter_by(addr='127.0.0.1', port=8443).first():
        # TLS verification disabled only for the local Incus self-signed endpoint.
        db.session.add(Server(addr='127.0.0.1', port=8443, name='E-DEV',
                              proxy='', ssl_verify=False, user_id=0))
    db.session.commit()
print('LXConsole: администратор и локальный Incus настроены.')

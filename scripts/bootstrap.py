#!/usr/bin/env python3
"""Fresh-install settings. Secrets are prompted on the TTY and never go in Git."""
import argparse
import datetime
import getpass
import importlib.machinery
import importlib.util
import ipaddress
import json
import os
import pathlib as P
import pwd
import re
import secrets
import shutil
import subprocess as S
import sys
import time
import uuid
from zoneinfo import ZoneInfo

REPO = P.Path(__file__).resolve().parents[1]
BASE = P.Path('/etc/e-dev')
RESERVED = ['10.42.0.0/24', '10.43.0.0/24', '10.158.92.0/24']
DOMAINS = ['ru', 'su', 'xn--p1ai', 'ozonusercontent.com', 'ozon.ru',
           'wildberries.ru', 'wb.ru', 'wbbasket.ru', 'wbstatic.net',
           '2gis.ru', '2gis.com', 'dgis.ru', 'gosuslugi.ru']


def run(*args, input=None, check=True, **kw):
    return S.run(args, input=input, text=True, check=check, **kw)


def incus(*args):
    # incus must not consume the installer's interactive input.
    return run('incus', *args, stdin=S.DEVNULL, stdout=S.PIPE, stderr=S.PIPE).stdout


def write(path, content, mode=0o644):
    path = P.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    # Open with mode 0600 before writing any secret, including on partial retries.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(content)
    temporary.chmod(mode)
    temporary.replace(path)


def parser():
    p = argparse.ArgumentParser(description='E-DEV: новая Raspberry Pi OS Lite 64-bit, Debian 13; uplink по Ethernet.')
    caller = os.environ.get('SUDO_USER', 'user')
    p.add_argument('--user', default=caller if caller != 'root' else 'user', help='Существующий Linux-пользователь (по умолчанию пользователь sudo)')
    p.add_argument('--hostname', default='E-DEV')
    p.add_argument('--ssid', default='E-DEV')
    p.add_argument('--country', default='RU', help='Двухбуквенная страна фактического использования Wi-Fi')
    p.add_argument('--timezone', default='Asia/Sakhalin')
    p.add_argument('--preflight', action='store_true', help=argparse.SUPPRESS)
    return p


def preflight(args):
    if os.geteuid() != 0:
        raise ValueError('Требуется sudo.')
    if not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', args.user) or pwd.getpwnam(args.user).pw_uid < 1000:
        raise ValueError('Нужен существующий обычный пользователь Linux.')
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9-]{0,62}', args.hostname) or args.hostname.endswith('-'):
        raise ValueError('Некорректный hostname.')
    if not 1 <= len(args.ssid.encode()) <= 32 or any(c in args.ssid for c in '\n\r\0'):
        raise ValueError('SSID: 1–32 байта без переводов строк.')
    # Keyfile escaping is intentionally unnecessary: this installer accepts simple SSIDs.
    if not re.fullmatch('[A-Za-z0-9 _.-]+', args.ssid):
        raise ValueError('SSID установщика: латиница, цифры, пробел, _, . или -.')
    if not re.fullmatch('[A-Z]{2}', args.country):
        raise ValueError('Страна: две заглавные буквы, например RU.')
    ZoneInfo(args.timezone)
    release = dict(line.split('=', 1) for line in P.Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('VERSION_CODENAME', '').strip('"') != 'trixie' or run('dpkg', '--print-architecture', stdout=S.PIPE).stdout.strip() != 'arm64':
        raise ValueError('Этот установщик рассчитан на Raspberry Pi OS Lite 64-bit / Debian 13 arm64.')
    model = P.Path('/proc/device-tree/model')
    if not model.exists() or 'Raspberry Pi 5' not in model.read_text().strip('\0'):
        raise ValueError('Для полной установки нужна Raspberry Pi 5. На другой системе сначала адаптируйте AP/сети.')
    if not P.Path('/sys/class/net/wlan0').exists() or not P.Path('/sys/class/net/eth0').exists():
        raise ValueError('Ожидаются wlan0 для AP и eth0 для uplink.')
    if (BASE/'installed.json').exists() or ((BASE/'mode').exists() and not (BASE/'setup-in-progress').exists()):
        raise ValueError('E-DEV уже настроен. Для обновления кода используйте sudo ./update.sh.')
    routes = json.loads(run('ip', '-j', '-4', 'route', 'show', 'table', 'main', stdout=S.PIPE).stdout)
    if not any(r.get('dst') == 'default' and r.get('dev') == 'eth0' for r in routes):
        raise ValueError('Первую установку выполняйте с рабочим Ethernet uplink (default route через eth0).')
    for route in routes:
        if route.get('dst') == 'default' or route.get('dev') in ('wlan0', 'vpnbr0', 'incusbr0'):
            continue
        net = ipaddress.IPv4Network(route['dst'], strict=False)
        if any(net.overlaps(ipaddress.IPv4Network(r)) for r in RESERVED):
            raise ValueError('LAN пересекается с одной из внутренних сетей E-DEV: ' + str(net))
    if not (BASE/'setup-in-progress').exists() and shutil.which('incus'):
        if json.loads(incus('list', '--format=json')) or json.loads(incus('storage', 'list', '--format=json')):
            raise ValueError('Найден существующий Incus с данными. Свежая установка не изменяет такие серверы.')
        if any(n.get('managed') for n in json.loads(incus('network', 'list', '--format=json'))):
            raise ValueError('Найдены существующие сети Incus. Используйте отдельную чистую Pi OS.')
    print('Проверка пройдена: Pi 5 / trixie / ARM64, Ethernet, сети не пересекаются.')


def init_incus(user):
    run('systemctl', 'enable', '--now', 'incus', 'incus.socket')
    if not json.loads(incus('storage', 'list', '--format=json')):
        incus('storage', 'create', 'default', 'dir')
    networks = {n['name'] for n in json.loads(incus('network', 'list', '--format=json'))}
    for name, address in [('incusbr0', '10.158.92.1/24'), ('vpnbr0', '10.43.0.1/24')]:
        if name not in networks:
            incus('network', 'create', name, 'ipv4.address='+address, 'ipv4.nat=true', 'ipv6.address=none')
    names = {p['name'] for p in json.loads(incus('profile', 'list', '--format=json'))}
    for name, network in [('default', 'incusbr0'), ('vpn', 'vpnbr0')]:
        if name not in names:
            incus('profile', 'create', name)
        incus('profile', 'set', name, 'limits.cpu=2', 'limits.memory=1GiB')
        profile = next(p for p in json.loads(incus('profile', 'list', '--format=json')) if p['name'] == name)
        if 'root' not in profile['devices']:
            incus('profile', 'device', 'add', name, 'root', 'disk', 'path=/', 'pool=default')
        if 'eth0' not in profile['devices']:
            incus('profile', 'device', 'add', name, 'eth0', 'nic', 'name=eth0', 'network='+network)
    run('usermod', '-aG', 'incus-admin', user)
    incus('config', 'set', 'core.https_address=:8443')
    write('/etc/systemd/system/incus.service.d/ui.conf', '[Service]\nEnvironment=INCUS_UI=/opt/incus/ui\n')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'restart', 'incus')


def init_lxconsole(args, password):
    path = P.Path('/opt/lxconsole')
    if not (path/'.env').exists():
        write(path/'.env', 'LXCONSOLE_SECRET_KEY='+secrets.token_urlsafe(96)+'\n', 0o600)
    tls = path/'tls'
    tls.mkdir(exist_ok=True)
    tls.chmod(0o700)
    if not (tls/'server.key').exists():
        addresses = json.loads(run('ip', '-j', '-4', 'addr', stdout=S.PIPE).stdout)
        ips = {'127.0.0.1', '10.42.0.1'}
        ips.update(a['local'] for d in addresses for a in d.get('addr_info', []) if d['ifname'] == 'eth0')
        san = 'DNS:'+args.hostname+'.local,DNS:localhost,'+','.join('IP:'+x for x in sorted(ips))
        run('openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-days', '3650',
            '-subj', '/CN='+args.hostname+'.local', '-addext', 'subjectAltName='+san,
            '-keyout', str(tls/'server.key'), '-out', str(tls/'server.crt'), stdout=S.DEVNULL, stderr=S.DEVNULL)
        (tls/'server.key').chmod(0o600)
    run('chown', '-R', 'lxconsole:lxconsole', str(path))
    run('runuser', '-u', 'lxconsole', '--', str(path/'.venv/bin/python'),
        '/opt/e-dev/bootstrap_lxconsole.py', args.user, input=password, cwd=path)
    (path/'certs/client.key').chmod(0o600)
    # Public certificate only; private key stays on this Pi.
    fingerprint = run('openssl', 'x509', '-in', str(path/'certs/client.crt'), '-noout',
                      '-fingerprint', '-sha256', stdout=S.PIPE).stdout.partition('=')[2].strip().replace(':', '').lower()
    trusted = json.loads(incus('config', 'trust', 'list', '--format=json'))
    if not any(c['fingerprint'].lower() == fingerprint for c in trusted):
        incus('config', 'trust', 'add-certificate', str(path/'certs/client.crt'), '--name=E-DEV LXConsole')


def initial_settings(args):
    BASE.mkdir(exist_ok=True)
    BASE.chmod(0o755)
    (BASE/'profiles').mkdir(exist_ok=True)
    (BASE/'profiles').chmod(0o700)
    P.Path('/etc/amnezia/amneziawg').mkdir(parents=True, exist_ok=True)
    P.Path('/etc/amnezia/amneziawg').chmod(0o700)
    settings = {
        'system.json': json.dumps({'user': args.user, 'hostname': args.hostname, 'ssid': args.ssid})+'\n',
        'mode': 'vpn\n', 'vpn-enabled': 'off\n', 'selected-profile': '\n', 'profile-names': '',
        'vpn-policy.json': '{"mode":"manual","primary":null,"backup":null}\n',
        'hybrid.json': '{"geo":true,"autoupdate":true,"dns":["1.1.1.1","8.8.8.8"]}\n',
        'direct-domains': '\n'.join(DOMAINS)+'\n', 'direct-ips': ''}
    for name, value in settings.items():
        if not (BASE/name).exists():
            write(BASE/name, value)
    write(BASE/'dns.conf', 'port=1053\nlisten-address=10.42.0.1\nbind-dynamic\nuser=root\nno-hosts\nno-resolv\ncache-size=1000\nserver=1.1.1.1@awg0\nserver=8.8.8.8@awg0\nnftset=/'+ '/'.join(DOMAINS)+'/4#inet#e_dev_split#ru4\n')
    write('/etc/NetworkManager/dnsmasq-shared.d/e-dev-upstream.conf', 'no-resolv\nserver=1.1.1.1\nserver=8.8.8.8\n')
    loader = importlib.machinery.SourceFileLoader('runtime', str(REPO/'src/e-dev-vpn'))
    runtime = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(runtime)
    # First AP start is protected even if VPN configuration has not been imported.
    if not (BASE/'ap-guard.nft').exists():
        write(BASE/'ap-guard.nft', runtime.rules('blocked'))


def init_ap(args, password):
    if shutil.which('raspi-config'):
        run('raspi-config', 'nonint', 'do_wifi_country', args.country)
    run('rfkill', 'unblock', 'wifi')
    run('iw', 'reg', 'set', args.country)
    write('/etc/modprobe.d/e-dev-wifi.conf', 'options cfg80211 ieee80211_regdom='+args.country+'\n')
    keyfile = P.Path('/etc/NetworkManager/system-connections/E-DEV-AP.nmconnection')
    # GLib KeyFile escaping; never pass the password as a command argument.
    escaped = password.replace('\\', '\\\\').replace(' ', '\\s').replace('\t', '\\t')
    connection = f'''[connection]
id=E-DEV-AP
uuid={uuid.uuid4()}
type=wifi
interface-name=wlan0
autoconnect=true
autoconnect-priority=100

[wifi]
ssid={args.ssid}
mode=ap
band=bg
channel=11
powersave=2

[wifi-security]
key-mgmt=wpa-psk
proto=rsn;
pairwise=ccmp;
group=ccmp;
psk={escaped}

[ipv4]
method=shared
address1=10.42.0.1/24
never-default=true

[ipv6]
method=disabled
'''
    write(keyfile, connection, 0o600)
    run('nmcli', 'connection', 'load', str(keyfile))
    run('nmcli', 'connection', 'up', 'E-DEV-AP')


def main():
    args = parser().parse_args()
    preflight(args)
    if args.preflight:
        return
    if not sys.stdin.isatty():
        raise ValueError('Для скрытого ввода пароля запустите из интерактивной SSH-сессии.')
    password = getpass.getpass('Общий пароль SSH / SMB / LXConsole / Wi-Fi (8–63 ASCII-символа): ')
    confirm = getpass.getpass('Повторите пароль: ')
    if password != confirm or not 8 <= len(password) <= 63 or any(ord(c) < 32 or ord(c) > 126 for c in password):
        raise ValueError('Пароли должны совпасть: 8–63 печатных ASCII-символа.')
    os.umask(0o077)
    BASE.mkdir(exist_ok=True)
    write(BASE/'setup-in-progress', 'owned by E-DEV installer\n', 0o600)
    backup = P.Path('/opt/e-dev/backups') / ('install-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True)
    backup.chmod(0o700)
    for filename in ['/etc/samba/smb.conf', '/etc/hostname', '/etc/hosts']:
        p = P.Path(filename)
        if p.exists():
            shutil.copy2(p, backup/p.name)
    run('hostnamectl', 'set-hostname', args.hostname)
    hosts = P.Path('/etc/hosts').read_text()
    hosts = re.sub(r'^127\.0\.1\.1\s+.*$', '127.0.1.1\t'+args.hostname, hosts, flags=re.M)
    if not re.search(r'^127\.0\.1\.1\s', hosts, re.M):
        hosts += '\n127.0.1.1\t'+args.hostname+'\n'
    write('/etc/hosts', hosts)
    run('timedatectl', 'set-timezone', args.timezone)
    init_incus(args.user)
    initial_settings(args)
    run('sysctl', '-p', '/etc/sysctl.d/90-e-dev-vpn.conf')
    run('systemctl', 'enable', '--now', 'e-dev-guard.service')
    run('systemctl', 'enable', '--now', 'e-dev-vpn.service')
    run('systemctl', 'enable', '--now', 'e-dev-dns.service')
    # Profiles remain empty and VPN is disabled until the user imports client configs.
    init_lxconsole(args, password)
    run('chpasswd', input=args.user+':'+password+'\n')
    share = P.Path('/srv/share')
    share.mkdir(exist_ok=True)
    run('chown', args.user+':'+str(pwd.getpwnam(args.user).pw_gid), str(share))
    share.chmod(0o770)
    write('/etc/samba/smb.conf', f'''[global]
 workgroup = WORKGROUP
 server string = E-DEV Files
 server role = standalone server
 map to guest = Never
 interfaces = lo eth* en* wl* edevbr0
 bind interfaces only = yes
 server min protocol = SMB2

[Share]
 path = /srv/share
 browseable = yes
 read only = no
 valid users = {args.user}
 create mask = 0660
 directory mask = 0770
''')
    run('smbpasswd', '-a', '-s', args.user, input=password+'\n'+password+'\n', stdout=S.DEVNULL)
    run('testparm', '-s', stdout=S.DEVNULL)
    run('systemctl', 'enable', '--now', 'lxconsole', 'smbd', 'avahi-daemon')
    run('systemctl', 'restart', 'smbd')
    for timer in ['e-dev-vpn-health.timer', 'e-dev-auto.timer', 'e-dev-geo-update.timer']:
        run('systemctl', 'enable', '--now', timer)
    # The firewall, policy routes and DNS are ready before NM can activate the AP.
    init_ap(args, password)
    # The asynchronous transaction survives an Ethernet/SSH reconnect.
    started = time.time()
    run('/usr/local/sbin/e-dev-lan', 'init')
    for _ in range(180):
        result_path = P.Path('/run/e-dev-lan-apply.json')
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        if result.get('updated',0)>=started:
            if not result['success']:
                raise ValueError('Не создан мост OUT: '+result.get('error','см. e-dev-lan-apply'))
            break
        time.sleep(1)
    else:
        raise ValueError('Применение LAN ещё не завершилось: journalctl -u e-dev-lan-apply')
    run('systemctl','enable','--now','e-dev-network.service','e-dev-uplink.timer')
    run('/usr/local/sbin/e-dev-geo-update', check=False)
    manifest = {'installed': datetime.datetime.now().astimezone().isoformat(), 'user': args.user,
                'hostname': args.hostname, 'country': args.country, 'timezone': args.timezone}
    write(BASE/'installed.json', json.dumps(manifest, indent=2)+'\n')
    (BASE/'setup-in-progress').unlink()
    print('AP готов. Интернет AP закрыт до импорта VPN либо команды e-dev wifi off.')
    print('Перезайдите в SSH, чтобы применились группа Incus, приветствие и Tab.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, S.CalledProcessError) as error:
        print('Установка остановлена: '+str(error), file=sys.stderr)
        sys.exit(1)

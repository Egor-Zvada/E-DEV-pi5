#!/usr/bin/env bash
# Update the custom E-DEV code, preserving profiles, modes, passwords and data.
set -euo pipefail
REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ $EUID == 0 && -f /etc/e-dev/mode ]] || { echo 'sudo ./update.sh на уже настроенной E-DEV' >&2; exit 1; }
BACKUP_DIR=/opt/e-dev/backups/code-$(date +%Y%m%d-%H%M%S)
install -d -m 700 "$BACKUP_DIR/bin" "$BACKUP_DIR/sbin" "$BACKUP_DIR/units"
cp -a /etc/e-dev "$BACKUP_DIR/config"
cp -a /opt/e-dev/docs "$BACKUP_DIR/docs"
cp -a /usr/local/bin/e-dev "$BACKUP_DIR/bin/"
for path in /usr/local/sbin/e-dev-*; do cp -a "$path" "$BACKUP_DIR/sbin/"; done
for path in /etc/systemd/system/e-dev-* /etc/systemd/system/lxconsole.service; do
    [[ -f "$path" ]] && cp -a "$path" "$BACKUP_DIR/units/"
done
cp -a /etc/bash_completion.d/e-dev "$BACKUP_DIR/completion"
cp -a /usr/local/share/man/man1/e-dev.1 "$BACKUP_DIR/e-dev.1"
if [[ -f /etc/systemd/journald.conf.d/e-dev.conf ]]; then
    cp -a /etc/systemd/journald.conf.d/e-dev.conf "$BACKUP_DIR/journald.conf"
fi
chmod -R go-rwx "$BACKUP_DIR"
bash "$REPO_DIR/scripts/deploy.sh"
if ! command -v dialog >/dev/null || ! command -v ethtool >/dev/null; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y dialog ethtool
fi
python3 -m compileall -q /usr/local/bin/e-dev /usr/local/sbin/e-dev-*
sysctl -p /etc/sysctl.d/90-e-dev-vpn.conf
/usr/local/sbin/e-dev-vpn boot
systemctl enable --now e-dev-vpn-health.timer e-dev-auto.timer e-dev-network.service e-dev-uplink.timer
printf 'Код обновлён. Резервная копия (содержит ключи, root-only): %s\n' "$BACKUP_DIR"

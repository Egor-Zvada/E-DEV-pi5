#!/usr/bin/env bash
# Copy only repository-managed public code/configuration; never copy secrets.
set -euo pipefail
REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ $EUID == 0 ]] || { echo 'Требуется root' >&2; exit 1; }
python3 "$REPO_DIR/scripts/build_wiki.py"
install -m 755 "$REPO_DIR/src/e-dev" /usr/local/bin/e-dev
for component in e-dev-vpn e-dev-policy e-dev-hybrid e-dev-geo-update e-dev-awg-launch e-dev-dashboard e-dev-wifi e-dev-lan e-dev-lan-ui e-dev-tui e-dev-actions e-dev-settings e-dev-diagnostics e-dev-incus-ready; do
    install -m 755 "$REPO_DIR/src/$component" "/usr/local/sbin/$component"
done
for alias_name in E-DEV E-dev edev; do ln -sfn /usr/local/bin/e-dev "/usr/local/bin/$alias_name"; done
install -d -m 755 /opt/e-dev/docs/wiki /usr/local/share/man/man1 /etc/bash_completion.d
install -m 644 "$REPO_DIR/assets/completion" /etc/bash_completion.d/e-dev
install -m 644 "$REPO_DIR/assets/"logo*.txt /opt/e-dev/docs/
install -m 644 "$REPO_DIR/assets/dialogrc" /opt/e-dev/docs/dialogrc
rm -f /opt/e-dev/docs/wiki/wifi.txt /opt/e-dev/docs/wiki/passwd.txt
install -m 644 "$REPO_DIR/docs/wiki/"*.txt /opt/e-dev/docs/wiki/
install -m 644 "$REPO_DIR/docs/COMMANDS.md" /opt/e-dev/docs/E-DEV-guide.md
install -m 644 "$REPO_DIR/docs/e-dev.1" /usr/local/share/man/man1/e-dev.1
install -m 644 "$REPO_DIR/config/e-dev-welcome.sh" /etc/profile.d/e-dev-welcome.sh
install -m 644 "$REPO_DIR/config/90-e-dev-vpn.conf" /etc/sysctl.d/90-e-dev-vpn.conf
install -m 644 "$REPO_DIR/config/systemd/"* /etc/systemd/system/
install -d -m 755 /etc/systemd/system/incus.service.d
install -m 644 "$REPO_DIR/config/incus/e-dev-ready.conf" /etc/systemd/system/incus.service.d/e-dev-ready.conf
install -d -m 755 /etc/systemd/journald.conf.d
install -m 644 "$REPO_DIR/config/journald/e-dev.conf" /etc/systemd/journald.conf.d/e-dev.conf
install -d -m 755 /etc/NetworkManager/dnsmasq-shared.d
install -m 644 "$REPO_DIR/config/e-dev-diagnostics-dhcp.conf" /etc/NetworkManager/dnsmasq-shared.d/e-dev-diagnostics.conf
dnsmasq --test --conf-file=/dev/null --conf-dir=/etc/NetworkManager/dnsmasq-shared.d
install -m 644 "$REPO_DIR/scripts/bootstrap_lxconsole.py" /opt/e-dev/bootstrap_lxconsole.py
# This was a one-time override on the original Pi; the shipped service includes it.
rm -f /etc/systemd/system/e-dev-geo-update.service.d/scheduled.conf
systemctl daemon-reload
systemctl try-restart systemd-journald.service
journalctl --flush
if [[ -f /etc/e-dev/mode ]]; then
    systemctl enable e-dev-diagnostics.service
    systemctl restart e-dev-diagnostics.service
fi

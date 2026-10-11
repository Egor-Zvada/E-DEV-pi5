#!/usr/bin/env bash
# Fresh Raspberry Pi OS Lite 64-bit (Debian 13). Run from an Ethernet SSH login.
set -euo pipefail
REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ${1:-} == --help ]]; then
    python3 "$REPO_DIR/scripts/bootstrap.py" --help
    exit 0
fi
[[ $EUID == 0 ]] || { echo 'Запуск: sudo ./install.sh [--user USER --country RU]' >&2; exit 1; }
python3 "$REPO_DIR/scripts/bootstrap.py" --preflight "$@"
source "$REPO_DIR/config/versions.env"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git gnupg build-essential pkg-config golang-go \
    python3 python3-venv python3-dev libffi-dev libssl-dev openssl network-manager \
    dnsmasq-base nftables iproute2 iw wireless-regdb rfkill samba avahi-daemon \
    bash-completion less man-db dnsutils apparmor uidmap
install -d -m 755 /etc/apt/keyrings
KEY_TMP=$(mktemp)
trap 'rm -f "$KEY_TMP"' EXIT
curl --fail --silent --show-error --location --retry 3 https://pkgs.zabbly.com/key.asc -o "$KEY_TMP"
KEY_FINGERPRINT=$(gpg --batch --show-keys --with-colons "$KEY_TMP" | awk -F: '$1=="fpr" { print $10; exit }')
[[ "$KEY_FINGERPRINT" == "$ZABBLY_KEY_FINGERPRINT" ]] || { echo 'Неверный отпечаток ключа Zabbly' >&2; exit 1; }
install -m 644 "$KEY_TMP" /etc/apt/keyrings/zabbly.asc
cat > /etc/apt/sources.list.d/zabbly-incus-lts-6.0.sources <<'APT'
Enabled: yes
Types: deb
URIs: https://pkgs.zabbly.com/incus/lts-6.0
Suites: trixie
Components: main
Architectures: arm64
Signed-By: /etc/apt/keyrings/zabbly.asc
APT
apt-get update
apt-get install -y incus incus-ui-canonical
install -d -m 755 /opt/e-dev/build
checkout() {
    local target=$1 remote=$2 revision=$3
    if [[ ! -d "$target/.git" ]]; then git clone --filter=blob:none "$remote" "$target"; fi
    git -C "$target" fetch origin "$revision"
    git -C "$target" checkout --detach "$revision"
    [[ $(git -C "$target" rev-parse HEAD) == "$revision" ]]
}
checkout /opt/e-dev/build/awg-go "$AWG_GO_REPO" "$AWG_GO_REV"
# apt's Go can fetch the toolchain required by this pinned module (go.mod).
(cd /opt/e-dev/build/awg-go && GOTOOLCHAIN=auto go build -trimpath -o /usr/local/bin/amneziawg-go .)
checkout /opt/e-dev/build/awg-tools "$AWG_TOOLS_REPO" "$AWG_TOOLS_REV"
make -C /opt/e-dev/build/awg-tools/src -j2
make -C /opt/e-dev/build/awg-tools/src install WITH_WGQUICK=yes WITH_SYSTEMDUNITS=no
curl --fail --silent --show-error --location --retry 3 "$SPEEDTEST_URL" -o /opt/e-dev/build/speedtest.tgz
printf '%s  %s\n' "$SPEEDTEST_SHA256" /opt/e-dev/build/speedtest.tgz | sha256sum -c -
install -d -m 755 /opt/e-dev/build/speedtest
tar -xzf /opt/e-dev/build/speedtest.tgz -C /opt/e-dev/build/speedtest
install -m 755 /opt/e-dev/build/speedtest/speedtest /usr/local/bin/speedtest
id lxconsole >/dev/null 2>&1 || useradd --system --home-dir /opt/lxconsole --shell /usr/sbin/nologin lxconsole
checkout /opt/lxconsole "$LXCONSOLE_REPO" "$LXCONSOLE_REV"
python3 -m venv /opt/lxconsole/.venv
/opt/lxconsole/.venv/bin/pip install -r "$REPO_DIR/config/lxconsole-requirements.txt"
chown -R lxconsole:lxconsole /opt/lxconsole
bash "$REPO_DIR/scripts/deploy.sh"
python3 "$REPO_DIR/scripts/bootstrap.py" "$@"
printf '\nУстановка завершена. Импортируйте VPN-конфиги: см. README.md.\n'
/usr/local/bin/e-dev links

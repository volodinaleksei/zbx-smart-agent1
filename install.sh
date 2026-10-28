#!/usr/bin/env bash
# Install SMART monitoring for Zabbix agent 1 (emulation of agent 2 smartdisk
# plugin keys, official template 10383 "SMART by Zabbix agent 2").
# Linux and FreeBSD. Run as root on the monitored host.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

OS=$(uname -s)
command -v python3 >/dev/null || {
    echo "python3 not found; install it first (pkg install python3 / apt install python3)"
    exit 1
}
command -v smartctl >/dev/null || {
    echo "installing smartmontools..."
    case "$OS" in
        Linux)  apt-get update -qq && apt-get install -y -qq smartmontools \
                    || yum install -y smartmontools ;;
        FreeBSD) pkg install -y smartmontools_ng || pkg install -y smartmontools ;;
    esac
    command -v smartctl >/dev/null || { echo "install smartmontools manually"; exit 1; }
}
SMARTCTL="$(command -v smartctl)"

cd "$(dirname "$0")"
install -m 755 smart_agent1.py /usr/local/bin/smart_agent1.py

if [ "$OS" = "FreeBSD" ]; then
    # find agent config dir (ports: /usr/local/etc/zabbix74 etc.)
    CONFDIR=$(dirname "$(ls /usr/local/etc/zabbix*/zabbix_agentd.conf 2>/dev/null | head -1)")
    [ -n "$CONFDIR" ] || { echo "zabbix_agentd.conf not found under /usr/local/etc/zabbix*"; exit 1; }
    mkdir -p "${CONFDIR}/zabbix_agentd.d"
    grep -q "Include=.*zabbix_agentd.d" "$CONFDIR/zabbix_agentd.conf" 2>/dev/null \
        || echo "Include=${CONFDIR}/zabbix_agentd.d/*.conf" >> "$CONFDIR/zabbix_agentd.conf"
    sed "s|/usr/bin/python3|$(command -v python3)|" zabbix_agentd.d/smart.conf \
        > "${CONFDIR}/zabbix_agentd.d/smart.conf"
    # sudo for zabbix user (agent runs unprivileged); if agent runs as root, harmless
    if id zabbix >/dev/null 2>&1; then
        echo "zabbix ALL=(root) NOPASSWD: ${SMARTCTL}" > /usr/local/etc/sudoers.d/zabbix_smartctl
        chmod 440 /usr/local/etc/sudoers.d/zabbix_smartctl
        visudo -cf /usr/local/etc/sudoers.d/zabbix_smartctl
    fi
    service zabbix_agentd restart && sleep 1 && service zabbix_agentd status | head -2 || true
    SELFTEST="zabbix_agentd -c ${CONFDIR}/zabbix_agentd.conf"
else
    install -m 644 zabbix_agentd.d/smart.conf /etc/zabbix/zabbix_agentd.d/smart.conf
    echo "zabbix ALL=(root) NOPASSWD: ${SMARTCTL}" > /etc/sudoers.d/zabbix_smartctl
    chmod 440 /etc/sudoers.d/zabbix_smartctl
    visudo -cf /etc/sudoers.d/zabbix_smartctl
    systemctl restart zabbix-agent
    sleep 1
    systemctl is-active zabbix-agent
    SELFTEST=zabbix_agentd
fi

echo "--- self test ---"
$SELFTEST -t smart.disk.discovery

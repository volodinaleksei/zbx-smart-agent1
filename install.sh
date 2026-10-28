#!/usr/bin/env bash
# Install SMART monitoring for Zabbix agent 1 (emulation of agent 2 smartdisk
# plugin keys, official template 10383 "SMART by Zabbix agent 2").
# Run as root on the monitored host.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo ./install.sh)"; exit 1; }
command -v python3 >/dev/null || { echo "python3 required"; exit 1; }

if ! command -v smartctl >/dev/null; then
    echo "installing smartmontools..."
    apt-get update -qq && apt-get install -y -qq smartmontools || {
        yum install -y smartmontools || { echo "install smartmontools manually"; exit 1; }
    }
fi

cd "$(dirname "$0")"
install -m 755 smart_agent1.py /usr/local/bin/smart_agent1.py
install -m 644 zabbix_agentd.d/smart.conf /etc/zabbix/zabbix_agentd.d/smart.conf
echo 'zabbix ALL=(root) NOPASSWD: /usr/sbin/smartctl' > /etc/sudoers.d/zabbix_smartctl
chmod 440 /etc/sudoers.d/zabbix_smartctl
visudo -cf /etc/sudoers.d/zabbix_smartctl

systemctl restart zabbix-agent
sleep 1
systemctl is-active zabbix-agent

echo "--- self test ---"
zabbix_agentd -t smart.disk.discovery

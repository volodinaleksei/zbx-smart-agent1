# zbx-smart-agent1

Monitor S.M.A.R.T. with the official Zabbix template
[10383 “SMART by Zabbix agent 2”](https://www.zabbix.com/integrations/smart)
on hosts running **plain Zabbix agent** (agent 1, `zabbix_agentd`) — no agent 2 required.

## How it works

Keys used by template 10383 (`smart.disk.discovery`, `smart.disk.get[path,raidtype]`)
are ordinary *Zabbix agent* item keys — agent 2 just intercepts them natively via its
smartdisk plugin. This project publishes the same keys through `UserParameter`, with a
small Python wrapper over `smartctl --json` that reproduces the agent 2 JSON output.
The template itself is linked **unchanged**: disk LLD, all dependent items, JSONPath
preprocessing, dashboards and vendor-attribute overrides work exactly like on agent 2.

Fields produced by the wrapper cover every JSONPath used by the template:
`$.model_name`, `$.serial_number`, `$.power_on_time`, `$.power_cycle_count.value`,
`$.temperature`, `$.exit_status`, `$.error`, `$.self_test_passed`,
`$.critical_warning`, `$.percentage_used`, `$.media_errors`, and ATA attributes
(`raw_read_error_rate`, `spin_up_time`, `start_stop_count`, `reallocated_sector_ct`,
`seek_error_rate`, `reported_uncorrect`, `program_fail_count_chip`, `bad_block_rate`)
each as `{value, worst, threshold}`.

## Requirements

- Zabbix agent 1 (any 4.x/5.x/6.x/7.x with `UserParameter` support)
- smartmontools ≥ 7.0 (tested: 7.4, 7.5)
- Python 3 (stdlib only)
- link template **10383** on the host (server-side, nothing to import)

## Installation

```bash
git clone https://github.com/volodinaleksei/zbx-smart-agent1
cd zbx-smart-agent1
sudo ./install.sh
```

What it does:

1. installs `smartmontools` if missing (apt/yum)
2. `/usr/local/bin/smart_agent1.py` — the wrapper
3. `/etc/zabbix/zabbix_agentd.d/smart.conf` — UserParameter definitions
4. `/etc/sudoers.d/zabbix_smartctl` — `NOPASSWD` for `/usr/sbin/smartctl`
   (smartctl needs root and cannot use an LLD macro argument via sudoers-safe args)
5. restarts `zabbix-agent` and runs a `zabbix_agentd -t smart.disk.discovery` self-test

Then link template *SMART by Zabbix agent 2* to the host. First discovery may take
up to one LLD interval (1 h by default; increase the template's discovery-rule delay
temporarily to speed it up).

## VMs without SMART

Virtual disks often report “SMART support is unavailable”, so discovery skips them.
To include them anyway (values 0, `exit_status=4`), edit `smart.conf`:

```
UserParameter=smart.disk.discovery,SMART_INCLUDE_UNSUPPORTED=1 /usr/bin/python3 /usr/local/bin/smart_agent1.py discovery
```

## Verification

```bash
zabbix_agentd -t smart.disk.discovery
zabbix_agentd -t 'smart.disk.get[/dev/sda,""]'
```

Or on the server: host → items → filter `smart.` — all items should be
`Supported`, no preprocessing errors.

## Compatibility notes

- smartmontools 7.5 renamed `attribute_name` → `name` in the JSON table;
  the wrapper reads both.
- Some SSDs (e.g. Kingston SA400) expose `power_cycle_count` only top-level;
  the wrapper always emits the `.value` object the template expects.
- Hardware RAID (meGaraid/hpraid/…) works via the `{#RAIDTYPE}` LLD macro
  (second argument of `smart.disk.get`, forwarded to `smartctl -d`).

## Verified on

| Host | OS | Agent | smartmontools | Disk | Result |
|---|---|---|---|---|---|
| VM (QEMU) | Ubuntu 24.04 | 7.0.32 | 7.4 | virtual sda, no SMART | 8 items, state 0 |
| vibestation | Ubuntu 26.04 | 7.4.16 | 7.5 | Kingston SA400S37120G | 12 items incl. attributes, state 0 |

## FreeBSD notes

Discovery uses `geom disk list` instead of `lsblk` (auto-detected via `sys.platform`).
Device naming: `ada*` → ata, `nda*/nvd*` → nvme, `da*` → scsi; `cd*` (optical) skipped.
Agent config lives in `/usr/local/etc/zabbixXX/zabbix_agentd.d/`; sudoers file goes to
`/usr/local/etc/sudoers.d/`. NVMe devices on FreeBSD may need `smartctl -d nvme` — the
`{#RAIDTYPE}` LLD macro covers this: edit the template's discovery command or set
`{#RAIDTYPE}` override if your NVMe disks are not auto-detected.

## License

MIT

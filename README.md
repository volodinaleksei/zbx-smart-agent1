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

## `percentage_used` on SATA SSDs

`percentage_used` (SSD wear %) is sourced with the following priority chain:

1. NVMe health log (`nvme_smart_health_information_log.percentage_used`).
2. ATA Device Statistics GP Log 0x04, page 7, entry **offset 0x008**
   (“Percentage Used Endurance Indicator”), when smartctl puts
   `ata_device_statistics` in its JSON. Matched by offset, not by name —
   for drives outside the smartctl database the entry name can print as
   “Unknown”.
3. Vendor SMART wear attributes (`Percent_Lifetime_Remain`,
   `Media_Wearout_Indicator`, `SSD_Life_Left`, …) using the **normalized**
   value: `used = 100 - value`. Raw encoding is vendor-inconsistent
   (raw = % used on Intel/Micron 202, % remaining on Kingston 231);
   per the ATA spec normalized values are always “higher = better”.
4. Text-mode fallback: some smartmontools builds (seen on FreeBSD local
   builds) omit `ata_device_statistics` from JSON **entirely** and print the
   Device Statistics table only in extended output — `smartctl -x`, not even
   `-a`. The wrapper parses the
   `0x07  0x008  1  <N>  N--  Percentage Used Endurance Indicator`
   line from `smartctl -x` as a last resort.

If all four yield nothing, the disk genuinely does not report wear.

## Template override “Not NVMe” (percentage_used missing on SATA by design)

Official template 10383 ships an LLD override **“Not NVMe”** on the
*Disk discovery* rule that sets `discover: NO_DISCOVER` for item prototypes
matching `Media|Percentage|Critical` when `{#DISKTYPE} != nvme`. In other
words: even when this wrapper returns a correct `percentage_used`, a SATA
disk will **not** get the “Percentage used” item — it is filtered out by the
template, not by the script.

To enable it, edit the override operation regex at the **template level**
(`Media|Percentage|Critical` → `Media|Critical`). Note that override edits on
a *host-inherited* copy of the discovery rule are rejected by the server
(`cannot update readonly parameter "overrides" of inherited object`) even
though the frontend form appears editable.

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

Updating an existing deployment: `git pull && install -m 755 smart_agent1.py
/usr/local/bin/smart_agent1.py` — no agent restart needed (the UserParameter
invokes the script fresh on each poll).

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

Debug mode (prints platform, discovered disk names, and per-disk smartctl
rc / smart_ok / JSON keys / messages — the one-command diagnostic when
discovery returns `[]`):

```bash
python3 /usr/local/bin/smart_agent1.py debug
```

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
| gw | FreeBSD 15.1 (pfSense) | 7.x | 7.5 | NVMe nda0 (Kingston via -d nvme) | items state 0 |
| akz-opnsense | FreeBSD 15.1 (OPNsense) | 7.x | 7.5 | mSATA “512GB SSD”, devstat only in `-x` text | percentage_used=47 |

## FreeBSD notes

Discovery uses `geom disk list` instead of `lsblk` (auto-detected via `sys.platform`).
Device naming: `ada*` → ata, `nda*/nvd*` → nvme, `da*` → scsi; `cd*` (optical) skipped.
Agent config lives in `/usr/local/etc/zabbixXX/zabbix_agentd.d/`; sudoers file goes to
`/usr/local/etc/sudoers.d/` (smartctl is at `/usr/local/sbin/smartctl` there —
`install.sh` resolves real paths, do not hardcode Linux ones).

NVMe quirks on FreeBSD:

- `nda*` nodes are not auto-detected by smartctl (“Unable to detect device
  type”, rc 3) — the wrapper retries transparently with `-d nvme`.
- If `nda*` opens but fails with `INQUIRY failed`, the working node is the
  controller (`/dev/nvmeX`, queried with `-d nvme`); discovery probes both and
  points `{#PATH}` at the working one, `get()` falls back too.
- When running under sudo (agent is not root), `-d <type>` must be inserted
  immediately after `smartctl`, not after `sudo`.
- Local smartmontools builds may omit Device Statistics from JSON — see the
  text-mode fallback above.

## License

MIT

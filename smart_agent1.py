#!/usr/bin/env python3
"""smartctl emulation of Zabbix agent 2 smartdisk plugin keys (for agent 1)."""
import json, os, subprocess, sys, datetime

TIMEOUT = 20
INCLUDE_UNSUPPORTED = os.environ.get("SMART_INCLUDE_UNSUPPORTED") == "1"
LOGFILE = os.environ.get("SMART_AGENT1_LOG")  # set to a path for debugging
SUDO = [] if subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip() == "root" else ["sudo", "-n"]

def log(msg):
    if LOGFILE is None:
        return
    try:
        with open(LOGFILE, "a") as f:
            f.write("%s uid=%s %s\n" % (datetime.datetime.now(), os.getuid(), msg))
    except Exception:
        pass

def smartctl_json(path, raidtype=None):
    cmd = SUDO + ["smartctl", "--json", "--all", path]
    if raidtype:
        cmd[len(SUDO) + 1:len(SUDO) + 1] = ["-d", raidtype]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        j = json.loads(p.stdout)
    except Exception as e:
        log("smartctl_json(%s,%s) EXC %r" % (path, raidtype, e))
        return None, 255
    # FreeBSD: /dev/nda* (NVMe) is not auto-detected ("Unable to detect device
    # type") — retry once with -d nvme. {#RAIDTYPE} is filled by caller anyway.
    if (raidtype is None and p.returncode
            and "Unable to detect device type" in json.dumps(j.get("smartctl", {}))):
        return smartctl_json(path, "nvme")
    return j, p.returncode

def list_disks_linux():
    p = subprocess.run(["lsblk", "-d", "-n", "-o", "NAME,TYPE"],
                       capture_output=True, text=True, timeout=10)
    out = []
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "disk" and not parts[0].startswith("sr"):
            out.append(parts[0])
    return out


def list_disks_bsd():
    # geom disk list prints only whole disks (no partitions); skip optical (cd*)
    p = subprocess.run(["geom", "disk", "list"], capture_output=True, text=True, timeout=10)
    out = []
    for line in p.stdout.splitlines():
        m = line.strip()
        if m.startswith("1. Name: "):
            name = m[len("1. Name: "):].strip()
            if "." in name or name.startswith("cd"):
                continue  # multipath suffix / optical
            out.append(name)
    return out


def bsd_dtype(name):
    if name.startswith(("nda", "nvd", "nvme")):
        return "nvme"
    if name.startswith("ada"):
        return "ata"
    return "scsi"


import re as _re


def bsd_candidates(name):
    """Yield (path, raidtype) probe candidates for a BSD disk name.

    NVMe on FreeBSD: smartctl cannot open the da-like node /dev/ndaX
    ("INQUIRY failed"); SMART must be read from the controller node
    /dev/nvmeX (or /dev/nvmeXnsN) with -d nvme. Yield direct path first,
    then controller equivalents.
    """
    yield ("/dev/" + name, "")
    m = _re.match(r"(?:nda|nvd|nvme)(\d+)$", name)
    if m:
        idx = m.group(1)
        yield ("/dev/nvme" + idx, "nvme")
        yield ("/dev/nvme" + idx + "ns1", "nvme")


def is_smart_ok(j):
    return bool(j.get("smart_support", {}).get("available", False)
                or "nvme_smart_health_information_log" in j
                or "ata_smart_attributes" in j
                or "scsi_grown_defect_list" in j
                or j.get("smart_status", {}))


def discover():
    is_bsd = sys.platform.startswith("freebsd") or sys.platform.startswith("dragonfly")
    try:
        names = list_disks_bsd() if is_bsd else list_disks_linux()
    except Exception as e:
        log("disk listing EXC %r" % e)
        names = []
    items = []
    for name in names:
        if is_bsd:
            dtype = bsd_dtype(name)
        else:
            dtype = "nvme" if name.startswith("nvme") else "ata"
        found = None
        if is_bsd:
            # probe path candidates; for NVMe the working node is the
            # controller (/dev/nvmeX), not /dev/ndaX
            for probe_path, probe_raid in bsd_candidates(name):
                j, rc = smartctl_json(probe_path, probe_raid or None)
                if j is not None and "json_format_version" in j and is_smart_ok(j):
                    found = (probe_path, probe_raid, j)
                    break
            if found is None:
                continue
            path, raid, j = found
        else:
            path, raid = "/dev/" + name, ""
            j, rc = smartctl_json(path, raid or None)
            if j is None or "json_format_version" not in j:
                continue
            if not is_smart_ok(j) and not INCLUDE_UNSUPPORTED:
                continue
        if dtype == "nvme" and "nvme" not in path and not raid:
            raid = "nvme"
        attrs = ",".join(a.get("name") or a.get("attribute_name") or ""
                         for a in j.get("ata_smart_attributes", {}).get("table", []))
        items.append({"{#NAME}": name, "{#PATH}": path, "{#DISKTYPE}": dtype,
                      "{#RAIDTYPE}": raid, "{#ATTRIBUTES}": attrs})
    log("discover -> %d items" % len(items))
    return items

def attr(tbl, name):
    for a in tbl:
        if (a.get("name") or a.get("attribute_name")) == name:
            return {"value": int(a.get("raw", {}).get("value") or a.get("value") or 0),
                    "worst": int(a.get("worst") or 0), "threshold": int(a.get("threshold") or 0)}
    return {"value": 0, "worst": 0, "threshold": 0}

def get(path, raidtype=None):
    j, rc = smartctl_json(path, raidtype)
    if (j is not None and "json_format_version" in j and not is_smart_ok(j)
            and (sys.platform.startswith("freebsd") or sys.platform.startswith("dragonfly"))):
        # direct node opened but no SMART (INQUIRY failed on nda*) — try controller nodes
        name = os.path.basename(path)
        for probe_path, probe_raid in bsd_candidates(name):
            if probe_path == path:
                continue
            j2, rc2 = smartctl_json(probe_path, probe_raid or None)
            if j2 is not None and "json_format_version" in j2 and is_smart_ok(j2):
                j, rc = j2, rc2
                break
    res = {"exit_status": rc, "error": ""}
    if j is None:
        res["error"] = "smartctl: no JSON output"
        log("get(%s) FAILED" % path)
        return res
    if "json_format_version" not in j:
        res["error"] = str(j.get("smartctl", {}).get("message_string", "unknown error"))
        return res
    if j.get("smartctl", {}).get("message_string") and rc:
        res["error"] = j["smartctl"]["message_string"]
    nvme = j.get("nvme_smart_health_information_log", {})
    ata = j.get("ata_smart_attributes", {}).get("table", [])
    res["model_name"] = j.get("model_name") or j.get("scsi_model_name", "unknown")
    res["serial_number"] = j.get("serial_number", "unknown")
    res["firmware_version"] = j.get("firmware_version", "")
    res["power_on_time"] = int(j.get("power_on_time", {}).get("hours") or 0)
    pcc_top = int(j.get("power_cycle_count") or 0)
    res["power_cycle_count_top"] = pcc_top
    res["temperature"] = int(j.get("temperature", {}).get("current") or 0)
    res["critical_warning"] = int(nvme.get("critical_warning") or 0)
    res["percentage_used"] = int(nvme.get("percentage_used") or 0)
    res["media_errors"] = int(nvme.get("media_errors") or j.get("scsi_error_counter_log", {}).get("read", {}).get("errors_corrected_by_eccfast", 0) or 0)
    res["self_test_passed"] = "yes"
    stl = j.get("self_test", {}).get("self_test_log", [])
    if stl:
        last = stl[0]
        status = (last.get("status") or {}).get("string", "") if isinstance(last.get("status"), dict) else str(last.get("status", ""))
        if ("completed" not in status.lower() and "never started" not in status.lower()) or last.get("lba_of_first_error"):
            res["self_test_passed"] = "no"
    for out, src in [("raw_read_error_rate", "Raw_Read_Error_Rate"), ("spin_up_time", "Spin_Up_Time"),
                     ("start_stop_count", "Start_Stop_Count"), ("reallocated_sector_ct", "Reallocated_Sector_Ct"),
                     ("seek_error_rate", "Seek_Error_Rate"), ("reported_uncorrect", "Reported_Uncorrect"),
                     ("command_timeout", "Command_Timeout"), ("program_fail_count_chip", "Program_Fail_Count_Chip"),
                     ("bad_block_rate", "Bad_Block_Rate"),
                     ("power_cycle_count", "Power_Cycle_Count")]:
        res[out] = attr(ata, src)
    if res["power_cycle_count"]["value"] == 0 and pcc_top:
        res["power_cycle_count"]["value"] = pcc_top
    log("get(%s,%s) ok es=%s model=%s" % (path, raidtype, rc, res["model_name"]))
    return res

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "discovery"
    if mode == "debug":
        is_bsd = sys.platform.startswith("freebsd") or sys.platform.startswith("dragonfly")
        print("platform:", sys.platform, "is_bsd:", is_bsd)
        try:
            names = list_disks_bsd() if is_bsd else list_disks_linux()
        except Exception as e:
            print("disk listing EXC:", repr(e)); names = []
        print("disk names:", names)
        for n in names:
            probes = list(bsd_candidates(n)) if is_bsd else [("/dev/" + n, "")]
            for p, r in probes:
                j, rc = smartctl_json(p, r or None)
                if j is None:
                    print(n, "-> smartctl FAILED (no JSON, rc=%s)" % rc)
                    continue
                ok = is_smart_ok(j)
                print("%s (%s,%s) -> rc=%s smart_ok=%s model=%s" % (
                    n, p, r or "-", rc, ok, j.get("model_name", "-")))
                if not ok:
                    print("   messages:", j.get("smartctl", {}).get("messages"))
                if ok:
                    break
        return
    log("main argv=%r" % sys.argv)
    if mode == "discovery":
        print(json.dumps(discover()))
    elif mode == "get":
        raid = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] not in ("", "auto", "undefined") else None
        print(json.dumps(get(sys.argv[2], raid)))
    else:
        print(json.dumps({"error": "bad mode: " + mode}))

if __name__ == "__main__":
    main()

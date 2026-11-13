#!/usr/bin/env python3
"""Read Emdoor EWMI state and use explicitly enabled Linux setting interfaces."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time

METHOD_GUID = "0B30FD67-1B62-4FCA-B10B-50DC7ADE6A39"
EVENT_GUID = "0B30FD67-1B62-4FCA-B10B-50DC7ADE6DDD"
AUTOPOWER_GUID = "C75665A3-4382-4646-ADC8-C81AE146E455"
MAINLINE_VERSION = "0.4.4"
MAINLINE_VERSIONS = ("0.4.4", "0.4.3", "0.4.2", "0.4.1", "0.4.0")
CANDIDATE_VERSION = "0.3.1-linux"
VERSION = MAINLINE_VERSION
SUPPORTED_VERSIONS = (*MAINLINE_VERSIONS, CANDIDATE_VERSION)
ATTRIBUTES = ("protocol_version", "performance_mode", "fn_lock", "win_lock", "copilot_mode_raw",
              "touchpad_enabled", "battery_mode_raw", "keyboard_backlight_raw", "battery_ec_raw")
SETTINGS = {"fn-lock": "fn_lock", "win-lock": "win_lock", "auto-power-on": "auto_power_on"}
PROFILE_TO_RAW = {"balanced": "1", "performance": "2"}
EVENT_DESCRIPTIONS = {
    0x00: "performance_mode_unknown",
    0x20: "keyboard_backlight_off",
    0x21: "keyboard_backlight_half",
    0x22: "keyboard_backlight_on",
    0x30: "touchpad_off",
    0x31: "touchpad_on",
    0x41: "performance_mode_balanced",
    0x42: "performance_mode_performance",
    0x50: "fn_unlock",
    0x51: "fn_lock",
    0xA0: "oem_control_center_hotkey",
    0xA1: "mic_mute_toggle_hotkey",
    0xA2: "refresh_rate_toggle_hotkey",
}
EC_BATTERY_FIELDS = (("B1DC", 0), ("B1FV", 2), ("B1FC", 4), ("B1CR", 7), ("B1RC", 9), ("B1VT", 11))


def doctor():
    expected = {"sys_vendor": "MECHREVO", "board_name": "WUJIE Series-Lark4-LNL",
                "bios_version": "EM_LNL326_V1.0.19"}
    identity = {}
    for name in expected:
        try:
            identity[name] = read(Path("/sys/class/dmi/id") / name)
        except OSError as exc:
            identity[name] = {"error": str(exc)}
    guid_presence = {guid: bool(list(Path("/sys/bus/wmi/devices").glob(guid + "-*")))
                     for guid in (METHOD_GUID, EVENT_GUID, AUTOPOWER_GUID)}
    secure_boot = {"status": "unavailable"}
    for path in Path("/sys/firmware/efi/efivars").glob("SecureBoot-*"):
        try:
            data = path.read_bytes()
            if len(data) == 5 and data[4] in (0, 1):
                secure_boot = {"status": "enabled" if data[4] else "disabled"}
        except OSError as exc:
            secure_boot = {"status": "unreadable", "error": str(exc)}
    version_path = Path("/sys/module/emdoor_wmi/version")
    try:
        module_version = read(version_path) if version_path.is_file() else None
    except OSError:
        module_version = None
    return {"kernel": os.uname().release, "architecture": os.uname().machine,
            "identity": identity, "target_match": identity == expected,
            "wmi_guid_presence": guid_presence,
            "kernel_headers_present": (Path("/lib/modules") / os.uname().release / "build/Makefile").is_file(),
            "module_loaded": Path("/sys/module/emdoor_wmi").exists(),
            "module_version": module_version,
            "secure_boot": secure_boot}


def read(path):
    return path.read_text().strip()


def write_sysfs(path, text):
    # Unbuffered write prevents BufferedWriter.close() from retrying after -EIO.
    with path.open("wb", buffering=0) as handle:
        handle.write((text + "\n").encode("ascii"))


def find_device(root, guid, driver):
    devices = list(root.glob(guid + "-*"))
    if len(devices) != 1 or (devices[0] / "driver").resolve().name != driver:
        raise RuntimeError(f"Expected one device bound to {driver}; load the Emdoor driver first")
    return devices[0]


class Paths:
    def __init__(self, root=Path("/sys/bus/wmi/devices"),
                 profile_root=Path("/sys/class/platform-profile")):
        self.method = find_device(root, METHOD_GUID, "emdoor-ewmi")
        self.event = find_device(root, EVENT_GUID, "emdoor-ewmi-events")
        self.autopower = find_device(root, AUTOPOWER_GUID, "emdoor-ewmi-autopower")
        self.profile_root = profile_root

    def setting(self, name):
        attribute = SETTINGS[name]
        return (self.autopower if name == "auto-power-on" else self.method) / attribute

    def profile_device(self):
        matches = [p.parent for p in self.profile_root.glob("*/name") if read(p) == "emdoor-wmi"]
        if len(matches) > 1:
            raise RuntimeError("Expected at most one emdoor-wmi platform-profile device")
        return matches[0] if matches else None


def read_status(paths):
    attributes = {name: paths.method / name for name in ATTRIBUTES}
    attributes["auto_power_on"] = paths.autopower / "auto_power_on"
    attributes["event_state"] = paths.event / "event_state"
    attributes["event_log"] = paths.event / "event_log"
    profile_dev = getattr(paths, "profile_device", lambda: None)()
    if profile_dev is not None:
        attributes["platform_profile"] = profile_dev / "profile"
    hwmons = list((paths.method / "hwmon").glob("hwmon*"))
    if len(hwmons) == 1:
        attributes.update({p.name: p for p in hwmons[0].glob("*_input")})
    result = {"attributes": {}, "errors": {}}
    for name, path in attributes.items():
        try:
            result["attributes"][name] = read(path)
        except OSError as exc:
            result["errors"][name] = {"errno": exc.errno, "message": str(exc)}
    return result


def decode_battery_ec_raw(text):
    """Decode two 15-byte EC battery samples matching Lark4 battery_regs ordering."""
    match = re.fullmatch(r"first=([0-9a-f]{30}) second=([0-9a-f]{30})", text.strip())
    if not match:
        raise ValueError("Malformed battery_ec_raw sample")
    raw_frames = [bytes.fromhex(part) for part in match.groups()]
    frames = []
    for raw in raw_frames:
        fields = {name: int.from_bytes(raw[offset:offset + 2], "little") for name, offset in EC_BATTERY_FIELDS}
        fields.update(B1ST=raw[6], BPCN=raw[13], EBCM=raw[14])
        frames.append(fields)
    first = frames[0]
    b1dc, b1fv, bpcn, b1cr = first["B1DC"], first["B1FV"], first["BPCN"], first["B1CR"]
    aml_derived = {
        "energy_full_uwh": ((b1dc * b1fv) // 1000) * 1000,
        "energy_now_uwh": ((((b1dc * bpcn) // 100) * b1fv) // 1000) * 1000,
        "power_now_uw": ((b1cr * b1fv) // 1000) * 1000,
        "voltage_now_uv": b1fv * 1000,
    }
    return {
        "frames_match": raw_frames[0] == raw_frames[1],
        "frames": frames,
        "aml_derived": aml_derived,
        "atomic_snapshot": False,
        "health_verified": False,
    }


def parse_events(text):
    records = []
    for line in text.splitlines():
        match = re.fullmatch(r"(\d+) (\d+) 0x([0-9a-f]{8})", line)
        if not match:
            raise ValueError("Malformed event history")
        code = int(match[3], 16)
        record = {
            "sequence": int(match[1]),
            "boottime_ns": int(match[2]),
            "code": code,
            "code_hex": f"0x{code:08x}",
            "description": EVENT_DESCRIPTIONS.get(code, "unknown"),
        }
        if records and record["sequence"] != records[-1]["sequence"] + 1:
            raise ValueError("Event history sequence is not contiguous")
        records.append(record)
    if len(records) > 32:
        raise ValueError("Event history exceeded its kernel bound")
    return records


def set_value(path, value, reader=read, writer=None):
    if value not in ("0", "1"):
        raise ValueError("Settings accept only 0 or 1")
    if not path.stat().st_mode & 0o222:
        raise PermissionError(f"Setting disabled at module load: {path.name}")
    original = reader(path)
    if original not in ("0", "1"):
        raise ValueError(f"Unexpected original state for {path.name}")
    if writer is None:
        writer = write_sysfs
    # Let the kernel decide no-op: a previous uncertain set may still need recovery.
    writer(path, value)
    actual = reader(path)
    if actual != value:
        raise RuntimeError(f"{path.name} readback {actual!r} does not match {value}")
    return {"original": original, "value": actual, "changed": original != actual}


def set_profile(profile_path, mode_path, target, reader=read, writer=None):
    if target not in PROFILE_TO_RAW:
        raise ValueError("Profile accepts only balanced or performance")
    if not profile_path.stat().st_mode & 0o222:
        raise PermissionError(f"Setting disabled at module load: {profile_path.name}")
    original = reader(profile_path)
    if original not in PROFILE_TO_RAW:
        raise ValueError(f"Unexpected original state for {profile_path.name}")
    if writer is None:
        writer = write_sysfs
    # Let the kernel decide no-op: a previous uncertain set may still need recovery.
    writer(profile_path, target)
    actual = reader(profile_path)
    if actual != target:
        raise RuntimeError(f"{profile_path.name} readback {actual!r} does not match {target}")
    raw_mode = reader(mode_path)
    if raw_mode != PROFILE_TO_RAW[target]:
        raise RuntimeError(f"{mode_path.name} readback {raw_mode!r} does not match {target}")
    return {"original": original, "value": actual, "raw_mode": raw_mode, "changed": original != actual}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Read machine/firmware prerequisites; no module required")
    status = commands.add_parser("status", help="Read current state; no settings changed")
    status.add_argument("--json", action="store_true")
    status.add_argument("--decode-raw", action="store_true", help="Decode raw EC battery telemetry fields")
    commands.add_parser("events", help="Show retained WMI event history")
    watch = commands.add_parser("watch", help="Observe events; Ctrl+C stops, no module loading")
    watch.add_argument("--seconds", type=int, default=60, choices=range(1, 3601), metavar="1..3600")
    setting = commands.add_parser("set", help="Use a setting explicitly enabled when loading the module")
    setting.add_argument("name", choices=(*SETTINGS, "profile"))
    setting.add_argument("value", choices=("on", "off", "0", "1", "balanced", "performance"))
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            result = doctor()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return int(not result["target_match"])
        paths = Paths()
        if args.command == "status":
            result = read_status(paths)
            if args.decode_raw and "battery_ec_raw" in result["attributes"]:
                try:
                    result["decoded"] = {"battery_ec": decode_battery_ec_raw(result["attributes"]["battery_ec_raw"])}
                except ValueError as exc:
                    result["decoded"] = {"battery_ec": {"error": str(exc)}}
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                for name, value in result["attributes"].items():
                    print(f"{name}: {value}")
                if "decoded" in result:
                    print(f"battery_ec_decoded: {json.dumps(result['decoded']['battery_ec'], ensure_ascii=False)}")
                for name, error in result["errors"].items():
                    print(f"{name}: {error['message']}", file=sys.stderr)
            return int(bool(result["errors"]))
        if args.command == "set":
            loaded_version = read(Path("/sys/module/emdoor_wmi/version"))
            if loaded_version not in SUPPORTED_VERSIONS:
                raise RuntimeError("Setting commands require a reviewed Emdoor module version")
            if args.name == "profile":
                if loaded_version not in MAINLINE_VERSIONS:
                    raise RuntimeError("Performance profile control requires mainline 0.4.0+")
                if args.value not in PROFILE_TO_RAW:
                    raise ValueError("Profile accepts only balanced or performance")
                profile_dev = paths.profile_device()
                if profile_dev is None:
                    raise PermissionError("Performance profile disabled at module load: allow_writes=0")
                print(json.dumps(set_profile(profile_dev / "profile", paths.method / "performance_mode", args.value)))
                return 0
            if args.value not in ("on", "off", "0", "1"):
                raise ValueError(f"{args.name} accepts only on, off, 0, or 1")
            value = "1" if args.value in ("on", "1") else "0"
            print(json.dumps(set_value(paths.setting(args.name), value)))
            return 0
        if args.command == "events":
            print(json.dumps(parse_events(read(paths.event / "event_log")), indent=2))
            return 0
        last = 0
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            records = parse_events(read(paths.event / "event_log"))
            new = [r for r in records if r["sequence"] > last]
            if new and last and new[0]["sequence"] != last + 1:
                print(json.dumps({"history_gap_after": last, "next_sequence": new[0]["sequence"]}), flush=True)
            for record in new:
                print(json.dumps(record), flush=True)
                last = record["sequence"]
            time.sleep(0.5)
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

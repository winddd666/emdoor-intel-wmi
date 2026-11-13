# emdoor-intel-wmi (`emdoor-wmi` 0.4.4)

Linux WMI platform driver, DKMS package, Arch `PKGBUILD`, and inspection CLI for Emdoor-based Intel laptops — initially verified on **MECHREVO Wujie 14 2026** (`WUJIE Series-Lark4-LNL`, Intel Core Ultra 5 226V Lunar Lake, BIOS `EM_LNL326_V1.0.19`).

针对亿道（Emdoor）Intel 平台 `\_SB.EWMI` 接口的 Linux 内核驱动、DKMS 安装包、Arch Linux `PKGBUILD` 与只读/控制工具 `emdoorctl`；当前已在 **机械革命无界 14 2026**（`MECHREVO / WUJIE Series-Lark4-LNL / EM_LNL326_V1.0.19`）实机通过 `0.4.4` 全套回归验证。

> **Note / 注意**：社区项目 `fewtarius/emdoor-wmi` 面向 **AMD 亿道平台**（`8600ACCE-...` GUID、AMD SMU 端口），与 Intel `\_SB.EWMI`（`0B30FD67-1B62-4FCA-B10B-50DC7ADE6A39`）协议及 EC 偏移完全不同，请勿混用。

## Supported Features / 支持功能

- **`hwmon` (`emdoor_ewmi`)**: Dual fan RPM (`Fan 1` / `Fan 2`) and 4 EC temperatures (`CPU`, `GT VR`, `Ambient`, `Charger`).
- **`platform_profile` (`emdoor-wmi`)**: `balanced` (`1`) / `performance` (`2`) synced with `Fn + X` hotkeys, with bounded `EWMI_MAILBOX_RETRIES` handling `SPFM` firmware `EC5F` race. Opt-in via `allow_writes=1`.
- **Keyboard & Firmware Switches**: `fn_lock`, `win_lock`, `touchpad_enabled`, and `auto_power_on` (`WMCC`). Writes require explicit opt-in (`allow_lock_writes=1`, `allow_autopower_writes=1`).
- **Hotkeys (`Emdoor EWMI hotkeys`)**: `sparse_keymap` reporting for touchpad toggle (`0x30`/`0x31`) and OEM keys (`0xA0`–`0xA2`), plus safe ignore entries for EC-handled hotkeys (`Fn + X`, `Fn + Esc`, `F8` backlight).
- **Read-only Diagnostic Attributes (`0400`)**: `protocol_version`, `performance_mode`, `copilot_mode_raw`, `keyboard_backlight_raw`, `battery_mode_raw`, `battery_ec_raw`, `event_state`, `event_log`.
- **Kernel Requirement / 内核要求**: Uses modern WMI marshalling APIs (`wmidev_invoke_method`, `notify_new`) and `<linux/unaligned.h>`, verified on Linux `7.2.14` and `7.3.4`.

All write flags default to **disabled (`0`)** for read-only safety. See [ABI documentation](Documentation/ABI/testing/sysfs-bus-wmi-devices-emdoor-ewmi) and [docs/FIRMWARE_MAP.md](docs/FIRMWARE_MAP.md).

## Quick Install / 快速安装

### Arch Linux (from cloned repo / 本地打包)

```sh
git clone https://github.com/winddd666/emdoor-intel-wmi.git
cd emdoor-intel-wmi/packaging/arch
makepkg -si
```

### Generic Linux (DKMS)

```sh
sudo dkms add ./driver
sudo dkms build emdoor-wmi/0.4.4
sudo dkms install emdoor-wmi/0.4.4
sudo install -Dm755 emdoorctl.py /usr/bin/emdoorctl
sudo install -Dm644 packaging/arch/emdoor-wmi.conf /etc/modprobe.d/emdoor-wmi.conf
```

### Opt-in Write Support / 按需开启写入

Uncomment desired module options in `/etc/modprobe.d/emdoor-wmi.conf` (or pass via `modprobe emdoor_wmi ...`):

```conf
options emdoor_wmi allow_writes=1 allow_lock_writes=1 allow_autopower_writes=1
```

## CLI Usage / 使用方式

```sh
emdoorctl status
emdoorctl status --decode-raw
sudo emdoorctl set-profile performance
sudo emdoorctl set-fn-lock on
sudo emdoorctl set-auto-power-on off
```

## Verification Baseline (`0.4.4` on Linux `7.3.4-arch1-1`)

- `W=1 KCFLAGS="-Werror"` clean build & `checkpatch.pl --no-tree --strict` (`0 errors, 0 warnings, 0 checks`).
- 1500 readonly attribute/hwmon stress reads (`ADP1=1`): 0 errors, 0 state drift.
- 20 `platform_profile` switches + 40 unbuffered idempotent writes: 0 errors (`421.7–438.8 ms` per switch).
- `fn_lock`, `win_lock`, and `auto_power_on` write/readback & restore cycles: pass.
- 20-step isolated DKMS lifecycle (`dkms-3.4.4`): pass.

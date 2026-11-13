# Emdoor Intel EWMI 0.4.4（主线统一版本）

仅匹配 `MECHREVO / WUJIE Series-Lark4-LNL / EM_LNL326_V1.0.19`。
当前统一了 `0.1.2`（`platform_profile`）与 `0.3.1-linux`（Copilot/键盘背光/电池只读原始属性、32 条 WMI 事件历史、Fn/Win 锁设置后等待与恢复、`WMCC` 通电自启、独立写入参数），将板级 EC 寄存器偏移和邮箱时序抽离为 `dmi_system_id.driver_data` 挂载的 `struct ewmi_board_config`；在 `0.4.1` 针对 `_QF6`/`_QF9` 热键与共享邮箱 `EC5D` 并发碰撞增加有界重试（`EWMI_MAILBOX_RETRIES = 3` + `mailbox_event_seq` 失效检测），在 `0.4.2`/`0.4.3` 排查 `platform_profile` 竞态后，通过 `DSDT.dsl` (`SPFM`) 确认固件在 `Notify(IETM, 0x88)` 与写入 `EC5F` 之前就先武装了 `ZZTT`/`TIZZ`，会在 `EC` 消费旧 `EC5F` 时返回 `RTNC=0` 却丢失本次切换，并于 `0.4.4` 将 `RTNC=1` 与“03/09 仍为旧值”统一进 `EWMI_MAILBOX_RETRIES = 3` 重试闭环（每次 `03/0A` 后即时+100 ms 复查 `03/09`，不匹配即重发 `03/0A`）。

## 编译

```sh
make
```

依赖匹配当前内核的 headers、C 编译器和 make；编译开启 `W=1 KCFLAGS="-Werror"`。运行时依赖 `ACPI_WMI`、`HWMON`、`INPUT`、`INPUT_SPARSEKMAP` 和 `ACPI_PLATFORM_PROFILE`。

## 临时加载与读取

```sh
sudo modprobe sparse_keymap
sudo insmod ./emdoor-wmi.ko
```

默认参数下三类写入全部关闭（`allow_writes=0 allow_lock_writes=0 allow_autopower_writes=0`）：
- 不注册 `platform_profile`；
- `fn_lock`、`win_lock`、`auto_power_on` 权限为 `0444`；
- `copilot_mode_raw`、`keyboard_backlight_raw`、`battery_mode_raw`、`battery_ec_raw` 与事件诊断 `event_state`/`event_log` 为 `0400`；
- 可使用 `python3 ../emdoorctl.py status`、`sensors` 查看状态与传感器，使用 `sudo rmmod emdoor_wmi` 卸载。

不要与原始 `probe/emdoor_probe.ko` 同时使用。

## 独立写入参数

- `allow_writes=1`：注册 `platform_profile`（`emdoor-wmi`：`balanced`/`performance`）。包含针对 `SPFM` 固件竞态的统一有界重试（`EWMI_MAILBOX_RETRIES = 3`：对 `RTNC=1` 或 `03/09` 即时及单次 100 ms 复查仍为旧值的情况重发同一绝对模式 `03/0A`）与 `profile_dirty` 保护。注意桌面 `power-profiles-daemon` 可能在 profile 注册后主动切换档位。
- `allow_lock_writes=1`：允许写入 `fn_lock` / `win_lock`，包含 `0.3.1-linux` 的 100 ms 设置后等待与 `lock_dirty` 恢复保护。
- `allow_autopower_writes=1`：允许写入 `emdoor-ewmi-autopower` 的 `auto_power_on`，等待 100 ms 并核对 EC `0xF5` 读回。

`query_settle_ms`（`0..200`，默认 `100`）和 `query_direct`（默认 `true`）为邮箱查询诊断参数。详细接口定义见 [`ABI.md`](ABI.md)。

## 验证状态说明

- **源码级与宿主测试（针对 `0.4.4` 当前代码，内核 `7.3.4-arch1-1`）**：`W=1 -Werror` 编译、`checkpatch.pl --no-tree --strict`、宿主 C 函数单元测试（含 `EC5D`/热键并发注入、`SPFM` 旧 `EC5F` 静默丢单重试与 100 ms 延迟回读测试）、Python CLI/检查器单元测试（含 `-EIO` 无缓冲单次 `write()` 回归测试），以及非 root 隔离 DKMS 构建与 `source-only` 归档生成。
- **历史分支实机验收证据（来自对应版本归档，未改动历史基线）**：
  - `0.1.1` / `0.1.2`：`3300` 次只读压力采样零错误；`20` 次性能切档、`40` 次重复设置及原档恢复通过（本机电池供电下两档 PL1/PL2 均为 30/40 W）。
  - `0.2.1-battery-ro`：`1200` 次电池与状态交错读取零错误。
  - `0.3.1-linux`：`Fn/Win/通电自启` 各四次设置/重复请求与恢复通过，隔离 DKMS 20 条命令通过（复用读取路径一致的 `0.3.0` 1500 次只读基线）。
  - `0.4.0`：已通过 1500 次只读 + 三项设置/恢复回归、20 步隔离 DKMS、手动 `s2idle` 及全部 `EWMI 0xD0` 物理热键实机事件捕获（并由此定位 `EC5D` 与 `_QF6`/`_QF9` 并发碰撞，在 `0.4.1` 中修复）。
  - `0.4.1`：已通过 1500 次只读 + 三项设置/恢复回归、30 秒 15 事件 `Fn + Esc`/`F8` 高频热键并发 375/375 零错误与 20 步隔离 DKMS（`7.2.14-arch1-1`）；`7.3.4-arch1-1` 下 `run-profile-check.sh --load-seconds 0 --switches 20` 在第 6/20 次切换失败（`SPFM` `TIZZ`-before-`EC5F` 竞态 + `BufferedWriter` 双写掩盖，见 `0.4.3`/`0.4.4` 分析）。
  - `0.4.2` / `0.4.3`：`7.3.4-arch1-1` 下 `run-profile-check.sh --load-seconds 0 --switches 20` 分别在第 11/20 次（`profiles-20261113T154107Z.json`）与第 12/20 次（`profiles-20261113T160227Z.json`，1.65 s 轮询 16 次 `actual` 仍为 `1`）复现同一竞态；由此定位 `SPFM` 静默消费旧 `EC5F` 与 Python `Path.write_text()` 在 `-EIO` 下的二次 `write()` 假象，并于 `0.4.4` 一并修复。
- **`0.4.4` 待实机复验范围**：执行 `sudo sh ~/.config/emdoor-dev/run-profile-check.sh --load-seconds 0 --switches 20` 复测 `platform_profile` 20 次切档；如有需要亦可按同样脚本复测 `run-mainline-check.sh` 与 `dkms-check.py install-check`。Windows 下已实机确认控制中心无电池限充/Copilot 锁/强冷开关，交流满载功率差异、背光计时编码、真实断电接电自启、宿主 DKMS 安装与 Secure Boot 仍待实测。

## DKMS

`dkms.conf` 指定 `PACKAGE_VERSION="0.4.4"`、`AUTOINSTALL=no`、架构 `^x86_64$`，构建命令传入 `KDIR=${kernel_source_dir}`。使用 `python3 ../dkms-check.py prepare` 可在私有隔离目录验证 add/build 与 source-only 归档。

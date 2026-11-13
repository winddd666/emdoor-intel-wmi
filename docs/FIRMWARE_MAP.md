# Emdoor Lark4-LNL 固件、OSD 与 EC 静态逆向对照

本文件汇总已在本机导出的 [`acpi-dump/DSDT.dsl`](acpi-dump/DSDT.dsl)、[`acpi-dump/ewmi.mof`](acpi-dump/ewmi.mof) 以及官方支持包 [`vendor-research/`](vendor-research) 中静态分析得到的协议、事件与寄存器映射，用于界定哪些能力已经实机验证、哪些仍属静态线索或待实测项。适用目标：`MECHREVO / WUJIE Series-Lark4-LNL / EM_LNL326_V1.0.19`。

---

## 1. `\_SB.EWMI._WDG` WMI 块总览（共 17 项）

依据 [`acpi-dump/DSDT.dsl`](acpi-dump/DSDT.dsl#L25972-L26023) 与 [`acpi-dump/ewmi.mof`](acpi-dump/ewmi.mof#L1-L129)：

| 序号 | GUID | Obj ID | Flags | AML 方法 / MOF 类名 | 状态与说明 |
| --- | --- | --- | --- | --- | --- |
| 0 | `05901221-D566-11D1-B2F0-00A0C9062910` | `AB` | `0x00` | `WQAB`（二进制 MOF） | 标准 WMI MOF 数据块（已导出为 [`ewmi.mof`](acpi-dump/ewmi.mof)） |
| 1 | `72CCF8D6-FF88-4B77-AED9-702724DA0925` | `AA` | `0x02` | `WMAA` / `AcpiTest_EcLid` | 含 `Buffer(0x08)` + `CreateField(Local0, 0x20, 0x3F, RES3)` 越界声明，禁用 |
| 2 | `C60B0BC4-B53B-41A6-9D24-51E33C742906` | `AC` | `0x02` | `WMAC` / `EmdAcpi_LoadDefault` | 同上越界声明，禁用 |
| 3 | `861F913E-1B25-41E5-B4DD-D1FD9091713F` | `AD` | `0x02` | `WMAD` / `EmdAcpi_ECInformation` | 同上越界声明，禁用 |
| 4 | `971F2336-4433-4124-8B8E-28FB454E88AA` | `AE` | `0x02` | `WMAE` / `EmdAcpi_LedControl` | 同上越界声明，禁用 |
| 5 | `D06385DE-BC53-4B9B-85DC-674936D9549F` | `AF` | `0x02` | `WMAF` / `EmdAcpi_FanControl` | 同上越界声明，其中 `MethodID=2 Arg2=1/2` 写 `ZZTT=0x12, TIZZ=0/1` 切换风扇控制 |
| 6 | `5399E2B7-1046-4058-936D-882EE7492C5B` | `BA` | `0x02` | `WMBA` / `EmdAcpi_GetTypeCVBUS` | 同上越界声明，禁用 |
| 7 | `87A5E402-3681-432A-9D27-B5D40EE8C489` | `BB` | `0x02` | `WMBB` / `EmdAcpi_PowerPlanMode` | 同上越界声明，直接写 `PPMD` `1/2/3`（无 DPTF/SMI 流程），禁用 |
| 8 | `67DAF0CA-A83E-4D58-B462-CD7201A2E6D6` | `BC` | `0x02` | `WMBC` / `EmdAcpi_FunctionKey` | 同上越界声明，禁用 |
| 9 | `2C70BE1B-C60E-452D-9413-EE888A830CBD` | `BD` | `0x02` | `WMBD` / `EmdAcpi_Keyboard_BackLight` | 同上越界声明，禁用 |
| 10 | `666D92BD-F6E0-4979-9B9F-0436A9A379D0` | `BE` | `0x02` | `WMBE` / `EmdAcpi_Battery_Charge_Mode` | 同上越界声明，直接写 `EBCM` (`0xA8`) `0/1/2/3`，与 `GBMM/SBMM` 非同一路径，禁用 |
| 11 | `8BE9E7F1-B926-492B-8F7B-3E34243ACDC7` | `BF` | `0x02` | `WMBF` / `EmdAcpi_Keyboard_BackLight_Status` | 同上越界声明，禁用 |
| 12 | `DBCDEBCC-2EB0-44A7-9413-7E566B42E2CD` | `CA` | `0x02` | `WMCA` / `EmdAcpi_SetSecureBoot` | 同上越界声明，禁用（注：DSDT 第 27771 行存在 `WMCB` 定义，但 `_WDG` 中从 `CA` 直接跳至 `CC`，未注册 `CB`） |
| 13 | `C75665A3-4382-4646-ADC8-C81AE146E455` | `CC` | `0x02` | `WMCC` / `EmdAcpi_AutoPowerOn` | **无越界声明**，控制 `H_EC.ATPU` (`0xF5`: `0x00`/`0xA0`)；已纳入 `emdoor-ewmi-autopower` |
| 14 | `0B30FD67-1B62-4FCA-B10B-50DC7ADE6A39` | `CD` | `0x02` | `WMCD` / `OemWMIMethod` | **无越界声明**，核心方法分发表（`GVER`/`GFNS`/`GWLS..TEST`）；已纳入 `emdoor-ewmi` |
| 15 | `0B30FD67-1B62-4FCA-B10B-50DC7ADE6DDD` | `\xd0\x00` | `0x08` | `_WED(0xD0)` / `OemWMIEvent` | **事件块**（返回整数 `MREN`）；已纳入 `emdoor-ewmi-events` |
| 16 | `C75665D0-4382-4646-ADC8-C81AE146E984` | `\xd3\x00` | `0x08` | `Notify(EWMI, 0xD3)` / `EmdAcpi_NotifiyUSBOC` | 被动通知块（[`_Q61`](acpi-dump/DSDT.dsl#L5031-L5034) USB 过流事件与 `WMCB` 触发）；无 `_WED(0xD3)` 返回体，当前驱动未绑定 |

---

## 2. EC `_Qxx` 事件码、`EmOSDFunction.exe` 与 Linux 键位对照

依据 `DSDT.dsl` [`H_EC._QF4.._Q99`](acpi-dump/DSDT.dsl#L4828-L4935) 与静态反编译的 [`vendor-research/em-osd-battery-cil.txt`](vendor-research/em-osd-battery-cil.txt#L2-L210)（`EmOSDFunction.ShowOSD::Window_Loaded`）：

| `MREN` 事件码 | 触发 AML `_Qxx` | AML 内附带动作 | 官方 `EmOSDFunction.exe` 行为（静态 CIL） | `emdoor-wmi.c` (`0.4.0`) 当前映射 | `emdoorctl.py` 语义标签 |
| --- | --- | --- | --- | --- | --- |
| `0x00` | `_QF4` | `PPMD` 非 `1/2` 时 | 未处理 | `KE_IGNORE`（留空记录事件） | `performance_mode_unknown` |
| `0x20` | `_QF9` | `EC5D == 0` | 显示 `KeyboardLedOff.png` | `KE_IGNORE`（固件自切换） | `keyboard_backlight_off` |
| `0x21` | `_QF9` | `EC5D == 1` | 显示 `KeyboardLedHalf.png` | `KE_IGNORE` | `keyboard_backlight_half` |
| `0x22` | `_QF9` | `EC5D == 2` | 显示 `KeyboardLedOn.png` | `KE_IGNORE` | `keyboard_backlight_on` |
| `0x30` | `_QF7` | 置 `TPEN=0`, `CMSW(0xDF, 0xB0)` | 显示 `TouchPadOff.png` | `KE_KEY` -> `KEY_TOUCHPAD_OFF` | `touchpad_off` |
| `0x31` | `_QF7` | 置 `TPEN=1`, `CMSW(0xDF, 0xB1)` | 显示 `TouchPadOn.png` | `KE_KEY` -> `KEY_TOUCHPAD_ON` | `touchpad_on` |
| `0x41` | `_QF4` | `PPMD == 1` -> `ODV0=1`, `Notify(IETM, 0x88)`, `ISMI(0xA3)` | 显示 `PerformanceMode1.png` 并调用 `GetPowerMode` | `KE_IGNORE` + 通知 `platform_profile` | `performance_mode_balanced` |
| `0x42` | `_QF4` | `PPMD == 2` -> `ODV0=3`, `Notify(IETM, 0x88)`, `ISMI(0xA3)` | 显示 `PerformanceMode2.png` 并调用 `GetPowerMode` | `KE_IGNORE` + 通知 `platform_profile` | `performance_mode_performance` |
| `0x50` | `_QF6` | `EC5D == 0` | 显示 `FnUnlock.png`（确认“功能状态”即 Fn 锁） | `KE_IGNORE` | `fn_unlock` |
| `0x51` | `_QF6` | `EC5D == 1` | 显示 `FnLock.png` | `KE_IGNORE` | `fn_lock` |
| `0xA0` | `_Q99` | 无 | 未在 `ShowOSD` 分支处理（控制中心键） | `KE_KEY` -> `KEY_PROG1` | `oem_control_center_hotkey` |
| `0xA1` | `_QF8` | 无 | 查询 `AudioHelper.GetAudioMuteState`，翻转静音并显示 `MicModeOn/Off.png` | `KE_KEY` -> `KEY_PROG2` | `mic_mute_toggle_hotkey` |
| `0xA2` | `_QF5` | 无 | 查询 `GetCurrentRefreshRate` 并在 `60Hz <-> 120Hz` 间调用 `SetScreenResolution` | `KE_KEY` -> `KEY_PROG3` | `refresh_rate_toggle_hotkey` |

**实机已验证与待物理核对边界**：
- **已实机观测（`2026-11-13` [`mainline-20261113T091822487735Z.json`](driver/mainline-20261113T091822487735Z.json) 与 [`mainline-20261113T092858455263Z.json`](driver/mainline-20261113T092858455263Z.json)）**：已在实机上触发并捕获所有物理可按的 `EWMI 0xD0` 热键：
  - 第一次 120 秒观察（`13` 条）：键盘背光 `0x22`/`0x20`/`0x21`（5 条）、麦克风静音 `0xA1`（2 条）、触控板开关 `0x30`/`0x31`（6 条）；
  - 第二次 30 秒隐藏组合键观察（`8` 条）：`Fn + X` 发出 `0x41`/`0x42` 并实测翻转固件 `performance_mode`（`2 -> 1 -> 2`）、`Fn + R` 发出 `0xA2`（`refresh_rate_toggle_hotkey`）、`Fn + Esc` 发出 `0x50`/`0x51` 并实测翻转固件 `fn_lock`（`1 -> 0 -> 1`）；
  - `Insert`/`Delete`/`End`/`PgUp`/`PgDn`/`Home` 组合键直接走 `i8042` 键盘扫描码通路，未触发 WMI `0xD0` 事件。
- **待核对**：`0xA1`（麦克风静音）与 `0xA2`（刷新率切换）在官方软件体系下由 Windows 用户态 `EmOSDFunction.exe` 响应并执行实际动作，而 AML 本身**不修改硬件**。目前仍待核对 `0xA1`/`0xA2`/`0x41`/`0x42`/`0x50`/`0x51` 等按键是否同时从 `AT Translated Set 2 keyboard` (`i8042`) 或桌面输入层重复发出键码（可对照 `libinput debug-events`），确认无冲突后再评估是否将 `0xA1` 改为 `KEY_MICMUTE`（或保持 `KEY_PROG*` 由桌面绑定）。

### 2.1 本机物理键盘热键分流（Lark4-LNL 实机 + 官方说明书第 12/19 页 + DSDT 对照）

对照机械革命官方电子版说明书第 12 页键帽图示、第 19 页（第二章第 7 节“Fn 组合键”）以及本机 `DSDT.dsl` / `/proc/bus/input/devices`，`WUJIE Series-Lark4-LNL` 的热键实际分属四条独立通路：

| 物理按键 | 官方说明功能 | 底层路由 / ACPI 对象 | Linux 当前接管层 | 实机状态 |
| --- | --- | --- | --- | --- |
| `F1` / `F2` | 屏幕亮度 - / + | EC `_Q07` / `_Q06` -> `\_SB.HIDD.HPEM(0x13 / 0x14)` | `intel-hid`（`INTC107B:00`） | 桌面直接接管，不经过 `EWMI` |
| `F3` | 触控板开关 | `EWMI` `0xD0`（`0x30` / `0x31`） | `emdoor-wmi`（`Emdoor EWMI hotkeys`） | 实机已捕获 `#7–#12`（共 6 条） |
| `F4` / `F5` / `F6` | 静音 / 音量 - / + | 标准键盘扫描码（`i8042` / `atkbd`） | 桌面音频层 | 标准键盘通路，不经过 `EWMI` |
| `F7` | 麦克风开关 | `EWMI` `0xD0`（`0xA1`） | `emdoor-wmi`（`KEY_PROG1`，预留 `KEY_MICMUTE` 评估） | 实机已捕获 `#5–#6`（共 2 条） |
| `F8` | 键盘背光调节 | `EWMI` `0xD0`（`0x20` / `0x21` / `0x22`） | `emdoor-wmi` | 实机已捕获 `#1–#4`、`#13`（共 5 条） |
| `F9` | 飞行模式开关 | EC `_Q08` -> `\_SB.HIDD.HPEM(0x08)` | `intel-hid`（`KEY_RFKILL`） | 内核原生接管，优先级天然高于 `EWMI` |
| `F10` | 睡眠（`Zz`） | 标准 ACPI / 键盘 `KEY_SLEEP` | `systemd-logind`（触发 `s2idle`） | 系统电源管理直接接管 |
| `F11` / `F12` / `Delete` / `Fn+方向键` | `Insert` / `PrtSc` / `Delete` / `Home/End/PgUp/PgDn` | `i8042` 标准扫描码 | 内核 `atkbd` | 实机按下无 WMI 事件 |
| 右侧 `Copilot` 键 | Copilot 快捷键 | Windows 标准 Copilot 组合扫描码（`Left Shift + Left Meta + F23`） | `i8042`（`atkbd`） | 不走 `EWMI 0xD0`；`WMCD 03/05–06` 仅控制其锁开关 |
| 无独立物理键（预留 `_Q99`） | 控制中心（`0xA0`） | EC `_Q99` -> `EWMI` `0xD0`（`0xA0`） | 驱动键码表已保留映射 | 本机键盘无此键帽 |
| `Fn + Esc`（无独立键帽图标） | Fn lock 切换 | EC `_QF6` -> `EWMI` `0xD0`（`0x50` / `0x51`） | 驱动状态接口 `fn_lock` 已实测写通 | 实机已捕获 `#5–#8`（`1 -> 0 -> 1` 同步更新 `fn_lock`） |
| `Fn + X`（无独立键帽图标） | 性能模式切换 | EC `_QF4` -> `EWMI` `0xD0`（`0x41` / `0x42`） | 驱动键码表与 `profile_work` 联动 | 实机已捕获 `#1–#2`（`2 -> 1 -> 2` 同步更新 `performance_mode`） |
| `Fn + R`（无独立键帽图标） | 屏幕刷新率切换 | EC `_QF5` -> `EWMI` `0xD0`（`0xA2`） | `KE_KEY` -> `KEY_PROG3` | 实机已捕获 `#3–#4`（`refresh_rate_toggle_hotkey`） |

---

## 3. `WMCD` 邮箱时序差异与未开放功能静态证据

### 3.1 `SWLS` / `SFLS` / `SCLS` 时序不对称
在 [`DSDT.dsl`](acpi-dump/DSDT.dsl#L28020-L28144) 中：
- `SWLS` (`03/02` Win 锁设置) 写入 `ZZTT=0x10` 后有 `Sleep(0x14)`（20 ms），然后写入 `TIZZ=0x03/0x04`，写 `TIZZ` 后**无等待**；
- `SFLS` (`03/04` Fn 锁设置) 与 `SCLS` (`03/06` Copilot 锁设置) 写入 `ZZTT=0x10` 后**零等待**即检查 `ZZTT == 0x10`，随后写 `TIZZ`，写 `TIZZ` 后同样**无等待**；
- `GFLS`/`GWLS`/`GCLS`/`GKBT` 读前仅等 `Sleep(0x14)`（20 ms，[`GBMM`](acpi-dump/DSDT.dsl#L28289-L28304) 甚至零等待），且实际硬件邮箱会在更长窗口内更新；这从 AML 源码层面解释了为何 `0.3.1-linux` 与 `0.4.0` 在 `ewmi_switch_store()` 中增加 100 ms `setter_settle_ms` 并配对重查后，静态工况下 `fn_lock`/`win_lock` 连续读写回归零错误。
- **共享 `EC5D` 与热键并发碰撞（实机捕获、修复并实机复测通过）**：[`_QF6`](acpi-dump/DSDT.dsl#L4858-L4871)（`Fn + Esc` 读写 `EC5D` 设 `0x50`/`0x51`）与 [`_QF9`](acpi-dump/DSDT.dsl#L4897-L4915)（`F8` 键盘背光写 `EC5D` 设 `0x20`/`0x21`/`0x22`）会在热键按下时异步覆写同一个 `EC5D` 寄存器。在 `0.4.0` 的 [`mainline-20261113T092858455263Z.json`](driver/mainline-20261113T092858455263Z.json) 实机观察中，恰好有两次 `03/05` (`copilot_mode_raw`) 与 `03/01` (`win_lock`) 落在 `#6`/`#8` `0x51` 热键事件正发生时的 `100 ms` 等待窗内，分别读到非本命令的 `0x01` 并返回 `-EIO`（甚至在理论上存在被 `0x22` 的 `0x02` 字节误认成 `win_lock=1` 的风险）。`0.4.1` 通过无条件注册 `ewmi_method_event` 监听 `0x20–0x22`/`0x50–0x51` 递增 `mailbox_event_seq`，并在 `ewmi_call_status()` 遇 `-EIO`/`-EAGAIN` 或 `mailbox_event_seq` 变化时最多重试 `EWMI_MAILBOX_RETRIES`（`3`）次；随后在 `0.4.1` 的 [`mainline-20261113T094915450763Z.json`](driver/mainline-20261113T094915450763Z.json) 实机 30 秒高压观察中触发 `15` 次 `Fn + Esc`/`F8` 互扰事件，实测捕获并透明重试恢复全部 `10` 次碰撞读取（`255.6–276.2 ms`），最终 `375/375` 次读取 **0 错误、0 内核警告、0 假翻转**。
- **`SPFM` (`03/0A`) `TIZZ`-before-`EC5F` 丢单竞态与 `BufferedWriter` 双写假象（实机捕获、`0.4.4` 定位并修复）**：在 [`DSDT.dsl`](acpi-dump/DSDT.dsl#L28209-L28242) 中，`SPFM` 在执行 `ODV0`、`Notify(IETM, 0x88)` 及 `ECWT(mode, EC5F)` **之前**就已经写入 `ZZTT=0x10` 与 `TIZZ=0x07` 武装 EC 邮箱。当 EC 在 `Notify(IETM, 0x88)` 期间提前扫描并消费旧 `EC5F` 同时清零 `ZZTT`/`TIZZ` 时，随后写入的 `EC5F=mode` 不会被 EC 执行，AML 仍在 `Sleep(0x64)` + `ISMI(0xA3)` 后返回 `RTNC=0` 而 `H_EC.PPMD` 保持旧值。实机 `0.4.1` ([`profiles-20261113T152521Z.json`](driver/profiles-20261113T152521Z.json) 第 6/20 次)、`0.4.2` ([`profiles-20261113T154107Z.json`](driver/profiles-20261113T154107Z.json) 第 11/20 次) 与 `0.4.3` ([`profiles-20261113T160227Z.json`](driver/profiles-20261113T160227Z.json) 第 12/20 次：1.65 s 内连续 16 次 `03/09` 仍全返回旧值 `1`) 均命中同一竞态；而此前 `-EIO` 后出现的 `"observed_after_error == target"`，源于用户态 Python `Path.write_text()` 在 `-EIO` 返回后经由 `BufferedWriter.close()` 冲刷缓冲触发的隐式二次 `write()` 系统调用（再次进入 `SPFM` 时 `EC5F` 已被预置为新档位）。`0.4.4` 一方面在 `ewmi_profile_set()` 中移除无效的 1.5 s 单纯轮询，改为在 `EWMI_MAILBOX_RETRIES`（`3`）上限内于每次 `03/0A` 后即时及等待一次 `100 ms` 复查 `03/09`，对 `RTNC=1` 或 `03/09` 仍为旧值统一重发 `03/0A`；另一方面在 Python 工具中全面改用 `open("wb", buffering=0)` 消除 `-EIO` 隐式二次写入。

### 3.2 Windows 官方控制中心实机界面核对（`2026-11-13`）
结合本机 Windows 下运行官方控制中心的实机核对（用户目视确认 + [`vendor-research/lnl-uwp/Strings_Json/zh-cn.json`](vendor-research/lnl-uwp/Strings_Json/zh-cn.json) 与官方说明书第 48–53 页），本机型实际界面裁切如下：
- **性能模式**：控制中心仅显示两档：**“节能模式”**（`strOSDOffice` = `OfficeMode` / `PPMD=1`）与**“性能模式”**（`strOSDTurbo` = `TurboMode` / `PPMD=2`）；无第三档或自定义档位（Linux `platform_profile` 对应映射 `1 -> balanced`、`2 -> performance`）。
- **键盘灯效计时 (`03/0B` / `03/0C`)**：存在“不触碰关闭键盘背光计时”选项（`strCloseTimer`），但因 `UEFI_Firmware.exe` 已确认存在大小端不一致且单位尚未实测，仍保持只读 `keyboard_backlight_raw`。
- **电池充电模式 (`03/0D` / `03/0E`)、Copilot 锁 (`03/05` / `03/06`) 与强冷**：控制中心界面均未开放相关开关（`battery_mode` 由 [`battery-windows-20261113T2206207896849Z-21800.json`](vendor-research/battery-windows-20261113T2206207896849Z-21800.json) 确认为 `absent`）。

### 3.3 `CopilotKey` (`03/05` `GCLS` / `03/06` `SCLS`)
- **AML 语义**：[`GCLS`](acpi-dump/DSDT.dsl#L28096-L28119) 将 EC `0x5D` 读出的 `0x00 -> 0`、`0x04 -> 1`；[`SCLS`](acpi-dump/DSDT.dsl#L28121-L28144) 写 `ZZTT=0x10` 后写 `TIZZ=0x05`（入参 `1`）或 `0x06`（入参 `0`）。
- **厂商 UI/原生程序证据**：[`UEFI_Firmware.exe`](vendor-research/uefi-copilot-native.txt) 导出名为 `GetCopilotKey` / `SetCopilotKey`；解包自 `lnl-uwp/` 的 `resources.pri` 与 `GamingCenter3_Cross.dll` 包含 `"Copilot Key Lock"` / `"Copilot key, click On to disable the Copilot key."`、`"Copilot键锁定"` / `"Copilot键，单击On禁用Copilot键。"` 以及符号 `COPILOTKEY_LOCK` / `COPILOTKEY_UNLOCK`、`CopilotKeyToggleSupport`。
- **实机现状**：`0.4.0` 只读测试下 `copilot_mode_raw` 始终为 `0`，官方控制中心实机未呈现此开关；暂保持只读诊断。

### 3.4 `LightTime` / `CloseTimer` (`03/0B` `GKBT` / `03/0C` `SKBT`)
- **AML 与厂商 UI 证据**：[`GKBT`/`SKBT`](acpi-dump/DSDT.dsl#L28244-L28287) 读写两字节 `KBTL`/`KBTH`；[`UEFI_Firmware.exe`](vendor-research/uefi-lighttime-native.txt) 导出名为 `GetLightTime` / `SetLightTime`。[`Strings_Json/zh-cn.json`](vendor-research/lnl-uwp/Strings_Json/zh-cn.json#L64-L65) 与 [官方说明书第 53 页](vendor-research/lnl-manual-page-53.png) 显示控制中心对应功能为**“灯效睡眠时间 / 闲置计时器”**（默认 `"不关闭"`），**并非**键盘背光亮度等级控制。
- **厂商二进制已知端序不一致**：`GetLightTime`（RVA `0x2C00`）按大端 `(out[1] << 8) | out[2]` 拼接返回值，而 `SetLightTime`（RVA `0x2E00`）按小端 `in[2] = arg & 0xff; in[3] = arg >> 8` 发送参数。
- **实机现状**：本机只读实测 `keyboard_backlight_raw` 为 `00 00`；单位（秒/分钟/编码表）与当前主板写入行为均未实测，驱动暂保留只读原始字节。

### 3.5 多机型定制标识与风扇强冷线索
- [`setup.ini`](<vendor-research/lnl-selected/Driver/21_机械革命控制中心/ControlCenter_5.56.1.15_Mechrevo_GX/setup.ini#L12-L13>) 声明 `CustomizeTarget = 56`；核心服务 `GCUService.exe` 的关键业务方法被 ConfuserEx/占位存根（RVA `0x7fe860`）保护，因此通用包中的选项是否在本机实际界面显示仍以实机界面为准。
- `TEST` (`03/0F`) 仅在 `EmOSDFunction.exe` `SystemEvents_SessionEnding`（`SystemShutdown`）路径观察到发送 `[3, 15, 1, 1]`；`WMAF MethodID=2` 含 `CreateField RES3` 越界缺陷。目前均未作为标准写入接口开放。

---

## 4. `battery_ec_raw`（15 字节）布局与两点实测对照

`ewmi_lark4_lnl_config.battery_regs` 按顺序读取 `H_EC` 偏移 `0x84..0x93, 0xA8`（对应 [`DSDT.dsl` `ECF2` 字段表](acpi-dump/DSDT.dsl#L4751-L4766)）：

| 字节区间 | `ECF2` 偏移 | AML 字段 | 字节序 | `BAT0._BIF` / `BAT0._BST` 公式与说明 |
| --- | --- | --- | --- | --- |
| `0..1` | `0x84..0x85` | `B1DC` | `u16 LE` | 设计容量 mAh 槽位；`_BIF[1]`（design cap）与 `_BIF[2]`（last full cap）均等于 `(B1DC * B1FV) / 1000` mWh |
| `2..3` | `0x86..0x87` | `B1FV` | `u16 LE` | 标称/报告电压 mV；直接作为 `_BST[3]` (`voltage_now`) 及能量乘法系数 |
| `4..5` | `0x88..0x89` | `B1FC` | `u16 LE` | 在本机 `_BIF` 中仅用于计算 `_BIF[5]`/`_BIF[6]`（低电量警告/容量），**未**用于满充容量 |
| `6` | `0x8C` | `B1ST` | `u8` | 电池状态掩码（`_BST[0] = B1ST & 0x07`；`0x01`=放电） |
| `7..8` | `0x8D..0x8E` | `B1CR` | `u16 LE` | 电流倍率字段；`_BST[1]` (`power_now`) = `(B1CR * B1FV) / 1000` mW |
| `9..10` | `0x8F..0x90` | `B1RC` | `u16 LE` | 声明的剩余容量字；`_BST[2]` (`energy_now`) 实际取 `((B1DC * BPCN / 100) * B1FV) / 1000` |
| `11..12` | `0x91..0x92` | `B1VT` | `u16 LE` | 声明但未被 AML `_BIF`/`_BST` 读取的字，实测随负载/电压变化 |
| `13` | `0x93` | `BPCN` | `u8` | 固件报告电量百分比 |
| `14` | `0xA8` | `EBCM` | `u8` | 旧 `WMBE` 写的 EC 电池模式寄存器（与 `GBMM` 的关系尚未验证） |

### 已采集实机报告快照对照（`battery_mode_raw=0`、双帧一致；含交流充电与电池放电采样）

| 采样报告 | 原始 hex (`first == second`) | `B1DC` | `B1FV` | `B1FC` | `B1ST` | `B1CR` | `B1RC` | `B1VT` | `BPCN` | `EBCM` | `BAT0` `energy_now` / `power_now` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [`mainline-20261113T162930858650Z.json`](driver/mainline-20261113T162930858650Z.json)（AC 充电，Cycle 3） | `30145a2d8913426e05cc1060335600` | `5168` | `11610` | `5001` | `66` (`0x42`) | `1390` | `4300` | `13152` | `86` | `0` | `51594000 µWh` / `16137000 µW` |
| [`mainline-20261113T162930858650Z.json`](driver/mainline-20261113T162930858650Z.json)（AC 充电，Cycle 1 `after`） | `30145a2d8913428d059a1053335500` | `5168` | `11610` | `5001` | `66` (`0x42`) | `1421` | `4250` | `13139` | `85` | `0` | `50991000 µWh` / `16497000 µW` |
| [`battery-20261112T155727057142Z.json`](experimental/battery-readonly/battery-20261112T155727057142Z.json) | `30145a2d77130170019707062c2700` | `5168` | `11610` | `4983` | `1` | `368` | `1943` | `11270` | `39` | `0` | — / — |
| [`mainline-20261113T094915450763Z.json`](driver/mainline-20261113T094915450763Z.json) | `30145a2d771301ca016e0d802e4500` | `5168` | `11610` | `4983` | `1` | `458` | `3438` | `11904` | `69` | `0` | `41389000 µWh` / `5317000 µW` |
| [`mainline-20261113T092858455263Z.json`](driver/mainline-20261113T092858455263Z.json) | `30145a2d771301d901030eef2e4800` | `5168` | `11610` | `4983` | `1` | `473` | `3587` | `12015` | `72` | `0` | `43189000 µWh` / `5491000 µW` |
| [`mainline-20261113T091822487735Z.json`](driver/mainline-20261113T091822487735Z.json) | `30145a2d771301c601670e272f4a00` | `5168` | `11610` | `4983` | `1` | `454` | `3687` | `12071` | `74` | `0` | `44396000 µWh` / `5270000 µW` |
| [`mainline-20261113T085010945966Z.json`](driver/mainline-20261113T085010945966Z.json) | `30145a2d771301a701c40ff02f5100` | `5168` | `11610` | `4983` | `1` | `423` | `4036` | `12272` | `81` | `0` | `48599000 µWh` / `4911000 µW` |
| [`mainline-20261113T082717936614Z.json`](driver/mainline-20261113T082717936614Z.json) | `30145a2d771301eb01591031305400` | `5168` | `11610` | `4983` | `1` | `491` | `4185` | `12337` | `84` | `0` | `50399000 µWh` / `5700000 µW` |

**解释边界**：在 `39%`、`69%`、`72%`、`74%`、`81%` 与 `84%` 六个电池放电点上 `B1FC=4983`、`B1ST=0x01`，而在交流充电（`85%` / `86%`）采样下 `B1FC` 动态跳变为 **`5001`**（`4250/5001 = 84.98%`、`4300/5001 = 85.98%`），`B1ST` 变为 **`66` (`0x42`)**（`0x42 & 0x07 == 0x02` 对应 `_BST[0]` Charging 且 bit 6 为 AC 在线，高阶 `B1CR`/`B1VT` 反映充电电流与 ~13.15 V 充电端电压），`BAT0` `energy_now`/`power_now` 与 AML 公式精确吻合。这进一步证明 `B1FC` 属于固件电量计动态估计值而非静态健康度百分比，驱动不会据此向外暴露健康度百分比。

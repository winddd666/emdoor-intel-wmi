# 主线接口说明

版本 `0.4.4`。仅适用于 `MECHREVO / WUJIE Series-Lark4-LNL / EM_LNL326_V1.0.19`；尚未提交上游，sysfs 调试/只读探测属性不属于已冻结的上游 ABI。

版本行为演进说明：
- `0.4.1`：`03/01`、`03/03`、`03/05`、`03/0b`、`03/0d` 等共享 EC 邮箱（`EC5D` / `0x5d`）查询在收到同工作期内写入 `EC5D` 的物理热键事件（`_QF6` `0x50`/`0x51` 与 `_QF9` `0x20`–`0x22`）或读到瞬态非法字节时，会在 `emdoor-ewmi` 内自动重试（最多 `3` 次），避免与热键按压并发碰撞返回虚假值或瞬态 `-EIO`。
- `0.4.2`：`ewmi_profile_set()` 发送 `03/0A` (`SPFM`) 成功后，对 `03/09` (`GPFM` -> `H_EC.PPMD`) 执行有界等待轮询（首次零等待，若仍为旧值最多间隔 `setter_settle_ms` = `100 ms` 复查 `EWMI_MAILBOX_RETRIES` = `3` 次，不重发 `03/0A`），持续不一致时输出限流警告 `dev_warn_ratelimited` 并返回 `-EIO`。
- `0.4.3`：实机 `profiles-20261113T154107Z.json` 第 11/20 次及 `profiles-20261113T160227Z.json` 第 12/20 次进一步证实纯延后 `03/09` 轮询至 `1.5 s` 仍会固定停留在旧 `PPMD`；结合 `DSDT.dsl` (`SPFM`) 反编译可见：`SPFM` 在 `Notify(IETM, 0x88)` 与写入 `EC5F` **之前**就已写入 `ZZTT=0x10`/`TIZZ=0x07` 触发 EC 邮箱扫描，当 EC 在 `Notify` 期间消费了旧 `EC5F` 并清零 `ZZTT`/`TIZZ` 时，随后 `SPFM` 写入的新 `EC5F` 不会被 EC 执行，却仍返回 `RTNC=0`；而原先用户态 Python `Path.write_text()` 的 `BufferedWriter` 在驱动返回 `-EIO` 后于上下文退出 `close()` 时隐式冲刷同一缓冲二次写入 sysfs，才让“紧接着的 `03/0A` 重发”把已被预置的 `EC5F` 提交成功并掩盖成 `observed_after_error == target`。
- `0.4.4`：`ewmi_profile_set()` 移除 `EWMI_PROFILE_SETTLE_POLLS`（`1.5 s` 单纯轮询），改为统一的 `EWMI_MAILBOX_RETRIES`（最多 `3` 次）尝试循环：每次发送 `03/0A` 后，若返回 `RTNC=1` 或首次 `03/09` 及一次 `setter_settle_ms` (`100 ms`) 等待后的 `03/09` 仍不等于目标档位，则自动重发同一绝对模式 `03/0A`（传输错误仍不重试）；3 次皆不匹配方记录 `dev_warn_ratelimited` 并返回 `-EIO`（保留 `profile_dirty=true`）。同时宿主测试与检查工具中的 sysfs 写入均改用无缓冲 `open("wb", buffering=0)`。

## 方法设备

目录 `/sys/bus/wmi/devices/0B30FD67-1B62-4FCA-B10B-50DC7ADE6A39-*/`，驱动名 `emdoor-ewmi`。

| 属性 | 默认权限 | 含义 |
| --- | --- | --- |
| `protocol_version` | 0444 | 固件协议版本，必须为 `1.0.1.0` |
| `performance_mode` | 0444 | 固件原始性能档位 `1`/`2`（只读） |
| `fn_lock`, `win_lock` | 0444 | `0`/`1`；加载时开启 `allow_lock_writes=1` 后变为 `0644` |
| `touchpad_enabled` | 0444 | 固件触控板启用状态 `0`/`1` |
| `copilot_mode_raw` | 0400 | 查询 `03/05` 后的原始 EC 值 `0`/`4`（厂商导出名 `GetCopilotKey`） |
| `keyboard_backlight_raw` | 0400 | 两字节十六进制（`mailbox_reg`、`mailbox_ext_reg`，即 `EC5D`、`TIZT`），例如 `00 00` |
| `battery_mode_raw` | 0400 | 原始 `0`/`1`/`2`；Windows 官方控制中心已实机确认本机型不提供电池模式切换（`absent`） |
| `battery_ec_raw` | 0400 | 两次各 15 字节 EC 采样：`first=<30hex> second=<30hex>`，非原子事务 |

EC 电池寄存器顺序由板级配置 `ewmi_lark4_lnl_config.battery_regs` 定义：`84, 85, 86, 87, 88, 89, 8C, 8D, 8E, 8F, 90, 91, 92, 93, A8`。
风扇 RPM 与温度传感器（`ewmi_lark4_lnl_config.temp_regs`：`70, 73, 72, 62`）通过设备下的 `hwmon` (`emdoor_ewmi`) 导出。

## 性能档位接口

仅在加载时显式开启 `allow_writes=1` 时注册 `platform-profile`（`name=emdoor-wmi`，可选 `balanced` / `performance`）。

- `balanced` <-> `03/0A` 参数 `1`
- `performance` <-> `03/0A` 参数 `2`
- 重复写入已选目标（且无未确认写入标记 `profile_dirty`）时直接 no-op 返回 `0`；单次尝试在发送 `03/0A` 后即时检查并通过最多一次 `setter_settle_ms`（`100 ms`）等待后复查 `03/09` (`H_EC.PPMD`)，当固件返回 `RTNC=1` 或复查 `03/09` 仍为旧值时，在 `EWMI_MAILBOX_RETRIES`（`3`）次上限内自动重发同一绝对模式 `03/0A`（传输错误不重试），3 次均未生效时记录限流警告并返回 `-EIO`。

## 事件设备

目录 `/sys/bus/wmi/devices/0B30FD67-1B62-4FCA-B10B-50DC7ADE6DDD-*/`，驱动名 `emdoor-ewmi-events`。

- `event_state` (`0400`)：`count=<累计次数> last=0x<8位事件码>`
- `event_log` (`0400`)：最近最多 32 条记录，每行 `序号 boottime纳秒 0x事件码`

## 通电自启设备

目录 `/sys/bus/wmi/devices/C75665A3-4382-4646-ADC8-C81AE146E455-*/`，驱动名 `emdoor-ewmi-autopower`。

- `auto_power_on`：默认 `0444`，读取板级 `autopower_reg`（`0xF5`，仅接受 `0x00`/`0xA0`，否则 `-EPROTO`），显示 `0`/`1`。
- 开启 `allow_autopower_writes=1` 后权限为 `0644`，使用标准 `WMCC` 方法、8 字节小端 `0`/`1`，等待 `setter_settle_ms`（100 ms）后核对返回状态及读回。

## 模块加载参数与写入边界

所有写入开关与时序参数权限均为 `0400`，无法在模块运行期通过 sysfs 修改：

- `allow_writes`（默认 `false`）：控制性能档位 `platform_profile` 注册与写入。
- `allow_lock_writes`（默认 `false`）：控制 `fn_lock` / `win_lock` 写入，写入后等待 100 ms 再发配对查询并核对读回，`lock_dirty` 防止部分失败被当作 no-op。
- `allow_autopower_writes`（默认 `false`）：控制 `auto_power_on` 写入，`dirty` 标志防止部分失败被当作 no-op。
- `query_settle_ms`（默认 `100`，有效范围 `0..200`）与 `query_direct`（默认 `true`）：查询邮箱等待与 EC 直接读取开关。

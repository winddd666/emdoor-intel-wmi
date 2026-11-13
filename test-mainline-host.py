#!/usr/bin/env python3
"""Exercise unified mainline driver/emdoor-wmi.c functions on host."""

from pathlib import Path
import resource
import subprocess
from tempfile import TemporaryDirectory

BASE = Path(__file__).resolve().parent
SOURCE = (BASE / "driver/emdoor-wmi.c").read_text()
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def slice_between(start, end):
    left = SOURCE.index(start)
    right = SOURCE.index(end, left)
    return SOURCE[left:right]


ENUMS = slice_between("enum {\n\tEWMI_FAN_COUNT", "static bool allow_writes;")
BOARD_STRUCT = slice_between("struct ewmi_board_config {", "struct ewmi_reply {")
BOARD_CFG = slice_between("static const struct ewmi_board_config ewmi_lark4_lnl_config",
                          "static const struct dmi_system_id ewmi_dmi_table[]")
DECODER = slice_between("static int ewmi_mailbox_reply(", "/*\n * WMCD returns")
CALL = slice_between("static int ewmi_call_status(", "static ssize_t protocol_version_show(")
SWITCH_SHOW = slice_between("static ssize_t ewmi_switch_show(", "static ssize_t ewmi_switch_store(")
LOCKS = slice_between("static ssize_t ewmi_switch_store(", "static ssize_t fn_lock_show(")
BATTERY = slice_between("static int ewmi_battery_raw(", "static ssize_t battery_ec_raw_show(")
HWMON_LABELS = slice_between("static const char * const ewmi_temp_labels",
                             "static int ewmi_hwmon_read(")
HWMON_READ = slice_between("static int ewmi_hwmon_read(", "static const struct hwmon_ops ewmi_hwmon_ops")
PROFILE = slice_between("static int ewmi_profile_probe(",
                        "static const struct platform_profile_ops ewmi_profile_ops")
METHOD_EVENT = slice_between("static int ewmi_method_event(", "static void ewmi_method_stop(")
EVENTS = slice_between("static void ewmi_record_event(", "static ssize_t event_state_show(")
AUTOPOWER = slice_between("static int ewmi_autopower_get(", "static ssize_t auto_power_on_show(")
INIT_EXIT = slice_between("static int __init ewmi_init(", "module_init(ewmi_init);")

SHIM = r'''
#include <assert.h>
#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint16_t __le16;
typedef uint64_t __le64;
typedef struct { int counter; } atomic_t;

#define EWMI_EVENT_HISTORY 32
#define EWMI_INPUT_SIZE 64
#define EWMI_OUTPUT_SIZE 256
#define BIT(x) (1U << (x))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define cpu_to_le64(x) ((u64)(x))
#define le16_to_cpu(x) ((u16)(x))
#define __init
#define __exit
#define guard(kind) (void)
#define lockdep_assert_held(lock) ((void)(lock))
#define dev_dbg(...) ((void)0)
#define dev_warn_ratelimited(...) ((void)0)
#define NOTIFY_DONE 0
#define NOTIFY_OK 1
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))

static int atomic_read(const atomic_t *v) { return v->counter; }
static void atomic_inc(atomic_t *v) { v->counter++; }
static void atomic_set(atomic_t *v, int i) { v->counter = i; }

static ssize_t sysfs_emit(char *buf, const char *fmt, ...)
{
    va_list args;
    int len;

    va_start(args, fmt);
    len = vsnprintf(buf, 4096, fmt, args);
    va_end(args);
    return len;
}

enum hwmon_sensor_types { hwmon_chip, hwmon_temp, hwmon_fan };
enum { hwmon_temp_input, hwmon_temp_label, hwmon_fan_input, hwmon_fan_label };
enum platform_profile_option {
    PLATFORM_PROFILE_BALANCED,
    PLATFORM_PROFILE_PERFORMANCE,
    PLATFORM_PROFILE_QUIET,
};

static void set_bit(int nr, unsigned long *addr)
{
    *addr |= (1UL << nr);
}
'''

STRUCTS_AND_MOCKS = r'''
struct ewmi_reply {
    u8 reserved[4];
    u8 status;
    u8 data[EWMI_OUTPUT_SIZE - 1];
};

struct device { void *priv; struct device *parent; };
struct wmi_device { struct device dev; };
struct notifier_block {
    int (*notifier_call)(struct notifier_block *nb, unsigned long code, void *data);
};
struct work_struct { int dummy; };
struct wmi_buffer { size_t length; void *data; };

struct ewmi_method {
    struct wmi_device *wdev;
    const struct ewmi_board_config *board;
    int lock;
    struct device *profile;
    struct notifier_block event_nb;
    struct work_struct profile_work;
    atomic_t mailbox_event_seq;
    bool profile_dirty;
    u8 lock_dirty;
};

struct ewmi_event_record {
    u64 sequence;
    u64 boottime_ns;
    u32 code;
};

struct ewmi_event {
    int lock;
    u64 count;
    u32 last_code;
    struct ewmi_event_record history[EWMI_EVENT_HISTORY];
};

struct ewmi_autopower {
    void *wdev;
    const struct ewmi_board_config *board;
    int lock;
    bool dirty;
};

static struct device parent_dev, other_parent_dev;
static struct wmi_device method_wdev = { .dev = { .parent = &parent_dev } };
static struct wmi_device event_wdev_same = { .dev = { .parent = &parent_dev } };
static struct wmi_device event_wdev_other = { .dev = { .parent = &other_parent_dev } };
static struct ewmi_method method_priv = { .wdev = &method_wdev, .board = &ewmi_lark4_lnl_config };
static struct ewmi_autopower auto_priv = { .board = &ewmi_lark4_lnl_config };
static struct device method_dev = { .priv = &method_priv };

static bool allow_writes, allow_lock_writes, allow_autopower_writes;
static bool query_direct = true;
static unsigned int query_settle_ms = 100;

static int raw_mb, high_mb, torn_mb, torn_at_ec_read, ec_error, ec_fail_at, ec_reads;
static u8 ec_map[256];
static bool use_ec_map;
static bool ec_flip_second_frame;
static int wmcd_status, wmcd_transport_error, wmcd_invokes, wmcd_frees, waits;
static int wmcc_calls, scheduled_profile_work;
static u8 sim_perf_mode = 2, prev_perf_mode = 2, sim_lock_val, sim_copilot_raw, sim_kbd_low, sim_kbd_high, sim_battery_mode, sim_auto_raw;
static bool delay_lock, pending_lock;
static u8 pending_lock_val;
static bool profile_hold_old;
static int profile_outcomes[3], profile_setters, profile_lag_reads, profile_lag_remaining, profile_stale_drops;
static u64 now_ns;
static int ewmi_method_driver, ewmi_event_driver, ewmi_autopower_driver;
static int registrations, register_calls, fail_registration;
static int hotkey_inject_at_wait, hotkey_inject_raw = -1;
static unsigned long hotkey_inject_code;

static int ewmi_method_event(struct notifier_block *nb, unsigned long code, void *data);
static void schedule_work(struct work_struct *w) { (void)w; scheduled_profile_work++; }
static void *dev_get_drvdata(struct device *dev) { return dev->priv; }

static int kstrtobool(const char *s, bool *v)
{
    if (!strcmp(s, "1")) { *v = true; return 0; }
    if (!strcmp(s, "0")) { *v = false; return 0; }
    return -EINVAL;
}

static int ec_read(u8 address, u8 *result)
{
    ec_reads++;
    if (ec_error && (!ec_fail_at || ec_reads == ec_fail_at))
        return ec_error;
    if (use_ec_map) {
        if (ec_flip_second_frame && ec_reads > 15)
            *result = (u8)(ec_map[address] ^ 0xff);
        else
            *result = ec_map[address];
        return 0;
    }
    if (address == 0xf5) {
        *result = sim_auto_raw;
        return 0;
    }
    if (address == 0x5c) {
        *result = (u8)high_mb;
        return 0;
    }
    if (address == 0x5d) {
        bool do_tear = (torn_mb && ec_reads == 3) || (torn_at_ec_read && ec_reads == torn_at_ec_read);
        *result = (u8)(raw_mb + (do_tear ? 1 : 0));
        return 0;
    }
    return -ENXIO;
}

static void msleep(unsigned int ms)
{
    assert(ms == 100 || ms == query_settle_ms);
    waits++;
    if (pending_lock) {
        sim_lock_val = pending_lock_val;
        pending_lock = false;
    }
    if (hotkey_inject_at_wait && waits == hotkey_inject_at_wait) {
        if (hotkey_inject_raw >= 0)
            raw_mb = hotkey_inject_raw;
        if (hotkey_inject_code)
            ewmi_method_event(&method_priv.event_nb, hotkey_inject_code, &event_wdev_same);
    }
}

static void kfree(void *data)
{
    if (data)
        wmcd_frees++;
    free(data);
}

static u64 ktime_get_boottime_ns(void) { return ++now_ns; }

static int wmi_driver_register(int *drv)
{
    (void)drv;
    register_calls++;
    if (register_calls == fail_registration)
        return -EIO;
    registrations++;
    return 0;
}

static void wmi_driver_unregister(int *drv)
{
    (void)drv;
    registrations--;
}

static int wmidev_invoke_method(void *wdev, int instance, int method,
                                struct wmi_buffer *in, struct wmi_buffer *out,
                                size_t minimum)
{
    const u8 *req = in->data;
    (void)wdev;
    assert(instance == 0 && method == 1);
    if (minimum == 8 && in->length == 8) {
        assert(req[0] <= 1);
        for (int i = 1; i < 8; i++)
            assert(req[i] == 0);
        wmcc_calls++;
        if (wmcd_transport_error)
            return wmcd_transport_error;
        out->data = calloc(1, 8);
        assert(out->data);
        out->length = 8;
        ((u8 *)out->data)[0] = (u8)wmcd_status;
        if (!profile_hold_old && !wmcd_status)
            sim_auto_raw = req[0] ? 0xa0 : 0x00;
        return 0;
    }
    assert(in->length == 64 && minimum == 260);
    wmcd_invokes++;
    if (wmcd_transport_error)
        return wmcd_transport_error;
    struct ewmi_reply *reply = calloc(1, sizeof(*reply));
    assert(reply);
    out->data = reply;
    out->length = sizeof(*reply);
    if (req[0] == 0x02 && req[1] == 0x01) {
        u16 rpm = req[2] == 0 ? 1234 : 5678;
        reply->status = 0;
        reply->data[0] = (u8)(rpm & 0xff);
        reply->data[1] = (u8)(rpm >> 8);
        return 0;
    }
    if (req[0] == 0x03 && req[1] == 0x09) {
        reply->status = 0;
        if (profile_lag_remaining > 0) {
            profile_lag_remaining--;
            reply->data[0] = prev_perf_mode;
        } else {
            reply->data[0] = sim_perf_mode;
        }
        return 0;
    }
    if (req[0] == 0x03 && req[1] == 0x0a) {
        int outcome = profile_outcomes[profile_setters++];
        if (outcome == 2) {
            free(reply);
            out->data = NULL;
            return -EIO;
        }
        if (profile_stale_drops > 0) {
            profile_stale_drops--;
            reply->status = 0;
            return 0;
        }
        if (!profile_hold_old) {
            prev_perf_mode = sim_perf_mode;
            sim_perf_mode = req[2];
            profile_lag_remaining = profile_lag_reads;
        }
        reply->status = (u8)(outcome == 1 ? 1 : 0);
        return 0;
    }
    if (req[0] == 0x03 && (req[1] == 0x01 || req[1] == 0x03)) {
        pending_lock = false;
        raw_mb = req[1] == 0x01 ? (sim_lock_val ? 2 : 0) : sim_lock_val;
        reply->status = (u8)wmcd_status;
        return 0;
    }
    if (req[0] == 0x03 && req[1] == 0x05) {
        raw_mb = sim_copilot_raw;
        reply->status = (u8)wmcd_status;
        return 0;
    }
    if (req[0] == 0x03 && req[1] == 0x0b) {
        raw_mb = sim_kbd_low;
        high_mb = sim_kbd_high;
        reply->status = (u8)wmcd_status;
        return 0;
    }
    if (req[0] == 0x03 && req[1] == 0x0d) {
        raw_mb = sim_battery_mode;
        reply->status = (u8)wmcd_status;
        return 0;
    }
    if (req[0] == 0x03 && (req[1] == 0x02 || req[1] == 0x04)) {
        if (wmcd_status) {
            reply->status = (u8)wmcd_status;
            return 0;
        }
        if (!profile_hold_old) {
            if (delay_lock) {
                pending_lock = true;
                pending_lock_val = req[2];
            } else {
                sim_lock_val = req[2];
            }
        }
        reply->status = 0;
        return 0;
    }
    reply->status = (u8)wmcd_status;
    return 0;
}
'''

TESTS = r'''
static void reset_all(void)
{
    method_priv.wdev = &method_wdev;
    method_priv.board = &ewmi_lark4_lnl_config;
    method_priv.profile = NULL;
    atomic_set(&method_priv.mailbox_event_seq, 0);
    method_priv.profile_dirty = false;
    method_priv.lock_dirty = 0;
    auto_priv.board = &ewmi_lark4_lnl_config;
    auto_priv.dirty = false;
    allow_writes = allow_lock_writes = allow_autopower_writes = false;
    query_direct = true;
    query_settle_ms = 100;
    raw_mb = high_mb = torn_mb = torn_at_ec_read = ec_error = ec_fail_at = ec_reads = 0;
    use_ec_map = ec_flip_second_frame = false;
    memset(ec_map, 0, sizeof(ec_map));
    wmcd_status = wmcd_transport_error = wmcd_invokes = wmcd_frees = waits = wmcc_calls = scheduled_profile_work = 0;
    sim_perf_mode = prev_perf_mode = 2;
    sim_lock_val = 0;
    sim_copilot_raw = 0;
    sim_kbd_low = sim_kbd_high = 0;
    sim_battery_mode = 0;
    sim_auto_raw = 0;
    delay_lock = pending_lock = false;
    profile_hold_old = false;
    profile_setters = profile_lag_reads = profile_lag_remaining = profile_stale_drops = 0;
    profile_outcomes[0] = profile_outcomes[1] = profile_outcomes[2] = 0;
    registrations = register_calls = fail_registration = 0;
    hotkey_inject_at_wait = 0;
    hotkey_inject_raw = -1;
    hotkey_inject_code = 0;
}

int main(void)
{
    reset_all();
    assert(ewmi_lark4_lnl_config.mailbox_reg == 0x5d);
    assert(ewmi_lark4_lnl_config.mailbox_ext_reg == 0x5c);
    assert(ewmi_lark4_lnl_config.autopower_reg == 0xf5);
    assert(ewmi_lark4_lnl_config.setter_settle_ms == 100);

    /* Mailbox decoder exhaustive check for commands 1, 3, 5, 13. */
    u8 val;
    const u8 cmds[] = { 1, 3, 5, 13 };
    for (size_t c = 0; c < sizeof(cmds); c++) {
        for (raw_mb = 0; raw_mb < 256; raw_mb++) {
            val = 0xa5;
            bool ok = cmds[c] == 5 ? (raw_mb == 0 || raw_mb == 4) :
                      cmds[c] == 1 ? (raw_mb == 0 || raw_mb == 2) :
                      raw_mb <= (cmds[c] == 3 ? 1 : 2);
            u8 exp = cmds[c] == 1 ? (raw_mb == 2) : (u8)raw_mb;
            assert(ewmi_mailbox_reply(&method_priv, cmds[c], &val, 1) == (ok ? 0 : -EIO));
            assert(val == (ok ? exp : 0xa5));
        }
    }
    u8 kbd[2];
    raw_mb = 0x12; high_mb = 0x34; ec_reads = 0;
    assert(ewmi_mailbox_reply(&method_priv, 0x0b, kbd, 2) == 0);
    assert(ec_reads == 4 && kbd[0] == 0x12 && kbd[1] == 0x34);
    torn_mb = 1; ec_reads = 0;
    assert(ewmi_mailbox_reply(&method_priv, 0x0b, kbd, 2) == -EAGAIN);
    torn_mb = 0;

    /* Lock writes with 100 ms post-setter settle and dirty recovery. */
    reset_all();
    allow_lock_writes = true;
    delay_lock = true;
    assert(ewmi_switch_store(&method_dev, 0x04, "1", 1) == 1);
    assert(sim_lock_val == 1 && waits == 3 && !method_priv.lock_dirty);
    assert(ewmi_switch_store(&method_dev, 0x04, "1", 1) == 1);
    assert(waits == 4 && wmcd_invokes == 4); /* no-op skips setter */
    wmcd_status = 1;
    assert(ewmi_switch_store(&method_dev, 0x04, "0", 1) == -EIO);
    assert(method_priv.lock_dirty != 0);
    wmcd_status = 0;
    assert(ewmi_switch_store(&method_dev, 0x04, "1", 1) == 1);
    assert(!method_priv.lock_dirty);

    /* Profile probe/get/set. */
    reset_all();
    unsigned long choices = 0;
    assert(ewmi_profile_probe(NULL, &choices) == 0);
    assert((choices & (BIT(PLATFORM_PROFILE_BALANCED) | BIT(PLATFORM_PROFILE_PERFORMANCE))) ==
           (BIT(PLATFORM_PROFILE_BALANCED) | BIT(PLATFORM_PROFILE_PERFORMANCE)));
    enum platform_profile_option prof;
    assert(ewmi_profile_get(&method_dev, &prof) == 0 && prof == PLATFORM_PROFILE_PERFORMANCE);
    assert(ewmi_profile_set(&method_dev, PLATFORM_PROFILE_BALANCED) == -EPERM);
    allow_writes = true;
    profile_outcomes[0] = 1;
    assert(ewmi_profile_set(&method_dev, PLATFORM_PROFILE_BALANCED) == 0);
    assert(profile_setters == 2 && waits == 1 && sim_perf_mode == 1 && !method_priv.profile_dirty);

    reset_all();
    allow_writes = true;
    profile_lag_reads = 1;
    assert(ewmi_profile_set(&method_dev, PLATFORM_PROFILE_BALANCED) == 0);
    assert(profile_setters == 1 && waits == 1 && sim_perf_mode == 1 && !method_priv.profile_dirty);

    reset_all();
    allow_writes = true;
    profile_stale_drops = 1;
    assert(ewmi_profile_set(&method_dev, PLATFORM_PROFILE_BALANCED) == 0);
    assert(profile_setters == 2 && waits == 1 && sim_perf_mode == 1 && !method_priv.profile_dirty);

    reset_all();
    allow_writes = true;
    profile_hold_old = true;
    assert(ewmi_profile_set(&method_dev, PLATFORM_PROFILE_BALANCED) == -EIO);
    assert(profile_setters == 3 && waits == 3 && method_priv.profile_dirty);

    /* hwmon fan & temp reads from board config. */
    reset_all();
    use_ec_map = true;
    ec_map[0x70] = 50; ec_map[0x73] = 40; ec_map[0x72] = 30; ec_map[0x62] = 0xff;
    long hval = 0;
    assert(ewmi_hwmon_read(&method_dev, hwmon_fan, hwmon_fan_input, 0, &hval) == 0 && hval == 1234);
    assert(ewmi_hwmon_read(&method_dev, hwmon_fan, hwmon_fan_input, 1, &hval) == 0 && hval == 5678);
    assert(ewmi_hwmon_read(&method_dev, hwmon_fan, hwmon_fan_input, 2, &hval) == -EOPNOTSUPP);
    assert(ewmi_hwmon_read(&method_dev, hwmon_temp, hwmon_temp_input, 0, &hval) == 0 && hval == 50000);
    assert(ewmi_hwmon_read(&method_dev, hwmon_temp, hwmon_temp_input, 3, &hval) == -ENODATA);
    assert(ewmi_hwmon_read(&method_dev, hwmon_temp, hwmon_temp_input, 4, &hval) == -EOPNOTSUPP);
    const char *label = NULL;
    assert(ewmi_hwmon_read_string(&method_dev, hwmon_fan, hwmon_fan_label, 0, &label) == 0 && !strcmp(label, "Fan 1"));
    assert(ewmi_hwmon_read_string(&method_dev, hwmon_temp, hwmon_temp_label, 3, &label) == 0 && !strcmp(label, "Charger"));
    assert(ewmi_hwmon_read_string(&method_dev, hwmon_temp, hwmon_temp_label, 4, &label) == -EOPNOTSUPP);

    /* Battery EC raw sampler (30 reads & 90 injected failures). */
    reset_all();
    use_ec_map = true;
    for (int i = 0; i < 15; i++)
        ec_map[ewmi_lark4_lnl_config.battery_regs[i]] = ewmi_lark4_lnl_config.battery_regs[i];
    u8 batt[2][15];
    ec_flip_second_frame = true;
    assert(ewmi_battery_raw(&method_priv, batt) == 0 && ec_reads == 30);
    assert(memcmp(batt[0], batt[1], 15) != 0);
    ec_flip_second_frame = false;
    for (int k = 0; k < 3; k++) {
        ec_error = k == 0 ? -EIO : k == 1 ? -ETIMEDOUT : -ENXIO;
        for (ec_fail_at = 1; ec_fail_at <= 30; ec_fail_at++) {
            ec_reads = 0;
            assert(ewmi_battery_raw(&method_priv, batt) == ec_error);
            assert(ec_reads == ec_fail_at);
        }
    }

    /* Autopower get/set + event ring. */
    reset_all();
    allow_autopower_writes = true;
    assert(ewmi_autopower_set(&auto_priv, true) == 0 && wmcc_calls == 1 && sim_auto_raw == 0xa0);
    assert(ewmi_autopower_set(&auto_priv, true) == 0 && wmcc_calls == 1);
    sim_auto_raw = 0x55;
    bool en = false;
    assert(ewmi_autopower_get(&auto_priv, &en) == -EPROTO);
    struct ewmi_event evt = { 0 };
    for (int i = 0; i < 64; i++)
        ewmi_record_event(&evt, (u32)i);
    assert(evt.count == 64 && evt.last_code == 63);
    assert(evt.history[0].sequence == 33 && evt.history[31].sequence == 64);

    /* Init/exit gates: query bounds, allow_writes=1 permitted, rollback on error. */
    reset_all();
    allow_writes = true;
    query_settle_ms = 201;
    assert(ewmi_init() == -EINVAL && !registrations);
    query_settle_ms = 0; query_direct = true;
    assert(ewmi_init() == -EINVAL && !registrations);
    query_settle_ms = 100;
    assert(ewmi_init() == 0 && registrations == 3);
    ewmi_exit();
    assert(registrations == 0);
    for (fail_registration = 1; fail_registration <= 3; fail_registration++) {
        registrations = register_calls = 0;
        assert(ewmi_init() == -EIO && !registrations);
    }

    /* EC5D mailbox hotkey collision & torn-read retry protection. */
    reset_all();
    char attr_buf[64];
    /* Case 1: transient invalid raw 0x01 on 03/01 (win_lock) and 03/05 (copilot) retries once. */
    sim_lock_val = 0;
    hotkey_inject_at_wait = 1; hotkey_inject_raw = 0x01; hotkey_inject_code = 0x51;
    assert(ewmi_switch_show(&method_dev, 0x01, attr_buf) == 2 && !strcmp(attr_buf, "0\n"));
    assert(waits == 2 && wmcd_invokes == 2);

    reset_all();
    sim_copilot_raw = 4;
    hotkey_inject_at_wait = 1; hotkey_inject_raw = 0x01; hotkey_inject_code = 0x51;
    u8 copilot_val = 0xff;
    assert(ewmi_call(&method_priv, 0x03, 0x05, 0, &copilot_val, 1) == 0 && copilot_val == 4);
    assert(waits == 2 && wmcd_invokes == 2);

    /* Case 2: valid-looking aliased 0x02 (F8 backlight event 0x22 while win_lock=0) is rejected via seq. */
    reset_all();
    sim_lock_val = 0;
    hotkey_inject_at_wait = 1; hotkey_inject_raw = 0x02; hotkey_inject_code = 0x22;
    assert(ewmi_switch_show(&method_dev, 0x01, attr_buf) == 2 && !strcmp(attr_buf, "0\n"));
    assert(waits == 2 && wmcd_invokes == 2);

    /* Case 3: torn 2-byte 03/0b read retries and succeeds; persistent corruption stops at 3 attempts. */
    reset_all();
    sim_kbd_low = 0x22; sim_kbd_high = 0x01;
    torn_at_ec_read = 3;
    u8 kbd_buf[2] = { 0, 0 };
    assert(ewmi_call(&method_priv, 0x03, 0x0b, 0, kbd_buf, 2) == 0);
    assert(kbd_buf[0] == 0x22 && kbd_buf[1] == 0x01 && waits == 2 && wmcd_invokes == 2);

    reset_all();
    hotkey_inject_at_wait = 1; hotkey_inject_raw = 0x01; hotkey_inject_code = 0;
    /* Keep raw_mb corrupted on every attempt by injecting via callback not needed: test persistent invalid via 03/05=0x01. */
    sim_copilot_raw = 0x01;
    hotkey_inject_at_wait = 0;
    assert(ewmi_call(&method_priv, 0x03, 0x05, 0, &copilot_val, 1) == -EIO);
    assert(waits == 3 && wmcd_invokes == 3 && wmcd_frees == 3);

    /* Case 4: ewmi_method_event increments mailbox_event_seq iff same parent and EC5D code (0x20-0x22, 0x50-0x51). */
    reset_all();
    const unsigned long mb_codes[] = { 0x20, 0x21, 0x22, 0x50, 0x51 };
    for (size_t i = 0; i < sizeof(mb_codes) / sizeof(mb_codes[0]); i++) {
        assert(ewmi_method_event(&method_priv.event_nb, mb_codes[i], &event_wdev_same) == NOTIFY_OK);
        assert(atomic_read(&method_priv.mailbox_event_seq) == (int)(i + 1));
    }
    assert(ewmi_method_event(&method_priv.event_nb, 0x51, &event_wdev_other) == NOTIFY_DONE);
    assert(atomic_read(&method_priv.mailbox_event_seq) == 5);
    const unsigned long other_codes[] = { 0x30, 0x31, 0x41, 0x42, 0xa0, 0xa1, 0xa2 };
    for (size_t i = 0; i < sizeof(other_codes) / sizeof(other_codes[0]); i++) {
        assert(ewmi_method_event(&method_priv.event_nb, other_codes[i], &event_wdev_same) == NOTIFY_OK);
        assert(atomic_read(&method_priv.mailbox_event_seq) == 5);
    }
    assert(scheduled_profile_work == 0);
    struct device dummy_profile = { 0 };
    method_priv.profile = &dummy_profile;
    assert(ewmi_method_event(&method_priv.event_nb, 0x41, &event_wdev_same) == NOTIFY_OK);
    assert(scheduled_profile_work == 1 && atomic_read(&method_priv.mailbox_event_seq) == 5);

    puts("PASS: unified mainline 0.4.4 board config, mailbox collision retry, profile retry/verify, locks, hwmon, battery EC, autopower, event ring, init/exit");
    return 0;
}
'''

BODY = "\n".join([
    SHIM, ENUMS, BOARD_STRUCT, BOARD_CFG, STRUCTS_AND_MOCKS,
    DECODER, CALL, SWITCH_SHOW, LOCKS, BATTERY, HWMON_LABELS, HWMON_READ,
    PROFILE, METHOD_EVENT, EVENTS, AUTOPOWER, INIT_EXIT, TESTS,
])

with TemporaryDirectory(prefix="emdoor-mainline-host-") as directory:
    path = Path(directory)
    (path / "mainline-host.c").write_text(BODY)
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                    "-Wno-sign-compare", "-Wno-unused-parameter",
                    str(path / "mainline-host.c"), "-o", str(path / "mainline-host")], check=True)
    subprocess.run([str(path / "mainline-host")], check=True)

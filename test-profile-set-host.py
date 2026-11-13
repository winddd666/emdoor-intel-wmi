#!/usr/bin/env python3
"""Compile the real profile_set function with injected firmware outcomes."""

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory


BASE = Path(__file__).resolve().parent
SOURCE = (BASE / "driver/emdoor-wmi.c").read_text()
FUNCTION = SOURCE[SOURCE.index("static int ewmi_profile_set("):
                  SOURCE.index("static const struct platform_profile_ops ewmi_profile_ops")]

SHIM = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <errno.h>

typedef unsigned char u8;
enum { EWMI_MAILBOX_RETRIES = 3 };
enum platform_profile_option {
    PLATFORM_PROFILE_BALANCED, PLATFORM_PROFILE_PERFORMANCE, PLATFORM_PROFILE_QUIET
};
struct ewmi_board_config { unsigned int setter_settle_ms; };
static const struct ewmi_board_config board = { .setter_settle_ms = 100 };
struct ewmi_method { int lock; const struct ewmi_board_config *board; bool profile_dirty; };
struct device { struct ewmi_method *priv; };
static struct ewmi_method priv = { .board = &board };
static struct device dev = { &priv };
static bool allow_writes = true;
static u8 raw_mode, prev_mode;
static int outcomes[3], setters, waits, lag_reads, lag_remaining, stale_drops;
static bool hold_old_mode;
#define guard(kind) (void)
#define dev_dbg(...) ((void)0)
#define dev_warn_ratelimited(...) ((void)0)
static void *dev_get_drvdata(struct device *device) { return device->priv; }
static void msleep(unsigned int delay) { assert(delay == 100); waits++; }

static int ewmi_call(struct ewmi_method *p, u8 group, u8 command, u8 argument,
                     void *result, size_t length)
{
    assert(p == &priv && group == 3 && command == 9 && argument == 0 && length == 1);
    if (lag_remaining > 0) {
        lag_remaining--;
        *(u8 *)result = prev_mode;
    } else {
        *(u8 *)result = raw_mode;
    }
    return 0;
}

static int ewmi_call_status(struct ewmi_method *p, u8 group, u8 command, u8 argument,
                            void *result, size_t length, bool *firmware_failure)
{
    assert(p == &priv && group == 3 && command == 10 && result == NULL && length == 0);
    assert(setters < 3);
    int outcome = outcomes[setters++];
    *firmware_failure = outcome == 1;
    if (outcome == 2) /* Transport failed: no firmware acknowledgement. */
        return -EIO;
    if (stale_drops > 0) {
        stale_drops--;
        return 0; /* RTNC=0, but EC consumed stale EC5F during notify. */
    }
    if (!hold_old_mode) {
        prev_mode = raw_mode;
        raw_mode = argument; /* Even an RTNC=1 response can have applied the mode. */
        lag_remaining = lag_reads;
    }
    return outcome == 1 ? -EIO : 0;
}
'''

TESTS = r'''
static void reset(void)
{
    priv.board = &board;
    priv.profile_dirty = false;
    raw_mode = prev_mode = 2;
    allow_writes = true;
    setters = waits = lag_reads = lag_remaining = stale_drops = 0;
    hold_old_mode = false;
    outcomes[0] = outcomes[1] = outcomes[2] = 0;
}

int main(void)
{
    reset();
    assert(ewmi_profile_set(&dev, PLATFORM_PROFILE_PERFORMANCE) == 0);
    assert(setters == 0); /* An already selected profile is a no-op. */

    reset(); outcomes[0] = 1;
    assert(ewmi_profile_set(&dev, PLATFORM_PROFILE_BALANCED) == 0);
    assert(setters == 2 && waits == 1 && raw_mode == 1 && !priv.profile_dirty);

    reset(); outcomes[0] = outcomes[1] = outcomes[2] = 1;
    assert(ewmi_profile_set(&dev, PLATFORM_PROFILE_BALANCED) == -EIO);
    assert(setters == 3 && waits == 2 && raw_mode == 1 && priv.profile_dirty);
    setters = waits = 0; outcomes[0] = 0;
    assert(ewmi_profile_set(&dev, PLATFORM_PROFILE_BALANCED) == 0);
    assert(setters == 1 && !priv.profile_dirty); /* Dirty state cannot pass as a no-op. */

    reset(); outcomes[0] = 2;
    assert(ewmi_profile_set(&dev, PLATFORM_PROFILE_BALANCED) == -EIO);
    assert(setters == 1 && waits == 0 && priv.profile_dirty);

    reset(); hold_old_mode = true;
    assert(ewmi_profile_set(&dev, PLATFORM_PROFILE_BALANCED) == -EIO);
    assert(setters == 3 && waits == 3 && priv.profile_dirty); /* Ack alone is insufficient. */

    reset();
    assert(ewmi_profile_set(&dev, PLATFORM_PROFILE_BALANCED) == 0);
    assert(setters == 1 && waits == 0 && raw_mode == 1 && !priv.profile_dirty);

    reset(); lag_reads = 1;
    assert(ewmi_profile_set(&dev, PLATFORM_PROFILE_BALANCED) == 0);
    assert(setters == 1 && waits == 1 && raw_mode == 1 && !priv.profile_dirty);

    reset(); stale_drops = 1;
    assert(ewmi_profile_set(&dev, PLATFORM_PROFILE_BALANCED) == 0);
    assert(setters == 2 && waits == 1 && raw_mode == 1 && !priv.profile_dirty);

    reset(); stale_drops = 2;
    assert(ewmi_profile_set(&dev, PLATFORM_PROFILE_BALANCED) == 0);
    assert(setters == 3 && waits == 2 && raw_mode == 1 && !priv.profile_dirty);

    reset(); allow_writes = false;
    assert(ewmi_profile_set(&dev, PLATFORM_PROFILE_BALANCED) == -EPERM && setters == 0);
    reset();
    assert(ewmi_profile_set(&dev, PLATFORM_PROFILE_QUIET) == -EOPNOTSUPP && setters == 0);
    puts("PASS: real profile_set no-op, partial acknowledgement, retry bound, dirty state, transport, readback and gates");
    return 0;
}
'''

with TemporaryDirectory(prefix="emdoor-profile-test-") as directory:
    path = Path(directory)
    (path / "profile-test.c").write_text(SHIM + FUNCTION + TESTS)
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(path / "profile-test.c"),
                    "-o", str(path / "profile-test")], check=True)
    subprocess.run([str(path / "profile-test")], check=True)

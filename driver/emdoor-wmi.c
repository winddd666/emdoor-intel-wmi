// SPDX-License-Identifier: GPL-2.0-or-later
/* Emdoor Intel EWMI support, initially limited to the verified Lark4 BIOS. */

#include <linux/acpi.h>
#include <linux/atomic.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/dmi.h>
#include <linux/hwmon.h>
#include <linux/input.h>
#include <linux/input/sparse-keymap.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/notifier.h>
#include <linux/platform_profile.h>
#include <linux/slab.h>
#include <linux/sysfs.h>
#include <linux/unaligned.h>
#include <linux/wmi.h>
#include <linux/workqueue.h>

#define EWMI_METHOD_GUID "0B30FD67-1B62-4FCA-B10B-50DC7ADE6A39"
#define EWMI_EVENT_GUID  "0B30FD67-1B62-4FCA-B10B-50DC7ADE6DDD"
#define EWMI_AUTOPOWER_GUID "C75665A3-4382-4646-ADC8-C81AE146E455"
#define EWMI_EVENT_HISTORY 32
#define EWMI_INPUT_SIZE  64
#define EWMI_OUTPUT_SIZE 256

enum {
	EWMI_FAN_COUNT = 2,
	EWMI_TEMP_COUNT = 4,
	EWMI_BATTERY_REG_COUNT = 15,
	EWMI_MAILBOX_RETRIES = 3,
};

static bool allow_writes;
module_param(allow_writes, bool, 0400);
MODULE_PARM_DESC(allow_writes, "Enable experimental performance profile writes (default: false)");

static bool allow_lock_writes;
module_param(allow_lock_writes, bool, 0400);
MODULE_PARM_DESC(allow_lock_writes, "Enable Fn/Win lock writes (default: false)");

static bool allow_autopower_writes;
module_param(allow_autopower_writes, bool, 0400);
MODULE_PARM_DESC(allow_autopower_writes, "Enable AC auto-power-on writes (default: false)");

/* This BIOS returns the previous command's mailbox data after its 20 ms wait. */
static unsigned int query_settle_ms = 100;
module_param(query_settle_ms, uint, 0400);
MODULE_PARM_DESC(query_settle_ms, "Mailbox reply settling delay (0-200 ms, default: 100)");

static bool query_direct = true;
module_param(query_direct, bool, 0400);
MODULE_PARM_DESC(query_direct, "Read the settled mailbox directly without reissuing (default: true)");

struct ewmi_board_config {
	u8 mailbox_reg;
	u8 mailbox_ext_reg;
	u8 autopower_reg;
	unsigned int setter_settle_ms;
	u8 temp_regs[EWMI_TEMP_COUNT];
	u8 battery_regs[EWMI_BATTERY_REG_COUNT];
};

struct ewmi_reply {
	u8 reserved[4];
	u8 status;
	u8 data[EWMI_OUTPUT_SIZE - 1];
};

static_assert(sizeof(struct ewmi_reply) == 260);

struct ewmi_method {
	struct wmi_device *wdev;
	const struct ewmi_board_config *board;
	struct mutex lock; /* Serializes firmware calls and EC sensor reads. */
	struct device *profile;
	struct notifier_block event_nb;
	struct work_struct profile_work;
	atomic_t mailbox_event_seq; /* EC5D hotkeys invalidate in-flight queries. */
	bool profile_dirty; /* A partial set must not become a successful no-op. */
	u8 lock_dirty; /* Uncertain lock settings must be explicitly restored. */
};

struct ewmi_event_record {
	u64 sequence;
	u64 boottime_ns;
	u32 code;
};

struct ewmi_event {
	struct input_dev *input;
	struct mutex lock; /* Serializes input press/release sequences. */
	u64 count;
	u32 last_code;
	struct ewmi_event_record history[EWMI_EVENT_HISTORY];
};

struct ewmi_autopower {
	struct wmi_device *wdev;
	const struct ewmi_board_config *board;
	struct mutex lock; /* Serializes auto-power-on queries and settings. */
	bool dirty; /* A failed setting may have partially applied. */
};

static BLOCKING_NOTIFIER_HEAD(ewmi_event_chain);

/* Offsets apply only to the verified MECHREVO Lark4 Lunar Lake BIOS. */
static const struct ewmi_board_config ewmi_lark4_lnl_config = {
	.mailbox_reg = 0x5d,
	.mailbox_ext_reg = 0x5c,
	.autopower_reg = 0xf5,
	.setter_settle_ms = 100,
	.temp_regs = { 0x70, 0x73, 0x72, 0x62 },
	.battery_regs = {
		0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8c, 0x8d,
		0x8e, 0x8f, 0x90, 0x91, 0x92, 0x93, 0xa8,
	},
};

static const struct dmi_system_id ewmi_dmi_table[] = {
	{
		.ident = "MECHREVO Wujie 14 2026 Lark4 Lunar Lake",
		.matches = {
			DMI_EXACT_MATCH(DMI_SYS_VENDOR, "MECHREVO"),
			DMI_EXACT_MATCH(DMI_BOARD_NAME, "WUJIE Series-Lark4-LNL"),
			DMI_EXACT_MATCH(DMI_BIOS_VERSION, "EM_LNL326_V1.0.19"),
		},
		.driver_data = (void *)&ewmi_lark4_lnl_config,
	},
	{ }
};

static const struct ewmi_board_config *ewmi_get_board_config(void)
{
	const struct dmi_system_id *id;

	id = dmi_first_match(ewmi_dmi_table);
	return id ? id->driver_data : NULL;
}

/* Exact-board workaround: consume the reply without sending another command. */
static int ewmi_mailbox_reply(struct ewmi_method *priv, u8 command,
			      void *result, size_t result_len)
{
	u8 raw, value, keyboard[2], verify[2];
	int ret;

	lockdep_assert_held(&priv->lock);
	if (result_len != (command == 0x0b ? sizeof(keyboard) : sizeof(value)))
		return -EINVAL;
	if (command == 0x0b) {
		ret = ec_read(priv->board->mailbox_reg, &keyboard[0]);
		if (ret)
			return ret;
		ret = ec_read(priv->board->mailbox_ext_reg, &keyboard[1]);
		if (ret)
			return ret;
		ret = ec_read(priv->board->mailbox_reg, &verify[0]);
		if (ret)
			return ret;
		ret = ec_read(priv->board->mailbox_ext_reg, &verify[1]);
		if (ret)
			return ret;
		if (memcmp(keyboard, verify, sizeof(keyboard)))
			return -EAGAIN;
		memcpy(result, keyboard, sizeof(keyboard));
		return 0;
	}
	ret = ec_read(priv->board->mailbox_reg, &raw);
	if (ret)
		return ret;
	switch (command) {
	case 0x01:
		if (raw != 0 && raw != 2)
			goto invalid;
		value = raw == 2;
		break;
	case 0x03:
		if (raw > 1)
			goto invalid;
		value = raw;
		break;
	case 0x05:
		if (raw != 0 && raw != 4)
			goto invalid;
		value = raw;
		break;
	case 0x0d:
		if (raw > 2)
			goto invalid;
		value = raw;
		break;
	default:
		return -EOPNOTSUPP;
	}
	memcpy(result, &value, sizeof(value));
	return 0;

invalid:
	dev_dbg(&priv->wdev->dev,
		"Settled mailbox 03/%02x transient/invalid raw 0x%02x\n",
		command, raw);
	return -EIO;
}

/*
 * WMCD returns Package(Buffer(4), Buffer(256)). The WMI core marshals
 * both buffers into 260 bytes; the firmware status is at byte 4.
 * Always check the full result size before accessing the payload.
 */
static int ewmi_call_status(struct ewmi_method *priv, u8 group, u8 command, u8 argument,
			    void *result, size_t result_len, bool *firmware_failure)
{
	bool mailbox_query = query_settle_ms && group == 0x03 &&
			     (command == 0x01 || command == 0x03 || command == 0x05 ||
			      command == 0x0b || command == 0x0d);
	u8 request[EWMI_INPUT_SIZE] = { group, command, argument };
	struct wmi_buffer in = { .length = sizeof(request), .data = request };
	struct wmi_buffer out = { };
	const struct ewmi_reply *reply;
	int attempt, ret = -EIO;

	lockdep_assert_held(&priv->lock);
	if (firmware_failure)
		*firmware_failure = false;
	if (result_len > sizeof(reply->data))
		return -EINVAL;

	for (attempt = 0; attempt < EWMI_MAILBOX_RETRIES; attempt++) {
		int seq = atomic_read(&priv->mailbox_event_seq);

		ret = wmidev_invoke_method(priv->wdev, 0, 1, &in, &out, sizeof(*reply));
		if (ret) {
			dev_warn_ratelimited(&priv->wdev->dev,
					     "WMCD %02x/%02x transport error %d\n",
					     group, command, ret);
			return ret;
		}
		if (!mailbox_query)
			break;

		/*
		 * GWLS/GFLS/GCLS/GKBT/GBMM share EC5D with each other and with
		 * _QF6/_QF9 hotkey notifications. Wait for the EC to settle and
		 * retry if an in-flight hotkey overwrites the mailbox.
		 */
		kfree(out.data);
		out = (struct wmi_buffer) { };
		msleep(query_settle_ms);
		if (query_direct) {
			ret = ewmi_mailbox_reply(priv, command, result, result_len);
			if (!ret && atomic_read(&priv->mailbox_event_seq) != seq)
				ret = -EAGAIN;
			if (!ret)
				return 0;
			if ((ret == -EIO || ret == -EAGAIN) &&
			    attempt + 1 < EWMI_MAILBOX_RETRIES)
				continue;
			if (ret == -EAGAIN)
				ret = -EIO;
			dev_warn_ratelimited(&priv->wdev->dev,
					     "WMCD %02x/%02x settled mailbox error %d\n",
					     group, command, ret);
			return ret;
		}
		ret = wmidev_invoke_method(priv->wdev, 0, 1, &in, &out, sizeof(*reply));
		if (ret) {
			dev_warn_ratelimited(&priv->wdev->dev,
					     "WMCD %02x/%02x settled transport error %d\n",
					     group, command, ret);
			return ret;
		}
		if (atomic_read(&priv->mailbox_event_seq) == seq)
			break;
		if (attempt + 1 < EWMI_MAILBOX_RETRIES) {
			kfree(out.data);
			out = (struct wmi_buffer) { };
		}
	}

	reply = out.data;
	if (reply->status) {
		if (firmware_failure && reply->status == 1)
			*firmware_failure = true;
		dev_warn_ratelimited(&priv->wdev->dev,
				     "WMCD %02x/%02x firmware status %u, data %8ph\n",
				     group, command, reply->status, reply->data);
		ret = -EIO;
	} else if (result_len) {
		memcpy(result, reply->data, result_len);
	}
	kfree(out.data);
	return ret;
}

static int ewmi_call(struct ewmi_method *priv, u8 group, u8 command, u8 argument,
		     void *result, size_t result_len)
{
	return ewmi_call_status(priv, group, command, argument, result, result_len, NULL);
}

static ssize_t protocol_version_show(struct device *dev,
				     struct device_attribute *attr, char *buf)
{
	struct ewmi_method *priv = dev_get_drvdata(dev);
	u8 version[4];
	int ret;

	guard(mutex)(&priv->lock);
	ret = ewmi_call(priv, 0x01, 0x01, 0, version, sizeof(version));
	if (ret)
		return ret;
	return sysfs_emit(buf, "%u.%u.%u.%u\n", version[0], version[1],
			  version[2], version[3]);
}
static DEVICE_ATTR_RO(protocol_version);

/* Raw firmware profile value; 1/2 power-limit behavior under AC load still unverified. */
static ssize_t performance_mode_show(struct device *dev,
				     struct device_attribute *attr, char *buf)
{
	struct ewmi_method *priv = dev_get_drvdata(dev);
	u8 mode;
	int ret;

	guard(mutex)(&priv->lock);
	ret = ewmi_call(priv, 0x03, 0x09, 0, &mode, sizeof(mode));
	if (ret)
		return ret;
	if (mode != 1 && mode != 2)
		return -EPROTO;
	return sysfs_emit(buf, "%u\n", mode);
}
static DEVICE_ATTR_RO(performance_mode);

static ssize_t ewmi_switch_show(struct device *dev, u8 command, char *buf)
{
	struct ewmi_method *priv = dev_get_drvdata(dev);
	u8 value;
	int ret;

	guard(mutex)(&priv->lock);
	ret = ewmi_call(priv, 0x03, command, 0, &value, sizeof(value));
	if (ret)
		return ret;
	if (value > 1)
		return -EPROTO;
	return sysfs_emit(buf, "%u\n", value);
}

static ssize_t ewmi_switch_store(struct device *dev, u8 command,
				 const char *buf, size_t count)
{
	struct ewmi_method *priv = dev_get_drvdata(dev);
	bool value;
	u8 actual, mask;
	int ret;

	if (!allow_lock_writes)
		return -EPERM;
	if (command != 0x02 && command != 0x04)
		return -EINVAL;
	mask = command == 0x02 ? BIT(0) : BIT(1);
	ret = kstrtobool(buf, &value);
	if (ret)
		return ret;

	guard(mutex)(&priv->lock);
	ret = ewmi_call(priv, 0x03, command - 1, 0, &actual, sizeof(actual));
	if (ret) {
		dev_warn_ratelimited(&priv->wdev->dev,
				     "Lock 03/%02x before-set query error %d\n", command, ret);
		return ret;
	}
	if (actual == value && !(priv->lock_dirty & mask))
		return count;
	priv->lock_dirty |= mask;
	ret = ewmi_call(priv, 0x03, command, value, NULL, 0);
	if (ret)
		return ret;
	/* SFLS/SWLS do not wait: a query can overwrite the pending set command. */
	msleep(priv->board->setter_settle_ms);
	/* The paired query waits for the EC reply before returning its state. */
	ret = ewmi_call(priv, 0x03, command - 1, 0, &actual, sizeof(actual));
	if (ret) {
		dev_warn_ratelimited(&priv->wdev->dev,
				     "Lock 03/%02x after-set query error %d\n", command, ret);
		return ret;
	}
	if (actual != value) {
		dev_warn_ratelimited(&priv->wdev->dev,
				     "Lock 03/%02x requested %u, settled readback %u\n",
				     command, value, actual);
		return -EIO;
	}
	priv->lock_dirty &= ~mask;
	return count;
}

static ssize_t fn_lock_show(struct device *dev, struct device_attribute *attr,
			    char *buf)
{
	return ewmi_switch_show(dev, 0x03, buf);
}

static ssize_t fn_lock_store(struct device *dev, struct device_attribute *attr,
			     const char *buf, size_t count)
{
	return ewmi_switch_store(dev, 0x04, buf, count);
}
static DEVICE_ATTR_RW(fn_lock);

static ssize_t win_lock_show(struct device *dev, struct device_attribute *attr,
			     char *buf)
{
	return ewmi_switch_show(dev, 0x01, buf);
}

static ssize_t win_lock_store(struct device *dev, struct device_attribute *attr,
			      const char *buf, size_t count)
{
	return ewmi_switch_store(dev, 0x02, buf, count);
}
static DEVICE_ATTR_RW(win_lock);

static ssize_t touchpad_enabled_show(struct device *dev,
				     struct device_attribute *attr, char *buf)
{
	return ewmi_switch_show(dev, 0x07, buf);
}
static DEVICE_ATTR_RO(touchpad_enabled);

static ssize_t copilot_mode_raw_show(struct device *dev,
				     struct device_attribute *attr, char *buf)
{
	struct ewmi_method *priv = dev_get_drvdata(dev);
	u8 raw;
	int ret;

	guard(mutex)(&priv->lock);
	ret = ewmi_call(priv, 0x03, 0x05, 0, &raw, sizeof(raw));
	if (ret)
		return ret;
	if (raw != 0 && raw != 4)
		return -EPROTO;
	return sysfs_emit(buf, "%u\n", raw);
}
static DEVICE_ATTR_ADMIN_RO(copilot_mode_raw);

static ssize_t keyboard_backlight_raw_show(struct device *dev,
					   struct device_attribute *attr, char *buf)
{
	struct ewmi_method *priv = dev_get_drvdata(dev);
	u8 bytes[2];
	int ret;

	guard(mutex)(&priv->lock);
	ret = ewmi_call(priv, 0x03, 0x0b, 0, bytes, sizeof(bytes));
	if (ret)
		return ret;
	return sysfs_emit(buf, "%02x %02x\n", bytes[0], bytes[1]);
}
static DEVICE_ATTR_ADMIN_RO(keyboard_backlight_raw);

/* Investigation only: no verified percentage or user-facing mode names. */
static ssize_t battery_mode_raw_show(struct device *dev,
				     struct device_attribute *attr, char *buf)
{
	struct ewmi_method *priv = dev_get_drvdata(dev);
	u8 mode;
	int ret;

	guard(mutex)(&priv->lock);
	ret = ewmi_call(priv, 0x03, 0x0d, 0, &mode, sizeof(mode));
	if (ret)
		return ret;
	if (mode > 2)
		return -EPROTO;
	return sysfs_emit(buf, "%u\n", mode);
}
static DEVICE_ATTR_ADMIN_RO(battery_mode_raw);

/* Two observations, not an atomic EC transaction; expose differences as data. */
static int ewmi_battery_raw(struct ewmi_method *priv,
			    u8 raw[2][EWMI_BATTERY_REG_COUNT])
{
	int frame, i, ret;

	lockdep_assert_held(&priv->lock);
	for (frame = 0; frame < 2; frame++) {
		for (i = 0; i < EWMI_BATTERY_REG_COUNT; i++) {
			ret = ec_read(priv->board->battery_regs[i], &raw[frame][i]);
			if (ret)
				return ret;
		}
	}
	return 0;
}

static ssize_t battery_ec_raw_show(struct device *dev,
				   struct device_attribute *attr, char *buf)
{
	struct ewmi_method *priv = dev_get_drvdata(dev);
	u8 raw[2][EWMI_BATTERY_REG_COUNT];
	int ret;

	guard(mutex)(&priv->lock);
	ret = ewmi_battery_raw(priv, raw);
	if (ret)
		return ret;
	return sysfs_emit(buf, "first=%*phN second=%*phN\n",
			  (int)sizeof(raw[0]), raw[0], (int)sizeof(raw[1]), raw[1]);
}
static DEVICE_ATTR_ADMIN_RO(battery_ec_raw);

static struct attribute *ewmi_attrs[] = {
	&dev_attr_protocol_version.attr,
	&dev_attr_performance_mode.attr,
	&dev_attr_fn_lock.attr,
	&dev_attr_win_lock.attr,
	&dev_attr_touchpad_enabled.attr,
	&dev_attr_copilot_mode_raw.attr,
	&dev_attr_keyboard_backlight_raw.attr,
	&dev_attr_battery_mode_raw.attr,
	&dev_attr_battery_ec_raw.attr,
	NULL
};

static umode_t ewmi_attr_is_visible(struct kobject *kobj, struct attribute *attr,
				    int index)
{
	if (!allow_lock_writes)
		return attr->mode & ~0222;
	return attr->mode;
}

static const struct attribute_group ewmi_attr_group = {
	.attrs = ewmi_attrs,
	.is_visible = ewmi_attr_is_visible,
};

static const char * const ewmi_temp_labels[EWMI_TEMP_COUNT] = {
	"CPU", "GT VR", "Ambient", "Charger"
};

static const char * const ewmi_fan_labels[EWMI_FAN_COUNT] = { "Fan 1", "Fan 2" };

static int ewmi_hwmon_read(struct device *dev, enum hwmon_sensor_types type,
			   u32 attr, int channel, long *value)
{
	struct ewmi_method *priv = dev_get_drvdata(dev);
	u8 temperature;
	__le16 rpm;
	int ret;

	guard(mutex)(&priv->lock);
	if (type == hwmon_fan && attr == hwmon_fan_input) {
		if (channel < 0 || channel >= EWMI_FAN_COUNT)
			return -EOPNOTSUPP;
		ret = ewmi_call(priv, 0x02, 0x01, channel, &rpm, sizeof(rpm));
		if (!ret)
			*value = le16_to_cpu(rpm);
		return ret;
	}
	if (type == hwmon_temp && attr == hwmon_temp_input) {
		if (channel < 0 || channel >= EWMI_TEMP_COUNT)
			return -EOPNOTSUPP;
		ret = ec_read(priv->board->temp_regs[channel], &temperature);
		if (!ret) {
			if (temperature == 0xff)
				return -ENODATA;
			*value = temperature * 1000L;
		}
		return ret;
	}
	return -EOPNOTSUPP;
}

static int ewmi_hwmon_read_string(struct device *dev, enum hwmon_sensor_types type,
				  u32 attr, int channel, const char **str)
{
	if (type == hwmon_fan && attr == hwmon_fan_label && channel >= 0 &&
	    channel < EWMI_FAN_COUNT) {
		*str = ewmi_fan_labels[channel];
		return 0;
	}
	if (type == hwmon_temp && attr == hwmon_temp_label && channel >= 0 &&
	    channel < EWMI_TEMP_COUNT) {
		*str = ewmi_temp_labels[channel];
		return 0;
	}
	return -EOPNOTSUPP;
}

static const struct hwmon_ops ewmi_hwmon_ops = {
	.visible = 0444,
	.read = ewmi_hwmon_read,
	.read_string = ewmi_hwmon_read_string,
};

static const struct hwmon_channel_info * const ewmi_hwmon_info[] = {
	HWMON_CHANNEL_INFO(fan, HWMON_F_INPUT | HWMON_F_LABEL,
			   HWMON_F_INPUT | HWMON_F_LABEL),
	HWMON_CHANNEL_INFO(temp, HWMON_T_INPUT | HWMON_T_LABEL,
			   HWMON_T_INPUT | HWMON_T_LABEL,
			   HWMON_T_INPUT | HWMON_T_LABEL,
			   HWMON_T_INPUT | HWMON_T_LABEL),
	NULL
};

static const struct hwmon_chip_info ewmi_hwmon_chip = {
	.ops = &ewmi_hwmon_ops,
	.info = ewmi_hwmon_info,
};

static int ewmi_profile_probe(void *drvdata, unsigned long *choices)
{
	set_bit(PLATFORM_PROFILE_BALANCED, choices);
	set_bit(PLATFORM_PROFILE_PERFORMANCE, choices);
	return 0;
}

static int ewmi_profile_get(struct device *dev, enum platform_profile_option *profile)
{
	struct ewmi_method *priv = dev_get_drvdata(dev);
	u8 mode;
	int ret;

	guard(mutex)(&priv->lock);
	ret = ewmi_call(priv, 0x03, 0x09, 0, &mode, sizeof(mode));
	if (ret)
		return ret;
	switch (mode) {
	case 1:
		*profile = PLATFORM_PROFILE_BALANCED;
		return 0;
	case 2:
		*profile = PLATFORM_PROFILE_PERFORMANCE;
		return 0;
	default:
		return -EPROTO;
	}
}

static int ewmi_profile_set(struct device *dev, enum platform_profile_option profile)
{
	struct ewmi_method *priv = dev_get_drvdata(dev);
	bool firmware_failure;
	u8 mode, actual;
	int attempt, ret;

	if (!allow_writes)
		return -EPERM;
	switch (profile) {
	case PLATFORM_PROFILE_BALANCED:
		mode = 1;
		break;
	case PLATFORM_PROFILE_PERFORMANCE:
		mode = 2;
		break;
	default:
		return -EOPNOTSUPP;
	}
	guard(mutex)(&priv->lock);
	/* Avoid a redundant EC command when userspace reapplies this profile. */
	ret = ewmi_call(priv, 0x03, 0x09, 0, &actual, sizeof(actual));
	if (ret)
		return ret;
	if (actual == mode && !priv->profile_dirty)
		return 0;
	/*
	 * SPFM arms the EC (ZZTT/TIZZ) before Notify(IETM, 0x88) and before
	 * writing EC5F, so an EC scan during the notify can consume a stale
	 * EC5F and return RTNC=0 without updating PPMD; SPFM can also report
	 * RTNC=1 after changing mode. Reissue this absolute mode command up
	 * to three times when RTNC=1 or 03/09 still reads back the old mode.
	 */
	priv->profile_dirty = true;
	for (attempt = 0; attempt < EWMI_MAILBOX_RETRIES; attempt++) {
		ret = ewmi_call_status(priv, 0x03, 0x0a, mode, NULL, 0, &firmware_failure);
		if (ret) {
			if (!firmware_failure || attempt + 1 == EWMI_MAILBOX_RETRIES)
				return ret;
			msleep(priv->board->setter_settle_ms);
			continue;
		}
		ret = ewmi_call(priv, 0x03, 0x09, 0, &actual, sizeof(actual));
		if (ret)
			return ret;
		if (actual == mode) {
			priv->profile_dirty = false;
			return 0;
		}
		msleep(priv->board->setter_settle_ms);
		ret = ewmi_call(priv, 0x03, 0x09, 0, &actual, sizeof(actual));
		if (ret)
			return ret;
		if (actual == mode) {
			priv->profile_dirty = false;
			return 0;
		}
		if (attempt + 1 < EWMI_MAILBOX_RETRIES)
			dev_dbg(dev,
				"Profile 03/0a requested %u, readback %u on attempt %d/%d; retrying\n",
				mode, actual, attempt + 1, EWMI_MAILBOX_RETRIES);
	}
	dev_warn_ratelimited(dev, "Profile 03/0a requested %u, settled readback %u\n",
			     mode, actual);
	return -EIO;
}

static const struct platform_profile_ops ewmi_profile_ops = {
	.probe = ewmi_profile_probe,
	.profile_get = ewmi_profile_get,
	.profile_set = ewmi_profile_set,
};

static void ewmi_profile_work(struct work_struct *work)
{
	struct ewmi_method *priv = container_of(work, struct ewmi_method, profile_work);

	platform_profile_notify(priv->profile);
}

static int ewmi_method_event(struct notifier_block *nb, unsigned long code, void *data)
{
	struct ewmi_method *priv = container_of(nb, struct ewmi_method, event_nb);
	struct wmi_device *event_wdev = data;

	if (event_wdev->dev.parent != priv->wdev->dev.parent)
		return NOTIFY_DONE;
	/* _QF6 (0x50/0x51) and _QF9 (0x20-0x22) share EC5D with WMCD queries. */
	if (code == 0x20 || code == 0x21 || code == 0x22 ||
	    code == 0x50 || code == 0x51)
		atomic_inc(&priv->mailbox_event_seq);
	if (priv->profile && (code == 0 || code == 0x41 || code == 0x42))
		schedule_work(&priv->profile_work);
	return NOTIFY_OK;
}

static void ewmi_method_stop(void *data)
{
	struct ewmi_method *priv = data;

	blocking_notifier_chain_unregister(&ewmi_event_chain, &priv->event_nb);
	if (priv->profile)
		cancel_work_sync(&priv->profile_work);
}

static int ewmi_method_probe(struct wmi_device *wdev, const void *context)
{
	static const u8 expected_version[] = { 1, 0, 1, 0 };
	const struct ewmi_board_config *board;
	struct ewmi_method *priv;
	struct device *hwmon;
	u8 version[4];
	int ret;

	board = ewmi_get_board_config();
	if (!board)
		return -ENODEV;
	priv = devm_kzalloc(&wdev->dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;
	priv->wdev = wdev;
	priv->board = board;
	mutex_init(&priv->lock);
	dev_set_drvdata(&wdev->dev, priv);

	scoped_guard(mutex, &priv->lock)
		ret = ewmi_call(priv, 0x01, 0x01, 0, version, sizeof(version));
	if (ret)
		return dev_err_probe(&wdev->dev, ret, "Cannot query protocol version\n");
	if (memcmp(version, expected_version, sizeof(version)))
		return dev_err_probe(&wdev->dev, -ENODEV, "Unsupported protocol version\n");

	if (allow_writes) {
		INIT_WORK(&priv->profile_work, ewmi_profile_work);
		priv->profile = devm_platform_profile_register(&wdev->dev, "emdoor-wmi",
							       priv, &ewmi_profile_ops);
		if (IS_ERR(priv->profile))
			return dev_err_probe(&wdev->dev, PTR_ERR(priv->profile),
					     "Cannot register platform profile\n");
	}
	priv->event_nb.notifier_call = ewmi_method_event;
	ret = blocking_notifier_chain_register(&ewmi_event_chain, &priv->event_nb);
	if (ret)
		return ret;
	ret = devm_add_action_or_reset(&wdev->dev, ewmi_method_stop, priv);
	if (ret)
		return ret;

	ret = devm_device_add_group(&wdev->dev, &ewmi_attr_group);
	if (ret)
		return ret;
	hwmon = devm_hwmon_device_register_with_info(&wdev->dev, "emdoor_ewmi", priv,
						     &ewmi_hwmon_chip, NULL);
	if (IS_ERR(hwmon))
		return dev_err_probe(&wdev->dev, PTR_ERR(hwmon), "Cannot register hwmon\n");

	dev_info(&wdev->dev, "EWMI protocol 1.0.1.0, profile writes %s, lock writes %s\n",
		 allow_writes ? "enabled" : "disabled",
		 allow_lock_writes ? "enabled" : "disabled");
	return 0;
}

/* Firmware already changes these states; never request a second toggle. */
static const struct key_entry ewmi_keymap[] = {
	{ KE_KEY, 0x30, { KEY_TOUCHPAD_OFF } },
	{ KE_KEY, 0x31, { KEY_TOUCHPAD_ON } },
	{ KE_KEY, 0xa0, { KEY_PROG1 } },
	{ KE_KEY, 0xa1, { KEY_PROG2 } },
	{ KE_KEY, 0xa2, { KEY_PROG3 } },
	{ KE_IGNORE, 0x00, { 0 } },
	{ KE_IGNORE, 0x41, { 0 } },
	{ KE_IGNORE, 0x42, { 0 } },
	{ KE_IGNORE, 0x20, { 0 } },
	{ KE_IGNORE, 0x21, { 0 } },
	{ KE_IGNORE, 0x22, { 0 } },
	{ KE_IGNORE, 0x50, { 0 } },
	{ KE_IGNORE, 0x51, { 0 } },
	{ KE_END, 0 }
};

static void ewmi_record_event(struct ewmi_event *priv, u32 code)
{
	struct ewmi_event_record *record;

	lockdep_assert_held(&priv->lock);
	priv->count++;
	priv->last_code = code;
	record = &priv->history[(priv->count - 1) % EWMI_EVENT_HISTORY];
	record->sequence = priv->count;
	record->boottime_ns = ktime_get_boottime_ns();
	record->code = code;
}

static ssize_t event_state_show(struct device *dev, struct device_attribute *attr,
				char *buf)
{
	struct ewmi_event *priv = dev_get_drvdata(dev);

	guard(mutex)(&priv->lock);
	return sysfs_emit(buf, "count=%llu last=0x%08x\n", priv->count, priv->last_code);
}
static DEVICE_ATTR_ADMIN_RO(event_state);

static ssize_t event_log_show(struct device *dev, struct device_attribute *attr,
			      char *buf)
{
	struct ewmi_event *priv = dev_get_drvdata(dev);
	const struct ewmi_event_record *record;
	u64 first, sequence;
	ssize_t length = 0;

	guard(mutex)(&priv->lock);
	first = priv->count > EWMI_EVENT_HISTORY ? priv->count - EWMI_EVENT_HISTORY : 0;
	for (sequence = first; sequence < priv->count; sequence++) {
		record = &priv->history[sequence % EWMI_EVENT_HISTORY];
		length += sysfs_emit_at(buf, length, "%llu %llu 0x%08x\n",
					record->sequence, record->boottime_ns, record->code);
	}
	return length;
}
static DEVICE_ATTR_ADMIN_RO(event_log);

static struct attribute *ewmi_event_attrs[] = {
	&dev_attr_event_state.attr,
	&dev_attr_event_log.attr,
	NULL
};

static const struct attribute_group ewmi_event_group = {
	.attrs = ewmi_event_attrs,
};

static int ewmi_event_probe(struct wmi_device *wdev, const void *context)
{
	struct ewmi_event *priv;
	int ret;

	if (!ewmi_get_board_config())
		return -ENODEV;
	priv = devm_kzalloc(&wdev->dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;
	mutex_init(&priv->lock);
	priv->input = devm_input_allocate_device(&wdev->dev);
	if (!priv->input)
		return -ENOMEM;
	priv->input->name = "Emdoor EWMI hotkeys";
	priv->input->phys = "wmi/input0";
	priv->input->id.bustype = BUS_HOST;
	ret = sparse_keymap_setup(priv->input, ewmi_keymap, NULL);
	if (ret)
		return ret;
	dev_set_drvdata(&wdev->dev, priv);
	ret = devm_device_add_group(&wdev->dev, &ewmi_event_group);
	if (ret)
		return ret;
	return input_register_device(priv->input);
}

static void ewmi_event_notify(struct wmi_device *wdev, const struct wmi_buffer *event)
{
	struct ewmi_event *priv = dev_get_drvdata(&wdev->dev);
	u32 code = get_unaligned_le32(event->data);

	blocking_notifier_call_chain(&ewmi_event_chain, code, wdev);
	guard(mutex)(&priv->lock);
	ewmi_record_event(priv, code);
	if (!sparse_keymap_report_event(priv->input, code, 1, true))
		dev_dbg(&wdev->dev, "Unknown event 0x%x\n", code);
}

static const struct wmi_device_id ewmi_method_ids[] = {
	{ EWMI_METHOD_GUID, NULL },
	{ }
};
MODULE_DEVICE_TABLE(wmi, ewmi_method_ids);

static const struct wmi_device_id ewmi_event_ids[] = {
	{ EWMI_EVENT_GUID, NULL },
	{ }
};
MODULE_DEVICE_TABLE(wmi, ewmi_event_ids);

static struct wmi_driver ewmi_method_driver = {
	.driver.name = "emdoor-ewmi",
	.id_table = ewmi_method_ids,
	.no_singleton = true,
	.probe = ewmi_method_probe,
};

static struct wmi_driver ewmi_event_driver = {
	.driver.name = "emdoor-ewmi-events",
	.id_table = ewmi_event_ids,
	.no_singleton = true,
	.min_event_size = sizeof(__le32),
	.probe = ewmi_event_probe,
	.notify_new = ewmi_event_notify,
};

static int ewmi_autopower_get(struct ewmi_autopower *priv, bool *enabled)
{
	u8 raw;
	int ret;

	lockdep_assert_held(&priv->lock);
	ret = ec_read(priv->board->autopower_reg, &raw);
	if (ret)
		return ret;
	if (raw != 0 && raw != 0xa0)
		return -EPROTO;
	*enabled = raw == 0xa0;
	return 0;
}

static int ewmi_autopower_set(struct ewmi_autopower *priv, bool enabled)
{
	__le64 argument = cpu_to_le64(enabled);
	struct wmi_buffer in = { .length = sizeof(argument), .data = &argument };
	struct wmi_buffer out = { };
	bool actual;
	u8 status;
	int ret;

	lockdep_assert_held(&priv->lock);
	if (!allow_autopower_writes)
		return -EPERM;
	ret = ewmi_autopower_get(priv, &actual);
	if (ret || (actual == enabled && !priv->dirty))
		return ret;
	priv->dirty = true;
	ret = wmidev_invoke_method(priv->wdev, 0, 1, &in, &out, 8);
	if (ret) {
		dev_warn_ratelimited(&priv->wdev->dev, "WMCC transport error %d\n", ret);
		return ret;
	}
	status = ((u8 *)out.data)[0];
	ret = status ? -EIO : 0;
	kfree(out.data);
	msleep(priv->board->setter_settle_ms);
	if (ret) {
		dev_warn_ratelimited(&priv->wdev->dev, "WMCC firmware status %u\n", status);
		return ret;
	}
	ret = ewmi_autopower_get(priv, &actual);
	if (ret) {
		dev_warn_ratelimited(&priv->wdev->dev, "WMCC readback error %d\n", ret);
		return ret;
	}
	if (actual != enabled) {
		dev_warn_ratelimited(&priv->wdev->dev, "WMCC requested %u, readback %u\n",
				     enabled, actual);
		return -EIO;
	}
	priv->dirty = false;
	return 0;
}

static ssize_t auto_power_on_show(struct device *dev, struct device_attribute *attr,
				  char *buf)
{
	struct ewmi_autopower *priv = dev_get_drvdata(dev);
	bool enabled;
	int ret;

	guard(mutex)(&priv->lock);
	ret = ewmi_autopower_get(priv, &enabled);
	if (ret)
		return ret;
	return sysfs_emit(buf, "%u\n", enabled);
}

static ssize_t auto_power_on_store(struct device *dev, struct device_attribute *attr,
				   const char *buf, size_t count)
{
	struct ewmi_autopower *priv = dev_get_drvdata(dev);
	bool enabled;
	int ret;

	ret = kstrtobool(buf, &enabled);
	if (ret)
		return ret;
	guard(mutex)(&priv->lock);
	ret = ewmi_autopower_set(priv, enabled);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(auto_power_on);

static struct attribute *ewmi_autopower_attrs[] = {
	&dev_attr_auto_power_on.attr,
	NULL
};

static umode_t ewmi_autopower_is_visible(struct kobject *kobj, struct attribute *attr,
					 int index)
{
	return allow_autopower_writes ? attr->mode : attr->mode & ~0222;
}

static const struct attribute_group ewmi_autopower_group = {
	.attrs = ewmi_autopower_attrs,
	.is_visible = ewmi_autopower_is_visible,
};

static int ewmi_autopower_probe(struct wmi_device *wdev, const void *context)
{
	const struct ewmi_board_config *board;
	struct ewmi_autopower *priv;

	board = ewmi_get_board_config();
	if (!board)
		return -ENODEV;
	priv = devm_kzalloc(&wdev->dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;
	priv->wdev = wdev;
	priv->board = board;
	mutex_init(&priv->lock);
	dev_set_drvdata(&wdev->dev, priv);
	return devm_device_add_group(&wdev->dev, &ewmi_autopower_group);
}

static const struct wmi_device_id ewmi_autopower_ids[] = {
	{ EWMI_AUTOPOWER_GUID, NULL },
	{ }
};
MODULE_DEVICE_TABLE(wmi, ewmi_autopower_ids);

static struct wmi_driver ewmi_autopower_driver = {
	.driver.name = "emdoor-ewmi-autopower",
	.id_table = ewmi_autopower_ids,
	.no_singleton = true,
	.probe = ewmi_autopower_probe,
};

static int __init ewmi_init(void)
{
	int ret;

	if (query_settle_ms > 200 || (query_direct && !query_settle_ms))
		return -EINVAL;
	ret = wmi_driver_register(&ewmi_method_driver);
	if (ret)
		return ret;
	ret = wmi_driver_register(&ewmi_event_driver);
	if (ret) {
		wmi_driver_unregister(&ewmi_method_driver);
		return ret;
	}
	ret = wmi_driver_register(&ewmi_autopower_driver);
	if (ret) {
		wmi_driver_unregister(&ewmi_event_driver);
		wmi_driver_unregister(&ewmi_method_driver);
	}
	return ret;
}

static void __exit ewmi_exit(void)
{
	wmi_driver_unregister(&ewmi_autopower_driver);
	wmi_driver_unregister(&ewmi_event_driver);
	wmi_driver_unregister(&ewmi_method_driver);
}

module_init(ewmi_init);
module_exit(ewmi_exit);

MODULE_AUTHOR("xzq");
MODULE_DESCRIPTION("Emdoor Intel EWMI platform driver");
MODULE_LICENSE("GPL");
MODULE_VERSION("0.4.4");

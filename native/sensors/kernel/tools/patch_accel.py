#!/usr/bin/env python3
# Applies the Android-mode changes to drivers/iio/accel/hid-sensor-accel-3d.c (work copy).
import sys

p = sys.argv[1]
s = open(p, newline="\n").read()


def rep(old, new, cnt=1):
    global s
    n = s.count(old)
    assert n == cnt, (old[:70], n)
    s = s.replace(old, new)


rep('#include "../common/hid-sensors/hid-sensor-trigger.h"\n',
    '#include "../common/hid-sensors/hid-sensor-trigger.h"\n'
    '#include "../common/hid-sensors/hid-sensor-android.h"\n'
    '\n'
    '/*\n'
    ' * Android mode (default): present the HID accelerometer as the ChromeOS EC "cros-ec-accel" device the\n'
    ' * Android desktop IIO sensors sub-HAL expects. See hid-sensor-android.h.\n'
    ' */\n'
    'static bool android = true;\n'
    'module_param(android, bool, 0444);\n'
    'MODULE_PARM_DESC(android, "Present the accelerometer as cros-ec-accel for the Android sensors HAL (default: 1)");\n'
    'static char *android_label = "accel-display";\n'
    'module_param(android_label, charp, 0444);\n'
    'MODULE_PARM_DESC(android_label, "IIO label in Android mode: accel-display (lid/tablet, default) or accel-base");\n'
    'static u32 android_axes = HID_SENSOR_ANDROID_AXES(0, 1, 2, 1, 1, 1);\n'
    'HID_SENSOR_ANDROID_AXES_PARAM(android_axes, "Android mode: Android x,y,z from HID axes, - negates (default: -x,-y,-z = HID/Windows gravity sign -> Android)");\n'
    'static bool android_hyst0 = true;\n'
    'module_param(android_hyst0, bool, 0444);\n'
    'MODULE_PARM_DESC(android_hyst0, "Android mode: set change sensitivity to 0 at probe so samples are periodic (default: 1)");\n'
    '\n'
    '/* Android mode: 1 count = 1/4096 g (+-8 g range); scale = 0.002394202 m/s^2 */\n'
    '#define ACCEL_3D_ANDROID_LSB_NANO\t2394202LL\n')

rep('''	int scale_pre_decml;
	int scale_post_decml;
	int scale_precision;
	int value_offset;
	int64_t timestamp;
};
''', '''	int scale_pre_decml;
	int scale_post_decml;
	int scale_precision;
	int value_offset;
	int64_t timestamp;
	/* Android mode */
	bool android;
	s32 android_raw[ACCEL_3D_CHANNEL_MAX];
	struct hid_sensor_android_scan android_scan;
};
''')

rep('''	IIO_CHAN_SOFT_TIMESTAMP(CHANNEL_SCAN_INDEX_TIMESTAMP),
};

/* Adjust channel real bits based on report descriptor */''', '''	IIO_CHAN_SOFT_TIMESTAMP(CHANNEL_SCAN_INDEX_TIMESTAMP),
};

/* Android mode channels: s16 x/y/z + timestamp, shared "scale" and "sampling_frequency" */
static const struct iio_chan_spec accel_3d_android_channels[] = {
	HID_SENSOR_ANDROID_CHANNEL(IIO_ACCEL, IIO_MOD_X, CHANNEL_SCAN_INDEX_X),
	HID_SENSOR_ANDROID_CHANNEL(IIO_ACCEL, IIO_MOD_Y, CHANNEL_SCAN_INDEX_Y),
	HID_SENSOR_ANDROID_CHANNEL(IIO_ACCEL, IIO_MOD_Z, CHANNEL_SCAN_INDEX_Z),
	IIO_CHAN_SOFT_TIMESTAMP(CHANNEL_SCAN_INDEX_TIMESTAMP),
};

static s16 accel_3d_android_value(struct accel_3d_state *st, s32 raw, bool negate)
{
	return hid_sensor_android_to_s16(raw, st->scale_pre_decml, st->scale_post_decml,
					 ACCEL_3D_ANDROID_LSB_NANO, negate);
}

/* Adjust channel real bits based on report descriptor */''')

rep('''	s32 min;
	struct hid_sensor_hub_device *hsdev =
					accel_state->common_attributes.hsdev;
''', '''	s32 min;
	u32 axes = READ_ONCE(android_axes);
	int idx = chan->scan_index;
	struct hid_sensor_hub_device *hsdev =
					accel_state->common_attributes.hsdev;
''')

rep('''		report_id = accel_state->accel[chan->scan_index].report_id;
		min = accel_state->accel[chan->scan_index].logical_minimum;
		address = accel_3d_addresses[chan->scan_index];
''', '''		/* Android mode: Android axis scan_index comes from HID axis SRC(scan_index) */
		if (accel_state->android)
			idx = HID_SENSOR_ANDROID_AXIS_SRC(axes, chan->scan_index);
		report_id = accel_state->accel[idx].report_id;
		min = accel_state->accel[idx].logical_minimum;
		address = accel_3d_addresses[idx];
''')

rep('''		if (report_id >= 0)
			*val = sensor_hub_input_attr_get_raw_value(
					accel_state->common_attributes.hsdev,
					hsdev->usage, address, report_id,
					SENSOR_HUB_SYNC,
					min < 0);
		else {''', '''		if (report_id >= 0) {
			*val = sensor_hub_input_attr_get_raw_value(
					accel_state->common_attributes.hsdev,
					hsdev->usage, address, report_id,
					SENSOR_HUB_SYNC,
					min < 0);
			if (accel_state->android)
				*val = accel_3d_android_value(accel_state, *val,
					HID_SENSOR_ANDROID_AXIS_NEG(axes, chan->scan_index));
		} else {''')

rep('''	case IIO_CHAN_INFO_SCALE:
		*val = accel_state->scale_pre_decml;
		*val2 = accel_state->scale_post_decml;
		ret_type = accel_state->scale_precision;
		break;''', '''	case IIO_CHAN_INFO_SCALE:
		if (accel_state->android) {
			*val = 0;
			*val2 = ACCEL_3D_ANDROID_LSB_NANO;
			ret_type = IIO_VAL_INT_PLUS_NANO;
			break;
		}
		*val = accel_state->scale_pre_decml;
		*val2 = accel_state->scale_post_decml;
		ret_type = accel_state->scale_precision;
		break;''')

rep('''	switch (mask) {
	case IIO_CHAN_INFO_SAMP_FREQ:
		ret = hid_sensor_write_samp_freq_value(
				&accel_state->common_attributes, val, val2);
		break;''', '''	switch (mask) {
	case IIO_CHAN_INFO_SAMP_FREQ:
		/* The Android HAL writes 0 Hz when it stops a sensor; keep the last real interval. */
		if (accel_state->android && val == 0 && val2 == 0)
			break;
		ret = hid_sensor_write_samp_freq_value(
				&accel_state->common_attributes, val, val2);
		break;''')

rep('''static const struct iio_info accel_3d_info = {
	.read_raw = &accel_3d_read_raw,
	.write_raw = &accel_3d_write_raw,
};
''', '''static int accel_3d_read_avail(struct iio_dev *indio_dev,
			       struct iio_chan_spec const *chan,
			       const int **vals, int *type, int *length,
			       long mask)
{
	if (mask != IIO_CHAN_INFO_SAMP_FREQ)
		return -EINVAL;

	*vals = hid_sensor_android_freq_avail;
	*type = IIO_VAL_INT_PLUS_MICRO;
	*length = ARRAY_SIZE(hid_sensor_android_freq_avail);
	return IIO_AVAIL_LIST;
}

static const struct iio_info accel_3d_info = {
	.read_raw = &accel_3d_read_raw,
	.write_raw = &accel_3d_write_raw,
	.read_avail = &accel_3d_read_avail,
};
''')

rep('''	dev_dbg(&indio_dev->dev, "accel_3d_proc_event\\n");
	if (atomic_read(&accel_state->common_attributes.data_ready)) {
		if (!accel_state->timestamp)''', '''	dev_dbg(&indio_dev->dev, "accel_3d_proc_event\\n");
	if (accel_state->android) {
		struct hid_sensor_android_scan *scan = &accel_state->android_scan;
		u32 axes = READ_ONCE(android_axes);
		int i;

		if (atomic_read(&accel_state->common_attributes.data_ready)) {
			for (i = 0; i < ACCEL_3D_CHANNEL_MAX; i++)
				scan->val[i] = accel_3d_android_value(accel_state,
					accel_state->android_raw[HID_SENSOR_ANDROID_AXIS_SRC(axes, i)],
					HID_SENSOR_ANDROID_AXIS_NEG(axes, i));
			iio_push_to_buffers_with_ts(indio_dev, scan, sizeof(*scan),
						    hid_sensor_android_timestamp());
		}
		accel_state->timestamp = 0;
		return 0;
	}
	if (atomic_read(&accel_state->common_attributes.data_ready)) {
		if (!accel_state->timestamp)''')

rep('''		offset = usage_id - HID_USAGE_SENSOR_ACCEL_X_AXIS;
		accel_state->scan.accel_val[CHANNEL_SCAN_INDEX_X + offset] =
						*(u32 *)raw_data;
		ret = 0;''', '''		offset = usage_id - HID_USAGE_SENSOR_ACCEL_X_AXIS;
		if (accel_state->android) {
			accel_state->android_raw[CHANNEL_SCAN_INDEX_X + offset] =
				hid_sensor_android_get_raw(raw_data, raw_len,
					accel_state->accel[CHANNEL_SCAN_INDEX_X + offset].logical_minimum < 0);
			ret = 0;
			break;
		}
		accel_state->scan.accel_val[CHANNEL_SCAN_INDEX_X + offset] =
						*(u32 *)raw_data;
		ret = 0;''')

rep('''	if (hsdev->usage == HID_USAGE_SENSOR_ACCEL_3D) {
		name = "accel_3d";
		channel_spec = accel_3d_channels;
		channel_size = sizeof(accel_3d_channels);
		indio_dev->num_channels = ARRAY_SIZE(accel_3d_channels);
	} else {''', '''	if (hsdev->usage == HID_USAGE_SENSOR_ACCEL_3D && android) {
		/* Android mode only for the real accelerometer; the gravity sensor keeps its ABI */
		accel_state->android = true;
		name = "accel_3d";
		channel_spec = accel_3d_android_channels;
		channel_size = sizeof(accel_3d_android_channels);
		indio_dev->num_channels = ARRAY_SIZE(accel_3d_android_channels);
	} else if (hsdev->usage == HID_USAGE_SENSOR_ACCEL_3D) {
		name = "accel_3d";
		channel_spec = accel_3d_channels;
		channel_size = sizeof(accel_3d_channels);
		indio_dev->num_channels = ARRAY_SIZE(accel_3d_channels);
	} else {''')

rep('''	indio_dev->info = &accel_3d_info;
	indio_dev->name = name;
	indio_dev->modes = INDIO_DIRECT_MODE;
''', '''	indio_dev->info = &accel_3d_info;
	indio_dev->name = name;
	indio_dev->modes = INDIO_DIRECT_MODE;

	if (accel_state->android) {
		struct iio_chan_spec *chans = (struct iio_chan_spec *)indio_dev->channels;
		int i;

		/* parse_report set the HID field widths; Android mode always pushes s16/16 */
		for (i = 0; i < ACCEL_3D_CHANNEL_MAX; i++)
			chans[i].scan_type = accel_3d_android_channels[i].scan_type;
		indio_dev->name = "cros-ec-accel";
		indio_dev->label = android_label;
		indio_dev->available_scan_masks = hid_sensor_android_scan_masks;
		if (android_hyst0 && accel_state->common_attributes.sensitivity.index >= 0)
			hid_sensor_write_raw_hyst_value(&accel_state->common_attributes, 0, 0);
		dev_info(&pdev->dev, "Android mode: cros-ec-accel (%s), HID scale %d.%09d\\n",
			 android_label, accel_state->scale_pre_decml,
			 accel_state->scale_post_decml);
	}
''')

open(p, "w", newline="\n").write(s)
print("patched", p)

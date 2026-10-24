#!/usr/bin/env python3
# Applies the Android-mode changes to drivers/iio/gyro/hid-sensor-gyro-3d.c (work copy).
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
    ' * Android mode (default): present the HID gyroscope as the ChromeOS EC "cros-ec-gyro" device the\n'
    ' * Android desktop IIO sensors sub-HAL expects. See hid-sensor-android.h.\n'
    ' */\n'
    'static bool android = true;\n'
    'module_param(android, bool, 0444);\n'
    'MODULE_PARM_DESC(android, "Present the gyroscope as cros-ec-gyro for the Android sensors HAL (default: 1)");\n'
    'static char *android_label = "accel-display";\n'
    'module_param(android_label, charp, 0444);\n'
    'MODULE_PARM_DESC(android_label, "IIO label in Android mode: accel-display (lid/tablet, default) or accel-base");\n'
    'static u32 android_axes = HID_SENSOR_ANDROID_AXES(0, 1, 2, 0, 0, 0);\n'
    'HID_SENSOR_ANDROID_AXES_PARAM(android_axes, "Android mode: Android x,y,z from HID axes, - negates (default: x,y,z, HID and Android both use the right-hand rule)");\n'
    'static bool android_hyst0 = true;\n'
    'module_param(android_hyst0, bool, 0444);\n'
    'MODULE_PARM_DESC(android_hyst0, "Android mode: set change sensitivity to 0 at probe so samples are periodic (default: 1)");\n'
    '\n'
    '/* Android mode: 1 count = 1 mrad/s (+-32.7 rad/s = +-1877 dps range); scale = 0.001 rad/s */\n'
    '#define GYRO_3D_ANDROID_LSB_NANO\t1000000LL\n')

rep('''	int scale_pre_decml;
	int scale_post_decml;
	int scale_precision;
	int value_offset;
	s64 timestamp;
};
''', '''	int scale_pre_decml;
	int scale_post_decml;
	int scale_precision;
	int value_offset;
	s64 timestamp;
	/* Android mode */
	bool android;
	s32 android_raw[GYRO_3D_CHANNEL_MAX];
	struct hid_sensor_android_scan android_scan;
};
''')

rep('''	IIO_CHAN_SOFT_TIMESTAMP(CHANNEL_SCAN_INDEX_TIMESTAMP)
};

/* Adjust channel real bits based on report descriptor */''', '''	IIO_CHAN_SOFT_TIMESTAMP(CHANNEL_SCAN_INDEX_TIMESTAMP)
};

/* Android mode channels: s16 x/y/z + timestamp, shared "scale" and "sampling_frequency" */
static const struct iio_chan_spec gyro_3d_android_channels[] = {
	HID_SENSOR_ANDROID_CHANNEL(IIO_ANGL_VEL, IIO_MOD_X, CHANNEL_SCAN_INDEX_X),
	HID_SENSOR_ANDROID_CHANNEL(IIO_ANGL_VEL, IIO_MOD_Y, CHANNEL_SCAN_INDEX_Y),
	HID_SENSOR_ANDROID_CHANNEL(IIO_ANGL_VEL, IIO_MOD_Z, CHANNEL_SCAN_INDEX_Z),
	IIO_CHAN_SOFT_TIMESTAMP(CHANNEL_SCAN_INDEX_TIMESTAMP),
};

static s16 gyro_3d_android_value(struct gyro_3d_state *st, s32 raw, bool negate)
{
	return hid_sensor_android_to_s16(raw, st->scale_pre_decml, st->scale_post_decml,
					 GYRO_3D_ANDROID_LSB_NANO, negate);
}

/* Adjust channel real bits based on report descriptor */''')

rep('''	u32 address;
	int ret_type;
	s32 min;

	*val = 0;
	*val2 = 0;
	switch (mask) {
	case IIO_CHAN_INFO_RAW:
		hid_sensor_power_state(&gyro_state->common_attributes, true);
		report_id = gyro_state->gyro[chan->scan_index].report_id;
		min = gyro_state->gyro[chan->scan_index].logical_minimum;
		address = gyro_3d_addresses[chan->scan_index];
''', '''	u32 address;
	int ret_type;
	s32 min;
	u32 axes = READ_ONCE(android_axes);
	int idx = chan->scan_index;

	*val = 0;
	*val2 = 0;
	switch (mask) {
	case IIO_CHAN_INFO_RAW:
		hid_sensor_power_state(&gyro_state->common_attributes, true);
		/* Android mode: Android axis scan_index comes from HID axis SRC(scan_index) */
		if (gyro_state->android)
			idx = HID_SENSOR_ANDROID_AXIS_SRC(axes, chan->scan_index);
		report_id = gyro_state->gyro[idx].report_id;
		min = gyro_state->gyro[idx].logical_minimum;
		address = gyro_3d_addresses[idx];
''')

rep('''		if (report_id >= 0)
			*val = sensor_hub_input_attr_get_raw_value(
					gyro_state->common_attributes.hsdev,
					HID_USAGE_SENSOR_GYRO_3D, address,
					report_id,
					SENSOR_HUB_SYNC,
					min < 0);
		else {''', '''		if (report_id >= 0) {
			*val = sensor_hub_input_attr_get_raw_value(
					gyro_state->common_attributes.hsdev,
					HID_USAGE_SENSOR_GYRO_3D, address,
					report_id,
					SENSOR_HUB_SYNC,
					min < 0);
			if (gyro_state->android)
				*val = gyro_3d_android_value(gyro_state, *val,
					HID_SENSOR_ANDROID_AXIS_NEG(axes, chan->scan_index));
		} else {''')

rep('''	case IIO_CHAN_INFO_SCALE:
		*val = gyro_state->scale_pre_decml;
		*val2 = gyro_state->scale_post_decml;
		ret_type = gyro_state->scale_precision;
		break;''', '''	case IIO_CHAN_INFO_SCALE:
		if (gyro_state->android) {
			*val = 0;
			*val2 = GYRO_3D_ANDROID_LSB_NANO;
			ret_type = IIO_VAL_INT_PLUS_NANO;
			break;
		}
		*val = gyro_state->scale_pre_decml;
		*val2 = gyro_state->scale_post_decml;
		ret_type = gyro_state->scale_precision;
		break;''')

rep('''	switch (mask) {
	case IIO_CHAN_INFO_SAMP_FREQ:
		ret = hid_sensor_write_samp_freq_value(
				&gyro_state->common_attributes, val, val2);
		break;''', '''	switch (mask) {
	case IIO_CHAN_INFO_SAMP_FREQ:
		/* The Android HAL writes 0 Hz when it stops a sensor; keep the last real interval. */
		if (gyro_state->android && val == 0 && val2 == 0)
			break;
		ret = hid_sensor_write_samp_freq_value(
				&gyro_state->common_attributes, val, val2);
		break;''')

rep('''static const struct iio_info gyro_3d_info = {
	.read_raw = &gyro_3d_read_raw,
	.write_raw = &gyro_3d_write_raw,
};
''', '''static int gyro_3d_read_avail(struct iio_dev *indio_dev,
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

static const struct iio_info gyro_3d_info = {
	.read_raw = &gyro_3d_read_raw,
	.write_raw = &gyro_3d_write_raw,
	.read_avail = &gyro_3d_read_avail,
};
''')

rep('''	dev_dbg(&indio_dev->dev, "gyro_3d_proc_event\\n");
	if (atomic_read(&gyro_state->common_attributes.data_ready)) {''', '''	dev_dbg(&indio_dev->dev, "gyro_3d_proc_event\\n");
	if (gyro_state->android) {
		struct hid_sensor_android_scan *scan = &gyro_state->android_scan;
		u32 axes = READ_ONCE(android_axes);
		int i;

		if (atomic_read(&gyro_state->common_attributes.data_ready)) {
			for (i = 0; i < GYRO_3D_CHANNEL_MAX; i++)
				scan->val[i] = gyro_3d_android_value(gyro_state,
					gyro_state->android_raw[HID_SENSOR_ANDROID_AXIS_SRC(axes, i)],
					HID_SENSOR_ANDROID_AXIS_NEG(axes, i));
			iio_push_to_buffers_with_ts(indio_dev, scan, sizeof(*scan),
						    hid_sensor_android_timestamp());
		}
		gyro_state->timestamp = 0;
		return 0;
	}
	if (atomic_read(&gyro_state->common_attributes.data_ready)) {''')

rep('''		offset = usage_id - HID_USAGE_SENSOR_ANGL_VELOCITY_X_AXIS;
		gyro_state->scan.gyro_val[CHANNEL_SCAN_INDEX_X + offset] =
				*(u32 *)raw_data;
		ret = 0;''', '''		offset = usage_id - HID_USAGE_SENSOR_ANGL_VELOCITY_X_AXIS;
		if (gyro_state->android) {
			gyro_state->android_raw[CHANNEL_SCAN_INDEX_X + offset] =
				hid_sensor_android_get_raw(raw_data, raw_len,
					gyro_state->gyro[CHANNEL_SCAN_INDEX_X + offset].logical_minimum < 0);
			ret = 0;
			break;
		}
		gyro_state->scan.gyro_val[CHANNEL_SCAN_INDEX_X + offset] =
				*(u32 *)raw_data;
		ret = 0;''')

rep('''	indio_dev->channels = devm_kmemdup(&pdev->dev, gyro_3d_channels,
					   sizeof(gyro_3d_channels), GFP_KERNEL);''', '''	gyro_state->android = android;
	if (gyro_state->android)
		indio_dev->channels = devm_kmemdup(&pdev->dev, gyro_3d_android_channels,
						   sizeof(gyro_3d_android_channels), GFP_KERNEL);
	else
		indio_dev->channels = devm_kmemdup(&pdev->dev, gyro_3d_channels,
						   sizeof(gyro_3d_channels), GFP_KERNEL);''')

rep('''	indio_dev->num_channels = ARRAY_SIZE(gyro_3d_channels);
	indio_dev->info = &gyro_3d_info;
	indio_dev->name = name;
	indio_dev->modes = INDIO_DIRECT_MODE;
''', '''	indio_dev->num_channels = ARRAY_SIZE(gyro_3d_channels);
	indio_dev->info = &gyro_3d_info;
	indio_dev->name = name;
	indio_dev->modes = INDIO_DIRECT_MODE;

	if (gyro_state->android) {
		struct iio_chan_spec *chans = (struct iio_chan_spec *)indio_dev->channels;
		int i;

		/* parse_report set the HID field widths; Android mode always pushes s16/16 */
		for (i = 0; i < GYRO_3D_CHANNEL_MAX; i++)
			chans[i].scan_type = gyro_3d_android_channels[i].scan_type;
		indio_dev->num_channels = ARRAY_SIZE(gyro_3d_android_channels);
		indio_dev->name = "cros-ec-gyro";
		indio_dev->label = android_label;
		indio_dev->available_scan_masks = hid_sensor_android_scan_masks;
		if (android_hyst0 && gyro_state->common_attributes.sensitivity.index >= 0)
			hid_sensor_write_raw_hyst_value(&gyro_state->common_attributes, 0, 0);
		dev_info(&pdev->dev, "Android mode: cros-ec-gyro (%s), HID scale %d.%09d\\n",
			 android_label, gyro_state->scale_pre_decml,
			 gyro_state->scale_post_decml);
	}
''')

open(p, "w", newline="\n").write(s)
print("patched", p)

#!/usr/bin/env python3
# One-off: switch patch_accel.py / patch_gyro.py from android_negate to the android_axes mapping.
import sys


def edit(path, pairs):
    s = open(path, newline="\n").read()
    for old, new in pairs:
        assert s.count(old) == 1, (path, old[:80], s.count(old))
        s = s.replace(old, new)
    open(path, "w", newline="\n").write(s)
    print("updated", path)


d = sys.argv[1]

edit(d + "/patch_accel.py", [
    ("""    'static bool android_negate = true;\\n'
    'module_param(android_negate, bool, 0644);\\n'
    'MODULE_PARM_DESC(android_negate, "Android mode: negate all axes (HID/Windows gravity sign -> Android, default: 1)");\\n'
""", """    'static u32 android_axes = HID_SENSOR_ANDROID_AXES(0, 1, 2, 1, 1, 1);\\n'
    'HID_SENSOR_ANDROID_AXES_PARAM(android_axes, "Android mode: Android x,y,z from HID axes, - negates (default: -x,-y,-z = HID/Windows gravity sign -> Android)");\\n'
"""),
    ("""static s16 accel_3d_android_value(struct accel_3d_state *st, s32 raw)
{
	return hid_sensor_android_to_s16(raw, st->scale_pre_decml, st->scale_post_decml,
					 ACCEL_3D_ANDROID_LSB_NANO, READ_ONCE(android_negate));
}""", """static s16 accel_3d_android_value(struct accel_3d_state *st, s32 raw, bool negate)
{
	return hid_sensor_android_to_s16(raw, st->scale_pre_decml, st->scale_post_decml,
					 ACCEL_3D_ANDROID_LSB_NANO, negate);
}"""),
    # read_raw: map the Android axis to its HID source axis
    ("""rep('''		if (report_id >= 0)
			*val = sensor_hub_input_attr_get_raw_value(""", """rep('''	s32 min;
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
			*val = sensor_hub_input_attr_get_raw_value("""),
    ("""			if (accel_state->android)
				*val = accel_3d_android_value(accel_state, *val);""",
     """			if (accel_state->android)
				*val = accel_3d_android_value(accel_state, *val,
					HID_SENSOR_ANDROID_AXIS_NEG(axes, chan->scan_index));"""),
    ("""		struct hid_sensor_android_scan *scan = &accel_state->android_scan;
		int i;

		if (atomic_read(&accel_state->common_attributes.data_ready)) {
			for (i = 0; i < ACCEL_3D_CHANNEL_MAX; i++)
				scan->val[i] = accel_3d_android_value(accel_state,
								      accel_state->android_raw[i]);""",
     """		struct hid_sensor_android_scan *scan = &accel_state->android_scan;
		u32 axes = READ_ONCE(android_axes);
		int i;

		if (atomic_read(&accel_state->common_attributes.data_ready)) {
			for (i = 0; i < ACCEL_3D_CHANNEL_MAX; i++)
				scan->val[i] = accel_3d_android_value(accel_state,
					accel_state->android_raw[HID_SENSOR_ANDROID_AXIS_SRC(axes, i)],
					HID_SENSOR_ANDROID_AXIS_NEG(axes, i));"""),
])

edit(d + "/patch_gyro.py", [
    ("""    'static bool android_negate;\\n'
    'module_param(android_negate, bool, 0644);\\n'
    'MODULE_PARM_DESC(android_negate, "Android mode: negate all axes (default: 0, HID and Android both use the right-hand rule)");\\n'
""", """    'static u32 android_axes = HID_SENSOR_ANDROID_AXES(0, 1, 2, 0, 0, 0);\\n'
    'HID_SENSOR_ANDROID_AXES_PARAM(android_axes, "Android mode: Android x,y,z from HID axes, - negates (default: x,y,z, HID and Android both use the right-hand rule)");\\n'
"""),
    ("""static s16 gyro_3d_android_value(struct gyro_3d_state *st, s32 raw)
{
	return hid_sensor_android_to_s16(raw, st->scale_pre_decml, st->scale_post_decml,
					 GYRO_3D_ANDROID_LSB_NANO, READ_ONCE(android_negate));
}""", """static s16 gyro_3d_android_value(struct gyro_3d_state *st, s32 raw, bool negate)
{
	return hid_sensor_android_to_s16(raw, st->scale_pre_decml, st->scale_post_decml,
					 GYRO_3D_ANDROID_LSB_NANO, negate);
}"""),
    ("""rep('''		if (report_id >= 0)
			*val = sensor_hub_input_attr_get_raw_value(""", """rep('''	u32 address;
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
			*val = sensor_hub_input_attr_get_raw_value("""),
    ("""			if (gyro_state->android)
				*val = gyro_3d_android_value(gyro_state, *val);""",
     """			if (gyro_state->android)
				*val = gyro_3d_android_value(gyro_state, *val,
					HID_SENSOR_ANDROID_AXIS_NEG(axes, chan->scan_index));"""),
    ("""		struct hid_sensor_android_scan *scan = &gyro_state->android_scan;
		int i;

		if (atomic_read(&gyro_state->common_attributes.data_ready)) {
			for (i = 0; i < GYRO_3D_CHANNEL_MAX; i++)
				scan->val[i] = gyro_3d_android_value(gyro_state,
								     gyro_state->android_raw[i]);""",
     """		struct hid_sensor_android_scan *scan = &gyro_state->android_scan;
		u32 axes = READ_ONCE(android_axes);
		int i;

		if (atomic_read(&gyro_state->common_attributes.data_ready)) {
			for (i = 0; i < GYRO_3D_CHANNEL_MAX; i++)
				scan->val[i] = gyro_3d_android_value(gyro_state,
					gyro_state->android_raw[HID_SENSOR_ANDROID_AXIS_SRC(axes, i)],
					HID_SENSOR_ANDROID_AXIS_NEG(axes, i));"""),
])

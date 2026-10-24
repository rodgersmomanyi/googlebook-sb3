// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * HID driver for the Microsoft Surface Aggregator (SAM) keyboard and touchpad
 * (Surface Book 3, Surface Laptop 3/4: HID 045E:09AE keyboard, 045E:09AF touchpad).
 *
 * Based on the linux-surface patch "HID: Add hid-surface driver to filter BTN_0
 * (FN key)" (Ciaran Coffey, linux-surface patches/6.18/0016-hid-surface.patch).
 * Changes for the Googlebook/Surface Book 3 port:
 *  - the button capability is dropped in input_mapped() (after the generic mapping
 *    has chosen the code), so the input device never advertises BTN_0. The
 *    original input_mapping() hook ran before the generic mapping, when
 *    usage->type/code are still 0, so it never matched. Android's EventHub
 *    classifies any device with a key in [BTN_MISC, BTN_MOUSE) as a gamepad.
 *  - the whole BTN_MISC block (BTN_0..BTN_9, 0x100-0x10f) is filtered: unknown
 *    1-bit vendor usages are all mapped to BTN_MISC by hid-input.
 *  - HID_QUIRK_INPUT_PER_APP is set like hid-generic does, so the input devices
 *    keep the names hid-generic gives them ("... Keyboard", "... Consumer Control",
 *    "... System Control") and existing input configuration files still match.
 *
 * The SAM firmware reports the Fn key as such a button at runtime; it can stay
 * "pressed" and flood the input stack. Fn is a hardware modifier, the EC applies
 * it itself, so nothing is lost by dropping it.
 */

#include <linux/device.h>
#include <linux/hid.h>
#include <linux/input.h>
#include <linux/module.h>

#include "hid-ids.h"

static bool surface_is_misc_button(const struct hid_usage *usage)
{
	return usage->type == EV_KEY && usage->code >= BTN_MISC &&
	       usage->code < BTN_MOUSE;
}

static int surface_input_mapped(struct hid_device *hdev, struct hid_input *hi,
				struct hid_field *field, struct hid_usage *usage,
				unsigned long **bit, int *max)
{
	/* < 0: no generic handling, i.e. the key bit is never set. */
	if (surface_is_misc_button(usage))
		return -1;

	return 0;
}

static int surface_event(struct hid_device *hdev, struct hid_field *field,
			 struct hid_usage *usage, __s32 value)
{
	/* Consumed: never forwarded to hid-input. */
	if (surface_is_misc_button(usage))
		return 1;

	return 0;
}

static int surface_probe(struct hid_device *hdev, const struct hid_device_id *id)
{
	int ret;

	/* One input device per application collection, as hid-generic does. */
	hdev->quirks |= HID_QUIRK_INPUT_PER_APP;

	ret = hid_parse(hdev);
	if (ret) {
		hid_err(hdev, "parse failed\n");
		return ret;
	}

	ret = hid_hw_start(hdev, HID_CONNECT_DEFAULT);
	if (ret) {
		hid_err(hdev, "hw start failed\n");
		return ret;
	}

	return 0;
}

static const struct hid_device_id surface_devices[] = {
	{ HID_DEVICE(BUS_HOST, HID_GROUP_GENERIC,
		     USB_VENDOR_ID_MICROSOFT, 0x09AE) },	/* Surface keyboard */
	{ HID_DEVICE(BUS_HOST, HID_GROUP_GENERIC,
		     USB_VENDOR_ID_MICROSOFT, 0x09AF) },	/* Surface touchpad (non-MT fallback) */
	{ }
};
MODULE_DEVICE_TABLE(hid, surface_devices);

static struct hid_driver surface_driver = {
	.name = "surface",
	.id_table = surface_devices,
	.probe = surface_probe,
	.input_mapped = surface_input_mapped,
	.event = surface_event,
};
module_hid_driver(surface_driver);

MODULE_AUTHOR("Linux Surface Project");
MODULE_DESCRIPTION("Microsoft Surface (SAM) HID keyboard/touchpad driver: drops the bogus Fn button");
MODULE_LICENSE("GPL");

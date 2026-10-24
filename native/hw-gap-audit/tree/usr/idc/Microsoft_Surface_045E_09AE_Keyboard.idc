# Surface Book 3 keyboard base: Surface Aggregator (SAM) HID 045E:09AE via surface_hid (bus 0x0019 BUS_HOST).
# Kernel input name: "Microsoft Surface 045E:09AE Keyboard" (surface_hid_core.c names the HID device
# "Microsoft Surface %04X:%04X"; hid-generic / hid-surface split it per application and append " Keyboard").
# Matched by canonical device name on purpose: a Vendor_045e_Product_09ae.idc would also apply to the
# "... Consumer Control" and "... System Control" siblings (same vendor/product), and the first keyboard-class
# device opened would then become the built-in keyboard (id 0).
# Installed at /odm/usr/idc (searched before /vendor/usr/idc by libinput InputDevice.cpp).

# Built into the device: never treated as an external keyboard (BUS_HOST is already internal; this makes it explicit
# so the base keeps working as the internal keyboard after a detach/re-attach).
device.internal = 1

# The default built-in keyboard (InputDevice id 0).
keyboard.builtIn = 1

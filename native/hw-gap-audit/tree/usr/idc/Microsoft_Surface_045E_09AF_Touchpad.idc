# Surface Book 3 precision touchpad: Surface Aggregator (SAM) HID 045E:09AF via surface_hid, bound by hid-multitouch
# (Windows Precision Touchpad, HID_GROUP_MULTITOUCH_WIN_8). Kernel input name: "Microsoft Surface 045E:09AF Touchpad".
# Its legacy mouse collection becomes a separate "... Mouse" input device that stays silent once hid-multitouch has
# switched the touchpad to PTP input mode; it is deliberately not matched here.
# Installed at /odm/usr/idc (searched before /vendor/usr/idc).

# Internal: avoid aggressive wake and keep it listed as a built-in pointing device.
device.internal = 1

# Use Android's touchpad path (TouchpadInputMapper + gestures library: tap-to-click, two-finger scroll,
# three/four-finger gestures). Palm rejection uses the PTP confidence bit, which hid-multitouch reports as
# MT_TOOL_PALM.
touch.deviceType = touchPad

# No pressure/size calibration on purpose: the lapis Elan/Synaptics IDCs tune pressure for touchpads that report
# ABS_MT_PRESSURE; a Windows PTP usually reports none. Tune gestureProp.* only after reading getevent -lp output
# from the real device.

# Android holds motion for gestureProp.Change_Timeout (200 ms default) after touchdown, so quick flicks never move the
# cursor. gbos-x86 issue #17 confirmed 0.02 fixes it on lapis (Android 17). Credit: github.com/linuxkid473/gbos-x86 #17.
# Only this property: the reporter found Drumroll_Suppression_Enable unnecessary, so it is not set (wave 3, item 9).
gestureProp.Change_Timeout = 0.02

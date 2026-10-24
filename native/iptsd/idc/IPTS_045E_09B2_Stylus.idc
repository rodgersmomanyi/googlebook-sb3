# Surface Book 3 13.5": the kernel's own input device for the "Stylus" application collection of the ipts HID
# device "IPTS 045E:09B2" (hid-generic, one input device per application). It carries the touch firmware's
# single-touch / native reports, i.e. basic touch while iptsd is not running; it is silent while iptsd keeps the
# firmware in multitouch mode. hid-input sets no INPUT_PROP_DIRECT here, so without this file Android would
# classify it as a pointer device. gb_iptsd.sh generates the same for any other name it finds (/odm/usr/idc).
device.internal = 1
touch.deviceType = touchScreen
touch.orientationAware = 1

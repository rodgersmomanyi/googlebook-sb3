# Surface Book 3 13.5" touchscreen: IPTS 045E:09B2 processed by iptsd, uinput "IPTSD Virtual Touchscreen 045E:09B2".
# Found by device name. Do NOT add a Vendor_045e_Product_09b2.idc: Android prefers vendor/product files over
# name files, and iptsd's stylus and the kernel's own IPTS input device share that vendor/product id.
device.internal = 1
touch.deviceType = touchScreen
touch.orientationAware = 1
# iptsd reports ABS_MT_ORIENTATION as 0..180 degrees, not the symmetric range Android interpolates.
touch.orientation.calibration = none

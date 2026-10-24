#!/usr/bin/env python3
"""Make Ruslan Bay's IPU4/IPU4P series (linux-surface v6.18 'ipu4p' branch) build on the
android17-6.18 GKI tree WITHOUT changing any core V4L2/vb2 header or v4l2-ioctl.c.

Patch 0003 of the series extends core structs (v4l2_mbus_frame_desc_entry.bpp/two_dim,
v4l2_subdev_pad_ops.get_routing, vb2_v4l2_buffer.reserved) and uapi headers. Changing those
would alter the GKI KMI (struct layouts / CRCs of exported V4L2 symbols) that the lapis
vendor modules were built against, so instead:
  * the plain #defines of 0003 go into a driver-private header (ipu-v4l2-compat.h),
  * the three code paths that need the non-mainline struct members are compiled out
    (they only fire for Intel BSP 'crlmodule' sensors; mainline sensors never report a
    2D frame descriptor and have no get_routing op),
  * the 0017 v4l2-ioctl.c hunk (descriptions of BSP fourccs) is replaced by the driver
    filling f->description itself,
  * Makefiles use $(src) (correct for in-tree O=/Kleaf and for M= builds) instead of
    $(srctree)/$(src), which breaks when srcroot is absolute (Kleaf),
  * warning-suppression flags move from intel/Makefile into ipu4/Makefile,
  * Kconfig gets real dependencies.
Usage: gki_compat.py <tree-root>   (idempotent)"""
import os
import re
import sys

root = sys.argv[1] if len(sys.argv) > 1 else "."
D = os.path.join(root, "drivers/media/pci/intel")


def rd(p):
    with open(p) as f:
        return f.read()


def wr(p, s):
    with open(p, "w") as f:
        f.write(s)


def sub1(path, old, new, count=1):
    s = rd(path)
    if new in s and old not in s:
        return
    n = s.count(old)
    assert n == count, (path, old[:70], n)
    wr(path, s.replace(old, new))


COMPAT = r'''/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Definitions that Intel's BSP kernels (and patch 0003 of the IPU4 series) add to core
 * V4L2 headers.  Kept private to the IPU4 driver so that the GKI kernel's core headers,
 * struct layouts and KMI stay untouched.
 */
#ifndef IPU_V4L2_COMPAT_H
#define IPU_V4L2_COMPAT_H

#include <linux/types.h>
#include <linux/videodev2.h>
#include <linux/v4l2-subdev.h>
#include <linux/media.h>
#include <linux/v4l2-controls.h>

#ifndef V4L2_SUBDEV_FL_HAS_SUBSTREAMS
#define V4L2_SUBDEV_FL_HAS_SUBSTREAMS		(1U << 4)
#endif
#ifndef MEDIA_PAD_FL_MULTIPLEX
#define MEDIA_PAD_FL_MULTIPLEX			(1U << 3)
#endif
#ifndef V4L2_CID_MIPI_LANES
#define V4L2_CID_MIPI_LANES			(V4L2_CID_IMAGE_SOURCE_CLASS_BASE + 64)
#endif
#ifndef V4L2_SUBDEV_FLAG_NEXT_STREAM
#define V4L2_SUBDEV_FLAG_NEXT_STREAM		0x80000000
#endif
#ifndef V4L2_SUBDEV_ROUTE_FL_IMMUTABLE
#define V4L2_SUBDEV_ROUTE_FL_IMMUTABLE		(1U << 1)
#endif
#ifndef V4L2_SUBDEV_ROUTE_FL_SOURCE
#define V4L2_SUBDEV_ROUTE_FL_SOURCE		(1U << 2)
#endif

/* Raw bayer vector formats (IPU4 BSP fourccs, not in mainline). */
#ifndef V4L2_PIX_FMT_SBGGR8_16V32
#define V4L2_PIX_FMT_SBGGR8_16V32 v4l2_fourcc('b', 'V', '0', 'A')
#define V4L2_PIX_FMT_SGBRG8_16V32 v4l2_fourcc('b', 'V', '0', 'B')
#define V4L2_PIX_FMT_SGRBG8_16V32 v4l2_fourcc('b', 'V', '0', 'C')
#define V4L2_PIX_FMT_SRGGB8_16V32 v4l2_fourcc('b', 'V', '0', 'D')
#endif
#ifndef V4L2_PIX_FMT_SBGGR8V32
#define V4L2_PIX_FMT_SBGGR8V32  v4l2_fourcc('b', 'V', '0', 'A')
#define V4L2_PIX_FMT_SGBRG8V32  v4l2_fourcc('b', 'V', '0', 'B')
#define V4L2_PIX_FMT_SGRBG8V32  v4l2_fourcc('b', 'V', '0', 'C')
#define V4L2_PIX_FMT_SRGGB8V32  v4l2_fourcc('b', 'V', '0', 'D')
#endif
#ifndef V4L2_PIX_FMT_SBGGR10V32
#define V4L2_PIX_FMT_SBGGR10V32 v4l2_fourcc('b', 'V', '0', 'E')
#define V4L2_PIX_FMT_SGBRG10V32 v4l2_fourcc('b', 'V', '0', 'F')
#define V4L2_PIX_FMT_SGRBG10V32 v4l2_fourcc('b', 'V', '0', 'G')
#define V4L2_PIX_FMT_SRGGB10V32 v4l2_fourcc('b', 'V', '0', 'H')
#endif
#ifndef V4L2_PIX_FMT_SBGGR12V32
#define V4L2_PIX_FMT_SBGGR12V32 v4l2_fourcc('b', 'V', '0', 'I')
#define V4L2_PIX_FMT_SGBRG12V32 v4l2_fourcc('b', 'V', '0', 'J')
#define V4L2_PIX_FMT_SGRBG12V32 v4l2_fourcc('b', 'V', '0', 'K')
#define V4L2_PIX_FMT_SRGGB12V32 v4l2_fourcc('b', 'V', '0', 'L')
#endif
#ifndef V4L2_PIX_FMT_SBGGR14V32
#define V4L2_PIX_FMT_SBGGR14V32 v4l2_fourcc('b', 'V', '0', 'M')
#define V4L2_PIX_FMT_SGBRG14V32 v4l2_fourcc('b', 'V', '0', 'N')
#define V4L2_PIX_FMT_SGRBG14V32 v4l2_fourcc('b', 'V', '0', 'O')
#define V4L2_PIX_FMT_SRGGB14V32 v4l2_fourcc('b', 'V', '0', 'P')
#endif
#ifndef V4L2_FMT_IPU_ISA_CFG
#define V4L2_FMT_IPU_ISA_CFG   v4l2_fourcc('i', 'p', '4', 'c')
#define V4L2_FMT_IPU_ISYS_META v4l2_fourcc('i', 'p', '4', 'm')
#endif
#ifndef V4L2_PIX_FMT_UYVY_V32
#define V4L2_PIX_FMT_UYVY_V32    v4l2_fourcc('y', 'V', '3', '2')
#define V4L2_PIX_FMT_YUYV420_V32 v4l2_fourcc('y', '0', '3', '2')
#endif

/*
 * Description for the fourccs above, filled in by the driver's ENUM_FMT so that
 * v4l_fill_fmtdesc() does not WARN about unknown pixel formats (replaces the
 * v4l2-ioctl.c hunk of the series).
 */
static inline const char *ipu_compat_fmt_desc(__u32 pixelformat)
{
	switch (pixelformat) {
	case V4L2_PIX_FMT_SBGGR8V32: return "8-bit Bayer BGBG/GRGR (V32)";
	case V4L2_PIX_FMT_SGBRG8V32: return "8-bit Bayer GBGB/RGRG (V32)";
	case V4L2_PIX_FMT_SGRBG8V32: return "8-bit Bayer GRGR/BGBG (V32)";
	case V4L2_PIX_FMT_SRGGB8V32: return "8-bit Bayer RGRG/GBGB (V32)";
	case V4L2_PIX_FMT_SBGGR10V32: return "10-bit Bayer BGBG/GRGR (V32)";
	case V4L2_PIX_FMT_SGBRG10V32: return "10-bit Bayer GBGB/RGRG (V32)";
	case V4L2_PIX_FMT_SGRBG10V32: return "10-bit Bayer GRGR/BGBG (V32)";
	case V4L2_PIX_FMT_SRGGB10V32: return "10-bit Bayer RGRG/GBGB (V32)";
	case V4L2_PIX_FMT_SBGGR12V32: return "12-bit Bayer BGBG/GRGR (V32)";
	case V4L2_PIX_FMT_SGBRG12V32: return "12-bit Bayer GBGB/RGRG (V32)";
	case V4L2_PIX_FMT_SGRBG12V32: return "12-bit Bayer GRGR/BGBG (V32)";
	case V4L2_PIX_FMT_SRGGB12V32: return "12-bit Bayer RGRG/GBGB (V32)";
	case V4L2_PIX_FMT_SBGGR14V32: return "14-bit Bayer BGBG/GRGR (V32)";
	case V4L2_PIX_FMT_SGBRG14V32: return "14-bit Bayer GBGB/RGRG (V32)";
	case V4L2_PIX_FMT_SGRBG14V32: return "14-bit Bayer GRGR/BGBG (V32)";
	case V4L2_PIX_FMT_SRGGB14V32: return "14-bit Bayer RGRG/GBGB (V32)";
	case V4L2_FMT_IPU_ISA_CFG: return "IPU4 ISA config";
	case V4L2_FMT_IPU_ISYS_META: return "IPU4 ISYS metadata";
	case V4L2_PIX_FMT_UYVY_V32: return "UYVY (V32)";
	case V4L2_PIX_FMT_YUYV420_V32: return "YUYV 4:2:0 (V32)";
	default: return NULL;
	}
}

#endif /* IPU_V4L2_COMPAT_H */
'''

WARN_FLAGS = """# private replacements for the BSP-only core V4L2 header additions
ccflags-y += -include $(src)/../ipu-v4l2-compat.h
# warnings the BSP code triggers (moved here from intel/Makefile so they do not
# leak into ipu3/ipu6/ivsc)
ccflags-y += -Wno-error=uninitialized
ccflags-y += $(call cc-disable-warning, unused-parameter)
ccflags-y += $(call cc-disable-warning, implicit-fallthrough)
ccflags-y += $(call cc-disable-warning, missing-field-initializers)
ccflags-y += $(call cc-disable-warning, int-conversion)
ccflags-y += $(call cc-disable-warning, enum-conversion)
ccflags-y += $(call cc-disable-warning, uninitialized)
subdir-ccflags-y += $(call cc-disable-warning, unused-parameter)
subdir-ccflags-y += $(call cc-disable-warning, implicit-fallthrough)
subdir-ccflags-y += $(call cc-disable-warning, missing-field-initializers)
subdir-ccflags-y += $(call cc-disable-warning, int-conversion)
subdir-ccflags-y += $(call cc-disable-warning, enum-conversion)
subdir-ccflags-y += $(call cc-disable-warning, uninitialized)
subdir-ccflags-$(CONFIG_VIDEO_INTEL_IPU_WERROR) += -Werror

"""


def main():
    wr(os.path.join(D, "ipu-v4l2-compat.h"), COMPAT)

    # frame descriptor 2D extension + get_routing in ipu-isys-csi2.c
    p = os.path.join(D, "ipu-isys-csi2.c")
    s = rd(p)
    if "IPU_HAVE_FD_2D" not in s:
        reps = [
            ('''	if (!rval) {
		cfg->input_pins[inpin].dt = IPU_ISYS_MIPI_CSI2_TYPE_EMBEDDED8;
		cfg->input_pins[inpin].input_res.width =
		    entry.two_dim.width * entry.bpp / BITS_PER_BYTE;
		cfg->input_pins[inpin].input_res.height =
		    entry.two_dim.height;
	}''', '''#ifdef IPU_HAVE_FD_2D	/* BSP-only v4l2_mbus_frame_desc_entry.two_dim/bpp */
	if (!rval) {
		cfg->input_pins[inpin].dt = IPU_ISYS_MIPI_CSI2_TYPE_EMBEDDED8;
		cfg->input_pins[inpin].input_res.width =
		    entry.two_dim.width * entry.bpp / BITS_PER_BYTE;
		cfg->input_pins[inpin].input_res.height =
		    entry.two_dim.height;
	}
#else
	(void)rval;
#endif'''),
            ('''	if (!rval) {
		fmt->format.width =
		    entry.two_dim.width * entry.bpp / BITS_PER_BYTE;
		fmt->format.height = entry.two_dim.height;
		fmt->format.code = entry.pixelcode;
		fmt->format.field = V4L2_FIELD_NONE;
	}
	return rval;''', '''#ifdef IPU_HAVE_FD_2D
	if (!rval) {
		fmt->format.width =
		    entry.two_dim.width * entry.bpp / BITS_PER_BYTE;
		fmt->format.height = entry.two_dim.height;
		fmt->format.code = entry.pixelcode;
		fmt->format.field = V4L2_FIELD_NONE;
	}
	return rval;
#else
	/* mainline sensors never describe embedded data as a 2D frame */
	return rval ? rval : -ENOIOCTLCMD;
#endif'''),
            ('''		if (!rval) {
			ffmt->width = entry.two_dim.width * entry.bpp
			    / BITS_PER_BYTE;
			ffmt->height = entry.two_dim.height;
			ffmt->code = entry.pixelcode;
			ffmt->field = V4L2_FIELD_NONE;
		}''', '''#ifdef IPU_HAVE_FD_2D
		if (!rval) {
			ffmt->width = entry.two_dim.width * entry.bpp
			    / BITS_PER_BYTE;
			ffmt->height = entry.two_dim.height;
			ffmt->code = entry.pixelcode;
			ffmt->field = V4L2_FIELD_NONE;
		}
#else
		(void)rval;
#endif'''),
            ('''	rval =
	    v4l2_subdev_call(media_entity_to_v4l2_subdev(link->source->entity),
			     pad, get_routing, &routing);
''', '''#ifdef IPU_HAVE_GET_ROUTING	/* BSP-only v4l2_subdev_pad_ops.get_routing */
	rval =
	    v4l2_subdev_call(media_entity_to_v4l2_subdev(link->source->entity),
			     pad, get_routing, &routing);
#else
	rval = -ENOIOCTLCMD;	/* mainline sensors: single stream */
#endif
'''),
            ('''	.set_routing = ipu_isys_subdev_set_routing,
	.get_routing = ipu_isys_subdev_get_routing,
};''', '''	.set_routing = ipu_isys_subdev_set_routing,
#ifdef IPU_HAVE_GET_ROUTING
	.get_routing = ipu_isys_subdev_get_routing,
#endif
};'''),
        ]
        for old, new in reps:
            assert s.count(old) == 1, ("csi2", old[:70], s.count(old))
            s = s.replace(old, new)
        wr(p, s)

    sub1(os.path.join(D, "ipu-isys-csi2-be-soc.c"), '''	.set_routing = ipu_isys_subdev_set_routing,
	.get_routing = ipu_isys_subdev_get_routing,
};''', '''	.set_routing = ipu_isys_subdev_set_routing,
#ifdef IPU_HAVE_GET_ROUTING
	.get_routing = ipu_isys_subdev_get_routing,
#endif
};''')

    # vb2_v4l2_buffer.reserved (BSP-only member)
    sub1(os.path.join(D, "ipu-isys-queue.c"), '''		buf->reserved &= 0xFFFFFF00;
		if (ip->csi2)
			buf->reserved |= ip->csi2->index << 4;
		buf->reserved |= ip->vc;
''', '''#ifdef IPU_HAVE_VB2_RESERVED	/* BSP-only vb2_v4l2_buffer.reserved */
		buf->reserved &= 0xFFFFFF00;
		if (ip->csi2)
			buf->reserved |= ip->csi2->index << 4;
		buf->reserved |= ip->vc;
#endif
''')

    # ENUM_FMT: describe BSP fourccs in the driver
    sub1(os.path.join(D, "ipu-isys-video.c"), '''		f->flags = 0;
		f->pixelformat = pfmt->pixelformat;

		return 0;''', '''		f->flags = 0;
		f->pixelformat = pfmt->pixelformat;
		if (ipu_compat_fmt_desc(pfmt->pixelformat))
			strscpy(f->description,
				ipu_compat_fmt_desc(pfmt->pixelformat),
				sizeof(f->description));

		return 0;''')

    # Makefiles: $(srctree)/$(src) -> $(src)
    for dp, _dn, fn in os.walk(os.path.join(D, "ipu4")):
        for f in fn:
            if not f.startswith("Makefile"):
                continue
            p = os.path.join(dp, f)
            s = o = rd(p)
            s = re.sub(r'ifneq \(\$\(EXTERNAL_BUILD\), ?1\)\nsrcpath := \$\(srctree\)\nendif\n', '', s)
            s = re.sub(r'(?m)^srcpath := \$\(srctree\)\n', '', s)
            s = s.replace("$(srcpath)/$(src)", "$(src)")
            if s != o:
                wr(p, s)
    p = os.path.join(D, "ipu4/Makefile")
    s = rd(p)
    if "ipu-v4l2-compat.h" not in s:
        anchor = "# ignore IPU FW Lib marco redefined warning if using clang"
        assert s.count(anchor) == 1
        s = s.replace(anchor, WARN_FLAGS + anchor)
        wr(p, s)
    p = os.path.join(D, "Makefile")
    s = rd(p)
    blk = re.search(r"# force check the compile warning.*?subdir-ccflags-\$\(CONFIG_VIDEO_INTEL_IPU_WERROR\) \+= -Werror\n\n?", s, re.S)
    if blk:
        s = s.replace(blk.group(0), "")
    s = s.replace("obj-$(CONFIG_VIDEO_INTEL_IPU4)  += ipu4/\nobj-$(CONFIG_VIDEO_INTEL_IPU4P)\t+= ipu4/\n",
                  "obj-$(CONFIG_VIDEO_INTEL_IPU)\t+= ipu4/\n")
    wr(p, s)

    # Kconfig
    sub1(os.path.join(D, "Kconfig"), '''config VIDEO_INTEL_IPU
	tristate "Intel IPU driver"
	depends on ACPI
	select IOMMU_API
	select IOMMU_IOVA
	select X86_DEV_DMA_OPS if X86
	select VIDEOBUF2_DMA_CONTIG
	select PHYS_ADDR_T_64BIT
	select COMMON_CLK
	help
	  Main driver for Intel Image Processing Unit. Say Y here.''', '''config VIDEO_INTEL_IPU
	tristate "Intel IPU4/IPU4P driver (Apollo Lake / Ice Lake)"
	depends on ACPI && PCI && X86_64
	depends on VIDEO_DEV && MEDIA_CONTROLLER
	depends on IPU_BRIDGE || !IPU_BRIDGE
	select IOMMU_API
	select IOMMU_IOVA
	select VIDEOBUF2_DMA_CONTIG
	select VIDEO_V4L2_SUBDEV_API
	select V4L2_FWNODE
	select COMMON_CLK
	help
	  Out-of-mainline driver for the Intel IPU4 (8086:5a88, Apollo Lake) and
	  IPU4P (8086:8a19, Ice Lake: Surface Pro 7 / Book 3 / Laptop 3) imaging
	  unit, from Intel's BSP kernel as forward-ported by Ruslan Bay.  Needs the
	  ipu4p_cpd.bin firmware.  Builds intel-ipu4p{,-isys,-psys}.ko.''')
    print("gki_compat: done")


main()

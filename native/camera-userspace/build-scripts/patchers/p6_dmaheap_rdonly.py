#!/usr/bin/env python3
# P6: open DMA heaps read-only. Android creates /dev/dma_heap/* as 0444 (system:system); the
# DMA_HEAP_IOCTL_ALLOC ioctl works on a read-only fd (that is what libdmabufheap does), but
# libcamera opened them O_RDWR and so failed, which disabled the software ISP on Android.
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from patchlib import edit

root = sys.argv[1]

edit(root, "src/libcamera/dma_buf_allocator.cpp", [
("""		int ret = ::open(info.deviceNodeName, O_RDWR | O_CLOEXEC, 0);
		if (ret < 0) {
""",
"""		/* DMA heaps only need a read-only fd (Android: 0444 nodes). */
		const int flags = info.type == DmaBufAllocator::DmaBufAllocatorFlag::UDmaBuf
					  ? O_RDWR : O_RDONLY;
		int ret = ::open(info.deviceNodeName, flags | O_CLOEXEC, 0);
		if (ret < 0) {
"""),
])
print("P6 applied")

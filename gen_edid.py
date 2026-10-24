#!/usr/bin/env python3
# Generate a valid EDID 1.3 blob (128 bytes) with a detailed timing descriptor for the preferred
# mode, so a mode-less/EDID-less virtual DRM connector (vmwgfx) gets a parseable identification
# block (fixes SurfaceFlinger getDisplayIdentificationData -8 -> stops it dropping to legacy
# multi-display mode -> framework gets a valid active display mode id instead of -1).
import os, sys

def build(hact, vact, hfp, hsync, hblank, vfp, vsync, vblank, pclk_khz, name):
    e = bytearray(128)
    e[0:8] = bytes([0x00,0xFF,0xFF,0xFF,0xFF,0xFF,0xFF,0x00])
    def pnp(s):
        v = ((ord(s[0])-64)<<10)|((ord(s[1])-64)<<5)|(ord(s[2])-64)
        return bytes([(v>>8)&0xFF, v&0xFF])
    e[8:10] = pnp("LNX")
    e[10:12] = bytes([0x00,0x00])   # product code
    e[12:16] = bytes([1,0,0,0])     # serial
    e[16] = 0; e[17] = 0x16         # week/year
    e[18] = 1; e[19] = 3            # EDID 1.3
    e[20] = 0x80                    # digital input
    e[21] = 34; e[22] = 21          # size cm (~16:10)
    e[23] = 120                     # gamma 2.2
    e[24] = 0x0A                    # RGB, preferred timing in DTD
    e[25:35] = bytes([0xEE,0x91,0xA3,0x54,0x4C,0x99,0x26,0x0F,0x50,0x54])  # chroma (generic)
    e[35] = 0x00; e[36] = 0x00; e[37] = 0x00  # no established timings
    for i in range(38, 54): e[i] = 0x01       # standard timings unused
    # Detailed Timing Descriptor #1 = preferred mode
    dtd = bytearray(18)
    pc = pclk_khz // 10
    dtd[0] = pc & 0xFF; dtd[1] = (pc>>8)&0xFF
    dtd[2] = hact & 0xFF
    dtd[3] = hblank & 0xFF
    dtd[4] = ((hact>>8)<<4) | ((hblank>>8)&0x0F)
    dtd[5] = vact & 0xFF
    dtd[6] = vblank & 0xFF
    dtd[7] = ((vact>>8)<<4) | ((vblank>>8)&0x0F)
    dtd[8] = hfp & 0xFF
    dtd[9] = hsync & 0xFF
    dtd[10] = ((vfp & 0x0F)<<4) | (vsync & 0x0F)
    dtd[11] = ((hfp>>8)<<6)|((hsync>>8)<<4)|((vfp>>4)<<2)|((vsync>>4))
    dtd[12] = 0x62   # h image size mm low (~354)
    dtd[13] = 0xDD   # v image size mm low (~221)
    dtd[14] = 0x10
    dtd[15] = 0x00; dtd[16] = 0x1E; dtd[17] = 0x00  # flags: digital separate +h +v
    e[54:72] = dtd
    # Descriptor #2: display name (0xFC)
    nm = (name + "\n").encode()[:13]
    d2 = bytearray(18); d2[3]=0xFC; d2[5:5+len(nm)] = nm
    for i in range(5+len(nm),18): d2[i]=0x20
    e[72:90] = d2
    # Descriptor #3: range limits (0xFD) 50-75Hz, 30-85kHz, max pclk
    d3 = bytearray(18); d3[3]=0xFD
    d3[5:13] = bytes([50,75,30,85,(pclk_khz//10000)+1,0x00,0x0A,0x20])
    d3[13:18] = bytes([0x20,0x20,0x20,0x20,0x20])
    e[90:108] = d3
    # Descriptor #4: unused dummy (0x10)
    d4 = bytearray(18); d4[3]=0x10
    for i in range(5,18): d4[i]=0x20
    e[108:126] = d4
    e[126] = 0
    e[127] = (256 - (sum(e[0:127]) % 256)) % 256
    assert sum(e)%256==0
    return bytes(e)

# 1280x800@60 (CVT, matches VMware SVGA native): htotal 1680, vtotal 831, pclk 83.5MHz
edid = build(hact=1280, vact=800, hfp=72, hsync=128, hblank=400,
             vfp=3, vsync=6, vblank=31, pclk_khz=83500, name="GB Virtual")
out = os.path.join(os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__)), "inject", "edid_1280x800.bin")
with open(out,"wb") as f: f.write(edid)
print("wrote", out, len(edid), "bytes, checksum OK")

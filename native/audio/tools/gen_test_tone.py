"""Generate gb_test_tone.wav: 1 s, 440 Hz sine, amplitude 8000, 48 kHz, 16-bit stereo (the gb_audiodiag.sh test tone).

Usage: python -I gen_test_tone.py [OUT]     (default: ../tree/gb_test_tone.wav next to this script)
"""
import math, os, struct, sys, wave

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tree",
                                                         "gb_test_tone.wav")
rate, freq, amp = 48000, 440.0, 8000
frames = b"".join(struct.pack("<hh", v, v) for v in (int(amp * math.sin(2 * math.pi * freq * i / rate))
                                                     for i in range(rate)))
with wave.open(out, "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(rate); w.writeframes(frames)
print("wrote", out)

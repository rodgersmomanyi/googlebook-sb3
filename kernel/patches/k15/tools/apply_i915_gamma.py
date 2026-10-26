#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Kernel build #15, wave 3 item 10: the default-off i915 display parameter gb_ignore_user_gamma.

Edits, under <common> (a v6.18 kernel tree or a copy of the three files at their paths):
  drivers/gpu/drm/i915/display/intel_display_params.h   param(int, gb_ignore_user_gamma, 0, 0400)
  drivers/gpu/drm/i915/display/intel_display_params.c   intel_display_param_named(gb_ignore_user_gamma, ...)
  drivers/gpu/drm/i915/display/intel_display.c          intel_crtc_copy_uapi_to_hw_state_nomodeset(): a set bit
                                                        (1 gamma, 2 degamma, 4 CTM) uses NULL (linear)
Default 0 = the original expressions. Refuses to run twice or on an unexpected tree.
Cause and fix from github.com/linuxkid473/gbos-x86 @f8a5c1a HANDOFF.md:112-135; code is ours.
Usage: python3 -I apply_i915_gamma.py <common>
"""
import os
import sys

D = "drivers/gpu/drm/i915/display/"
EDITS = [
    (D + "intel_display_params.h",
     "\tparam(int, enable_dmc_wl, -1, 0400) \\\n",
     "\tparam(int, enable_dmc_wl, -1, 0400) \\\n"
     "\tparam(int, gb_ignore_user_gamma, 0, 0400) \\\n"),
    (D + "intel_display_params.c",
     '\t"Enable DMC wakelock "\n'
     '\t"(-1=use per-chip default, 0=disabled, 1=enabled, 2=match any register, 3=always locked) "\n'
     '\t"Default: -1");\n',
     '\t"Enable DMC wakelock "\n'
     '\t"(-1=use per-chip default, 0=disabled, 1=enabled, 2=match any register, 3=always locked) "\n'
     '\t"Default: -1");\n'
     '\n'
     '/* Googlebook (GB): "white veil" switch, default off. Cause and fix from\n'
     ' * github.com/linuxkid473/gbos-x86 @f8a5c1a HANDOFF.md:112-135; code is ours. */\n'
     'intel_display_param_named(gb_ignore_user_gamma, int, 0400,\n'
     '\t"GB: bitmask 1=gamma 2=degamma 4=CTM -> linear (default 0)");\n'),
    (D + "intel_display.c",
     "\tstruct intel_crtc_state *crtc_state =\n"
     "\t\tintel_atomic_get_new_crtc_state(state, crtc);\n"
     "\n"
     "\tWARN_ON(intel_crtc_is_joiner_secondary(crtc_state));\n"
     "\n"
     "\tdrm_property_replace_blob(&crtc_state->hw.degamma_lut,\n"
     "\t\t\t\t  crtc_state->uapi.degamma_lut);\n"
     "\tdrm_property_replace_blob(&crtc_state->hw.gamma_lut,\n"
     "\t\t\t\t  crtc_state->uapi.gamma_lut);\n"
     "\tdrm_property_replace_blob(&crtc_state->hw.ctm,\n"
     "\t\t\t\t  crtc_state->uapi.ctm);\n",
     "\tstruct intel_display *display = to_intel_display(state);\n"
     "\tstruct intel_crtc_state *crtc_state =\n"
     "\t\tintel_atomic_get_new_crtc_state(state, crtc);\n"
     "\t/* GB: i915.gb_ignore_user_gamma (default 0): a set bit uses linear (NULL) */\n"
     "\tint ig = display->params.gb_ignore_user_gamma;\n"
     "\n"
     "\tWARN_ON(intel_crtc_is_joiner_secondary(crtc_state));\n"
     "\n"
     "\tdrm_property_replace_blob(&crtc_state->hw.degamma_lut,\n"
     "\t\t\t\t  unlikely(ig & BIT(1)) ? NULL : crtc_state->uapi.degamma_lut);\n"
     "\tdrm_property_replace_blob(&crtc_state->hw.gamma_lut,\n"
     "\t\t\t\t  unlikely(ig & BIT(0)) ? NULL : crtc_state->uapi.gamma_lut);\n"
     "\tdrm_property_replace_blob(&crtc_state->hw.ctm,\n"
     "\t\t\t\t  unlikely(ig & BIT(2)) ? NULL : crtc_state->uapi.ctm);\n"),
]


def main(root):
    for rel, old, new in EDITS:
        p = os.path.join(root, rel)
        t = open(p, encoding="utf-8").read()
        if "gb_ignore_user_gamma" in t:
            raise SystemExit("ERROR: %s already carries gb_ignore_user_gamma" % rel)
        if t.count(old) != 1:
            raise SystemExit("ERROR: %s: anchor found %d times (expected 1)" % (rel, t.count(old)))
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(t.replace(old, new, 1))
        print("edited", rel)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

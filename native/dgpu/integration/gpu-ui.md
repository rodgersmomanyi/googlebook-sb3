# gpu-ui (component 6)

The integration notes for component `gpu-ui` (the GbGpu app) are in [ux-app.md](ux-app.md). That is the file name
the Wave 1 task asked for; this file exists because INTERFACES 1.1 and the Wave 2 checklist (`README.md`) refer to
`integration/gpu-ui.md`.

**v1.2 (wave 1b, graphics switching):** see [ux-app.md section 8](ux-app.md) for the 1b artifacts (GbGpu.apk sha256
`87a71359...`, `gpu_ui.json` with the `gfx_app_prefs.default` seed), the tests (UI-1B-01..13, all PASS, incl. the
mock == real gb_power v1.2 cross-check), the deviations UI-1B-D1..D13, the spec questions and the Wave 2 edits
(W2G-05 and the verify_image items).

**v1.2 r2 (review fix pass, INTERFACES 29):** see [ux-app.md section 9](ux-app.md): GbGpu.apk sha256 `d653940a...`
(versionCode 29882330; R-16 detach-mode refusals, the R-6 mock rule); all gpu-ui tests PASS again on the r2 code.

# Preview latency and Gtuner CV Python (2026102106)

The preview now reads the latest shared frame, converts BGR to RGB, and resizes on a dedicated worker. The Tk thread only uploads the prepared image. The worker holds one prepared image, so slow rendering skips older frames rather than building a queue. Tk worker-message processing is bounded to 64 messages per poll to avoid unbounded log bursts starving input/render events.

The display separates frame age, preparation time, and Tk image upload time. AI inference remains its own metric. On preview closure, preview_timing.json records p50, p95 and maximum for the latest 600 displayed samples. Frame age starts after OpenCV returns a decoded frame; it excludes the capture card's internal buffering, HDMI latency, and monitor scanout. It is not full physical input-to-screen latency.

A 60 FPS source has a 16.67 ms frame period; 120 FPS has 8.33 ms. A 2–3 ms processing-stage budget does not mean every new game frame arrives within 3 ms. There is no measured speedup claim for the user's hardware and no model accuracy change in this update. If inference is the 80 ms stage, preview work will not make inference itself 3 ms. Compare 640 vs 1280 on the same saved validation clips before selecting a faster resolution; do not silently change the user's model input.

## Gtuner environment path

The app CUDA environment and Gtuner's embedded Python/OpenCV host are separate. Our gtuner_meter.py imports standard-library modules and Gtuner's own gtuner module; it does not load Torch or perform GPU inference. It forwards app telemetry to the Titan script via Gtuner CV. Running it with ordinary Python is not a replacement for Gtuner's gtuner module or USB host.

In Titan Two → Setup & runtime:

1. Browse to the Python executable from the installation selected for Gtuner CV, then click Check CV Python.
2. The bounded check reports that exact interpreter's version/architecture, base-installation DLLs, OpenCV import and extension paths. Results are saved in test_results/gtuner_python_check.json. A venv executable alone does not establish that Gtuner can locate its base DLL and dependencies.
3. In Gtuner Preferences → Computer Vision, choose that installation's folder and confirm Gtuner's own Python library and OpenCV checks. Match Python architecture to Gtuner. Do not assume the app's modern CUDA Python is compatible simply because Torch works.
4. Load the exported gtuner_meter.py in Gtuner CV with Null Video Input and actually start it. Keep the capture card in this app.
5. Compile/install the matching titan_meter.gpc in a chosen slot. Connect/Reconnect from the app; inspect worker acknowledgments, round-trip time, and slot/payload confirmation. Console output remains a separate physical test.

Sources checked October 21, 2026:

- Official ConsoleTuner downloads/changelog: https://www.consoletuner.com/titan-two-downloads/ — describes the CV Python executable-library/OpenCV integration, Null input, gcv_ready/gcv_read and gtuner.gpc_string support.
- Open report in the official repository, September 21, 2026: https://github.com/J2Kbr/GtunerIV/issues/441 — user reports Python imports working while Gtuner OpenCV discovery fails across environments. This is an unresolved report, not evidence of the same fault on this PC, and not a verified fix or version compatibility table.

Local validation: 115 unit tests, 114 passed and one Windows-only skip; package build and Python syntax checks passed. Native Windows preview, actual Gtuner embedded loading, Titan USB round trip and console output remain unverified here.

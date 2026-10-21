# Modular desktop runtime — 2026102107

## What the reference shows

The supplied October 21 Helios screenshot shows Processing: 0 FPS and a worker startup error loading `scripts/_2k_Vision/bin/ch.dll` or a dependency. It also labels the Titan PROG host as Helios. These observations do not establish that this app's CUDA detector ran at 80 ms. Our app does not use that DLL. Replacing it with an arbitrary downloaded DLL would not be a verified repair of that third-party script.

The updated workspace uses charcoal panels, cyan accents, capture/model controls on the left, the console view in the center, and the Titan connection/slots on the right. Unsupported firmware, console-port telemetry, and direct slot flashing are not displayed as supported controls. Slot labels still come from Gtuner accessibility; they are not guessed.

## Independent lifetimes

- **Capture process:** owns the card and publishes shared source/display frames. It does not import Torch or require a model or Titan.
- **Detection engine process:** loads the selected model weight in the selected CUDA/CPU environment, reads shared frames, tracks shots, and publishes observations. A weight is a selectable data artifact; it is not a USB bridge or a separate executable.
- **Preview worker:** prepares the newest frame outside Tk, independent of inference and bridge acknowledgment latency.
- **Bridge host process:** runs the authenticated loopback API and UDP link to Gtuner with no OpenCV, Torch or model loading. A supervisor thread handles control requests and cached status outside Tk. The host exits when the app's lease disappears or becomes stale.
- **Gtuner CV worker and Titan GPC:** remain the supported device backend. The app cannot directly program slots or confirm console authentication with this backend.

The detection engine's bridge client uses one latest-state slot and performs HTTP on a worker thread. It never waits for a bridge HTTP response inside inference. Restarting the bridge changes its private connection descriptor atomically; the running engine reloads it. The engine stays attached while bridge output is disabled, allowing a later connection without an AI restart. Slot requirements live in the bridge host rather than a frozen engine launch argument.

Bridge verification still requires a fresh payload echo, the expected active slot and a live heartbeat. Disabled, stale, absent, predicted or unverified state does not arm output. A running API or a USB enumeration is not console success. Reducing the host's idle poll from 50 ms to 5 ms removes that avoidable polling delay; it does not establish a 5 ms physical controller path.

## Bridge-first physical acceptance sequence

1. Stop/disconnect the Titan bridge in Helios or another USB-host application before using this app's Gtuner backend. This app does not integrate with the Helios device backend.
2. Connect Titan PROG to the PC; verify normal-mode USB detection. Keep the trigger disarmed.
3. In Gtuner, confirm its own Python-library and OpenCV indicators. Use this app's Check CV Python on the same selected installation to collect interpreter/DLL/import evidence. CUDA belongs in the app's model environment and is not required by the forwarding Gtuner worker.
4. Export this version's matching scripts, compile/install titan_meter.gpc into an unused selected slot, and actually start gtuner_meter.py in Gtuner CV using Null Video Input. Opening the scripts is insufficient.
5. With capture and AI stopped, Connect / Reconnect. Inspect whether failure is at USB discovery, worker acknowledgments, payload echo or the selected slot. Verify the neutral software round trip before attempting any armed output.
6. Separately test physical controller passthrough to the console using the manufacturer's supported output protocol/controller-authentication setup. Console output remains unverified in this app. Do not infer successful console authentication from the bridge echo.
7. Only after those checks, start capture, then AI in dry run. Inspect the saved observations and green calibration before enabling the trigger in offline practice.

## Performance acceptance

Measure the reported stage before choosing a remedy: preview preparation, Tk upload, frame age and inference are different quantities. Frame age starts after OpenCV returns, excluding hidden device buffering. A 60 FPS source has a 16.67 ms frame period and 120 FPS has 8.33 ms. Stable zero-to-three-millisecond full video-to-controller latency is not established or promised.

No trained model or new labeled gameplay was available in this workspace, so detection accuracy, GPU speed and the user's 80 ms observation have not been reproduced. No weight, input size or precision was silently changed. Compare the existing 640/960/1280 selections against the same validation clips before adopting a faster input size.

## Validation evidence

- Unit/integration tests cover the actual bridge child process, neutral simulated payload round trip, wrong-slot rejection, process termination, owner-lease loss, blocked HTTP isolation and restarted-host descriptor reload.
- A bridge-only CLI test forbids importing OpenCV, Torch and Ultralytics.
- Capture ownership and AI-stop/preview continuity tests remain in the suite.
- A release extracted into a clean directory starts and stops the independent bridge without a model; missing Gtuner/hardware remains unverified.
- Windows smoke checks the new sidebar/slots, bridge PID separation and existing capture/AI controls. It is configured for CI but was not run natively here. No physical Titan, console, GPU benchmark or Windows visual test was available in this session.

# NBA 2K shot-meter CV pipeline

## AI uses the tested console release command (2026102210)

Automatic Timing green events now emit exactly the same three-byte RELEASE_X command as the working manual PowerShell test. The persistent Gtuner worker sends it once per fresh eligible event; no new PowerShell process is started at the green frame. Titan still forwards controller output to Xbox. The separate native green-trigger output is disabled by the worker; rhythm remains deferred.

Update the app and restart its exported `gtuner_meter.py` in Gtuner CV. Keep the existing meter GPC 3.6. Arming now checks that this updated worker is running. The display distinguishes **AI release sent** from **Bridge command 3 (X release)**; the former is a send marker, and the latter is the last command reported by the device. Neither alone proves the Xbox action or correct shot timing. The manual console test remains unchanged.

The existing fresh/observed/valid/eligible/armed checks still apply. Release takes priority over a pending neutral callback, duplicate events are suppressed, and normal telemetry cannot also invoke the old shot path. Separate software regression tests exercise the real local API/UDP path; physical console validation remains necessary.


## Release validation startup fix (2026102209)

The first publication attempt stopped in two bridge-process tests: cold Windows PowerShell/Python startup had not completed within their 3–4 second assertions. Tests now wait, with a 20-second maximum and early-exit diagnostics, for the first successful bridge API status before running the original live echo and slot checks. The separate two-second bridge verification deadline, parent-lease shutdown check and release validation gate remain intact. This is a test harness correction; the app's arming fix and runtime behavior are unchanged.

## Controller-held X override arming (2026102209)

The calibration checkbox previously changed a UI variable without applying detector settings or bridge output permission. It now invokes the same arming/disarming operation as the live controls immediately. Both views show a persistent X override status and the specific blocker (capture, stale detector, bridge stage, script capability, or settings write failure); transitions are logged. Unchecking disables output and clears pending events immediately.

A Windows USB inventory scan can report a device missing even while the existing Gtuner/Titan session remains verified. Such a scan no longer disarms or reconnects that verified session. Loss of the actual live bridge still disarms output, as do stopped/stale capture or detection. These checks are retained rather than forcing the checkbox to stay selected without a working pipeline.

For normal controller shots: Start Session, wait for live frames and the verified meter bridge, then enable **AI override of held X**. Release X once after arming, then hold X to shoot. A fresh observed green event makes Titan output X=0 while physical X remains held; the override persists until physical release. This uses the existing bridge 3.6; no GPC change is needed for this app fix. **Start AI X Shot** is the separate app-started hold test and is not required for controller-held shots.

Regression checks invoke the actual checkbox on Windows, confirm detector settings and host permission change together, preserve a verified live bridge across missing/reappearing USB scans, and retain the existing physical-X-until-release GPC behavior tests. The user's exact current shutdown cause still requires the visible status/log from their run.


## Integrate verified console output with AI X shots (2026102208)

The user confirmed the PowerShell X hold/release test operates the Xbox on October 22, 2026. This establishes the output path; it does not establish meter timing accuracy. The app now adds **Start AI X Shot** to test the complete path in practice: PowerShell requests one X hold, the GPU detector measures the meter, and a fresh observed green event releases that same held output. The 750 ms manual test remains independent; it is not used to time this AI shot. Rhythm remains deferred.

Start Session, wait for fresh capture/detection and a verified **meter bridge 3.6**, then let the meter disappear and release physical X. Click Start AI X Shot. The app arms output, waits for the Titan armed state, and sends one start command through the proven PowerShell/UDP path. It does not spawn a new PowerShell process on the green frame. The persistent detector/bridge path delivers the green event, and GPC uses the same X release operation already tested. Normal physical-X shots still use Arm Meter Shots and controller hold.

Install the exported `titan_meter.gpc` 3.6 and restart `gtuner_meter.py` after updating. The original standalone diagnostic pair intentionally cannot start an AI shot. The worker requires fresh, armed Timing telemetry and an absent meter before accepting a start. The Titan additionally requires neutral physical X, an armed link and no existing hold. Repeated clicks/commands cannot refresh a running hold. A normal neutral callback does not cancel it.

Live status distinguishes **AI X HELD**, the detector's green event ID, the last accepted **Titan action** event ID, and **AI X TIMEOUT**. A timeout releases X after 2500 ms without claiming a green event. Disarm or a 250 ms device-link timeout also releases the AI hold. Stale, predicted, ambiguous or missing meter measurements cannot manufacture a successful release event. Sending or acknowledging a start does not prove Xbox receipt or a made shot.

Validation stays separate from the runtime: tests cover measured synthetic meter frames through GreenGate and GCV encoding into the actual GPC main-body shim; physical hold and AI hold; neutral callbacks; duplicate starts; wrong modes; disarm/link loss; and no-green timeout. Windows validation checks the real PowerShell path, GUI startup and the packaged runtime. Vendor compilation and the live Xbox AI shot remain hardware checks.

Next detection milestone: record complete practice jumpshots and label meter appearance, movement, green crossing and final fill/stop. Compare observed frames, missed frames, late first detections, event age and Titan action IDs before changing weights. Collect failures across supported meter types, resolutions and gameplay/display conditions; keep evaluation clips separate from training clips. A console output test passing is not a reason to automatically promote a new model.


## PowerShell X hold and release (2026102207)

The original standalone receiver accepted only A/X taps, so a RELEASE_X command sent to it was ignored. Both the standalone **test pair 1.1** and **meter bridge 3.5** now support explicit HOLD_X and RELEASE_X commands through the existing loopback PowerShell sender. The app adds **Hold X → Release via PowerShell** with a 100–2000 ms duration (default 750 ms), plus **Release X via PowerShell** for a physical held button. A hold test needs no physical X press, model or green detection. It disarms AI output before testing.

The sender sends HOLD_X once and RELEASE_X in a finally block after the duration. Titan maintains X output at 100 across GCV callbacks until release, with a device-side 2500 ms maximum if the sender/worker stops. Release takes priority over the test cooldown, neutral callback and armed flag. It clears the synthetic hold and sets X output to zero; if physical X remains held, output stays zero until physical release. The working 100 ms A/X tap commands remain available. No rhythm inputs are produced.

After the app update, export and install the updated matching GPC **and** restart its Python GCV worker: `console_button_check.gpc` 1.1 + `console_button_check.py` for the proven standalone path, or `titan_meter.gpc` 3.5 + `gtuner_meter.py` for the integrated bridge. Old stored Titan scripts do not acquire new commands from an app update alone. Use Null Input in Gtuner and keep the app as the capture-card owner.

In practice, choose 750 ms and click Hold X → Release via PowerShell. Watch Gtuner X **output** stay at 100, then go to 0, and check Xbox starts/releases the shot. The standalone GPC logs both actions; the app cannot independently acknowledge it. Meter bridge acknowledgments report device execution only, not Xbox receipt. Once this works, use physical X hold with armed AI timing and inspect the green event separately.

Tests exercise both real UDP receivers with the timed sender, native PowerShell on Windows, both GPC main bodies in a host shim, physical hold suppression, lost-release timeout, and release priority. These checks do not replace Gtuner compilation or a physical Xbox test.


## X-only Xbox jumpshots (2026102206)

The current app uses **Timing: hold Xbox X, then release X on a fresh observed green event**. Rhythm is deferred. Bridge **3.4** never rewrites the right stick, RT or movement. After an accepted release, X output remains zero until the physical button is released; disarming or a link timeout cannot turn the held button back into a second press. Release X before arming.

The app now shows **X input: HELD/released**, independently of its output override, and offers **Release Held X — test**. This test disarms AI output and sends one manual command through PowerShell to the running meter worker. It calls the same X suppression operation as AI timing. Titan acknowledges it only when physical X is held. An acknowledgment reports device execution, not Xbox receipt.

1. Install the exported `titan_meter.gpc` **3.4** in the selected Titan slot and restart the updated `gtuner_meter` Computer Vision worker with Null Input. Stop any standalone test worker that owns port 26741. An app update cannot reprogram a stored Titan script.
2. In practice, hold X and keep holding it. Confirm **X input: HELD**, then click **Release Held X — test**. Check Gtuner output X becomes zero and whether the Xbox releases the shot. No AI or capture is needed for this test.
3. Physically release X. Start capture/detection, arm Timing, and hold X through the green. Inspect the observed green event, Titan action ID and Xbox result separately. If the manual release works but AI release does not, investigate the green event and its freshness rather than the console transport.

Software coverage exercises physical X holds, manual command delivery and acknowledgment, stale/link-loss gates, no RS override, and suppression until physical release. Native Windows tests remain separate from vendor GPC compilation and physical Xbox validation. Historical rhythm notes below describe earlier releases and do not apply to the current X-only mode.


## Green-event delivery and Titan shot gates (2026102205)

Bridge **3.3** fixes two reproduced cases where a valid detector event never started an action: the script treated each short observation TTL as a controller-link timeout, latching out held inputs between slower GCV callbacks; it also required the Titan itself to sample a pre-green meter frame, even though the detector had already verified the approach. Titan now tracks the physical hold independently, accepts only a fresh observed green token, and uses a separate **250 ms link watchdog**. Explicit disarm, R3 cancel, mode changes and link loss still require a released/neutral controller before rearming. An accepted rhythm command completes its configured delay/hold while the link remains alive; expired observations cannot start a new action.

The asynchronous detector client and bridge host now retain one valid observed green event ahead of ordinary video-state updates. Its **first capture timestamp** is preserved; repeated copies cannot refresh its deadline. The event is eligible for delivery only while at least the existing 25 ms GCV reserve remains in its configured maximum age. Disarming, changing mechanics, clearing or losing the bridge cancels pending output. A later non-green, absent or predicted frame cannot manufacture a trigger and does not erase an already observed, still-fresh event. Current video state remains latest-only. Near-deadline packets that cannot trigger no longer masquerade as an explicit user disarm while the source observation is still within its age limit.

The live status now shows **Titan action** plus the script's actual mode and gate: disarmed, link timeout, R3 held, release X/RS to reset, waiting for physical hold, waiting for green, expired green, or action started. The heartbeat has an optional eighth field carrying this gate/mode word; the 26-byte v3 command payload is unchanged. **Install the exported `titan_meter.gpc` 3.3 and restart the updated `gtuner_meter` worker.** Arming requires the new diagnostic capability so an older script cannot silently keep the old gates.

Regression tests first reproduced both failures in the actual GPC main-body host shim. Coverage now includes 35 ms observation lifetimes with roughly 70 ms GCV gaps, physical holds before the first observed callback, brief green pulses through the HTTP/UDP/GCV path, cancellation and unchanged event deadlines. Native Windows software tests are separate from the vendor GPC compiler and physical Xbox shot validation.

## Restore the original manual output path (2026102204)

The A/X diagnostic buttons now send the original `T2TEST` command even when the app's meter bridge is not ready, the API is unavailable, or there is no new Titan heartbeat/counter. The previous button incorrectly required the newer bridge's readiness before permitting a test of that bridge. This prevented using the actual standalone pair that had worked on the console.

**Open original working test pair** exports and opens the existing `console_button_check.py` **and** `console_button_check.gpc`. It disconnects automatic meter output. An original test also pauses automatic reconnect/slot selection so the meter bridge cannot take over the working test slot. Reuse the original test GPC slot and start **console_button_check** in Gtuner Computer Vision with Null Input, exactly as in the successful manual setup. Then press **Test A via PowerShell** in the app. No new meter GPC revision or pasted PowerShell command is needed for this original test. If the configured Gtuner Python executable exists, the sender uses it; otherwise it uses the app interpreter. The sender uses only Python's standard library.

The standalone GPC has no app heartbeat or acknowledgment. Its console action is therefore checked through **Gtuner's `CONSOLE TEST: Titan issuing A` output and the Xbox screen**, not a fabricated app success indicator. The app says only that one request was sent when execution cannot be independently observed. The original pair is a button diagnostic; it does not implement AI-controlled rhythm/timing shots. Return to the meter GPC/worker afterward to test detection-driven output. Windows tests exercise the actual PowerShell sender against the original standalone receiver without an app API or heartbeat; physical Xbox and vendor compilation remain separate checks.

## Integrated PowerShell console-output tests (2026102203)

On Windows, the app now launches the persistent model-free bridge host through **PowerShell → the app's Python environment**. Arguments are passed as quoted literals in a UTF-16LE encoded command; the app does not launch PowerShell once per video frame. The owner lease still shuts down the Python host on app exit. The GPU detector, capture and Gtuner USB host retain their existing independent roles.

In Live Detection, the right sidebar has **CONSOLE OUTPUT TEST → Test A via PowerShell / Test X via PowerShell**. These buttons run the same Python sender and `T2TEST:A/X:<timestamp>` loopback UDP protocol on port **26741** that worked in the manual console test. Each test disarms meter output first and sends exactly one 100 ms tap, without retrying uncertain delivery. Capture and the detector are not needed. Arming is blocked while a test is outstanding. The status distinguishes sender completion, a new matching Titan execution counter, and unverified Xbox receipt.

**Install the new bridge 3.2 `titan_meter.gpc` once and restart the updated `gtuner_meter` CV worker.** The normal worker now also receives the manual protocol, and the meter GPC handles its A/X commands using the original diagnostic combinations. No separate test slot or CV script switching is needed. Stop any old standalone `console_button_check` worker that owns port 26741. The app refuses to claim an unsupported old GPC has executed a test. Normal meter packets remain protocol v3; the heartbeat adds the independent manual-test counter and button.

Use a menu or practice session for the explicit button test. If Titan reports issuing A but Xbox does not respond, investigate the Titan OUTPUT cable, authentication and console configuration. If A works while meter shots do not, inspect the selected mode, physical hold, armed state and green detector measurements. PowerShell is the launcher/sender, not a replacement for the Gtuner/Titan USB connection. Software tests and native PowerShell checks cannot confirm a physical Xbox action or a successful shot.

## GPC2 numeric-type fix (2026102202)

Bridge **3.1** fixes the vendor compilation errors on direct `get_actual()` comparisons: that API returns `fix32`, so RS/X thresholds now use decimal literals (`50.0`, `-50.0`, `0.0`, `-20.0`, `20.0`). Shooting mechanics and wire protocol v3 are unchanged. Export the matching scripts from the updated app and compile/install `titan_meter.gpc` again. The original host shim returned integers and missed this restriction; it now returns a distinct fixed-point stand-in that rejects comparisons with integer literals. Host/Windows tests still do not replace compilation in Gtuner or console validation.

## Rhythm and Timing shot modes (2026102201)

The Live Detection sidebar now selects **rhythm** or **timing**. Changing mode disarms output; release X and RS, then explicitly arm again. This replaces the earlier X-to-RS conversion described in older release notes below.

- **Rhythm:** hold the actual RS opposite the selected flick direction (default: RS down). The script preserves your hold, recognizes an observed meter before green, then applies the configured delay (default 45 ms), flick (default RS up for 100 ms), and neutral. Releasing early does not trigger a fallback flick. One flick is allowed per hold; neutral RS rearms the hold logic.
- **Timing:** hold Xbox X normally. A fresh observed green event releases X immediately, with no RS output and no rhythm delay. X stays suppressed until you physically release it, preventing a second press while held.
- RT, movement and unrelated inputs pass through in both modes. R3 cancels automation; it no longer maps R3 to X. Disarm, stale data, cancellation and mode changes require a fresh neutral/released input before another automated shot.

**One-time Titan migration:** update the app, use **Open Bridge Files**, compile/install the exported `titan_meter.gpc` (bridge **3.0**) in your chosen slot, and stop/restart the updated `gtuner_meter` worker in Gtuner Computer Vision with Null Video Input. An app update cannot rewrite a script already stored on Titan. The new app refuses to arm against a v2 worker or Titan script and explains the mismatch. Old and new bridge files must not be mixed.

The new GCV payload is **26 bytes**, retaining offsets 0..24 from the historical layout, with version **3** at byte 2 and shot mode at byte 25 (**0 rhythm, 1 timing**). The device heartbeat adds a final `:3` protocol revision; both worker and device revisions plus a current payload echo are required for readiness. Byte zero remains zero so the old Onyx flag reader cannot fire on metadata.

The current detector recognizes the **meter**, not the player's jump animation. Player-animation onset is future detection work. Tests cover both mode values through real local API/UDP/GCV encoding, mode/arming resets, and the actual GPC main-body state machine under a C++ controller shim. The shim is not the Gtuner vendor compiler. These tests do not establish physical Xbox actions, successful shots, or detection accuracy on gameplay; validate each mode in practice with the separate testing workflow.


This project focuses on meter detection, continuous per-frame tracking, and measurable validation. It takes a trained Ultralytics detector (`best.pt`) and gameplay MP4. Inference is performed on every decoded frame; no frame skipping is used. Keep this folder separate from the older scripts so the original project remains intact.

## Desktop app (Windows)

After installing the desktop update into this project folder, double-click **`Launch NBA2K CV.bat`**. The app has separate tabs for Live Detection, Results, Updates, and Improve Model. It looks for `best.pt` in previous live-run reports and the known Downloads layout; **Auto Find** searches Downloads more broadly. You can paste a full model path into the field or use **Browse**. The app remembers the selected path. **Start Live Test** creates a fresh results folder each time. **Test Capture Only** measures the card without loading the model. Results shows each run's device and measured FPS, with a button to open its folder. The Improve Model tab requires human-corrected labels and distinct training/validation/test captures, as described below.

**Import to App** copies a selected trained `.pt` weight into `models/best.pt`. The app automatically finds that copy on later launches, and code updates leave `models/` intact. Replacing it keeps `models/best.previous.pt` as a backup. Select your existing `runs\meter_v2_gpu_1280-6\weights\best.pt` once, then click **Check Setup** to verify that the installed PyTorch and Ultralytics can load it. The app cannot build a reliable trained weight from code alone; training needs reviewed gameplay labels, separate validation and held-out test captures, and a suitable GPU. The **Improve Model** tab handles that workflow when those inputs exist.

The launcher uses **Python 3.11 x64** and a dedicated **`.venv311`**. If Python 3.11 is missing and WinGet is available, it installs the user-scoped x64 package; otherwise it reports the required installer. OpenCV, PyWin32 (`win32api`, `pywintypes`, `pythoncom`), Pillow, Windows controls and detection dependencies are checked before startup. On a PC visible to `nvidia-smi`, it installs the official CUDA 12.8 PyTorch 2.11 / torchvision 0.26 wheels when CUDA is unavailable and verifies GPU allocation before launching. Other environments, including the older `.venv`, are preserved. This is a managed Python app, not a self-contained executable. Windows CI now targets Python 3.11.

**Titan Two → Setup & runtime → Prepare Gtuner Python 3.11** installs OpenCV and PyWin32 into the full Python 3.11 base behind the app environment, then reports the installation folder. Select that full folder in Gtuner Preferences → Computer Vision. Gtuner embeds Python and does not automatically use the app's venv or its CUDA packages. The preparation action checks imports and DLL paths; Gtuner indicators, worker startup, payload echo and console output still need separate verification.

The Updates tab checks a configured release feed at startup. **Install Update** downloads a new ZIP, checks its HTTPS source, version, size, and SHA-256 digest, validates archive contents, backs up code under `updates/backups`, installs it, and restarts. Model weights, `.venv311`, other environments, settings, recordings, labels, and `test_results` stay in place. **Install Downloaded ZIP** remains as a fallback. The first desktop installation still requires extracting the provided update ZIP over your existing project folder once. `update_source.json` points to this repository's latest release feed. `app.py` contains the interface; `app_update.py` handles downloads and replacement; `run_live.py` and `testing/` remain usable directly or in a code editor such as VS Code.

For this GitHub repository, use **Actions → Publish desktop update → Run workflow**, selecting `main`, after committing an increased `app_version.json`. The workflow handles Windows validation, packaging, the tag, and both release assets. No manual ZIP upload is needed.

Click **Check Setup** before a live run. It writes `setup_check.json` and checks the chosen model, installed CV dependencies, CUDA access, and model loading. Failures are shown in the app; unexpected UI errors are written to `app_errors.log`. This is a readiness check, not a speed or accuracy test. Before treating a build as a reliable daily-use app, verify on the Windows PC that setup passes, capture reaches the requested mode, the live preview and both recording modes work, the updater restores/restarts, and held-out labeled clips meet the shot tracking and fill accuracy targets. The Windows GUI and trained model cannot be executed in this cloud workspace. A standalone `.exe` requires a Windows build and testing of its bundled runtime; the current double-click launcher uses the project's virtual environment. PyInstaller cannot cross-build a Windows executable from Linux.

## GitHub release updates

The repository includes a Windows-gated publication workflow. The easiest release path is **Actions → Publish desktop update → Run workflow → main**. It reads the version from `app_version.json`; no tag needs to be created beforehand. A pushed `v<version>` tag or a published stable release also triggers it. A tag must match the version in its source commit. Already used versions cannot be assigned to a different commit: increase the version for changed code.

The workflow validates the exact source on Windows, runs the bounded GUI smoke check, and retains that tested ZIP. Publication downloads that same artifact, generates a SHA-256 manifest pinned to its version tag, and checks the archive and Python syntax. A new release stays a draft until both files have been uploaded and downloaded back for verification. It then publishes and checks the public files. Stable publications are serialized; publishing an older version cannot move the latest feed backward. Prereleases are excluded from the stable app feed.

The updater expects exactly `NBA2K_CV_Update.zip` and `update.json`. Its configured feed remains `https://github.com/OWNER/REPO/releases/latest/download/update.json`, while each manifest points to `/releases/download/v<version>/NBA2K_CV_Update.zip`. New clients can recover from a missing latest manifest by inspecting the newest complete stable releases, and pin legacy mutable ZIP links before downloading. Missing assets report an actionable error. HTTPS, version, size, SHA-256, archive path validation, backup, and rollback remain required.

After the workflow completes successfully, users select **Updates → Check for Updates → Install Update** in the app. Models, environments, settings and results stay in place. Failed Windows validation prevents publication; inspect the failed check and rerun after fixing it. Source code reaching `main` alone does not establish a published app update. The repository and release downloads must be public for the current unauthenticated updater.

To build locally, use `python scripts/build_release.py --repository OWNER/REPO --out NBA2K_CV_Update.zip`. Identical inputs produce reproducible archives. Generate a manifest with `testing/make_update_manifest.py`; its `--verify --archive ZIP --manifest JSON --tag v<version>` mode performs the same artifact checks used during publication. Existing installations with no configured feed still need the initial ZIP installation once.

## Setup (Windows PowerShell)

```powershell
py -3.11 -m venv .venv311
.\.venv311\Scripts\Activate.ps1
pip install -r requirements.txt
python run_video.py --model "C:\path\to\runs\meter_v2_gpu_1280-6\weights\best.pt" --video "C:\path\to\gameplay.mp4" --out test_results\first_run
```

## Live capture test

Connect the Xbox to a capture card that appears as a Windows video device. In PowerShell, with the same environment active:

```powershell
python run_live.py --model "C:\path\to\runs\meter_v2_gpu_1280-6\weights\best.pt" --source 0 --dshow --seconds 60 --record --out test_results\live_60s
```

First measure the capture device **without AI or recording**. Run this from the project folder, with the environment active:

```powershell
python run_live.py --probe-only --source 0 --dshow --width 1920 --height 1080 --capture-fps 60 --fourcc MJPG --seconds 10
```

Inspect `test_results\live\capture_probe.json`: `decoded_sizes` is the size actually returned, `reported.fps` is the backend's claim, and `read_fps` is the rate delivered to Python without the detector. You may repeat with `--capture-fps 120` only if the capture card/input supports that mode. If it still returns 640×480 or 60 FPS, the mode request was not honored; check the capture device and its source settings. A 60 FPS game/input cannot yield 120 distinct gameplay frames just by requesting 120 here. `read_fps` does not prove every frame is visually unique. The app window itself is updated after inference, so its visible update rate can be lower than the device rate.

Once capture alone is measured, request the same mode in the live detector, and compare processing settings using the same gameplay conditions:

```powershell
python run_live.py --model "C:\path\to\best.pt" --source 0 --dshow --width 1920 --height 1080 --capture-fps 60 --fourcc MJPG --imgsz 640 --seconds 60 --record --out test_results\live_640
python run_live.py --model "C:\path\to\best.pt" --source 0 --dshow --width 1920 --height 1080 --capture-fps 60 --fourcc MJPG --imgsz 960 --seconds 60 --record --out test_results\live_960
```

`--imgsz` changes the network input size; `--width` and `--height` request the capture-device mode. These are independent. Small meters may become harder to detect at a lower network input size. Compare missed meter frames and box quality, not only FPS. A CUDA-enabled GPU can help inference if available; `live_run.json` identifies the device selected by the installed PyTorch build. This workspace cannot benchmark the user's Windows hardware or the trained weights.

To see whether your current Python environment can use CUDA, run `python -c "import torch; print(torch.cuda.is_available())"` in that same PowerShell environment. If it prints `False`, changing the capture FPS to 120 will not speed up CPU inference. The 60 FPS capture goal requires end-to-end processing under 16.7 ms per delivered frame; 120 FPS requires under 8.3 ms. Otherwise the preview shows only the processed frames and cannot represent every source frame.

The window shows a green box for a real detector result and an orange box for a short tracker prediction. Press **Q** to stop. If source 0 is a webcam, try `--source 1` or another Windows capture device index. `--headless` runs without the window. `--record` writes `live_preview.mp4`; `live_frames.csv`, `live_shots.json`, and `live_run.json` are saved whether or not recording is enabled. Do several jumpshots and periods with no meter so false detections and starts/ends can be reviewed.

`live_run.json` records measured throughput, the number of inference and full-loop times over the source frame budget, and late read intervals. At 60 FPS the budget is 16.7 ms. A camera backend may discard frames while inference is running, and frame indices in the log count frames returned by the device rather than every frame transmitted by the console. These timing flags are diagnostic, not a verified dropped-frame count. For accurate model validation, retain separate labeled gameplay clips and evaluate them with the testing tools below.

The live log now measures capture read, raw recording, inference, overlay, and preview recording separately. In the 1080p RTX 5060 Ti recording supplied for review, 771 frames were processed in 30.08 seconds (25.63 FPS); inference median was 11.52 ms, but the whole loop median was 33.68 ms while both raw and preview recording ran. The new stage times identify which recording step costs time on the next test. The meter was visibly present during gaps at frames 622–632 and 707–731. Replaying the **saved detections** with `max_missed=4` grouped 14 candidate spans into 8; this is a tracking continuity change, not a new model inference or accuracy result. Predicted frames never supply fill evidence. The live default is now four missed frames; the offline runner remains at two, and both can be overridden with `--max-missed`.

The live MP4 is recorded at up to 30 FPS on a wall-clock timeline. When inference is slower than capture, it holds the previous analyzed frame between updates; repeated frames are **not** new detections. The JSON records the playback frame rate and count. The CSV remains the source for observed-frame measurements. A 60 FPS source needs inference and the entire loop below 16.7 ms to inspect each live frame; the runner cannot recover source frames discarded while it was busy. If the report says `device: cpu` and all frames exceed the budget, first check whether a CUDA-capable GPU is available and the installed PyTorch build exposes it, then compare `--imgsz 960` and `--imgsz 640` on a separately labeled clip. Lower input size may miss a small meter, so speed alone does not establish an improvement. On the supplied 640×480 CPU run, the older version processed 810 frames in 60.05 seconds (13.49 FPS) and encoded them as a 13.5-second MP4. It reported 12 candidate spans, including four whose first observed fill was already at or above 98%; these are not validated shot or fill counts.

The outputs are `frames.csv` (one row per decoded frame), `shots.json` (candidate shot spans and last **observed** fill), and `run.json` (inputs/settings). `status=predicted` bridges up to four missing detector frames in the live runner and two in the offline video runner by default; these frames have no fill value and never count as a detection. Tracking rejects candidates with a large position or size jump and prefers a matching box over a higher-confidence distraction. `stop_candidate` means the measured fill plateaued, not that a release or green outcome was proven. `shots.json` retains `last_stop_candidate_frame` and `first_full_frame` when observed. A `reached_full` flag is based on the provisional fill estimate; it is not a game outcome.

The white-fill pixel estimator follows the prior project's bright/low-saturation center-band logic. It returns an empty fill when uncertain. The detector box includes the pointed decorative ends of the meter, so fill is normalized to the interior track. The defaults `--fill-top .04 --fill-bottom .90` were visually checked on the provided recording, not calibrated against ground-truth fill labels. Recalibrate for a different meter style, HDR/color setting, compression, or UI. The detector's training and broader generalization are properties of the supplied weights and data, not guaranteed by this runner. Test each environment before relying on fill or final-state results.

For a first run, set `--max-frames 300` if desired. The default is 1280, matching the earlier full-video test; try `--imgsz 960` or `--imgsz 640` for measured speed/accuracy comparisons. Do not compare FPS across machines without recording hardware and settings. CUDA is used when available, otherwise CPU.

## Separate validation tools

```powershell
python -m unittest discover -s testing -v
python testing\check_capture.py --video "C:\path\to\gameplay.mp4" --out test_results\capture_check.json
python testing\replay_detections.py --csv "C:\path\to\detections.csv" --out test_results\replay
python testing\evaluate.py --frames test_results\first_run\frames.csv --annotations testing\annotations.example.json --out test_results\evaluation.json
```

The example annotations are a **format illustration only** and must not be used for accuracy claims. Create labels for every frame in each selected clip, including frames with no meter. Annotations use source-video pixel coordinates and fill fractions from 0 to 1. `shot_id` groups consecutive frames of a jumpshot; the evaluator rejects gaps inside a shot. It reports frame precision/recall, mean box IoU and IoU pass rate, fill MAE when labeled, and per-shot coverage, onset delay, last detection and localized-box gaps, and final fill error. It excludes predicted frames from positive detections. Unknown box/fill values are excluded from their respective metric, while an unannotated frame is not evaluated. Presence recall alone does not prove the box follows the meter; inspect the box localization metrics too.

The capture checker fully decodes the file and compares decoded frames with metadata, frame dimensions, and video timestamps. `run_video.py` fails if the capture ends early or timestamps repeat/regress; `run.json` records `complete` so partial output is not mistaken for a completed run. A deliberate `--max-frames` run is marked incomplete. The checker detects video-file integrity, not the reliability of a capture card, HDMI splitter, USB input, or live stream.

For a trustworthy test set, keep entire gameplay clips/shots out of training and tuning. Include different courts, UI settings, output resolutions, camera views, meter colors, motion, occlusion, and no-meter gameplay. Hand-label onset, every meter frame, and final position. Inspect failures by environment and shot rather than using overall detection rate as accuracy.

## Test-to-training learning loop

Every new `run_live.py` run now writes `review/queue.json`. The queue flags detector gaps, low confidence boxes, transitions, large fill jumps, and sampled no-meter frames. It is **automatic triage**, not verified error labels. To save unannotated frames that can be corrected and used for training, add `--record-raw` to a live test. This also saves `raw_capture.mp4` and extracts up to 120 original frames into `review/frames`. The raw MP4 contains one frame per device read, indexed exactly like `live_frames.csv`; its encoded FPS is source metadata, not proof that processing kept up. Recording 1080p raw frames can use significant disk space and increase processing time; compare the timing report with and without it.

```powershell
python run_live.py --model "C:\path\to\best.pt" --source 0 --dshow --width 1920 --height 1080 --capture-fps 60 --fourcc MJPG --imgsz 1280 --seconds 30 --record-raw --out test_results\train_run_1
```

Review the images and video, then create `reviewed_labels.json` in that run folder. Each entry needs `reviewed: true`, a boolean `meter`, and a corrected pixel box for positive frames. Negative entries must have no box. Predictions in `queue.json` are only hints. Example:

```json
{"frames":[
  {"frame":0,"meter":false,"reviewed":true},
  {"frame":1,"meter":true,"reviewed":true,"box":[100,100,122,161],"shot_id":"shot-1","fill":0.22},
  {"frame":2,"meter":true,"reviewed":true,"box":[101,100,123,160],"shot_id":"shot-1","fill":0.31}
]}
```

Training runs may label selected frames. Reserve **different captures** for validation and final testing, and label *every* captured frame in those runs, including no-meter frames. Give each complete shot a consistent `shot_id` and a hand-measured final `fill`. Keep shots/courts/settings from the final test out of training and model selection. Add original, varied training examples if possible so fine-tuning does not forget earlier conditions.

When you have one or more corrected training runs plus separate fully labeled validation and test runs:

```powershell
python testing\learning_loop.py train --train-run test_results\train_run_1 --validation-run test_results\validation_run --test-run test_results\heldout_run --model "C:\path\to\best.pt" --out test_results\learning_001 --epochs 20 --imgsz 1280
```

The script fine-tunes a candidate, validates it on the validation capture during training, and independently compares old/new weights on the held-out capture. It also reruns full-frame tracking/fill analysis there. It writes `comparison.json` and only creates `improved_best.pt` if detection mAP50 improves by at least .01 without precision, recall, box localization, any shot's coverage/onset/final gap, or labeled fill error getting worse. It never overwrites the input weight. A failed gate keeps the candidate for inspection but does not select it. This requires manual corrected labels: an AI detector cannot reliably fix its own weights from its unverified predictions. The local workspace lacks your model and capture hardware, so training and live speed must be measured on your PC.

## Current evidence and limits

The supplied historical `detections.csv` replays as 5,588 frames: 3,184 observed detections, 102 short predicted frames, 2,302 absent frames, and 39 candidate shot spans. A diagnostic full pipeline pass used that CSV in place of a model and flagged 6 candidate shots as full and 27 with a plateau. These are **not** accuracy or true-shot counts. The recorded 1920×1080, 60 FPS MP4 fully decoded all 5,588 frames with no timestamp regressions or duplicates in the available environment. The available labeling archive has 80 sampled JPEGs and no completed label text files; the model weights are not available here. A fresh inference run and ground-truth validation remain pending those inputs.


## Live dashboard and Titan Two bridge (2026102001)

Open the existing launcher, then use **Live Detection**. Model weights are selectable from the model dropdown (managed `models/` and project `runs/` weights plus the current choice). Browse adds an external weight. The environment dropdown lists the app runtime, project `.venv311`, and legacy `.venv`; use **Browse Python** for another existing environment's `Scripts/python.exe`. Choose compute `0` to require CUDA GPU 0, `auto` for CUDA with CPU fallback, or `cpu`. **Check Setup** runs in the selected environment; an explicit CUDA selection fails rather than quietly falling back. Changing the runtime does not install CUDA or modify that environment. The launcher defaults to the dedicated Python 3.11 app environment; verify its CUDA access on your RTX 5060 Ti with Check Setup. Older environments remain selectable for troubleshooting.

**Seconds = 0** runs until **Stop**. Stop now requests a graceful exit so frame logs and shot summaries are saved. A disconnected device is reported separately. If a driver hangs inside a blocking read, graceful stopping must wait for that read; the app cannot guarantee a driver-level timeout. Recording defaults off. The app displays the capture with detection overlay, processing FPS, inference time and preview age. The embedded diagnostic preview is limited to 640 pixels and up to 10 updates/second; this is independent of inference FPS. Encoding/writing happens on a worker with only one pending image, preventing an old-frame preview queue. This does not add an independent high-FPS capture thread or recover discarded device frames. PNG overhead and bridge overhead still need measurement on the Windows capture hardware.

The controller dropdown records a manual profile (Xbox/GameSir, Xbox Elite, DualSense, DualShock 4, Generic HID) in each run's `app_profile.json`. It does not discover devices or change their mapping. **Titan Two** contains setup steps and **Open Bridge Files**, which opens/exports the two adapter scripts:

1. Connect Titan Two PROG to the PC; controller to INPUT-A; OUTPUT to the console according to the official ConsoleTuner device guide.
2. Compile `titan_heartbeat.gpc` in Gtuner IV and run it on the device. This diagnostic script emits a changing `T2CV:` heartbeat every 500 ms while leaving controller inputs unchanged.
3. Open `gtuner_meter.py` in Gtuner IV Computer Vision. Choose a display/video source different from the app's capture-card device to avoid exclusive access conflicts. Start Gtuner's worker. Gtuner uses its own supported Python runtime; the worker needs the built-in `gtuner` module but does not need PyTorch/CUDA. The desktop detector keeps its separate CUDA environment.
4. Enable **Titan Two bridge** in the app and start live detection. Only one Gtuner worker can bind localhost UDP port 26740. The port must remain the same in both components.

The dashboard distinguishes a fresh Gtuner receiver acknowledgement from a changing hardware heartbeat. Acknowledgements expire after 750 ms, hardware evidence after 1.5 seconds, and receiver telemetry after 250 ms. Packets older than 250 ms are rejected. Network round-trip timing is for the local app/worker link, not capture/console latency. Connection status is available during a running detector; stopping it marks the bridge inactive. A working return heartbeat does not prove the device consumed a GCV payload or that the console received an action.

The worker returns an eight-byte telemetry packet through Gtuner's GCV interface: version (u8), status (u8: 0 absent/stale, 1 detected, 2 predicted), fill (big-endian u16: 0..10000, 65535 unknown), sequence (big-endian u32). Predicted frames always carry unknown fill. The included diagnostic GPC does not consume these packets or automate controller inputs; hardware-side consumption is a subsequent integration step. Do not infer a verified final meter state from a bridged prediction.

The support modules stay separate in Git, while release packaging embeds them in existing `run_live.py` so the first installed updater can accept the package. Bridge resources are exported when **Open Bridge Files** is clicked. No trained weight is included or changed. Tests exercise UDP acknowledgements, stale data, wrong-session rejection, predicted fill, graceful stop and dashboard startup/command generation. Actual Titan Two compilation, Gtuner runtime compatibility, USB disconnection and live GPU/capture latency require the Windows hardware test.

References: [Gtuner IV](https://www.consoletuner.com/software/gtuner-iv/), [official GPC-to-Python heartbeat example](https://github.com/J2Kbr/GtunerIV/issues/406), [Titan Two downloads and GCV release notes](https://www.consoletuner.com/titan-two-downloads/).


## Green-zone detector and controller bridge (2026102002)

The app now provides the missing Python side of the meter-to-green trigger. It does not require the original Onyx Python program. **Do not load the supplied Onyx GPC with the old version-1 worker**: its byte-zero flag is incompatible with that telemetry format. Use **Titan Two > Open Bridge Files** after updating; this exports the matching `gtuner_meter.py` and `titan_meter.gpc` together. Copy/exported adapter scripts are overwritten with this release when the button is clicked. Your original uploaded Onyx file is not modified.

The current trained weight still localizes the meter. A separate provisional color detector looks for one contiguous green band inside the current **observed** box, and checks whether the raw white-fill edge is inside it. It does not substitute a percentage threshold for a visible green band or use smoothed/predicted fill as trigger evidence. Green hue bounds (OpenCV HSV hue 0..179), saturation, brightness, green row coverage, detector confidence, confirmation frames, maximum frame age, and absence rearm time are adjustable in the Titan Two tab. The preview draws the measured green-band boundaries and yellow fill edge. This initial heuristic supports the calibrated vertical white-fill meter; other meter styles and HDR conditions require separate calibration or additional training. The default hue values are starting values, not a validated gameplay calibration.

**Trigger output starts off every time the app opens.** Run with it off to collect `green_frames.jsonl`: each frame logs presence, visible band measurements, reason for uncertainty, raw fill, event candidate and output settings. The model's ordinary frame/shot reports remain separate. Use raw recordings and hand labels to validate green entry and event timing; synthetic tests only establish software behavior. No real-game green accuracy or console timing has been verified for this release.

The gate must first observe the white-fill edge below the green band, then inside it. It requires current confident observations and the configured confirmation count, emits one event per visual encounter, and rearms only after sustained meter absence. Changes in tracker shot ID alone cannot rearm it. Enabling output clears the prior approach; a new observed approach is required. **Apply Detection / Flick Settings** updates the active run without restarting and saves calibration/timing for later runs; armed output is intentionally not remembered. Changing colors during a run should be done in dry-run mode.

Load `titan_meter.gpc` into Titan Two using Gtuner IV. Hold the physical Square / Xbox X shoot button to initiate the opposite stick direction. A fresh eligible green event starts the configured flick: delay (default 45 ms), selected right-stick direction (default up), hold (default 100 ms), then neutral until button release. A down flick starts from up; an up flick starts from down. This follows the core Onyx hold-to-flick sequence, but **does not** trigger on button release. Hardware allows only one event per physical press. R3 cancels the state machine immediately, maps its input to Square, and neutralizes both right-stick axes while held. Physical release, disabled output and expired transport cancel the active state; normal physical controls then pass through. Unlike the original script, left-stick movement and R2 are not suppressed.

The version-2 GCV payload is 25 bytes, big-endian: byte 0 is always 0; byte 1 is magic 77; byte 2 is version 2; byte 3 is observed status (0 absent/stale, 1 detected, 2 predicted); byte 4 armed; byte 5 eligible green; byte 6 direction (0 up, 1 down); bytes 7..8 delay ms; 9..10 hold ms; 11..14 event token; 15..18 visual shot ID; 19..22 sequence; 23..24 remaining lifetime ms. Unknown versions fail closed. The fixed zero byte prevents the old Onyx flag reader from firing on version metadata. Legacy diagnostic packets are also zero at byte 0 in this worker. Stale, uncertain, unarmed and predicted observations cannot set the green trigger. A moving heartbeat reports the sequence received and last event started on Titan Two. This is device-side evidence, not confirmation of console behavior or a successful shot.

The Python detector runs in the CUDA environment selected in our app. Gtuner's lightweight worker remains in Gtuner's supported Python environment and requires no Torch. The app packages both adapters; Gtuner is still required to compile/load GPC and host the device interface. The GPC source has not been compiled on Titan Two hardware here. Verify compilation, physical-button mapping, R3 cancellation, unplug/reconnect, stick direction, stale timeout and end-to-end latency before relying on it. The detector's existing frame gaps and final-fill accuracy still require labeled gameplay validation. Zen is not supported by this Titan Two protocol.


## Model library and workspace design (2026102003)

Live Detection now uses a dark preview workspace with a scrollable settings column, separate processed-FPS/inference/status cards, a larger preview area and visible start/stop actions. The Titan Two, Results, Improve Model and Updates workflows remain available. The **Models** tab lists weight names, file sizes and locations. Double-click a row or click **Use selected weight** to select it; the live dropdown shows readable run/file names and keeps the real path separately. Duplicate filenames receive distinct labels. Empty and missing files are excluded, and listing weights does not deserialize or execute them.

**Add weight** remembers an existing local `.pt` path; **Import to App** retains the existing managed-copy/backup behavior. Added weights, environment choices and the selected path are saved in `app_settings.json` and preserved by code updates. The library also scans the project's `models/` and `runs/` folders. **Find models** searches Downloads in a background worker and adds all discovered `.pt` paths rather than freezing the interface. No model is downloaded or trained by selecting it. Active runs must stop before switching weights.

The **Inference input size** dropdown changes `imgsz` between 640, 960 and 1280; it does not replace the weight or change capture resolution. Keep 1280 as the current baseline for the small shot meter. The supplied 1080p live run with this baseline averaged 57.71 processed FPS without recording. There is no equivalent labeled 640/960 comparison yet. Use the same held-out clips to compare speed, missed meter frames, green entry and final fill before adopting a smaller size. A faster setting alone does not establish better detection. See the [Ultralytics prediction arguments](https://docs.ultralytics.com/modes/predict/) for the inference-size setting.

### Update notifications

The app checks the published HTTPS release feed on launch and every five minutes while open. A newer release shows a nonmodal notification and an Install Update banner on every tab. Later dismisses the notification for that version until the next launch; the banner stays available. Installation waits until runs finish, verifies the download, preserves local models/settings/results, and restarts the app. Failed downloads can be retried. Existing installations need to install this version once from Updates → Check Online → Install Update to receive these notifications. This is periodic polling, not a push connection.

### Gtuner startup and pre-capture readiness (2026102005)

Select `Gtuner.exe` in the Titan Two tab and enable automatic Gtuner startup. With Titan bridge enabled, opening the app opens Gtuner with the matching GPC and CV worker files. It does **not** compile, flash, select a slot, or start the CV worker automatically. Gtuner's documented command-line behavior opens compatible files; no supported external slot-programming API has been verified.

One-time hardware setup: install `titan_meter.gpc` to an unused slot in Gtuner IV, select it, and enable **Recall loaded memory slot between power cycles** under Device Configuration. Start `gtuner_meter.py` in Gtuner Computer Vision, using a Null or separate display source rather than the app's capture card. Source updates still require reinstalling the GPC through Gtuner. The app's GPC remains uncompiled and untested on physical Titan Two hardware here.

**Prepare / Check Bridge** sends unarmed, absent-meter v2 packets for up to five seconds and waits for a fresh heartbeat echoing a randomly seeded packet sequence. A software acknowledgement or an unrelated diagnostic heartbeat is insufficient. Starting capture with Titan bridge enabled repeats this check and refuses to open capture if it fails. Detection-only capture remains available with the bridge disabled. The check verifies local transport and device payload consumption, not controller authentication, console actions, timing accuracy, or successful shots. Existing stale-packet cancellation still applies during capture. The bridge has its own lightweight Gtuner runtime; CUDA stays in the desktop app.

## Slots, reconnect, and direct card display (2026102006)

This version replaces the earlier PNG diagnostic view with an independent raw BGR live display in the desktop app. One reader owns the capture card and continuously drains it while inference works. The app reads the newest completed buffer from a two-buffer memory-mapped mailbox; it does not open the capture card again, encode PNGs for direct display, or build a video backlog. The displayed card image is raw; AI measurements and bridge status are reported separately. Reducing display FPS reduces display work independently of the capture request and model input size.

| Setting | Choices / behavior |
| --- | --- |
| Capture FPS request | 60, 120, 240; requires a card/source/USB mode that actually delivers it |
| Capture backend and format | DirectShow: MJPG, YUY2, BGR3 requests; Media Foundation: MJPG, YUY2 requests; Auto/default |
| Performance display | Up to 1280×720, default display limit 120 FPS |
| Resolution display | Up to 1920×1080, default display limit 60 FPS |
| Display limit | 15, 30, 60, 120, 240; adjustable during a run |

Presets change the display, not the requested card dimensions or the detector weight/input. Width and height still select the card mode. OpenCV delivers decoded BGR; BGR3 is a device-format request, not a promise that a driver supports native BGR. Capture delivery FPS, AI FPS, rendered FPS, raw-frame age, reported card mode, and skipped inference frames are distinct. Neither selecting 240 nor selecting Performance creates extra unique source frames. Performance on the user's physical capture card and CUDA environment still needs measurement.

The low-latency reader drops old frames **for inference** when AI cannot keep up. Every such source-frame gap is recorded in `capture_skips.jsonl` and per-row `source_frame` / `skipped_before`; unknown frames cannot support fill, plateau, or consecutive green confirmation. They count against the real-time target. Raw training recording contains the analyzed frames, not every card-delivered frame. Such a sampled run is rejected as exhaustive validation. For frame-complete testing, keep using sequential `run_live.py` without `--live-direct`, or `run_video.py` against a separately recorded original video, and the testing/validation workflow. No model weights or meter families were changed in this update.

### Titan Two slot selection

Update through the app, then let **Launch NBA2K CV.bat** finish installing its Windows accessibility dependency. The updater restarts through that launcher. Select the real Gtuner executable, run one Gtuner IV instance, and keep the **Device Memory Slots** pane visible. **Refresh Slot Names** reads labels exposed by Gtuner's Windows accessibility tree; unavailable or ambiguous labels stay explicitly unavailable. These labels are software observations, not proof of flash contents or an empty slot.

**Auto (meter bridge)** chooses only one unambiguously named `NBA2K CV Meter Bridge` or `titan_meter.gpc` slot. If the label cannot be read, select its known slot number explicitly. With automatic selection enabled, startup and reconnect checks request that slot using Gtuner's documented Ctrl+1…9 shortcut, targeted to the selected Gtuner process after a focus check. It may bring Gtuner to the foreground. Slot switching disarms the trigger and can run while capture and AI continue. A sent shortcut is not reported as a confirmed switch: a fresh GPC heartbeat must report the selected slot and echo the new unarmed payload before bridge output is considered ready.

**Reinstall the exported `titan_meter.gpc` in Gtuner once for this version**, and reload the matching `gtuner_meter.py` worker: its new heartbeat includes `mslot_get()`. The app still does not compile or write memory slots automatically. Old three-field heartbeats remain visible as hardware evidence but cannot confirm a selected slot. The GPC source has not been compiled or run on physical Titan hardware in this environment.

**Connect / Reconnect** reattaches to Gtuner and reruns slot selection and the unarmed transport check. **Disconnect App Bridge** disables bridge output while capture and AI continue. If a verified bridge goes stale, the trigger is disabled and the app attempts up to three background reconnections. Recovery does not restart capture or re-arm output. A missing cable, stopped Gtuner CV worker, inaccessible slot pane, or USB driver failure requires checking the reported stage; reconnect never resets firmware or fabricates hardware status.

Primary references: [Gtuner slot shortcuts, vendor issue #89](https://github.com/J2Kbr/GtunerIV/issues/89), [vendor slot functions issue #75](https://github.com/J2Kbr/GtunerIV/issues/75), [OpenCV video backend/property reference](https://docs.opencv.org/4.12.0/d4/d15/group__videoio__flags__base.html).

### Automatic Gtuner discovery (2026102007)

The Titan Two tab fills **Gtuner IV executable** automatically on startup, keeping a valid saved choice first, then checking a running Gtuner process, Windows App Paths, common install folders, and bounded Gtuner portable folders in Downloads/Desktop/Documents. The chosen path is saved. **Find Gtuner** repeats discovery and **Browse** remains available for custom locations. Missing or moved paths trigger discovery before connecting. Gtuner is not bundled or downloaded by this feature; if it is absent, the app says so instead of displaying a nonexistent default path.

## Independent capture and persistent local bridge API (2026102008)

**Start Capture** opens the capture card in a dedicated process using the app's OpenCV environment. It does not load a weight, import Torch/Ultralytics, or require Gtuner/CUDA. The console view stays live until **Stop Capture**. **Start AI Detection** requires a running capture and reads its original-resolution frames through a separate two-buffer mailbox. It never opens the card a second time. **Stop AI** leaves capture and its display running; Stop Capture also stops AI. Display presets and FPS changes still belong to the capture view. Capture status/results are in the capture session's `capture_status.json`, `capture_run.json`, and `capture_frames.csv`; model results remain in a separate AI run. Shared-frame timestamps remain the original capture delivery timestamps, and skipped inference frames remain explicitly counted. Model or bridge failures do not stop console viewing.

The app now owns a persistent, authenticated, loopback-only HTTP bridge service. The detector publishes current measurement/trigger state to it; one persistent UDP session connects it to the existing Gtuner CV worker. Idle, expired, disabled, wrong-slot, and unverified-device states cannot forward armed output. The API is background transport, not a new console/USB driver. Its private per-run configuration is local and is not sent to the release feed.

Connect/Reconnect first tests the **live receiver and device payload echo**. A working bridge bypasses Gtuner window discovery, executable lookup, script-label reading, and slot-keyboard commands. This fixes the previous software path that could reject a working bridge because Gtuner's accessibility labels were unavailable. If a host must be launched, the app reuses an existing matching Gtuner process or launches one minimized. Explicit slot switching can still bring its window forward. The vendor CV worker must already be running or be started once in Gtuner; this update does not pretend to start it through an undocumented API. **Gtuner may stay minimized but must remain running** as the USB host. No new GPC/worker protocol change is introduced by this version.

Bridge checks report separate stages: local API availability, Gtuner receiver, fresh Titan script payload echo, selected-slot match, and **console output unverified**. Latest sanitized diagnostics are written automatically to `test_results/bridge_latest.json`, without API credentials. Disconnect App Bridge disables its output while capture and AI can continue. If a previously verified bridge disappears, the trigger is disabled and the app retries the bridge in the background; it no longer stops/restarts the capture or model. Reconnection does not re-arm the trigger. Gtuner worker startup, Titan USB PROG connection, the installed/running GPC, controller authentication, and Titan OUTPUT-to-console wiring still need actual hardware verification. No live test logs from the user's Windows PC are available in this workspace, so this release does not claim a verified console connection.

Tests cover an authenticated API/UDP round trip with a simulated device, missing receiver and wrong slot, stale/idle disarming, capture-only execution without AI imports, one capture-card owner shared with a separate detector, and leaving capture running after AI detaches. These are software tests, not physical Titan/console validation.

### Release verification

Before publishing, run the unit suite and Windows GUI smoke check. The GUI check fails on unexpected modal dialogs, dumps stacks after 90 seconds, and terminates its worker after 120 seconds. A CI timeout is a failure, not a pass.

Each release needs both `NBA2K_CV_Update.zip` and `update.json`. The latest feed selects a release, but its manifest must point to that specific tag’s ZIP. This prevents a newer release changing the ZIP underneath an older manifest during an update. Creating an empty release does not provide an app update. Verify both assets are attached and the downloaded ZIP matches the manifest’s version, byte count, and SHA-256 before announcing availability.

Physical Titan/console verification and labeled gameplay accuracy remain separate from software tests. A verified local API or GPC echo does not prove console output or meter-detection accuracy.

### Preview Capture ownership and bridge 2.1

Preview Capture stops this app's active capture probe before opening the card. Card-owning app processes share a cross-process lock per numeric device index; AI reads the existing shared stream. On Windows the preview capture child runs at Above Normal CPU priority. This improves scheduling priority; it cannot override another program's exclusive driver handle. Close OBS, Camera, or the capture vendor utility if they hold the card, and use Null Video Input for the Gtuner bridge worker. Device indexes are local to Windows video APIs, so choose the matching physical card explicitly for each backend.

Failed opens retry for three seconds and save a clear capture failure report. Native driver calls can block past that retry interval; the UI independently terminates its capture child if it makes no frame progress for 15 seconds. Stop cancels a pending probe-to-preview handoff. No unrelated application is force-terminated.

The exported meter GPC is now bridge 2.1. CV byte reads use `gcv_read(offset, &value)` into a uint8 destination before decoding the existing 25-byte protocol. Recompile and replace the installed meter slot once using this exported script; updating Python files cannot change code already stored on the Titan. Gtuner's vendor compiler and physical console bridge are not available in the local test environment, so the read-contract tests do not constitute a GPC compiler or hardware pass.

### Titan USB discovery

The Titan Two tab now scans present Windows USB devices in the background, separately from the Gtuner/GPC transport. It recognizes normal `2508:0032` and bootloader `2508:8032`, reports Windows driver problem codes, and refuses to guess between multiple devices. Rescan Titan USB refreshes the same inventory. When one started normal-mode Titan appears and the bridge is enabled, the app requests an unarmed reconnect; unplugging it disables output. Device instance paths and serials are not written to diagnostics; `test_results/titan_hardware.json` uses hashed identities. Windows installs the normal device driver; discovery itself requires no new driver or administrator prompt.

This is automatic discovery and reconnect, not a standalone Titan programmer. The current bridge backend still uses Gtuner's compiler, installed GPC, and CV worker. Hardware presence does not prove a programmed slot, packet echo, or console output. The public interfaces researched do not establish a complete supported replacement programming backend. See [research and Ethernet findings](docs/bridge_research.md) for source links, alternative-backend capabilities, and remaining verification work.

### Bridge setup cancellation and run ownership

Bridge setup has a 12-second UI deadline. Stop, Disconnect, USB loss, and a changed slot invalidate the old check; late results cannot replace the current readiness state or switch a slot after a cancelled accessibility read. Changing the selected slot disarms output and requests a new check. Starting AI retains the explicitly selected slot guard, and toggling bridge enable does not clear it. A missing device-reported slot cannot count as ready. Output stays disarmed during preparation.

Capture-probe completion is associated with its run folder, so a late probe result cannot mark a newer AI run stopped. Repeated identical missing-device scans no longer rewrite settings and overwrite the current capture status every three seconds. These software checks do not replace compiling the GPC and testing the actual Titan-to-console path.

Python 3.11 migration validation: the local suite passed 126 tests under CPython 3.11, with two native-Windows checks skipped. Clean extracted-package imports also passed under Python 3.11. Windows launcher execution, real PyWin32 imports, NVIDIA CUDA allocation and Gtuner embedded loading remain Windows/hardware acceptance checks.

## App-controlled shooting sessions (2026102112)

Use **Start Session** in Live Detection to launch/reuse the capture, start detection when frames are ready, and connect the Titan bridge. Startup remains disarmed. **Arm Meter Shots** becomes usable after fresh detector output and a matching Titan payload echo are available. Hold Xbox **X** to begin a shot; the existing installed meter GPC commands the selected right-stick flick when an observed green event arrives. Disarm Shots stops automatic output. The detector must see an approach before entering green; arming while a meter is already green does not immediately fire. Direction and the rest of the calibration remain adjustable from the app. The status shows the local green event and Titan's reported flick event separately.

**Disarm Shots** leaves preview and detection running. **Stop Session** disables output first and stops the capture/engine. Capture or detection becoming stale disarms output; reconnecting does not re-arm it. Session preparation has a bounded deadline and never repeatedly restarts a failed engine. Capture-only and detection-only controls remain available. No PowerShell commands are required for normal meter-based output: the detector publishes directly to the authenticated local bridge API, and the Gtuner worker forwards the existing 25-byte protocol. Capture, inference, the bridge host, and preview preparation remain independent workers.

Gtuner remains the vendor USB host. The app discovers and launches/reuses it, verifies the slot, and requests CV startup only if its accessible Computer Vision script selector and start button are unambiguous. Builds that do not expose those controls need `gtuner_meter` started once in Gtuner with Null Video Input; the app reports this instead of guessing coordinates. Gtuner can remain minimized afterward. This app update does not change the meter GPC or packet format, so an already installed matching bridge does not need reprogramming for these controls.

Green-zone measurement still uses the calibrated color/fill heuristic inside the model's meter box. Software tests cover the synthetic meter-to-GCV command path, explicit arming/disarming, stale or missing components, and session cancellation. They do not establish detection accuracy across real meter types or verify that a shot scores on Xbox. Validate in practice using annotated footage and the separate testing workflow before relying on shot timing.

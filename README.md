# NBA 2K shot-meter CV pipeline

This project focuses on meter detection, continuous per-frame tracking, and measurable validation. It takes a trained Ultralytics detector (`best.pt`) and gameplay MP4. Inference is performed on every decoded frame; no frame skipping is used. Keep this folder separate from the older scripts so the original project remains intact.

## Desktop app (Windows)

After installing the desktop update into this project folder, double-click **`Launch NBA2K CV.bat`**. The app has separate tabs for Live Detection, Results, Updates, and Improve Model. It looks for `best.pt` in previous live-run reports and the known Downloads layout; **Auto Find** searches Downloads more broadly. You can paste a full model path into the field or use **Browse**. The app remembers the selected path. **Start Live Test** creates a fresh results folder each time. **Test Capture Only** measures the card without loading the model. Results shows each run's device and measured FPS, with a button to open its folder. The Improve Model tab requires human-corrected labels and distinct training/validation/test captures, as described below.

**Import to App** copies a selected trained `.pt` weight into `models/best.pt`. The app automatically finds that copy on later launches, and code updates leave `models/` intact. Replacing it keeps `models/best.previous.pt` as a backup. Select your existing `runs\meter_v2_gpu_1280-6\weights\best.pt` once, then click **Check Setup** to verify that the installed PyTorch and Ultralytics can load it. The app cannot build a reliable trained weight from code alone; training needs reviewed gameplay labels, separate validation and held-out test captures, and a suitable GPU. The **Improve Model** tab handles that workflow when those inputs exist.

The `.bat` launcher creates `.venv` if needed, installs detection dependencies if imports are missing, and starts the app. This build still requires Python for Windows; the launcher manages the project's environment but is not a standalone `.exe`. Existing working environments are reused, including a CUDA-capable PyTorch build. Check Setup reports CUDA and model loading. A future bundled Windows build would need separate testing on Windows and must still acquire the trained weight once.

The Updates tab checks a configured release feed at startup. **Install Update** downloads a new ZIP, checks its HTTPS source, version, size, and SHA-256 digest, validates archive contents, backs up code under `updates/backups`, installs it, and restarts. Model weights, `.venv`, settings, recordings, labels, and `test_results` stay in place. **Install Downloaded ZIP** remains as a fallback. The first desktop installation still requires extracting the provided update ZIP over your existing project folder once. `update_source.json` points to this repository's latest release feed. `app.py` contains the interface; `app_update.py` handles downloads and replacement; `run_live.py` and `testing/` remain usable directly or in a code editor such as VS Code.

To publish a release, upload `NBA2K_CV_Update.zip` to a stable HTTPS release URL, then generate the feed with `python testing\make_update_manifest.py --archive NBA2K_CV_Update.zip --download-url https://example.org/releases/NBA2K_CV_Update.zip --out update.json`. Host `update.json` at a stable HTTPS URL and put that URL in `update_source.json` for the initial installed build. For each later version, increment `app_version.json`, publish the new ZIP, and replace the feed JSON at the same URL. Never publish model weights, raw captures, or user results in the update package. An unconfigured feed cannot fetch updates by itself; the ZIP fallback remains functional.

Click **Check Setup** before a live run. It writes `setup_check.json` and checks the chosen model, installed CV dependencies, CUDA access, and model loading. Failures are shown in the app; unexpected UI errors are written to `app_errors.log`. This is a readiness check, not a speed or accuracy test. Before treating a build as a reliable daily-use app, verify on the Windows PC that setup passes, capture reaches the requested mode, the live preview and both recording modes work, the updater restores/restarts, and held-out labeled clips meet the shot tracking and fill accuracy targets. The Windows GUI and trained model cannot be executed in this cloud workspace. A standalone `.exe` requires a Windows build and testing of its bundled runtime; the current double-click launcher uses the project's virtual environment. PyInstaller cannot cross-build a Windows executable from Linux.

## GitHub release updates

The repository includes `.github/workflows/release.yml` and `scripts/build_release.py`. A `v<version>` tag runs the tests, builds a code-only ZIP from the updater's allowlist, inserts the repository feed URL, creates a SHA-256 manifest, and publishes both assets to GitHub Releases. The tag must match `app_version.json`. The feed URL is `https://github.com/OWNER/REPO/releases/latest/download/update.json`; the ZIP is `NBA2K_CV_Update.zip` in the same release. The repository and releases must be public for the app to fetch them without an account.

Open this folder in VS Code, connect the intended GitHub repository, review tracked files, and push the code. Do not commit model weights, captures, results, or credentials. For a release, increment `app_version.json`, commit, tag that commit `v<version>`, and push the tag. After GitHub Actions passes, install the first release ZIP once on the Windows PC; subsequent updates appear in the app's Updates tab. Existing installations with an empty feed need this one-time ZIP installation. The app downloads an offered update when **Install Update** is clicked, then verifies and applies it.

You can inspect a local package with `python scripts/build_release.py --repository OWNER/REPO --out NBA2K_CV_Update.zip`. The builder puts the real feed URL into the ZIP without changing the working tree. The repository connection and publishing must be completed before automatic downloads can work.

## Setup (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
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

Open the existing launcher, then use **Live Detection**. Model weights are selectable from the model dropdown (managed `models/` and project `runs/` weights plus the current choice). Browse adds an external weight. The environment dropdown lists the app runtime and project `.venv`; use **Browse Python** for another existing environment's `Scripts/python.exe`. Choose compute `0` to require CUDA GPU 0, `auto` for CUDA with CPU fallback, or `cpu`. **Check Setup** runs in the selected environment; an explicit CUDA selection fails rather than quietly falling back. Changing the runtime does not install CUDA or modify that environment. Use the existing CUDA-enabled environment verified on your RTX 5060 Ti.

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

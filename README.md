# NBA 2K shot-meter CV pipeline

This project focuses on meter detection, continuous per-frame tracking, and measurable validation. It takes a trained Ultralytics detector (`best.pt`) and gameplay MP4. Inference is performed on every decoded frame; no frame skipping is used. Keep this folder separate from the older scripts so the original project remains intact.

## Desktop app (Windows)

After installing the desktop update into this project folder, double-click **`Launch NBA2K CV.bat`**. The app has separate tabs for Live Detection, Results, Updates, and Improve Model. It looks for `best.pt` in previous live-run reports and the known Downloads layout; **Auto Find** searches Downloads more broadly. You can paste a full model path into the field or use **Browse**. The app remembers the selected path. **Start Live Test** creates a fresh results folder each time. **Test Capture Only** measures the card without loading the model. Results shows each run's device and measured FPS, with a button to open its folder. The Improve Model tab requires human-corrected labels and distinct training/validation/test captures, as described below.

The Updates tab checks a configured release feed at startup. **Install Update** downloads a new ZIP, checks its HTTPS source, version, size, and SHA-256 digest, validates archive contents, backs up code under `updates/backups`, installs it, and restarts. Model weights, `.venv`, settings, recordings, labels, and `test_results` stay in place. **Install Downloaded ZIP** remains as a fallback. The first desktop installation still requires extracting the provided update ZIP over your existing project folder once. The online feed is not active until a stable public HTTPS release host is connected and `update_source.json` receives its manifest URL; until then the tab says so. `app.py` contains the interface; `app_update.py` handles downloads and replacement; `run_live.py` and `testing/` remain usable directly or in a code editor such as VS Code.

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

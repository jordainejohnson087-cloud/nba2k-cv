# AI X release: Phase 1 diagnosis and verification (2026-10-26)

Baseline: GitHub main 999d2898cfae804554de9d383a1e211617976651. Work branch: fix/ai-release-lifecycle-20261026. Existing clean local checkouts matched this baseline. The user's Windows installation was not accessible and was not overwritten.

## Reproduced defects

1. The Gtuner UDP loop retained only its newest packet. An observed green followed by a newer non-green observation in the same callback erased the event. `test_green_survives_later_non_green_in_same_udp_drain` failed on the baseline.
2. Neutral event zero reset worker duplicate suppression. Replaying an already-issued event after neutral caused a second command. `test_neutral_does_not_replay_a_completed_event` failed on the baseline.
3. TriggerLatch keyed only on event number. A restarted detector's first event could reuse the previous run's number and be suppressed. The detector now supplies a run UUID, and both upstream latches and the receiver scope identities to the run.
4. The automatic path emitted the anonymous manual command once and marked it sent. It could not identify a dropped execution or associate a device result with an AI event. The manual test counter was insufficient evidence of an AI release.

These are reproduced software defects and evidence gaps, not a claim that every failure on the user's Xbox has been diagnosed. Missing/ambiguous green measurements, stale frames, arming state, device script mismatch and physical-console routing can independently prevent a shot.

## Production changes

Keep the original manual PowerShell `T2TEST` sender and three-byte `84,49,3` release unchanged. Automatic release uses command 3 in a distinct identified envelope: `84,50,3`, a positive 31-bit token (four big-endian bytes), and remaining lifetime (two big-endian bytes). Both paths use the existing release_x output clamp. The automatic envelope requires meter GPC 3.7 (capability bit 16384); old scripts cannot silently accept it. The worker suppresses the old native meter green-action bit.

Capture one pending observed event before draining the rest of UDP input. Later non-green video state does not erase it. Explicit disarm, detector restart, mode change or a manual command cancels it. Retired transport sessions are ignored. Neutral frames never reset event deduplication. The worker retries only the same token within the original deadline; the device never executes that token twice. No per-shot subprocess or fixed jumpshot delay is introduced.

Device result 1 is emitted after the output clamp has set X to zero; results 2/3/4 reject unarmed/stale, ineligible hold, or invalid lifetime respectively. Immediate replies supplement the existing 500 ms heartbeat. No Gtuner callback/FPS configuration changes are made. A positive result means the script applied its output; it does not prove delivery to Xbox or a correctly timed green shot.

The worker reports pending/sent followed by executed, rejected, canceled or timed_out. An acknowledgment arriving after a terminal timeout/cancellation is recorded separately as late_ack/device_result rather than replaying the event. A timeout means execution is unknown. A manual command never counts as an AI acknowledgment. Source expiration, missing heartbeat and mismatched capabilities are reported explicitly. The most recent event result is carried in status; terminal results are printed by Gtuner and surfaced through the Python backend log while connected.

## Timing policy and measurement limits

Phase 1 policy: release immediately for a fresh observed green event; reject an event that lacks the remaining 25 ms reserve; never refresh its deadline on repeated frames. Source age is computed at ingress from the existing PC capture timestamp, then deadlines/retries/ack intervals use the PC monotonic clock. Device timestamps are not subtracted from PC timestamps. `ack_latency_ms` measures receipt of the device's reply after the worker's first send, including reply/callback overhead; it is not one-way USB latency or Xbox response latency.

The baseline X path already ignored delay_ms for execution. Its 250 ms field validation is not a 250 ms jumpshot timer. This phase preserves stale-link and manual-hold protections; it does not claim predictive scheduling, full capture-to-console compensation, or removal of every legacy timing control. Those require Phase 2 measurements. Physics/animation timing, meter velocity and subpixel green measurements are not inferred from this patch.

## Validation performed / required

Automated regression coverage includes the two baseline failures, a new detector reusing event 1, stale deadline/duplicate behavior, missing acknowledgment, script mismatch, manual release while armed, device rejection after early physical release/disarm/link loss, original manual hold/release behavior, and two 100-event synthetic stress tests. The actual GPC main-body host shim checks X suppression across physical and AI-created holds and preservation of RT/movement. It is not the vendor GPC compiler or USB hardware.

After installing the update, Open Bridge Files, compile/install the exported titan_meter.gpc 3.7, select that slot, and restart the matching gtuner_meter.py. Keep the existing working Gtuner Python configuration. First repeat the known manual X hold/release test. In dry run, verify observed green and event IDs with no output. Then explicitly arm for three physical-X shots, recording raw video and the status/log for each. Expect one token per shot and a matching device execution result while physical X remains held. Confirm actual Xbox release visually; never infer it solely from the heartbeat.

Diagnostics: the app Output panel and test_results/backend/<session>/backend.log contain AI RELEASE STATUS lines while the host is active. Gtuner logs AI RELEASE RESULT terminal records. live_status.json/live_run.json expose bridge.release_result; green_frames.jsonl (when present in the run) records detector measurements independently. Use the actual run's filenames; no new live footage or labeled-video benchmark was generated here.

## Remaining phases

Phase 2: full timestamp instrumentation, per-shot prediction/scheduling with an explicit late policy, transport latency characterization and hardware execution tests.
Phase 3: additional verified backend adapters and automated setup; no account authentication or Remote Play added.
Phase 4: measured rendering/capture/inference/bridge rates and sustained Windows hardware testing; no 120 FPS claim.
Phase 5: dynamic ROI/green-window improvements and labeled full-shot benchmarks, isolated from controller output. No untrained replacement model or accuracy claim.

## Modified files

Production: `meter_green.py`, `bridge_service.py`, `bridge/gtuner_meter.py`, `bridge/titan_meter.gpc`, `live_link.py`, `app.py`, `app_version.json`.

Validation: `testing/test_release_lifecycle.py` (new), `testing/test_trigger_delivery.py`, `testing/test_green_bridge.py`, `testing/test_shot_mechanics.py`, `testing/test_powershell_console.py`, `testing/test_session_flow.py`, `testing/windows_smoke.py`.

Documentation: `README.md`, `docs/ai_release_phase1.md` (this file).

Local validation: 218 tests, passing with six platform-specific skips. The two new baseline reproduction tests failed before the fix. The GPC main-body tests passed after the final acknowledgment-format adjustment. Windows CI results must be checked independently; vendor GPC compilation and actual Titan/Xbox operation have not been performed here.

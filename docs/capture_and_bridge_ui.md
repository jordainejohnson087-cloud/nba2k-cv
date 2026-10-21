# Capture and Titan dashboard (2026102105)

Start Capture owns the card in a dedicated worker. It releases this app's running probe first, uses the existing per-source lease, and requests Windows Above Normal CPU priority. If Windows denies that scheduling hint, the worker retries once at normal priority. The session's capture_priority.json records the request and fallback; it does not prove the scheduler's effective priority or unique video frames. No realtime priority or external process termination is used.

Windows cannot transfer another program's exclusive capture handle to this app. Close its camera source first. Gtuner's CV worker should use Null input while this app captures the card. Capture and AI remain separate actions, and the stalled-driver watchdog remains active.

The Titan Control Center keeps USB detection, Connect/Reconnect, disconnect, and bridge status above three pages:

- Setup & runtime: wiring steps, matching scripts, shared controller profile, and Gtuner discovery.
- Scripts & slots: slot selection, automatic recall requests, script labels read from Gtuner, and active-slot confirmation.
- Detection calibration: green thresholds and existing guarded trigger controls.

USB presence, a fresh bridge echo, and console input are distinct. The UI explicitly leaves console output unverified. Programming the GPC still requires Gtuner; this change does not implement a vendor USB programming API.

The organizational reference is the official Helios Titan Bridge documentation: https://inputsense.com/products/helios/docs/plugins/output/titan-bridge/ . No proprietary code or assets were copied. Helios's documented device programming capabilities are not claimed by this app.

Local validation: 112 unit tests, 111 passed and one Windows-only skip; release archive built and all 18 packaged Python files compiled. Windows smoke now checks the dashboard pages and saves a Titan screenshot alongside the capture screenshot. Native Windows layout, hardware capture, and console behavior still require the Windows/hardware run.

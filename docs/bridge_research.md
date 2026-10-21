# Titan bridge and 2K CV research — October 21, 2026

## What the current app can verify

The Windows app now enumerates present USB devices through SetupAPI and reads driver status through Configuration Manager. Titan Two uses normal ID `2508:0032` and bootloader ID `2508:8032`. The app ignores stale/unplugged registrations and composite child interfaces. It reports missing hardware, bootloader mode, device/driver errors, and multiple units separately. It automatically retries the existing bridge when one started normal-mode device appears, with output disarmed. USB presence is not a GPC echo, installed-script check, or console-output test.

Native enumeration needs no extra driver package and does not open the device. The manufacturer manual says Windows installs the device drivers when the Titan is connected. Do not replace its driver with a guessed WinUSB/libusb driver to repair a stopped Gtuner worker.

## Two different hardware backends

| Path | Device owner | Script installation | Required runtime |
| --- | --- | --- | --- |
| This app today | Gtuner IV | Compile/install exported GPC in Gtuner | Gtuner CV worker running, using Null Video Input |
| Helios Titan Bridge | Helios hardware backend | Vendor documents compiling GPC and programming its exposed slots | Helios, its supported plugin, compatible runtime/account access |

Helios documents firmware/port reports and three exposed device slots. Its MCP interface can discover installed plugins and their live command schemas. It is a real integration route to investigate on a Windows installation, rather than guessing binary USB programming commands or copying a proprietary runtime. Our app does not currently include that backend, compiler, or a verified standalone Titan programmer. Neither a README nor USB enumeration supplies those capabilities.

Some other vendors explicitly say to close Gtuner because their own app opens the Titan directly. That advice must not be applied to this app's current Gtuner-backed transport, which needs its worker running. The capture card remains owned by our app; Gtuner must use Null Video Input. No claim of automatic slot installation or working console output is justified without a supported programmer and a physical test.

## Ethernet decision

Ethernet does not carry data in our local HDMI/USB capture → CUDA detector → loopback API → Gtuner → USB Titan path. Therefore a USB Ethernet adapter will not repair that bridge, speed up GPU inference, or raise unique capture frames per second. Wired networking is relevant to Remote Play and online network stability. Sony recommends trying a wired PC connection for Remote Play problems; Input Sense documents Remote Play as a local-network output option separate from its wired Titan option. An Ethernet adapter is useful only if that wired-network path is needed and the PC lacks a suitable working port. Do not buy one as a USB bridge fix.

## Detection and testing lessons

Helios separates one video source from CV consumers, provides dataset playback, and distinguishes requested controller output from the Titan's device report. Its troubleshooting guidance recommends checking source frame delivery before inference/output, avoiding multiple capture-card owners, checking SDR/HDR color consistency, and recalibrating thresholds after capture changes. Those fit this project's existing separate capture and AI controls and testing workflow.

The public TitanLabs README describes meter-region color scanning, fill measurement, configurable thresholds, and duplicate-event suppression. These are design descriptions, not independent accuracy/latency benchmarks. Its license is restrictive, so no implementation or protected model was copied. Priority remains labeled onset-to-final-state videos, per-frame misses and capture gaps, final-fill error, p95 frame age, and held-out validation across display/meter conditions.

## Access and evidence limits

Public Reddit discussions about Helios/2K Vision were reviewed as user anecdotes, not verified benchmarks. The public Input Sense Discord invitation was accessible, but private channels were not inspected and no server was joined or messaged. TikTok retrieval was blocked by robots.txt; no TikTok findings are claimed. No third-party executable or Discord attachment was downloaded or run. No public supported standalone Titan flashing SDK or complete programming protocol was verified in this research.

## Sources

- Manufacturer manual (USB IDs and automatic Windows driver installation): https://m.media-amazon.com/images/I/91A8FN3IOeS.pdf
- Microsoft SetupAPI, present-only enumeration: https://learn.microsoft.com/en-us/windows/win32/api/setupapi/nf-setupapi-setupdigetclassdevsw
- Microsoft device status/problem codes: https://learn.microsoft.com/en-us/windows/win32/api/cfgmgr32/nf-cfgmgr32-cm_get_devnode_status
- Helios Titan Bridge: https://inputsense.com/products/helios/docs/plugins/output/titan-bridge/
- Helios documented MCP interface: https://www.inputsense.com/products/helios/docs/api-reference/mcp/
- Helios capture and SDR/HDR guidance: https://inputsense.com/products/helios/docs/plugins/capture/
- Helios troubleshooting: https://www.inputsense.com/products/helios/docs/troubleshooting/
- Input Sense hardware/output choices: https://www.inputsense.com/getting-started/
- Sony Remote Play networking: https://www.playstation.com/en-us/support/games/ps4-remote-play-connection-troubleshooting/
- TitanLabs public README and license: https://github.com/devingwithmell/Titan-Labs---NBA2K26-CV/blob/main/README.md
- Public Reddit setup anecdote: https://www.reddit.com/r/TitanTwo/comments/1pmu0ia/if_u_wanna_be_plugged_on_2k_i_dont_sell_the_any/
- Public Discord invitation only: https://discord.com/invite/inputsense

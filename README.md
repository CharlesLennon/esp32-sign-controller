# esp32-sign-controller

A standalone ESP32-S3 button box that drives a **CoolLEDUX** BLE LED matrix
sign from a car — no phone, no app. Built as a real daily-use project (a
message board mounted in a car), and now also serves as a full worked
example of using **[coolledux-ble](https://github.com/CharlesLennon/coolledux-ble)**,
the general-purpose Python library this project's own protocol
implementation was extracted into.

![Snake game mid-play: green body, apples eaten, black background](docs_images/snake_game.jpg)

## How this project uses coolledux-ble

Everything protocol-level — LZSS compression, the custom CRC-32, BLE
envelope framing, font rendering, tiled full-screen animation, device
commands — lives in `coolledux-ble`, not here. `bake_messages_coolledux.py`
imports it and supplies only what's specific to *this* project:

- The actual message content (`MESSAGES`, `ANIMATIONS`, `SLOTS` — what this
  car's sign actually says)
- A couple of custom icons built on top of the library's generic
  `build_icon_idle_message_packets()` (an L-plate learner-driver icon, a
  waving-hand animation for "HI")
- A Snake game and a pixel-bounce test, built on the library's generic
  tiled-animation primitives
- The SD-card JSON export + flash-fallback C header generation that feeds
  the ESP32 firmware

If you want to build your own sign controller (or anything else that talks
to a CoolLEDUX sign) from scratch, start with `coolledux-ble` directly —
this repo is the "here's a real thing built with it" reference, including
the parts a library can't provide on its own: physical hardware, a rotary-
encoder menu UI, a WiFi web control panel, and the ESP32 firmware itself.

## What it does

- **No laptop needed after setup** — runs standalone in the car off a 5V USB
  supply. Messages live on an SD card as small JSON files (chunk bytes
  pre-compiled by `bake_messages_coolledux.py`); the ESP32 just reads a
  file, hex-decodes it, and streams it to BLE. A small flash-baked fallback
  covers the SD card being missing.
- **Three ways to control it**: a rotary-encoder knob + beeper menu (built
  into a 3D-printer-style 12864 LCD panel, so no screen-watching needed
  while driving), a web control panel served from the ESP32's own WiFi
  access point, or raw Serial commands for testing.
- **Full-screen, full-color, multi-frame animations**, not just scrolling
  text — proven with a playable Snake game running entirely on-sign after a
  single upload, zero further BLE traffic (see `coolledux-ble`'s README for
  how the tiling trick behind this works).

![8 tiled graffiti segments rendering a full-width rainbow stripe test](docs_images/tiling_8stripe.jpg)

## Hardware

- **ESP32-S3** (developed on a DevKitC-1 N16R8, 16MB flash / 8MB octal PSRAM)
- A 12864-style LCD panel with a built-in rotary encoder + piezo beeper (the
  kind used on 3D-printer control panels) — optional; the web UI and Serial
  interface work without it
- A microSD card + SPI breakout (or any board with one built in) — optional;
  falls back to a small built-in flash message table if absent
- The CoolLEDUX sign itself, obviously

Default pin mapping (edit the `CONFIG` section at the top of the `.ino` to
match your wiring):

| Function | Pins |
|---|---|
| Display (software SPI) | CLK=12, MOSI=11, CS=10, RST=9 |
| Rotary encoder | A=4, B=5, button=6 |
| Beeper | 7 |
| SD card (SPI) | CS=15, MOSI=16, MISO=17, SCK=18 |

## Getting started

1. **Find your sign's BLE MAC address and confirm it's CoolLEDUX**, not the
   older "CoolLEDX" protocol (a different, already-open-sourced project) —
   see `coolledux-ble`'s README for how to tell the difference.
2. **Arduino IDE setup**:
   - Board: ESP32S3 Dev Module, with **PSRAM = OPI PSRAM**, **Flash Size =
     16MB**, **Partition Scheme = Huge APP** (the WiFi web UI needs more
     program space than the default partition table allows).
   - Libraries (via Library Manager): `NimBLE-Arduino`, `U8g2` (olikraus),
     `AiEsp32RotaryEncoder` (Igor Antolic), `ArduinoJson` (Benoit Blanchon,
     v7.x).
3. **Python setup**: `pip install -r requirements.txt` (installs
   `coolledux-ble` straight from its GitHub repo).
4. **Set your sign's MAC address** in the `CONFIG` section of
   `esp32_sign_controller.ino` (`SIGN_MAC_ADDRESS`).
5. **Bake your messages**: edit the `MESSAGES`/`ANIMATIONS`/`SLOTS` lists in
   `bake_messages_coolledux.py`, then run it:
   ```
   python bake_messages_coolledux.py
   ```
   This writes `sd_export/messages/*.json` (copy the `messages` **folder**
   onto a FAT32 SD card's root) and `coolledx_fallback.h` (copy into the
   sketch folder, next to the `.ino`).
6. **Build the web UI** (optional, only if you changed `PROTOCOL.md` or
   `web_ui_template.html`):
   ```
   python build_web_ui.py
   ```
   Copy the resulting `web_ui.h` into the sketch folder too.
7. **Flash** `esp32_sign_controller.ino` via the Arduino IDE.
8. Connect to the ESP32's own WiFi AP (`SignController`, open network by
   default — see the security note below) and visit `http://192.168.4.1/`,
   or use the knob, or type commands into the Serial Monitor.

## Repo layout

```
esp32_sign_controller.ino    -- the firmware (knob menu, BLE send, WiFi/web UI)
bake_messages_coolledux.py   -- this project's message content, built on coolledux-ble
build_web_ui.py              -- generates web_ui.h from web_ui_template.html + PROTOCOL.md
web_ui_template.html         -- the web control panel's source (edit this, not web_ui.h)
PROTOCOL.md                  -- full reverse-engineered protocol reference (see also coolledux-ble)
docs_images/                 -- photos of real hardware tests referenced in PROTOCOL.md
requirements.txt             -- pulls in coolledux-ble
```

`coolledx_fallback.h`, `web_ui.h`, and the `sd_export`/`sd_card_files` folders
are build output — regenerate them with the two Python scripts rather than
expecting them in the repo (see `.gitignore`).

## Security note

The WiFi access point currently runs **open (no password)**. A WPA2-PSK
handshake bug (looks like a real esp32-arduino/ESP-IDF core issue — an open
network connects instantly, but the same AP with a password fails Android's
"problem authenticating the connection" check no matter what combination of
PMF/cipher/bandwidth settings are forced) meant password protection had to be
dropped rather than fixed. If you find the actual fix, `WIFI_AP_PASSWORD` is
still defined and ready to be wired back in — see the comment in
`setupWiFi()`.

## Status / disclaimer

This is a reverse-engineered protocol (see `coolledux-ble`) with no official
documentation or support from the sign's manufacturer. It's been tested
extensively against one real CoolLEDUX sign (64×16 pixels), but sign
firmware may vary between units/manufacturers. Use at your own risk,
particularly around anything that writes device settings (OTA update
commands are deliberately not implemented at all).

## License

MIT — see [LICENSE](LICENSE).

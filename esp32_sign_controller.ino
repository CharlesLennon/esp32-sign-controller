/*
  ESP32-S3 LED Sign Button Box — no laptop needed
  ----------------------------------------------
  Sends preset messages directly to a CoolLEDUX sign over Bluetooth Low
  Energy. Runs standalone in the car off a 5V USB power source — no
  computer required after setup.

  CONTROL SURFACES (2026-07-26): the LCD display, rotary encoder + click
  button, piezo beeper, the knob-driven menu tree, and the SD card have all
  been REMOVED from this sketch -- they're no longer wired up or used. The
  only two ways to control the sign now are:
    - The WiFi web UI (see "WEB CONTROL PANEL" below) -- send any slot,
      power/brightness/mirror/light color, raw hex, from a phone/laptop
      browser.
    - The Serial Monitor test interface (see handleSerialInput()) -- type a
      code, PWRON/PWROFF, BRIGHT:<0-255>, or RAWHEX:<hex>.
  Both were already fully working before this change and are unaffected by
  it. BLE itself is opt-in either way: it doesn't auto-connect at boot (see
  enableBLE()) -- trigger it via the web UI's "Connect to Sign" button, or by
  typing any code over Serial (which calls trySendCode(), same as the web UI).

  MESSAGES LIVE ENTIRELY IN FLASH NOW, NOT ON AN SD CARD. Every message/
  animation used to be compiled directly into this sketch as one big C byte
  array (coolledx_bytes.h), which meant flash usage grew with every message
  and updating content meant re-flashing the whole board -- that got fixed by
  an SD-card rework partway through this project (message JSON files read
  off a card at send time). With the SD card now removed entirely, messages
  are back to being flash-baked, but via a different, already-proven
  mechanism than the original one: bake_messages_coolledux.py's
  FALLBACK_TABLE / coolledx_fallback.h (see findFallback()/trySendCode()
  below) -- this was originally just a small safety-net table for when the
  SD card was missing, and turned out (after measuring actual compiled flash
  size, not just the JSON text-file size) to comfortably hold the full real
  message set with room to spare in the `huge_app` 3MB partition. There is
  now exactly ONE source for message content: FALLBACK_TABLE. Updating
  content means re-running bake_messages_coolledux.py (which regenerates
  coolledx_fallback.h) and reflashing -- there's no more "edit content
  without reflashing" path now that the SD card is gone, which is a real
  tradeoff of this simplification, accepted because the knob/LCD/SD hardware
  itself was the thing being removed.

  CONNECTION CONFIRMATION ON THE SIGN ITSELF: every time connectToSign()
  succeeds -- at boot, and again after any reconnect following a dropped
  link -- it automatically sends the "CONNECTED" message so you can SEE the
  BLE connection worked by looking at the sign's own LED matrix, not just the
  Serial Monitor. CONNECTED lives in FALLBACK_CODES (flash), so this
  confirmation always works. It's also sendable manually, same as any other
  code: type CONNECTED into the Serial Monitor, or POST /api/send?code=CONNECTED.

  The Send-to-Display slot list (slotTable[]/slotTableCount, used by the web
  UI's /api/slots) is built once at boot by loadSlotTable() -- a fixed,
  built-in 12-entry list (kept in sync with the SLOTS list in
  bake_messages_coolledux.py). This used to be one of two sources (an SD
  card's /messages/slots.json, with this list as the fallback) -- now that
  the SD card is gone, this fixed list is simply the only source.

  "Send to Display" slot 0 conventionally sends "BLANK" — a normal message
  whose text is a single space, so nothing lights up. This is a workaround,
  not a real clear/off command (none has been reverse engineered yet): it
  works because every message we send fully replaces whatever the sign was
  previously showing, same as any other button. See the project doc.

  Power on/off and brightness (cmd 0x05 and 0x04 in the protocol) are small
  enough to not need flash baking at all — built directly here in C (see
  buildEnvelope()/sendPower()/sendBrightness() below) and reachable from both
  the web UI and Serial: type PWRON, PWROFF, or BRIGHT:<0-255>.

  LOGGING: every log line is timestamped (ms since boot) and tagged by
  subsystem ([SYS] [BLE] [SEND] [RECV] [WEB] [AP]) specifically so a Serial
  Monitor capture from a failed hardware test can be read back and
  correlated after the fact. If something doesn't work, copy the whole
  Serial Monitor log rather than just describing the symptom.

  SETUP STEPS:
    1. Run bake_messages_coolledux.py (on any computer). It writes
       coolledx_fallback.h -- copy this into the sketch folder next to this
       .ino file (this is now the ONLY source of message content, see above).
    2. Run build_web_ui.py (needs bake_messages_coolledux.py and PROTOCOL.md
       in the same folder) -- writes web_ui.h, also copy into the sketch
       folder.
    3. Find your sign's Bluetooth MAC address with ble_sign_scanner.ino,
       and fill in SIGN_MAC_ADDRESS below.
    4. Flash this sketch to the ESP32-S3 (no display/encoder/beeper/SD wiring
       needed at all anymore -- just the ESP32-S3 board itself).

  WEB CONTROL PANEL (2026-07-25): the ESP32-S3 also hosts its own WiFi
  Access Point (see WIFI_AP_SSID/WIFI_AP_PASSWORD below) and a small web
  server -- this is ALWAYS up, unconditionally, which is what makes the box
  fully standalone in a car with no WiFi router nearby. Connect a phone/
  laptop to that WiFi network and visit http://192.168.4.1/ for a page with
  three tabs: **Control** (send any slot, power/brightness/mirror/light
  color, plus ambient light), **Raw Send** (type or pick an example raw hex
  command and fire it straight at the sign -- same mechanism as the Serial
  RAWHEX: command, useful for trying new protocol ideas without
  recompiling), and **Docs** (the full PROTOCOL.md API reference, rendered
  to static HTML at bake time -- see build_web_ui.py). The whole page
  (HTML/CSS/JS, no external requests since the AP has no internet access) is
  baked into flash via web_ui.h, generated by build_web_ui.py -- same
  "Python generates a C header, arduino-cli compiles it in" pattern as
  coolledx_fallback.h. Re-run build_web_ui.py and reflash whenever
  PROTOCOL.md or the web UI itself changes.

  AP-ONLY, no optional WiFi STA join (2026-07-25 -- removed, not just
  disabled). There used to be an optional feature here where the ESP32
  would ALSO join an existing network (e.g. home WiFi) in parallel with the
  AP, so the web UI was reachable at a normal LAN IP too. Taking the board
  away from home (STA target out of range) correlated with the AP dying
  after a brief working window, right when it was the only live control
  path available -- never conclusively root-caused, but disabled at the
  user's request rather than left running unconfirmed. The AP's actual
  connectivity problems that day turned out to be a separate, unrelated
  bug (a broken WPA2 handshake on the AP itself -- see the AP setup code
  further down for that story), so STA may never have been the real
  culprit. Given the choice between re-enabling it and removing it outright
  once mobile data covered the "internet while connected to the sign"
  need, the user chose to just remove it and reclaim the flash space.

  UNTESTED ASSUMPTION, flagged rather than silently hoped: the ESP32-S3 has
  a single 2.4GHz radio shared between WiFi and BLE via time-division
  coexistence. Running the WiFi AP (periodic beacons, HTTP request handling)
  alongside the BLE connection to the sign could in principle add latency/
  jitter to BLE chunk writes. CHUNK_DELAY_MS's existing margin should absorb
  minor jitter, but this hasn't been stress-tested (e.g. sending a big
  animation while a phone is actively loading the web page) -- first thing
  to watch if sends start failing/corrupting only after the web UI shipped.

  REQUIRES (all via Arduino IDE Library Manager, except WiFi/WebServer which
  ship with the ESP32 core):
    - "NimBLE-Arduino"
    - "ArduinoJson" by Benoit Blanchon, v7.x
    - coolledx_fallback.h + web_ui.h, both generated by Python scripts
      (bake_messages_coolledux.py and build_web_ui.py respectively) and
      placed in the same sketch folder as this .ino file
*/

#include <NimBLEDevice.h>
#include <ArduinoJson.h>
#include <WiFi.h>
#include <WebServer.h>
#include <esp_system.h>
#include "coolledx_fallback.h"
#include "web_ui.h"

// ---------------- CONFIG: edit these ----------------

static const char* SIGN_MAC_ADDRESS = "01:00:00:89:a8:09"; // CoolLEDUX, found via ble_sign_scanner.ino
static const char* SERVICE_UUID = "FFF0";
static const char* CHARACTERISTIC_UUID = "FFF1";

// WiFi Access Point for the web control panel -- the ESP32 ALWAYS hosts this
// network itself (no router needed); connect a phone/laptop to it and visit
// http://192.168.4.1/. This is what makes the box work standalone in a car
// with no WiFi router nearby.
//
// NOT PASSWORD-PROTECTED (2026-07-25) -- WIFI_AP_PASSWORD below is currently
// UNUSED. The WPA2-PSK handshake on this AP turned out to be broken
// (confirmed real: an open version connected instantly, disabling PMF
// didn't fix the password version, forcing WPA2-PSK/CCMP explicitly +
// 20MHz-only bandwidth didn't either) -- see setupWiFi() for the full
// story. Rather than keep chasing a probable esp32-arduino/IDF core bug,
// the AP just runs open instead (visible, not hidden -- hidden was tried
// too and made things worse, see setupWiFi()), which the user accepted as a
// reasonable tradeoff for a personal car-sign control panel. If a real WPA2
// fix is ever found, WIFI_AP_PASSWORD is still here, ready to be wired back
// in.
static const char* WIFI_AP_SSID = "SignController";
static const char* WIFI_AP_PASSWORD = "changeme123"; // unused while the AP runs open, see above

// CONFIRMED against real hardware (2026-07-24): the sign's BLE characteristic
// only supports write-WITHOUT-response (canWrite=0, canWriteNoResponse=1), so
// there is no BLE-level delivery guarantee for any chunk -- a write can be
// silently dropped. A fixed gap that's too short can lose chunks, which shows
// up as "only the first color in a frame renders, everything after it goes
// dark" (each frame is one LZSS-compressed stream, so a single dropped chunk
// corrupts everything after it once decompressed).
//
// HISTORY (same day, several iterations): tried waiting for the sign's notify
// after each chunk instead of a fixed delay, with a timeout+retry if no notify
// showed up. That made things WORSE -- direct testing showed the sign's notify
// is NOT a reliable per-chunk ack: chunks were reported as "no ack, retrying"
// over and over even though the sign had already received and rendered them,
// so the "retry" was really just re-sending already-successful data, which is
// almost certainly what caused truncated/wrong-region renders (duplicate
// chunk indices confusing the sign's own reassembly). Reverted to a plain
// write + fixed-delay pace, trusting writeValue()'s own return value as the
// only real failure signal; notify is still logged for visibility but no
// longer gates anything. See writeChunkReliable().
const unsigned long CHUNK_DELAY_MS = 60;       // gap after each chunk write -- lowered back down (2026-07-25)
                                                // now that the ack-retry mechanism (the actual cause of
                                                // dropped/corrupted chunks) is gone; 400ms was a band-aid
                                                // from when retries were duplicating chunk indices, not a
                                                // real BLE throughput requirement. Needed low for animation
                                                // frame rate -- a full 8-tile screen is ~22 packets/frame.
const unsigned long RECONNECT_RETRY_MS = 3000; // how often to retry a dropped connection
const unsigned long HEARTBEAT_MS = 10000;      // how often to log a [SYS] status line even when nothing else happens
const unsigned long AP_STATUS_MS = 3000;       // 2026-07-25, WiFi-AP-drops debugging: much tighter than
                                                // HEARTBEAT_MS so we can pin down exactly when/if the AP dies

// Upper bound on how many Send-to-Display slots loadSlotTable() can define.
// Just a RAM buffer size -- raise it freely if you want more slots.
const int MAX_SLOTS = 20;

// ------------------------------------------------------

// ---- Logging helpers ----
// Every log line is timestamped and tagged. logLine() prints a full line;
// logPart() prints the timestamp+tag prefix without a newline, for building
// up a line across several Serial.print() calls (e.g. a hex dump that
// follows some descriptive text on the same line).

void logLine(const String& msg) {
  Serial.print('[');
  Serial.print(millis());
  Serial.print("ms] ");
  Serial.println(msg);
}

void logPart(const String& msg) {
  Serial.print('[');
  Serial.print(millis());
  Serial.print("ms] ");
  Serial.print(msg);
}

void printHex(const uint8_t* data, size_t len, size_t maxBytes) {
  size_t shown = len < maxBytes ? len : maxBytes;
  for (size_t i = 0; i < shown; i++) {
    char buf[4];
    snprintf(buf, sizeof(buf), "%02X ", data[i]);
    Serial.print(buf);
  }
  if (len > maxBytes) {
    Serial.print("... (");
    Serial.print(len);
    Serial.print("B total)");
  }
}

void logHeap(const char* context) {
  logLine(String("[SYS] free heap: ") + ESP.getFreeHeap() + " bytes (" + context + ")");
}

// 2026-07-25, added for the WiFi-AP-drops investigation: a normal user-
// triggered reboot shows POWERON or SW; anything else here (especially
// BROWNOUT/PANIC/*_WDT) means the board silently crashed and restarted on
// its own at some point, which is a very different bug from "the AP is
// running fine but stopped broadcasting."
const char* resetReasonString(esp_reset_reason_t reason) {
  switch (reason) {
    case ESP_RST_POWERON:   return "POWERON (normal power-up)";
    case ESP_RST_SW:        return "SW (software/serial reset, e.g. RTS-pin or reset button)";
    case ESP_RST_PANIC:     return "PANIC (crash -- exception/assert)";
    case ESP_RST_INT_WDT:   return "INT_WDT (interrupt watchdog -- something blocked too long)";
    case ESP_RST_TASK_WDT:  return "TASK_WDT (task watchdog -- a task starved the scheduler)";
    case ESP_RST_WDT:       return "WDT (other watchdog)";
    case ESP_RST_BROWNOUT:  return "BROWNOUT (power dipped below threshold)";
    case ESP_RST_SDIO:      return "SDIO";
    case ESP_RST_USB:       return "USB (reset via native USB)";
    case ESP_RST_JTAG:      return "JTAG";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP wake";
    case ESP_RST_EXT:       return "EXT (external pin reset)";
    default:                return "UNKNOWN";
  }
}

NimBLEClient* pClient = nullptr;
NimBLERemoteCharacteristic* pChar = nullptr;
bool isConnected = false;
bool bleEnabled = false; // 2026-07-25: BLE no longer auto-starts at boot -- see enableBLE()
unsigned long lastReconnectAttempt = 0;
unsigned long lastHeartbeat = 0;
unsigned long lastApStatus = 0; // 2026-07-25, WiFi-AP-drops debugging
unsigned long connectAttempts = 0;

// Bumped by the notify callback every time the sign acks something. Used by
// writeChunkReliable() to detect "did THIS write get acked" without needing a
// full request/response matching scheme -- see that function's comment.
volatile unsigned long ackCount = 0;
unsigned long disconnectCount = 0;

WebServer server(80);

// ---- Slot table (built once at boot by loadSlotTable()) ----

struct SlotInfo {
  int slot;
  char code[12];
  char label[24];
};

SlotInfo slotTable[MAX_SLOTS];
int slotTableCount = 0;

char lastSentLabel[24] = "(none yet)";
char lastSentCode[24] = "";

// ---- Raw single-write commands: power on/off, brightness ----
// Power (cmd 0x05) and brightness (cmd 0x04) payloads are only 2 bytes, so
// unlike message content they don't need SD storage or the announce+LZSS+
// chunk pipeline -- just one small envelope, built here directly in C. This
// exact algorithm was cross-checked byte-for-byte against the verified
// Python reference (power_command_bytes()/brightness_command_bytes() in
// bake_messages_coolledux.py) for on/off and brightness 0,1,2,3,4,128,255
// (0,1,2,3 specifically exercise the escape-stuffing logic below) before
// ever being tried against real hardware.

bool appendStuffedByte(uint8_t b, uint8_t* out, size_t& pos, size_t outCap) {
  // convertData(): bytes 0x01-0x03 become 0x02 followed by (b ^ 0x04).
  if (b > 0 && b < 4) {
    if (pos + 2 > outCap) return false;
    out[pos++] = 0x02;
    out[pos++] = b ^ 0x04;
  } else {
    if (pos + 1 > outCap) return false;
    out[pos++] = b;
  }
  return true;
}

size_t buildEnvelope(const uint8_t* payload, size_t payloadLen, uint8_t* out, size_t outCap) {
  // getSendDataWithInfo(): 0x01 + escape(2-byte length + payload) + 0x03.
  // This is the same outer envelope every flash-baked message chunk already
  // has baked in by the Python script -- here we build it live in C instead,
  // for the two commands small enough not to need flash storage at all.
  size_t pos = 0;
  if (pos + 1 > outCap) return 0;
  out[pos++] = 0x01;
  if (!appendStuffedByte((uint8_t)((payloadLen >> 8) & 0xFF), out, pos, outCap)) return 0;
  if (!appendStuffedByte((uint8_t)(payloadLen & 0xFF), out, pos, outCap)) return 0;
  for (size_t i = 0; i < payloadLen; i++) {
    if (!appendStuffedByte(payload[i], out, pos, outCap)) return 0;
  }
  if (pos + 1 > outCap) return 0;
  out[pos++] = 0x03;
  return pos;
}

bool sendRawCommand(const String& label, const uint8_t* payload, size_t payloadLen) {
  if (!isConnected || pChar == nullptr) {
    logLine("[SEND] " + label + " skipped -- not connected");
    return false;
  }

  uint8_t envelope[16];
  size_t envLen = buildEnvelope(payload, payloadLen, envelope, sizeof(envelope));
  if (envLen == 0) {
    logLine("[SEND] " + label + " FAILED -- envelope didn't fit in buffer");
    return false;
  }

  logPart("[SEND] " + label + " payload=");
  printHex(payload, payloadLen, 16);
  Serial.print(" envelope(");
  Serial.print(envLen);
  Serial.print("B)=");
  printHex(envelope, envLen, 32);
  Serial.println();

  bool useResponse = pChar->canWrite();
  bool ok = pChar->writeValue(envelope, envLen, useResponse);
  if (!ok && useResponse && pChar->canWriteNoResponse()) {
    logLine("[SEND]   write-with-response failed, retrying without response...");
    ok = pChar->writeValue(envelope, envLen, false);
  }
  logLine(String("[SEND]   ") + (ok ? "OK" : "FAILED"));
  return ok;
}

void sendPower(bool on) {
  uint8_t payload[2] = { 0x05, (uint8_t)(on ? 1 : 0) };
  sendRawCommand(on ? "POWER ON" : "POWER OFF", payload, 2);
}

void sendBrightness(uint8_t level) {
  uint8_t payload[2] = { 0x04, level };
  sendRawCommand("BRIGHTNESS " + String(level), payload, 2);
}

// mirror_command_bytes() in bake_messages_coolledux.py -- cmd 0x0c + [01|00].
// CONFIRMED against real hardware (2026-07-25): a live, reversible
// horizontal mirror, applied instantly to whatever's already on screen.
void sendMirror(bool on) {
  uint8_t payload[2] = { 0x0c, (uint8_t)(on ? 1 : 0) };
  sendRawCommand(on ? "MIRROR ON" : "MIRROR OFF", payload, 2);
}

// rgb444_pixel_color()'s clamping "transfer" curve in
// bake_messages_coolledux.py -- used for graffiti/animation pixels AND the
// Light solid-color command below (NOT the same formula as text color).
uint8_t rgb444TransferChannel(uint8_t v) {
  if (v >= 238) return 15;
  if (v <= 47) return 0;
  return (uint8_t)(((v - 47) / 14) + 1);
}

// light_color_bytes() in bake_messages_coolledux.py -- cmd 0x13 0x01 + RGB444
// color(2B). CONFIRMED against real hardware (2026-07-25): an instant, full
// 64x16-width solid color fill -- a separate live/ambient mode from the
// stored-program pipeline, no tiling cap.
void sendLightColor(uint8_t r, uint8_t g, uint8_t b) {
  uint8_t r4 = rgb444TransferChannel(r);
  uint8_t g4 = rgb444TransferChannel(g);
  uint8_t b4 = rgb444TransferChannel(b);
  uint8_t payload[4] = { 0x13, 0x01, r4, (uint8_t)((g4 << 4) | b4) };
  sendRawCommand("LIGHT COLOR", payload, 4);
}

// ---- Connection handling ----

class ClientCallbacks : public NimBLEClientCallbacks {
  void onDisconnect(NimBLEClient* client, int reason) override {
    disconnectCount++;
    logLine("[BLE] Disconnected from sign. reason=" + String(reason) +
            " (disconnect #" + String(disconnectCount) + ")");
    isConnected = false;
  }
};

ClientCallbacks clientCB;

// 2026-07-25: BLE used to auto-start at boot alongside WiFi. Changed at the
// user's request while debugging WiFi connectivity issues -- the board now
// comes up WiFi-only, and this is called (via the web UI's "Connect to
// Sign" button, see handleApiConnectBle()) to turn BLE on for the first
// time. Idempotent -- safe to call again after BLE is already enabled,
// just re-triggers a connection attempt via connectToSign() rather than
// re-initializing NimBLE (which only needs to happen once).
void enableBLE() {
  if (!bleEnabled) {
    NimBLEDevice::init("");
    NimBLEDevice::setMTU(247); // request a large MTU so our 136-byte chunks fit
    bleEnabled = true;
    logLine("[BLE] NimBLE initialized, requested MTU=247 (enabled via web UI)");
  }
  connectToSign();
}

bool connectToSign() {
  connectAttempts++;
  logLine("[BLE] Connecting to sign (attempt #" + String(connectAttempts) + ")... target=" + SIGN_MAC_ADDRESS);

  if (pClient == nullptr) {
    pClient = NimBLEDevice::createClient();
    pClient->setClientCallbacks(&clientCB, false);
    logLine("[BLE] created NimBLE client");
  }

  NimBLEAddress addr(SIGN_MAC_ADDRESS, BLE_ADDR_PUBLIC);
  if (!pClient->connect(addr)) {
    logLine("[BLE] connect() FAILED.");
    return false;
  }
  logLine("[BLE] connect() ok, negotiating MTU...");

  // Our message chunks are up to 136 bytes each, but a fresh BLE connection
  // starts at the spec-minimum 23-byte MTU (~20 bytes usable). Explicitly
  // request a bigger MTU so writeValue() doesn't reject the chunks.
  bool mtuOk = pClient->exchangeMTU();
  logLine("[BLE] MTU exchange " + String(mtuOk ? "ok" : "failed") +
          ", negotiated MTU=" + String(pClient->getMTU()));

  NimBLERemoteService* pSvc = pClient->getService(SERVICE_UUID);
  if (pSvc == nullptr) {
    logLine("[BLE] Service " + String(SERVICE_UUID) + " NOT found on this device.");
    pClient->disconnect();
    return false;
  }
  logLine("[BLE] found service " + String(SERVICE_UUID));

  pChar = pSvc->getCharacteristic(CHARACTERISTIC_UUID);
  if (pChar == nullptr) {
    logLine("[BLE] Characteristic " + String(CHARACTERISTIC_UUID) + " NOT found.");
    pClient->disconnect();
    return false;
  }

  // Dump what this characteristic actually supports — this tells us whether
  // "write with response" is even valid here, which is the #1 suspect when
  // writeValue() fails instantly instead of timing out.
  logLine("[BLE] characteristic " + String(CHARACTERISTIC_UUID) +
          " properties: canWrite=" + String(pChar->canWrite()) +
          " canWriteNoResponse=" + String(pChar->canWriteNoResponse()) +
          " canNotify=" + String(pChar->canNotify()) +
          " canIndicate=" + String(pChar->canIndicate()));

  if (pChar->canNotify()) {
    bool subOk = pChar->subscribe(true, [](NimBLERemoteCharacteristic* c, uint8_t* data, size_t len, bool isNotify) {
      // Sign acknowledged a chunk/command. writeChunkReliable() waits on
      // ackCount advancing to know a write actually landed, instead of a
      // fixed delay -- see that function's comment for why (real-hardware
      // testing on 2026-07-24 showed the fixed-delay approach silently drops
      // chunks under load, especially across multiple back-to-back frames).
      ackCount++;
      logPart("[RECV] notify, " + String(len) + "B: ");
      printHex(data, len, 32);
      Serial.println();
    });
    logLine(String("[BLE] notify subscribe ") + (subOk ? "ok" : "FAILED"));
  } else {
    logLine("[BLE] characteristic does not support notify -- no response channel, sends are fire-and-forget.");
  }

  logLine("[BLE] Connected to sign.");
  isConnected = true;
  logHeap("after connect");

  // Send a visual confirmation straight to the sign's own LED matrix, so the
  // connection can be confirmed by looking at the sign itself, not just the
  // Serial Monitor log. Runs every time this function connects successfully
  // -- including reconnects after a drop, not only the very first boot --
  // so it also doubles as a live "we're back online" indicator any time the
  // link recovers. CONNECTED is in FALLBACK_CODES (see coolledx_fallback.h),
  // the only source of message content now that the SD card is gone.
  logLine("[BLE] Sending CONNECTED confirmation message to the sign...");
  trySendCode("CONNECTED", "CONNECTED");

  return true;
}

// ---- Sending a message: from the flash-baked fallback table ----

// Writes one chunk and paces after it. CONFIRMED against real hardware
// (2026-07-24, later same day than the ack-wait version this replaces): the
// sign's notify channel is NOT a reliable per-chunk ack. Direct testing showed
// every chunk of an otherwise-successful send reporting "no ack" and retrying
// (even with a 2500ms wait), while the sign visibly still received and
// rendered the data -- i.e. retrying on a missing ack was re-sending chunks
// that had already landed, which is almost certainly what caused the
// truncated/wrong-region renders we were chasing (duplicate chunk indices
// confusing the sign's own reassembly), not a real transmission failure.
//
// So: trust writeValue()'s own return value as the only signal of whether a
// write actually failed at the BLE layer (that's still meaningful -- it does
// fail instantly on a real error). Notify is logged for visibility only and
// never gates a retry. Pace with CHUNK_DELAY_MS like the original approach;
// see that constant's comment for the reliability history that led here.
bool writeChunkRaw(const uint8_t* data, size_t len, const String& label) {
  if (!isConnected || pChar == nullptr) return false;
  bool useResponse = pChar->canWrite();

  bool ok = pChar->writeValue(data, len, useResponse);
  if (!ok && useResponse && pChar->canWriteNoResponse()) {
    ok = pChar->writeValue(data, len, false);
  }
  if (!ok) {
    logLine("[SEND]     " + label + " write call itself failed, retrying once...");
    delay(100);
    ok = pChar->writeValue(data, len, false);
    if (!ok) {
      logLine("[SEND]     " + label + " FAILED (write call failed twice)");
      return false;
    }
  }
  return true;
}

bool writeChunkReliable(const uint8_t* data, size_t len, const String& label) {
  // Blocking write-then-pace, used by callers that send a small, fixed
  // burst of packets in one synchronous call where blocking loop() briefly
  // is fine (RAWHEX, the web UI's Raw Send playground -- see handleApiRaw()
  // and the Serial RAWHEX: handler). The big multi-chunk message/animation
  // sends do NOT use this -- see pumpMessageSend() below for why those
  // needed to stop blocking entirely (2026-07-25: so a NEW send request can
  // actually interrupt/override one still in progress, instead of queuing
  // behind it for however long it takes to finish -- up to 70+ seconds for
  // a big animation).
  bool ok = writeChunkRaw(data, len, label);
  if (ok) delay(CHUNK_DELAY_MS);
  return ok;
}

// ---- Non-blocking chunked BLE send state machine (2026-07-25) ----
//
// Sending a multi-chunk message/animation used to be one long blocking
// function call (loop through every chunk, write, delay, repeat -- up to
// 70+ seconds for a big animation like SNAKE). Because this whole sketch is
// single-threaded (loop() drives BOTH the web server and BLE writes), that
// meant a SECOND send request -- from the web UI or Serial --
// couldn't even be RECEIVED until the first one finished every chunk: back-
// to-back button clicks just queued up and played out in order, one full
// send at a time, instead of the newest click actually overriding what was
// mid-transmission. Requested explicitly: "click a few back to back, it
// should override whatever is currently being sent."
//
// Fix: trySendCode() now only RESOLVES the content source (the flash
// fallback table) and records it as `pendingSend`, returning immediately --
// it does not write any BLE data itself. loop() calls pumpMessageSend()
// every iteration, which writes AT MOST one chunk per call, gated by
// `pendingSend.nextChunkAtMs` so the pacing (CHUNK_DELAY_MS) is preserved
// without ever blocking. Because trySendCode() returns fast now, the web
// server can accept a brand new request within milliseconds of the previous
// one, even if that previous send's chunks are still trickling out --
// trySendCode() cancels `pendingSend` and replaces it wholesale before the
// old content's next chunk would have gone out, so the sign ends up
// receiving a clean, complete stream of the NEWEST request's chunks (the old
// one's partial chunks, if any were sent, just never get completed -- the
// sign's own chunk reassembly is keyed by each data chunk's declared total-
// length header, which differs per program, so an abandoned partial transfer
// doesn't corrupt the new one; this matches how the protocol already had to
// tolerate a dropped connection mid-send, just triggered deliberately now
// instead of by accident).
//
// (2026-07-26: this used to also support an SD-card content source
// alongside flash, with its own JsonDocument/hex-decode plumbing -- removed
// along with the rest of the SD card support. Flash is now the only source.)
enum SendSource { SEND_NONE, SEND_FLASH };

struct PendingSend {
  bool active = false;
  uint32_t generation = 0;
  SendSource source = SEND_NONE;
  char code[24] = "";
  size_t chunkIndex = 0;
  size_t totalChunks = 0;
  unsigned long nextChunkAtMs = 0;
  const MessageEntry* flashEntry = nullptr;
};

PendingSend pendingSend;
uint32_t sendGeneration = 0;

void cancelPendingSend() {
  pendingSend.active = false;
  pendingSend.source = SEND_NONE;
  pendingSend.chunkIndex = 0;
  pendingSend.totalChunks = 0;
  pendingSend.flashEntry = nullptr;
}

bool beginSendFlash(const MessageEntry& entry) {
  if (!isConnected || pChar == nullptr) {
    logLine("[SEND] " + String(entry.code) + " skipped -- not connected");
    return false;
  }
  logLine("[SEND] " + String(entry.code) + " (flash fallback) queued, " + String(entry.num_chunks) +
          " chunk(s), MTU=" + String(pClient->getMTU()));
  return true;
}

// Called every loop() iteration. Sends AT MOST one chunk, only once
// CHUNK_DELAY_MS has elapsed since the previous one -- this is what replaces
// the old per-chunk delay(CHUNK_DELAY_MS) blocking call, so loop() (and
// therefore server.handleClient(), everything else) stays responsive for
// the whole duration of a big send.
void pumpMessageSend() {
  if (!pendingSend.active) return;

  unsigned long now = millis();
  if (now < pendingSend.nextChunkAtMs) return; // not due yet

  if (!isConnected || pChar == nullptr) {
    logLine(String("[SEND] ") + pendingSend.code + " ABORTED -- connection lost mid-send");
    cancelPendingSend();
    return;
  }

  size_t i = pendingSend.chunkIndex;
  const uint8_t* data = pendingSend.flashEntry->chunks[i];
  size_t len = pendingSend.flashEntry->lengths[i];

  logPart("[SEND]   " + String(pendingSend.code) + " chunk " + String(i) + "/" + String(pendingSend.totalChunks - 1) +
          ", " + String(len) + "B, data=");
  printHex(data, len, 24);
  Serial.println();

  String label = String(pendingSend.code) + " chunk " + String(i);
  bool ok = writeChunkRaw(data, len, label);
  logLine(String("[SEND]     ") + (ok ? "ok" : "FAILED"));

  if (!ok) {
    logLine(String("[SEND] ") + pendingSend.code + " ABORTED at chunk " + String(i));
    cancelPendingSend();
    return;
  }

  pendingSend.chunkIndex++;
  pendingSend.nextChunkAtMs = now + CHUNK_DELAY_MS;

  if (pendingSend.chunkIndex >= pendingSend.totalChunks) {
    logLine(String("[SEND] ") + pendingSend.code + " done.");
    cancelPendingSend();
  }
}

// ---- Hex decoding (used by RAWHEX/api-raw hex passthrough and hex color params) ----

static inline bool isHexChar(char c) {
  return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}

static inline uint8_t hexNibble(char c) {
  if (c >= '0' && c <= '9') return (uint8_t)(c - '0');
  if (c >= 'a' && c <= 'f') return (uint8_t)(c - 'a' + 10);
  if (c >= 'A' && c <= 'F') return (uint8_t)(c - 'A' + 10);
  return 0; // unreachable if isHexChar() was checked first
}

// Decodes a hex string (no separators, e.g. "01000c02...") into out[]. Returns
// the number of bytes decoded, or 0 on ANY problem: odd-length string,
// invalid (non-hex) character, empty string, or too large for outCap.
// STRICT on purpose -- silently decoding garbled input as if it were valid
// bytes would send whatever-that-produces to the sign instead of failing
// cleanly, which is a much worse failure mode than just refusing to send.
size_t hexDecode(const char* hexStr, uint8_t* out, size_t outCap) {
  size_t hexLen = strlen(hexStr);
  if (hexLen == 0 || (hexLen % 2) != 0) return 0;
  size_t byteLen = hexLen / 2;
  if (byteLen > outCap) return 0;
  for (size_t i = 0; i < hexLen; i++) {
    if (!isHexChar(hexStr[i])) return 0;
  }
  for (size_t i = 0; i < byteLen; i++) {
    out[i] = (uint8_t)((hexNibble(hexStr[i * 2]) << 4) | hexNibble(hexStr[i * 2 + 1]));
  }
  return byteLen;
}

const MessageEntry* findFallback(const char* code) {
  for (size_t i = 0; i < FALLBACK_TABLE_SIZE; i++) {
    if (strcmp(FALLBACK_TABLE[i].code, code) == 0) {
      return &FALLBACK_TABLE[i];
    }
  }
  return nullptr;
}

// Cheap existence check -- does NOT read/decode/send anything, just answers
// "would trySendCode(code) actually find something." Used by the web UI's
// /api/slots so it can show only buttons that will really do something
// instead of every slot regardless of whether its content is reachable
// (the flash fallback table is the only content source now that the SD
// card is gone -- see the top-of-file comment).
bool hasContentFor(const char* code) {
  return findFallback(code) != nullptr;
}

// Looks up the code in the flash-baked FALLBACK_TABLE and updates
// lastSentLabel/lastSentCode either way. This is the single funnel both the
// web UI and the Serial test interface send through. Returns true only if
// content was actually found and QUEUED -- callers (notably the web UI's
// /api/send, see handleApiSend()) need this to report a REAL result instead
// of always claiming success, which previously masked "why isn't this
// button doing anything" for codes with no flash-fallback entry.
//
// Non-blocking (2026-07-25): this used to fully send the message before
// returning (up to 70+ seconds for a big animation). Now it only RESOLVES
// the content source and hands it to pumpMessageSend() to stream
// incrementally from loop() -- see the "Non-blocking chunked BLE send state
// machine" block above writeChunkRaw() for the full reasoning. The new
// content is only swapped into `pendingSend` (replacing/cancelling whatever
// was previously mid-send, if anything) once it's confirmed ready -- a
// request for a code with no real source leaves an already-in-progress send
// running rather than destroying it for nothing.
bool trySendCode(const char* code, const char* label) {
  bool queued = false;
  SendSource newSource = SEND_NONE;
  const MessageEntry* newFlashEntry = nullptr;
  size_t newTotalChunks = 0;

  const MessageEntry* fb = findFallback(code);
  if (fb != nullptr && beginSendFlash(*fb)) {
    queued = true;
    newSource = SEND_FLASH;
    newFlashEntry = fb;
    newTotalChunks = fb->num_chunks;
  }

  if (queued) {
    cancelPendingSend(); // now safe to replace -- the new content is ready
    pendingSend.source = newSource;
    pendingSend.flashEntry = newFlashEntry;
    pendingSend.totalChunks = newTotalChunks;
    pendingSend.chunkIndex = 0;
    pendingSend.active = true;
    sendGeneration++;
    pendingSend.generation = sendGeneration;
    pendingSend.nextChunkAtMs = millis(); // first chunk goes out on the very next loop() tick
    strncpy(pendingSend.code, code, sizeof(pendingSend.code) - 1);
    pendingSend.code[sizeof(pendingSend.code) - 1] = '\0';

    strncpy(lastSentLabel, label ? label : code, sizeof(lastSentLabel) - 1);
    strncpy(lastSentCode, code, sizeof(lastSentCode) - 1);
    lastSentCode[sizeof(lastSentCode) - 1] = '\0';
  } else {
    logLine(String("[SEND] ") + code + " has no source (not in flash fallback table) -- nothing sent.");
    strncpy(lastSentLabel, "(not set)", sizeof(lastSentLabel) - 1);
    // lastSentCode intentionally left alone on failure -- "now showing" should
    // keep reflecting whatever's actually still on the physical panel, not a
    // code that was never sent.
  }
  lastSentLabel[sizeof(lastSentLabel) - 1] = '\0';
  return queued;
}

// ---- Slot table ----

void loadSlotTable() {
  // Fixed, built-in list -- the only source of the Send-to-Display slot list
  // now that the SD card (which used to optionally supply /messages/
  // slots.json, with this list as the fallback) is gone. Kept in sync with
  // the SLOTS list in bake_messages_coolledux.py. Replaced 2026-07-25: the
  // original placeholder message set (MERGING/THANK YOU/SORRY!/STUDENT
  // DRIVER/two never-filled-in "MESSAGE 5"/"MESSAGE 6" slots) was swapped
  // for a curated set of real driving-safety messages, all flash-baked (see
  // FALLBACK_CODES). HONK (slot 7) added same day, filling the gap left by
  // the dropped "MERGING FLY-IN" demo. HI (slot 11) added as a simple
  // friendly greeting.
  static const char* defCodes[12]  = { "BLANK", "LPLATE", "MERGING", "SORRY", "THANKS", "PATIENCE", "SPACE", "HONK", "SNAKE", "LONGSNAKE", "BOUNCE", "HI" };
  static const char* defLabels[12] = { "BLANK", "L PLATE", "MERGING", "SORRY!", "THANK YOU", "PLEASE BE PATIENT", "KEEP YOUR DISTANCE", "DON'T HONK", "SNAKE GAME", "LONG SNAKE", "PIXEL BOUNCE", "HI" };
  static const int defSlots[12]    = { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 };

  slotTableCount = 12;
  for (int i = 0; i < 12; i++) {
    slotTable[i].slot = defSlots[i];
    strncpy(slotTable[i].code, defCodes[i], sizeof(slotTable[i].code) - 1);
    slotTable[i].code[sizeof(slotTable[i].code) - 1] = '\0';
    strncpy(slotTable[i].label, defLabels[i], sizeof(slotTable[i].label) - 1);
    slotTable[i].label[sizeof(slotTable[i].label) - 1] = '\0';
  }
  logLine("[SYS] Loaded built-in slot table (12 slots)");
}

// ---- Web control panel: WiFi AP + HTTP API ----
// The ESP32 hosts its own WiFi network (see WIFI_AP_SSID/WIFI_AP_PASSWORD)
// and serves one page (INDEX_HTML, baked into flash by build_web_ui.py --
// see web_ui.h) with three tabs: Control (send any slot, power/brightness/
// mirror/ambient light -- calls the exact same trySendCode()/sendPower()/
// sendBrightness()/sendMirror()/sendLightColor() functions the Serial
// interface uses, so there is exactly one code path for "make the sign do
// something" regardless of which UI triggered it), Raw Send (arbitrary hex
// straight to BLE, same mechanism as the Serial RAWHEX: command), and Docs
// (PROTOCOL.md, rendered to static HTML at bake time).
//
// All handlers are synchronous (the built-in WebServer, not an async one) --
// a request blocks loop() for its duration, same as any Serial-triggered
// send already does for a multi-chunk send. No new category of blocking
// behavior, just one more thing that can trigger it.

void sendJsonHeader() {
  server.sendHeader("Cache-Control", "no-store");
}

void handleRoot() {
  sendJsonHeader();
  server.send_P(200, "text/html", INDEX_HTML);
}

// Minimal JSON string escaping for the handful of fields we embed (labels/
// codes/log text) -- just enough to not produce broken JSON if a label ever
// contains a quote or backslash, not a general-purpose encoder.
String jsonEscape(const String& s) {
  String out;
  out.reserve(s.length() + 4);
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '"' || c == '\\') out += '\\';
    if (c == '\n') { out += "\\n"; continue; }
    out += c;
  }
  return out;
}

void handleApiStatus() {
  String json = "{";
  json += "\"bleEnabled\":" + String(bleEnabled ? "true" : "false") + ",";
  json += "\"connected\":" + String(isConnected ? "true" : "false") + ",";
  json += "\"lastSent\":\"" + jsonEscape(lastSentLabel) + "\",";
  json += "\"lastSentCode\":\"" + jsonEscape(lastSentCode) + "\",";
  json += "\"uptimeSec\":" + String(millis() / 1000) + ",";
  json += "\"freeHeap\":" + String(ESP.getFreeHeap()) + ",";
  json += "\"connectAttempts\":" + String(connectAttempts) + ",";
  json += "\"disconnects\":" + String(disconnectCount);
  json += "}";
  server.send(200, "application/json", json);
}

void handleApiSlots() {
  // Only returns slots that actually have content available right now (i.e.
  // it's in the flash fallback table) -- so the web UI shows just the
  // buttons that will really do something instead of a full list where some
  // silently failed on click. Add ?all=1 to get the complete list (including
  // unavailable ones) for debugging.
  bool showAll = server.hasArg("all") && server.arg("all") == "1";
  String json = "[";
  bool first = true;
  for (int i = 0; i < slotTableCount; i++) {
    bool available = hasContentFor(slotTable[i].code);
    if (!available && !showAll) continue;
    if (!first) json += ",";
    first = false;
    json += "{\"slot\":" + String(slotTable[i].slot) +
            ",\"code\":\"" + jsonEscape(slotTable[i].code) + "\"" +
            ",\"label\":\"" + jsonEscape(slotTable[i].label) + "\"" +
            ",\"available\":" + String(available ? "true" : "false") + "}";
  }
  json += "]";
  server.send(200, "application/json", json);
}

// 2026-07-25: the web UI's "Connect to Sign" button, since BLE no longer
// auto-starts at boot -- see enableBLE()/setup(). Fires the (idempotent)
// enableBLE() and reports back whatever isConnected is right after --
// connectToSign() itself may take a moment, so a "false" here doesn't
// necessarily mean failure, just that it hasn't completed by the time this
// response goes out; the web UI's normal status polling picks up the real
// outcome shortly after.
void handleApiConnectBle() {
  logLine("[WEB] /api/connectble requested");
  enableBLE();
  String json = "{\"bleEnabled\":" + String(bleEnabled ? "true" : "false") +
                ",\"connected\":" + String(isConnected ? "true" : "false") + "}";
  server.send(200, "application/json", json);
}

void handleApiSend() {
  if (!server.hasArg("code")) {
    server.send(400, "text/plain", "missing 'code' param");
    return;
  }
  String code = server.arg("code");
  logLine("[WEB] /api/send code=" + code);
  bool sent = trySendCode(code.c_str(), nullptr); // also updates lastSentLabel
  if (sent) {
    server.send(200, "text/plain", "ok");
  } else {
    // Real failure, not a fake "ok" -- e.g. this code isn't in the flash
    // fallback table. Distinct HTTP status (409/Conflict, not 200) so the
    // web UI can tell success from "nothing to send" instead of reporting
    // every click as successful regardless.
    server.send(409, "text/plain", String(code) + " has no source (not in the flash fallback table)");
  }
}

void handleApiPower() {
  bool on = server.arg("on") == "1";
  logLine("[WEB] /api/power on=" + String(on));
  sendPower(on);
  server.send(200, "text/plain", "ok");
}

void handleApiBrightness() {
  int level = constrain(server.arg("level").toInt(), 0, 255);
  logLine("[WEB] /api/brightness level=" + String(level));
  sendBrightness((uint8_t)level);
  server.send(200, "text/plain", "ok");
}

void handleApiMirror() {
  bool on = server.arg("on") == "1";
  logLine("[WEB] /api/mirror on=" + String(on));
  sendMirror(on);
  server.send(200, "text/plain", "ok");
}

void handleApiLight() {
  String hex = server.arg("hex");
  hex.replace("#", "");
  if (hex.length() != 6) {
    server.send(400, "text/plain", "expected 6 hex chars (RRGGBB)");
    return;
  }
  uint8_t rgb[3];
  if (hexDecode(hex.c_str(), rgb, 3) != 3) {
    server.send(400, "text/plain", "invalid hex color");
    return;
  }
  logLine("[WEB] /api/light hex=" + hex);
  sendLightColor(rgb[0], rgb[1], rgb[2]);
  server.send(200, "text/plain", "ok");
}

// Raw hex straight to BLE, one already-fully-framed packet per line -- same
// mechanism and buffer size as the Serial RAWHEX: command, just reachable
// from the web UI's "Raw Send" playground instead of a terminal. Multi-
// packet examples (e.g. the Frame border test) are multiple lines; each is
// hex-decoded and written with the same writeChunkReliable() pacing as
// every other multi-chunk send in this sketch.
void handleApiRaw() {
  if (!isConnected || pChar == nullptr) {
    server.send(200, "text/plain", "not connected to sign, nothing sent");
    return;
  }
  String body = server.arg("plain");
  logLine("[WEB] /api/raw, " + String(body.length()) + " chars of input");

  static uint8_t rawBuf[260]; // matches RAWHEX's buffer margin
  String report;
  int lineNum = 0;
  int start = 0;
  while (start < (int)body.length()) {
    int nl = body.indexOf('\n', start);
    String line = (nl == -1) ? body.substring(start) : body.substring(start, nl);
    line.trim();
    start = (nl == -1) ? body.length() : nl + 1;
    if (line.length() == 0) continue;
    lineNum++;

    size_t len = hexDecode(line.c_str(), rawBuf, sizeof(rawBuf));
    if (len == 0) {
      report += "line " + String(lineNum) + ": FAILED (malformed hex or too long)\n";
      continue;
    }
    bool ok = writeChunkReliable(rawBuf, len, "WEB-RAW line " + String(lineNum));
    report += "line " + String(lineNum) + ": " + String(len) + "B " + (ok ? "ok" : "FAILED") + "\n";
  }
  if (lineNum == 0) report = "no hex lines found in request body";
  server.send(200, "text/plain", report);
}

void handleApiNotFound() {
  server.send(404, "text/plain", "not found: " + server.uri());
}

// Fires asynchronously as the AP starts/stops/gets or loses a client --
// never called from setup()/loop() directly, never blocks anything.
// Registered unconditionally in setupWiFi(); a default case logs the raw
// event number so nothing is silently invisible even if it's a case not
// explicitly named below.
void onWiFiEvent(WiFiEvent_t event) {
  switch (event) {
    case ARDUINO_EVENT_WIFI_READY:
      logLine("[WEB] WiFi event: WIFI_READY");
      break;
    case ARDUINO_EVENT_WIFI_AP_START:
      logLine(String("[WEB] WiFi event: AP_START (mode=") + String((int)WiFi.getMode()) + ")");
      break;
    case ARDUINO_EVENT_WIFI_AP_STOP:
      logLine(String("[WEB] WiFi event: AP_STOP -- the AP just went down (mode=") +
              String((int)WiFi.getMode()) + "). THIS is the event we're hunting for.");
      break;
    case ARDUINO_EVENT_WIFI_AP_STACONNECTED:
      logLine("[WEB] WiFi event: AP_STACONNECTED -- a client (e.g. your phone) associated with the AP");
      break;
    case ARDUINO_EVENT_WIFI_AP_STADISCONNECTED:
      logLine("[WEB] WiFi event: AP_STADISCONNECTED -- a client left/dropped from the AP");
      break;
    case ARDUINO_EVENT_WIFI_AP_STAIPASSIGNED:
      logLine("[WEB] WiFi event: AP_STAIPASSIGNED -- gave a client an IP over DHCP");
      break;
    default:
      logLine("[WEB] WiFi event: (unhandled) id=" + String((int)event));
      break;
  }
}

void setupWiFi() {
  // AP-only -- always comes up regardless of anything else, which is what
  // makes the box work standalone in a car with no router nearby. No STA
  // join (removed 2026-07-25, see the WIFI_AP_SSID comment above and the
  // CLAUDE.md project doc for the full story of why).
  WiFi.onEvent(onWiFiEvent);
  WiFi.mode(WIFI_AP);

  // 2026-07-25: WiFi modem sleep disabled -- didn't end up being the fix for
  // the WPA2 handshake bug below, but harmless/still worth keeping (sleep
  // can add latency to AP responsiveness in general).
  WiFi.setSleep(false);

  // 2026-07-25: the WPA2-PSK handshake on this AP is broken -- confirmed via
  // a diagnostic open (no-password) AP, which connected instantly, then two
  // rounds of targeted fixes (disabling PMF; then also forcing WPA2-PSK/
  // CCMP explicitly + 20MHz-only bandwidth) that did NOT resolve it. Rather
  // than keep chasing what is likely a real esp32-arduino/IDF core bug, the
  // AP just runs open (no password) instead -- acceptable for a
  // personal-project car-sign control panel. If a real fix for the WPA2
  // handshake is ever found, this is where it'd go back to
  // WiFi.softAP(WIFI_AP_SSID, WIFI_AP_PASSWORD).
  //
  // NOT hidden, despite an earlier attempt at that (2026-07-25, briefly):
  // hidden was tried as an extra privacy nicety once the AP had to go
  // password-less, but it broke reconnection outright -- confirmed with a
  // clean test (WiFi brought up BEFORE BLE, boot blocked for 90s waiting
  // for a client with zero BLE running at all, to rule out radio
  // coexistence interference) that the phone still couldn't join a hidden
  // SSID even with the whole radio to itself. Reverted to visible, which is
  // the exact config that was already confirmed working earlier the same
  // day.
  bool apOk = WiFi.softAP(WIFI_AP_SSID);
  logLine(String("[WEB] WiFi AP '") + WIFI_AP_SSID + "' " + (apOk ? "started" : "FAILED TO START") +
          " (open), IP=" + WiFi.softAPIP().toString());
}

void setupWebServer() {
  server.on("/", HTTP_GET, handleRoot);
  server.on("/api/status", HTTP_GET, handleApiStatus);
  server.on("/api/slots", HTTP_GET, handleApiSlots);
  server.on("/api/connectble", HTTP_POST, handleApiConnectBle);
  server.on("/api/send", HTTP_POST, handleApiSend);
  server.on("/api/power", HTTP_POST, handleApiPower);
  server.on("/api/brightness", HTTP_POST, handleApiBrightness);
  server.on("/api/mirror", HTTP_POST, handleApiMirror);
  server.on("/api/light", HTTP_POST, handleApiLight);
  server.on("/api/raw", HTTP_POST, handleApiRaw);
  server.onNotFound(handleApiNotFound);
  server.begin();
  logLine("[WEB] HTTP server started on port 80. Visit http://" + WiFi.softAPIP().toString() + "/");
}

// ---- Serial test interface ----
// Type a command into the Serial Monitor and hit Enter:
//   BTN1..BTN9, BLANK, or any other code in the flash fallback table
//                             -- send that message, same as clicking it in
//                                the web UI
//   PWRON / PWROFF           -- power the sign on/off
//   BRIGHT:<0-255>           -- set brightness (e.g. BRIGHT:128)
// Handy for confirming the sign connection + message bytes work, and as a
// shortcut that bypasses the web UI entirely.

void handleSerialInput() {
  if (!Serial.available()) {
    return;
  }

  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) {
    return;
  }

  logLine("[SYS] Serial command: '" + line + "'");

  if (line.equalsIgnoreCase("PWRON")) {
    sendPower(true);
    return;
  }
  if (line.equalsIgnoreCase("PWROFF")) {
    sendPower(false);
    return;
  }
  if (line.startsWith("BRIGHT:") || line.startsWith("BRIGHT ")) {
    int level = line.substring(7).toInt();
    level = constrain(level, 0, 255);
    sendBrightness((uint8_t)level);
    return;
  }

  // RAWHEX:<hex> -- writes one already-fully-framed BLE chunk (envelope and
  // all) built on the PC by bake_messages_coolledux.py, straight to FFF1.
  // Added for protocol-hypothesis debugging (2026-07-24): lets us try new
  // color-encoding/segment-order variants by just re-running the Python
  // builder and piping hex over Serial, instead of recompiling/reflashing
  // the whole sketch for every hypothesis. Does not touch SD or the flash
  // fallback table -- purely a direct pass-through to the BLE characteristic.
  if (line.startsWith("RAWHEX:")) {
    if (!isConnected || pChar == nullptr) {
      logLine("[RAW] not connected, cannot send");
      return;
    }
    const char* hexStr = line.c_str() + 7;
    static uint8_t rawBuf[260]; // matches /api/raw's buffer margin
    size_t len = hexDecode(hexStr, rawBuf, sizeof(rawBuf));
    if (len == 0) {
      logLine("[RAW] hexDecode failed, empty, or too large for the buffer");
      return;
    }
    logPart("[RAW] sending " + String(len) + "B: ");
    printHex(rawBuf, len, 32);
    Serial.println();
    bool ok = writeChunkReliable(rawBuf, len, "RAWHEX");
    logLine(String("[RAW] ") + (ok ? "ok" : "FAILED"));
    return;
  }

  trySendCode(line.c_str(), nullptr);
}

// ---- Setup / loop ----

void setup() {
  Serial.begin(115200);
  delay(500);

  logLine("[SYS] ===== boot =====");
  logLine(String("[SYS] chip: ") + ESP.getChipModel() + " rev" + ESP.getChipRevision() +
          ", cores=" + ESP.getChipCores() + ", cpu=" + ESP.getCpuFreqMHz() + "MHz");
  logLine(String("[SYS] reset reason: ") + resetReasonString(esp_reset_reason()) +
          " -- if this ever reads BROWNOUT/PANIC/WDT instead of POWERON/SW after the sign "
          "has been running a while, that's a silent crash/reset, not the WiFi AP just "
          "going quiet on its own");
  logHeap("boot");

  loadSlotTable(); // builds slotTable[] from the fixed built-in list (see loadSlotTable())

  // 2026-07-25: boot is now WiFi-ONLY -- BLE no longer auto-starts here at
  // all. This followed a whole debugging arc (briefly reordering/blocking
  // boot on WiFi before BLE to rule out radio coexistence interference,
  // which came back a clean negative result -- see enableBLE()'s comment)
  // and the user's own conclusion from it: rather than have BLE running
  // unconditionally in the background as a variable to worry about, make it
  // fully opt-in. WiFi/the web UI comes up alone; BLE only turns on when
  // the user taps "Connect to Sign" in the web UI, which calls enableBLE()
  // via POST /api/connectble (see handleApiConnectBle()).
  setupWiFi();      // starts the WiFi AP
  setupWebServer(); // starts the HTTP server for the web control panel
  logLine("[SYS] BLE is OFF -- connect to WiFi and tap 'Connect to Sign' in the web UI to enable it.");

  logHeap("end of setup");
  logLine("[SYS] Ready.");
  logLine("[SYS] Over Serial: <any flash-baked code>, PWRON, PWROFF, BRIGHT:<0-255>, RAWHEX:<hex>");
  logLine(String("[SYS] Or over WiFi: connect to '") + WIFI_AP_SSID + "', visit http://" + WiFi.softAPIP().toString() + "/");
  // Flash fallback (FALLBACK_TABLE) is now the only source of message
  // content, so this is always logged unconditionally -- built from the
  // real table, not hand-copied, so it can't go stale as FALLBACK_CODES grows.
  {
    String codes = "";
    for (size_t i = 0; i < FALLBACK_TABLE_SIZE; i++) {
      if (i > 0) codes += ", ";
      codes += FALLBACK_TABLE[i].code;
    }
    logLine("[SYS] Flash-baked messages: " + codes);
  }
}

void loop() {
  unsigned long now = millis();

  server.handleClient(); // web UI: serves the page + handles /api/* requests, synchronous
  pumpMessageSend();      // advance any in-progress chunked BLE send by at most one chunk

  // Maintain connection -- only once BLE has been turned on (see enableBLE());
  // before that, isConnected just stays false and this is a no-op.
  if (bleEnabled && !isConnected && (now - lastReconnectAttempt) > RECONNECT_RETRY_MS) {
    lastReconnectAttempt = now;
    connectToSign();
  }

  // 2026-07-25, WiFi-AP-drops debugging: a much tighter-interval log than
  // the heartbeat below, specifically watching the AP's own state --
  // WiFi.getMode() (did it silently change away from including AP?),
  // softAPgetStationNum() (any client ever actually associate?), and the
  // AP IP (does it ever go blank/0.0.0.0, which would mean the AP struct
  // itself got torn down). Meant to be temporary/verbose on purpose --
  // remove or slow back down once the AP-drop mystery is solved.
  if (now - lastApStatus > AP_STATUS_MS) {
    lastApStatus = now;
    wifi_mode_t mode = WiFi.getMode();
    logLine("[AP] t=" + String(now / 1000) + "s mode=" + String((int)mode) +
            " (0=OFF,1=STA,2=AP,3=AP_STA) apIP=" + WiFi.softAPIP().toString() +
            " stations=" + String(WiFi.softAPgetStationNum()) +
            " freeHeap=" + String(ESP.getFreeHeap()));
  }

  // Periodic heartbeat so a Serial Monitor capture shows the sketch is
  // still alive (not hung) even during long stretches with no button
  // presses or connection changes.
  if (now - lastHeartbeat > HEARTBEAT_MS) {
    lastHeartbeat = now;
    logLine("[SYS] heartbeat: uptime=" + String(now / 1000) + "s bleEnabled=" + String(bleEnabled) +
            " connected=" + String(isConnected) +
            " connectAttempts=" + String(connectAttempts) +
            " disconnects=" + String(disconnectCount) + " freeHeap=" + String(ESP.getFreeHeap()) +
            " wifiAP=" + WiFi.softAPIP().toString());
  }

  handleSerialInput();
}

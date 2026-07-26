#!/usr/bin/env python3
"""
bake_messages_coolledux.py
---------------------------
Replacement for bake_messages.py, built from scratch by reverse-engineering the
REAL CoolLEDUX "advanced protocol" out of the official Android app
(com.jtkj.led1248), since the open-source coolledx-driver library only supports
the older "simple protocol" (CoolLEDX generation) and silently produces bytes
the sign's firmware doesn't understand.

This script is fully self-contained: no coolledx-driver dependency, no PIL, no
external font files. The exact glyph bitmaps for the characters our messages use
were extracted directly from the app's own bundled font asset
(assets/flutter_assets/assets/resources/coolledux/font_library/unicode_16_bold)
and are embedded below as GLYPHS.

Protocol summary (see project doc "coolledux-protocol-findings.md" for the full
writeup) — reconstructed from com.jtkj.led1248.light.utils.CoolledUXUtils and
FontUtils via jadx decompilation:

  1. Build the "program" bytes: 8 zero bytes, content-count byte, zero byte,
     then per combine-program data. For a solid-color text message that's a
     "custom color" segment (per-run width + RGB444 color) followed by a
     "content" segment (the actual monochrome glyph-column bitmap).
  2. Compute CRC-32 (custom variant: poly 0x04C11DB7, init 0xFFFFFFFF, MSB
     first, NO final complement) and length of those uncompressed program
     bytes -> the "announce" packet (cmd 0x02).
  3. LZSS-compress (classic Okumura algorithm, window=512, lookahead=18,
     threshold=2) the program bytes -> chunk into <=UX_PACKAGE_SIZE pieces,
     each with its own header/index/checksum -> "data" packets (cmd 0x03).
  4. Every packet (announce + each data chunk) is wrapped:
     0x01 + [2-byte length + payload, with bytes 0x01-0x03 escaped as
     0x02 followed by (byte XOR 0x04)] + 0x03.

Output (two things, since the SD-card rework):
  1. ./sd_export/messages/*.json + ./sd_export/messages/slots.json -- copy the
     "messages" FOLDER ITSELF onto the ROOT of a FAT32 SD card, so the card
     ends up with /messages/BTN1.json, /messages/slots.json, etc. This is
     where the full message/animation set lives now, so the ESP32's flash
     usage stays small and constant no matter how many messages you add --
     update messages by re-running this script and swapping files on the
     card, no reflash needed. JSON (hex-encoded chunk bytes) instead of raw
     binary specifically so the files are human-readable/diffable, and so
     they could be served as-is by a future on-ESP32 web page (see the TODO
     list in the project doc) without a translation step.
  2. coolledx_fallback.h -- a MINIMAL flash-baked fallback (just BLANK + one
     emergency message) copied into the ESP32 sketch folder as before, used
     only if the SD card is missing/unreadable at boot.

ASSUMPTIONS worth knowing about (best-effort from decompiled/obfuscated code —
first real-hardware test will confirm or reveal issues to tune):
  - Treats each whole message as ONE "text run" of a single solid color
    (matches our use case: one fixed color per button).
  - Inserts a 1-empty-column gap between characters (textSpacing=1).
  - Gives the space character a 4-column-wide blank gap.
  - showWidth/showHeight are set to the sign's physical dimensions (64x16);
    the glyph bitmap stream itself can be wider (the sign scrolls it).
"""

import json
import os
import struct

from coolledux import wire, colors, fonts, content, commands

# from coolledux.wire
crc32_custom = wire.crc32_custom
lzss_compress = wire.lzss_compress
lzss_compress_safe = wire.lzss_compress_safe
escape_stuff = wire.escape_stuff
unescape = wire.unescape
send_data_with_info = wire.send_data_with_info
decode_envelope = wire.decode_envelope
xor_all = wire.xor_all
get_data_packet = wire.get_data_packet
get_start_data_for_program = wire.get_start_data_for_program

# from coolledux.colors
rgb444_text_color = colors.rgb444_text_color
rgb444_pixel_color = colors.rgb444_pixel_color
PIXEL_OFF_COLOR = colors.PIXEL_OFF_COLOR
ANIMATION_OFF_COLOR = colors.ANIMATION_OFF_COLOR

# from coolledux.fonts
GLYPHS = fonts.GLYPHS
SMALL_GLYPHS = fonts.SMALL_GLYPHS
SPACE_WIDTH = fonts.SPACE_WIDTH
CHAR_GAP = fonts.CHAR_GAP
SMALL_SPACE_WIDTH = fonts.SMALL_SPACE_WIDTH
SMALL_CHAR_GAP = fonts.SMALL_CHAR_GAP
build_glyph_columns = fonts.build_glyph_columns
build_small_glyph_columns = fonts.build_small_glyph_columns
build_glyph_stream = fonts.build_glyph_stream

# from coolledux.content (SIGN_HEIGHT/SIGN_WIDTH/UX_PACKAGE_SIZE stay defined
# locally below as this project's own editable CONFIG constants, not
# delegated to the library -- it just happens to default to the same values)
render_animation_frame = content.render_animation_frame
render_pixel_grid = content.render_pixel_grid
render_static_text_frame = content.render_static_text_frame
render_message_frame = content.render_message_frame
get_data_with_graffiti_content = content.get_data_with_graffiti_content
build_graffiti_program_bytes = content.build_graffiti_program_bytes
build_graffiti_message_packets = content.build_graffiti_message_packets
build_tiled_graffiti_program_bytes = content.build_tiled_graffiti_program_bytes
build_tiled_graffiti_message_packets = content.build_tiled_graffiti_message_packets
get_data_with_pixel_animation_content = content.get_data_with_pixel_animation_content
build_tiled_pixel_animation_program_bytes = content.build_tiled_pixel_animation_program_bytes
build_tiled_pixel_animation_message_packets = content.build_tiled_pixel_animation_message_packets
build_static_text_message_packets = content.build_static_text_message_packets
build_tiled_static_text_message_packets = content.build_tiled_static_text_message_packets
build_tiled_scrolling_text_message_packets = content.build_tiled_scrolling_text_message_packets
get_data_with_flyin_animation_content = content.get_data_with_flyin_animation_content
build_flyin_program_bytes = content.build_flyin_program_bytes
build_flyin_message_packets = content.build_flyin_message_packets
build_tiled_flyin_message_packets = content.build_tiled_flyin_message_packets
build_icon_scroll_message_packets = content.build_icon_scroll_message_packets
build_icon_idle_message_packets = content.build_icon_idle_message_packets
get_data_with_gif_content = content.get_data_with_gif_content
build_gif_message_packets = content.build_gif_message_packets
frame_border_bytes = content.frame_border_bytes

# from coolledux.commands
power_command_bytes = commands.power_command_bytes
brightness_command_bytes = commands.brightness_command_bytes
mirror_command_bytes = commands.mirror_command_bytes
rotate_command_bytes = commands.rotate_command_bytes
device_info_query_bytes = commands.device_info_query_bytes
synchronize_time_command_bytes = commands.synchronize_time_command_bytes
timer_switch_query_bytes = commands.timer_switch_query_bytes
timer_switch_set_bytes = commands.timer_switch_set_bytes
countdown_query_bytes = commands.countdown_query_bytes
countdown_set_bytes = commands.countdown_set_bytes
countdown_start_stop_bytes = commands.countdown_start_stop_bytes
stopwatch_query_bytes = commands.stopwatch_query_bytes
stopwatch_reset_bytes = commands.stopwatch_reset_bytes
stopwatch_start_stop_bytes = commands.stopwatch_start_stop_bytes
scoreboard_query_bytes = commands.scoreboard_query_bytes
scoreboard_set_score_bytes = commands.scoreboard_set_score_bytes
scoreboard_set_clock_bytes = commands.scoreboard_set_clock_bytes
scoreboard_start_stop_bytes = commands.scoreboard_start_stop_bytes
password_check_bytes = commands.password_check_bytes
password_set_bytes = commands.password_set_bytes
light_color_bytes = commands.light_color_bytes
light_speed_bytes = commands.light_speed_bytes
light_color_mode_bytes = commands.light_color_mode_bytes
set_device_info_bytes = commands.set_device_info_bytes

# ---------------- CONFIG: edit these ----------------

SIGN_HEIGHT = 16
SIGN_WIDTH = 64
UX_PACKAGE_SIZE = 128  # BLE write chunk size; comfortably under MTU 247 - ATT overhead

# Each entry: (code_name, text, hex_color, mode, speed, stayTime)
# mode values come from the app's own "coolledu_mode_string" resource array:
#   0=Static 1=Shift left continuously 2=Shift right continuously 3=Up 4=Down
#   5=Accumulation 6=Picture scroll 7=Flashing 8=Pan left 9=Pan right
#   10=Override left 11=Override right 12=Horizontal interspersed
# (earlier notes guessed mode=2 meant "left-scroll" -- it's actually "shift
# right continuously" per this array; not yet confirmed against real hardware
# either way, but this is what the app's own UI labels it as).
# REAL, user-facing text messages -- (code_name, text, hex_color). Built via
# build_tiled_static_text_message_packets() / build_tiled_scrolling_text_message_packets()
# in main() (auto-selected by measured glyph width vs SIGN_WIDTH), NOT the
# legacy text-content+color-segment mechanism below (LEGACY_DEBUG_MESSAGES) --
# that mechanism was root-caused (2026-07-24) to never apply color at all
# (always rendered a plain white block), and even the graffiti-based fix that
# superseded it (build_static_text_message_packets(), written 2026-07-24) was
# never actually wired into main()'s real message pipeline until now
# (2026-07-25) -- confirmed on real hardware that EVERY message below had
# been silently broken (either solid white, or -- after the graffiti fix
# existed but still wasn't tiled -- truncated to ~8 columns of glyph data)
# for the entire life of this project until this fix. See the "Text messages
# were never actually fixed" project note for the full story. No more `mode`/
# `speed`/`stay_time` fields -- those were specific to the abandoned
# text-content mechanism and don't apply to the graffiti/animation path.
# Real driving-safety message set (2026-07-25) -- replaces the original
# placeholder BTN1-6 buttons (a couple of which, "MESSAGE 5"/"MESSAGE 6",
# were never even given real content) with a small curated set actually
# meant to be shown to other drivers while this thing is mounted in a car.
# "STUDENT DRIVER" was dropped as a separate message -- superseded by the
# new LPLATE icon+scroll message below, which communicates the same thing
# more clearly (an actual L-plate icon, not just text). MERGING/SORRY!/
# THANK YOU/PATIENCE/SPACE all moved out of this plain list and into
# L_PLATE_SEAMLESS_MESSAGES further down (the "L-plate idle, message pushes
# it out of the way, L-plate returns" animation) -- explicit follow-up ask:
# "for this case yes I want them all to be seamless with some cool
# animations." Only BLANK is left here now -- it's the one thing that
# genuinely should NOT idle-animate (it exists specifically to go dark).
MESSAGES = [
    # "Blank the sign" -- not a real clear/off command (we haven't reverse
    # engineered one), just a normal text message whose content is a single
    # space character. A space has zero lit columns, so nothing is drawn;
    # since every message we send fully replaces whatever was showing before
    # (same as every other button), this has the same practical effect as
    # clearing the display. Color is irrelevant since nothing lights up.
    ("BLANK", " ", "#000000"),
    # System message, not a Send-to-Display slot: sent automatically by the
    # firmware every time connectToSign() succeeds (see connectToSign() in
    # esp32_sign_controller.ino), so you can visually confirm the BLE
    # connection worked by looking at the sign itself instead of only the
    # Serial Monitor log. Also included in FALLBACK_CODES below so this still
    # works even if the SD card is missing/unreadable -- exactly the
    # first-hardware-bringup scenario where this confirmation matters most.
    # Still sendable manually too (knob Serial command "CONNECTED", or a
    # future slot) since trySendCode() takes any code by name.
    #
    # Text is "CONNECT", not "CONNECTED" -- deliberately: "CONNECTED" is 71
    # columns wide (exceeds SIGN_WIDTH=64), which would make this a SCROLLING
    # message (~24 frames, ~440 packets, ~50s to transmit) -- a bad fit for a
    # connection-confirmation that fires automatically on every single BLE
    # reconnect and is also baked into the flash fallback (FALLBACK_CODES
    # below) for when the SD card is missing. "CONNECT" (55 cols) fits within
    # SIGN_WIDTH, so it's a single static frame -- fast (a few packets) and
    # small in flash, while still clearly conveying the same thing. The CODE
    # name stays "CONNECTED" (firmware calls trySendCode("CONNECTED", ...)
    # unchanged) -- only the displayed text changed.
    ("CONNECTED", "CONNECT", "#00FF00"),
]

# --- Legacy hardware-debug test messages (2026-07-24), KEPT INTENTIONALLY
# BROKEN for historical/diagnostic reference -- these deliberately still use
# the original text-content+color-segment mechanism (build_message_packets(),
# NOT the graffiti/animation path real MESSAGES use above) specifically
# because that's the mechanism they were built to diagnose. Do not "fix"
# these by routing them through the new tiled functions -- that would defeat
# their purpose. Not in SLOTS (not knob-reachable), only sendable by typing
# their code into Serial, for anyone who wants to re-confirm the original bug
# is still exactly as documented on the (now abandoned) text-content path.
#
# CONFIRMED (2026-07-24, clean single-boot test, one command at a time): ALL
# FOUR of CONNECTED/TESTSTATIC/TESTSHORT/TESTSWAPWH rendered as a plain WHITE
# block -- none of their distinct intended colors (green/yellow/cyan/magenta)
# showed up, just white pixels in a shape/position that varied with each
# message's text/dimensions. That ruled out scroll-vs-static and width/height
# field order as the bug (either would still show the RIGHT color in the
# WRONG place) and pointed to the color segment not being applied at all.
LEGACY_DEBUG_MESSAGES = [
    ("TESTSTATIC", "CONNECTED", "#FFFF00", 0, 40, 3),
    ("TESTSHORT", "HI", "#00FFFF", 2, 40, 3),
]

# TESTSWAPWH isn't a plain MESSAGES tuple because it needs the width/height
# override -- see the comment block above. (code_name, text, hex_color, mode,
# speed, stay_time, width, height).
TESTSWAPWH_ENTRY = ("TESTSWAPWH", "HI", "#FF00FF", 0, 40, 3, SIGN_HEIGHT, SIGN_WIDTH)

# TESTORDERSWAP (violet #8000FF): same as TESTSTATIC/TESTSHORT's mechanism
# (the "custom color text" program: a separate color-run segment + a glyph
# bitmap segment), but with the two segments concatenated in the OPPOSITE
# order (content segment first, then color segment) -- tests whether the
# color-then-content order documented from the decompile is actually
# backwards. (code_name, text, hex_color, mode, speed, stay_time, color_first)
TESTORDERSWAP_ENTRY = ("TESTORDERSWAP", "HI", "#8000FF", 0, 40, 3, False)

# "Dynamic Text" effects like the app's "Fly In" turn out to not be a separate
# wire-protocol feature -- the app renders the effect into ordinary animation
# frames on-device (DptLoadImage) and sends it through the SAME raw-pixel
# animation content type used by hand-drawn animations/graffiti. We do the
# same thing here: render the text into frames ourselves (reusing our
# existing font glyph data) and encode via get_data_with_flyin_animation_content.
#
# UNTESTED against real hardware -- in particular whether the sign loops an
# animation program after it finishes, or just stops on the last frame. We
# pad with `hold_frames` repeats of the resting (fully arrived) frame so the
# text stays visible for a while either way; tune once we can watch it run.
#
# Each entry: (code_name, text, hex_color, step, frame_delay_ms, hold_frames, hold_delay_ms)
#   step           = columns the text moves per frame (bigger = fewer frames, faster/choppier)
#   frame_delay_ms = how long each in-motion frame is held
#   hold_frames    = how many extra copies of the final "arrived" frame to send
#   hold_delay_ms  = how long each hold frame is held
# Real, user-facing fly-in animation(s) -- built via
# build_tiled_flyin_message_packets() in main() (tiled, genuine black
# background). (code_name, text, hex_color, step, frame_delay_ms,
# hold_frames, hold_delay_ms).
#
# Empty as of 2026-07-25 -- the old "MERGING FLY-IN" demo (BTN7) was dropped
# as part of replacing the placeholder message set with real driving-safety
# content; LPLATE (see ICON_SCROLL_MESSAGES, defined further down near
# render_l_plate_icon_pixel_fn/build_icon_scroll_message_packets since it
# needs those functions already defined) is the new animated flagship
# message instead of a demo effect on top of an existing message.
ANIMATIONS = []

# --- Legacy hardware-debug animation (2026-07-24), KEPT INTENTIONALLY
# BROKEN for historical/diagnostic reference -- see LEGACY_DEBUG_MESSAGES
# above for the same pattern/reasoning. Built via the original
# build_flyin_message_packets() (single UNTILED segment) specifically
# because that's the mechanism it was built to diagnose (whether the
# raw-pixel animation path applies color at all, as opposed to the broken
# text color-segment path) -- not knob-reachable, Serial-only.
#
# TESTANIM (orange #FF8000): every "custom color text" message tested back
# on 2026-07-24 (CONNECTED/TESTSTATIC/TESTSHORT/TESTSWAPWH) rendered plain
# WHITE regardless of intended color. Animations/graffiti use a completely
# different mechanism (color baked directly into each pixel of the raw frame
# data, no separate color segment) -- TESTANIM confirmed that path DOES
# apply color correctly (came out orange, not white), which is what led to
# the graffiti/animation-based fix used everywhere real content is built
# today. step is set to more than show_width so the fly-in collapses to
# just [off-screen blank frame, arrived frame] instead of a real multi-step
# slide -- the long hold afterward means a photo taken a few seconds after
# sending catches the held "arrived" frame.
LEGACY_DEBUG_ANIMATIONS = [
    ("TESTANIM", "HI", "#FF8000", 65, 60, 8, 500),
]

# Single source of truth for the knob's "Send to Display" submenu: which
# button/animation goes in which slot, and what label the ESP32's display
# shows for it. This gets written out as SLOTS.TXT on the SD card (see
# write_slots_manifest() below) -- the firmware reads it at boot instead of
# having this list hardcoded, so reordering/renaming/adding slots is just a
# text-file edit + SD card swap, not a reflash. Each entry: (slot_number,
# code_name, label). code_name must match a MESSAGES/ANIMATIONS entry above
# (one exception: BLANK isn't in either list above by convention -- it's
# just a MESSAGES entry with a single-space body, see the BLANK row there).
SLOTS = [
    (0, "BLANK", "BLANK"),
    (1, "LPLATE", "L PLATE"),
    (2, "MERGING", "MERGING"),
    (3, "SORRY", "SORRY!"),
    (4, "THANKS", "THANK YOU"),
    (5, "PATIENCE", "PLEASE BE PATIENT"),
    (6, "SPACE", "KEEP YOUR DISTANCE"),
    (7, "HONK", "DON'T HONK"),
    (8, "SNAKE", "SNAKE GAME"),
    (9, "LONGSNAKE", "LONG SNAKE"),
    (10, "BOUNCE", "PIXEL BOUNCE"),
    (11, "HI", "HI"),
]

SD_EXPORT_DIR = "sd_export"      # local staging folder mirroring the SD card's root
MESSAGES_DIR_NAME = "messages"   # subfolder on the card: /messages/BTN1.json, /messages/slots.json, ...
SLOTS_MANIFEST_FILE = "slots.json"
FALLBACK_HEADER_FILE = "coolledx_fallback.h"
# Baked into flash as an emergency fallback if the SD card is missing/
# unreadable at boot. Used to be kept deliberately tiny (just BLANK + one
# real message) out of a flash-size fear that turned out to be overstated
# for a C byte array (see the note below) -- as of 2026-07-25 the ENTIRE
# real driving-message set is flash-baked, precisely because "useful when
# driving" implies it has to work with no dependency on the SD card even
# being wired up, the same reasoning that drove the SD-card-optional WiFi
# design elsewhere in this project. CONNECTED stays included so the
# auto-sent "we're connected" confirmation still works with no SD card too.
# TESTSTATIC/TESTSHORT/TESTSWAPWH/TESTORDERSWAP/TESTANIM are temporary
# hardware-debug additions (2026-07-24) -- flash-baked so they're
# Serial-typeable without needing the SD card working first.
FALLBACK_CODES = ["BLANK", "LPLATE", "MERGING", "SORRY", "THANKS", "PATIENCE", "SPACE", "HONK", "HI",
                   "CONNECTED", "TESTSTATIC", "TESTSHORT", "TESTSWAPWH", "TESTORDERSWAP",
                   "TESTANIM", "SNAKE", "LONGSNAKE", "BOUNCE"]
# Earlier notes here claimed a "~6x expansion as a C header" (from the CONNECTED-scroll
# incident) and used that to justify keeping most content SD-only. That figure was real but
# for the wrong metric: it describes coolledx_fallback.h's TEXT FILE size (each byte written
# out as a "0xAB, " string literal), not actual COMPILED FLASH size (which the C compiler
# packs back down to ~1 byte per byte of real data -- see write_fallback_header()). Confirmed
# by compiling with SNAKE/LONGSNAKE/BOUNCE added (2026-07-25): usage grew by almost exactly
# the added wire-byte count, not 6x it. With that corrected, there's no real reason to keep
# the actual driving-message content SD-only anymore -- see CLAUDE.md for the measured
# flash-usage numbers after this change.

# ------------------------------------------------------

# ==================== protocol primitives ====================
# (crc32_custom, LZSS, escape/unescape, colors, and font glyph data now come
# from the coolledux-ble library -- see the alias block near the top of this
# file. This section keeps only the pieces the library doesn't provide.)


def get_data_with_text_custom_color_program_content(width, height, mode, speed, stay_time, move_space, hex_color, text_width_cols):
    payload = bytearray()
    payload.append(0x06)
    payload.extend([0x00] * 5)
    payload.extend(struct.pack(">H", move_space))
    payload.extend(struct.pack(">H", 0))       # startColumn
    payload.extend(struct.pack(">H", 0))       # startRow
    payload.extend(struct.pack(">H", width))   # showWidth (display area, not content width)
    payload.extend(struct.pack(">H", height))  # showHeight
    payload.append(mode & 0xFF)
    payload.append(speed & 0xFF)
    payload.append(stay_time & 0xFF)
    payload.append(0x00)
    # textCustomColorDataForEmoji: textNumber, allTextWidth, allTextWidths, allTextColors
    text_number = 1
    payload.extend(struct.pack(">H", text_number))
    payload.extend(struct.pack(">H", text_width_cols))  # allTextWidth (total content width, columns)
    payload.append(min(text_width_cols, 0xFF))           # allTextWidths[0] (1 byte per run)
    payload.extend(rgb444_text_color(hex_color))         # allTextColors[0]

    out = struct.pack(">I", len(payload) + 4) + bytes(payload)
    return out


def get_data_with_text_content_program_content(width, height, mode, speed, stay_time, move_space, layer_type, glyph_stream):
    payload = bytearray()
    payload.append(0x01)
    payload.extend([0x00] * 7)
    payload.append(layer_type & 0xFF)
    payload.extend(struct.pack(">H", 0))       # startColumn
    payload.extend(struct.pack(">H", 0))       # startRow
    payload.extend(struct.pack(">H", width))   # showWidth
    payload.extend(struct.pack(">H", height))  # showHeight
    payload.append(mode & 0xFF)
    payload.append(speed & 0xFF)
    payload.append(stay_time & 0xFF)
    payload.extend(struct.pack(">H", move_space))
    payload.extend(glyph_stream)

    out = struct.pack(">I", len(payload) + 4) + bytes(payload)
    return out



def _snake_head_path(width, height, n_frames, start=(2, 2), vel=(1, 1)):
    """Deterministic DVD-logo-style bounce path for the snake's head."""
    x, y = start
    dx, dy = vel
    path = []
    for _ in range(n_frames):
        path.append((x, y))
        nx, ny = x + dx, y + dy
        if nx < 0 or nx >= width:
            dx = -dx
            nx = x + dx
        if ny < 0 or ny >= height:
            dy = -dy
            ny = y + dy
        x, y = nx, ny
    return path


def render_snake_game_frames(width=SIGN_WIDTH, height=SIGN_HEIGHT, n_frames=36,
                              snake_len=6, snake_color="#00FF00", apple_color="#FF0000"):
    """Proof-of-concept full-screen game (2026-07-25): a FIXED-length snake
    (does not grow) bouncing around the whole display, eating apples placed
    directly on its own precomputed path (so at least a couple of real eats
    are guaranteed within n_frames) -- built as the validation vehicle for the
    tiled multi-frame Animation mechanism (see build_tiled_pixel_animation_message_packets
    and the "Full-screen tiled animation breakthrough" project notes). Confirmed
    on real hardware: instant per-frame pop (no scroll), true black background,
    fixed-length body, apples disappearing on contact, wall bounce, and
    continuous self-looping playback with zero further BLE traffic after the
    single upload. Returns (frames, delays) ready for
    build_tiled_pixel_animation_message_packets(..., off_color handled internally)."""
    head_path = _snake_head_path(width, height, n_frames)
    sample_idxs = [i for i in range(8, n_frames, max(1, n_frames // 4)) if i < n_frames]
    apples = {head_path[i] for i in sample_idxs}
    eaten = set()
    frames = []
    for i in range(n_frames):
        body = head_path[max(0, i - snake_len + 1):i + 1]
        head = body[-1]
        if head in apples:
            eaten.add(head)
        body_set = set(body)

        def get_pixel(x, y, body_set=body_set, apples=apples, eaten=eaten):
            if (x, y) in body_set:
                return snake_color
            if (x, y) in apples and (x, y) not in eaten:
                return apple_color
            return None

        frames.append(render_pixel_grid(width, height, get_pixel, off_color=ANIMATION_OFF_COLOR))
    return frames


def build_snake_game_message_packets(width=SIGN_WIDTH, height=SIGN_HEIGHT, n_frames=36,
                                      frame_delay_ms=150, tile_width=8,
                                      index=0, count=1, show_count=1, **snake_kwargs):
    frames = render_snake_game_frames(width, height, n_frames, **snake_kwargs)
    delays = [frame_delay_ms] * len(frames)
    return build_tiled_pixel_animation_message_packets(
        frames, delays, width, height, tile_width=tile_width,
        index=index, count=count, show_count=show_count)


def _bounce_path(width, n_frames, y, step=3):
    """1D ping-pong path along a single row -- a simple, fast-to-recognize
    hardware/timing test pattern (distinct from the diagonal 2D bounce used
    by the snake games)."""
    path = []
    x, dx = 0, step
    for _ in range(n_frames):
        path.append((x, y))
        nx = x + dx
        if nx < 0 or nx >= width:
            dx = -dx
            nx = x + dx
        x = nx
    return path


def render_pixel_bounce_frames(width=SIGN_WIDTH, height=SIGN_HEIGHT, n_frames=48,
                                y=None, step=3, color="#00FF00"):
    """Simple hardware/timing test animation (2026-07-25): a single pixel
    running back and forth along one row, true black background. Minimal
    content (one lit pixel per frame) but still a full 64x16 dense raster
    per frame like every other tiled animation here, so it's still subject
    to the same per-tile frame-buffer cap found while building LONGSNAKE --
    kept at n_frames=48 (the same empirically-confirmed-safe count) rather
    than assumed safe just because the content itself is simple."""
    if y is None:
        y = height // 2
    path = _bounce_path(width, n_frames, y, step=step)
    frames = []
    for px, py in path:
        def get_pixel(x, yy, px=px, py=py):
            return color if (x == px and yy == py) else None
        frames.append(render_pixel_grid(width, height, get_pixel, off_color=ANIMATION_OFF_COLOR))
    return frames


def build_pixel_bounce_message_packets(width=SIGN_WIDTH, height=SIGN_HEIGHT, n_frames=48,
                                        frame_delay_ms=70, tile_width=8, **bounce_kwargs):
    frames = render_pixel_bounce_frames(width, height, n_frames, **bounce_kwargs)
    delays = [frame_delay_ms] * len(frames)
    return build_tiled_pixel_animation_message_packets(
        frames, delays, width, height, tile_width=tile_width)


def render_l_plate_icon_pixel_fn(icon_width=16, height=SIGN_HEIGHT,
                                  plate_color="#FFFFFF", border_color="#000000", l_color="#FF0000"):
    """A classic 'L plate' icon -- white plate, thin dark border, centered red
    'L' -- as a get_pixel(x, y) callback usable with render_pixel_grid() or
    composited into a larger frame (see build_icon_scroll_message_packets()).
    Held static across every frame of whatever animation uses it."""
    l_cols = GLYPHS['L']
    l_width = len(l_cols)
    l_x0 = max(0, (icon_width - l_width) // 2)

    def get_pixel(x, y):
        if x == 0 or x == icon_width - 1 or y == 0 or y == height - 1:
            return border_color
        if l_x0 <= x < l_x0 + l_width:
            col = l_cols[x - l_x0]
            bit = (col >> (15 - y)) & 1 if y < 16 else 0
            if bit:
                return l_color
        return plate_color

    return get_pixel


# Composite icon + scrolling-text messages (2026-07-25), built via
# build_icon_scroll_message_packets() in main() -- a static icon held in the
# left `icon_width` columns next to text scrolling through the rest. Each
# entry: (code_name, icon_pixel_fn, icon_width, text, text_color,
# scroll_step, frame_delay_ms). `scroll_step` is picked per-message: longer
# phrases need a bigger step to keep total frame count under the
# empirically-confirmed-safe ~48-frames-per-tile ceiling (see LONGSNAKE's
# comment in build_snake_game_message_packets()) -- ALWAYS check the printed
# frame count when adding a new one here, don't assume a default is safe.
#
# LPLATE: the flagship new message, explicitly requested -- a classic white/
# red "L plate" icon next to the full safety message scrolling alongside it.
# The phrase is genuinely long (482 glyph-columns) -- at the usual
# scroll_step=6 that would be ~90 frames, well past the frame-count ceiling
# (uploads fine, silently never renders -- exactly the LONGSNAKE failure
# mode).
#
# scroll_step/frame_delay_ms/font tuned three times (2026-07-25):
#   1. step=14, delay=70ms, normal font -- 39 frames, 2.7s/cycle. Worked on
#      hardware but was, per direct user feedback after watching it run,
#      "waaaayyyy too fast" to actually read.
#   2. step=12, delay=180ms, normal font -- 46 frames, 8.3s/cycle (slower
#      clock, same font). Confirmed on hardware with a proper BURST of
#      closely-spaced photos (not single sparse snapshots) so the actual
#      motion could be judged -- and even at 8.3s/cycle, the visible window
#      changed to a completely different, non-overlapping chunk of the
#      phrase roughly every 0.7s: at the normal font's size, the 48-column
#      text region only ever shows ~5.6 characters at once, so there's no
#      reading continuity no matter how slow the clock runs.
#   3. (current) small_font=True, step=9, delay=150ms -- the actual fix per
#      direct user diagnosis ("shrink the letters so you can fit more of a
#      word"): SMALL_GLYPHS is ~30% narrower per character, so the same
#      48-column window now shows ~8 characters at once, and 45 frames (vs.
#      46 before -- still comfortably under the confirmed-safe-at-48
#      ceiling) covers the now-shorter 344-column phrase (was 482) at a
#      finer step, giving real frame-to-frame overlap instead of flash-card
#      jumps. 6.75s/cycle.
ICON_SCROLL_MESSAGES = [
    ("LPLATE", render_l_plate_icon_pixel_fn(), 16,
     "LEARNER DRIVER KEEP YOUR SPACE FOR THE SAKE OF EVERYONE'S INSURANCE",
     "#FFFFFF", 9, 150, True),
]


def render_l_plate_centered_pixel_fn(width=SIGN_WIDTH, height=SIGN_HEIGHT, icon_width=16, **kwargs):
    """The L-plate icon horizontally centered on the full canvas -- the
    sign's 'idle' resting frame for build_l_plate_message_packets()."""
    icon_fn = render_l_plate_icon_pixel_fn(icon_width=icon_width, height=height, **kwargs)
    x0 = (width - icon_width) // 2

    def get_pixel(x, y):
        cc = x - x0
        if 0 <= cc < icon_width:
            return icon_fn(cc, y)
        return None

    return get_pixel


# Simple pixel-art open/waving hand (2026-07-25, for HI) -- 4 splayed
# fingers + thumb nubs over a palm, 11 columns wide, occupying physical rows
# 2-13 (matching the normal font's row range, for consistent scale/boldness
# next to "HI" text at the normal font size). Row-art style, same as
# SMALL_GLYPHS -- easy to eyeball-verify in the source.
_HAND_ROWS = [
    "..#.#.#.#..",
    "..#.#.#.#..",
    "..#.#.#.#..",
    ".##.#.#.##.",
    ".#########.",
    ".#########.",
    ".#########.",
    ".#########.",
    "..#######..",
    "..#######..",
    "...#####...",
    "...#####...",
]
_HAND_WIDTH = 11
_HAND_FINGER_ROWS = 3  # top N rows of _HAND_ROWS that tilt for the wave motion


def render_waving_hand_pixel_fn(x0, hand_color="#FFFFFF", tilt=0):
    """One frame of the waving hand at tilt position `tilt` (-1/0/+1) --
    shifts just the finger rows (the top _HAND_FINGER_ROWS of _HAND_ROWS)
    left/right relative to the anchored palm below, to simulate a wave when
    alternated across several hold frames in build_l_plate_message_packets()."""
    def get_pixel(x, y):
        row_idx = y - 2
        if not (0 <= row_idx < len(_HAND_ROWS)):
            return None
        shift = tilt if row_idx < _HAND_FINGER_ROWS else 0
        cc = (x - x0) - shift
        if 0 <= cc < _HAND_WIDTH and _HAND_ROWS[row_idx][cc] == '#':
            return hand_color
        return None
    return get_pixel


def render_hi_wave_frame_fn(tilt, width=SIGN_WIDTH, hand_color="#FFFFFF", text_color="#FFAA00"):
    """One frame of the HI message's hold: the waving hand next to "HI" text,
    both centered together as a group. Reuses the plain GLYPHS text (HI is
    tiny, 10 columns, no need for the compact font)."""
    text_columns = build_glyph_columns("HI")
    text_width = len(text_columns)
    gap = 4
    total_width = _HAND_WIDTH + gap + text_width
    x0 = (width - total_width) // 2
    hand_fn = render_waving_hand_pixel_fn(x0, hand_color=hand_color, tilt=tilt)
    text_x0 = x0 + _HAND_WIDTH + gap

    def get_pixel(x, y):
        hp = hand_fn(x, y)
        if hp:
            return hp
        cc = x - text_x0
        if 0 <= cc < text_width:
            bit = (text_columns[cc] >> (15 - y)) & 1 if y < 16 else 0
            if bit:
                return text_color
        return None

    return get_pixel


def build_l_plate_message_packets(text, hex_color, small_font=False, icon_width=16,
                                   idle_hold_frames=4, idle_delay_ms=65000,
                                   transition_frames=8, transition_delay_ms=60,
                                   message_hold_frames=5, message_delay_ms=400,
                                   scroll=False, scroll_step=9, scroll_delay_ms=120,
                                   style="push", hold_frame_fns=None,
                                   show_width=None, show_height=None, tile_width=8,
                                   index=0, count=1, show_count=1):
    """Thin project-specific wrapper around the library's generic
    content.build_icon_idle_message_packets() (see coolledux-ble's
    coolledux/content.py for the full "idle icon -> transition -> message ->
    transition back to idle" mechanism and its extensive docstring --
    frame-ordering rationale, the push/merge transition math, the ~16-20s
    on-device playback-start latency for content this size, etc. -- all of
    that now lives there, not duplicated here).

    This project's specific "idle" icon is the centered L-plate
    (render_l_plate_centered_pixel_fn(), still defined locally in this file
    since it's project content, not general-purpose library logic) --
    everything else (transitions, scroll mode, animated hold_frame_fns,
    tiling, timing) is exactly the library's build_icon_idle_message_packets()
    with that icon plugged in as `idle_frame_fn`. Kept as a same-named,
    same-signature wrapper (rather than switching every call site over to
    the library function directly) specifically so every existing call in
    this file (main(), the HI one-off build, etc.) needed ZERO changes."""
    width = show_width if show_width is not None else SIGN_WIDTH
    height = show_height if show_height is not None else SIGN_HEIGHT
    idle_frame_fn = render_l_plate_centered_pixel_fn(width, height, icon_width)
    return build_icon_idle_message_packets(
        idle_frame_fn, text, hex_color, small_font=small_font,
        idle_hold_frames=idle_hold_frames, idle_delay_ms=idle_delay_ms,
        transition_frames=transition_frames, transition_delay_ms=transition_delay_ms,
        message_hold_frames=message_hold_frames, message_delay_ms=message_delay_ms,
        scroll=scroll, scroll_step=scroll_step, scroll_delay_ms=scroll_delay_ms,
        style=style, hold_frame_fns=hold_frame_fns,
        show_width=width, show_height=height, tile_width=tile_width,
        index=index, count=count, show_count=show_count)


# Messages using the seamless L-plate-idle transition (2026-07-25), explicit
# request: THANK YOU and SORRY! first, as a proof of the pattern -- each
# entry (code_name, text, hex_color, small_font, scroll). THANK YOU uses the
# compact font (66 columns at the normal font size -- 2 over SIGN_WIDTH=64,
# would clip; ~51 columns compact, fits with room to spare) held statically;
# SORRY! fits statically at the normal (bolder, more readable) font size
# with no need to shrink it. HONK (user-selected from a suggested-message
# menu) is genuinely too long to hold statically even at the compact font
# (140 columns vs. SIGN_WIDTH=64) -- scroll=True instead, see
# build_l_plate_message_packets()'s scroll mode.
L_PLATE_SEAMLESS_MESSAGES = [
    ("THANKS", "THANK YOU", "#00FF00", True, False, "push"),
    ("SORRY", "SORRY!", "#FFFF00", False, False, "push"),
    ("HONK", "PLEASE DON'T HONK - LEARNING", "#FF8000", True, True, "push"),
    # MERGING/PATIENCE/SPACE migrated here 2026-07-25 (explicit follow-up:
    # "I want them all to be seamless with some cool animations"). MERGING
    # gets the new style="merge" transition -- thematically apt (the name
    # IS the animation: two bands closing in and merging toward the center)
    # -- and fits statically at the normal (bolder) font, 50 columns.
    # PATIENCE/SPACE stay on the proven style="push", both needing
    # scroll=True even at the compact font (86/94 columns vs. SIGN_WIDTH=64).
    # SPACE's color changed from HONK's orange to magenta purely so the two
    # scrolling messages don't look identical in the same color.
    ("MERGING", "MERGING", "#FF0000", False, False, "merge"),
    ("PATIENCE", "PLEASE BE PATIENT", "#00FFFF", True, True, "push"),
    ("SPACE", "KEEP YOUR DISTANCE", "#FF00FF", True, True, "push"),
    # HI is NOT here -- built as a one-off in main() instead, since it needs
    # a custom animated hold (the waving hand, see render_hi_wave_frame_fn())
    # that this list's plain (text, color, small_font, scroll, style) shape
    # can't express. See "Building HI (waving hand)..." in main().
]


def build_program_bytes(text, hex_color, mode, speed, stay_time, width=None, height=None, color_first=True):
    # width/height default to SIGN_WIDTH/SIGN_HEIGHT (64x16); overridable for
    # the TESTSWAPWH hardware-debug message (2026-07-24), which deliberately
    # sends them swapped (16x64) to test whether the showWidth/showHeight wire
    # fields are actually in (rows, cols) order rather than (cols, rows) as
    # currently assumed -- see MESSAGES below.
    width = SIGN_WIDTH if width is None else width
    height = SIGN_HEIGHT if height is None else height
    glyph_stream, text_width_cols = build_glyph_stream(text)
    custom_color_segment = get_data_with_text_custom_color_program_content(
        width, height, mode, speed, stay_time, move_space=0,
        hex_color=hex_color, text_width_cols=text_width_cols)
    content_segment = get_data_with_text_content_program_content(
        width, height, mode, speed, stay_time, move_space=0,
        layer_type=1, glyph_stream=glyph_stream)

    # color_first=False is the TESTORDERSWAP hardware-debug message
    # (2026-07-24): every real hardware test so far rendered plain white
    # regardless of the intended text color, which looks like the color
    # segment isn't being applied at all -- one candidate explanation is the
    # two segments are supposed to be concatenated content-then-color rather
    # than color-then-content as currently assumed. See MESSAGES below.
    if color_first:
        combine_program_data = custom_color_segment + content_segment
    else:
        combine_program_data = content_segment + custom_color_segment
    content_number = 2  # one CoolleduxTextCombineProgram, textItem.getContentNumber() == 2 (no frame)

    program = bytearray()
    program.extend([0x00] * 8)
    program.append(content_number & 0xFF)
    program.append(0x00)
    program.extend(combine_program_data)
    return bytes(program)


def build_message_packets(text, hex_color, mode, speed, stay_time, index=0, count=1, show_count=1, width=None, height=None, color_first=True):
    program_bytes = build_program_bytes(text, hex_color, mode, speed, stay_time, width=width, height=height, color_first=color_first)
    announce = get_start_data_for_program(program_bytes, index, count, show_count)
    compressed = lzss_compress(program_bytes)
    data_packets = get_data_packet(compressed, 0x03, UX_PACKAGE_SIZE)
    return [announce] + data_packets


# ==================== SD card export ====================

def write_message_json(out_dir, code_name, packets, meta=None):
    """Writes messages/<CODE>.json: {"code", ...meta (human-readable
    definition)..., "num_chunks", "chunks": [hex, ...]}. `chunks` is what
    actually gets sent -- same pre-built protocol bytes coolledx_bytes.h used
    to bake into flash (and the earlier .BIN format wrote raw), just
    hex-encoded as JSON text so the files are human-readable/diffable, and
    could be served directly to a browser later (see the
    phone-browser-control-page TODO) without a translation step. `meta` (see
    main() -- `type`, `text`, `color`, and either text mode/speed/stayTime or
    animation step/timing/`frames`) is purely descriptive: it documents WHY
    the chunks look the way they do (what text, what color, what frame
    sequence) without needing to hex-decode anything, but it's not read back
    by the firmware -- only `chunks` is. The ESP32 still doesn't run any
    LZSS/CRC/protocol logic itself -- it just hex-decodes and streams
    `chunks`, exactly as it streamed raw .BIN bytes before."""
    if len(packets) > 2000:
        # Was capped at 255 (way more than any single-frame text/graffiti message
        # should ever need -- a sanity net, not a protocol limit). Raised
        # 2026-07-25 for the SNAKE game (a full-screen tiled multi-frame
        # animation, confirmed working on real hardware at 656 chunks) --
        # still a real sanity cap, just sized for legitimate large animations
        # instead of only single frames.
        raise ValueError(f"{code_name}: {len(packets)} chunks -- unexpectedly large, double check")
    path = os.path.join(out_dir, f"{code_name}.json")
    doc = {"code": code_name}
    if meta:
        doc.update(meta)
    doc["num_chunks"] = len(packets)
    doc["chunks"] = [chunk.hex() for chunk in packets]
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    total = sum(len(c) for c in packets)
    json_size = os.path.getsize(path)
    print(f"    -> {path} ({len(packets)} chunk(s), {total}B raw -> {json_size}B as JSON)")


def write_slots_json(out_dir):
    """messages/slots.json: one {"slot","code","label"} object per
    Send-to-Display menu slot, read by the ESP32 at boot instead of a
    hardcoded list. Reorder/rename/add slots by editing SLOTS above and
    re-running this script -- no reflash."""
    path = os.path.join(out_dir, SLOTS_MANIFEST_FILE)
    doc = [{"slot": slot, "code": code, "label": label} for slot, code, label in SLOTS]
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"    -> {path}")


def write_fallback_header(all_entries, codes, output_file):
    """Minimal flash-baked header -- same C struct shape as the old
    coolledx_bytes.h, just restricted to `codes` (see FALLBACK_CODES) so it
    stays tiny. Used by the firmware only if the SD card is missing/unreadable
    at boot; renamed FALLBACK_TABLE (not MESSAGE_TABLE) to make that role
    explicit in the .ino."""
    lines = []
    lines.append("// Auto-generated by bake_messages_coolledux.py -- do not hand-edit.")
    lines.append("// Minimal FLASH FALLBACK ONLY -- used if the SD card is missing/unreadable.")
    lines.append("// The full message/animation set lives on the SD card: see the")
    lines.append("// 'messages' folder (sd_export/messages/, incl. slots.json).")
    lines.append("#pragma once")
    lines.append("#include <Arduino.h>")
    lines.append("")

    entries = []
    for code_name in codes:
        packets = all_entries[code_name]
        var_prefix = code_name.lower()
        chunk_count = len(packets)
        for i, chunk in enumerate(packets):
            byte_str = ", ".join(f"0x{b:02X}" for b in chunk)
            lines.append(f"const uint8_t {var_prefix}_chunk{i}[] = {{ {byte_str} }};")
        lens = ", ".join(f"sizeof({var_prefix}_chunk{i})" for i in range(chunk_count))
        ptrs = ", ".join(f"{var_prefix}_chunk{i}" for i in range(chunk_count))
        lines.append(f"const uint8_t* const {var_prefix}_chunks[] = {{ {ptrs} }};")
        lines.append(f"const size_t {var_prefix}_lengths[] = {{ {lens} }};")
        lines.append(f"const size_t {var_prefix}_num_chunks = {chunk_count};")
        lines.append("")
        entries.append((code_name, var_prefix))

    lines.append("struct MessageEntry {")
    lines.append("  const char* code;")
    lines.append("  const uint8_t* const* chunks;")
    lines.append("  const size_t* lengths;")
    lines.append("  size_t num_chunks;")
    lines.append("};")
    lines.append("")
    lines.append("const MessageEntry FALLBACK_TABLE[] = {")
    for code_name, var_prefix in entries:
        lines.append(f'  {{ "{code_name}", {var_prefix}_chunks, {var_prefix}_lengths, {var_prefix}_num_chunks }},')
    lines.append("};")
    lines.append(f"const size_t FALLBACK_TABLE_SIZE = {len(entries)};")
    lines.append("")

    with open(output_file, "w") as f:
        f.write("\n".join(lines))


def main():
    messages_dir = os.path.join(SD_EXPORT_DIR, MESSAGES_DIR_NAME)
    os.makedirs(messages_dir, exist_ok=True)

    all_entries = {}  # code_name -> packets (used for the JSON `chunks` and the flash fallback)
    all_meta = {}      # code_name -> human-readable definition dict (see write_message_json())

    print("Building messages...")
    for code_name, text, color in MESSAGES:
        total_width = len(build_glyph_columns(text))
        if total_width <= SIGN_WIDTH:
            packets = build_tiled_static_text_message_packets(text, color)
            msg_type = "text_static_tiled"
            extra_meta = {}
        else:
            packets = build_tiled_scrolling_text_message_packets(text, color)
            msg_type = "text_scrolling_tiled"
            extra_meta = {"note": f"{total_width}-column text on a {SIGN_WIDTH}-column "
                                   "display -- scrolls continuously, sign loops it on its own"}
        all_entries[code_name] = packets
        all_meta[code_name] = {"type": msg_type, "text": text, "color": color, **extra_meta}
        print(f"{code_name} ({text!r}, {msg_type}): {len(packets)} chunk(s), "
              f"{sum(len(c) for c in packets)} bytes total")

    print("\nBuilding icon+scrolling-text messages...")
    for code_name, icon_pixel_fn, icon_width, text, text_color, scroll_step, frame_delay_ms, small_font in ICON_SCROLL_MESSAGES:
        packets = build_icon_scroll_message_packets(
            icon_pixel_fn, icon_width, text, text_color,
            scroll_step=scroll_step, frame_delay_ms=frame_delay_ms, small_font=small_font)
        all_entries[code_name] = packets
        all_meta[code_name] = {
            "type": "icon_scroll_tiled",
            "text": text,
            "color": text_color,
            "icon_width": icon_width,
            "scroll_step": scroll_step,
            "frame_delay_ms": frame_delay_ms,
            "small_font": small_font,
            "note": f"static icon in the left {icon_width} columns, {text!r} scrolling "
                    f"through the remaining {SIGN_WIDTH - icon_width} columns",
        }
        print(f"{code_name} ({text!r}, icon_scroll_tiled): {len(packets)} chunk(s), "
              f"{sum(len(c) for c in packets)} bytes total")

    print("\nBuilding L-plate seamless messages...")
    for code_name, text, text_color, small_font, scroll, style in L_PLATE_SEAMLESS_MESSAGES:
        packets = build_l_plate_message_packets(text, text_color, small_font=small_font,
                                                 scroll=scroll, style=style)
        all_entries[code_name] = packets
        all_meta[code_name] = {
            "type": "l_plate_seamless",
            "text": text,
            "color": text_color,
            "small_font": small_font,
            "scroll": scroll,
            "style": style,
            "note": "starts/ends idling on a centered L-plate icon; the message transitions "
                    "it out of the way (push or merge), holds (scrolling if too long to fit "
                    "statically), then reverses -- seamless loop point",
        }
        print(f"{code_name} ({text!r}, l_plate_seamless): {len(packets)} chunk(s), "
              f"{sum(len(c) for c in packets)} bytes total")

    print("\nBuilding HI (waving hand)...")
    # Wave pattern: neutral, lean left, neutral, lean right -- repeated 3x
    # for a "few seconds" hold (12 frames x 350ms = 4.2s), well under the
    # confirmed-safe ~48-frame ceiling even added to the idle+transition
    # frames (4 + 9 + 12 + 7 = 32).
    hi_wave_tilts = [0, -1, 0, 1] * 3
    hi_hold_fns = [render_hi_wave_frame_fn(t) for t in hi_wave_tilts]
    hi_packets = build_l_plate_message_packets(
        "HI", "#FFAA00", hold_frame_fns=hi_hold_fns, message_delay_ms=350)
    all_entries["HI"] = hi_packets
    all_meta["HI"] = {
        "type": "l_plate_seamless",
        "text": "HI",
        "color": "#FFAA00",
        "note": "starts/ends idling on a centered L-plate icon; a waving hand + 'HI' text "
                "pushes it out of the way, waves for a few seconds, then reverses",
    }
    print(f"HI ('HI', l_plate_seamless, waving hand): {len(hi_packets)} chunk(s), "
          f"{sum(len(c) for c in hi_packets)} bytes total")

    print("\nBuilding legacy debug messages (intentionally kept on the old, "
          "known-broken text-content mechanism -- see LEGACY_DEBUG_MESSAGES comment)...")
    for code_name, text, color, mode, speed, stay_time in LEGACY_DEBUG_MESSAGES:
        packets = build_message_packets(text, color, mode, speed, stay_time)
        all_entries[code_name] = packets
        all_meta[code_name] = {
            "type": "text_legacy_broken",
            "text": text,
            "color": color,
            "mode": mode,
            "speed": speed,
            "stay_time": stay_time,
            "note": "intentionally kept on the old text-content+color-segment mechanism, "
                    "confirmed broken (renders plain white) -- diagnostic reference only",
        }
        print(f"{code_name} ({text!r}): {len(packets)} chunk(s), "
              f"{sum(len(c) for c in packets)} bytes total")

    # TESTSWAPWH: same as a normal text message except showWidth/showHeight
    # are sent swapped (16, 64 instead of 64, 16) -- see MESSAGES above.
    tsw_code, tsw_text, tsw_color, tsw_mode, tsw_speed, tsw_stay, tsw_w, tsw_h = TESTSWAPWH_ENTRY
    tsw_packets = build_message_packets(tsw_text, tsw_color, tsw_mode, tsw_speed, tsw_stay, width=tsw_w, height=tsw_h)
    all_entries[tsw_code] = tsw_packets
    all_meta[tsw_code] = {
        "type": "text",
        "text": tsw_text,
        "color": tsw_color,
        "mode": tsw_mode,
        "speed": tsw_speed,
        "stay_time": tsw_stay,
        "show_width": tsw_w,
        "show_height": tsw_h,
        "note": "debug test: showWidth/showHeight sent swapped (16, 64) vs the normal (64, 16)",
    }
    print(f"{tsw_code} ({tsw_text!r}, swapped W/H={tsw_w}x{tsw_h}): {len(tsw_packets)} chunk(s), "
          f"{sum(len(c) for c in tsw_packets)} bytes total")

    # TESTORDERSWAP: same as a normal text message except the color and
    # content segments are concatenated in the opposite order -- see
    # TESTORDERSWAP_ENTRY above.
    tos_code, tos_text, tos_color, tos_mode, tos_speed, tos_stay, tos_color_first = TESTORDERSWAP_ENTRY
    tos_packets = build_message_packets(tos_text, tos_color, tos_mode, tos_speed, tos_stay, color_first=tos_color_first)
    all_entries[tos_code] = tos_packets
    all_meta[tos_code] = {
        "type": "text",
        "text": tos_text,
        "color": tos_color,
        "mode": tos_mode,
        "speed": tos_speed,
        "stay_time": tos_stay,
        "note": "debug test: content segment sent before color segment (reversed from normal)",
    }
    print(f"{tos_code} ({tos_text!r}, color_first={tos_color_first}): {len(tos_packets)} chunk(s), "
          f"{sum(len(c) for c in tos_packets)} bytes total")

    print("\nBuilding animations...")
    for code_name, text, color, step, frame_delay_ms, hold_frames, hold_delay_ms in ANIMATIONS:
        print(f"{code_name} ({text!r}, tiled fly-in animation):")
        packets, offsets, delays = build_tiled_flyin_message_packets(
            text, color, step=step, frame_delay_ms=frame_delay_ms,
            hold_frames=hold_frames, hold_delay_ms=hold_delay_ms)
        all_entries[code_name] = packets
        # `frames` mirrors exactly what got baked into the wire bytes -- one
        # {offset, delay_ms} per frame, offset=show_width meaning fully
        # off-screen right and offset=0 meaning flush left/arrived. Purely
        # descriptive: readable here for debugging/authoring reference, not
        # read back by the ESP32.
        all_meta[code_name] = {
            "type": "animation_tiled",
            "text": text,
            "color": color,
            "step": step,
            "frame_delay_ms": frame_delay_ms,
            "hold_frames": hold_frames,
            "hold_delay_ms": hold_delay_ms,
            "frame_count": len(offsets),
            "frames": [{"offset": o, "delay_ms": d} for o, d in zip(offsets, delays)],
        }
        print(f"    {len(packets)} chunk(s), {sum(len(c) for c in packets)} bytes total")

    print("\nBuilding legacy debug animation (intentionally kept on the old, "
          "known-broken UNTILED animation mechanism -- see LEGACY_DEBUG_ANIMATIONS comment)...")
    for code_name, text, color, step, frame_delay_ms, hold_frames, hold_delay_ms in LEGACY_DEBUG_ANIMATIONS:
        print(f"{code_name} ({text!r}, legacy untiled fly-in animation):")
        packets, offsets, delays = build_flyin_message_packets(
            text, color, step=step, frame_delay_ms=frame_delay_ms,
            hold_frames=hold_frames, hold_delay_ms=hold_delay_ms)
        all_entries[code_name] = packets
        all_meta[code_name] = {
            "type": "animation_legacy_broken",
            "text": text,
            "color": color,
            "step": step,
            "frame_delay_ms": frame_delay_ms,
            "hold_frames": hold_frames,
            "hold_delay_ms": hold_delay_ms,
            "frame_count": len(offsets),
            "frames": [{"offset": o, "delay_ms": d} for o, d in zip(offsets, delays)],
            "note": "intentionally kept on the old untiled single-segment animation mechanism, "
                    "confirmed broken (no visible change on real hardware) -- diagnostic reference only",
        }
        print(f"    {len(packets)} chunk(s), {sum(len(c) for c in packets)} bytes total")

    print("\nBuilding games...")
    print("SNAKE (full-screen tiled multi-frame animation):")
    snake_packets = build_snake_game_message_packets()
    all_entries["SNAKE"] = snake_packets
    all_meta["SNAKE"] = {
        "type": "tiled_animation",
        "note": "Full-screen (64x16) proof-of-concept Snake game -- fixed-length body, "
                "red apples, true black background, self-looping on-sign playback. "
                "See render_snake_game_frames()/build_snake_game_message_packets().",
    }
    total_snake_bytes = sum(len(c) for c in snake_packets)
    print(f"    {len(snake_packets)} chunk(s), {total_snake_bytes} bytes total "
          f"(large -- this is a one-time upload cost; the sign plays it back on its "
          f"own afterward with no further BLE traffic)")

    print("LONGSNAKE (full-screen tiled multi-frame animation, longer body + longer loop):")
    # n_frames=48/snake_len=12 -- NOT arbitrary. A first attempt at 64
    # frames/18-segment body (256B/frame/tile raw x 64 = 16384B/tile) uploaded
    # cleanly (0 packet errors) but never appeared on the physical sign --
    # the previous SORRY! message just stayed put. 48 frames/12 segments
    # (12288B/tile raw) DOES render and animate correctly (camera-confirmed,
    # 2026-07-25). This points at a real per-tile decoded-frame-buffer size
    # cap on the sign's own firmware somewhere between those two raw sizes --
    # same species of hard-coded scratch-buffer limit as the ~8-column
    # single-segment cap documented in "Full-screen tiled animation
    # breakthrough" above, just for animation frame *count* x *tile size*
    # rather than segment width. Not bisected further/pinned to an exact byte
    # boundary -- 48/12 already gives a visibly longer, longer-bodied snake
    # than the original SNAKE (36 frames/6 segments) and reliably works, which
    # was the actual goal. Revisit with a tighter bisection only if a still
    # longer animation is wanted later.
    longsnake_packets = build_snake_game_message_packets(
        n_frames=48, frame_delay_ms=140, snake_len=12)
    all_entries["LONGSNAKE"] = longsnake_packets
    all_meta["LONGSNAKE"] = {
        "type": "tiled_animation",
        "note": "Bigger sibling of SNAKE (2026-07-25) -- a 12-segment body (vs SNAKE's 6) "
                "over a longer 48-frame loop, same true-black-background/self-looping "
                "mechanism. See render_snake_game_frames()/build_snake_game_message_packets(). "
                "48/12 chosen empirically -- a 64-frame/18-segment first attempt uploaded "
                "cleanly but silently failed to render, pointing at a real per-tile frame-buffer "
                "size cap on the sign's own firmware; see the comment above this block.",
    }
    total_longsnake_bytes = sum(len(c) for c in longsnake_packets)
    print(f"    {len(longsnake_packets)} chunk(s), {total_longsnake_bytes} bytes total "
          f"(large -- one-time upload cost, plays back on-sign afterward with no further "
          f"BLE traffic)")

    print("\nBuilding test animations...")
    print("BOUNCE (green pixel running back and forth, full-screen tiled animation):")
    bounce_packets = build_pixel_bounce_message_packets()
    all_entries["BOUNCE"] = bounce_packets
    all_meta["BOUNCE"] = {
        "type": "tiled_animation",
        "note": "Simple hardware/timing test animation (2026-07-25) -- a single green pixel "
                "running back and forth along one row on a true black background. "
                "See render_pixel_bounce_frames()/build_pixel_bounce_message_packets().",
    }
    total_bounce_bytes = sum(len(c) for c in bounce_packets)
    print(f"    {len(bounce_packets)} chunk(s), {total_bounce_bytes} bytes total")

    missing = [code for _, code, _ in SLOTS if code not in all_entries]
    if missing:
        print(f"\nWARNING: SLOTS references code(s) with no MESSAGES/ANIMATIONS entry: {missing}"
              f" -- those slots will show '(not set)' on the sign until you add them.")

    # Clean up stale .json files from a PREVIOUS run whose code no longer
    # exists in this run's content (e.g. the old BTN1-BTN7 placeholder set,
    # replaced 2026-07-25) -- the export step below only ever WRITES, so
    # without this, removing/renaming a message would leave its old file
    # behind forever, silently stale and never referenced by anything.
    keep_names = {f"{code_name}.json" for code_name in all_entries} | {SLOTS_MANIFEST_FILE}
    if os.path.isdir(messages_dir):
        for fname in os.listdir(messages_dir):
            if fname.endswith(".json") and fname not in keep_names:
                stale_path = os.path.join(messages_dir, fname)
                os.remove(stale_path)
                print(f"    removed stale {stale_path} (no longer in MESSAGES/ANIMATIONS/etc.)")

    print(f"\nWriting SD card export to ./{messages_dir}/ ...")
    for code_name, packets in all_entries.items():
        write_message_json(messages_dir, code_name, packets, all_meta.get(code_name))
    write_slots_json(messages_dir)
    print(f"\nCopy the '{MESSAGES_DIR_NAME}' FOLDER ITSELF (./{messages_dir}/) onto the ROOT of a "
          f"FAT32 SD card -- the firmware looks for /{MESSAGES_DIR_NAME}/BTN1.json, "
          f"/{MESSAGES_DIR_NAME}/slots.json, etc.")

    missing_fallback = [c for c in FALLBACK_CODES if c not in all_entries]
    if missing_fallback:
        raise SystemExit(f"FALLBACK_CODES references undefined code(s): {missing_fallback}")

    print(f"\nWriting flash fallback header {FALLBACK_HEADER_FILE} "
          f"(only {', '.join(FALLBACK_CODES)} -- used only if the SD card is missing/unreadable)...")
    write_fallback_header(all_entries, FALLBACK_CODES, FALLBACK_HEADER_FILE)
    print(f"Wrote {FALLBACK_HEADER_FILE} -- copy it into your ESP32 sketch folder.")


if __name__ == "__main__":
    main()

# PiKVM TESmart Control

Reverse-engineered UART control and PiKVM integration for **TESmart**
KVM switches: the 4-port **HKS401-M24** and the 8-port, dual-monitor
**HKS802-M24**, with a model-independent core for other units that use
the same command set.

> **Status:** Experimental. Findings here are from direct testing of an
> HKS401-M24 running `HKS401-M24-SOFMUS2_V_V111-APP` (build May 19,
> 2025) and an HKS802-M24. Other firmware/models may differ.

## Overview

This project lets PiKVM control and monitor a TESmart KVM through its
3.3 V TTL UART interface. A dedicated daemon, `tesmartd`, is the sole
owner of the UART. PiKVM and command-line clients communicate with that
daemon over a Unix socket. The PiKVM controls are generated for the
connected unit, so they only show the PCs, outputs and features it has.

Current functionality includes:

-   Select PCs on all outputs, or per output (Monitor A/B) on
    multi-monitor units.
-   Detect physical KVM input changes.
-   Bidirectionally synchronize KVM selection with PiKVM.
-   Query and decode most useful KVM state.
-   Change buzzer (on/off or volume), lighting, KM mode, Ethernet,
    mouse, fan, audio-follow and audio selection settings.
-   Start/stop the **real** Auto Scan function.
-   Query actual Auto Scan runtime state.
-   Enable/disable Auto Detect (`0x0E/0x8E`).
-   Set the mixed-mode keyboard/mouse focus (`0x07/0x87`) on units that
    have it.
-   Keep a PiKVM Switch on the same PC as the KVM output PiKVM displays.

## Supported models and configuration

| Model | PCs | Outputs | Differences |
| --- | --- | --- | --- |
| HKS401-M24 | 4 | 1 | Lighting, Ethernet switch, Audio Follow; buzzer on/off; scan interval with updated firmware |
| HKS802-M24 | 8 | 2 (A, B) | Per-output selection, buzzer volume (off/low/medium/high), mixed-mode K/M focus; no lighting, Ethernet, Audio Follow or scan interval (yet) |

The daemon reads `/etc/tesmart.conf` (installed from
`tesmart.conf.example` on first deploy, never overwritten):

``` ini
MODEL=auto          # auto | HKS401-M24 | HKS802-M24 | custom
#PCS=8              # override the PC (input) count
#OUTPUTS=2          # override the output (monitor) count
#FEATURES=          # replace the feature list (comma-separated)
PIKVM_OUTPUT=B      # output wired to PiKVM's video capture
#AUTOSCAN_CMD=0x0F  # set 0x0E only if a unit has Auto Scan/Auto Detect swapped
SWITCH_LINK=1       # two-way PiKVM Switch link
#SERIAL_DEV=/dev/ttyAMA0
```

-   `MODEL=auto` identifies the unit from its `0x81` reply (`41` =
    HKS401-M24, `82` = HKS802-M24) and adds any feature whose query the
    unit answers at startup. An unknown unit gets its PC/output counts
    from `0x81` and only the features it answers.
-   A model name uses that profile without depending on detection;
    `custom` with `PCS`/`OUTPUTS` covers other units.
-   Features: `buzzer`, `buzzer_levels`, `lighting`, `km_mode`,
    `network`, `mouse`, `fan`, `audio_follow`, `audio`, `autoscan`,
    `auto_detect`, `scan_interval`, `mixed_focus`.

After editing, apply it with `systemctl restart tesmartd kvmd`. `tesmart
model` shows what the daemon resolved:

``` json
{
  "ready": true,
  "model": {
    "name": "HKS802-M24", "pcs": 8, "outputs": 2, "letters": "AB",
    "features": ["buzzer", "buzzer_levels", "km_mode", "mouse", "fan",
                 "audio", "autoscan", "auto_detect", "mixed_focus"],
    "pikvm_output": "B", "source": "auto"
  }
}
```

## Hardware and TRRS cable

Tested with a Raspberry Pi 4 running PiKVM and a TESmart HKS401-M24.

### A 4-conductor TRRS plug is required

A normal 3-conductor TRS plug is **not sufficient**. The HKS401 UART
jack carries four conductors:

| TRRS contact | Function | STM32F030C8T6 |
| --- | --- | --- |
| Tip | +3.3 V | NC |
| Ring 1 | HKS401 TX | Pin 30 — PA9 / USART1_TX |
| Ring 2 | HKS401 RX | Pin 31 — PA10 / USART1_RX |
| Sleeve | GND | GND(jack's leg to GND |
<img src="pics/trrs.jpeg" alt="TRRS Plug" width="200">
The two ring positions should be verified on the individual unit before
wiring. On the tested HKS401, UART traces to the STM32F030C8T6:

-   Pin 30 - PA9 = USART1_TX
-   Pin 31 - PA10 = USART1_RX

An earlier TRS plug grounded the second ring/HKS401 RX and prevented
proper bidirectional communication. Moving to TRRS solved the problem.

### Raspberry Pi wiring

| Raspberry Pi | HKS401 |
| --- | --- |
| Physical pin 6 — GND | GND / Sleeve |
| Physical pin 8 — GPIO14/TXD0 | HKS401 RX |
| Physical pin 10 — GPIO15/RXD0 | HKS401 TX |
| No connection | HKS401 +3.3 V / Tip |

**Leave the HKS401 +3.3 V Tip disconnected.** Only TX, RX and common
ground are required.

The interface is **3.3 V TTL, not RS-232**. Do not use a traditional
±RS-232 interface.

## UART configuration

Working Pi serial device:

``` text
/dev/ttyAMA0
```

Pi configuration:

``` text
enable_uart=1
dtoverlay=disable-bt
```

Confirmed serial settings:

``` text
9600 baud, 8 data bits, no parity, 1 stop bit
```

or **9600 8N1**.

TESmart support suggested 38400 during troubleshooting, but 38400 did
not work on the tested unit. 9600 works.

A persistent permission rule can be used:

``` text
KERNEL=="ttyAMA0", GROUP="uucp", MODE="0660"
```

The relevant PiKVM/daemon user should have access through `uucp`.

## UART framing and checksum

Observed commands use:

``` text
AA BB <command> <command-specific data> <checksum>
```

The checksum is the low byte of the additive sum of every preceding
byte.

Example:

``` text
AA BB 03 00 02 6A
```

because:

``` text
AA + BB + 03 + 00 + 02 = 0x16A
checksum = 0x6A
```

Some documentation/early analysis suggested a generic payload-length
field. Experiments with set-command data bytes show that the layouts should
instead be treated as command-specific until all fields are understood.

## PC selection

| Function | Frame |
| --- | --- |
| PC1 | `AA BB 03 00 00 68` |
| PC2 | `AA BB 03 00 01 69` |
| PC3 | `AA BB 03 00 02 6A` |
| PC4 | `AA BB 03 00 03 6B` |
| PC5-PC8 (HKS802) | `AA BB 03 00 04 6C` ... `AA BB 03 00 07 6F` |
| Next PC | `AA BB 03 FF 00 67` |
| Output A/B only (HKS802) | `AA BB 03 0N PP CC`, N = 1 (A) / 2 (B), PP = PC index |

The second byte selects the output: `00` switches all outputs (and
keyboard/mouse), `01` only output A, `02` only output B. See
[HKS802-M24 findings](#hks802-m24-findings) for how focus follows.

## Queries implemented

| State | Query |
| --- | --- |
| PC/monitor topology | `AA BB 81 00 00 E6` |
| Keyboard/mouse focus | `AA BB 82 00 FF E6` |
| Monitor/PC correspondence | `AA BB 83 00 FF E7` |
| Buzzer | `AA BB 84 00 FF E8` |
| Lighting | `AA BB 85 00 FF E9` |
| KM mode (USB keyboard/mouse) | `AA BB 88 00 FF EC` |
| Ethernet/network | `AA BB 89 00 FF ED` |
| Mouse | `AA BB 8A 00 FF EE` |
| Fan | `AA BB 8B 00 FF EF` |
| Audio Follow | `AA BB 8C 00 FF F0` |
| Audio selection | `AA BB 8D 00 FF F1` |
| Mixed-mode K/M focus (HKS802) | `AA BB 87 00 FF EB` |
| Auto Detect | `AA BB 8E 00 FF F2` |
| **Auto Scan runtime** | `AA BB 8F 00 FF F3` |

`0x86` and `0x87` did not respond on the tested HKS401-M24, which has no
Mixed Mode. The HKS802-M24 answers `0x87` (see below) but not `0x85`,
`0x89`, `0x8C` or `0x90`.

`0x06` (not in TESmart's command sheet) has no observable effect: no
beep, no reply, no unsolicited frames, no change to any queried setting,
and no change to Auto Scan dwell. Tested with data
`00 FF`, `00 00`, `00 01`, `00 03`, `00 05`, `00 0C`, `01 00`, `03 00`,
`FF 00`.

## Decoded query state

### `0x81` topology

The high nibble is the PC count and the low nibble the monitor
(output) count. The HKS401-M24 answers `41`, the HKS802-M24 `82`:

``` json
"ports": {"pcs": 4, "monitors": 1, "raw": "41"}
"ports": {"pcs": 8, "monitors": 2, "raw": "82"}
```

### `0x82` keyboard/mouse focus

``` text
00 = PC1
01 = PC2
02 = PC3
03 = PC4
```

### `0x83` monitor/PC correspondence

``` text
01 00 = Monitor 1 -> PC1
01 01 = Monitor 1 -> PC2
01 02 = Monitor 1 -> PC3
01 03 = Monitor 1 -> PC4
```

The reply is a list of (1-based monitor, zero-based PC) pairs, one per
output. HKS802-M24 example, Monitor A on PC2 and Monitor B on PC8:

``` text
AA BB 83 04 01 01 02 07 FD
```

### Other decoded queries

``` text
0x84 buzzer:
  00 off
  01 on
  (TESmart lists 02 medium / 03 high; this model ignores them and
  keeps reporting 01.)

0x85 lighting:
  04 00 off
  04 01 indicator
  04 02 marquee
  04 03 breathing

0x88 KM mode (USB keyboard/mouse):
  00 passthrough  (KVM beeps once)
  01 compatible   (KVM beeps twice)

0x89 Ethernet:
  first byte = four-PC enable mask
  0F all enabled
  0E PC1 disabled
  0D PC2 disabled
  0B PC3 disabled
  07 PC4 disabled
  second byte = focus PC, 00..03

0x8A mouse wheel switching:
  00 off
  01 on

0x8B fan:
  04 00 off
  04 01 auto
  04 02 low
  04 03 high

0x8C Audio Follow:
  00 off
  01 on

0x8D audio:
  00 PC1
  01 PC2
  02 PC3
  03 PC4
```

With Audio Follow enabled, explicit audio selection is
overridden/follows PC selection.

## Set commands implemented

### Buzzer --- `0x04`

``` text
AA BB 04 00 00 69  # off
AA BB 04 00 01 6A  # on
```

### Lighting --- `0x05`

``` text
AA BB 05 02 00 6C  # off
AA BB 05 02 01 6D  # indicator
AA BB 05 02 02 6E  # marquee
AA BB 05 02 03 6F  # breathing
```

### KM mode --- `0x08`

``` text
AA BB 08 00 00 6D  # passthrough
AA BB 08 00 01 6E  # compatible
```

### Ethernet/network --- `0x09`

``` text
AA BB 09 00 0F 7D  # all on
AA BB 09 00 0E 7C  # PC1 off
AA BB 09 00 0D 7B  # PC2 off
AA BB 09 00 0B 79  # PC3 off
AA BB 09 00 07 75  # PC4 off
```

### Mouse --- `0x0A`

``` text
AA BB 0A 00 00 6F  # off
AA BB 0A 00 01 70  # on
```

### Fan --- `0x0B`

``` text
AA BB 0B 00 00 70  # off
AA BB 0B 00 01 71  # auto
AA BB 0B 00 02 72  # low
AA BB 0B 00 03 73  # high
```

### Audio Follow --- `0x0C`

``` text
AA BB 0C 00 00 71  # off
AA BB 0C 00 01 72  # on
```

### Audio selection --- `0x0D`

``` text
AA BB 0D 00 00 72  # PC1
AA BB 0D 00 01 73  # PC2
AA BB 0D 00 02 74  # PC3
AA BB 0D 00 03 75  # PC4
AA BB 0D FF 00 71  # next
```

## Auto Scan and Auto Detect

TESmart's command sheet is correct for both tested models:

| Function | Set | Query / reply |
| --- | --- | --- |
| Auto Scan | `AA BB 0F 00 01 75` on, `AA BB 0F 00 00 74` off | `AA BB 8F 00 FF F3` -> `8F 01 01` running, `8F 01 00` stopped |
| Auto Detect (not in the sheet) | `AA BB 0E 00 01 74` on, `AA BB 0E 00 00 73` off | `AA BB 8E 00 FF F2` -> `8E 01 01` on, `8E 01 00` off |

> Earlier revisions of this project had the two swapped on the HKS401
> (`0x0E` as Auto Scan). That was a mapping bug in our software, not
> a model difference; the daemon now uses the sheet's mapping for every
> unit. `AUTOSCAN_CMD=0x0E` in `/etc/tesmart.conf` swaps them back for a
> unit that really differs.

The daemon exposes these as:

``` text
set autoscan on|off       # 0x0F
set auto_detect on|off    # 0x0E
query autoscan            # 0x8F
query auto_detect         # 0x8E
```

### Scan interval and keyboard control

The physical keyboard supports:

``` text
Right-Ctrl, Right-Ctrl, Space  # start Auto Scan
Right-Ctrl, Right-Ctrl, +      # increase scan period
Right-Ctrl, Right-Ctrl, -      # decrease scan period
Esc                            # stop Auto Scan
Right-Ctrl, Right-Ctrl, F7     # toggle Auto Detect
```

-   Changing the period with the keyboard visibly changes the dwell time
    but produces **no UART traffic**; each `-` press shortened the dwell
    by 1 s on the HKS401.
-   On stock firmware no command sets or reports the period (`0x06`,
    `0x86`, `0x90`-`0x9F` don't reply); see `0x10`/`0x90` below for the
    updated HKS401 firmware.
-   Only powered-on, connected sources are visited, starting from the
    current PC (HKS802 manual; matches HKS401 behaviour). The HKS802's
    default dwell is ~15 s.

### Scan interval --- `0x10`/`0x90` (updated firmware)

TESmart supplied updated HKS401 firmware that makes the interval
settable and readable over UART:

``` text
AA BB 10 00 HH LL CC   # set interval, HH LL = seconds (5-3600), big-endian; no reply
AA BB 90 00 FF F4      # query interval
AA BB 90 02 HH LL CC   # reply, e.g. AA BB 90 02 00 0A 01 = 10 s
```

Verified on hardware: 5, 8, 10, 15, 20, 30, 60 and 300 s all read back
via `0x90`, and timed scans dwelled exactly 10.0 s at a 10 s setting.
Stock firmware ignores both frames.

``` text
hks401 query scan_interval
hks401 set scan_interval 30        # 5-3600, validated before sending
hks401 set scan_interval next     # cycle 5 -> 10 -> 15 -> 30 -> 60 -> 5 s
```

`next` moves to the next preset above the current value and rolls over
from 60 s to 5 s (a custom value such as 20 s goes to 30 s).
Because keyboard changes are UART-silent, the daemon re-reads `0x90`
every 5 s while Auto Scan runs, but only once the unit has answered a
`0x90` query, so stock firmware isn't polled.

In the PiKVM UI the **Auto Scan Interval** row has 5/10/15/30/60 s
preset buttons (the current one is green; none is lit for a custom
value) and a **Next** button that cycles through them.

### Auto Detect behaviour

With Auto Detect on, the KVM switches to a newly attached active source,
or to the next active source when one is removed (and the buzzer
sounds); with it off, connections are ignored. It does not start or stop
Auto Scan.

Measured query round trip (write start to full reply) is ~17 ms:
~6.3 ms per 6-byte frame on the wire plus ~4 ms KVM processing. No
replies were dropped in testing, including queries sent immediately
after a set or back-to-back. The daemon still retries readbacks in case a
frame is lost: it queries after 20 ms, re-queries 0.15, 0.5 and 1 s
later, and logs `NO REPLY` 1 s after the last attempt, which also covers
the HKS802's ~1.8 s busy period after a switch. A
sniffer sees nothing if `tesmartd` is running, because the daemon
consumes the reply.

## Unsolicited state frames

Physical PC selection generates unsolicited state:

``` text
AA BB 83 02 01 00 EB  # Monitor 1 -> PC1
AA BB 82 01 00 E8     # focus PC1

AA BB 83 02 01 03 EE  # Monitor 1 -> PC4
AA BB 82 01 03 EB     # focus PC4
```

This lets PiKVM follow a physical HKS401 selection.

### Auto Scan transitions are silent

Starting Auto Scan from the keyboard produces an initial `0x83`/`0x82`
snapshot. Automatic PC-to-PC transitions then produce **no UART
frames**. `Esc` produces another ordinary snapshot.

Explicit `0x82` and `0x83` queries during Auto Scan correctly return the
current PC, and `0x8F` correctly reports whether scanning is active.

The daemon therefore polls `0x82` once a second (and re-checks `0x8F`
every 5 seconds) **only while Auto Scan is active**, rather than
continuously polling the entire KVM. The PiKVM PC buttons, Ethernet focus
outline and Audio Follow selection follow each scan step within about a
second. Any unsolicited `0x82`/`0x83` snapshot triggers a `0x8F` query, so
Auto Scan started or stopped from the keyboard is tracked as well.

The daemon must not depend on the nominal five-second scan interval
because a keyboard user can change it locally without producing UART
traffic.

## Auto Detect is separate from Auto Scan

-   Pressing the HKS401's `M` button itself produces no UART frame.
-   With Auto Detect off, HDMI connect/disconnect events produce no UART
    state; with it on, qualifying connection events produce ordinary
    `0x83`/`0x82` state.
-   The Auto Scan status (`0x8F`) is independent of Auto Detect
    (`0x8E`).

## Ethernet jack

The HKS401-M24 LAN jack is not a management endpoint on the tested
hardware. Direct testing showed DHCP/IPv6 traffic from downstream PCs
passing through it, consistent with an internal Ethernet switch/uplink.

No HKS401 management endpoint was found there. TESmart support also
indicated no planned firmware update to add endpoint management.

UART is therefore the management/control path used by this project.

## HKS802-M24 findings

Tested by probing every query `0x81`-`0x9F` and each select variant:

| Behaviour | Result |
| --- | --- |
| `0x81` topology | `82`: 8 PCs, 2 monitors |
| PC5-PC8 | `03 00 04`-`03 00 07` switch all outputs; audio `0D 00 04`-`0D 00 07` work |
| `03 01 PP` (output A) | Switches A only. K/M focus and audio usually stay put |
| `03 02 PP` (output B) | Switches B; K/M focus and audio usually follow |
| `03 0N FF` (per-output next) | Ignored; the daemon computes it from `0x83` |
| UART select | Unsolicited `0x82` follows (the HKS401 sends nothing) |
| Buzzer `04 00 01/02/03` | Low/medium/high, read back by `0x84` |
| `0x07 00 PP` / `0x07 FF 00` | Sets / steps the mixed-mode K/M focus (`0x82` doesn't change). Accepted only while the outputs show different PCs (mixed screen), ignored otherwise; an all-outputs select resets it |
| `0x87` | Mixed-mode focus PC, then one PC per monitor, B before A (e.g. `87 03 03 03 01` = focus PC4, B on PC4, A on PC2). Only the focus byte is used |
| `0x0A`/`0x8A` mouse middle button | Works; set and readback match |
| `0x0C` Audio Follow | Ignored: audio always follows an all-outputs or output-B switch, and `0x0D` still changes it in "follow" mode. Not shown in the UI |
| `0x09` Ethernet | No effect: the HKS802 has no per-PC network switch (`0x89` never answers, tried data `00 FF`, `00 00`, `FF FF`); not shown in the UI |
| No reply | `0x85` lighting, `0x89` Ethernet, `0x8C` Audio Follow, `0x90` scan interval (the `0x10`/`0x90` firmware update isn't known for this model yet), `0x86`, `0x91`-`0x9F` |
| Reply latency | ~50 ms idle, but replies are queued for ~1.8 s after a switch |
| Auto Scan | `0x0F`/`0x8F`. Default dwell ~15 s; visits powered-on, connected sources from the current PC in order PC1-PC8 (LCD shows "SCAN") |
| Auto Detect | `0x0E`/`0x8E`; hotkey Right-Ctrl, Right-Ctrl, F7 |

Because of the queued replies, readbacks are retried at 0.15/0.5/1/1 s
before `NO REPLY` is logged. A select whose result `0x83` doesn't confirm
within 1.5 s is re-sent up to twice.

### HKS802 LAN control port

The HKS802's single RJ45 port is a control interface (TCP port 5000; MAC
prefix `0C:3D:5E`), not a network switch. Factory settings are
`192.168.1.10`, mask `255.255.255.0`, gateway `192.168.1.1`; the tested
unit has been moved to `192.168.50.11` on PiKVM's subnet. Several
commands can be sent over one TCP connection.

It speaks TESmart's older fixed-length protocol, the one kvmd's built-in
`tesmart` driver uses (`EE` terminator, no checksum on requests):

``` text
AA BB 03 10 00 EE   # query active PC  -> AA BB 03 11 NN 19 (NN = 0-based PC)
AA BB 03 01 NN EE   # switch to PC NN (1-based)
```

The UART command set (`0x81`-`0x8F` queries etc.) gets no reply over
TCP, so this project keeps using the UART, which has per-output
selection, settings and unsolicited state. The KVM only answers on its
own subnet, so it must sit on PiKVM's subnet (or be given a route).

#### Network settings over TCP

TESmart's Windows "KVM Switch control panel" changes the address with a
plain-ASCII protocol on the same port 5000 (decoded from a packet
capture; TESmart's notes say this firmware can't set the IP over RS232).
Each set replies `OK`; queries return zero-padded values:

``` text
IP?                    -> IP:192.168.050.011;
PT?                    -> PT:05000;
GW?                    -> GW:192.168.050.001;
MA?                    -> MA:255.255.255.000;

IP:192.168.50.11;      -> OK
PT:5000;               -> OK
GW:192.168.50.1;       -> OK
MA:255.255.255.0;      -> OK
```

The tool sends all four sets, then re-queries them. The new settings
take effect after a power cycle. The queries were verified from PiKVM
with a few lines of Python; the sets haven't been sent from Linux yet.
Note the new address before applying: there is no discovery protocol,
so a forgotten address can only be found again with an ARP scan for the
`0C:3D:5E` MAC.

Whether K/M focus follows an output-only select wasn't consistent in
testing; the daemon always takes focus from `0x82` rather than assuming.

## `tesmartd` daemon

The daemon is deliberately the **sole normal owner** of `/dev/ttyAMA0`.

This matters: during reverse engineering, having multiple processes read
the UART caused lost responses and misleading retry behavior.

Architecture:

``` text
TESmart UART
     |
     v
+-----------+
| tesmartd  |  sole UART owner
+-----+-----+
      |
      | Unix socket
      v
/run/tesmartd.sock
      |
      +----------> tesmart CLI
      |
      +----------> PiKVM UGPIO plugin (tesmart_uart)
      |
      +----------> tesmart-kvmd-ui (generates the PiKVM controls)
```

Runtime state:

``` text
/run/tesmartd-state.json
```

On start the daemon queries everything once; when every query has
answered or timed out it settles the model (`"ready": true`). After
that, side-effect refreshes (e.g. Ethernet/audio after a PC change) are
only sent for queries the unit has answered, so unsupported ones aren't
retried on every switch.

The daemon starts before `kvmd`.

Example daemon operations (the `tesmart` CLI wraps them, e.g.
`tesmart 3`, `tesmart b 5`, `tesmart a next`, `tesmart model`):

``` text
select 3          # all outputs (and keyboard/mouse) to PC3
select next
select b 5        # output B only (multi-output units)
select a next

query focus
query correspondence
query autoscan

state
model

set autoscan on
set buzzer medium # off|on|low|medium|high
```

Example decoded state:

``` json
{
  "model": {"name": "HKS401-M24", "pcs": 4, "outputs": 1, "...": "..."},
  "ready": true,
  "active_pc": 1,
  "focus_pc": 1,
  "outputs": {"A": 1},
  "monitor_pc": 1,
  "ports": {
    "pcs": 4,
    "monitors": 1,
    "raw": "41"
  },
  "buzzer": "on",
  "lighting": "breathing",
  "km_mode": "compatible",
  "network_mask": "0x0f",
  "network_ports": {
    "pc1": true,
    "pc2": true,
    "pc3": true,
    "pc4": true
  },
  "network_focus_pc": 1,
  "mouse": "off",
  "fan": "auto",
  "audio_follow": "on",
  "audio_pc": 1,
  "autoscan": "off",
  "auto_detect": "off",
  "scan_interval": 8,
  "switch_port": 0
}
```

## PiKVM integration

The custom UGPIO driver (type `tesmart_uart`; kvmd already ships an
unrelated `tesmart` driver for TESmart's network-controlled KVMs) is kept
at:

``` text
/usr/local/lib/tesmart/tesmart_uart.py
```

and linked into the PiKVM Python plugin location:

``` text
/usr/lib/python3.14/site-packages/kvmd/plugins/ugpio/tesmart_uart.py
```

The plugin:

-   Talks to `tesmartd`, never directly to UART.
-   Polls daemon state.
-   Exposes named UGPIO pins (up to 16 PCs and outputs A-D; each unit's
    generated view uses a subset):

| Pins | Type | Daemon command |
| --- | --- | --- |
| `pc1`-`pc16` | choice | `select N` (all outputs; lit = K/M focus) |
| `out_a_pc1`-`out_d_pc16` | choice | `select X N` |
| `out_a_next`-`out_d_next` | pulse | `select X next` |
| `km_passthrough`, `km_compatible` | choice | `set km_mode MODE` |
| `buzzer_off/low/medium/high` | choice | `set buzzer LEVEL` |
| `lighting_off/indicator/marquee/breathing` | choice | `set lighting MODE` |
| `fan_off/auto/low/high` | choice | `set fan MODE` |
| `audio_pc1`-`audio_pc16` | choice | `set audio pcN` |
| `mixed_pc1`-`mixed_pc16`, `mixed_next` | choice, pulse | `set mixed_focus pcN/next` |
| `autoscan`, `buzzer`, `mouse` (mouse wheel switching), `audio_follow`, `auto_detect` | on/off | `set NAME on/off` |
| `net_pc1`-`net_pc8` | on/off | `set network_pcN on/off` |
| `pc_next`, `audio_next` | pulse | `select next`, `set audio next` |
| `scan_5`, `scan_10`, `scan_15`, `scan_30`, `scan_60` | choice | `set scan_interval N` |
| `scan_next` | pulse | `set scan_interval next` |
| `net_focus_pc1`-`net_focus_pc8` | input | — (0x89 focus PC) |

-   Choice pins act only on the ON transition; turning the active
    choice OFF is ignored.
-   Notifies PiKVM when state changes.

The daemon builds the Ethernet `0x09` frame from the current mask, so
each PC can be toggled independently (`set network_mask 0xNN` sets the
whole mask). Only the all-on and single-PC-off masks have been verified
on hardware.

This provides bidirectional synchronization:

``` text
Physical KVM change
        -> UART
        -> tesmartd
        -> UGPIO plugin
        -> PiKVM UI
```

and:

``` text
PiKVM UI
        -> UGPIO plugin
        -> tesmartd
        -> UART
        -> KVM
```

### Generated controls

`tesmart-kvmd-ui` asks `tesmartd` for its settled model and prints the
GPIO scheme and view. `install-tesmart-kvmd-plugin` (kvmd
`ExecStartPre`) writes it to `/etc/kvmd/override.d/tesmart.yaml` on every
kvmd start if it changed. If the daemon can't settle a model within
10 s (KVM off), the existing file is kept.

| Section | Shown when |
| --- | --- |
| PC1-PCn + Next PC (all outputs; 2 per row up to 4 PCs, else 4) | always |
| Monitor A/B/... rows: PC1-PCn + Next, PiKVM's output marked "(PiKVM)" | more than one output |
| Controls: Auto Scan, Buzzer, Mouse Wheel, Audio Follow, Auto Detect toggles | per feature (Buzzer toggle only without `buzzer_levels`) |
| KM Mode, Buzzer (Off/Low/Medium/High), Lighting, Fan, Ethernet, Auto Scan Interval | per feature |
| Audio, Mixed K/M Focus (compact PC buttons, each in its own table) | per feature |

kvmd renders the view as plain tables in which every row shares column
widths, so the generator starts a new table for sections with different
button widths (per-output rows, Audio, Mixed K/M Focus), and `tesmart.css` keeps
stand-alone section titles from widening the first column.

The HKS802-M24 view:

``` text
HKS802-M24 Input (all outputs)
  [PC 1] [PC 2] [PC 3] [PC 4]
  [PC 5] [PC 6] [PC 7] [PC 8]
  [Next PC]
Monitor A          [PC 1] ... [PC 8] [Next]
Monitor B (PiKVM)  [PC 1] ... [PC 8] [Next]
Controls: Auto Scan, Mouse Wheel, Auto Detect
KM Mode  [Passthru] [Compat]
Buzzer   [Off] [Low] [Medium] [High]
Fan      [Off] [Auto] [Low] [High]
-----
Audio    [PC 1] ... [PC 8] [Next]
-----
Mixed K/M Focus  [PC 1] ... [PC 8] [Next]
```

### PiKVM Switch link

When a PiKVM Switch is attached (used here for ATX control only; video
comes from the TESmart), `tesmartd` keeps it on the same PC as the
output wired to PiKVM (`PIKVM_OUTPUT`): KVM PC N corresponds to Switch
port N (API port `N-1`).

-   Changes of that output's PC (UI buttons, Next, front panel, keyboard
    hotkeys, Auto Scan steps) move the Switch via
    `POST /switch/set_active`.
-   Selecting a Switch port in the PiKVM UI selects the matching PC on
    that output (all outputs on single-output units).
-   PCs beyond the Switch's port count (PC5-PC8 with one 4-port Switch)
    leave the Switch where it is; chained Switch units extend the range
    automatically.
-   The daemon polls `GET /switch` every 0.5 s over
    `/run/kvmd/kvmd.sock`. Root is a kvmd `kvmd_users` unix-socket user,
    so no credentials are stored.
-   After either side is driven, the other is ignored for up to 2 s
    until it confirms (and while a select is unconfirmed), so stale
    reports cannot cause ping-pong. A Switch change made within that
    window is overridden.
-   If kvmd is down the link retries every 5 s; `switch_port` in the
    daemon state shows the last known port.

Disable it with `SWITCH_LINK=0` in `/etc/tesmart.conf`.

### Surviving PiKVM updates

The canonical plugin is kept under `/usr/local` because package files
may be replaced by PiKVM updates.

Helper:

``` text
/usr/local/sbin/install-tesmart-kvmd-plugin
```

A `kvmd` systemd drop-in (`kvmd.service.d/tesmart.conf`) runs:

``` text
ExecStartPre=+/usr/local/sbin/install-tesmart-kvmd-plugin
```

to restore the plugin symlink when needed and regenerate the controls. This recovery mechanism has
been tested by removing the installed link and confirming that it is
recreated.

No PiKVM-owned files are edited:

-   UGPIO config lives in `/etc/kvmd/override.d/tesmart.yaml`, not in
    `override.yaml`.
-   UI styling is a marked block in `/etc/kvmd/web.css`, which PiKVM
    serves as `share/css/user.css`. Other user CSS in that file is
    preserved.
-   The only file added inside the KVMD package tree is the plugin
    symlink, which KVMD's plugin loader requires.
-   The helper only writes when something is missing or stale, and
    temporarily remounts the read-only root when it must.

### Deploying from this repo

Edit files in this repo, then deploy:

``` bash
sudo bash /home/pikvm-tesmart-uart/deploy.sh
```

`deploy.sh` installs `/etc/tesmart.conf` if missing, enables `tesmartd`
and restarts it and kvmd. On a unit that still has the earlier
`hks401`-named install, it first stops and removes `hks401d`, its CLI,
plugin, override, udev rule and kvmd hook; the kvmd hook also drops the
old `hks401` CSS block and plugin symlink.

## Raw UART testing

Always stop `tesmartd` before opening the UART manually:

``` bash
sudo systemctl stop tesmartd
```

Passive sniffer:

``` python
import serial
import time

ser = serial.Serial(
    "/dev/ttyAMA0",
    9600,
    bytesize=8,
    parity="N",
    stopbits=1,
    timeout=0.05,
)

start = time.monotonic()

try:
    while True:
        data = ser.read(256)
        if data:
            elapsed = time.monotonic() - start
            print(
                f"+{elapsed:8.3f}s  "
                + " ".join(f"{b:02X}" for b in data),
                flush=True,
            )
finally:
    ser.close()
```

Restart normal operation afterward:

``` bash
sudo systemctl start tesmartd
```

Do **not** run a raw UART reader and `tesmartd` simultaneously.

## Current status

### Confirmed

-   HKS401-M24 UART operates at 9600 8N1.
-   Electrical interface is 3.3 V TTL.
-   Four-conductor TRRS is required.
-   Additive low-byte checksum.
-   PC1-PC4 selection.
-   Physical selection produces `0x82/0x83` state.
-   Topology and monitor/PC correspondence decoded.
-   Most useful documented settings have working queries/setters.
-   Auto Scan starts/stops through `0x0F` and is reported by `0x8F`, as
    in TESmart's sheet.
-   Automatic scan transitions are UART-silent.
-   Keyboard scan-period changes are UART-silent.
-   Scan interval set/query via `0x10`/`0x90` on updated firmware.
-   `0x0E/0x8E` is Auto Detect, independent of Auto Scan.
-   LAN jack is an internal network switch/uplink, not the HKS401
    management interface.
-   PiKVM PC-selection integration works bidirectionally.
-   PiKVM UI follows Auto Scan via conditional `0x82` polling.
-   Two-way PC selection link with the PiKVM Switch.
-   HKS802-M24: 8 PCs, per-output A/B selection, buzzer volume levels,
    mixed-mode K/M focus, auto-detected by `MODEL=auto`.

### Still being investigated

-   Whether keyboard/mouse actually follow the HKS802's `0x07` mixed
    focus (it is accepted only while A and B show different PCs).
-   When the HKS802 moves K/M focus on an output-only select.
-   The `0x10`/`0x90` scan-interval firmware for the HKS802 (the
    daemon adds the UI row automatically once the unit answers `0x90`).
-   Additional PiKVM UI controls and status indicators.

## Disclaimer

This is an independent reverse-engineering project and is not affiliated
with or endorsed by TESmart or PiKVM. Commands are based on observed
behavior of the tested HKS401-M24 and HKS802-M24 and may differ on other
firmware or hardware revisions.

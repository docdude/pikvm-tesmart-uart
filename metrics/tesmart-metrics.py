#!/usr/bin/env python3
"""Expose TESmart KVM state (from tesmartd, pikvm-tesmart-uart) for node_exporter's textfile collector."""
import json, os, socket, time

STATE = "/run/tesmartd-state.json"
SOCK = "/run/tesmartd.sock"
OUT = "/run/node_exporter/tesmart.prom"
INTERVAL = 15
# tesmartd only receives frames on events, so an explicit read-only query proves the UART link
PROBE_EVERY = 300
PROBE_WAIT = 5
SETTINGS = ("buzzer", "lighting", "km_mode", "mouse", "fan", "audio_follow", "autoscan", "auto_detect")


def read_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def probe():
    before = (read_state() or {}).get("last_update") or 0
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(SOCK)
            s.sendall(b"query focus\n")
            if not json.loads(s.recv(4096).decode()).get("ok"):
                return 0
    except (OSError, ValueError):
        return 0
    deadline = time.time() + PROBE_WAIT
    while time.time() < deadline:
        time.sleep(0.25)
        if ((read_state() or {}).get("last_update") or 0) > before:
            return 1
    return 0


def esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def render(st, link_up, probe_ts):
    m = [
        "# HELP tesmart_state_readable 1 if tesmartd's state file could be read.",
        "# TYPE tesmart_state_readable gauge",
        f"tesmart_state_readable {1 if st else 0}",
        "# HELP tesmart_link_up 1 if the last read-only UART query got a reply from the KVM.",
        "# TYPE tesmart_link_up gauge",
        f"tesmart_link_up {link_up}",
        "# HELP tesmart_link_probe_timestamp_seconds When the last UART link probe ran.",
        "# TYPE tesmart_link_probe_timestamp_seconds gauge",
        f"tesmart_link_probe_timestamp_seconds {probe_ts:.0f}",
    ]
    if not st:
        return m
    model = st.get("model") or {}
    m += ["# HELP tesmart_info KVM model detected by tesmartd, always 1.",
          "# TYPE tesmart_info gauge",
          f'tesmart_info{{model="{esc(model.get("name", "unknown"))}",pcs="{esc(model.get("pcs", ""))}",'
          f'outputs="{esc(model.get("outputs", ""))}",pikvm_output="{esc(model.get("pikvm_output", ""))}"}} 1']
    for key in ("active_pc", "focus_pc", "monitor_pc", "audio_pc", "mixed_focus_pc"):
        if isinstance(st.get(key), int):
            m += [f"# TYPE tesmart_{key} gauge", f"tesmart_{key} {st[key]}"]
    outputs = st.get("outputs") or {}
    if outputs:
        m += ["# HELP tesmart_output_pc PC shown on each KVM output (multi-output models).",
              "# TYPE tesmart_output_pc gauge"]
        for out, pc in outputs.items():
            if isinstance(pc, int):
                m.append(f'tesmart_output_pc{{output="{esc(out)}"}} {pc}')
    m.append("# HELP tesmart_setting Current KVM setting (value label), always 1.")
    m.append("# TYPE tesmart_setting gauge")
    for name in SETTINGS:
        if st.get(name) is not None:
            m.append(f'tesmart_setting{{name="{name}",value="{esc(st[name])}"}} 1')
    m.append("# TYPE tesmart_network_port_enabled gauge")
    for pc, on in (st.get("network_ports") or {}).items():
        m.append(f'tesmart_network_port_enabled{{pc="{esc(pc)}"}} {1 if on else 0}')
    if st.get("last_update"):
        m += ["# HELP tesmart_last_frame_timestamp_seconds Last UART frame received from the KVM.",
              "# TYPE tesmart_last_frame_timestamp_seconds gauge",
              f"tesmart_last_frame_timestamp_seconds {st['last_update']:.0f}"]
    return m


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    link_up, probe_ts = 0, 0.0
    while True:
        if time.time() - probe_ts >= PROBE_EVERY:
            link_up, probe_ts = probe(), time.time()
        with open(OUT + ".tmp", "w") as f:
            f.write("\n".join(render(read_state(), link_up, probe_ts)) + "\n")
        os.replace(OUT + ".tmp", OUT)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

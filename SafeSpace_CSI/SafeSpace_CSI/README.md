# SafeSpace CSI — ESP32 Wall-Penetrating Human Presence Detection

Smart city human presence detection using **ESP32 WiFi Channel State Information (CSI)** — no cameras, no PIR sensors, no expensive hardware. Just two ESP32 DevKit boards and physics.

## How It Works

WiFi signals change when they pass through a human body. By measuring the **amplitude variance** of CSI subcarrier data between a TX and RX ESP32, we can determine whether a person is present in a room — through walls, in the dark, without line of sight.

```
ESP32 TX ──WiFi CSI──► ESP32 RX ──Serial──► Python Backend ──WebSocket──► Browser Dashboard
```

## Hardware Required

- 2× ESP32 DevKit (classic ESP32, not S3/C3)
- 2× USB cables
- Any laptop or Raspberry Pi running the Python backend

## Firmware Setup (PlatformIO)

### TX Board
```bash
cd csi_tx
pio run --target upload
```

### RX Board
```bash
cd csi_rx
pio run --target upload
```

## Python Backend Setup

```bash
pip install -r requirements.txt
```

Edit `app.py` line 10 — set `SERIAL_PORT` to your RX board's COM port.

```bash
python app.py
```

Open browser at `http://localhost:5000`

## Presence Detection Logic

| Metric | Description |
|--------|-------------|
| CSI Amplitude | Mean magnitude across all WiFi subcarriers |
| Variance | Rolling variance over 30 samples — rises when human absorbs/reflects signal |
| Threshold | Variance > 2.5 = human present (tunable in app.py) |

## Tuning

- `PRESENCE_THRESH` in `app.py` — lower = more sensitive, higher = fewer false positives
- `WINDOW_SIZE` — larger window = smoother but slower response
- `PING_INTERVAL_MS` in TX firmware — 50ms (20 packets/sec) is optimal

## Future City Application

- Smart building energy management — auto HVAC/lighting per room occupancy
- Security monitoring without privacy-invasive cameras
- Elderly care fall detection through walls
- Retail footfall analytics

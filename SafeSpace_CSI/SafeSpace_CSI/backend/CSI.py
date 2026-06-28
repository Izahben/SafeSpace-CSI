import serial
import threading
import time
import collections
import numpy as np
from flask import Flask, render_template_string
from flask_socketio import SocketIO

# ─── CONFIG ───────────────────────────────────────────────────
SERIAL_PORT      = "COM14"
BAUD_RATE        = 115200
WINDOW_SIZE      = 30
CALIBRATION_SECS = 5
PRESENCE_MARGIN  = 0.15
STALE_TIMEOUT    = 3.0
# ──────────────────────────────────────────────────────────────

app = Flask(__name__)
io  = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

state = {
    "amplitude":   0.0,
    "variance":    0.0,
    "rssi":        0,
    "presence":    False,
    "confidence":  0,
    "history":     collections.deque(maxlen=100),
    "connected":   False,
    "threshold":   None,
    "calibrating": True,
    "last_packet": 0.0,
}
amp_window   = collections.deque(maxlen=WINDOW_SIZE)
calib_buffer = []
lock         = threading.Lock()

# ─── SERIAL READER ────────────────────────────────────────────
def serial_reader():
    global calib_buffer
    ser = None

    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
            print(f"[SERIAL] Port open: {SERIAL_PORT}")

            calib_buffer = []
            amp_window.clear()
            calib_start = time.time()
            calibrated  = False

            with lock:
                state["calibrating"] = True
                state["threshold"]   = None
                state["history"].clear()

            while True:
                line = ser.readline().decode("utf-8", errors="ignore").strip()

                if not line.startswith("CSI,"):
                    continue

                parts = line.split(",")
                if len(parts) < 4:
                    print(f"[SERIAL] Malformed line: {line}")
                    continue

                try:
                    amp  = float(parts[1])
                    rssi = int(parts[2])
                except ValueError:
                    print(f"[SERIAL] Parse error: {line}")
                    continue

                now = time.time()
                print(f"[CSI] amp={amp:.4f}  rssi={rssi}dBm")

                # ── AUTO CALIBRATION ──
                if not calibrated:
                    calib_buffer.append(amp)
                    elapsed   = now - calib_start
                    remaining = max(0, int(CALIBRATION_SECS - elapsed) + 1)
                    print(f"[CALIB] Collecting baseline... {remaining}s remaining "
                          f"(samples={len(calib_buffer)})")

                    if elapsed >= CALIBRATION_SECS and len(calib_buffer) >= 10:
                        baseline  = float(np.var(calib_buffer))
                        threshold = round(baseline + PRESENCE_MARGIN, 4)
                        calibrated = True
                        print(f"[CALIB] Done. baseline_var={baseline:.4f}  "
                              f"threshold={threshold:.4f}")
                        with lock:
                            state["threshold"]   = threshold
                            state["calibrating"] = False
                            state["connected"]   = True
                    continue

                # ── DETECTION ──
                amp_window.append(amp)
                variance = float(np.var(list(amp_window))) if len(amp_window) > 5 else 0.0

                with lock:
                    thresh = state["threshold"] or (PRESENCE_MARGIN + 0.5)

                presence   = variance > thresh
                confidence = min(100, int((variance / thresh) * 100)) if thresh > 0 else 0

                print(f"[DETECT] var={variance:.4f}  thresh={thresh:.4f}  "
                      f"presence={presence}  conf={confidence}%")

                with lock:
                    state["amplitude"]   = round(amp, 4)
                    state["variance"]    = round(variance, 4)
                    state["rssi"]        = rssi
                    state["presence"]    = presence
                    state["confidence"]  = confidence
                    state["connected"]   = True
                    state["last_packet"] = now
                    state["history"].append({
                        "t":   time.strftime("%H:%M:%S"),
                        "amp": round(amp, 2),
                        "var": round(variance, 2),
                    })

        except Exception as e:
            print(f"[SERIAL] Error: {e} — retrying in 3s")
            with lock:
                state["connected"]   = False
                state["calibrating"] = True
                state["threshold"]   = None
            if ser:
                try: ser.close()
                except: pass
                ser = None
            time.sleep(3)

# ─── STALE WATCHDOG ───────────────────────────────────────────
def stale_watchdog():
    while True:
        time.sleep(1)                          # plain time.sleep — no eventlet
        with lock:
            if state["connected"] and state["last_packet"] > 0:
                if time.time() - state["last_packet"] > STALE_TIMEOUT:
                    print("[WATCHDOG] No packets received — marking disconnected")
                    state["connected"] = False

# ─── BROADCASTER ──────────────────────────────────────────────
_last_payload = {}

def broadcaster():
    global _last_payload
    while True:
        time.sleep(0.2)
        with lock:
            payload = {
                "amplitude":   state["amplitude"],
                "variance":    state["variance"],
                "rssi":        state["rssi"],
                "presence":    state["presence"],
                "confidence":  state["confidence"],
                "connected":   state["connected"],
                "calibrating": state["calibrating"],
                "threshold":   state["threshold"],
                "history":     list(state["history"])[-20:],
            }
        comparable = {k: v for k, v in payload.items() if k != "history"}
        if comparable == _last_payload:
            continue
        _last_payload = comparable
        print(f"[EMIT] presence={payload['presence']}  "
              f"conf={payload['confidence']}%  connected={payload['connected']}")
        io.emit("csi_update", payload)

# ─── DASHBOARD ────────────────────────────────────────────────
DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SafeSpace CSI</title>
<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  :root{--bg:#0d1117;--card:#161b22;--border:#30363d;--green:#3fb950;--red:#f85149;--yellow:#d29922;--blue:#58a6ff;--text:#e6edf3;--muted:#8b949e}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',sans-serif;min-height:100vh}
  header{padding:20px 32px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px}
  header h1{font-size:1.3rem;font-weight:700}
  header span{font-size:.8rem;color:var(--muted)}
  .badges{margin-left:auto;display:flex;gap:8px;align-items:center}
  .badge{font-size:.78rem;padding:4px 12px;border-radius:20px;font-weight:600}
  .connected{background:#1a3a20;color:var(--green)}
  .disconnected{background:#3a1a1a;color:var(--red)}
  .sock-ok{background:#1a2a3a;color:var(--blue)}
  .sock-err{background:#3a1a1a;color:var(--red)}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;padding:24px 32px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:20px}
  .card label{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
  .card .val{font-size:2rem;font-weight:700;margin-top:6px}
  .card .sub{font-size:.78rem;color:var(--muted);margin-top:4px}
  #presence-card{border-color:var(--green)}
  #presence-card.occupied{border-color:var(--red)}
  #presence-card.calibrating-state{border-color:var(--yellow)}
  #presence-badge{display:inline-block;padding:6px 18px;border-radius:20px;font-size:1rem;font-weight:700;margin-top:10px}
  .vacant{background:#1a3a20;color:var(--green)}
  .occupied{background:#3a1a1a;color:var(--red)}
  .calib-badge{background:#2a2a10;color:var(--yellow)}
  .chart-wrap{padding:0 32px 32px}
  .chart-wrap h2{font-size:.9rem;color:var(--muted);margin-bottom:12px}
  canvas{max-height:200px}
  #conf-bar-wrap{margin-top:10px;background:#1c2128;border-radius:6px;height:8px;overflow:hidden}
  #conf-bar{height:100%;border-radius:6px;transition:width .3s,background .3s}
  #calib-msg{text-align:center;padding:10px;color:var(--yellow);font-size:.85rem;display:none}
</style>
</head>
<body>
<header>
  <div>
    <h1>&#128246; SafeSpace CSI</h1>
    <span>ESP32 Channel State Information — Human Presence Detection</span>
  </div>
  <div class="badges">
    <div id="sock-status" class="badge sock-err">● Socket Disconnected</div>
    <div id="hw-status"   class="badge disconnected">● Hardware Disconnected</div>
  </div>
</header>

<div id="calib-msg">&#9203; Calibrating baseline... please ensure room is empty</div>

<div class="grid">
  <div class="card" id="presence-card">
    <label>Room Status</label>
    <div id="presence-badge" class="vacant">VACANT</div>
    <div class="sub" style="margin-top:8px">Confidence</div>
    <div id="conf-bar-wrap"><div id="conf-bar" style="width:0%;background:#3fb950"></div></div>
    <div class="sub" id="conf-val">0%</div>
  </div>
  <div class="card">
    <label>CSI Amplitude</label>
    <div class="val" id="amp-val">—</div>
    <div class="sub">Mean subcarrier magnitude</div>
  </div>
  <div class="card">
    <label>Signal Variance</label>
    <div class="val" id="var-val">—</div>
    <div class="sub" id="thresh-label">Threshold: auto</div>
  </div>
  <div class="card">
    <label>RSSI</label>
    <div class="val" id="rssi-val">—</div>
    <div class="sub">dBm from TX board</div>
  </div>
</div>

<div class="chart-wrap">
  <h2>LIVE CSI AMPLITUDE + VARIANCE</h2>
  <canvas id="chart"></canvas>
</div>

<script>
const socket = io();
const MAX_POINTS = 50;
const labels=[], ampData=[], varData=[];

const chart = new Chart(document.getElementById('chart'),{
  type:'line',
  data:{labels,datasets:[
    {label:'Amplitude',data:ampData,borderColor:'#58a6ff',borderWidth:1.5,pointRadius:0,tension:.3,fill:false},
    {label:'Variance', data:varData,borderColor:'#f85149',borderWidth:1.5,pointRadius:0,tension:.3,fill:false}
  ]},
  options:{
    animation:false,responsive:true,
    plugins:{legend:{labels:{color:'#8b949e',font:{size:11}}}},
    scales:{
      x:{ticks:{color:'#8b949e',maxTicksLimit:8},grid:{color:'#21262d'}},
      y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}
    }
  }
});

socket.on('connect',    ()=>{ document.getElementById('sock-status').textContent='● Socket Connected';    document.getElementById('sock-status').className='badge sock-ok'; });
socket.on('disconnect', ()=>{ document.getElementById('sock-status').textContent='● Socket Disconnected'; document.getElementById('sock-status').className='badge sock-err'; });

let wasConnected = false;

socket.on('csi_update', d => {
  if(d.connected && !wasConnected){
    labels.length=0; ampData.length=0; varData.length=0; chart.update();
  }
  wasConnected = d.connected;

  const hw = document.getElementById('hw-status');
  hw.textContent = d.connected ? '● Hardware Connected' : '● Hardware Disconnected';
  hw.className   = d.connected ? 'badge connected' : 'badge disconnected';

  const calibMsg  = document.getElementById('calib-msg');
  const card      = document.getElementById('presence-card');
  const badge     = document.getElementById('presence-badge');
  const threshLbl = document.getElementById('thresh-label');

  if(d.calibrating){
    calibMsg.style.display='block';
    badge.textContent='CALIBRATING'; badge.className='calib-badge';
    card.className='card calibrating-state';
    threshLbl.textContent='Threshold: calibrating...';
    return;
  }

  calibMsg.style.display='none';
  threshLbl.textContent = d.threshold !== null ? `Threshold: ${d.threshold.toFixed(4)}` : 'Threshold: auto';

  badge.textContent = d.presence ? 'OCCUPIED' : 'VACANT';
  badge.className   = d.presence ? 'occupied'  : 'vacant';
  card.className    = d.presence ? 'card occupied' : 'card';

  const bar = document.getElementById('conf-bar');
  bar.style.width      = d.confidence+'%';
  bar.style.background = d.confidence>60 ? '#f85149' : d.confidence>30 ? '#d29922' : '#3fb950';
  document.getElementById('conf-val').textContent = d.confidence+'%';

  document.getElementById('amp-val').textContent  = d.amplitude.toFixed(3);
  document.getElementById('var-val').textContent  = d.variance.toFixed(3);
  document.getElementById('rssi-val').textContent = d.rssi+' dBm';

  if(d.history && d.history.length){
    const last = d.history[d.history.length-1];
    labels.push(last.t); ampData.push(last.amp); varData.push(last.var);
    if(labels.length>MAX_POINTS){labels.shift();ampData.shift();varData.shift();}
    chart.update();
  }
});
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(DASHBOARD)

if __name__ == "__main__":
    t1 = threading.Thread(target=serial_reader,  daemon=True)
    t2 = threading.Thread(target=broadcaster,    daemon=True)
    t3 = threading.Thread(target=stale_watchdog, daemon=True)
    t1.start(); t2.start(); t3.start()
    io.run(app, host="0.0.0.0", port=5000, debug=False,
           use_reloader=False, allow_unsafe_werkzeug=True)

// ===== API helpers =====
async function api(path, method = 'GET', body = null) {
  const opt = { method, headers: {} };
  if (body) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function getStatus() {
  const j = await api('/api/status');
  renderStatus(j);
}

async function tune(freq) {
  await api('/api/tune', 'POST', { frequency: Number(freq) });
  await getStatus();
}

async function step(dir) {
  await api('/api/step', 'POST', { direction: dir });
  await getStatus();
}

async function mute() {
  await api('/api/mute', 'POST');
  await getStatus();
}

async function unmute() {
  await api('/api/unmute', 'POST');
  await getStatus();
}

async function setMono(forceMono) {
  await api('/api/mono', 'POST', { mono: !!forceMono });
  await getStatus();
}

// ===== UI rendering =====
function renderStatus(j) {
  // Frequency text
  const freqEl = document.getElementById('freq');
  if (freqEl) freqEl.textContent = j.frequency.toFixed(1);

  // Station name
  const nameEl = document.getElementById('name');
  if (nameEl) nameEl.textContent = j.station_name || 'Unknown';

  // Lamps
  const stereoLamp = document.getElementById('lamp-stereo');
  if (stereoLamp) toggleLamp(stereoLamp, !!j.stereo);
  const muteLamp = document.getElementById('lamp-mute');
  if (muteLamp) toggleLamp(muteLamp, !!j.muted);

  // Signal bars (0..15)
  const meter = document.getElementById('meter');
  if (meter) {
    const bars = meter.querySelectorAll('span');
    const level = Math.max(0, Math.min(15, Number(j.signal || 0)));
    bars.forEach((b, idx) => {
      if (idx < level) b.classList.add('on');
      else b.classList.remove('on');
    });
  }

  // Dial needle
  setNeedleAngle(j.frequency);
}

function toggleLamp(el, on) {
  if (on) el.classList.add('active');
  else el.classList.remove('active');
}

// Map frequency (87.5–108.0 MHz) to needle angle (-70deg to +70deg)
function setNeedleAngle(freq) {
  const minF = 87.5;
  const maxF = 108.0;
  const minDeg = -70;
  const maxDeg = 70;
  const clamped = Math.max(minF, Math.min(maxF, Number(freq)));
  const t = (clamped - minF) / (maxF - minF);
  const deg = minDeg + t * (maxDeg - minDeg);
  const needle = document.getElementById('needle');
  if (needle) {
    needle.style.transform = `translate(-50%, -100%) rotate(${deg.toFixed(2)}deg)`;
  }
}

// Initial fetch and gentle polling (2s) just to keep lamps/signal fresh without noise risk
window.addEventListener('load', () => {
  getStatus().catch(console.error);
  setInterval(() => getStatus().catch(console.error), 2000);
});

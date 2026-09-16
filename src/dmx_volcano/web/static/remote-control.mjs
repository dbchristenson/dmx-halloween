const $ = id => document.getElementById(id);
let state, layout, connected = false;
const holds = new Map();
const lightControls = new Map();
const message = text => { $('feedback').textContent = text; };
try { document.documentElement.dataset.theme = localStorage.getItem('volcano-theme') || 'light'; } catch {}
$('theme').onclick = () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('volcano-theme', theme); } catch {}
};
async function post(path, body, keepalive = false) {
  const response = await fetch(path, {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(body), keepalive, signal:AbortSignal.timeout(1500)});
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  const result = await response.json();
  if (!result.ok) throw new Error(result.reason || 'Request refused');
  return result;
}
async function action(path, body, success) {
  try { await post(path, body); message(success); await refresh(); }
  catch (error) { message(error.message); }
}
function release(kind) {
  const hold = holds.get(kind);
  if (!hold) return;
  holds.delete(kind);
  hold.released = true;
  clearTimeout(hold.timer);
  $(`${kind}Hold`).dataset.holding = 'false';
  if (hold.token) post('/api/remote/release', {token:hold.token}, true).catch(() => {});
  render();
}
function releaseAll() { for (const kind of [...holds.keys()]) release(kind); }
async function press(kind) {
  const button = $(`${kind}Hold`);
  if (button.disabled || holds.has(kind)) return;
  const hold = {released:false, token:null, timer:null};
  holds.set(kind, hold);
  button.dataset.holding = 'true';
  message(`${kind === 'flame' ? 'Flame' : 'CO₂'} hold requested. Release to stop.`);
  try {
    const result = await post('/api/remote/begin', {kind});
    hold.token = result.token;
    if (hold.released) {
      await post('/api/remote/release', {token:hold.token}, true);
      return;
    }
    async function renew() {
      if (hold.released) return;
      try {
        await post('/api/remote/renew', {token:hold.token});
        if (!hold.released) hold.timer = setTimeout(renew, 150);
      } catch (error) {
        // Keep this press consumed until an actual release; key repeat cannot restart it.
        clearTimeout(hold.timer);
        button.dataset.holding = 'false';
        post('/api/remote/release', {token:hold.token}, true).catch(() => {});
        message(`${error.message}. Release before pressing again.`);
      }
    }
    await renew();
  } catch (error) {
    button.dataset.holding = 'false';
    if (!hold.released) message(error.message);
  }
}
for (const kind of ['flame','co2']) {
  const button = $(`${kind}Hold`);
  let pointer = null;
  button.addEventListener('pointerdown', event => {
    if (event.button !== 0 || button.disabled || pointer !== null) return;
    event.preventDefault();
    pointer = event.pointerId;
    button.setPointerCapture(pointer);
    press(kind);
  });
  button.addEventListener('pointermove', event => {
    if (event.pointerId !== pointer) return;
    const r = button.getBoundingClientRect();
    if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) release(kind);
  });
  for (const eventName of ['pointerup','pointercancel','lostpointercapture']) {
    button.addEventListener(eventName, event => { if (event.pointerId === pointer) { pointer = null; release(kind); } });
  }
  button.addEventListener('keydown', event => {
    if (event.key === ' ' || event.key === 'Enter') {event.preventDefault();if (!event.repeat) press(kind);}
  });
  button.addEventListener('keyup', event => {if (event.key === ' ' || event.key === 'Enter') {event.preventDefault();release(kind);}});
  button.addEventListener('blur', () => release(kind));
  button.addEventListener('contextmenu', event => {event.preventDefault();release(kind);});
}
window.addEventListener('blur', releaseAll);
window.addEventListener('pagehide', releaseAll);
document.addEventListener('visibilitychange', () => {if (document.hidden) releaseAll();});
document.addEventListener('navigation-open', releaseAll);
document.addEventListener('keydown', event => {if (event.key === 'Escape') releaseAll();});
$('enable').onclick = () => {
  releaseAll();
  const on = !state?.test?.enabled;
  action('/api/test/enable', {on}, on ? 'Remote controls enabled. Show held dormant.' : 'Controls disabled. Show remains held dormant.');
};
$('arm').onclick = () => action('/api/arm', {}, 'Pyro armed. Hold a fixture button to activate.');
$('disarm').onclick = () => {releaseAll();action('/api/disarm', {}, 'Pyro disarmed.');};
$('fog').oninput = () => { $('fogValue').textContent = `${Math.round(Number($('fog').value) / 255 * 100)}%`; };
$('fog').onchange = () => action('/api/test/fog', {level:Number($('fog').value)}, 'Fog level updated.');
$('fogOff').onclick = () => action('/api/test/fog', {level:0}, 'Fog off.');
function buildLights() {
  for (const fixture of layout.fixtures.filter(f => !f.pyro && 'dimmer' in f.channels)) {
    const article = document.createElement('article');
    article.className = 'fixture light';
    const top = document.createElement('div'); top.className = 'fixture-top';
    const title = document.createElement('h3'); title.textContent = fixture.name.toUpperCase();
    const output = document.createElement('span'); output.className = 'output';
    top.append(title, output);
    const label = document.createElement('label'); label.textContent = 'Color';
    const color = document.createElement('input'); color.type = 'color'; color.value = '#ff8c28';color.setAttribute('aria-label', `${fixture.name} color`);label.append(color);
    const button = document.createElement('button');button.textContent = 'Turn on';button.disabled = true;
    article.append(top, label, button);$('lights').append(article);
    const control = {fixture, color, button, output};lightControls.set(fixture.name, control);
    const isOn = () => Boolean(state?.test?.overrides[fixture.channels.dimmer]);
    const send = on => action('/api/test/light', {name:fixture.name, on, color:color.value.match(/[a-f\d]{2}/gi).map(x => parseInt(x,16))}, `${fixture.name.toUpperCase()} ${on ? 'on' : 'off'}.`);
    button.onclick = () => send(!isOn());color.onchange = () => {if (isOn()) send(true);};
  }
}
function render() {
  const enabled = connected && state?.test?.enabled;
  $('connection').textContent = connected ? `${state.sim ? 'Simulation · fake DMX' : 'Live DMX'} · connected` : 'Connection lost · holds stopped';
  $('phase').textContent = state ? `${state.phase} ${state.hold ? '· show held' : ''}` : '';
  $('sessionTitle').textContent = enabled ? 'Remote controls enabled' : 'Remote controls off';
  $('sessionDetail').textContent = enabled ? 'Individual fixtures are under manual control.' : 'Enable while dormant to take control of individual fixtures.';
  $('enable').textContent = enabled ? 'Disable controls' : 'Enable controls';
  $('enable').disabled = !connected || (!enabled && state?.phase !== 'dormant');
  $('disarm').disabled = !connected || !state?.armed;
  $('arm').disabled = !enabled || state?.armed || state?.rehearsal;
  $('arm').textContent = state?.armed ? 'Pyro armed' : 'Arm pyro';
  $('pyroStatus').textContent = state?.rehearsal ? 'Rehearsal lock: pyro is disabled in the configuration.' : state?.armed ? 'Pyro armed. Physical machine arming is also required.' : 'Pyro disarmed. Enable controls, then arm to use the hold buttons.';
  for (const kind of ['flame', 'co2']) {
    const fixture = layout?.fixtures.find(f => f.name === kind);
    const channel = fixture?.channels[kind === 'flame' ? 'fire' : 'jet'];
    const on = connected && state?.frame[channel - 1] > 0;
    $(`${kind}Output`).textContent = connected ? (on ? 'Output on' : 'Output off') : 'Unknown';
    $(`${kind}Output`).classList.toggle('on', on);
    const gap = state?.test?.ready_in[kind] || 0;
    $(`${kind}Hold`).disabled = !enabled || !state?.armed || (!holds.has(kind) && gap > 0);
    $(`${kind}Ready`).textContent = !enabled ? 'Enable controls first' : !state?.armed ? 'Pyro locked' : gap > 0 ? `${on ? 'Active / cooldown' : 'Ready in'} ${gap.toFixed(1)} s` : 'Ready · mouse, touch, Space or Enter';
  }
  for (const {fixture, button, color, output} of lightControls.values()) {
    const on = Boolean(state?.test?.overrides[fixture.channels.dimmer]);
    button.disabled = !enabled;color.disabled = !enabled;
    button.textContent = on ? 'Turn off' : 'Turn on';button.setAttribute('aria-pressed', String(on));
    const live = connected && state?.frame[fixture.channels.dimmer - 1] > 0;
    output.textContent = connected ? (live ? 'On' : 'Off') : '—';output.classList.toggle('on', live);
  }
  $('fog').disabled = !enabled;$('fogOff').disabled = !enabled;
  const fogFixture = layout?.fixtures.find(f => 'fog' in f.channels);
  const level = state?.test?.overrides[fogFixture?.channels.fog] || 0;
  if (document.activeElement !== $('fog')) { $('fog').value = level; $('fogValue').textContent = `${Math.round(level/255*100)}%`; }
  const fogLive = connected && state?.frame[fogFixture?.channels.fog - 1] > 0;
  $('fogOutput').textContent = connected ? (fogLive ? 'Output on' : 'Output off') : 'Unknown';
}
async function refresh() {
  try {
    if (!layout) {const r = await fetch('/api/layout', {signal:AbortSignal.timeout(1500)});if (!r.ok) throw new Error();layout = await r.json();buildLights();}
    const r = await fetch('/api/state', {cache:'no-store', signal:AbortSignal.timeout(1500)});
    if (!r.ok) throw new Error();
    state = await r.json();connected = true;
    if (!state.test?.enabled || !state.armed) releaseAll();
  } catch { connected = false;releaseAll(); }
  render();
}
async function poll() {await refresh();setTimeout(poll, 250);}
poll();

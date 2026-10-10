/* New flight: arm the camera, then watch the flight controller.
 *
 * This page never commands the aircraft. Mission Planner arms and flies it; all
 * this does is open a flight record so incoming CAMERA_TRIGGER messages get
 * saved, and close it when the drone disarms.
 */
(function () {
  'use strict';

  const root = document.getElementById('newflight-root');
  if (!root) return;
  const recording = root.dataset.recording;

  // These describe what you configured in Mission Planner -- the controller
  // fires the shutter and the Pi captures on the MAVLink event it sends back.
  // Say what to set up, not what the Pi does, because the Pi does not decide.
  const TRIGGER_NOTES = {
    distance: 'Set CAM_TRIGG_DIST, or put DO_SET_CAM_TRIGG_DIST in the mission. ' +
      'The controller counts distance flown and fires the shutter. Best for ' +
      'even coverage.',
    waypoint: 'Put a DO_DIGICAM_CONTROL command at each waypoint. Fewer photos, ' +
      'tied to your plan, and the aircraft can hold still for each one.',
    interval: 'A steady timer, ignoring position. Coverage then depends on how ' +
      'fast you fly.'
  };

  function paintTriggerNote() {
    const note = document.getElementById('trigger-note');
    const checked = HC.$('[name="trigger"]:checked');
    if (note && checked) note.textContent = TRIGGER_NOTES[checked.value] || '';
  }
  HC.$$('[name="trigger"]').forEach(function (radio) {
    radio.addEventListener('change', function () {
      paintTriggerNote();
      HC.saveSetting({ trigger_mode: radio.value });
    });
  });
  paintTriggerNote();

  // Same setting the Debug view exposes, surfaced here because it changes what
  // every photo in the flight will mean — and this is the last screen before
  // the drone leaves the ground.
  HC.$$('[data-nf-mask]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const on = btn.dataset.nfMask === 'on';
      HC.$$('[data-nf-mask]').forEach(function (b) {
        b.classList.toggle('is-on', (b.dataset.nfMask === 'on') === on);
      });
      const label = document.getElementById('nf-mask-label');
      if (label) label.textContent = on ? 'On' : 'Off';
      HC.saveSetting({ mask_low_signal: on });
    });
  });

  const override = document.getElementById('override-trigger');
  if (override) {
    override.addEventListener('click', function () {
      HC.api('/api/settings', {
        method: 'PATCH', body: { trigger_source: 'dashboard' }
      }).then(function () { location.reload(); })
        .catch(function (err) { HC.toast(err.message, true); });
    });
  }

  const useMission = document.getElementById('use-mission-trigger');
  if (useMission) {
    useMission.addEventListener('click', function () {
      HC.api('/api/settings', {
        method: 'PATCH', body: { trigger_source: 'mission' }
      }).then(function () { location.reload(); })
        .catch(function (err) { HC.toast(err.message, true); });
    });
  }

  const start = document.getElementById('start-session');
  if (start) {
    start.addEventListener('click', async function () {
      start.disabled = true;
      start.textContent = 'Getting ready…';
      try {
        await HC.api('/api/flights', { method: 'POST' });
        location.reload();
      } catch (err) {
        HC.toast(err.message, true);
        start.disabled = false;
        start.textContent = 'Get the camera ready';
      }
    });
  }

  const cancel = document.getElementById('cancel-session');
  if (cancel) {
    cancel.addEventListener('click', async function () {
      const ok = await HC.confirmDialog({
        title: 'Cancel this flight?',
        body: 'Photos already saved are kept and the flight is closed. If nothing ' +
          'has been photographed yet, the empty flight is removed.',
        action: 'Cancel the flight'
      });
      if (!ok) return;
      try {
        await HC.api('/api/flights/' + recording + '/cancel', { method: 'POST' });
        window.location.href = '/';
      } catch (err) { HC.toast(err.message, true); }
    });
  }

  const finish = document.getElementById('finish-session');
  if (finish) {
    finish.addEventListener('click', async function () {
      finish.disabled = true;
      try {
        await HC.api('/api/flights/' + recording + '/process', { method: 'POST' });
        window.location.href = '/processing';
      } catch (err) {
        HC.toast(err.message, true);
        finish.disabled = false;
      }
    });
  }

  /* ── pre-flight card, kept current ───────────────────────────────────────── */
  // The checks and the mission card are re-rendered by the server every few
  // seconds, so uploading a new mission from the ground station, or the GPS
  // getting its fix, shows up here without a reload. Swapped only when the
  // markup actually changed, so nothing flickers while it stays the same.

  const preflight = document.getElementById('preflight');
  if (preflight && preflight.dataset.refresh) {
    let last = preflight.innerHTML.trim();
    HC.poll(async function () {
      const res = await fetch(preflight.dataset.refresh, { cache: 'no-store' });
      if (!res.ok) throw new Error('preflight ' + res.status);
      const html = (await res.text()).trim();
      if (html !== last) {
        preflight.innerHTML = html;
        last = html;
      }
      return true;
    }, 5000);
  }

  /* ── live telemetry while armed ──────────────────────────────────────────── */

  // A flight opens by itself when the drone arms, "Get the camera ready" or
  // not. If this page was opened before that, it is still showing the
  // pre-flight checklist: reload into the live view of the flight.
  if (!recording) {
    HC.poll(function () {
      return HC.api('/api/telemetry').then(function (snap) {
        if (snap.recording_flight) { location.reload(); return false; }
        return true;
      });
    }, 3000);
  }

  if (recording) {
    let wasArmed = null;
    HC.poll(function () {
      return HC.api('/api/telemetry').then(function (snap) {
        setText('tel-mode', snap.mode || (snap.connected ? '—' : 'not connected'));
        setText('tel-wp', snap.mission.count
          ? (snap.mission.current + ' of ' + snap.mission.count)
          : '—');
        setText('tel-alt', snap.position
          ? snap.position.rel_alt_m.toFixed(1) + ' m'
          : '—');
        setText('tel-gps', snap.gps.fix_type >= 2
          ? snap.gps.fix_label + ', ' + snap.gps.satellites + ' sats'
          : (snap.connected ? 'no fix' : '—'));

        const title = document.getElementById('session-title');
        const body = document.getElementById('session-body');
        if (snap.armed && title) {
          title.textContent = 'Recording photos';
          if (body) {
            body.textContent = 'Leave this page open — it closes the flight by ' +
              'itself when the drone disarms.';
          }
        }

        // The flight closes server-side on disarm; follow it to the processing
        // page so the operator sees the progress rather than a stale screen.
        if (wasArmed === true && snap.armed === false) {
          window.location.href = '/processing';
          return false;
        }
        wasArmed = snap.armed;

        // The flight stays marked recording while its photos are processed,
        // so landing shows up here as processing having started -- whether or
        // not this page was open to see the drone disarm.
        const proc = snap.processing || {};
        if (!snap.recording_flight || (proc.running && proc.flight_id === recording)) {
          window.location.href = '/processing';
          return false;
        }
        return true;
      });
    }, 2000);

    HC.poll(function () {
      return HC.api('/api/flights/' + recording).then(function (f) {
        setText('tel-photos', f.capture_count);
      });
    }, 4000);
  }

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }
}());

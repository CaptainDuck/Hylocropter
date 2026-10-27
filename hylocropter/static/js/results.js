/* The tapped photo, explained.
 *
 * Each photo is a patch of the heatmap. map.js sends hc:photo-pick when one is
 * tapped (or its pin is); this fills the card under the map with what that
 * photo's *plants* say -- not the soil between them, which reads yellow on
 * every flight -- and answers hc:photo-selected so the map rings the patch.
 *
 * A photo's status (flights.photo_level) is Okay, Dapat tingnan or Silipin
 * agad, from how much of its crown reads orange or red. The page opens on
 * the first flagged photo, and the verdict's "go through them" button steps
 * through the rest; with nothing flagged it opens on the lowest plant reading
 * and says that is still normal.
 *
 * Everything comes from the map's pins; nothing here asks the server for
 * anything but the image.
 */
(function () {
  'use strict';

  const panel = document.getElementById('square-panel');
  const strEl = document.getElementById('results-strings');
  const host = document.getElementById('plot-map');
  if (!panel || !strEl || !host) return;

  const S = JSON.parse(strEl.textContent);
  const pins = JSON.parse(host.dataset.pins || '[]');
  const flaggedIds = JSON.parse(panel.dataset.flagged || '[]');
  const byId = {};
  pins.forEach(function (p) { byId[p.id] = p; });
  const flagged = flaggedIds.map(function (id) { return byId[id]; })
    .filter(Boolean);

  const $ = function (id) { return document.getElementById(id); };
  // The statuses' colours, as on the map's pins and in the legend.
  const LEVEL_COLOUR = { healthy: '#2f8f3e', moderate: '#e0a020', stressed: '#c1442e' };

  function say(key, vars) {
    let text = S[key] || key;
    Object.keys(vars || {}).forEach(function (k) {
      text = text.split('{' + k + '}').join(vars[k]);
    });
    return text;
  }
  /** A share that is there but rounds to nothing still has to read as there. */
  function share(v) {
    if (v === null || v === undefined) return '?';
    return v > 0 && v < 0.1 ? '<0.1' : (Math.round(v * 10) / 10);
  }
  /** '15:18:53' -> '3:18:53 PM', the way the page header says the time. */
  function clock12(hms) {
    const h = parseInt(hms, 10);
    if (isNaN(h)) return hms;
    return ((h % 12) || 12) + hms.slice(2) + (h < 12 ? ' AM' : ' PM');
  }
  function has(v) { return v !== null && v !== undefined; }
  function orange(p) { return Math.max(0, (p.crown_weak || 0) - (p.crown_red || 0)); }

  let photoView = 'heatmap';
  let current = null;
  let tour = null;        // index into `flagged` while stepping through them

  function show(p, why) {
    current = p;
    const kicker = why === 'first' ? say('start_here')
      : (why === 'lowest' ? say('lowest_kicker') : '');
    $('sq-kicker').textContent = kicker;
    $('sq-kicker').hidden = !kicker;
    const hint = why === 'first' ? say('worst_hint')
      : (why === 'lowest' ? say('lowest_hint') : '');
    $('sq-hint').textContent = hint;
    $('sq-hint').hidden = !hint;

    $('sq-where').textContent = say('where', {
      n: p.number || '?', time: clock12(p.seconds || p.time)
    });
    const measured = has(p.mean);
    const level = p.level || 'healthy';
    const dot = $('sq-dot');
    dot.className = 'square-dot is-' + (!measured ? 'none' : level);
    dot.style.background = !measured ? '' : LEVEL_COLOUR[level];
    // What got it flagged, not a BNDVI figure: "+0.44" beside "worth a look"
    // reads as healthy and contradicts the label.
    $('sq-short').textContent = !measured ? '—'
      : (level === 'stressed' && p.plant_red > 0 ? share(p.plant_red) + '%'
        : (has(p.crown_weak) ? share(orange(p)) + '%' : ''));
    $('sq-name').textContent = !measured ? say('no_reading') : say(level);
    $('sq-mean').textContent = !measured ? '' : say(level + '_mean');
    $('sq-detail').textContent = p.footprint && measured
      ? say('cell_detail', {
        w: Math.round(p.footprint.width_m), h: Math.round(p.footprint.height_m),
        alt: has(p.alt) ? Number(p.alt).toFixed(1) : '?'
      })
      : '';

    const why2 = [];
    // Orange and red said apart, as the paper's classes are: the crown's
    // below-0.30 share includes any red, so take the red out of it.
    if (has(p.crown_weak)) why2.push(say('photo_crown', { w: share(orange(p)) }));
    // Red is read on the whole plant area, dark dying tissue included
    // (bndvi.plant_red_stats) -- not only the bright crown.
    if (p.plant_red > 0) why2.push(say('photo_crown_red', { r: share(p.plant_red) }));
    if (p.weaker) why2.push(say('photo_weaker'));
    $('sq-share').textContent = why2.join(' ');
    $('sq-open').href = '/capture/' + p.id;
    paintPhoto(p);
    paintTour();
  }

  function paintPhoto(p) {
    const raw = photoView === 'raw' && p.raw;
    // The heatmap thumbnail: the same image as the full false-colour PNG, at
    // about the width this card shows it -- 14 KB instead of 10 MB per tap over
    // the Pi's hotspot. "Open the photo" has the full size.
    $('sq-img').src = raw ? p.raw : (p.thumb || p.falsecolor);
    $('sq-photo-note').textContent = say(raw ? 'raw_note' : 'photo_note');
    panel.querySelectorAll('.photo-switch button').forEach(function (b) {
      b.classList.toggle('is-active', b.dataset.view === (raw ? 'raw' : 'heatmap'));
    });
  }

  function paintTour() {
    const bar = $('sq-tour');
    bar.hidden = tour === null;
    if (tour === null) return;
    const many = flagged.length > 1;
    $('sq-tour-prev').hidden = !many;
    $('sq-tour-next').hidden = !many;
    $('sq-tour-text').textContent = say('tour', { i: tour + 1, n: flagged.length });
  }

  panel.querySelectorAll('.photo-switch button').forEach(function (b) {
    b.addEventListener('click', function () {
      photoView = b.dataset.view;
      if (current) paintPhoto(current);
    });
  });

  function select(p, why, reveal) {
    show(p, why);
    document.dispatchEvent(new CustomEvent('hc:photo-selected', {
      detail: { id: p.id, reveal: !!reveal }
    }));
  }

  function step(by) {
    if (!flagged.length) return;
    tour = tour === null ? 0 : (tour + by + flagged.length) % flagged.length;
    select(flagged[tour], tour === 0 && by === 0 ? 'first' : 'tour', true);
  }

  $('sq-tour-next').addEventListener('click', function () { step(1); });
  $('sq-tour-prev').addEventListener('click', function () { step(-1); });
  const go = $('show-flagged');
  if (go) {
    go.addEventListener('click', function () {
      tour = null;
      step(0);
      panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  document.addEventListener('hc:photo-pick', function (e) {
    const p = byId[e.detail.id];
    if (!p) return;
    const i = flagged.indexOf(p);
    tour = i >= 0 ? i : null;    // a tap on a flagged photo keeps the stepper
    select(p, 'tap');
    // Take the farmer to the answer: the card sits below the map on every
    // screen size, so a tap would otherwise seem to do nothing.
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  // Open on the first flagged photo; with none, on the lowest plant reading.
  if (flagged.length) {
    step(0);
  } else {
    let low = null;
    pins.forEach(function (p) {
      const v = has(p.plant) ? p.plant : p.mean;
      if (has(v) && (low === null || v < (has(low.plant) ? low.plant : low.mean))) low = p;
    });
    if (low) select(low, 'lowest');
  }
}());

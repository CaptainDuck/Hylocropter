/* The tapped square, explained.
 *
 * map.js sends hc:cell-pick when a square is tapped; this fills the panel under
 * the map with what that square means -- its colour's name and what to do about
 * it, how many photos it averages, and the photo taken nearest its centre --
 * then answers hc:cell-selected so the map rings it. The page opens on the
 * lowest-reading square, because that is where a walk should start.
 *
 * Everything comes from data the page already has (flights.grid_cells() and
 * the map's pins); nothing here asks the server for anything but the photo.
 */
(function () {
  'use strict';

  const panel = document.getElementById('square-panel');
  const strEl = document.getElementById('results-strings');
  if (!panel || !strEl) return;

  const S = JSON.parse(strEl.textContent);
  const cells = JSON.parse(panel.dataset.cells || '[]');
  const cellM = JSON.parse(panel.dataset.cellM || 'null');
  const cols = parseInt(panel.dataset.cols, 10) || 14;
  const host = document.getElementById('plot-map');
  const pins = host ? JSON.parse(host.dataset.pins || '[]') : [];
  const pinById = {};
  pins.forEach(function (p) { pinById[p.id] = p; });

  const BG = { healthy: '#2f8f3e', moderate: '#e0a020', stressed: '#c1442e' };
  const $ = function (id) { return document.getElementById(id); };

  function say(key, vars) {
    let text = S[key] || key;
    Object.keys(vars || {}).forEach(function (k) {
      text = text.split('{' + k + '}').join(vars[k]);
    });
    return text;
  }
  function sayN(key, n, vars) {
    vars = Object.assign({ n: n }, vars);
    return say(n === 1 && S[key + '_one'] ? key + '_one' : key, vars);
  }
  function fmt(v) { return (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(2); }
  /** '15:18:53' -> '3:18:53 PM', the way the page header says the time. */
  function clock12(hms) {
    const h = parseInt(hms, 10);
    if (isNaN(h)) return hms;
    return ((h % 12) || 12) + hms.slice(2) + (h < 12 ? ' AM' : ' PM');
  }

  let photoView = 'bands';
  let current = null;

  function show(index, why) {
    current = index;
    const c = cells[index];
    const row = (index / cols | 0) + 1, col = index % cols + 1;
    $('sq-where').textContent = say('where', { r: row, c: col });
    $('sq-kicker').textContent = why === 'worst' ? say('start_here') : '';
    $('sq-kicker').hidden = why !== 'worst';
    $('sq-hint').textContent = why === 'worst' ? say('worst_hint') : '';
    $('sq-hint').hidden = why !== 'worst';

    const dot = $('sq-dot');
    if (!c) {
      dot.className = 'square-dot is-none';
      dot.style.background = '';
      $('sq-short').textContent = '—';
      $('sq-name').textContent = say('no_photo_name');
      $('sq-mean').textContent = say('no_photo_mean');
      $('sq-detail').textContent = '';
      $('sq-patch').hidden = true;
      $('sq-photo').hidden = true;
      return;
    }
    dot.className = 'square-dot is-' + c.band;
    dot.style.background = BG[c.band];
    $('sq-short').textContent = fmt(c.mean);
    $('sq-name').textContent = say(c.band);
    $('sq-mean').textContent = say(c.band + '_mean');
    $('sq-detail').textContent = sayN('cell_detail', c.photos, {
      w: cellM ? cellM[0] : '?', h: cellM ? cellM[1] : '?', v: fmt(c.mean)
    });

    // A green average can hide a red corner. Say so when the photos in a
    // square that isn't red themselves hold a real share of weaker ground.
    const patch = $('sq-patch');
    const weak = c.band === 'healthy'
      ? (c.stressed_pct >= 5 ? ['stressed', c.stressed_pct]
        : (c.moderate_pct >= 10 ? ['moderate', c.moderate_pct] : null))
      : null;
    patch.hidden = !weak;
    if (weak) {
      patch.textContent = say('cell_patches', {
        pct: Math.round(weak[1]), band: say(weak[0]).toLowerCase()
      });
    }

    const p = pinById[c.photo];
    $('sq-photo').hidden = !p;
    if (p) {
      paintPhoto(p);
      $('sq-photo-meta').textContent = say('photo_meta', {
        n: p.number || '?', time: clock12(p.seconds || p.time),
        alt: p.alt !== null && p.alt !== undefined ? Number(p.alt).toFixed(1) : '?',
        v: p.mean !== null ? fmt(p.mean) : '—',
        g: p.healthy_pct !== null && p.healthy_pct !== undefined
          ? Math.round(p.healthy_pct) : '?'
      });
      $('sq-open').href = '/capture/' + p.id;
    }
  }

  function paintPhoto(p) {
    const raw = photoView === 'raw' && p.raw;
    $('sq-img').src = raw ? p.raw : p.bands;
    $('sq-photo-note').textContent = say(raw ? 'raw_note' : 'photo_note');
    panel.querySelectorAll('.photo-switch button').forEach(function (b) {
      b.classList.toggle('is-active', b.dataset.view === (raw ? 'raw' : 'bands'));
    });
  }

  panel.querySelectorAll('.photo-switch button').forEach(function (b) {
    b.addEventListener('click', function () {
      photoView = b.dataset.view;
      const c = cells[current];
      if (c && pinById[c.photo]) paintPhoto(pinById[c.photo]);
    });
  });

  function select(index, why, reveal) {
    show(index, why);
    document.dispatchEvent(new CustomEvent('hc:cell-selected', {
      detail: { index: index, reveal: !!reveal }
    }));
  }

  document.addEventListener('hc:cell-pick', function (e) {
    select(e.detail.index, 'tap');
    if (window.matchMedia('(max-width: 760px)').matches) {
      panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });

  // Open on the lowest-reading square.
  let worst = null;
  cells.forEach(function (c, i) {
    if (c && (worst === null || c.mean < cells[worst].mean)) worst = i;
  });
  if (worst !== null) select(worst, 'worst', true);
}());

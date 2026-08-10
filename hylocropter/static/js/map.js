/* Farm map.
 *
 * Four ways of looking at the same flight, because they answer different
 * questions:
 *
 *   Photos — every capture's false-colour image drawn at the patch of ground it
 *            actually covers, rotated to the heading the drone was flying. This
 *            is the real farm heatmap: full detail, and honest about coverage
 *            because ground nobody photographed simply stays bare imagery.
 *   Grid   — the same data averaged into 14x9 cells. Coarser, but stable and
 *            comparable between flights, and it's what the summary numbers and
 *            the hover readout are computed from.
 *   Bands  — the grid at your three thresholds, for "which rows do I walk".
 *   Off    — satellite imagery alone.
 *
 * Footprints come from simple trigonometry: at height h a lens with horizontal
 * angle of view a covers 2*h*tan(a/2) across. For the Pi Camera v2 (62.2 x 48.8
 * degrees) at 12 m that is about 14.5 x 10.9 m.
 *
 * Leaflet and its images are vendored; tiles come off the Pi's own disk. Nothing
 * here touches the network at run time.
 */
(function () {
  'use strict';

  const host = document.getElementById('plot-map');
  if (!host || !window.L) return;

  // `flight` is null before the first flight. The map still renders then, because
  // finding the farm on the imagery is the thing to do before flying.
  const flight = JSON.parse(host.dataset.flight || 'null');
  const coverage = JSON.parse(host.dataset.coverage);
  const vicinity = JSON.parse(host.dataset.vicinity);
  const pins = JSON.parse(host.dataset.pins || '[]');
  let blocks = JSON.parse(host.dataset.blocks || '[]');

  // Switching location changes the vicinity, the coverage outline and which
  // tiles exist, all of which are rendered server-side — so reload rather than
  // try to rebuild the map in place and miss one of them.
  const siteSel = document.getElementById('map-site');
  if (siteSel) {
    siteSel.addEventListener('change', function () {
      siteSel.disabled = true;
      HC.api('/api/settings', {
        method: 'PATCH', body: { active_site: siteSel.value }
      }).then(function () {
        location.reload();
      }).catch(function (err) {
        siteSel.disabled = false;
        HC.toast('Could not switch location: ' + err.message, true);
      });
    });
  }

  let mode = 'photos';
  let overlay = null;
  let opacity = 0.82;
  let showPins = true;
  const mosaicCache = {};

  const M_PER_DEG_LAT = 111320;
  function mPerDegLon(lat) { return M_PER_DEG_LAT * Math.cos(lat * Math.PI / 180); }

  /* ── bounds ─────────────────────────────────────────────────────────────── */

  const b = flight && flight.bounds;
  const gridBounds = b
    ? [[b.south, b.west], [b.north, b.east]]
    : vicinityBounds();

  /** A square of `sideM` metres centred on lat/lon, as Leaflet bounds. */
  function squareBounds(lat, lon, sideM) {
    const half = sideM / 2;
    const dLat = half / M_PER_DEG_LAT;
    const dLon = half / mPerDegLon(lat);
    return [[lat - dLat, lon - dLon], [lat + dLat, lon + dLon]];
  }

  function vicinityBounds() {
    return squareBounds(vicinity.lat, vicinity.lon, vicinity.box_m);
  }

  /** Union of every photo's ground footprint — what the mosaic has to cover. */
  function photoBounds() {
    let s = 90, w = 180, n = -90, e = -180, any = false;
    pins.forEach(function (p) {
      if (!p.footprint) return;
      // rotation means the footprint's extent is the diagonal at worst, so pad
      // by the half-diagonal rather than the half-width
      const r = Math.sqrt(Math.pow(p.footprint.half_w_m, 2) +
                          Math.pow(p.footprint.half_h_m, 2));
      const dLat = r / M_PER_DEG_LAT;
      const dLon = r / mPerDegLon(p.lat);
      s = Math.min(s, p.lat - dLat); n = Math.max(n, p.lat + dLat);
      w = Math.min(w, p.lon - dLon); e = Math.max(e, p.lon + dLon);
      any = true;
    });
    return any ? [[s, w], [n, e]] : null;
  }

  const mosaicBounds = photoBounds();
  const hasFootprints = !!mosaicBounds;

  /* ── the map ────────────────────────────────────────────────────────────── */

  const map = L.map(host, {
    zoomSnap: 0,
    zoomDelta: 0.5,
    zoomControl: true,
    attributionControl: true,
    // Scroll to zoom, like every other web map. The old setting made the map
    // feel inert, and a farm map is something you want to move around in.
    scrollWheelZoom: true,
    // Two fingers to scroll past on a phone, so the page still scrolls.
    dragging: !L.Browser.mobile ? true : true,
    tap: true
  });

  /* Only the zoom levels in the manifest exist on disk, so bound the *native*
     range and let Leaflet scale what we have. Without this, zooming past either
     end asks for tiles we never downloaded and renders blank. */
  const zooms = (coverage.zooms && coverage.zooms.length) ? coverage.zooms : [16, 19];
  L.tileLayer('/tiles/{z}/{x}/{y}.jpg', {
    minZoom: Math.min(12, zooms[0]),
    maxZoom: 22,
    minNativeZoom: zooms[0],
    maxNativeZoom: zooms[zooms.length - 1],
    attribution: coverage.attribution || 'Imagery © Esri'
  }).addTo(map);

  /* Opening view. With a flight, frame the flight. Without one, frame the drawn
     blocks if there are any, and otherwise the whole vicinity — which is the
     "I don't know where the farm is yet, show me everything" case. */
  function allBlocksBounds() {
    if (!blocks.length) return null;
    let s = 90, w = 180, n = -90, e = -180;
    blocks.forEach(function (b) {
      s = Math.min(s, b.south); n = Math.max(n, b.north);
      w = Math.min(w, b.west); e = Math.max(e, b.east);
    });
    // pad so a single small block doesn't open zoomed to the point of no context
    const padLat = (n - s) * 0.6 + 0.0004;
    const padLon = (e - w) * 0.6 + 0.0004;
    return [[s - padLat, w - padLon], [n + padLat, e + padLon]];
  }

  const openingBounds = mosaicBounds ||
    (flight ? gridBounds : (allBlocksBounds() || vicinityBounds()));
  map.fitBounds(openingBounds, { padding: [24, 24] });
  L.control.scale({ imperial: false, position: 'bottomright' }).addTo(map);

  // The flight extent. Only meaningful once something has been flown — before
  // that it would just be a box drawn around the middle of the vicinity.
  if (flight) {
    L.rectangle(gridBounds, {
      color: '#f5ead8', weight: 2, dashArray: '6 5', fill: false, interactive: false
    }).addTo(map);
  }

  // How far the offline imagery goes, so a blank patch is distinguishable from
  // ground that is genuinely bare.
  if (coverage.has_tiles && (coverage.tile_bounds || coverage.bounds)) {
    const c = coverage.tile_bounds || coverage.bounds;
    L.rectangle([[c.south, c.west], [c.north, c.east]], {
      color: '#8c491a', weight: 1.5, dashArray: '2 6', fill: false,
      interactive: false
    }).addTo(map).bindTooltip(
      'Edge of the downloaded map — ' +
      (coverage.tile_area_ha ? coverage.tile_area_ha + ' ha' : '') +
      ' of imagery, ' + coverage.size_label + '. This is the vicinity to search ' +
      'in, not the area you fly. Beyond this line the map is blank.',
      { sticky: true });
  }

  /* ── survey blocks ──────────────────────────────────────────────────────────
   *
   * The vicinity is tens of hectares; the drone flies one or two at a time. The
   * farm sits somewhere inside it, its exact outline unknown, and it has several
   * plots — so blocks are a *list* of rectangles, each drawn corner to corner and
   * named. Everything the mission planner says is scaled to one of them, and the
   * names are also the choices on the All flights filter.
   *
   * Two clicks rather than a centre and a size, because a plot is rarely square
   * and "drag a box round it on the imagery" is how anyone would describe this.
   */

  const BLOCK_COLOUR = '#f0a03c';
  const layers = {};            // block id -> Leaflet rectangle
  let drawing = false;
  let draft = [];               // corners clicked so far, in order
  let lastReason = null;        // why the draft is not saveable yet
  let pending = null;           // the rectangle being drawn
  let rubber = null;            // live rectangle following the cursor

  const els = {
    list: document.getElementById('block-list'),
    empty: document.getElementById('block-empty'),
    count: document.getElementById('block-count'),
    start: document.getElementById('block-start'),
    edit: document.getElementById('block-edit'),
    hint: document.getElementById('block-hint'),
    where: document.getElementById('block-where'),
    name: document.getElementById('block-name'),
    save: document.getElementById('block-save'),
    undo: document.getElementById('block-undo'),
    cancel: document.getElementById('block-cancel')
  };

  function boundsOf(b) {
    return [[b.south, b.west], [b.north, b.east]];
  }

  /** Metres across and down, and the area. Mirrors flights.block_dimensions(). */
  function dimsOf(b) {
    const midLat = (b.south + b.north) / 2;
    const w = (b.east - b.west) * mPerDegLon(midLat);
    const h = (b.north - b.south) * M_PER_DEG_LAT;
    return { w: w, h: h, ha: w * h / 10000 };
  }

  function describeBlock(b) {
    const d = dimsOf(b);
    return Math.round(d.w) + ' × ' + Math.round(d.h) + ' m · ' +
      d.ha.toFixed(2) + ' ha';
  }

  function drawBlocks() {
    Object.keys(layers).forEach(function (id) {
      layers[id].remove();
      delete layers[id];
    });
    blocks.forEach(function (b) {
      // Draw the outline that was traced. Falling back to the bounding box keeps
      // blocks saved before polygons existed on the map.
      const style = {
        color: BLOCK_COLOUR, weight: 2.5, fillColor: BLOCK_COLOUR,
        fillOpacity: 0.10, interactive: false
      };
      const rect = (b.points && b.points.length >= 3)
        ? L.polygon(b.points, style).addTo(map)
        : L.rectangle(boundsOf(b), style).addTo(map);
      // Name only. The dimensions are in the panel list, and repeating them here
      // makes labels wide enough to collide once there are a few blocks.
      rect.bindTooltip(b.name, {
        permanent: true, direction: 'center', className: 'block-label'
      });
      layers[b.id] = rect;
    });
  }

  function paintList() {
    if (els.count) els.count.textContent = String(blocks.length);
    if (els.empty) els.empty.hidden = blocks.length > 0;
    if (!els.list) return;
    els.list.innerHTML = blocks.map(function (b) {
      return '<li class="block-item" data-id="' + esc(b.id) + '">' +
        '<button class="block-zoom" data-id="' + esc(b.id) + '">' +
        '<span class="block-name">' + esc(b.name) + '</span>' +
        '<span class="block-dims">' + esc(describeBlock(b)) + '</span>' +
        '</button>' +
        '<button class="block-del" data-id="' + esc(b.id) +
        '" title="Delete ' + esc(b.name) + '">\u00d7</button></li>';
    }).join('');
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
               "'": '&#39;' }[c];
    });
  }

  /* ── drawing a new block ─────────────────────────────────────────────────── */

  function setDrawing(on) {
    drawing = on;
    draft = [];
    pending = null;
    if (els.undo) els.undo.hidden = true;
    if (rubber) { rubber.remove(); rubber = null; }
    if (els.edit) els.edit.hidden = !on;
    if (els.start) els.start.hidden = on;
    host.classList.toggle('is-picking', on);
    if (on) {
      /* You cannot draw a plot on a view tighter than a plot. Arriving from a
         past flight the map is framed on ~60 m of ground, where a whole field
         would be a couple of pixels tall — so pull back to something workable
         first, rather than leaving the operator to guess why their box keeps
         coming out "too small to fly". */
      const view = map.getBounds();
      const spanM = (view.getNorth() - view.getSouth()) * M_PER_DEG_LAT;
      if (spanM < 200) {
        map.fitBounds(allBlocksBounds() || vicinityBounds(), { padding: [24, 24] });
      }
      if (els.name) els.name.value = suggestName();
      if (els.where) els.where.textContent = 'no corners yet';
      if (els.hint) els.hint.textContent = draftHint(false);
    }
    if (els.save) els.save.disabled = true;
  }

  /** "Block 3" when there are two. Editable, but never blank. */
  function suggestName() {
    const taken = blocks.map(function (b) { return b.name; });
    let n = blocks.length + 1;
    while (taken.indexOf('Block ' + n) !== -1) n++;
    return 'Block ' + n;
  }

  /* One click, one corner — no shortcuts and no modes.
     There was briefly a rule where two clicks meant "opposite corners of a
     rectangle", and it was a mistake twice over: the shape jumped when the third
     click replaced that derived rectangle with a triangle of the clicked points,
     and the four derived corners were not the points anyone had put down. There
     is no longer any reason to privilege a north-facing box either, because the
     planner orients flight lines to the plot's own axis whatever angle it lies
     at. What you click is what you get. */
  function draftPoints() {
    return draft.map(function (p) { return [p.lat, p.lng]; });
  }

  /** Does the outline cross itself? Mirrors flights.is_simple_polygon.
   *  Clicking corners out of order makes a bow tie, whose shoelace area is the
   *  difference of its two lobes rather than their sum — so it would report a
   *  plot far smaller than the ground it spans. Caught here so the hint can say
   *  why, rather than leaving Save mysteriously dead. */
  function selfCrossing(pts) {
    const n = pts.length;
    if (n < 4) return false;
    const k = mPerDegLon(pts[0][0]);
    const xy = pts.map(function (p) {
      return [(p[1] - pts[0][1]) * k, (p[0] - pts[0][0]) * M_PER_DEG_LAT];
    });
    function side(a, b, c) {
      return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    }
    for (let i = 0; i < n; i++) {
      const a = xy[i], b = xy[(i + 1) % n];
      for (let j = i + 1; j < n; j++) {
        if ((j + 1) % n === i || (i + 1) % n === j) continue;
        const c = xy[j], d = xy[(j + 1) % n];
        if ((side(a, b, c) > 0) !== (side(a, b, d) > 0) &&
            (side(c, d, a) > 0) !== (side(c, d, b) > 0)) return true;
      }
    }
    return false;
  }

  /** Shoelace area in m², mirroring flights.polygon_area_m2. */
  function draftArea(pts) {
    if (pts.length < 3) return 0;
    const k = mPerDegLon(pts[0][0]);
    let t = 0;
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i], b = pts[(i + 1) % pts.length];
      t += ((a[1] - pts[0][1]) * k) * ((b[0] - pts[0][0]) * M_PER_DEG_LAT)
         - ((b[1] - pts[0][1]) * k) * ((a[0] - pts[0][0]) * M_PER_DEG_LAT);
    }
    return Math.abs(t) / 2;
  }

  function describeDraft(pts) {
    if (pts.length < 3) {
      return pts.length + (pts.length === 1 ? ' corner set' : ' corners set');
    }
    return pts.length + ' corners · ' + (draftArea(pts) / 10000).toFixed(2) + ' ha';
  }

  /** The solid shape is exactly what will be saved. Anything following the mouse
   *  is drawn dashed and unfilled, so the preview can never be mistaken for the
   *  block — which is precisely what went wrong before. */
  function showRubber(pts, cursor) {
    if (rubber) { rubber.remove(); rubber = null; }
    if (!pts.length) return;
    rubber = L.layerGroup().addTo(map);

    if (pts.length >= 3) {
      L.polygon(pts, {
        color: BLOCK_COLOUR, weight: 2.5, fillColor: BLOCK_COLOUR,
        fillOpacity: 0.10, interactive: false
      }).addTo(rubber);
    } else if (pts.length === 2) {
      L.polyline(pts, {
        color: BLOCK_COLOUR, weight: 2.5, interactive: false
      }).addTo(rubber);
    }

    if (cursor) {
      const to = [cursor.lat, cursor.lng];
      L.polyline([pts[pts.length - 1], to], {
        color: BLOCK_COLOUR, weight: 1.5, dashArray: '4 5', interactive: false
      }).addTo(rubber);
      // Also show the edge that would close the ring, so the shape you are about
      // to make is legible before you commit the corner.
      if (pts.length >= 2) {
        L.polyline([to, pts[0]], {
          color: BLOCK_COLOUR, weight: 1.5, dashArray: '4 5', opacity: 0.55,
          interactive: false
        }).addTo(rubber);
      }
    }

    // A dot per placed corner, filled white so a committed corner reads
    // differently from the line chasing the cursor.
    pts.forEach(function (p) {
      L.circleMarker(p, {
        radius: 3.5, color: BLOCK_COLOUR, fillColor: '#fff', fillOpacity: 1,
        weight: 2, interactive: false
      }).addTo(rubber);
    });
  }

  function refreshDraft(cursor) {
    const pts = draftPoints();
    if (!pts.length) {
      if (rubber) { rubber.remove(); rubber = null; }
      if (els.where) els.where.textContent = 'no corners yet';
      if (els.save) els.save.disabled = true;
      pending = null;
      return;
    }
    showRubber(pts, draft.length >= 1 ? cursor : null);
    const crossed = selfCrossing(pts);
    const usable = pts.length >= 3 && draftArea(pts) >= 25 && !crossed;
    pending = usable ? { points: pts } : null;
    lastReason = crossed ? 'crossed' : (pts.length >= 3 ? 'thin' : null);
    if (els.save) els.save.disabled = !usable;
    if (els.where) els.where.textContent = describeDraft(pts);
    if (els.undo) els.undo.hidden = draft.length === 0;
    if (els.hint) els.hint.textContent = draftHint(usable);
  }

  function draftHint(usable) {
    if (draft.length === 0) {
      return 'Click each corner of the plot, going round the edge. Three or ' +
        'more corners, then save.';
    }
    if (draft.length === 1) return 'Click the next corner along the edge.';
    if (draft.length === 2) return 'One more corner and it is a shape.';
    if (usable) return 'Keep going round the plot, or name it and save.';
    return lastReason === 'crossed'
      ? 'The outline crosses itself — go round the edge in order, or undo the ' +
        'last corner.'
      : 'Those corners are almost in a line — click one off to the side.';
  }

  function onDrawClick(latlng) {
    draft.push(latlng);
    refreshDraft(null);
  }

  /* ── persistence ─────────────────────────────────────────────────────────── */

  /** The whole list goes over in one PATCH. It is a handful of rectangles, and
   *  one round trip means the server's validation is the only arbiter of what a
   *  valid block is. */
  async function persist(next, what) {
    try {
      const res = await HC.api('/api/settings',
        { method: 'PATCH', body: { survey_blocks: next } });
      (res.warnings || []).forEach(function (w) { HC.toast(w, true); });
      // Trust what came back: the server normalises corners, ids and names.
      blocks = (res.settings && res.settings.survey_blocks) || next;
      blocks = blocks.map(function (b) { return b; });
      drawBlocks();
      paintList();
      note(what);
      return true;
    } catch (err) {
      HC.toast('Could not save: ' + err.message, true);
      return false;
    }
  }

  if (els.start) {
    els.start.addEventListener('click', function () { setDrawing(true); });
  }

  if (els.undo) {
    els.undo.addEventListener('click', function () {
      // One misplaced corner shouldn't cost the whole outline — tracing a
      // ten-sided plot and starting over because of the last click is the sort
      // of thing that makes people give up and draw a box instead.
      draft.pop();
      refreshDraft(null);
    });
  }

  if (els.cancel) {
    els.cancel.addEventListener('click', function () {
      setDrawing(false);
      note(IDLE_HINT);
    });
  }

  if (els.save) {
    els.save.addEventListener('click', async function () {
      if (!pending) return;
      els.save.disabled = true;
      const name = (els.name && els.name.value.trim()) || suggestName();
      const next = blocks.concat([{
        id: 'b' + Date.now().toString(36),
        name: name,
        // Points only. The server derives the bounding box from them, so there
        // is one place that decides what a block's extent is.
        points: pending.points
      }]);
      const ok = await persist(next,
        name + ' saved — ' + describeDraft(pending.points) +
        '. The mission planner on the New flight page can plan for it now.');
      if (!ok) { els.save.disabled = false; return; }
      setDrawing(false);
    });
  }

  // Zoom to a block, or delete it. Delegated, because the list is re-rendered.
  if (els.list) {
    els.list.addEventListener('click', async function (e) {
      const zoom = e.target.closest('.block-zoom');
      const del = e.target.closest('.block-del');
      if (zoom) {
        const b = blocks.find(function (x) { return x.id === zoom.dataset.id; });
        if (b) {
          map.fitBounds(boundsOf(b), { padding: [40, 40] });
          note(b.name + ' — ' + describeBlock(b));
        }
        return;
      }
      if (!del) return;
      const b = blocks.find(function (x) { return x.id === del.dataset.id; });
      if (!b) return;
      const yes = await HC.confirmDialog({
        title: 'Delete ' + b.name + '?',
        body: 'The block is removed from the map and from the All flights ' +
              'filter. Flights already recorded under this name keep it.',
        action: 'Delete'
      });
      if (!yes) return;
      await persist(blocks.filter(function (x) { return x.id !== b.id; }),
                    b.name + ' deleted.');
    });
  }

  drawBlocks();
  paintList();

  /* ── photo mosaic ───────────────────────────────────────────────────────── */

  /** Draw every capture's false-colour thumbnail at its own ground footprint.
   *
   *  Built into one canvas and added as a single image overlay: twenty separate
   *  overlays would each be a DOM node Leaflet has to reposition on every pan.
   *  Photos overlap (at 12 m the footprint is ~14 m across and the trigger fires
   *  every 5 m), and later ones simply draw over earlier ones — this is a
   *  telemetry-placed mosaic, not a blended orthomosaic, which is exactly what
   *  the thesis describes.
   */
  function buildMosaic() {
    return new Promise(function (resolve) {
      if (!hasFootprints) { resolve(null); return; }
      const [[south, west], [north, east]] = mosaicBounds;
      const midLat = (south + north) / 2;
      const spanLonM = (east - west) * mPerDegLon(midLat);
      const spanLatM = (north - south) * M_PER_DEG_LAT;

      // ~2000 px on the long edge: enough that a 14 m footprint gets a couple of
      // hundred pixels, without making a canvas the Pi's browser chokes on.
      const target = 2000;
      const pxPerM = target / Math.max(spanLonM, spanLatM);
      const cv = document.createElement('canvas');
      cv.width = Math.max(64, Math.round(spanLonM * pxPerM));
      cv.height = Math.max(64, Math.round(spanLatM * pxPerM));
      const ctx = cv.getContext('2d');

      const usable = pins.filter(function (p) { return p.footprint && p.thumb; });
      if (!usable.length) { resolve(null); return; }

      let pending = usable.length;
      usable.forEach(function (p) {
        const img = new Image();
        img.onload = function () {
          const x = (p.lon - west) * mPerDegLon(midLat) * pxPerM;
          const y = (north - p.lat) * M_PER_DEG_LAT * pxPerM;
          const w = p.footprint.half_w_m * 2 * pxPerM;
          const h = p.footprint.half_h_m * 2 * pxPerM;
          ctx.save();
          ctx.translate(x, y);
          // Heading is degrees clockwise from north, which is also the screen
          // rotation for a north-up map.
          ctx.rotate((p.heading || 0) * Math.PI / 180);
          ctx.drawImage(img, -w / 2, -h / 2, w, h);
          ctx.restore();
          if (--pending === 0) resolve(cv.toDataURL());
        };
        img.onerror = function () { if (--pending === 0) resolve(cv.toDataURL()); };
        img.src = p.thumb;
      });
    });
  }

  /* ── grid overlay ───────────────────────────────────────────────────────── */

  /** Nearest covered cell for every empty cell.
   *
   *  For interpolation only. Without it, smooth upscaling blends each covered
   *  cell's *alpha* toward its empty neighbours and a sparse flight fades to
   *  nearly invisible over the imagery. The alpha mask below then hides the
   *  filled cells completely, so no unvisited ground is ever shown.
   */
  function fillNearest(cells, cols, rows) {
    const filled = cells.slice();
    const known = [];
    for (let i = 0; i < cells.length; i++) {
      if (cells[i] !== null && cells[i] !== undefined) {
        known.push([i % cols, (i / cols) | 0, cells[i]]);
      }
    }
    if (!known.length) return filled;
    for (let i = 0; i < filled.length; i++) {
      if (filled[i] !== null && filled[i] !== undefined) continue;
      const x = i % cols, y = (i / cols) | 0;
      let best = Infinity, val = known[0][2];
      for (let k = 0; k < known.length; k++) {
        const dx = known[k][0] - x, dy = known[k][1] - y;
        const d = dx * dx + dy * dy;
        if (d < best) { best = d; val = known[k][2]; }
      }
      filled[i] = val;
    }
    return filled;
  }

  function gridCanvas(bands) {
    const g = flight && flight.grid;
    if (!g) return null;
    const th = flight.thresholds || { healthy: 0.3, moderate: 0.1 };
    const colours = bands ? g.cells : fillNearest(g.cells, g.cols, g.rows);

    const cv = document.createElement('canvas');
    cv.width = g.cols;
    cv.height = g.rows;
    const ctx = cv.getContext('2d');
    const img = ctx.createImageData(g.cols, g.rows);
    for (let i = 0; i < colours.length; i++) {
      const v = colours[i];
      if (v === null || v === undefined) { img.data[i * 4 + 3] = 0; continue; }
      const c = bands ? Colormap.band(v, th.healthy, th.moderate) : Colormap.cmap(v);
      img.data[i * 4] = c[0];
      img.data[i * 4 + 1] = c[1];
      img.data[i * 4 + 2] = c[2];
      img.data[i * 4 + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);

    const scale = 24;
    const big = document.createElement('canvas');
    big.width = g.cols * scale;
    big.height = g.rows * scale;
    const bctx = big.getContext('2d');
    bctx.imageSmoothingEnabled = !bands;
    bctx.imageSmoothingQuality = 'high';
    bctx.drawImage(cv, 0, 0, big.width, big.height);

    // Punch out everything the drone did not photograph. Unsmoothed, so the
    // coverage boundary stays crisp and honest.
    const mask = document.createElement('canvas');
    mask.width = g.cols;
    mask.height = g.rows;
    const mctx = mask.getContext('2d');
    const mimg = mctx.createImageData(g.cols, g.rows);
    for (let i = 0; i < g.cells.length; i++) {
      const covered = g.cells[i] !== null && g.cells[i] !== undefined;
      mimg.data[i * 4] = mimg.data[i * 4 + 1] = mimg.data[i * 4 + 2] = 255;
      mimg.data[i * 4 + 3] = covered ? 255 : 0;
    }
    mctx.putImageData(mimg, 0, 0);
    bctx.globalCompositeOperation = 'destination-in';
    bctx.imageSmoothingEnabled = false;
    bctx.drawImage(mask, 0, 0, big.width, big.height);
    return big.toDataURL();
  }

  /* ── overlay switching ──────────────────────────────────────────────────── */

  async function drawOverlay() {
    if (overlay) { overlay.remove(); overlay = null; }
    if (!flight || mode === 'none') return;

    let url = null, bounds = gridBounds;
    if (mode === 'photos') {
      if (!hasFootprints) {
        note('These photos have no altitude recorded, so the ground each one ' +
             'covers is unknown. Showing the averaged grid instead.');
        mode = 'smooth';
        syncModeButtons();
      } else {
        if (!mosaicCache.url) mosaicCache.url = await buildMosaic();
        url = mosaicCache.url;
        bounds = mosaicBounds;
      }
    }
    if (mode === 'smooth' || mode === 'bands') url = gridCanvas(mode === 'bands');
    if (!url) return;

    overlay = L.imageOverlay(url, bounds, {
      opacity: opacity, interactive: false,
      className: mode === 'photos' ? 'mosaic-layer' : 'grid-layer'
    }).addTo(map);
  }

  /* ── photo pins and flight track ────────────────────────────────────────── */

  const pinLayer = L.layerGroup();
  const trackLayer = L.layerGroup();

  function buildPins() {
    const COLOURS = {
      healthy: '#2f8f3e', moderate: '#e0a020', stressed: '#c1442e'
    };
    const track = [];
    pins.forEach(function (p) {
      track.push([p.lat, p.lon]);
      const marker = L.circleMarker([p.lat, p.lon], {
        radius: 6, weight: 2, color: '#f5ead8', fillOpacity: 0.95,
        fillColor: COLOURS[p.classification] || '#82796a'
      });
      const label = Colormap.BAND_LABELS[p.classification] || 'no reading';
      marker.bindTooltip(
        '<strong>' + p.time + '</strong> · ' + label +
        (p.mean === null ? '' : '<br>BNDVI ' + HC.fmt(p.mean)) +
        (p.footprint ? '<br>covers ' + p.footprint.width_m + ' × ' +
          p.footprint.height_m + ' m from ' + p.alt + ' m up' : '') +
        (p.gsd_cm ? '<br>' + p.gsd_cm + ' cm per pixel' : '') +
        '<br><em>click to open</em>',
        { direction: 'top', offset: [0, -6] });
      marker.on('click', function () {
        window.location.href = '/capture/' + p.id;
      });
      // Show where this photo actually looked, on hover.
      let ghost = null;
      marker.on('mouseover', function () {
        if (!p.footprint) return;
        ghost = L.rectangle(footprintBounds(p), {
          color: '#f5ead8', weight: 1.5, dashArray: '3 3', fill: false,
          interactive: false
        }).addTo(map);
      });
      marker.on('mouseout', function () {
        if (ghost) { ghost.remove(); ghost = null; }
      });
      pinLayer.addLayer(marker);
    });

    if (track.length > 1) {
      L.polyline(track, {
        color: '#f5ead8', weight: 2, opacity: 0.55, dashArray: '5 6',
        interactive: false
      }).addTo(trackLayer);
    }
  }

  function footprintBounds(p) {
    const r = Math.sqrt(Math.pow(p.footprint.half_w_m, 2) +
                        Math.pow(p.footprint.half_h_m, 2));
    const dLat = r / M_PER_DEG_LAT;
    const dLon = r / mPerDegLon(p.lat);
    return [[p.lat - dLat, p.lon - dLon], [p.lat + dLat, p.lon + dLon]];
  }

  function syncPins() {
    if (showPins && pins.length) {
      trackLayer.addTo(map);
      pinLayer.addTo(map);
    } else {
      map.removeLayer(pinLayer);
      map.removeLayer(trackLayer);
    }
  }

  /* ── hover readout ──────────────────────────────────────────────────────── */

  const hint = document.getElementById('map-hint');
  const IDLE_HINT = pins.length
    ? 'Move over the field to check one spot. Click a photo pin to open it.'
    : (flight
      ? 'Move over the field to check one spot. Scroll to zoom, drag to pan.'
      : 'Scroll to zoom, drag to pan. The dashed line is as far as the offline ' +
        'imagery goes.');

  function note(text) { if (hint) hint.textContent = text; }

  function readCell(latlng) {
    const g = flight && flight.grid;
    if (!g) return null;
    const fx = (latlng.lng - gridBounds[0][1]) /
               (gridBounds[1][1] - gridBounds[0][1]);
    const fy = 1 - (latlng.lat - gridBounds[0][0]) /
               (gridBounds[1][0] - gridBounds[0][0]);
    if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return null;
    const col = Math.min(g.cols - 1, Math.floor(fx * g.cols));
    const row = Math.min(g.rows - 1, Math.floor(fy * g.rows));
    return { row: row, col: col, value: g.cells[row * g.cols + col] };
  }

  /** Closest photo, so the readout can name the actual capture under the cursor
   *  rather than only a grid cell. */
  function nearestPin(latlng) {
    let best = null, bestD = Infinity;
    pins.forEach(function (p) {
      const dLat = (p.lat - latlng.lat) * M_PER_DEG_LAT;
      const dLon = (p.lon - latlng.lng) * mPerDegLon(p.lat);
      const d = Math.sqrt(dLat * dLat + dLon * dLon);
      if (d < bestD) { bestD = d; best = p; }
    });
    return best && bestD < 40 ? { pin: best, metres: bestD } : null;
  }

  function describe(latlng) {
    const near = nearestPin(latlng);
    const cell = readCell(latlng);
    const parts = [];

    if (cell) {
      const where = 'Row ' + (cell.row + 1) + ', column ' + (cell.col + 1);
      if (cell.value === null || cell.value === undefined) {
        parts.push(where + ' — no photo covered this spot');
      } else {
        const th = (flight && flight.thresholds) || { healthy: 0.3, moderate: 0.1 };
        const name = Colormap.bandName(cell.value, th.healthy, th.moderate);
        parts.push(where + ' — ' + Colormap.BAND_LABELS[name].toLowerCase() +
          ' · BNDVI ' + HC.fmt(cell.value));
      }
    }
    if (near) {
      parts.push('nearest photo ' + near.pin.time + ' (' +
        Math.round(near.metres) + ' m away)');
    }
    return parts.length ? parts.join('  ·  ') : IDLE_HINT;
  }

  map.on('mousemove', function (e) {
    if (drawing) {
      // Rubber-band the shape out to the cursor so the size and outline are
      // visible before committing the next corner.
      if (draft.length) refreshDraft(e.latlng);
      return;                            // the picker owns the hint while it's up
    }
    note(describe(e.latlng));
  });
  map.on('mouseout', function () { if (!drawing) note(IDLE_HINT); });
  map.on('click', function (e) {
    if (drawing) { onDrawClick(e.latlng); return; }
    note(describe(e.latlng));
  });

  /* ── controls ───────────────────────────────────────────────────────────── */

  function syncModeButtons() {
    HC.$$('.map-mode').forEach(function (b2) {
      b2.classList.toggle('is-active', b2.dataset.mode === mode);
    });
  }

  HC.$$('.map-mode').forEach(function (btn) {
    btn.addEventListener('click', function () {
      mode = btn.dataset.mode;
      syncModeButtons();
      note(mode === 'photos'
        ? 'Each photo drawn where it was taken, at the ground it actually covers.'
        : (mode === 'smooth' ? 'Averaged into a grid — coarser, but comparable between flights.'
          : (mode === 'bands' ? 'Three colours at your thresholds.'
            : 'Satellite imagery only.')));
      drawOverlay();
    });
  });

  const fade = document.getElementById('map-opacity');
  if (fade) {
    fade.addEventListener('input', function () {
      opacity = parseFloat(fade.value);
      if (overlay) overlay.setOpacity(opacity);
    });
  }

  const pinToggle = document.getElementById('map-show-pins');
  if (pinToggle) {
    pinToggle.addEventListener('change', function () {
      showPins = pinToggle.checked;
      syncPins();
    });
    pinToggle.closest('.map-toggle').hidden = pins.length === 0;
  }

  /* ── go ─────────────────────────────────────────────────────────────────── */

  buildPins();
  syncPins();
  drawOverlay();

  if (flight && !hasFootprints && pins.length) {
    note('Photos have no altitude recorded, so only the averaged grid is ' +
         'available. Fly with the flight controller connected to get footprints.');
  }

  // Leaflet mis-measures a container laid out after init, and again for print.
  setTimeout(function () { map.invalidateSize(); }, 80);
  window.addEventListener('beforeprint', function () { map.invalidateSize(); });
}());

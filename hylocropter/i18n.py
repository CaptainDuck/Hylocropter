"""
Tagalog and English for the flight results page.

The farmers this is for read Tagalog first, so Tagalog is the default; the
toggle in the page header switches to English and a cookie remembers it. Only
the flight results page is translated so far -- the setup, debug and settings
pages are for the technical team and stay in English.

Strings are ``str.format`` templates. Where English needs a plural the key has
a ``_one`` twin; Tagalog does not inflect for number, so both read the same.
"""

LANGS = ("tl", "en")
DEFAULT = "tl"
COOKIE = "hc_lang"

MONTHS = {
    "tl": ["Ene", "Peb", "Mar", "Abr", "May", "Hun",
           "Hul", "Ago", "Set", "Okt", "Nob", "Dis"],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}

STRINGS = {
    # ── header ──────────────────────────────────────────────────────────
    "flown": {"tl": "Lumipad noong {when}", "en": "Flown {when}"},
    "minutes": {"tl": "{n} minuto", "en": "{n} minutes"},
    "under_minute": {"tl": "wala pang isang minuto", "en": "under a minute"},
    "photos": {"tl": "{n} litrato", "en": "{n} photos"},
    "save_pdf": {"tl": "I-save bilang PDF", "en": "Save as PDF"},
    "new_flight": {"tl": "Bagong lipad", "en": "New flight"},
    "all_flights": {"tl": "Lahat ng {n} lipad →", "en": "All {n} flights →"},
    "lang_label": {"tl": "Wika", "en": "Language"},

    # ── legend ──────────────────────────────────────────────────────────
    "legend_title": {"tl": "Ano ang ibig sabihin ng kulay",
                     "en": "What the colours mean"},
    "healthy": {"tl": "Okay", "en": "Doing well"},
    "moderate": {"tl": "Bantayan", "en": "Keep an eye"},
    "stressed": {"tl": "Silipin agad", "en": "Needs a look"},
    "none": {"tl": "Walang litrato", "en": "No photo"},
    "healthy_mean": {"tl": "Malakas ang halaman. Walang kailangang gawin.",
                     "en": "Plants look strong. Nothing to do."},
    "moderate_mean": {"tl": "Baka nauuhaw o may simula ng sakit. Silipin sa loob ng 1–2 araw.",
                      "en": "May be thirsty or starting to get sick. Check within 1–2 days."},
    "stressed_mean": {"tl": "Puntahan ngayon. Tingnan ang dilig, peste, o bulok sa tangkay.",
                      "en": "Go today. Check the watering, pests or stem rot."},
    "none_mean": {"tl": "Hindi nadaanan ng drone, kaya hindi alam ang lagay dito.",
                  "en": "The drone didn’t photograph this, so its condition is unknown."},
    "squares_n": {"tl": "{n} kahon", "en": "{n} squares"},
    "squares_n_one": {"tl": "{n} kahon", "en": "{n} square"},

    # ── map ─────────────────────────────────────────────────────────────
    "mode_squares": {"tl": "Kahon", "en": "Squares"},
    "mode_photos": {"tl": "Litrato", "en": "Photos"},
    "mode_off": {"tl": "Wala", "en": "Off"},
    "fade": {"tl": "Linaw", "en": "Fade"},
    "pins": {"tl": "Pin ng litrato at daan", "en": "Photo pins & track"},
    "map_hint": {"tl": "I-tap ang isang kahon para malaman ang lagay doon.",
                 "en": "Tap a square to have it explained."},
    "photos_note": {"tl": "Bawat litrato sa lugar kung saan ito kinuha. Gumagamit ito ng tuloy-tuloy na kulay: ang dilaw dito ay mga +0.35, na lampas pa rin sa linya ng “Okay”.",
                    "en": "Each photo where it was taken. These use the smooth colour scale: yellow here is about +0.35, still above the “Doing well” line."},
    "square_note": {"tl": "Bawat kahon ay mga {w} × {h} metro ng lupa. Ang kulay nito ay average ng lahat ng nasa litrato, kasama ang lupa sa pagitan ng mga halaman.",
                    "en": "Each square is about {w} × {h} m of ground. Its colour is the average of everything in the photos, including the soil between plants."},

    # ── the tapped square ───────────────────────────────────────────────
    "where": {"tl": "Hanay {r}, kolum {c}", "en": "Row {r}, column {c}"},
    "no_photo_name": {"tl": "Walang litrato dito", "en": "No photo here"},
    "no_photo_mean": {"tl": "Hindi nadaanan ng drone ang bahaging ito.",
                      "en": "The drone didn’t photograph this part."},
    "cell_detail": {"tl": "Mga {w} × {h} m · {n} litrato · BNDVI {v}",
                    "en": "About {w} × {h} m · {n} photos · BNDVI {v}"},
    "cell_detail_one": {"tl": "Mga {w} × {h} m · 1 litrato · BNDVI {v}",
                        "en": "About {w} × {h} m · 1 photo · BNDVI {v}"},
    "cell_patches": {"tl": "Sa loob ng kahong ito, {pct}% ng lupa ay {band}. Silipin kahit berde ang kahon.",
                     "en": "Inside this square, {pct}% of the ground reads {band}. Worth a look even though the square is green."},
    "pick_hint": {"tl": "I-tap ang isang kahon sa mapa.",
                  "en": "Tap a square on the map."},
    "start_here": {"tl": "Dito magsimula", "en": "Start here"},
    "worst_hint": {"tl": "Ito ang may pinakamababang sukat. I-tap ang ibang kahon para makita ang lagay doon.",
                   "en": "This is the lowest-reading square. Tap any other square to see it."},

    # ── verdict ─────────────────────────────────────────────────────────
    "verdict_kicker": {"tl": "Ang lagay ngayon", "en": "How it looks today"},
    "verdict_healthy": {"tl": "Okay ang mga halaman", "en": "The plants look okay"},
    "verdict_moderate": {"tl": "Halos okay, may {n} kahong babantayan",
                         "en": "Mostly okay — {n} squares to keep an eye on"},
    "verdict_moderate_one": {"tl": "Halos okay, may isang kahong babantayan",
                             "en": "Mostly okay — 1 square to keep an eye on"},
    "verdict_stressed": {"tl": "{n} kahon ang dapat silipin agad",
                         "en": "{n} squares need a look today"},
    "verdict_stressed_one": {"tl": "Isang kahon ang dapat silipin agad",
                             "en": "1 square needs a look today"},
    "verdict_body": {"tl": "Sa {covered} kahong nakunan ng drone: {g} okay, {a} bantayan, {r} silipin agad.",
                     "en": "Of the {covered} squares the drone photographed: {g} doing well, {a} keep an eye, {r} need a look."},
    "verdict_body_mean": {"tl": "Walang GPS ang mga litrato, kaya hindi sila mailagay sa mapa. Ang hatol ay batay sa average ng lahat ng litrato.",
                          "en": "These photos had no GPS, so they could not be placed on the map. The verdict is from the average of all of them."},
    "ground_share": {"tl": "Sa lahat ng lupang nakunan, {g}% ang berde, {a}% dilaw at {r}% pula.",
                     "en": "Of all the ground photographed, {g}% reads green, {a}% amber and {r}% red."},
    "photo_share": {"tl": "Sa lupang nasa litratong ito, {g}% ang berde, {a}% dilaw at {r}% pula.",
                    "en": "Of the ground in this photo, {g}% reads green, {a}% amber and {r}% red."},
    "no_reading": {"tl": "Walang sukat ang litratong ito — masyadong madilim.",
                   "en": "This photo has no reading — it was too dark to measure."},
    "no_gps": {"tl": "Walang GPS ang litratong ito, kaya hindi ito mailagay sa mapa.",
               "en": "This photo had no GPS fix, so it could not be placed on the map."},
    "action_healthy": {"tl": "Walang kailangang gawin ngayon. Lumipad ulit pagkalipas ng tatlong araw.",
                       "en": "Nothing to do today. Fly again in three days."},
    "action_moderate": {"tl": "Silipin ang mga dilaw na kahon sa loob ng 1–2 araw. I-tap ang kahon sa mapa para makita kung saan.",
                        "en": "Check the amber squares within 1–2 days. Tap one on the map to see where."},
    "action_stressed": {"tl": "Puntahan ngayon ang mga pulang kahon. Tingnan ang dilig, peste, o bulok sa tangkay.",
                        "en": "Go to the red squares today. Check the watering, pests or stem rot."},
    "verdict_none": {"tl": "Walang litratong may sukat sa lipad na ito",
                     "en": "This flight has no photos with a reading"},
    "verdict_pending": {"tl": "Pinoproseso pa ang mga litrato",
                        "en": "The photos are still being processed"},

    # ── one photo, up close ─────────────────────────────────────────────
    "photo_title": {"tl": "Isang litrato, malapitan", "en": "One photo, up close"},
    "color_short": {"tl": "May kulay", "en": "Coloured"},
    "raw_short": {"tl": "Orihinal", "en": "Original"},
    "photo_note": {"tl": "Pansinin: berde rin ang lupa sa pagitan ng mga halaman. Ang pula o dilaw na bahagi lang ang dapat puntahan.",
                   "en": "Note: the soil between plants is green too. Only red or amber patches need a visit."},
    "raw_note": {"tl": "Kuha ng camera (madilim ito talaga).",
                 "en": "What the camera saw (it really is this dark)."},
    "photo_meta": {"tl": "Litrato #{n} · {time} · {alt} m ang taas · average {v} · {g}% berde",
                   "en": "Photo #{n} · {time} · {alt} m up · average {v} · {g}% green"},
    "open_photo": {"tl": "Buksan ang litrato →", "en": "Open the photo →"},
    "photos_from_flight": {"tl": "Mga litrato mula sa lipad na ito",
                           "en": "Photos from this flight"},

    # ── technical ───────────────────────────────────────────────────────
    "tech_title": {"tl": "Para sa technical team", "en": "For the technical team"},
    "tech_count": {"tl": "{n} dapat ayusin", "en": "{n} to fix"},
    "tech_none": {"tl": "Walang dapat ayusin", "en": "Nothing to fix"},
    "tech_sub": {"tl": "Ang hatol sa itaas ay batay sa kasalukuyang settings. Ito ang mga kailangang ayusin bago lubos na pagkatiwalaan ang mga numero. (Nasa English ang detalye.)",
                 "en": "The verdict above uses the settings on the day. These need fixing before the numbers can be fully trusted."},
}


def pick(lang):
    return lang if lang in LANGS else DEFAULT


def t(lang, key, n=None, **kw):
    """The string for `key` in `lang`, formatted. Pass `n` for a count: it picks
    the ``_one`` form when n == 1 and is available to the template as {n}."""
    if n is not None:
        kw["n"] = n
        if n == 1 and key + "_one" in STRINGS:
            key += "_one"
    entry = STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry["en"]
    return text.format(**kw) if kw else text


def when(ts, lang):
    """'2026-10-13T15:18:01' -> '13 Okt 2026, 3:18 PM'."""
    if not ts or len(ts) < 16:
        return ts or ""
    year, month, day = ts[0:4], int(ts[5:7]), int(ts[8:10])
    hour, minute = int(ts[11:13]), ts[14:16]
    h12 = hour % 12 or 12
    return (f"{day} {MONTHS[pick(lang)][month - 1]} {year}, "
            f"{h12}:{minute} {'AM' if hour < 12 else 'PM'}")


def bundle(lang, prefix_keys):
    """Strings for the page's JavaScript, already in the chosen language."""
    return {k: STRINGS[k].get(lang) or STRINGS[k]["en"] for k in prefix_keys}

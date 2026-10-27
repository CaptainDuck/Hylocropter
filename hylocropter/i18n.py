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
    "legend_title": {"tl": "Kulay ng heatmap",
                    "en": "Heatmap colours"},
    "legend_green": {"tl": "Berde", "en": "Green"},
    "legend_green_mean": {"tl": "Malakas ang halaman.", "en": "Strong plants."},
    "legend_yellow": {"tl": "Dilaw", "en": "Yellow"},
    "legend_yellow_mean": {"tl": "Karaniwan — kadalasang lupa o tuyong damo, hindi problema.",
                           "en": "Average — usually soil or dry grass, not a problem."},
    "legend_red": {"tl": "Kahel o pula sa halaman", "en": "Orange or red on a plant"},
    "legend_red_mean": {"tl": "Dapat tingnan: baka nauuhaw o may sakit.",
                        "en": "Worth a look: may be thirsty or sick."},
    "legend_flag": {"tl": "Litratong dapat tingnan", "en": "Photo to check"},
    "tour": {"tl": "Titingnan: litrato {i} sa {n}", "en": "To check: photo {i} of {n}"},
    "tour_next": {"tl": "Susunod →", "en": "Next →"},
    "tour_prev": {"tl": "← Nauna", "en": "← Previous"},
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

    # ── map ─────────────────────────────────────────────────────────────
    "fade": {"tl": "Linaw", "en": "Fade"},
    "pins": {"tl": "Pin ng litrato at daan", "en": "Photo pins & track"},
    "map_hint": {"tl": "I-tap ang isang bahagi ng heatmap para makita ang litrato at lagay nito sa ibaba.",
                "en": "Tap a patch of the heatmap to see its photo and status below."},

    "nearest": {"tl": "pinakamalapit na litrato {time} ({m} m ang layo)",
                "en": "nearest photo {time} ({m} m away)"},

    # ── the tapped square ───────────────────────────────────────────────
    "where": {"tl": "Litrato #{n} · {time}",
             "en": "Photo #{n} · {time}"},
    "cell_detail": {"tl": "Sakop na lupa mga {w} × {h} m · {alt} m ang taas",
                   "en": "Covers about {w} × {h} m from {alt} m up"},
    "worst_hint": {"tl": "Ito ang unang dapat tingnan. Gamitin ang Susunod para sa iba, o i-tap ang ibang bahagi ng mapa.",
                  "en": "This is the first one to check. Use Next for the others, or tap any other patch on the map."},

    "start_here": {"tl": "Dito magsimula", "en": "Start here"},
    "verdict_no_gps": {"tl": "Walang GPS ang mga litrato, kaya hindi sila mailagay sa mapa.",
                       "en": "These photos had no GPS, so they could not be placed on the map."},

    # ── verdict ─────────────────────────────────────────────────────────
    "crown_short": {"tl": "{w}% kahel o pula sa halaman",
                   "en": "{w}% orange or red on the plants"},
    "verdict_widespread_body": {"tl": "{n} sa {m} litrato ang may kahel o pula sa mismong halaman. Nakaayos sila mula sa may pinakamarami — unahin ang mga nasa simula.",
                                "en": "{n} of the {m} photos have orange or red on the plants themselves. They are listed with the most first — start at the top."},
    "verdict_widespread": {"tl": "Karamihan ng block ay may kahel o pula sa halaman",
                          "en": "Most of the block has orange or red on the plants"},
    "lowest_hint": {"tl": "Ito ang litratong may pinakamababang sukat ng halaman, pero nasa normal pa para sa lipad na ito.",
                   "en": "This photo has the lowest plant reading, but it is still within normal for this flight."},
    "lowest_kicker": {"tl": "Pinakamababa, pero normal",
                     "en": "Lowest, but normal"},
    "photo_weaker": {"tl": "Mas mahina rin ang halaman dito kaysa sa karaniwang litrato ng lipad na ito.",
                    "en": "The plants here also read weaker than a typical photo from this flight."},
    "photo_crown": {"tl": "Sa halaman sa litratong ito: {w}% kahel o pula.",
                   "en": "On the plants in this photo: {w}% orange or red."},
    "flagged_name": {"tl": "Dapat tingnan",
                    "en": "Worth a look"},
    "verdict_flagged": {"tl": "{n} litrato ang dapat tingnan",
                       "en": "{n} photos to check"},
    "verdict_flagged_one": {"tl": "Isang litrato ang dapat tingnan",
                           "en": "1 photo to check"},
    "verdict_low": {"tl": "Mahina ang buong block",
                   "en": "The whole block reads weak"},
    "verdict_how": {"tl": "Halaman lang ang tiningnan sa bawat isa sa {n} litrato — hindi kasama ang lupa sa pagitan.",
                   "en": "Only the plants were looked at in each of the {n} photos — not the soil between them."},
    "verdict_crowns": {"tl": "{n} litrato ang may kahel o pula sa mismong halaman.",
                      "en": "{n} photos have orange or red on the plants themselves."},
    "verdict_crowns_one": {"tl": "Isang litrato ang may kahel o pula sa mismong halaman.",
                          "en": "1 photo has orange or red on the plants themselves."},
    "verdict_weaker": {"tl": "{n} litrato ang mas mahina ang halaman kaysa sa iba sa lipad na ito.",
                      "en": "{n} photos have plants reading clearly weaker than the rest of this flight."},
    "verdict_weaker_one": {"tl": "Isang litrato ang mas mahina ang halaman kaysa sa iba sa lipad na ito.",
                          "en": "1 photo has plants reading clearly weaker than the rest of this flight."},
    "verdict_clear": {"tl": "Walang kahel o pula sa mga halaman, at walang litratong namumukod na mahina.",
                     "en": "No orange or red on the plants, and no photo stands out as weaker."},
    "verdict_low_body": {"tl": "Kahit ang halaman sa karaniwang litrato ay mababa sa {th}. Sabay-sabay silang mahina, kaya walang namumukod.",
                        "en": "Even the plants in a typical photo read below {th}. They are all down together, so none stands out."},
    "verdict_few": {"tl": "Kaunti pa ang litrato ({n}) para paghambingin; ang kahel o pula lang sa halaman ang tiningnan.",
                   "en": "Too few photos ({n}) to compare with each other; only orange or red on the plants was checked."},
    "verdict_mean": {"tl": "Kinukuwenta pa ang sukat ng halaman para sa lipad na ito. Pansamantala, batay ito sa average ng bawat litrato, kasama ang lupa.",
                    "en": "Plant readings for this flight are still being worked out. For now this is from each photo's average, soil included."},
    "show_flagged": {"tl": "Isa-isahin sa mapa",
                    "en": "Go through them on the map"},
    "verdict_kicker": {"tl": "Ang lagay ngayon", "en": "How it looks today"},
    "verdict_healthy": {"tl": "Okay ang mga halaman", "en": "The plants look okay"},
    "no_reading": {"tl": "Walang sukat ang litratong ito — masyadong madilim.",
                   "en": "This photo has no reading — it was too dark to measure."},
    "no_gps": {"tl": "Walang GPS ang litratong ito, kaya hindi ito mailagay sa mapa.",
               "en": "This photo had no GPS fix, so it could not be placed on the map."},
    "action_healthy": {"tl": "Walang kailangang gawin ngayon. Lumipad ulit pagkalipas ng tatlong araw.",
                       "en": "Nothing to do today. Fly again in three days."},
    "action_moderate": {"tl": "Puntahan ang mga litratong ito sa loob ng 1–2 araw at tingnan ang halaman: dilig, peste, o bulok sa tangkay.",
                       "en": "Visit these photos within 1–2 days and look at the plants: watering, pests or stem rot."},
    "action_stressed": {"tl": "Puntahan ngayon ang mga pulang bahagi. Tingnan ang dilig, peste, o bulok sa tangkay.",
                       "en": "Go to the red patches today. Check the watering, pests or stem rot."},
    "verdict_none": {"tl": "Walang litratong may sukat sa lipad na ito",
                     "en": "This flight has no photos with a reading"},
    "verdict_pending": {"tl": "Pinoproseso pa ang mga litrato",
                        "en": "The photos are still being processed"},

    # ── one photo, up close ─────────────────────────────────────────────
    "photo_title": {"tl": "Isang litrato, malapitan", "en": "One photo, up close"},
    "color_short": {"tl": "May kulay", "en": "Coloured"},
    "raw_short": {"tl": "Orihinal", "en": "Original"},
    "photo_note": {"tl": "Ito ang heatmap ng litrato: berde ang pinakamalakas, dilaw ang karaniwan, at kahel hanggang pula ang mahina. Ang kahel o pulang bahagi ang dapat puntahan.",
                   "en": "This is the photo's heatmap: green is strongest, yellow is middling, and orange to red is weak. Orange or red patches are the ones to visit."},
    "raw_note": {"tl": "Kuha ng camera (madilim ito talaga).",
                 "en": "What the camera saw (it really is this dark)."},
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

"""Processing steps from the client work instruction (LCCP-Biscochoc).

Fermentation: 48h -> turn -> 48h -> turn -> 24h -> turn -> 24h
Drying:       Solar day 1, 2, 3   OR   Fermentry day 1 + Solar day 1, 2
Then:         Packing into jute bags -> depot check (cut test, moisture)
"""

FERMENT = [
    {"code": "FERMENT_1", "label": "First fermentation", "hours": 48, "kind": "ferment",
     "tip": "Put the wet beans in the box or heap and cover them. Leave for 48 hours."},
    {"code": "TURN_1", "label": "First turning", "hours": 0, "kind": "turn",
     "tip": "Turn the beans and take a photo."},
    {"code": "FERMENT_2", "label": "Second fermentation", "hours": 48, "kind": "ferment",
     "tip": "Cover again and leave for 48 hours."},
    {"code": "TURN_2", "label": "Second turning", "hours": 0, "kind": "turn",
     "tip": "Turn the beans and take a photo."},
    {"code": "FERMENT_3", "label": "Third fermentation", "hours": 24, "kind": "ferment",
     "tip": "Cover again and leave for 24 hours."},
    {"code": "TURN_3", "label": "Third turning", "hours": 0, "kind": "turn",
     "tip": "Turn the beans and take a photo."},
    {"code": "FERMENT_4", "label": "Fourth fermentation", "hours": 24, "kind": "ferment",
     "tip": "Last fermentation – 24 hours, then move to drying."},
]

SOLAR_DRYING = [
    {"code": "SOLAR_1", "label": "Solar drier – day 1", "hours": 24, "kind": "dry",
     "tip": "Spread the beans in the solar drier."},
    {"code": "SOLAR_2", "label": "Solar drier – day 2", "hours": 24, "kind": "dry",
     "tip": "Keep drying. Rake the beans during the day."},
    {"code": "SOLAR_3", "label": "Solar drier – day 3", "hours": 24, "kind": "dry",
     "tip": "Final drying day. Beans should be dry and crack when pressed."},
]

FERMENTRY_DRYING = [
    {"code": "FERMENTRY_1", "label": "Fermentry – day 1", "hours": 24, "kind": "dry",
     "tip": "First day of drying in the fermentry."},
    {"code": "SOLAR_1", "label": "Solar drier – day 1", "hours": 24, "kind": "dry",
     "tip": "Move the beans to the solar drier."},
    {"code": "SOLAR_2", "label": "Solar drier – day 2", "hours": 24, "kind": "dry",
     "tip": "Final drying day."},
]

PACKING = [
    {"code": "PACKING", "label": "Packing into jute bags", "hours": 0, "kind": "pack",
     "tip": "Weigh the dry beans, pack them in jute bags and take a photo."},
]


def steps_for(drying_method):
    drying = FERMENTRY_DRYING if drying_method == "FERMENTRY" else SOLAR_DRYING
    out = []
    for i, d in enumerate(FERMENT + drying + PACKING):
        out.append({**d, "sequence": i})
    return out


BY_CODE = {}
for _d in FERMENT + SOLAR_DRYING + FERMENTRY_DRYING + PACKING:
    BY_CODE.setdefault(_d["code"], _d)

CHOICES = [(code, d["label"]) for code, d in BY_CODE.items()]

"""
Bandipora Farmer Enrollment Portal
==================================
Flask app for Render deployment. Reads daily AGRISTACK snapshots from
`data/YYYY-MM-DD.csv` (committed via GitHub). The baseline through
30 Sept 2026 is frozen in-code; each uploaded snapshot represents the
full state of registered farmers as of that date, so day-over-day
additions = (snapshot_D counts) minus (snapshot_(D-1) counts) per village.

See README.md for deployment steps.
"""
import csv
import glob
import json
import os
import re
import secrets as _secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, Response, abort, render_template, request, jsonify, session, redirect, url_for, flash

# =========================================================================
# BAKED-IN DATA — baseline, targets, camp directors, first-bucket, tehsils
# These are frozen in code; edit here (and commit) to change them.
# =========================================================================

BASELINE_LABEL = "through 30 Sept 2026"
BASELINE_DATE = "2026-09-30"   # the baseline is parked on this date
BASELINE_ENABLED = True

# Pre-Oct per-village totals: "Tehsil||Village" -> [issued, approved]
BASELINE_THROUGH_SEP30 = {
    "Ajas||S.K. Payeen": [230, 159],
    "Aloosa||Ashtangoo": [286, 3],
    "Bandipora||Brar": [254, 187],
    "Bandipora||Chak Arsala Khan": [216, 185],
    "Bandipora||Chak Reshipora": [59, 6],
    "Bandipora||Chandaji": [67, 51],
    "Bandipora||Chuntimulla": [282, 187],
    "Bandipora||Dachigam": [98, 82],
    "Bandipora||Gundi Qasier": [138, 107],
    "Bandipora||Kharpora": [183, 154],
    "Bandipora||Labkachal": [58, 37],
    "Bandipora||Papachan": [153, 116],
    "Bandipora||Shamthan": [88, 43],
    "Bandipora||Takiya Ahmad Shah": [148, 114],
    "Bandipora||Weven": [146, 11],
    "Gurez||Khandiyal": [237, 192],
    "Gurez||Koragbal": [59, 55],
    "Gurez||Mastan Khopri": [193, 158],
    "Hajin||Gulabwari": [1, 1],
    "Hajin||Gund-i-Balkh": [148, 118],
    "Hajin||Gund-i-Ramzan": [7, 6],
    "Hajin||Madwan": [194, 150],
    "Hajin||Tangpora": [105, 92],
    "Hajin||Zoonipora": [125, 93],
    "Sonawari||Chewa": [394, 270],
    "Sonawari||Gaad Khud": [264, 186],
    "Sonawari||Gund i Nowgam": [396, 237],
    "Sonawari||Malik Pora": [152, 115],
    "Sonawari||Najin": [198, 155],
    "Sonawari||Shadi Pora": [385, 254],
    "Sonawari||Shilvat": [231, 182],
    "Sonawari||Zal Pora": [255, 197],
    "Tulail||Abdullian": [70, 19],
    "Tulail||Gund Gul Sheikh": [59, 54],
    "Tulail||Hussangam": [160, 142],
    "Tulail||Kilshay Payeen": [95, 32],
    "Tulail||Malangam": [77, 68],
    "Tulail||Manz Gund": [92, 48],
    "Tulail||Puranatulail": [102, 66],
    "Tulail||Wazirthal": [58, 50],
}

DEFAULT_TARGETS = [
    ("Ajas", "Ajas", 2912),
    ("Ajas", "S.K. Payeen", 718),
    ("Aloosa", "Ashtangoo", 1472),
    ("Aloosa", "Mangnipora", 1918),
    ("Bandipora", "Ahemshreif", 444),
    ("Bandipora", "Aragam", 1358),
    ("Bandipora", "Athwatoo", 645),
    ("Bandipora", "Ayatmulla", 821),
    ("Bandipora", "Bhutto", 362),
    ("Bandipora", "Brar", 640),
    ("Bandipora", "Chak Arsala Khan", 482),
    ("Bandipora", "Chak Reshipora", 195),
    ("Bandipora", "Chandaji", 186),
    ("Bandipora", "Chuntimulla", 1051),
    ("Bandipora", "Dachigam", 295),
    ("Bandipora", "Gamroo", 660),
    ("Bandipora", "Garoora", 1388),
    ("Bandipora", "Gund Dachina", 699),
    ("Bandipora", "Gundi Qasier", 465),
    ("Bandipora", "Gundipora Rampora", 1082),
    ("Bandipora", "Kharpora", 531),
    ("Bandipora", "Khayar", 899),
    ("Bandipora", "Kudara", 986),
    ("Bandipora", "Labkachal", 136),
    ("Bandipora", "Lawaypora", 1118),
    ("Bandipora", "Lowdara", 540),
    ("Bandipora", "Nadihal", 1230),
    ("Bandipora", "Nass", 514),
    ("Bandipora", "Onagam", 1857),
    ("Bandipora", "Panjigam", 992),
    ("Bandipora", "Papachan", 515),
    ("Bandipora", "Shamthan", 196),
    ("Bandipora", "Soner Wani", 1058),
    ("Bandipora", "Takiya Ahmad Shah", 355),
    ("Bandipora", "Watapora", 1209),
    ("Bandipora", "Weven", 377),
    ("Gurez", "Badwan", 1213),
    ("Gurez", "Dawar", 670),
    ("Gurez", "Gulshanpora", 847),
    ("Gurez", "Kanzalwan Nail", 571),
    ("Gurez", "Khandiyal", 632),
    ("Gurez", "Koragbal", 111),
    ("Gurez", "Markoot", 586),
    ("Gurez", "Mastan Khopri", 421),
    ("Gurez", "Shah Pora - Achoora (Churwan)", 1114),
    ("Hajin", "Gulabwari", 28),
    ("Hajin", "Gulshanpora", 821),
    ("Hajin", "Gund i Prang", 785),
    ("Hajin", "Gund-i-Balkh", 322),
    ("Hajin", "Gund-i-Ramzan", 11),
    ("Hajin", "Gundi Boon", 754),
    ("Hajin", "Gundi Jahangir", 1017),
    ("Hajin", "Gundi Saderkote", 2139),
    ("Hajin", "Kani Pora", 427),
    ("Hajin", "Kosumbagh", 0),
    ("Hajin", "Madwan", 777),
    ("Hajin", "Poshwari", 1176),
    ("Hajin", "Rakh i Hajin", 1107),
    ("Hajin", "Sari Hari Karan Gund", 529),
    ("Hajin", "Tangpora", 220),
    ("Hajin", "Vijipara", 1269),
    ("Hajin", "Zoonipora", 465),
    ("Sumbal", "Asham", 1296),
    ("Sumbal", "Chewa", 1116),
    ("Sumbal", "Gaad Khud", 674),
    ("Sumbal", "Ganastan", 1560),
    ("Sumbal", "Gund i Khalil", 1308),
    ("Sumbal", "Gund i Nowgam", 587),
    ("Sumbal", "Hilalabad", 1164),
    ("Sumbal", "Malik Pora", 347),
    ("Sumbal", "Najin", 404),
    ("Sumbal", "Nowgam", 941),
    ("Sumbal", "Odina", 1006),
    ("Sumbal", "Rakh Shilvat", 2692),
    ("Sumbal", "Rakh Sultan Pora", 1973),
    ("Sumbal", "Sarai Dangarpora", 2149),
    ("Sumbal", "Shadi Pora", 781),
    ("Sumbal", "Shilvat", 767),
    ("Sumbal", "Sumbal Inderkote", 849),
    ("Sumbal", "Trigam", 1399),
    ("Sumbal", "Wahid pora", 884),
    ("Sumbal", "Zal Pora", 602),
    ("Tulail", "Abdullian", 213),
    ("Tulail", "Baduaab", 502),
    ("Tulail", "Barnaie", 234),
    ("Tulail", "Budugam", 648),
    ("Tulail", "Buglinder", 397),
    ("Tulail", "Dengithal", 187),
    ("Tulail", "Gujran", 450),
    ("Tulail", "Gund Gul Sheikh", 157),
    ("Tulail", "Hussangam", 265),
    ("Tulail", "Jurnial", 446),
    ("Tulail", "Kilshay Payeen", 205),
    ("Tulail", "Malangam", 208),
    ("Tulail", "Manz Gund", 181),
    ("Tulail", "Neeru", 443),
    ("Tulail", "Puranatulail", 208),
    ("Tulail", "Saradaab", 459),
    ("Tulail", "Wazirthal", 116),
    ("Tulail", "Zedgey", 132),
]

TEHSIL_REFERENCE = {
    "BANDIPORA": {"totalVillages": 43, "totalSurveyNos": 72131},
    "SUMBAL":    {"totalVillages": 21, "totalSurveyNos": 33624},
    "HAJIN":     {"totalVillages": 24, "totalSurveyNos": 39356},
    "ALOOSA":    {"totalVillages": 6,  "totalSurveyNos": 22421},
    "GUREZ":     {"totalVillages": 9,  "totalSurveyNos": 10808},
    "TULAIL":    {"totalVillages": 18, "totalSurveyNos": 8036},
    "AJAS":      {"totalVillages": 3,  "totalSurveyNos": 6853},
}

TEHSIL_ALIAS = {"SONAWARI": "SUMBAL"}

# Alternative spellings that map to the same village. Keys are normalized
# alternative spellings (uppercase, no punctuation); values are the canonical
# normalized village name used in CAMP_DIRECTORS_59.  Resolves cases like
# AGRISTACK CSV saying "Wahid pora" when the camp-director list says "WAHIDPORA".
VILLAGE_ALIAS = {
    # Sumbal (CSV ← CD canonical)
    "WAHID PORA":           "WAHIDPORA",
    "GUND I KHALIL":        "GUNDIKHALIL",
    "RAKH SHILVAT":         "RAKHI SHILVAT",
    "RAKH SULTAN PORA":     "RAKHI SULTANPORA",
    "SARAI DANGARPORA":     "SARIE DANGERPORA",
    # Hajin
    "RAKH I HAJIN":         "RAKHI HAJIN",
    "GUNDI JAHANGIR":       "GUND JAHENGEER",
    "GUNDI SADERKOTE":      "GUND SADERKOOT",
    "VIJIPARA":             "VIJPARA",
    "SARI HARI KARAN GUND": "SRI HARI KARANGUND",
    "KANI PORA":            "KANIPORA",
    # Bandipora
    "GUNDIPORA RAMPORA":    "GUNDPORA RAMPORA",
    "NASS":                 "NASSU",
    "SONER WANI":           "SONERWANI",
    # Gurez
    "BADWAN":               "BADWAN WANPORA",
    "SHAH PORA ACHOORA CHURWAN": "SHAHPORA ACHOORA CHURWAN",
    # Tulail
    "SARADAAB":             "SARDAAB",
    "BUDUGAM":              "BADUGAM",
    "JURNIAL":              "JURNIYAL",
    "DENGITHAL":            "DANGITHAL",
    "ZEDGEY":               "ZEDGAY",
    "BARNAIE":              "BARNAYEE",
}

FIRST_BUCKET_VILLAGE_KEYS = {
    "ABDULLIAN", "ASHTANGOO", "BRAR", "CHAK ARSALA KHAN", "CHAK RESHIPORA",
    "CHANDAJI", "CHEWA", "CHUNTIMULLA", "DACHIGAM", "GAAD KHUD", "GULABWARI",
    "GUND GUL SHEIKH", "GUND I BALKH", "GUND I NOWGAM", "GUND I RAMZAN",
    "GUNDI QASIER", "HUSSANGAM", "KHANDIYAL", "KHARPORA", "KILSHAY PAYEEN",
    "KORAGBAL", "LABKACHAL", "MADWAN", "MALANGAM", "MALIK PORA", "MANZ GUND",
    "MASTAN KHOPRI", "NAJIN", "PAPACHAN", "PURANATULAIL", "S K PAYEEN",
    "SHADI PORA", "SHAMTHAN", "SHILVAT", "TAKIYA AHMAD SHAH", "TANGPORA",
    "WAZIRTHAL", "WEVEN", "ZAL PORA", "ZOONIPORA"
}

# Camp directors for the 59 non-first-bucket villages
CAMP_DIRECTORS_59 = {"SUMBAL||SUMBAL INDERKOTE": {"n": "Dr Syed Mubarak Hussain Shah", "p": "9858332300", "t": "SUMBAL", "v": "SUMBAL INDERKOTE"}, "SUMBAL||WAHIDPORA": {"n": "Dr Syed Mubarak Hussain Shah", "p": "9858332300", "t": "SUMBAL", "v": "WAHIDPORA"}, "SUMBAL||GANASTAN": {"n": "Jameel Farooq", "p": "7006912553", "t": "SUMBAL", "v": "Ganastan"}, "SUMBAL||RAKHI SULTANPORA": {"n": "Jameel Farooq", "p": "7006912553", "t": "SUMBAL", "v": "Rakhi Sultanpora"}, "SUMBAL||TRIGAM": {"n": "Dr Suhail Ahmad", "p": "9797756269", "t": "SUMBAL", "v": "Trigam"}, "SUMBAL||HILALABAD": {"n": "Dr Suhail Ahmad", "p": "9797756269", "t": "SUMBAL", "v": "HILALABAD"}, "SUMBAL||RAKHI SHILVAT": {"n": "Jameel Farooq", "p": "7006912553", "t": "SUMBAL", "v": "RAKHI SHILVAT"}, "SUMBAL||ASHAM": {"n": "Jameel Farooq", "p": "7006912553", "t": "SUMBAL", "v": "Asham"}, "SUMBAL||SARIE DANGERPORA": {"n": "Dr Aasima Zehra", "p": "9149687674", "t": "SUMBAL", "v": "SARIE DANGERPORA"}, "SUMBAL||ODINA": {"n": "Dr Shaista Naz", "p": "7780935280", "t": "SUMBAL", "v": "ODINA"}, "SUMBAL||GUNDIKHALIL": {"n": "Dr Mudasir Ahmad Shah", "p": "9906580671", "t": "SUMBAL", "v": "GUNDIKHALIL"}, "SUMBAL||NOWGAM": {"n": "Dr Mudasir Ahmad Shah", "p": "9906580671", "t": "SUMBAL", "v": "NOWGAM"}, "HAJIN||RAKHI HAJIN": {"n": "Ghulam Rasool Hajam", "p": "9797826874", "t": "HAJIN", "v": "RAKHI HAJIN"}, "HAJIN||GUND JAHENGEER": {"n": "Ghulam Rasool Hajam", "p": "9797826874", "t": "HAJIN", "v": "Gund Jahengeer"}, "HAJIN||GUND SADERKOOT": {"n": "Dr Arif Mohd Khan", "p": "7889818735", "t": "HAJIN", "v": "Gund saderkoot"}, "HAJIN||VIJPARA": {"n": "Dr Arif Mohd Khan", "p": "7889818735", "t": "HAJIN", "v": "Vijpara"}, "HAJIN||GUND I PRANG": {"n": "Dr Ateeqa", "p": "9596657419", "t": "HAJIN", "v": "GUND- I- PRANG"}, "HAJIN||SRI HARI KARANGUND": {"n": "Dr Ateeqa", "p": "9596657419", "t": "HAJIN", "v": "SRI HARI KARANGUND"}, "HAJIN||GUNDI BOON": {"n": "Dr Arif Mohd Khan", "p": "7889818735", "t": "HAJIN", "v": "GUNDI BOON"}, "HAJIN||KANIPORA": {"n": "Dr Syed Imran", "p": "7006409066", "t": "HAJIN", "v": "KANIPORA"}, "HAJIN||POSHWARI": {"n": "Dr Abdul Rashid Teli", "p": "8493011248", "t": "HAJIN", "v": "POSHWARI,"}, "HAJIN||GULSHANPORA": {"n": "Dr Abdul Rashid Teli", "p": "8493011248", "t": "HAJIN", "v": "GULSHANPORA"}, "BANDIPORA||WATAPORA": {"n": "Aarif Hussain Rather", "p": "7006515845", "t": "BANDIPORA", "v": "WATAPORA"}, "BANDIPORA||ARAGAM": {"n": "Dr Tariq Ahmad", "p": "9596184321", "t": "BANDIPORA", "v": "Aragam"}, "BANDIPORA||GUNDPORA RAMPORA": {"n": "Dr Tariq Ahmad", "p": "9596184321", "t": "BANDIPORA", "v": "Gundpora Rampora"}, "BANDIPORA||NADIHAL": {"n": "Javid Ahmad Dar (SMS SDL)", "p": "9697876747", "t": "BANDIPORA", "v": "Nadihal"}, "BANDIPORA||ONAGAM": {"n": "Javid Ahmad Dar (SMS SDL)", "p": "9697876747", "t": "BANDIPORA", "v": "Onagam"}, "BANDIPORA||SONERWANI": {"n": "Mohd Abbas Mir (HDO)", "p": "7006051991", "t": "BANDIPORA", "v": "Sonerwani"}, "BANDIPORA||KHAYAR": {"n": "Mohd Abbas Mir (HDO)", "p": "7006051991", "t": "BANDIPORA", "v": "KHAYAR"}, "BANDIPORA||LAWAYPORA": {"n": "Mohd Abbas Mir (HDO)", "p": "7006051991", "t": "BANDIPORA", "v": "LAWAYPORA"}, "BANDIPORA||KUDARA": {"n": "Dr Tariq Ahmad", "p": "9596184321", "t": "BANDIPORA", "v": "KUDARA."}, "BANDIPORA||GAMROO": {"n": "Gulzar Ahmad Khan", "p": "7889808614", "t": "BANDIPORA", "v": "GAMROO"}, "BANDIPORA||LOWDARA": {"n": "Aasif Jameel", "p": "7006414605", "t": "BANDIPORA", "v": "LOWDARA"}, "BANDIPORA||GAROORA": {"n": "Javid Ahmad Dar (SMS SDL)", "p": "9697876747", "t": "BANDIPORA", "v": "GAROORA"}, "BANDIPORA||PANJIGAM": {"n": "Dr Aijaz Ahmad", "p": "9149986467", "t": "BANDIPORA", "v": "PANJIGAM"}, "BANDIPORA||AHEMSHREIF": {"n": "Dr Reyaz Ahmad", "p": "7889374549", "t": "BANDIPORA", "v": "AHEMSHREIF"}, "BANDIPORA||AYATMULLA": {"n": "Dr Reyaz Ahmad", "p": "7889374549", "t": "BANDIPORA", "v": "AYATMULLA"}, "BANDIPORA||BHUTTO": {"n": "Dr Ishtiyaq Mughal", "p": "7780861724", "t": "BANDIPORA", "v": "BHUTTO"}, "BANDIPORA||NASSU": {"n": "Manzoor Ahmad Malla", "p": "7006114318", "t": "BANDIPORA", "v": "NASSU"}, "BANDIPORA||ATHWATOO": {"n": "Dr Sheeraz Ahmad", "p": "8491007147", "t": "BANDIPORA", "v": "ATHWATOO"}, "BANDIPORA||GUND DACHINA": {"n": "Dr Adil Majeed", "p": "7788932598", "t": "BANDIPORA", "v": "GUND DACHINA"}, "ALOOSA||MANGNIPORA": {"n": "Dr Aijaz Ahmad", "p": "9149986467", "t": "ALOOSA", "v": "Mangnipora"}, "AJAS||AJAS": {"n": "Leteef Ahmad Shah SMS III", "p": "", "t": "AJAS", "v": "Ajas"}, "GUREZ||BADWAN WANPORA": {"n": "Dr.Sameer Ahmad Lone, VAS", "p": "70066028181", "t": "GUREZ", "v": "Badwan-Wanpora"}, "GUREZ||GULSHANPORA": {"n": "Dr.Ishfaq Ahamd, VAS", "p": "7051260966", "t": "GUREZ", "v": "Gulshanpora"}, "GUREZ||KANZALWAN NAIL": {"n": "Dr.Ishfaq Ahamd, VAS", "p": "7051260966", "t": "GUREZ", "v": "Kanzalwan-Nail"}, "GUREZ||DAWAR": {"n": "Dr.Naseer Shanum, SDO Gurez", "p": "6005498991", "t": "GUREZ", "v": "Dawar"}, "GUREZ||MARKOOT": {"n": "Dr.Naseer Shanum, SDO Gurez", "p": "6005498991", "t": "GUREZ", "v": "Markoot"}, "GUREZ||SHAHPORA ACHOORA CHURWAN": {"n": "Dr.Sameer Ahmad Lone, VAS", "p": "70066028181", "t": "GUREZ", "v": "Shahpora (Achoora -Churwan)"}, "TULAIL||GUJRAN": {"n": "Dr.Nazir Ahmad", "p": "9419418166", "t": "TULAIL", "v": "Gujran"}, "TULAIL||BADUAAB": {"n": "Dr.Nazir Ahmad", "p": "9419418166", "t": "TULAIL", "v": "Baduaab"}, "TULAIL||BUGLINDER": {"n": "Mr.Abdullah", "p": "7006868262", "t": "TULAIL", "v": "Buglinder"}, "TULAIL||SARDAAB": {"n": "Mr.Abdullah", "p": "7006868262", "t": "TULAIL", "v": "Sardaab"}, "TULAIL||BADUGAM": {"n": "Dr.Aabid VAS", "p": "9149696927", "t": "TULAIL", "v": "Badugam"}, "TULAIL||NEERU": {"n": "Dr.Aabid VAS", "p": "9149696927", "t": "TULAIL", "v": "Neeru"}, "TULAIL||JURNIYAL": {"n": "Dr.Aabid VAS", "p": "9149696927", "t": "TULAIL", "v": "Jurniyal"}, "TULAIL||DANGITHAL": {"n": "Dr.Ab Rehman, VAS", "p": "7780815224", "t": "TULAIL", "v": "Dangithal"}, "TULAIL||ZEDGAY": {"n": "Dr.Ab Rehman, VAS", "p": "7780815224", "t": "TULAIL", "v": "Zedgay"}, "TULAIL||BARNAYEE": {"n": "Dr.Ab Rehman, VAS", "p": "7780815224", "t": "TULAIL", "v": "Barnayee"}}


# =========================================================================
# Normalizers
# =========================================================================

def norm_name(s):
    """UPPER, strip non-alphanumeric, collapse whitespace."""
    s = (s or "").upper()
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def canonical_tehsil(tehsil):
    n = norm_name(tehsil)
    if n in TEHSIL_ALIAS:
        return TEHSIL_ALIAS[n]
    return n if n in TEHSIL_REFERENCE else None

def norm_key(tehsil, village):
    canon_t = canonical_tehsil(tehsil) or norm_name(tehsil)
    canon_v = norm_name(village)
    canon_v = VILLAGE_ALIAS.get(canon_v, canon_v)
    return canon_t + "||" + canon_v

def is_first_bucket(village):
    return norm_name(village) in FIRST_BUCKET_VILLAGE_KEYS

def pretty_tehsil(tehsil):
    """Title-case tehsil for display."""
    return tehsil.title() if tehsil else tehsil


# =========================================================================
# CSV loading — read all data/YYYY-MM-DD.csv snapshots and aggregate
# =========================================================================

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))

# Column aliases (match AGRISTACK's various export schemas)
TEHSIL_HEADERS  = ["subDistrictName", "Sub District Name", "Sub-District Name", "subdistrictname", "tehsilName", "Tehsil"]
VILLAGE_HEADERS = ["villageName", "reports.VillageName", "Village Name", "villagename", "Village"]
STATUS_HEADERS  = ["approvalStatus", "Farmer Account Created?", "Farmer Account Created", "accountCreated", "Status"]

def _norm_header(s):
    return re.sub(r"[?_\-\s]", "", (s or "")).lower()

def find_header_idx(header_row, candidates):
    normed = [_norm_header(h) for h in header_row]
    for c in candidates:
        n = _norm_header(c)
        if n in normed:
            return normed.index(n)
    return -1

def normalize_status(raw):
    s = (raw or "").strip().upper()
    if s in ("APPROVED", "YES", "Y", "TRUE"):
        return "APPROVED"
    return s

DATE_FILENAME_RE = re.compile(r"(\d{4})[-._](\d{2})[-._](\d{2})|(\d{2})[-._](\d{2})[-._](\d{4})")

def parse_date_from_filename(fname):
    """Accept 2026-10-04.csv, 04-10-2026.csv, 04.10.2026.csv, etc."""
    base = os.path.basename(fname)
    m = DATE_FILENAME_RE.search(base)
    if not m:
        return None
    g = m.groups()
    if g[0]:  # YYYY-MM-DD
        return "{}-{}-{}".format(g[0], g[1], g[2])
    # DD-MM-YYYY
    return "{}-{}-{}".format(g[5], g[4], g[3])

def parse_snapshot_csv(path):
    """Parse one daily snapshot CSV. Returns {village_key: {issued, approved}} aggregated by village."""
    counts = {}
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return counts
        t_idx = find_header_idx(header, TEHSIL_HEADERS)
        v_idx = find_header_idx(header, VILLAGE_HEADERS)
        s_idx = find_header_idx(header, STATUS_HEADERS)
        if t_idx < 0 or v_idx < 0 or s_idx < 0:
            raise ValueError(
                "{}: header must have tehsil, village and status columns".format(os.path.basename(path))
            )
        for row in reader:
            if not row or len(row) <= max(t_idx, v_idx, s_idx):
                continue
            tehsil = (row[t_idx] or "").strip()
            village = (row[v_idx] or "").strip()
            if not tehsil or not village:
                continue
            key = norm_key(tehsil, village)
            rec = counts.setdefault(key, {
                "tehsil": tehsil, "village": village,
                "issued": 0, "approved": 0,
            })
            rec["issued"] += 1
            if normalize_status(row[s_idx]) == "APPROVED":
                rec["approved"] += 1
    return counts

def discover_snapshots():
    """Scan data/ for CSV files, group by inferred date, return sorted [(YYYY-MM-DD, path)]."""
    snaps = []
    if not os.path.isdir(DATA_DIR):
        return snaps
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.csv"))):
        d = parse_date_from_filename(path)
        if d and d > BASELINE_DATE:
            snaps.append((d, path))
    snaps.sort(key=lambda x: x[0])
    return snaps

def baseline_counts():
    """Return {village_key: {issued, approved, tehsil, village}} from frozen baseline."""
    counts = {}
    if not BASELINE_ENABLED:
        return counts
    for raw_key, (iss, appr) in BASELINE_THROUGH_SEP30.items():
        t, v = raw_key.split("||")
        counts[norm_key(t, v)] = {
            "tehsil": t, "village": v,
            "issued": iss, "approved": appr,
        }
    return counts

def _merge(dst, src):
    """Overlay src counts onto dst for the same village key. AGRISTACK snapshots
    are cumulative — counts only grow — so if src shows fewer records than dst
    (e.g. a partial file was uploaded by mistake), we keep the higher count. This
    keeps per-day additions non-negative under all upload conditions.
    Keys only in dst are kept (so baseline persists for villages not in snapshot).
    """
    for k, v in src.items():
        prev = dst.get(k, {})
        dst[k] = {
            "tehsil": prev.get("tehsil", v["tehsil"]),
            "village": prev.get("village", v["village"]),
            "issued": max(v["issued"], prev.get("issued", 0)),
            "approved": max(v["approved"], prev.get("approved", 0)),
        }

def build_state():
    """Load baseline + all snapshots. Returns:
      - current_counts: {key: {issued, approved, ...}}        -> latest known total per village
      - per_date_counts: {YYYY-MM-DD: {key: {issued,approved}}} -> cumulative at that date (post-overlay)
      - sorted_dates: [YYYY-MM-DD]   -> baseline date first, then snapshot dates
    """
    base = baseline_counts()
    per_date = {BASELINE_DATE: {k: dict(v) for k, v in base.items()}}
    cumulative = {k: dict(v) for k, v in base.items()}
    sorted_dates = [BASELINE_DATE]
    errors = []
    for date, path in discover_snapshots():
        try:
            snap = parse_snapshot_csv(path)
        except Exception as e:
            errors.append(str(e))
            continue
        # Snapshot is a cumulative state (all registered farmers as of that date),
        # so overlay it onto cumulative. Villages not in the snapshot keep their
        # previous (usually baseline) numbers.
        _merge(cumulative, snap)
        per_date[date] = {k: dict(v) for k, v in cumulative.items()}
        sorted_dates.append(date)
    return cumulative, per_date, sorted_dates, errors


# =========================================================================
# Daily additions — differences between consecutive dated snapshots
# =========================================================================

def per_day_additions(per_date, sorted_dates):
    """Return {YYYY-MM-DD: {district_total_issued_added, by_tehsil, by_village_key}}.
    For the baseline date it's the baseline totals themselves (no previous).
    """
    out = {}
    for i, d in enumerate(sorted_dates):
        current = per_date[d]
        if i == 0:
            # Baseline — "additions" is the baseline itself.
            tot = sum(v["issued"] for v in current.values())
            by_t = {}
            by_v = {}
            for k, v in current.items():
                t = canonical_tehsil(v["tehsil"]) or norm_name(v["tehsil"])
                by_t[t] = by_t.get(t, 0) + v["issued"]
                by_v[k] = v["issued"]
            out[d] = {"total": tot, "by_tehsil": by_t, "by_village": by_v}
            continue
        prev = per_date[sorted_dates[i-1]]
        tot = 0
        by_t = {}
        by_v = {}
        all_keys = set(current.keys()) | set(prev.keys())
        for k in all_keys:
            c = current.get(k, {}).get("issued", 0)
            p = prev.get(k, {}).get("issued", 0)
            delta = c - p
            if delta == 0:
                continue
            tehsil_name = (current.get(k) or prev.get(k))["tehsil"]
            t = canonical_tehsil(tehsil_name) or norm_name(tehsil_name)
            by_t[t] = by_t.get(t, 0) + delta
            by_v[k] = delta
            tot += delta
        out[d] = {"total": tot, "by_tehsil": by_t, "by_village": by_v}
    return out


# =========================================================================
# Nested view structure — same shape today's dashboard expects
# =========================================================================

# ---- Overrides persisted in data/ (and committed to GitHub) -----------
TARGETS_OVERRIDE_FILE = os.path.join(DATA_DIR, "targets.json")
CD_OVERRIDE_FILE      = os.path.join(DATA_DIR, "camp_directors.json")

def load_targets_overrides():
    """Return {norm_key: int}. Overrides merge on top of DEFAULT_TARGETS."""
    if not os.path.exists(TARGETS_OVERRIDE_FILE):
        return {}
    try:
        with open(TARGETS_OVERRIDE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {k: int(v) for k, v in raw.items() if v is not None}
    except Exception:
        return {}

def load_cd_overrides():
    """Return {norm_key: {n, p, t, v}} overrides, merged on top of CAMP_DIRECTORS_59."""
    if not os.path.exists(CD_OVERRIDE_FILE):
        return {}
    try:
        with open(CD_OVERRIDE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def effective_targets():
    """DEFAULT_TARGETS as a list of (tehsil, village, target) with overrides applied."""
    overrides = load_targets_overrides()
    seen = set()
    out = []
    for t, v, tg in DEFAULT_TARGETS:
        k = norm_key(t, v)
        seen.add(k)
        out.append((t, v, overrides.get(k, tg)))
    # Any override for a village not in defaults → add as a new target row
    for k, tg in overrides.items():
        if k in seen:
            continue
        parts = k.split("||", 1)
        if len(parts) == 2:
            out.append((parts[0].title(), parts[1].title(), tg))
    return out

def villages_seen_in_uploads():
    """Scan every daily snapshot CSV in data/ and return a dict keyed by
    norm_key -> {tehsil, village} for villages that have ever appeared in a
    snapshot. Used by admin pages to surface villages that showed up in a CSV
    but aren't yet in DEFAULT_TARGETS or CAMP_DIRECTORS_59, so an admin can
    quickly assign targets / directors to them.
    """
    seen = {}
    for date, path in discover_snapshots():
        try:
            counts = parse_snapshot_csv(path)
        except Exception:
            continue
        for key, rec in counts.items():
            # Keep first-seen display spelling
            if key not in seen:
                seen[key] = {"tehsil": rec["tehsil"], "village": rec["village"]}
    return seen

# =========================================================================
# Pending Entries — pulled from the published Google Sheet
# =========================================================================

# The sheet is "Published to web → CSV" so this URL returns raw CSV with no auth.
# Override by setting PENDING_SHEET_URL env var on Render if the URL changes.
DEFAULT_PENDING_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vQ8OtxiJ6k3O37CNEbOgMW_XxOlXvdvKFc22-o0f7cnEBdQF_aBJ8EKjAtkRcnv9PM4jLV0PZ8fCKtq/"
    "pub?output=csv"
)
PENDING_SHEET_URL = os.environ.get("PENDING_SHEET_URL", DEFAULT_PENDING_SHEET_URL)
PENDING_CACHE_SECONDS = int(os.environ.get("PENDING_CACHE_SECONDS", "300"))  # 5 min

# Column-name aliases (checked after norm_header normalization).
PENDING_VILLAGE_HEADERS = ["village", "villagename", "village name", "reports.villagename", "villagescope", "name of village"]
PENDING_TEHSIL_HEADERS  = ["tehsil", "subdistrictname", "sub district name", "subdistrict", "name of tehsil"]
PENDING_COUNT_HEADERS   = [
    "pendingentries", "pending entries",
    "notyetsubmitted", "not yet submitted",
    "pendingentriesonsubmissionportal", "pending entries on submission portal",
    "notsubmitted", "not submitted",
    "pending", "balance",
]

# In-process cache: (ts, data_or_error)
_pending_cache = {"ts": 0, "map": {}, "error": None, "columns_seen": [], "rows_matched": 0}

def _norm_header_cmp(s):
    # Only alphanumeric, lowercase (space/underscore/punctuation stripped).
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def fetch_pending_entries(force=False):
    """Fetch the published Google Sheet and return {norm_key: pending_count}.
    Caches for PENDING_CACHE_SECONDS. Fails silently (returns {} and records
    the error in _pending_cache['error']) so the dashboard still renders.
    """
    import time, urllib.request
    now = time.time()
    if not force and (now - _pending_cache["ts"]) < PENDING_CACHE_SECONDS and _pending_cache["map"]:
        return _pending_cache["map"]
    if not PENDING_SHEET_URL:
        _pending_cache.update({"ts": now, "map": {}, "error": "PENDING_SHEET_URL not set"})
        return {}
    try:
        req = urllib.request.Request(PENDING_SHEET_URL, headers={
            "User-Agent": "farmer-id-portal/1.0 (+https://github.com/)",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        _pending_cache.update({"ts": now, "map": {}, "error": "fetch failed: {}".format(e)})
        return {}

    # Parse CSV
    import io
    try:
        reader = csv.reader(io.StringIO(raw))
        rows = list(reader)
    except Exception as e:
        _pending_cache.update({"ts": now, "map": {}, "error": "CSV parse failed: {}".format(e)})
        return {}
    if not rows or len(rows) < 2:
        _pending_cache.update({"ts": now, "map": {}, "error": "sheet has no data rows"})
        return {}

    # Find header row — accept any row within the first 5 where expected columns are present.
    header_row_idx = None
    v_idx = t_idx = c_idx = -1
    for ri in range(min(5, len(rows))):
        header = rows[ri]
        normed = [_norm_header_cmp(h) for h in header]
        try_v = next((i for i, h in enumerate(normed) if any(h == _norm_header_cmp(cand) for cand in PENDING_VILLAGE_HEADERS)), -1)
        try_t = next((i for i, h in enumerate(normed) if any(h == _norm_header_cmp(cand) for cand in PENDING_TEHSIL_HEADERS)), -1)
        try_c = next((i for i, h in enumerate(normed) if any(h == _norm_header_cmp(cand) for cand in PENDING_COUNT_HEADERS)), -1)
        if try_v >= 0 and try_c >= 0:
            header_row_idx, v_idx, t_idx, c_idx = ri, try_v, try_t, try_c
            break
    if header_row_idx is None:
        header_show = rows[0] if rows else []
        _pending_cache.update({
            "ts": now, "map": {},
            "error": "couldn't find village + pending columns. Headers seen: {}".format(header_show),
            "columns_seen": header_show,
        })
        return {}

    def to_int(s):
        s = str(s or "").strip().replace(",", "")
        if not s:
            return 0
        try:
            return int(float(s))
        except ValueError:
            return 0

    out = {}
    matched = 0
    for row in rows[header_row_idx + 1:]:
        if len(row) <= max(v_idx, c_idx):
            continue
        village = (row[v_idx] or "").strip()
        if not village:
            continue
        tehsil = (row[t_idx] or "").strip() if t_idx >= 0 and t_idx < len(row) else ""
        count  = to_int(row[c_idx])
        if not tehsil:
            # Try to find the tehsil by looking up the village in CAMP_DIRECTORS_59 or targets.
            # Fallback: build key with normalized village only, prefixed by '*' as a wildcard match later.
            # Simpler: iterate known villages to find a match by normalized village name alone.
            matched_tehsil = _find_tehsil_for_village(village)
            if matched_tehsil:
                tehsil = matched_tehsil
        if not tehsil:
            continue  # can't match this row without tehsil
        key = norm_key(tehsil, village)
        out[key] = out.get(key, 0) + count
        matched += 1

    _pending_cache.update({
        "ts": now, "map": out, "error": None,
        "columns_seen": rows[header_row_idx],
        "rows_matched": matched,
    })
    return out

def _find_tehsil_for_village(village):
    """When the sheet lacks a tehsil column, look up which tehsil the village
    belongs to by scanning DEFAULT_TARGETS + CAMP_DIRECTORS_59. Returns tehsil
    string or None.
    """
    v_norm = norm_name(village)
    v_norm = VILLAGE_ALIAS.get(v_norm, v_norm)
    for t, v, _tg in DEFAULT_TARGETS:
        if norm_name(v) == v_norm or VILLAGE_ALIAS.get(norm_name(v), norm_name(v)) == v_norm:
            return t
    for k, cd in CAMP_DIRECTORS_59.items():
        if k.split("||", 1)[1] == v_norm:
            return cd["t"]
    return None


def effective_camp_directors():
    """Return merged camp-director map keyed by norm_key -> {n,p,t,v}.
    Entries whose name is 'NA' / 'N/A' (any case) are treated as 'no director'
    and dropped — the user uses NA as a placeholder for 'not yet assigned'.
    """
    merged = {k: dict(v) for k, v in CAMP_DIRECTORS_59.items()}
    for k, v in load_cd_overrides().items():
        merged[k] = v
    # Filter out NA placeholders — those villages won't appear in CD views.
    out = {}
    for k, v in merged.items():
        name = (v.get("n") or "").strip().upper()
        if name and name not in ("NA", "N/A", "NOT AVAILABLE"):
            out[k] = v
    return out

def camp_director_for(tehsil, village):
    """Overridden version — reads live overrides, not just baked-in."""
    return effective_camp_directors().get(norm_key(tehsil, village))


def build_nested_structure():
    cumulative, per_date, sorted_dates, errors = build_state()

    # Pending Entries pulled from the published Google Sheet (cached).
    pending_map = fetch_pending_entries()

    # Target map — DEFAULT_TARGETS + any overrides from data/targets.json.
    # Keep display spellings alongside targets.
    targets = {}  # norm_key -> target
    display = {}  # norm_key -> (display_tehsil, display_village)
    for t, v, tg in effective_targets():
        k = norm_key(t, v)
        targets[k] = tg
        display[k] = (t, v)

    # Build village rows: union of (targets ∪ cumulative)
    villages = {}
    for key, tgt in targets.items():
        rec = cumulative.get(key)
        if rec:
            villages[key] = {
                "tehsil": rec["tehsil"], "village": rec["village"], "target": tgt,
                "issued": rec["issued"], "approved": rec["approved"],
                "issuedBaseline": BASELINE_THROUGH_SEP30.get(
                    rec["tehsil"] + "||" + rec["village"], [0, 0])[0],
                "approvedBaseline": BASELINE_THROUGH_SEP30.get(
                    rec["tehsil"] + "||" + rec["village"], [0, 0])[1],
                "firstBucket": is_first_bucket(rec["village"]),
                "campDirector": camp_director_for(rec["tehsil"], rec["village"]),
            }
        else:
            # Target with no data yet — baseline fallback with proper display names
            disp_t, disp_v = display.get(key, (key.split("||")[0], key.split("||")[1] if "||" in key else ""))
            iss_b, app_b = 0, 0
            for raw_key, (ib, ab) in BASELINE_THROUGH_SEP30.items():
                rt, rv = raw_key.split("||")
                if norm_key(rt, rv) == key:
                    iss_b, app_b = ib, ab
                    break
            villages[key] = {
                "tehsil": disp_t, "village": disp_v, "target": tgt,
                "issued": iss_b, "approved": app_b,
                "issuedBaseline": iss_b, "approvedBaseline": app_b,
                "firstBucket": is_first_bucket(disp_v),
                "campDirector": camp_director_for(disp_t, disp_v),
            }

    for key, rec in cumulative.items():
        if key in villages:
            continue
        iss_b = BASELINE_THROUGH_SEP30.get(rec["tehsil"] + "||" + rec["village"], [0, 0])[0]
        app_b = BASELINE_THROUGH_SEP30.get(rec["tehsil"] + "||" + rec["village"], [0, 0])[1]
        villages[key] = {
            "tehsil": rec["tehsil"], "village": rec["village"], "target": 0,
            "issued": rec["issued"], "approved": rec["approved"],
            "issuedBaseline": iss_b, "approvedBaseline": app_b,
            "firstBucket": is_first_bucket(rec["village"]),
            "campDirector": camp_director_for(rec["tehsil"], rec["village"]),
        }

    # Attach pending-entries count from the Google Sheet (0 if the village
    # isn't in the sheet or the sheet couldn't be fetched).
    for key, v in villages.items():
        v["pending"] = pending_map.get(key, 0)

    # Attach per-village datedCounts (incremental daily deltas, each positive int)
    daily = per_day_additions(per_date, sorted_dates)
    for d, data in daily.items():
        for vk, delta in data["by_village"].items():
            if vk in villages and delta > 0:
                villages[vk].setdefault("datedCounts", {})
                villages[vk]["datedCounts"][d] = {"issued": delta, "approved": 0}
    # Approvals per date — compute analogously
    for i, d in enumerate(sorted_dates):
        current = per_date[d]
        for key, rec in current.items():
            if key not in villages:
                continue
            if i == 0:
                a_delta = rec["approved"]
            else:
                prev = per_date[sorted_dates[i-1]]
                a_delta = rec["approved"] - prev.get(key, {}).get("approved", 0)
            if a_delta > 0:
                villages[key].setdefault("datedCounts", {})
                villages[key]["datedCounts"].setdefault(d, {"issued": 0, "approved": 0})
                villages[key]["datedCounts"][d]["approved"] = a_delta

    for v in villages.values():
        v.setdefault("datedCounts", {})

    # Group by canonical tehsil
    tehsils = {}
    for canon in TEHSIL_REFERENCE:
        tehsils[canon] = {
            "tehsil": canon, "target": 0, "issued": 0, "approved": 0,
            "issuedBaseline": 0, "approvedBaseline": 0, "pending": 0,
            "villagesBucketed": 0, "bucketed": 0,
            "totalVillages": TEHSIL_REFERENCE[canon]["totalVillages"],
            "totalSurveyNos": TEHSIL_REFERENCE[canon]["totalSurveyNos"],
            "villages": [],
        }
    for key, v in villages.items():
        canon = canonical_tehsil(v["tehsil"]) or norm_name(v["tehsil"])
        if canon not in tehsils:
            tehsils[canon] = {
                "tehsil": v["tehsil"], "target": 0, "issued": 0, "approved": 0,
                "issuedBaseline": 0, "approvedBaseline": 0, "pending": 0,
                "villagesBucketed": 0, "bucketed": 0,
                "totalVillages": 0, "totalSurveyNos": 0, "villages": [],
            }
        t = tehsils[canon]
        t["target"] += v["target"]
        t["issued"] += v["issued"]
        t["approved"] += v["approved"]
        t["issuedBaseline"] += v["issuedBaseline"]
        t["approvedBaseline"] += v["approvedBaseline"]
        t["pending"] += v.get("pending", 0)
        if v["issued"] > 0:
            t["villagesBucketed"] += 1
        t["villages"].append(v)
    # Sort villages inside each tehsil by issue % desc
    for t in tehsils.values():
        t["villages"].sort(key=lambda v: (
            -1 * (v["issued"] / v["target"]) if v["target"] > 0 else -2
        ))
    tehsil_list = sorted(tehsils.values(), key=lambda t: -t["issued"])

    # Last 5 days district additions (strip at top) — most recent dates after baseline
    recent_dates = [d for d in sorted_dates if d > BASELINE_DATE][-5:]
    last5 = [{"date": d, "total": daily[d]["total"]} for d in recent_dates]

    # Dates for range picker
    min_date = sorted_dates[0] if sorted_dates else BASELINE_DATE
    max_date = sorted_dates[-1] if sorted_dates else BASELINE_DATE

    return {
        "tehsils": tehsil_list,
        "last5": last5,
        "minDate": min_date,
        "maxDate": max_date,
        "baselineDate": BASELINE_DATE,
        "baselineLabel": BASELINE_LABEL,
        "generated": datetime.utcnow().isoformat() + "Z",
        "snapshotDates": [d for d in sorted_dates if d > BASELINE_DATE],
        "errors": errors,
        "pendingMeta": {
            "ok": bool(pending_map) and not _pending_cache.get("error"),
            "error": _pending_cache.get("error"),
            "rowsMatched": _pending_cache.get("rows_matched", 0),
            "columnsSeen": _pending_cache.get("columns_seen", []),
            "totalDistrict": sum(pending_map.values()) if pending_map else 0,
        },
    }


# =========================================================================
# Flask routes
# =========================================================================

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or _secrets.token_hex(32)


# ---- Login / admin session ----------------------------------------------

def is_admin():
    return bool(session.get("admin"))

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_admin():
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper

@app.context_processor
def inject_admin_flag():
    return {"is_admin": is_admin()}

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    pw = os.environ.get("ADMIN_PASSWORD", "")
    if request.method == "POST":
        if not pw:
            error = "ADMIN_PASSWORD is not set on the server. Set it in Render → Environment, then try again."
        elif request.form.get("password") == pw:
            session["admin"] = True
            session.permanent = False
            dest = request.args.get("next") or url_for("admin_home")
            return redirect(dest)
        else:
            error = "Incorrect password."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.pop("admin", None)
    return redirect(url_for("dashboard"))

@app.route("/admin")
@admin_required
def admin_home():
    return render_template("admin.html")


# ---- Dashboard (public) --------------------------------------------------

@app.route("/")
def dashboard():
    state = build_nested_structure()
    return render_template("dashboard.html", state=state)

@app.route("/health")
def health():
    return jsonify({"ok": True, "ts": datetime.utcnow().isoformat() + "Z"})


# ---- Upload (admin) ------------------------------------------------------

def _parse_date_input(raw):
    """Accept 04-10-2026, 04.10.2026, 04/10/2026, 2026-10-04. Returns YYYY-MM-DD or None."""
    raw = (raw or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", raw)
    if m:
        return raw
    m = re.match(r"^(\d{2})[./-](\d{2})[./-](\d{4})$", raw)
    if m:
        return "{}-{}-{}".format(m.group(3), m.group(2), m.group(1))
    return None

@app.route("/upload", methods=["GET", "POST"])
@admin_required
def upload():
    error = None
    saved = None
    replaced_date = None  # set when a successful upload replaced an existing snapshot
    if request.method == "POST":
        date = _parse_date_input(request.form.get("date", ""))
        if not date or date <= BASELINE_DATE:
            error = "Date must be in dd-mm-yyyy and after {}.".format(BASELINE_DATE)
        else:
            out_path = os.path.join(DATA_DIR, date + ".csv")
            already_exists = os.path.exists(out_path)
            # If overwriting, the client must confirm (the JS on the page sets this
            # hidden flag after the admin acknowledges the warning).
            if already_exists and request.form.get("confirm_overwrite") != "yes":
                error = ("A snapshot for {} already exists. Tick 'Yes, replace it' and "
                         "resubmit to confirm the overwrite.").format(date)
            else:
                f = request.files.get("csv")
                if not f or not f.filename:
                    error = "Pick a CSV file."
                else:
                    os.makedirs(DATA_DIR, exist_ok=True)
                    f.save(out_path)
                    try:
                        parse_snapshot_csv(out_path)
                    except Exception as e:
                        os.remove(out_path)
                        error = "That CSV didn't parse: {}".format(e)
                    else:
                        saved = date + ".csv"
                        if already_exists:
                            replaced_date = date
                        if os.environ.get("GH_TOKEN") and os.environ.get("GH_REPO"):
                            try:
                                verb = "replace" if already_exists else "add"
                                commit_to_github(out_path, "data/" + date + ".csv",
                                                 "data: {} snapshot for {}".format(verb, date))
                                saved += " (committed to GitHub)"
                            except Exception as e:
                                error = "Saved locally but GitHub push failed: {}".format(e)
    # Collect existing snapshot dates for the UI
    existing = []
    for d, _ in discover_snapshots():
        existing.append(d)
    return render_template("upload.html",
                           baseline_date=BASELINE_DATE,
                           existing_dates=existing,
                           replaced_date=replaced_date,
                           error=error, saved=saved)


# ---- Edit targets (admin) ------------------------------------------------

@app.route("/admin/targets", methods=["GET", "POST"])
@admin_required
def edit_targets():
    error = None
    saved = False
    if request.method == "POST":
        new_overrides = {}
        # 1) Existing-row edits: fields named "target::<norm_key>"
        for key, val in request.form.items():
            if not key.startswith("target::"):
                continue
            vk = key[len("target::"):]
            try:
                n = int(val or 0)
            except ValueError:
                continue
            if n < 0:
                continue
            new_overrides[vk] = n
        # 2) "Unknown" villages (from CSVs) that admin assigned targets to.
        for key, val in request.form.items():
            if not key.startswith("unknown_target::"):
                continue
            vk = key[len("unknown_target::"):]
            v = (val or "").strip()
            if not v:
                continue
            try:
                n = int(v)
                if n >= 0:
                    new_overrides[vk] = n
            except ValueError:
                continue
        # 3) Blank "add new village" row at the very bottom.
        nt = (request.form.get("new_tehsil") or "").strip()
        nv = (request.form.get("new_village") or "").strip()
        nvt = (request.form.get("new_target") or "").strip()
        if nt and nv:
            try:
                ntg = int(nvt or 0)
                if ntg >= 0:
                    new_overrides[norm_key(nt, nv)] = ntg
            except ValueError:
                error = "New village target must be a number."
        # Write to disk + commit
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(TARGETS_OVERRIDE_FILE, "w", encoding="utf-8") as f:
            json.dump(new_overrides, f, indent=2, sort_keys=True)
        if os.environ.get("GH_TOKEN") and os.environ.get("GH_REPO"):
            try:
                commit_to_github(TARGETS_OVERRIDE_FILE, "data/targets.json",
                                 "admin: update village targets")
                saved = True
            except Exception as e:
                error = "Saved locally but GitHub push failed: {}".format(e)
        else:
            saved = True
    # Build row list for display: defaults + any override-only (added) villages
    overrides = load_targets_overrides()
    rows = []
    seen = set()
    for t, v, tg in DEFAULT_TARGETS:
        k = norm_key(t, v)
        seen.add(k)
        rows.append({
            "key": k, "tehsil": t, "village": v,
            "default": tg,
            "current": overrides.get(k, tg),
            "overridden": k in overrides and overrides[k] != tg,
            "added": False,
        })
    # Admin-added villages (keys only in overrides)
    for k, cur in overrides.items():
        if k in seen:
            continue
        parts = k.split("||", 1)
        t = parts[0].title() if len(parts) == 2 else k
        v = parts[1].title() if len(parts) == 2 else ""
        rows.append({
            "key": k, "tehsil": t, "village": v,
            "default": "—",
            "current": cur,
            "overridden": True,
            "added": True,
        })
    rows.sort(key=lambda r: (r["tehsil"], r["village"]))
    # Villages seen in uploaded CSVs but not yet in any target row.
    all_known = seen | set(overrides.keys())
    unknown = []
    for k, info in villages_seen_in_uploads().items():
        if k in all_known:
            continue
        unknown.append({"key": k, "tehsil": info["tehsil"], "village": info["village"]})
    unknown.sort(key=lambda r: (r["tehsil"], r["village"]))
    return render_template("edit_targets.html",
                           rows=rows, unknown=unknown,
                           error=error, saved=saved)


# ---- Edit camp directors (admin) -----------------------------------------

@app.route("/admin/camp-directors", methods=["GET", "POST"])
@admin_required
def edit_camp_directors():
    error = None
    saved = False
    existing_overrides = load_cd_overrides()
    # Keys we know about (baked-in + already-added overrides)
    known_keys = set(CAMP_DIRECTORS_59.keys()) | set(existing_overrides.keys())
    if request.method == "POST":
        new_overrides = {}
        # 1) Edits to existing rows (baked-in or previously added)
        for k in known_keys:
            n_key = "cdname::" + k
            p_key = "cdphone::" + k
            if n_key not in request.form:
                continue
            name = request.form.get(n_key, "").strip()
            phone = request.form.get(p_key, "").strip()
            if not name:
                # blank name = revert to default (if baked-in) or drop (if added)
                continue
            # Determine the display tehsil/village for this key
            default = CAMP_DIRECTORS_59.get(k) or existing_overrides.get(k, {})
            tehsil = default.get("t", k.split("||")[0] if "||" in k else "")
            village = default.get("v", k.split("||")[1] if "||" in k else "")
            # For baked-in: skip writing if unchanged from default
            if k in CAMP_DIRECTORS_59:
                d = CAMP_DIRECTORS_59[k]
                if name == d["n"] and phone == d.get("p", ""):
                    continue
            new_overrides[k] = {"n": name, "p": phone, "t": tehsil, "v": village}
        # 2) "Unknown" villages (from CSVs) that admin assigned directors to.
        posted_unknown_keys = set()
        for key in request.form.keys():
            if key.startswith("unknown_cdname::"):
                posted_unknown_keys.add(key[len("unknown_cdname::"):])
        for vk in posted_unknown_keys:
            name = (request.form.get("unknown_cdname::" + vk) or "").strip()
            phone = (request.form.get("unknown_cdphone::" + vk) or "").strip()
            disp_t = (request.form.get("unknown_tehsil::" + vk) or "").strip()
            disp_v = (request.form.get("unknown_village::" + vk) or "").strip()
            if not name:
                continue
            new_overrides[vk] = {"n": name, "p": phone,
                                 "t": canonical_tehsil(disp_t) or disp_t.upper(),
                                 "v": disp_v}
        # 3) Blank "add new village" row at the very bottom.
        nt = (request.form.get("new_tehsil") or "").strip()
        nv = (request.form.get("new_village") or "").strip()
        nn = (request.form.get("new_cdname") or "").strip()
        np_ = (request.form.get("new_cdphone") or "").strip()
        if nt and nv and nn:
            nk = norm_key(nt, nv)
            new_overrides[nk] = {"n": nn, "p": np_, "t": canonical_tehsil(nt) or nt.upper(), "v": nv}
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CD_OVERRIDE_FILE, "w", encoding="utf-8") as f:
            json.dump(new_overrides, f, indent=2, sort_keys=True)
        if os.environ.get("GH_TOKEN") and os.environ.get("GH_REPO"):
            try:
                commit_to_github(CD_OVERRIDE_FILE, "data/camp_directors.json",
                                 "admin: update camp directors")
                saved = True
            except Exception as e:
                error = "Saved locally but GitHub push failed: {}".format(e)
        else:
            saved = True
        existing_overrides = load_cd_overrides()
        known_keys = set(CAMP_DIRECTORS_59.keys()) | set(existing_overrides.keys())
    # Build row list: baked-in first (sorted), then admin-added villages
    rows = []
    for k, default in sorted(CAMP_DIRECTORS_59.items()):
        cur = existing_overrides.get(k, default)
        rows.append({
            "key": k, "tehsil": default["t"], "village": default["v"],
            "name": cur["n"], "phone": cur.get("p", ""),
            "overridden": k in existing_overrides, "added": False,
        })
    for k, cur in sorted(existing_overrides.items()):
        if k in CAMP_DIRECTORS_59:
            continue
        rows.append({
            "key": k, "tehsil": cur.get("t", k.split("||")[0]),
            "village": cur.get("v", k.split("||")[1] if "||" in k else ""),
            "name": cur.get("n", ""), "phone": cur.get("p", ""),
            "overridden": True, "added": True,
        })
    # Villages that appeared in uploaded CSVs but have no director entry yet.
    all_known_cd = set(CAMP_DIRECTORS_59.keys()) | set(existing_overrides.keys())
    unknown = []
    for k, info in villages_seen_in_uploads().items():
        if k in all_known_cd:
            continue
        unknown.append({"key": k, "tehsil": info["tehsil"], "village": info["village"]})
    unknown.sort(key=lambda r: (r["tehsil"], r["village"]))
    return render_template("edit_camp_directors.html",
                           rows=rows, unknown=unknown,
                           error=error, saved=saved)


# =========================================================================
# Analysis page + CSV / XLSX downloads (public)
# =========================================================================

def _pct(num, den):
    return round((num / den * 100), 1) if den > 0 else 0.0

def analysis_data():
    """Build metrics for the analysis page. Returns a dict with two sections:
    'all' (every village with a target or any issuance) and 'cd' (just the
    camp-director villages). Each section has summary, top, bottom, etc.
    """
    state = build_nested_structure()

    # Flatten all villages
    all_villages = []
    cd_villages = []
    for t in state["tehsils"]:
        for v in t["villages"]:
            row = {
                "tehsil": t["tehsil"],
                "village": v["village"],
                "target": v["target"],
                "issued": v["issued"],
                "approved": v["approved"],
                "issuedBaseline": v["issuedBaseline"],
                "approvedBaseline": v["approvedBaseline"],
                "sinceOct1": v["issued"] - v["issuedBaseline"],
                "issuePct": _pct(v["issued"], v["target"]),
                "approvalPct": _pct(v["approved"], v["issued"]),
                "firstBucket": v["firstBucket"],
                "director": (v["campDirector"] or {}).get("n", "") if v["campDirector"] else "",
                "directorPhone": (v["campDirector"] or {}).get("p", "") if v["campDirector"] else "",
            }
            if row["target"] > 0 or row["issued"] > 0:
                all_villages.append(row)
            if row["director"]:
                cd_villages.append(row)

    def section(rows, label):
        with_target = [r for r in rows if r["target"] > 0]
        no_progress = [r for r in rows if r["sinceOct1"] == 0 and r["target"] > 0]
        overachievers = [r for r in rows if r["issuePct"] >= 100]
        at_risk = sorted([r for r in with_target if r["issuePct"] < 30],
                         key=lambda r: r["issuePct"])
        top10 = sorted(with_target, key=lambda r: -r["issuePct"])[:10]
        bottom10 = sorted(with_target, key=lambda r: r["issuePct"])[:10]
        total_tgt = sum(r["target"] for r in rows)
        total_iss = sum(r["issued"] for r in rows)
        total_apr = sum(r["approved"] for r in rows)
        total_since = sum(r["sinceOct1"] for r in rows)
        return {
            "label": label,
            "count": len(rows),
            "total_target": total_tgt,
            "total_issued": total_iss,
            "total_approved": total_apr,
            "total_since_oct1": total_since,
            "overall_issue_pct": _pct(total_iss, total_tgt),
            "overall_approval_pct": _pct(total_apr, total_iss),
            "top10": top10,
            "bottom10": bottom10,
            "no_progress": no_progress,
            "overachievers": overachievers,
            "at_risk": at_risk,
        }

    # Camp director leaderboard — aggregate per unique director (within a tehsil)
    cd_groups = {}
    for r in cd_villages:
        key = (r["tehsil"], " ".join(r["director"].upper().split()))
        if key not in cd_groups:
            cd_groups[key] = {
                "tehsil": r["tehsil"],
                "director": r["director"],
                "phone": r["directorPhone"],
                "villages": [],
            }
        cd_groups[key]["villages"].append(r)
    director_rows = []
    for g in cd_groups.values():
        total_tgt = sum(v["target"] for v in g["villages"])
        total_iss = sum(v["issued"] for v in g["villages"])
        total_apr = sum(v["approved"] for v in g["villages"])
        total_since = sum(v["sinceOct1"] for v in g["villages"])
        director_rows.append({
            "tehsil": g["tehsil"],
            "director": g["director"],
            "phone": g["phone"],
            "village_count": len(g["villages"]),
            "village_names": ", ".join(v["village"] for v in g["villages"]),
            "target": total_tgt,
            "issued": total_iss,
            "approved": total_apr,
            "sinceOct1": total_since,
            "issuePct": _pct(total_iss, total_tgt),
            "approvalPct": _pct(total_apr, total_iss),
        })
    director_rows_by_pct = sorted(director_rows, key=lambda r: -r["issuePct"])

    # Tehsil ranking
    tehsil_ranking = []
    for t in state["tehsils"]:
        tgt = t["target"]
        iss = t["issued"]
        apr = t["approved"]
        tehsil_ranking.append({
            "tehsil": t["tehsil"],
            "villages": len(t["villages"]),
            "target": tgt,
            "issued": iss,
            "approved": apr,
            "sinceOct1": iss - (t.get("issuedBaseline") or 0),
            "issuePct": _pct(iss, tgt),
            "approvalPct": _pct(apr, iss),
        })
    tehsil_ranking.sort(key=lambda r: -r["issuePct"])

    return {
        "all": section(all_villages, "All villages"),
        "cd":  section(cd_villages, "Camp Director villages (59)"),
        "director_leaderboard": director_rows_by_pct,
        "tehsil_ranking": tehsil_ranking,
        "state_meta": {
            "baselineDate": state["baselineDate"],
            "snapshotDates": state["snapshotDates"],
            "generated": state["generated"],
        },
    }


@app.route("/analysis")
def analysis():
    return render_template("analysis.html", data=analysis_data())


# ---- CSV download: village-level, flat --------------------------------

@app.route("/download/csv")
def download_csv():
    import io
    state = build_nested_structure()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Tehsil", "Village", "Target", "Total Issued", "Approved",
                "Issued through 30 Sep", "Approved through 30 Sep",
                "Added since 1 Oct", "Issue %", "Approval %",
                "First Bucket", "Camp Director", "Director Phone"])
    for t in state["tehsils"]:
        for v in t["villages"]:
            cd = v["campDirector"] or {}
            since = v["issued"] - v["issuedBaseline"]
            issue_pct  = round(v["issued"]/v["target"]*100, 1) if v["target"] > 0 else ""
            appr_pct   = round(v["approved"]/v["issued"]*100, 1) if v["issued"] > 0 else ""
            w.writerow([t["tehsil"], v["village"], v["target"], v["issued"], v["approved"],
                        v["issuedBaseline"], v["approvedBaseline"],
                        since, issue_pct, appr_pct,
                        "Yes" if v["firstBucket"] else "No",
                        cd.get("n", ""), cd.get("p", "")])
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return Response(buf.getvalue(),
                    mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=farmer_id_progress_{today}.csv"})


# ---- XLSX download: multi-sheet workbook ------------------------------

@app.route("/download/xlsx")
def download_xlsx():
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    state = build_nested_structure()
    data  = analysis_data()
    wb = Workbook()

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1A3A5C")
    totals_font = Font(bold=True)
    totals_fill = PatternFill("solid", fgColor="EEF2F7")

    def write_sheet(ws, headers, rows, totals_row=None):
        for i, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=i, value=h)
            c.font = header_font
            c.fill = header_fill
            c.alignment = Alignment(horizontal="left", vertical="center")
        for r_idx, row in enumerate(rows, 2):
            for c_idx, val in enumerate(row, 1):
                ws.cell(row=r_idx, column=c_idx, value=val)
        if totals_row:
            r = len(rows) + 2
            for c_idx, val in enumerate(totals_row, 1):
                cell = ws.cell(row=r, column=c_idx, value=val)
                cell.font = totals_font
                cell.fill = totals_fill
        # Auto column widths (rough)
        for c_idx in range(1, len(headers) + 1):
            max_len = max([len(str(headers[c_idx-1]))] + [len(str(r[c_idx-1])) for r in rows if c_idx-1 < len(r)])
            ws.column_dimensions[get_column_letter(c_idx)].width = min(max_len + 2, 42)
        ws.freeze_panes = "A2"

    # --- Sheet 1: Tehsil Summary ---
    ws = wb.active
    ws.title = "Tehsil Summary"
    headers = ["#", "Tehsil", "Villages", "Target", "Total Issued", "Added since 1 Oct",
               "Issue %", "Approved", "Approval %"]
    rows = []
    gT = gI = gA = gS = 0
    for i, t in enumerate(data["tehsil_ranking"], 1):
        rows.append([i, t["tehsil"], t["villages"], t["target"], t["issued"],
                     t["sinceOct1"], t["issuePct"], t["approved"], t["approvalPct"]])
        gT += t["target"]; gI += t["issued"]; gA += t["approved"]; gS += t["sinceOct1"]
    totals = ["", "TOTAL", "", gT, gI, gS, _pct(gI, gT), gA, _pct(gA, gI)]
    write_sheet(ws, headers, rows, totals)

    # --- Sheet 2: Village Details (all villages) ---
    ws = wb.create_sheet("Village Details")
    headers = ["#", "Tehsil", "Village", "Target", "Total Issued", "Approved",
               "Issued thru 30 Sep", "Approved thru 30 Sep", "Added since 1 Oct",
               "Issue %", "Approval %", "First Bucket", "Camp Director", "Phone"]
    rows = []
    n = 1
    for t in state["tehsils"]:
        for v in t["villages"]:
            cd = v["campDirector"] or {}
            since = v["issued"] - v["issuedBaseline"]
            rows.append([n, t["tehsil"], v["village"], v["target"], v["issued"], v["approved"],
                         v["issuedBaseline"], v["approvedBaseline"], since,
                         _pct(v["issued"], v["target"]), _pct(v["approved"], v["issued"]),
                         "Yes" if v["firstBucket"] else "No",
                         cd.get("n", ""), cd.get("p", "")])
            n += 1
    write_sheet(ws, headers, rows)

    # --- Sheet 3: Camp Director Villages (59) ---
    ws = wb.create_sheet("Camp Director Villages")
    headers = ["#", "Tehsil", "Village", "Target", "Total Issued", "Added since 1 Oct",
               "Issue %", "Approved", "Approval %", "Camp Director", "Phone"]
    rows = []
    n = 1
    for t in state["tehsils"]:
        for v in t["villages"]:
            if not v.get("campDirector"):
                continue
            cd = v["campDirector"]
            since = v["issued"] - v["issuedBaseline"]
            rows.append([n, t["tehsil"], v["village"], v["target"], v["issued"], since,
                         _pct(v["issued"], v["target"]), v["approved"],
                         _pct(v["approved"], v["issued"]), cd["n"], cd.get("p", "")])
            n += 1
    write_sheet(ws, headers, rows)

    # --- Sheet 4: Camp Director Leaderboard ---
    ws = wb.create_sheet("Camp Director Leaderboard")
    headers = ["#", "Director", "Phone", "Tehsil", "Villages Managed", "Village Names",
               "Target", "Total Issued", "Added since 1 Oct", "Issue %", "Approved", "Approval %"]
    rows = []
    for i, d in enumerate(data["director_leaderboard"], 1):
        rows.append([i, d["director"], d["phone"], d["tehsil"], d["village_count"],
                     d["village_names"], d["target"], d["issued"], d["sinceOct1"],
                     d["issuePct"], d["approved"], d["approvalPct"]])
    write_sheet(ws, headers, rows)

    # --- Sheet 5: Daily Deltas ---
    ws = wb.create_sheet("Daily Additions")
    headers = ["Date", "Tehsil", "Village", "Issued (that day)", "Approved (that day)"]
    rows = []
    for t in state["tehsils"]:
        for v in t["villages"]:
            dc = v.get("datedCounts") or {}
            for d in sorted(dc.keys()):
                e = dc[d]
                if (e.get("issued", 0) or e.get("approved", 0)) and d > state["baselineDate"]:
                    rows.append([d, t["tehsil"], v["village"],
                                 e.get("issued", 0), e.get("approved", 0)])
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    write_sheet(ws, headers, rows)

    # Serialize + return
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return Response(bio.getvalue(),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=farmer_id_progress_{today}.xlsx"})


def commit_to_github(local_path, repo_path, message):
    """Push a file to GitHub via API. Requires GH_TOKEN + GH_REPO env vars.
    local_path: path on this server; repo_path: path inside the repo (e.g. 'data/2026-10-04.csv').
    """
    import base64, urllib.request, urllib.error
    token = os.environ["GH_TOKEN"]
    repo = os.environ["GH_REPO"]
    branch = os.environ.get("GH_BRANCH", "main")
    url = "https://api.github.com/repos/{}/contents/{}".format(repo, repo_path)
    with open(local_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("ascii")
    sha = None
    try:
        req = urllib.request.Request(url + "?ref=" + branch,
                                      headers={"Authorization": "Bearer " + token,
                                               "Accept": "application/vnd.github+json"})
        resp = urllib.request.urlopen(req, timeout=15)
        sha = json.loads(resp.read())["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    body = {"message": message, "content": content_b64, "branch": branch}
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                  headers={"Authorization": "Bearer " + token,
                                           "Accept": "application/vnd.github+json",
                                           "Content-Type": "application/json"},
                                  method="PUT")
    urllib.request.urlopen(req, timeout=20)


# Jinja filter so templates can format YYYY-MM-DD as "4 Oct '26"
MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
@app.template_filter("short_date")
def short_date(ymd):
    if not ymd or len(ymd) < 10:
        return ymd or ""
    y, m, d = ymd[:4], ymd[5:7], ymd[8:10]
    try:
        return "{} {} '{}".format(int(d), MONTHS[int(m)-1], y[2:])
    except Exception:
        return ymd


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)

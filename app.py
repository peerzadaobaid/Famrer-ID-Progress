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
import os
import re
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, Response, abort, render_template, request, jsonify

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
    ("Ajas", "S.K. Payeen", 990),
    ("Bandipora", "Brar", 1100),
    ("Bandipora", "Chak Arsala Khan", 638),
    ("Bandipora", "Chak Reshipora", 235),
    ("Bandipora", "Chandaji", 279),
    ("Bandipora", "Labkachal", 136),
    ("Bandipora", "Shamthan", 248),
    ("Bandipora", "Weven", 560),
    ("Gurez", "Koragbal", 123),
    ("Hajin", "Gulabwari", 21),
    ("Hajin", "Gund-i-Balkh", 430),
    ("Hajin", "Gund-i-Ramzan", 10),
    ("Hajin", "Tangpora", 242),
    ("Sonawari", "Gund i Nowgam", 838),
    ("Sonawari", "Malik Pora", 410),
    ("Sonawari", "Najin", 616),
    ("Sonawari", "Zal Pora", 853),
    ("Tulail", "Abdullian", 258),
    ("Tulail", "Manz Gund", 203),
    ("Tulail", "Wazirthal", 121),
    ("Tulail", "Kilshay Payeen", 425),
    ("Tulail", "Puranatulail", 247),
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
CAMP_DIRECTORS_59 = {"SUMBAL||SUMBAL INDERKOTE":{"n":"Dr Syed Mubarak Hussain Shah","p":"9858332300","t":"SUMBAL","v":"SUMBAL INDERKOTE"},"SUMBAL||WAHIDPORA":{"n":"Dr Syed Mubarak Hussain Shah","p":"9858332300","t":"SUMBAL","v":"WAHIDPORA"},"SUMBAL||GANASTAN":{"n":"Jameel Farooq","p":"7006912553","t":"SUMBAL","v":"Ganastan"},"SUMBAL||RAKHI SULTANPORA":{"n":"Jameel Farooq","p":"7006912553","t":"SUMBAL","v":"Rakhi Sultanpora"},"SUMBAL||TRIGAM":{"n":"Dr Suhail Ahmad","p":"9797756269","t":"SUMBAL","v":"Trigam"},"SUMBAL||HILALABAD":{"n":"Dr Suhail Ahmad","p":"9797756269","t":"SUMBAL","v":"HILALABAD"},"SUMBAL||RAKHI SHILVAT":{"n":"Jameel Farooq","p":"7006912553","t":"SUMBAL","v":"RAKHI SHILVAT"},"SUMBAL||ASHAM":{"n":"Jameel Farooq","p":"7006912553","t":"SUMBAL","v":"Asham"},"SUMBAL||SARIE DANGERPORA":{"n":"DR Aasima Zehra","p":"9149687674","t":"SUMBAL","v":"SARIE DANGERPORA"},"SUMBAL||ODINA":{"n":"Dr Shaista Naz","p":"7780935280","t":"SUMBAL","v":"ODINA"},"SUMBAL||GUNDIKHALIL":{"n":"Dr Mudasir Ahmad Shah","p":"9906580671","t":"SUMBAL","v":"GUNDIKHALIL"},"SUMBAL||NOWGAM":{"n":"Dr Mudasir Ahmad Shah","p":"9906580671","t":"SUMBAL","v":"NOWGAM"},"HAJIN||RAKHI HAJIN":{"n":"Ghulam Rasool Hajam","p":"9797826874","t":"HAJIN","v":"RAKHI HAJIN"},"HAJIN||GUND JAHENGEER":{"n":"Ghulam Rasool Hajam","p":"9797826874","t":"HAJIN","v":"Gund Jahengeer"},"HAJIN||GUND SADERKOOT":{"n":"Dr Arif Mohd Khan","p":"7889818735","t":"HAJIN","v":"Gund saderkoot"},"HAJIN||VIJPARA":{"n":"Dr Arif Mohd Khan","p":"7889818735","t":"HAJIN","v":"Vijpara"},"HAJIN||GUND I PRANG":{"n":"Dr Ateeqa","p":"9596657419","t":"HAJIN","v":"GUND- I- PRANG"},"HAJIN||SRI HARI KARANGUND":{"n":"Dr Ateeqa","p":"9596657419","t":"HAJIN","v":"SRI HARI KARANGUND"},"HAJIN||GUNDI BOON":{"n":"Dr Arif Mohd Khan","p":"7889818735","t":"HAJIN","v":"GUNDI BOON"},"HAJIN||KANIPORA":{"n":"Dr Syed Imran","p":"7006409066","t":"HAJIN","v":"KANIPORA"},"HAJIN||POSHWARI":{"n":"Dr Abdul Rashid Teli","p":"8493011248","t":"HAJIN","v":"POSHWARI,"},"HAJIN||GULSHANPORA":{"n":"Dr Abdul Rashid Teli","p":"8493011248","t":"HAJIN","v":"GULSHANPORA"},"BANDIPORA||WATAPORA":{"n":"Aarif Hussain Rather","p":"7006515845","t":"BANDIPORA","v":"WATAPORA"},"BANDIPORA||ARAGAM":{"n":"Dr Tariq Ahmad","p":"9596184321","t":"BANDIPORA","v":"Aragam"},"BANDIPORA||GUNDPORA RAMPORA":{"n":"Dr Tariq Ahmad","p":"9596184321","t":"BANDIPORA","v":"Gundpora Rampora"},"BANDIPORA||NADIHAL":{"n":"Javid Ahmad Dar","p":"9697876747","t":"BANDIPORA","v":"Nadihal"},"BANDIPORA||ONAGAM":{"n":"Javid Ahmad Dar","p":"9697876747","t":"BANDIPORA","v":"Onagam"},"BANDIPORA||SONERWANI":{"n":"Mohd Abbas Mir (HDO)","p":"7006051991","t":"BANDIPORA","v":"Sonerwani"},"BANDIPORA||KHAYAR":{"n":"Mohd Abbas Mir (HDO)","p":"7006051991","t":"BANDIPORA","v":"KHAYAR"},"BANDIPORA||LAWAYPORA":{"n":"Mohd Abbas Mir (HDO)","p":"7006051991","t":"BANDIPORA","v":"LAWAYPORA"},"BANDIPORA||KUDARA":{"n":"Dr Tariq Ahmad","p":"9596184321","t":"BANDIPORA","v":"KUDARA."},"BANDIPORA||GAMROO":{"n":"Gulzar Ahmad Khan","p":"7889808614","t":"BANDIPORA","v":"GAMROO"},"BANDIPORA||LOWDARA":{"n":"Aasif Jameel","p":"7006414605","t":"BANDIPORA","v":"LOWDARA"},"BANDIPORA||GAROORA":{"n":"Javid Ahmad Dar","p":"9697876747","t":"BANDIPORA","v":"GAROORA"},"BANDIPORA||PANJIGAM":{"n":"Dr Aijaz Ahmad","p":"9149986467","t":"BANDIPORA","v":"PANJIGAM"},"BANDIPORA||AHEMSHREIF":{"n":"Dr Reyaz Ahmad","p":"7889374549","t":"BANDIPORA","v":"AHEMSHREIF"},"BANDIPORA||AYATMULLA":{"n":"Dr Reyaz Ahmad","p":"7889374549","t":"BANDIPORA","v":"AYATMULLA"},"BANDIPORA||BHUTTO":{"n":"Dr Ishtiyaq Mughal","p":"7780861724","t":"BANDIPORA","v":"BHUTTO"},"BANDIPORA||NASSU":{"n":"Manzoor Ahmad Malla","p":"7006114318","t":"BANDIPORA","v":"NASSU"},"BANDIPORA||ATHWATOO":{"n":"Dr Sheeraz Ahmad","p":"8491007147","t":"BANDIPORA","v":"ATHWATOO"},"BANDIPORA||GUND DACHINA":{"n":"Dr Adil Majeed","p":"7788932598","t":"BANDIPORA","v":"GUND DACHINA"},"ALOOSA||MANGNIPORA":{"n":"Dr Aijaz Ahmad","p":"9149986467","t":"ALOOSA","v":"Mangnipora"},"AJAS||AJAS":{"n":"Leteef Ahmad Shah / SMS III","p":"","t":"AJAS","v":"Ajas"},"GUREZ||BADWAN WANPORA":{"n":"DR.Sameer Ahmad Lone,VAS","p":"0066028181","t":"GUREZ","v":"Badwan-Wanpora"},"GUREZ||GULSHANPORA":{"n":"Dr.Ishfaq Ahamd,VAS","p":"7051260966","t":"GUREZ","v":"Gulshanpora"},"GUREZ||KANZALWAN NAIL":{"n":"Dr.Ishfaq Ahamd,VAS","p":"7051260966","t":"GUREZ","v":"Kanzalwan-Nail"},"GUREZ||DAWAR":{"n":"Dr.Naseer Shanum.SDO Gurez","p":"6005498991","t":"GUREZ","v":"Dawar"},"GUREZ||MARKOOT":{"n":"Dr.Naseer Shanum.SDO Gurez","p":"6005498991","t":"GUREZ","v":"Markoot"},"GUREZ||SHAHPORA ACHOORA CHURWAN":{"n":"DR.Sameer Ahmad Lone,VAS","p":"0066028181","t":"GUREZ","v":"Shahpora (Achoora -Churwan"},"TULAIL||GUJRAN":{"n":"Dr.Nazir Ahmad","p":"9419418166","t":"TULAIL","v":"Gujran"},"TULAIL||BADUAAB":{"n":"Dr.Nazir Ahmad","p":"9419418166","t":"TULAIL","v":"Baduaab"},"TULAIL||BUGLINDER":{"n":"Mr.Abdullah","p":"7006868262","t":"TULAIL","v":"Buglinder"},"TULAIL||SARDAAB":{"n":"Mr.Abdullah","p":"7006868262","t":"TULAIL","v":"Sardaab"},"TULAIL||BADUGAM":{"n":"Dr.Aabid VAS","p":"9149696927","t":"TULAIL","v":"Badugam"},"TULAIL||NEERU":{"n":"Dr.Aabid VAS","p":"9149696927","t":"TULAIL","v":"Neeru"},"TULAIL||JURNIYAL":{"n":"Dr.Aabid VAS","p":"9149696927","t":"TULAIL","v":"Jurniyal"},"TULAIL||DANGITHAL":{"n":"Dr.Ab Rehman,VAS","p":"7780815224","t":"TULAIL","v":"Dangithal"},"TULAIL||ZEDGAY":{"n":"Dr.Ab Rehman,VAS","p":"7780815224","t":"TULAIL","v":"Zedgay"},"TULAIL||BARNAYEE":{"n":"Dr.Ab Rehman,VAS","p":"7780815224","t":"TULAIL","v":"Barnayee"}}


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
    return (canonical_tehsil(tehsil) or norm_name(tehsil)) + "||" + norm_name(village)

def is_first_bucket(village):
    return norm_name(village) in FIRST_BUCKET_VILLAGE_KEYS

def camp_director_for(tehsil, village):
    return CAMP_DIRECTORS_59.get(norm_key(tehsil, village))

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

def build_nested_structure():
    cumulative, per_date, sorted_dates, errors = build_state()

    # Target map
    targets = {}
    for t, v, tg in DEFAULT_TARGETS:
        targets[norm_key(t, v)] = tg

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
            # Target with no data yet — baseline fallback
            parts = key.split("||")
            # Try to find a baseline entry under either tehsil spelling
            iss_b, app_b = 0, 0
            for raw_key, (ib, ab) in BASELINE_THROUGH_SEP30.items():
                rt, rv = raw_key.split("||")
                if norm_key(rt, rv) == key:
                    iss_b, app_b = ib, ab
                    break
            villages[key] = {
                "tehsil": parts[0], "village": parts[1], "target": tgt,
                "issued": iss_b, "approved": app_b,
                "issuedBaseline": iss_b, "approvedBaseline": app_b,
                "firstBucket": is_first_bucket(parts[1]),
                "campDirector": camp_director_for(parts[0], parts[1]),
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
            "issuedBaseline": 0, "approvedBaseline": 0,
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
                "issuedBaseline": 0, "approvedBaseline": 0,
                "villagesBucketed": 0, "bucketed": 0,
                "totalVillages": 0, "totalSurveyNos": 0, "villages": [],
            }
        t = tehsils[canon]
        t["target"] += v["target"]
        t["issued"] += v["issued"]
        t["approved"] += v["approved"]
        t["issuedBaseline"] += v["issuedBaseline"]
        t["approvedBaseline"] += v["approvedBaseline"]
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
    }


# =========================================================================
# Flask routes
# =========================================================================

app = Flask(__name__)

@app.route("/")
def dashboard():
    state = build_nested_structure()
    return render_template("dashboard.html", state=state)

@app.route("/health")
def health():
    return jsonify({"ok": True, "ts": datetime.utcnow().isoformat() + "Z"})

@app.route("/upload", methods=["GET", "POST"])
def upload():
    error = None
    saved = None
    pw = os.environ.get("ADMIN_PASSWORD", "")
    if request.method == "POST":
        if pw and request.form.get("password") != pw:
            error = "Incorrect password."
        else:
            raw = request.form.get("date", "").strip()
            # Accept 04.10.2026, 04-10-2026, 2026-10-04
            m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", raw)
            if m:
                date = raw
            else:
                m = re.match(r"^(\d{2})[./-](\d{2})[./-](\d{4})$", raw)
                if m:
                    date = "{}-{}-{}".format(m.group(3), m.group(2), m.group(1))
                else:
                    date = None
            if not date or date <= BASELINE_DATE:
                error = "Date must be a valid date after {}. Received: {!r}".format(BASELINE_DATE, raw)
            else:
                f = request.files.get("csv")
                if not f or not f.filename:
                    error = "Pick a CSV file."
                else:
                    os.makedirs(DATA_DIR, exist_ok=True)
                    out_path = os.path.join(DATA_DIR, date + ".csv")
                    f.save(out_path)
                    try:
                        parse_snapshot_csv(out_path)
                    except Exception as e:
                        os.remove(out_path)
                        error = "That CSV didn't parse: {}".format(e)
                    else:
                        saved = out_path
                        # If GH_TOKEN set, attempt a GitHub commit — see README.
                        if os.environ.get("GH_TOKEN") and os.environ.get("GH_REPO"):
                            try:
                                commit_to_github(out_path, date)
                                saved = out_path + " (committed to GitHub)"
                            except Exception as e:
                                error = "Saved locally but GitHub push failed: {}".format(e)
    return render_template("upload.html",
                           baseline_date=BASELINE_DATE,
                           has_password=bool(pw),
                           error=error, saved=saved)

def commit_to_github(path, date):
    """Push the saved CSV to GitHub via API. Requires GH_TOKEN + GH_REPO env vars.
    GH_REPO looks like 'peerzadaobaid/agristack-bandipora'.
    """
    import base64, urllib.request, urllib.error, json
    token = os.environ["GH_TOKEN"]
    repo = os.environ["GH_REPO"]
    branch = os.environ.get("GH_BRANCH", "main")
    api_path = "data/" + date + ".csv"
    url = "https://api.github.com/repos/{}/contents/{}".format(repo, api_path)
    with open(path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("ascii")
    # Check if file already exists to get its SHA
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
    body = {
        "message": "data: snapshot for " + date,
        "content": content_b64,
        "branch": branch,
    }
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

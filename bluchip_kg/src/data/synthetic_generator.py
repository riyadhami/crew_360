"""
Synthetic member data generator — Transactional / Event-Driven Schema
======================================================================

Generates individual Booking, Segment, PartnerTransaction, and AddOn
records so the graph matches the ontology:

    Member → MADE_BOOKING → Booking → CONTAINS_SEGMENT → Segment
    Segment → FLIES_ON → Route → ORIGIN_AIRPORT/DEST_AIRPORT → Airport
    Segment → PURCHASED_ADDON → AddOn
    Member → EARNS_POINTS_FROM → PartnerTransaction → WITH_PARTNER → Partner
    Member → BELONGS_TO_TIER → Tier
    Member → USES_FARE_TYPE → FareType
    Member → PREFERS_ROUTE → Route

No real customer data is used.  All values follow statistically plausible
distributions based on typical IndiGo BluChip programme demographics.
"""

import random
import uuid
from datetime import datetime, timedelta

from faker import Faker

from .static_data import AIRPORTS, ADDONS, FARE_TYPES, PARTNERS, TIERS

# ── Faker instances — locale-weighted to match nationality distribution ────────
_fakers = {
    "IN": Faker("en_IN"),
    "AE": Faker("en_GB"),   # no Arabic locale in faker
    "SG": Faker("en_GB"),
    "US": Faker("en_US"),
    "GB": Faker("en_GB"),
}

# ── Static look-ups ───────────────────────────────────────────────────────────
_METRO_CODES   = [a["code"] for a in AIRPORTS if a["city_type"] == "METRO"]
_LEISURE_CODES = [a["code"] for a in AIRPORTS if "LEISURE" in a["city_type"]]
_INTL_CODES    = [a["code"] for a in AIRPORTS if a["city_type"] == "INTERNATIONAL"]
_ALL_DOM_CODES = _METRO_CODES + _LEISURE_CODES

_FINANCIAL_PARTNERS     = [p for p in PARTNERS if p["category"] == "FINANCIAL"]
_NON_FINANCIAL_PARTNERS = [p for p in PARTNERS if p["category"] == "NON_FINANCIAL"]

_FARE_NAMES = [f["name"] for f in FARE_TYPES]


# ── Identity helpers ──────────────────────────────────────────────────────────

def _generate_identity(gender: str, nationality: str) -> dict:
    """Return first_name, last_name, full_name, email, phone using Faker."""
    fake = _fakers.get(nationality, _fakers["IN"])
    first_name = fake.first_name_male() if gender == "MALE" else fake.first_name_female()
    last_name  = fake.last_name()
    full_name  = f"{first_name} {last_name}"
    email = (
        f"{first_name.lower()}.{last_name.lower()}{random.randint(1, 99)}"
        f"@{random.choices(['gmail.com','yahoo.com','outlook.com','hotmail.com','icloud.com'], weights=[55,20,12,8,5])[0]}"
    )
    return {
        "first_name": first_name,
        "last_name":  last_name,
        "full_name":  full_name,
        "email":      email,
        "phone":      fake.phone_number(),
    }


# ── Date helpers ──────────────────────────────────────────────────────────────

def _days_ago(min_days: int, max_days: int) -> str:
    delta = random.randint(min_days, max_days)
    return (datetime.now() - timedelta(days=delta)).strftime("%Y-%m-%d")


def _date_after(date_str: str, min_days: int, max_days: int) -> str:
    """Return a date string that is min_days–max_days after date_str."""
    base = datetime.strptime(date_str, "%Y-%m-%d")
    return (base + timedelta(days=random.randint(min_days, max_days))).strftime("%Y-%m-%d")


# ── Tier assignment ───────────────────────────────────────────────────────────

def _assign_tier(annual_spend: int, annual_flights: int) -> str:
    if annual_spend >= 200_000 and annual_flights >= 8:
        return "Blu 1"
    if annual_spend >= 100_000 and annual_flights >= 4:
        return "Blu 2"
    return "Blu 3"


def _compute_bluchips_balance(total_spend: int, tier_name: str, b2c_channel: str) -> int:
    """Earned BluChips minus a random redemption fraction."""
    tier_bonus   = {"Blu 3": 0, "Blu 2": 2, "Blu 1": 4}[tier_name]
    chan_bonus    = 4 if b2c_channel in ("webapp", "mobileapp") else 0
    effective    = 8 + tier_bonus + (chan_bonus * 0.70)
    earned       = int((total_spend / 100) * effective)
    redeemed     = int(earned * random.uniform(0.10, 0.45))
    return max(0, earned - redeemed)


def _bluchip_earn(partner: dict, spend_amount: int) -> int:
    return int((spend_amount / partner["unit_spend"]) * partner["bluchips_per_unit"])


# ── Segment generator ─────────────────────────────────────────────────────────

def _generate_segment(
    segment_id: str,
    origin: str,
    dest: str,
    flight_date: str,
    travel_purpose: str,
    traveller_type: str,
    seat_pref: str,
    meal_pref: str,
    is_international: bool,
    excess_baggage_prob: float,
) -> dict:
    """Generate one flight segment vertex dict."""
    # Duration heuristic
    if is_international:
        duration = random.randint(200, 420)
    elif origin in _METRO_CODES and dest in _METRO_CODES:
        duration = random.randint(80, 165)
    else:
        duration = random.randint(55, 100)

    tod = random.choices(
        ["MORNING", "DAY", "EVENING", "REDEYE"],
        weights=[35, 30, 25, 10],
    )[0]

    flight_dt  = datetime.strptime(flight_date, "%Y-%m-%d")
    travel_day = "WEEKEND" if flight_dt.weekday() >= 5 else "WEEKDAY"

    # Seat — 80% chance member's preferred seat, else random
    seat_type = seat_pref if random.random() < 0.80 else random.choice(["WINDOW", "AISLE", "MIDDLE"])

    # Meal
    if random.random() < 0.55:
        meal = "VEG" if meal_pref == "VEG" else "NON_VEG"
    else:
        meal = "NONE"

    # Baggage
    if is_international:
        baggage = random.choices([15, 20, 25, 30], weights=[20, 40, 30, 10])[0]
        excess  = random.random() < 0.15
    else:
        baggage = random.choices([0, 15, 20, 25], weights=[20, 40, 25, 15])[0]
        excess  = random.random() < excess_baggage_prob

    # For MIXED travel purpose, randomly assign segment-level purpose
    if travel_purpose == "MIXED":
        seg_purpose = random.choice(["BUSINESS", "LEISURE"])
    else:
        seg_purpose = travel_purpose

    return {
        "segment_id":         segment_id,
        "flight_number":      f"6E-{random.randint(100, 9999)}",
        "flight_date":        flight_date,
        "origin":             origin,
        "destination":        dest,
        "flight_duration_min": duration,
        "flight_time_of_day": tod,
        "travel_day":         travel_day,
        "seat_type":          seat_type,
        "meal_ordered":       meal,
        "baggage_weight_kg":  baggage,
        "excess_baggage":     excess,
        "travel_status":      random.choices(["BOARDED", "NO_SHOW"], weights=[97, 3])[0],
        "is_international":   is_international,
        "travel_purpose":     seg_purpose,
        "traveller_companions": traveller_type,
    }


# ── Add-on picker ─────────────────────────────────────────────────────────────

def _pick_addons(seat_pref: str, meal_pref: str, excess_baggage: bool, is_international: bool) -> list:
    """Return a list of AddOn vertex IDs to attach to this segment."""
    addons = []

    # Seat selection (70 % pre-book)
    if random.random() < 0.70:
        seat_map = {
            "WINDOW": "addon__window_seat",
            "AISLE":  "addon__aisle_seat",
            "MIDDLE": "addon__middle_seat",
            "XL":     "addon__xl_seat",
        }
        addons.append(seat_map.get(seat_pref, "addon__aisle_seat"))

    # Meal (45 % chance)
    if random.random() < 0.45:
        addons.append("addon__meal_veg" if meal_pref == "VEG" else "addon__meal_nonveg")

    # Fast Forward (28 % chance)
    if random.random() < 0.28:
        addons.append("addon__fast_forward")

    # Excess baggage
    if excess_baggage:
        addons.append(random.choice([
            "addon__excess_baggage_5kg",
            "addon__excess_baggage_10kg",
            "addon__excess_baggage_15kg",
        ]))

    # Snack combo if no pre-booked meal (18 % chance)
    has_meal = "addon__meal_veg" in addons or "addon__meal_nonveg" in addons
    if not has_meal and random.random() < 0.18:
        addons.append("addon__snack_combo")

    return list(set(addons))


# ── Booking generator ─────────────────────────────────────────────────────────

def _generate_booking(
    origin: str,
    dest: str,
    booking_date: str,
    fare_type: str,
    b2c_channel: str,
    is_roundtrip: bool,
    is_international: bool,
    travel_purpose: str,
    traveller_type: str,
    seat_pref: str,
    meal_pref: str,
    avg_fare: int,
    excess_baggage_prob: float,
) -> dict:
    """Generate one booking dict (with segments + addon_ids)."""
    booking_id = f"BK{random.randint(10_000_000, 99_999_999)}"
    status     = random.choices(["CONFIRMED", "CANCELLED", "CHANGED"], weights=[85, 10, 5])[0]
    fare_factor = random.uniform(0.75, 1.35)
    total_fare  = max(1500, int(avg_fare * fare_factor * (1.8 if is_roundtrip else 1.0)))

    booking = {
        "booking_id":      booking_id,
        "booking_date":    booking_date,
        "booking_channel": b2c_channel,
        "booking_status":  status,
        "fare_type":       fare_type,
        "trip_type":       "ROUNDTRIP" if is_roundtrip else "ONEWAY",
        "is_international": is_international,
        "promo_code_used": random.random() < 0.30,
        "self_booked":     random.random() < 0.75,
        "is_insured":      random.random() < 0.22,
        "total_fare":      total_fare,
    }

    # Outbound segment
    out_flight_date = _date_after(booking_date, 1, 45)
    out_seg_id      = f"SEG{random.randint(10_000_000, 99_999_999)}"
    outbound = _generate_segment(
        out_seg_id, origin, dest, out_flight_date,
        travel_purpose, traveller_type, seat_pref, meal_pref,
        is_international, excess_baggage_prob,
    )
    out_addons = _pick_addons(seat_pref, meal_pref, outbound["excess_baggage"], is_international)
    segments   = [{"segment": outbound, "addon_ids": out_addons}]

    # Return segment (for round trips)
    if is_roundtrip:
        ret_flight_date = _date_after(out_flight_date, 2, 14)
        ret_seg_id      = f"SEG{random.randint(10_000_000, 99_999_999)}"
        return_seg = _generate_segment(
            ret_seg_id, dest, origin, ret_flight_date,
            travel_purpose, traveller_type, seat_pref, meal_pref,
            is_international, excess_baggage_prob,
        )
        ret_addons = _pick_addons(seat_pref, meal_pref, return_seg["excess_baggage"], is_international)
        segments.append({"segment": return_seg, "addon_ids": ret_addons})

    return {"booking": booking, "segments": segments}


# ── Partner transaction generator ─────────────────────────────────────────────

def _generate_partner_transactions(
    financial_partner_choice: dict | None,
    non_financial_choices: list,
) -> list:
    """
    Generate individual PartnerTransaction records.

    Args:
        financial_partner_choice: {"partner": <partner dict>, "txn_count": int} | None
        non_financial_choices:    list of {"partner": <partner dict>, "txn_count": int}

    Returns:
        list of {"transaction": <props dict>, "partner_id": <str>}
    """
    transactions = []

    if financial_partner_choice:
        fp         = financial_partner_choice["partner"]
        txn_count  = financial_partner_choice["txn_count"]
        for _ in range(txn_count):
            spend        = random.randint(2_000, 18_000)
            act_date     = _days_ago(1, 365)
            issue_date   = _date_after(act_date, 3, 8)
            transactions.append({
                "transaction": {
                    "transaction_id":  f"TXN{random.randint(10_000_000, 99_999_999)}",
                    "lms_txn_id":      f"LMS{random.randint(100_000, 999_999)}",
                    "partner_txn_id":  f"PTXN{random.randint(100_000, 999_999)}",
                    "activity_date":   act_date,
                    "issue_date":      issue_date,
                    "bluchips_earned": _bluchip_earn(fp, spend),
                    "spend_amount":    spend,
                    "spend_currency":  "INR",
                    "product_type":    random.choice(["BASE", "XL"]),
                    "subcategory":     fp["business_category"],
                    "network":         fp.get("network") or "",
                },
                "partner_id": fp["id"],
            })

    for nfp_data in non_financial_choices:
        nfp       = nfp_data["partner"]
        txn_count = nfp_data["txn_count"]
        for _ in range(txn_count):
            spend      = random.randint(100, 3_000)
            act_date   = _days_ago(1, 365)
            issue_date = _date_after(act_date, 2, 6)
            transactions.append({
                "transaction": {
                    "transaction_id":  f"TXN{random.randint(10_000_000, 99_999_999)}",
                    "lms_txn_id":      f"LMS{random.randint(100_000, 999_999)}",
                    "partner_txn_id":  f"PTXN{random.randint(100_000, 999_999)}",
                    "activity_date":   act_date,
                    "issue_date":      issue_date,
                    "bluchips_earned": _bluchip_earn(nfp, spend),
                    "spend_amount":    spend,
                    "spend_currency":  "INR",
                    "product_type":    "",
                    "subcategory":     nfp["business_category"],
                    "network":         "",
                },
                "partner_id": nfp["id"],
            })

    return transactions


# ── Core generator ────────────────────────────────────────────────────────────

def generate_member(ffn_id: str | None = None) -> dict:
    """
    Generate one synthetic BluChip member with full transactional history.

    Returns:
        {
          "member":               → Member vertex properties
          "tier":                 → BELONGS_TO_TIER edge properties
          "bookings":             → list of {booking, segments:[{segment, addon_ids}]}
          "partner_transactions": → list of {transaction, partner_id}
          "preferred_routes":     → list of routes for PREFERS_ROUTE edges
          "fare_usage":           → list for USES_FARE_TYPE edges
        }
    """
    if ffn_id is None:
        ffn_id = f"IB{random.randint(10_000_000, 99_999_999)}"

    # ── Demographics ──────────────────────────────────────────────────────────
    age         = random.randint(22, 62)
    gender      = random.choices(["MALE", "FEMALE"], weights=[65, 35])[0]
    nationality = random.choices(
        ["IN", "AE", "SG", "US", "GB"],
        weights=[85, 5, 4, 3, 3],
    )[0]

    # ── Travel volume (drives tier) ───────────────────────────────────────────
    tier_roll = random.random()
    if tier_roll < 0.60:
        total_bookings = random.randint(1, 5)
        avg_fare       = random.randint(3_000, 12_000)
    elif tier_roll < 0.90:
        total_bookings = random.randint(4, 14)
        avg_fare       = random.randint(8_000, 18_000)
    else:
        total_bookings = random.randint(8, 28)
        avg_fare       = random.randint(12_000, 28_000)

    # ── Travel purpose ─────────────────────────────────────────────────────────
    purpose_roll = random.random()
    if purpose_roll < 0.35:
        travel_purpose = "BUSINESS"
    elif purpose_roll < 0.65:
        travel_purpose = "LEISURE"
    else:
        travel_purpose = "MIXED"

    # ── Traveller type ─────────────────────────────────────────────────────────
    traveller_roll = random.random()
    if traveller_roll < 0.35:
        traveller_type = "SOLO"
    elif traveller_roll < 0.60:
        traveller_type = "FAMILY"
    elif traveller_roll < 0.80:
        traveller_type = "COMPANION"
    else:
        traveller_type = "GROUP"

    # ── Booking channel ────────────────────────────────────────────────────────
    b2c_channel = random.choices(
        ["webapp", "mobileapp", "ota"],
        weights=[50, 30, 20],
    )[0]

    # ── Preferences ───────────────────────────────────────────────────────────
    meal_pref        = random.choices(["VEG", "NON_VEG"], weights=[55, 45])[0]
    seat_pref        = random.choices(["WINDOW", "AISLE", "MIDDLE", "XL"], weights=[40, 35, 10, 15])[0]
    excess_bag_prob  = random.uniform(0.08, 0.25)

    # ── Fare preference ────────────────────────────────────────────────────────
    raw_weights = {
        "SAVER":         0.40,
        "FLEXI":         0.20,
        "CORPORATE":     0.15 if travel_purpose in ("BUSINESS", "MIXED") else 0.04,
        "SME":           0.05,
        "SUPER6E":       0.10,
        "STRETCH":       0.05,
        "PROMO":         0.10,
        "RETURN_FAMILY": 0.08,
    }
    total_w      = sum(raw_weights.values())
    norm_w       = {k: v / total_w for k, v in raw_weights.items()}
    fare_names   = list(norm_w.keys())
    fare_wts     = list(norm_w.values())
    primary_fare = random.choices(fare_names, weights=fare_wts)[0]

    # ── Preferred routes ───────────────────────────────────────────────────────
    preferred_origins = random.sample(_METRO_CODES, k=min(2, random.randint(1, 2)))
    dest_pool = [c for c in (_METRO_CODES + _LEISURE_CODES) if c not in preferred_origins]
    preferred_dests   = random.sample(dest_pool, k=min(4, random.randint(2, 4)))

    intl_roll = random.random()
    intl_bookings_count = 0
    if intl_roll < 0.70:
        intl_bookings_count = 0
    elif intl_roll < 0.90:
        intl_bookings_count = random.randint(1, max(1, int(total_bookings * 0.20)))
        preferred_dests.append(random.choice(_INTL_CODES))
    else:
        intl_bookings_count = random.randint(1, max(1, int(total_bookings * 0.40)))
        preferred_dests.append(random.choice(_INTL_CODES))

    # ── Generate individual bookings ──────────────────────────────────────────
    all_bookings        = []
    route_counts: dict  = {}   # (origin, dest) → count, for PREFERS_ROUTE derivation
    fare_counts: dict   = {}   # fare_name → count, for USES_FARE_TYPE derivation
    total_spend         = 0
    total_segments      = 0

    intl_remaining = intl_bookings_count
    for i in range(total_bookings):
        # Route selection
        origin = random.choice(preferred_origins)
        is_intl = (intl_remaining > 0) and (random.random() < 0.5 or i == total_bookings - 1)
        if is_intl and intl_remaining > 0:
            dest = preferred_dests[-1]  # last entry is an intl airport if added
            intl_remaining -= 1
        else:
            dom_dests = [d for d in preferred_dests if d not in _INTL_CODES and d != origin]
            dest = random.choice(dom_dests) if dom_dests else random.choice(_ALL_DOM_CODES)

        # Booking meta
        booking_date = _days_ago(30, 400)
        fare_type    = random.choices(fare_names, weights=fare_wts)[0]
        is_roundtrip = random.random() < 0.42

        bk = _generate_booking(
            origin, dest, booking_date, fare_type, b2c_channel,
            is_roundtrip, is_intl,
            travel_purpose, traveller_type, seat_pref, meal_pref,
            avg_fare, excess_bag_prob,
        )
        all_bookings.append(bk)
        total_spend    += bk["booking"]["total_fare"]
        total_segments += len(bk["segments"])

        # Tally routes and fares
        key = (origin, dest, "INTERNATIONAL" if is_intl else "DOMESTIC")
        route_counts[key] = route_counts.get(key, 0) + 1
        if is_roundtrip:
            ret_key = (dest, origin, "INTERNATIONAL" if is_intl else "DOMESTIC")
            route_counts[ret_key] = route_counts.get(ret_key, 0) + 1
        fare_counts[fare_type] = fare_counts.get(fare_type, 0) + 1

    # ── Tier assignment ───────────────────────────────────────────────────────
    tier_name        = _assign_tier(total_spend, total_bookings)
    bluchips_balance = _compute_bluchips_balance(total_spend, tier_name, b2c_channel)
    tier_since       = _days_ago(30, 730)

    # ── Spending segment / frequency ──────────────────────────────────────────
    high_freq  = total_bookings >= 6
    high_spend = total_spend >= 60_000
    if high_freq and high_spend:
        spending_segment = "HIGH_FREQ_HIGH_SPEND"
    elif high_freq:
        spending_segment = "HIGH_FREQ_LOW_SPEND"
    elif high_spend:
        spending_segment = "LOW_FREQ_HIGH_SPEND"
    else:
        spending_segment = "LOW_FREQ_LOW_SPEND"

    if total_bookings == 1:
        flyer_frequency = "ONETIME_FLYER"
    elif total_bookings <= 4:
        flyer_frequency = "ANNUAL_TRIPPER"
    else:
        flyer_frequency = "FREQUENT_FLYER"

    # ── PREFERS_ROUTE: top routes by trip count ───────────────────────────────
    sorted_routes = sorted(route_counts.items(), key=lambda x: x[1], reverse=True)
    preferred_routes = []
    for (orig, dst, rtype), cnt in sorted_routes[:4]:
        # Find a plausible last_flown date from segments on this route
        last_flown = _days_ago(7, 180)
        preferred_routes.append({
            "origin":       orig,
            "destination":  dst,
            "trip_count":   cnt,
            "last_flown":   last_flown,
            "route_type":   rtype,
        })

    # ── USES_FARE_TYPE: per-fare usage counts ─────────────────────────────────
    fare_usage = []
    for fname, cnt in sorted(fare_counts.items(), key=lambda x: x[1], reverse=True):
        fare_usage.append({
            "fare_name":      fname,
            "usage_count":    cnt,
            "pct_of_bookings": round(cnt / max(1, total_bookings), 2),
        })

    # ── Partner transactions (individual) ─────────────────────────────────────
    financial_choice    = None
    non_financial_list  = []

    if random.random() < 0.40:
        fp          = random.choice(_FINANCIAL_PARTNERS)
        txn_count   = random.randint(2, 20)
        financial_choice = {"partner": fp, "txn_count": txn_count}

    if random.random() < 0.60:
        num_nfp = random.randint(1, 3)
        for nfp in random.sample(_NON_FINANCIAL_PARTNERS, k=min(num_nfp, len(_NON_FINANCIAL_PARTNERS))):
            txn_count = random.randint(1, 10)
            non_financial_list.append({"partner": nfp, "txn_count": txn_count})

    partner_transactions = _generate_partner_transactions(financial_choice, non_financial_list)

    # ── Assemble result ───────────────────────────────────────────────────────
    return {
        "member": {
            "ffn_id":              ffn_id,
            **_generate_identity(gender, nationality),
            "age":                 age,
            "gender":              gender,
            "nationality":         nationality,
            "contactable":         random.random() < 0.85,
            "unique_mobile_count": random.choices([1, 2, 3], weights=[70, 20, 10])[0],
            "unique_email_count":  random.choices([1, 2],    weights=[75, 25])[0],
            "has_passport":        (nationality != "IN") or (random.random() < 0.45),
            "is_only_booker":      random.random() < 0.35,
            "customer_type":       "REPEAT" if total_bookings > 1 else "NEW",
            "hotel_customer":      random.random() < 0.25,
            "user_uniq_id":        str(uuid.uuid4())[:12],
            # Behavioural summary (convenience — full history is in Booking/Segment nodes)
            "spending_segment":    spending_segment,
            "travel_purpose":      travel_purpose,
            "traveller_type":      traveller_type,
            "flyer_frequency":     flyer_frequency,
            "primary_channel":     b2c_channel,
            "primary_fare":        primary_fare,
        },
        "tier": {
            "tier_name":           tier_name,
            "bluchips_balance":    bluchips_balance,
            "qualifying_spend":    total_spend,
            "qualifying_flights":  total_bookings,
            "tier_since":          tier_since,
        },
        "bookings":             all_bookings,
        "partner_transactions": partner_transactions,
        "preferred_routes":     preferred_routes,
        "fare_usage":           fare_usage,
    }


def generate_members(n: int = 20, seed: int = 42) -> list[dict]:
    """Generate `n` unique synthetic members with deterministic seed."""
    random.seed(seed)
    seen: set[str] = set()
    members: list[dict] = []
    while len(members) < n:
        ffn_id = f"IB{random.randint(10_000_000, 99_999_999)}"
        if ffn_id in seen:
            continue
        seen.add(ffn_id)
        members.append(generate_member(ffn_id=ffn_id))
    return members

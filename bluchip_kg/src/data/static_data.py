# Static reference data for the IndiGo BluChip Knowledge Graph.
# Tier data scraped from goindigo.in/loyalty/tier-benefits.html (June 2026).
# Partner data sourced from partner_data.txt.

# ── Tier qualification is spend-based + flight-count, NOT BluChips-balance ──
#
#   Blu 3  →  Entry tier. All new members start here. No upgrade requirement.
#   Blu 2  →  ₹1,00,000 spend + min 4 IndiGo flights in any 12-month window.
#   Blu 1  →  ₹2,00,000 spend + min 8 IndiGo flights in any 12-month window.
#
#   BluChips BALANCE is separate — it accumulates from flights/partners/add-ons
#   and can be redeemed. Tier status does NOT depend on it.

TIERS = [
    {
        "id": "tier__blu3",
        "name": "Blu 3",
        "rank": 1,                            # 1 = worst, 3 = best
        "description": "Entry tier — all new members start here. No expiry, no upgrade requirement.",
        # Qualification (to reach this tier)
        "upgrade_spend_inr": None,            # N/A — entry tier
        "upgrade_min_flights": None,
        "retention_spend_inr": None,          # N/A — entry tier never lapses
        "retention_min_flights": None,
        "tier_validity_days": None,           # No expiry
        # BluChips earn rates (per ₹100 spent on flights)
        "base_earn_rate": 8,                  # all tiers share this
        "tier_bonus_rate": 0,                 # no bonus at Blu 3
        "channel_bonus_rate": 4,              # extra 4 if booked via IndiGo web/app
        "max_flight_earn_rate": 12,           # base + channel (8 + 4)
        "addon_earn_rate": 8,                 # on pre-booked seat/baggage/fast-fwd
        # Perks
        "prime_passes": 0,
        "addon_discount_pct": 10,             # 10% off pre-booked seat/bag/fast-fwd
        "benefits": [
            "8 BluChips / ₹100 on IndiGo flights (base fare + fuel surcharge)",
            "4 bonus BluChips / ₹100 when booking via IndiGo website or app",
            "8 BluChips / ₹100 on select 6E Add-ons",
            "10% discount on pre-booked Seat Select, Excess Baggage, Fast Forward",
            "BluChips valid as long as programme active (24-month window)",
            "Up to 5 nominees",
        ],
    },
    {
        "id": "tier__blu2",
        "name": "Blu 2",
        "rank": 2,
        "description": "Mid tier — unlocked by ₹1L spend + 4 flights in 12 months.",
        # Qualification
        "upgrade_spend_inr": 100_000,         # ₹1,00,000 to reach Blu 2 from Blu 3
        "upgrade_min_flights": 4,
        "retention_spend_inr": 100_000,       # same threshold to stay at Blu 2
        "retention_min_flights": 4,
        "tier_validity_days": 365,
        # Earn rates
        "base_earn_rate": 8,
        "tier_bonus_rate": 2,                 # +2 tier bonus
        "channel_bonus_rate": 4,
        "max_flight_earn_rate": 14,           # 8 base + 2 tier + 4 channel
        "addon_earn_rate": 10,                # 8 + 2 tier bonus
        # Perks
        "prime_passes": 20,
        "addon_discount_pct": 20,
        "benefits": [
            "8 BluChips / ₹100 base + 2 tier bonus = 10 BluChips / ₹100 on flights",
            "4 bonus BluChips / ₹100 when booking via IndiGo website or app (total 14)",
            "10 BluChips / ₹100 on select 6E Add-ons",
            "20 × 6E Prime passes (free seat + snack combo + Fast Forward per pass)",
            "20% discount on pre-booked Seat Select, Excess Baggage, Fast Forward",
            "BluChips valid as long as programme active (24-month window)",
            "Up to 5 nominees",
        ],
    },
    {
        "id": "tier__blu1",
        "name": "Blu 1",
        "rank": 3,
        "description": "Top tier — unlocked by ₹2L spend + 8 flights in 12 months.",
        # Qualification
        "upgrade_spend_inr": 200_000,         # ₹2,00,000 to reach Blu 1 from Blu 2
        "upgrade_min_flights": 8,
        "retention_spend_inr": 200_000,
        "retention_min_flights": 8,
        "tier_validity_days": 365,
        # Earn rates
        "base_earn_rate": 8,
        "tier_bonus_rate": 4,                 # +4 tier bonus
        "channel_bonus_rate": 4,
        "max_flight_earn_rate": 16,           # 8 base + 4 tier + 4 channel
        "addon_earn_rate": 12,                # 8 + 4 tier bonus
        # Perks
        "prime_passes": 40,
        "addon_discount_pct": 30,
        "benefits": [
            "8 BluChips / ₹100 base + 4 tier bonus = 12 BluChips / ₹100 on flights",
            "4 bonus BluChips / ₹100 when booking via IndiGo website or app (total 16)",
            "12 BluChips / ₹100 on select 6E Add-ons",
            "40 × 6E Prime passes (free seat + snack combo + Fast Forward per pass)",
            "30% discount on pre-booked Seat Select, Excess Baggage, Fast Forward",
            "BluChips valid as long as programme active (24-month window)",
            "Up to 5 nominees",
        ],
    },
]

PARTNERS = [
    # ── Non-Financial Partners ────────────────────────────────────────────────
    {
        "id": "partner__swiggy",
        "name": "Swiggy",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 1,
        "unit_spend": 250,
        "currency": "INR",
        "business_category": "Food Delivery",
        "network": None,
    },
    {
        "id": "partner__pizzahut",
        "name": "Pizza Hut",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 2,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Dining",
        "network": None,
    },
    {
        "id": "partner__dutyfree",
        "name": "Duty Free",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 5,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Airport Retail",
        "network": None,
    },
    {
        "id": "partner__nobero",
        "name": "Nobero",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 1,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Apparel",
        "network": None,
    },
    {
        "id": "partner__eazydiner",
        "name": "EazyDiner",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 1,
        "unit_spend": 250,
        "currency": "INR",
        "business_category": "Dining",
        "network": None,
    },
    {
        "id": "partner__healthians",
        "name": "Healthians",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 12,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Healthcare",
        "network": None,
    },
    {
        "id": "partner__thepostcardhotel",
        "name": "The Postcard Hotel",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 1,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Hospitality",
        "network": None,
    },
    {
        "id": "partner__commbitz",
        "name": "Commbitz",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 30,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Telecom",
        "network": None,
    },
    {
        "id": "partner__onpoint",
        "name": "OnPoint",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 4,
        "unit_spend": 5,
        "currency": "INR",
        "business_category": "Loyalty Exchange",
        "network": None,
    },
    {
        "id": "partner__atlys",
        "name": "Atlys",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 4,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Visa Services",
        "network": None,
    },
    {
        "id": "partner__airportzo",
        "name": "AirportZo",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 5,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Airport Services",
        "network": None,
    },
    {
        "id": "partner__adani",
        "name": "Adani",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 5,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Retail / Fuel",
        "network": None,
    },
    {
        "id": "partner__iplanet",
        "name": "iPlanet",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 30,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Electronics",
        "network": None,
    },
    {
        "id": "partner__hpcl",
        "name": "HPCL",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 1,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Fuel",
        "network": None,
    },
    {
        "id": "partner__mcdonalds",
        "name": "McDonald's",
        "category": "NON_FINANCIAL",
        "bluchips_per_unit": 2,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Dining",
        "network": None,
    },
    # ── Financial Partners ────────────────────────────────────────────────────
    {
        "id": "partner__idfc",
        "name": "IDFC First Bank",
        "category": "FINANCIAL",
        "bluchips_per_unit": 22,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Banking",
        "network": "Visa",
    },
    {
        "id": "partner__axisbank",
        "name": "Axis Bank",
        "category": "FINANCIAL",
        "bluchips_per_unit": 23,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Banking",
        "network": "Mastercard",
    },
    {
        "id": "partner__kotak",
        "name": "Kotak Mahindra",
        "category": "FINANCIAL",
        "bluchips_per_unit": 21,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Banking",
        "network": "Visa",
    },
    {
        "id": "partner__sbicard",
        "name": "SBI Card",
        "category": "FINANCIAL",
        "bluchips_per_unit": 23,
        "unit_spend": 100,
        "currency": "INR",
        "business_category": "Banking",
        "network": "Mastercard",
    },
]

FARE_TYPES = [
    {"id": "fare__saver",         "name": "SAVER",         "description": "Budget-friendly, restrictive fare with limited flexibility"},
    {"id": "fare__flexi",         "name": "FLEXI",         "description": "Flexible fare allowing free date changes and cancellations"},
    {"id": "fare__corporate",     "name": "CORPORATE",     "description": "Negotiated fare for corporate accounts"},
    {"id": "fare__sme",           "name": "SME",           "description": "Small and medium enterprise discounted fare"},
    {"id": "fare__super6e",       "name": "SUPER6E",       "description": "Premium economy fare with added comfort benefits"},
    {"id": "fare__stretch",       "name": "STRETCH",       "description": "Extra legroom XL seat fare"},
    {"id": "fare__promo",         "name": "PROMO",         "description": "Promotional or limited-time sale fare"},
    {"id": "fare__return_family", "name": "RETURN_FAMILY", "description": "Family return booking fare with group discounts"},
]

AIRPORTS = [
    {"id": "airport__DEL", "code": "DEL", "name": "Indira Gandhi International",         "city": "Delhi",     "city_type": "METRO",         "region": "NORTH"},
    {"id": "airport__BOM", "code": "BOM", "name": "Chhatrapati Shivaji Maharaj Intl",    "city": "Mumbai",    "city_type": "METRO",         "region": "WEST"},
    {"id": "airport__BLR", "code": "BLR", "name": "Kempegowda International",            "city": "Bengaluru", "city_type": "METRO",         "region": "SOUTH"},
    {"id": "airport__HYD", "code": "HYD", "name": "Rajiv Gandhi International",          "city": "Hyderabad", "city_type": "METRO",         "region": "SOUTH"},
    {"id": "airport__MAA", "code": "MAA", "name": "Chennai International",               "city": "Chennai",   "city_type": "METRO",         "region": "SOUTH"},
    {"id": "airport__CCU", "code": "CCU", "name": "Netaji Subhas Chandra Bose Intl",     "city": "Kolkata",   "city_type": "METRO",         "region": "EAST"},
    {"id": "airport__GOI", "code": "GOI", "name": "Goa International (Dabolim)",         "city": "Goa",       "city_type": "LEISURE_BEACH", "region": "WEST"},
    {"id": "airport__JAI", "code": "JAI", "name": "Jaipur International",                "city": "Jaipur",    "city_type": "LEISURE",       "region": "NORTH"},
    {"id": "airport__AMD", "code": "AMD", "name": "Sardar Vallabhbhai Patel Intl",       "city": "Ahmedabad", "city_type": "METRO",         "region": "WEST"},
    {"id": "airport__PNQ", "code": "PNQ", "name": "Pune Airport",                       "city": "Pune",      "city_type": "METRO",         "region": "WEST"},
    {"id": "airport__COK", "code": "COK", "name": "Cochin International",                "city": "Kochi",     "city_type": "LEISURE_BEACH", "region": "SOUTH"},
    {"id": "airport__IXB", "code": "IXB", "name": "Bagdogra Airport",                   "city": "Siliguri",  "city_type": "LEISURE",       "region": "EAST"},
    {"id": "airport__SXR", "code": "SXR", "name": "Sheikh ul Alam International",        "city": "Srinagar",  "city_type": "LEISURE",       "region": "NORTH"},
    {"id": "airport__DXB", "code": "DXB", "name": "Dubai International",                "city": "Dubai",     "city_type": "INTERNATIONAL", "region": "INTL"},
    {"id": "airport__SIN", "code": "SIN", "name": "Singapore Changi",                   "city": "Singapore", "city_type": "INTERNATIONAL", "region": "INTL"},
]

# ── Add-On catalog (reference vertices — shared across all segments) ──────────
ADDONS = [
    {
        "id": "addon__xl_seat",
        "addon_type": "XL_SEAT",
        "name": "XL Seat",
        "category": "SEAT",
        "description": "Extra legroom seat with up to 6 extra inches of legroom",
        "approx_price_inr": 599,
    },
    {
        "id": "addon__window_seat",
        "addon_type": "WINDOW_SEAT",
        "name": "Window Seat",
        "category": "SEAT",
        "description": "Pre-selected window seat",
        "approx_price_inr": 149,
    },
    {
        "id": "addon__aisle_seat",
        "addon_type": "AISLE_SEAT",
        "name": "Aisle Seat",
        "category": "SEAT",
        "description": "Pre-selected aisle seat for easy access",
        "approx_price_inr": 149,
    },
    {
        "id": "addon__middle_seat",
        "addon_type": "MIDDLE_SEAT",
        "name": "Middle Seat",
        "category": "SEAT",
        "description": "Pre-selected middle seat (typically lowest price)",
        "approx_price_inr": 99,
    },
    {
        "id": "addon__excess_baggage_5kg",
        "addon_type": "EXCESS_BAGGAGE_5KG",
        "name": "Excess Baggage 5 kg",
        "category": "BAGGAGE",
        "description": "Pre-booked 5 kg additional baggage allowance",
        "approx_price_inr": 600,
    },
    {
        "id": "addon__excess_baggage_10kg",
        "addon_type": "EXCESS_BAGGAGE_10KG",
        "name": "Excess Baggage 10 kg",
        "category": "BAGGAGE",
        "description": "Pre-booked 10 kg additional baggage allowance",
        "approx_price_inr": 1100,
    },
    {
        "id": "addon__excess_baggage_15kg",
        "addon_type": "EXCESS_BAGGAGE_15KG",
        "name": "Excess Baggage 15 kg",
        "category": "BAGGAGE",
        "description": "Pre-booked 15 kg additional baggage allowance",
        "approx_price_inr": 1500,
    },
    {
        "id": "addon__fast_forward",
        "addon_type": "FAST_FORWARD",
        "name": "Fast Forward",
        "category": "SERVICE",
        "description": "Priority check-in, security, and boarding",
        "approx_price_inr": 249,
    },
    {
        "id": "addon__meal_veg",
        "addon_type": "MEAL_VEG",
        "name": "Veg Meal",
        "category": "MEAL",
        "description": "Pre-booked vegetarian hot meal on board",
        "approx_price_inr": 249,
    },
    {
        "id": "addon__meal_nonveg",
        "addon_type": "MEAL_NON_VEG",
        "name": "Non-Veg Meal",
        "category": "MEAL",
        "description": "Pre-booked non-vegetarian hot meal on board",
        "approx_price_inr": 299,
    },
    {
        "id": "addon__snack_combo",
        "addon_type": "SNACK_COMBO",
        "name": "Snack Combo",
        "category": "MEAL",
        "description": "6E Prime snack box + beverage combo",
        "approx_price_inr": 199,
    },
    {
        "id": "addon__6e_prime",
        "addon_type": "6E_PRIME",
        "name": "6E Prime Bundle",
        "category": "BUNDLE",
        "description": "Bundled: pre-selected seat + snack combo + Fast Forward priority",
        "approx_price_inr": 549,
    },
]

# ── Look-up maps for convenience ──────────────────────────────────────────────
AIRPORT_BY_CODE    = {a["code"]: a     for a in AIRPORTS}
PARTNER_BY_ID      = {p["id"]: p       for p in PARTNERS}
TIER_BY_NAME       = {t["name"]: t     for t in TIERS}    # e.g. TIER_BY_NAME["Blu 1"]
TIER_BY_RANK       = {t["rank"]: t     for t in TIERS}    # e.g. TIER_BY_RANK[3] → Blu 1
FARE_TYPE_BY_NAME  = {f["name"]: f     for f in FARE_TYPES}
ADDON_BY_TYPE      = {a["addon_type"]: a for a in ADDONS}

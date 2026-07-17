"""
Read-only Gremlin traversals for the BluChip 360 Knowledge Graph.
==================================================================

Designed to be called directly by LLM agents as named tools.
Every method returns plain Python dicts/lists — no Gremlin objects leak out.

Schema this query layer assumes:

    Member → BELONGS_TO_TIER   → Tier
    Member → MADE_BOOKING      → Booking → CONTAINS_SEGMENT → Segment
    Segment → FLIES_ON         → Route   → ORIGIN_AIRPORT/DEST_AIRPORT → Airport
    Segment → PURCHASED_ADDON  → AddOn
    Member → EARNS_POINTS_FROM → PartnerTransaction → WITH_PARTNER → Partner
    Member → USES_FARE_TYPE    → FareType
    Member → PREFERS_ROUTE     → Route
"""

import logging
from datetime import datetime, timedelta

from .cosmos_helpers import escape_gremlin, get_cosmos_client, run_gremlin

logger = logging.getLogger(__name__)


class BluChipGraphDB:
    """
    Query interface for the BluChip member 360 knowledge graph.

    All methods return serialisable Python objects (dicts / lists).
    Instantiate once and reuse across agent turns — the Gremlin
    WebSocket client is held open for the lifetime of the object.
    """

    def __init__(self):
        self._client = get_cosmos_client()

    def _q(self, query: str) -> list:
        return run_gremlin(self._client, query)

    # ═══════════════════════════════════════════════════════════════════════════
    # CATALOGUE / OVERVIEW TOOLS
    # ═══════════════════════════════════════════════════════════════════════════

    def schema(self) -> dict:
        """
        Return vertex label counts, all edge types, and total edge count.
        Use this to get a high-level picture of what's in the graph.
        """
        vertex_counts = self._q("g.V().groupCount().by(label())")
        edge_types    = self._q("g.E().label().dedup().fold()")
        edge_count    = self._q("g.E().count()")
        return {
            "vertex_counts": vertex_counts[0] if vertex_counts else {},
            "edge_types":    edge_types[0]    if edge_types    else [],
            "total_edges":   edge_count[0]    if edge_count    else 0,
        }

    def tier_overview(self) -> list:
        """
        All tiers (Blu 3 / Blu 2 / Blu 1) with earn rates, upgrade thresholds,
        and live member counts.  Ordered best-to-entry (rank 3 → 1).
        """
        return self._q(
            "g.V().hasLabel('Tier')"
            ".project('name','rank','upgrade_spend_inr','upgrade_min_flights',"
            "'max_flight_earn_rate','addon_earn_rate','prime_passes',"
            "'addon_discount_pct','benefits','member_count')"
            ".by(values('name'))"
            ".by(values('rank'))"
            ".by(values('upgrade_spend_inr'))"
            ".by(values('upgrade_min_flights'))"
            ".by(values('max_flight_earn_rate'))"
            ".by(values('addon_earn_rate'))"
            ".by(values('prime_passes'))"
            ".by(values('addon_discount_pct'))"
            ".by(values('benefits'))"
            ".by(__.in('BELONGS_TO_TIER').count())"
            ".order().by(select('rank'), desc)"
        )

    def partner_overview(self) -> list:
        """
        All partners with earn rate, category, and number of earn transactions.
        """
        return self._q(
            "g.V().hasLabel('Partner')"
            ".project('name','category','business_category','network',"
            "'bluchips_per_unit','unit_spend','transaction_count')"
            ".by(values('name'))"
            ".by(values('category'))"
            ".by(values('business_category'))"
            ".by(values('network'))"
            ".by(values('bluchips_per_unit'))"
            ".by(values('unit_spend'))"
            ".by(__.in('WITH_PARTNER').count())"
        )

    def addon_catalog(self) -> list:
        """
        All available add-on types with category and approximate price.
        """
        return self._q(
            "g.V().hasLabel('AddOn')"
            ".project('addon_type','name','category','description','approx_price_inr')"
            ".by(values('addon_type'))"
            ".by(values('name'))"
            ".by(values('category'))"
            ".by(values('description'))"
            ".by(values('approx_price_inr'))"
            ".order().by(select('category'))"
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # MEMBER-CENTRIC TOOLS
    # ═══════════════════════════════════════════════════════════════════════════

    def member_profile(self, ffn_id: str) -> dict:
        """
        Full 360 view for one member: demographics, tier, preferred routes,
        fare usage, booking count, and partner transaction count.

        Args:
            ffn_id: e.g. "IB12345678"
        """
        ffn = escape_gremlin(ffn_id)

        member = self._q(
            f"g.V().hasLabel('Member').has('ffn_id', '{ffn}').valueMap(true)"
        )
        tier = self._q(
            f"g.V().hasLabel('Member').has('ffn_id', '{ffn}')"
            f".outE('BELONGS_TO_TIER')"
            f".project('tier_name','bluchips_balance','qualifying_spend',"
            f"'qualifying_flights','tier_since')"
            f".by(__.inV().values('name'))"
            f".by(values('bluchips_balance'))"
            f".by(values('qualifying_spend'))"
            f".by(values('qualifying_flights'))"
            f".by(values('tier_since'))"
        )
        preferred_routes = self._q(
            f"g.V().hasLabel('Member').has('ffn_id', '{ffn}')"
            f".outE('PREFERS_ROUTE')"
            f".project('route_id','trip_count','last_flown','route_type')"
            f".by(__.inV().values('route_id'))"
            f".by(values('trip_count'))"
            f".by(values('last_flown'))"
            f".by(__.inV().values('route_type'))"
            f".order().by(select('trip_count'), desc)"
        )
        fare_usage = self._q(
            f"g.V().hasLabel('Member').has('ffn_id', '{ffn}')"
            f".outE('USES_FARE_TYPE')"
            f".project('fare_name','usage_count','pct_of_bookings')"
            f".by(__.inV().values('name'))"
            f".by(values('usage_count'))"
            f".by(values('pct_of_bookings'))"
            f".order().by(select('usage_count'), desc)"
        )
        booking_count = self._q(
            f"g.V().hasLabel('Member').has('ffn_id', '{ffn}')"
            f".out('MADE_BOOKING').count()"
        )
        txn_count = self._q(
            f"g.V().hasLabel('Member').has('ffn_id', '{ffn}')"
            f".out('EARNS_POINTS_FROM').count()"
        )
        return {
            "member":          member[0]  if member  else {},
            "tier":            tier[0]    if tier    else {},
            "preferred_routes": preferred_routes,
            "fare_usage":      fare_usage,
            "total_bookings":  booking_count[0] if booking_count else 0,
            "total_partner_transactions": txn_count[0] if txn_count else 0,
        }

    def member_bookings(self, ffn_id: str, limit: int = 10) -> list:
        """
        All bookings made by a member — booking date, channel, fare type,
        status, trip type, segment count, and total fare.

        Args:
            ffn_id: member FFN ID
            limit:  maximum number of bookings to return (default 10)
        """
        ffn = escape_gremlin(ffn_id)
        return self._q(
            f"g.V().hasLabel('Member').has('ffn_id', '{ffn}')"
            f".out('MADE_BOOKING')"
            f".project('booking_id','booking_date','booking_channel','fare_type',"
            f"'booking_status','trip_type','is_international','total_fare','segment_count')"
            f".by(values('booking_id'))"
            f".by(values('booking_date'))"
            f".by(values('booking_channel'))"
            f".by(values('fare_type'))"
            f".by(values('booking_status'))"
            f".by(values('trip_type'))"
            f".by(values('is_international'))"
            f".by(values('total_fare'))"
            f".by(__.out('CONTAINS_SEGMENT').count())"
            f".order().by(select('booking_date'), desc)"
            f".limit({limit})"
        )

    def booking_details(self, booking_id: str) -> dict:
        """
        All segments inside a booking, with add-ons purchased per segment.

        Args:
            booking_id: e.g. "BK12345678"
        """
        bid = escape_gremlin(booking_id)
        booking = self._q(
            f"g.V().hasLabel('Booking').has('booking_id', '{bid}').valueMap(true)"
        )
        segments = self._q(
            f"g.V().hasLabel('Booking').has('booking_id', '{bid}')"
            f".out('CONTAINS_SEGMENT')"
            f".project('segment_id','flight_number','flight_date','origin','destination',"
            f"'seat_type','meal_ordered','travel_status','travel_purpose','flight_duration_min',"
            f"'baggage_weight_kg','excess_baggage','traveller_companions','addons')"
            f".by(values('segment_id'))"
            f".by(values('flight_number'))"
            f".by(values('flight_date'))"
            f".by(values('origin'))"
            f".by(values('destination'))"
            f".by(values('seat_type'))"
            f".by(values('meal_ordered'))"
            f".by(values('travel_status'))"
            f".by(values('travel_purpose'))"
            f".by(values('flight_duration_min'))"
            f".by(values('baggage_weight_kg'))"
            f".by(values('excess_baggage'))"
            f".by(values('traveller_companions'))"
            f".by(__.out('PURCHASED_ADDON').values('addon_type').fold())"
            f".order().by(select('flight_date'))"
        )
        return {
            "booking":  booking[0] if booking else {},
            "segments": segments,
        }

    def segment_history(self, ffn_id: str, limit: int = 20) -> list:
        """
        Flat list of all flight segments flown by a member (across all bookings),
        most recent first.  Useful for understanding a member's full travel history.

        Args:
            ffn_id: member FFN ID
            limit:  max segments to return (default 20)
        """
        ffn = escape_gremlin(ffn_id)
        return self._q(
            f"g.V().hasLabel('Member').has('ffn_id', '{ffn}')"
            f".out('MADE_BOOKING')"
            f".out('CONTAINS_SEGMENT')"
            f".project('segment_id','flight_number','flight_date','origin','destination',"
            f"'seat_type','meal_ordered','travel_status','travel_purpose',"
            f"'flight_duration_min','excess_baggage','is_international')"
            f".by(values('segment_id'))"
            f".by(values('flight_number'))"
            f".by(values('flight_date'))"
            f".by(values('origin'))"
            f".by(values('destination'))"
            f".by(values('seat_type'))"
            f".by(values('meal_ordered'))"
            f".by(values('travel_status'))"
            f".by(values('travel_purpose'))"
            f".by(values('flight_duration_min'))"
            f".by(values('excess_baggage'))"
            f".by(values('is_international'))"
            f".order().by(select('flight_date'), desc)"
            f".limit({limit})"
        )

    def member_addons(self, ffn_id: str) -> list:
        """
        All add-ons purchased by a member across every segment.
        Returns add-on type, name, category, and count of how many times purchased.

        Args:
            ffn_id: member FFN ID
        """
        ffn = escape_gremlin(ffn_id)
        return self._q(
            f"g.V().hasLabel('Member').has('ffn_id', '{ffn}')"
            f".out('MADE_BOOKING')"
            f".out('CONTAINS_SEGMENT')"
            f".out('PURCHASED_ADDON')"
            f".groupCount().by('addon_type')"
        )

    def member_partner_transactions(self, ffn_id: str) -> list:
        """
        All BluChip earn transactions for a member, with partner name and category.

        Args:
            ffn_id: member FFN ID
        """
        ffn = escape_gremlin(ffn_id)
        return self._q(
            f"g.V().hasLabel('Member').has('ffn_id', '{ffn}')"
            f".out('EARNS_POINTS_FROM')"
            f".project('transaction_id','activity_date','issue_date','bluchips_earned',"
            f"'spend_amount','product_type','subcategory','network','partner_name','partner_category')"
            f".by(values('transaction_id'))"
            f".by(values('activity_date'))"
            f".by(values('issue_date'))"
            f".by(values('bluchips_earned'))"
            f".by(values('spend_amount'))"
            f".by(values('product_type'))"
            f".by(values('subcategory'))"
            f".by(values('network'))"
            f".by(__.out('WITH_PARTNER').values('name'))"
            f".by(__.out('WITH_PARTNER').values('category'))"
            f".order().by(select('activity_date'), desc)"
        )

    def member_preferred_routes(self, ffn_id: str) -> list:
        """
        Routes a member prefers (PREFERS_ROUTE edges), with trip count and
        last flown date.  Ordered by trip count descending.

        Args:
            ffn_id: member FFN ID
        """
        ffn = escape_gremlin(ffn_id)
        return self._q(
            f"g.V().hasLabel('Member').has('ffn_id', '{ffn}')"
            f".outE('PREFERS_ROUTE')"
            f".project('route_id','origin','destination','route_type','trip_count','last_flown')"
            f".by(__.inV().values('route_id'))"
            f".by(__.inV().values('origin'))"
            f".by(__.inV().values('destination'))"
            f".by(__.inV().values('route_type'))"
            f".by(values('trip_count'))"
            f".by(values('last_flown'))"
            f".order().by(select('trip_count'), desc)"
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # POPULATION / SEGMENT TOOLS
    # ═══════════════════════════════════════════════════════════════════════════

    def tier_members(self, tier_name: str) -> list:
        """
        All members on a given tier.  Pass 'Blu 3', 'Blu 2', or 'Blu 1'.
        Returns FFN ID, bluchips_balance, qualifying_spend, spending_segment,
        customer_type.

        Args:
            tier_name: "Blu 3" | "Blu 2" | "Blu 1"
        """
        tn = escape_gremlin(tier_name)
        return self._q(
            f"g.V().hasLabel('Tier').has('name', '{tn}')"
            f".in('BELONGS_TO_TIER')"
            f".project('ffn_id','full_name','spending_segment','customer_type',"
            f"'flyer_frequency','bluchips_balance','qualifying_spend','qualifying_flights')"
            f".by(values('ffn_id'))"
            f".by(values('full_name'))"
            f".by(values('spending_segment'))"
            f".by(values('customer_type'))"
            f".by(values('flyer_frequency'))"
            f".by(__.outE('BELONGS_TO_TIER').values('bluchips_balance'))"
            f".by(__.outE('BELONGS_TO_TIER').values('qualifying_spend'))"
            f".by(__.outE('BELONGS_TO_TIER').values('qualifying_flights'))"
        )

    def tier_details(self, tier_name: str) -> dict:
        """
        Full details of a single tier (earn rates, thresholds, perks).

        Args:
            tier_name: "Blu 3" | "Blu 2" | "Blu 1"
        """
        result = self._q(
            f"g.V().hasLabel('Tier').has('name', '{escape_gremlin(tier_name)}').valueMap(true)"
        )
        return result[0] if result else {}

    def route_travelers(self, origin: str, destination: str) -> list:
        """
        Members who frequently fly a specific origin → destination route
        (via PREFERS_ROUTE edges).

        Args:
            origin:      IATA code, e.g. "DEL"
            destination: IATA code, e.g. "BOM"
        """
        route_id = f"{origin.upper()}_{destination.upper()}"
        return self._q(
            f"g.V().hasLabel('Route').has('route_id', '{route_id}')"
            f".inE('PREFERS_ROUTE')"
            f".project('ffn_id','full_name','trip_count','last_flown','spending_segment')"
            f".by(__.outV().values('ffn_id'))"
            f".by(__.outV().values('full_name'))"
            f".by(values('trip_count'))"
            f".by(values('last_flown'))"
            f".by(__.outV().values('spending_segment'))"
            f".order().by(select('trip_count'), desc)"
        )

    def popular_routes(self, top_n: int = 10) -> list:
        """
        Routes ranked by number of members who prefer them (PREFERS_ROUTE count).

        Args:
            top_n: how many routes to return (default 10)
        """
        return self._q(
            f"g.V().hasLabel('Route')"
            f".project('route_id','origin','destination','route_type','traveler_count','segment_count')"
            f".by(values('route_id'))"
            f".by(values('origin'))"
            f".by(values('destination'))"
            f".by(values('route_type'))"
            f".by(__.in('PREFERS_ROUTE').count())"
            f".by(__.in('FLIES_ON').count())"
            f".order().by(select('traveler_count'), desc)"
            f".limit({top_n})"
        )

    def partner_transactions(self, partner_name: str) -> list:
        """
        All earn transactions for a specific partner, with member FFN IDs
        and BluChips earned per transaction.

        Args:
            partner_name: e.g. "Axis Bank", "Swiggy"
        """
        pn = escape_gremlin(partner_name)
        return self._q(
            f"g.V().hasLabel('Partner').has('name', '{pn}')"
            f".in('WITH_PARTNER')"
            f".project('transaction_id','activity_date','bluchips_earned',"
            f"'spend_amount','product_type','ffn_id','member_name')"
            f".by(values('transaction_id'))"
            f".by(values('activity_date'))"
            f".by(values('bluchips_earned'))"
            f".by(values('spend_amount'))"
            f".by(values('product_type'))"
            f".by(__.in('EARNS_POINTS_FROM').values('ffn_id'))"
            f".by(__.in('EARNS_POINTS_FROM').values('full_name'))"
            f".order().by(select('activity_date'), desc)"
        )

    def members_by_fare(self, fare_name: str) -> list:
        """
        Members who use a specific fare type (via USES_FARE_TYPE edges).

        Args:
            fare_name: "SAVER" | "FLEXI" | "CORPORATE" | "SME" |
                       "SUPER6E" | "STRETCH" | "PROMO" | "RETURN_FAMILY"
        """
        fn = escape_gremlin(fare_name)
        return self._q(
            f"g.V().hasLabel('FareType').has('name', '{fn}')"
            f".inE('USES_FARE_TYPE')"
            f".project('ffn_id','full_name','usage_count','pct_of_bookings','spending_segment')"
            f".by(__.outV().values('ffn_id'))"
            f".by(__.outV().values('full_name'))"
            f".by(values('usage_count'))"
            f".by(values('pct_of_bookings'))"
            f".by(__.outV().values('spending_segment'))"
            f".order().by(select('usage_count'), desc)"
        )

    def members_by_addon(self, addon_type: str) -> list:
        """
        Members who have purchased a specific add-on type (across any segment).
        Returns member FFN ID and purchase count.

        Args:
            addon_type: e.g. "XL_SEAT", "FAST_FORWARD", "MEAL_VEG"
        """
        at = escape_gremlin(addon_type)
        return self._q(
            f"g.V().hasLabel('AddOn').has('addon_type', '{at}')"
            f".in('PURCHASED_ADDON')"                                 # Segment
            f".in('CONTAINS_SEGMENT')"                                # Booking
            f".in('MADE_BOOKING')"                                    # Member
            f".groupCount().by('ffn_id')"
        )

    def high_value_members(self, segment: str = "HIGH_FREQ_HIGH_SPEND") -> list:
        """
        Members matching a spending segment label.

        Args:
            segment: "HIGH_FREQ_HIGH_SPEND" | "HIGH_FREQ_LOW_SPEND" |
                     "LOW_FREQ_HIGH_SPEND"  | "LOW_FREQ_LOW_SPEND"
        """
        seg = escape_gremlin(segment)
        return self._q(
            f"g.V().hasLabel('Member').has('spending_segment', '{seg}')"
            f".project('ffn_id','full_name','flyer_frequency','customer_type','tier_name','bluchips_balance')"
            f".by(values('ffn_id'))"
            f".by(values('full_name'))"
            f".by(values('flyer_frequency'))"
            f".by(values('customer_type'))"
            f".by(__.outE('BELONGS_TO_TIER').inV().values('name'))"
            f".by(__.outE('BELONGS_TO_TIER').values('bluchips_balance'))"
        )

    def segment_purchase_summary(self, origin: str, destination: str) -> dict:
        """
        For a specific route: count segments flown (via FLIES_ON) and
        a breakdown of add-ons purchased on those segments.
        Useful for understanding what passengers buy on a particular route.

        Args:
            origin:      IATA code, e.g. "DEL"
            destination: IATA code, e.g. "BOM"
        """
        route_id = f"{origin.upper()}_{destination.upper()}"
        segment_count = self._q(
            f"g.V().hasLabel('Route').has('route_id', '{route_id}')"
            f".in('FLIES_ON').count()"
        )
        addon_breakdown = self._q(
            f"g.V().hasLabel('Route').has('route_id', '{route_id}')"
            f".in('FLIES_ON')"
            f".out('PURCHASED_ADDON')"
            f".groupCount().by('addon_type')"
        )
        fare_breakdown = self._q(
            f"g.V().hasLabel('Route').has('route_id', '{route_id}')"
            f".in('FLIES_ON')"
            f".in('CONTAINS_SEGMENT')"
            f".groupCount().by('fare_type')"
        )
        return {
            "route_id":       route_id,
            "segment_count":  segment_count[0] if segment_count else 0,
            "addon_breakdown": addon_breakdown[0] if addon_breakdown else {},
            "fare_breakdown":  fare_breakdown[0]  if fare_breakdown  else {},
        }

    def member_subgraph(self, ffn_id: str, depth: int = 2) -> list:
        """
        All vertices and edges within `depth` hops of a member node.
        Gives an LLM a local neighbourhood view.
        Keep depth ≤ 3 for reasonable Cosmos DB RU cost.

        Args:
            ffn_id: member FFN ID
            depth:  graph traversal depth (default 2, max 3)
        """
        depth = min(depth, 3)
        ffn   = escape_gremlin(ffn_id)
        return self._q(
            f"g.V().hasLabel('Member').has('ffn_id', '{ffn}')"
            f".repeat(__.bothE().otherV().simplePath()).times({depth})"
            f".path().by(valueMap(true)).by(label())"
        )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _days_cutoff(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

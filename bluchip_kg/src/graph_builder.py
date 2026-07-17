"""
BluChip 360 Knowledge Graph Builder — Transactional Schema
===========================================================

Builds a persistent member 360 knowledge graph in Azure Cosmos DB (Gremlin API).

Ontology implemented:
    Member → BELONGS_TO_TIER → Tier
    Member → MADE_BOOKING    → Booking → CONTAINS_SEGMENT → Segment
    Segment → FLIES_ON       → Route   → ORIGIN_AIRPORT/DEST_AIRPORT → Airport
    Segment → PURCHASED_ADDON → AddOn
    Member → EARNS_POINTS_FROM → PartnerTransaction → WITH_PARTNER → Partner
    Member → USES_FARE_TYPE  → FareType
    Member → PREFERS_ROUTE   → Route

Pipeline:
  1. Create static reference vertices  →  Tier, Partner, FareType, Airport, AddOn
  2. Generate synthetic members         →  via synthetic_generator
  3. For each member create:
       Member vertex
       Booking vertices + MADE_BOOKING edges
       Segment vertices + CONTAINS_SEGMENT edges
       Route vertices  + FLIES_ON edges + ORIGIN_AIRPORT/DEST_AIRPORT edges
       AddOn vertices (static) + PURCHASED_ADDON edges
       PartnerTransaction vertices + EARNS_POINTS_FROM + WITH_PARTNER edges
       BELONGS_TO_TIER, USES_FARE_TYPE, PREFERS_ROUTE edges

Usage:
    python -m src.graph_builder                      # 20 synthetic members
    python -m src.graph_builder --members 100        # 100 members
    python -m src.graph_builder --purge              # drop graph first
    python -m src.graph_builder --static-only        # only load reference nodes
    python -m src.graph_builder --skip-static        # skip reference nodes
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from .data.static_data import (
    ADDONS, AIRPORTS, FARE_TYPES, PARTNERS, TIERS,
    TIER_BY_NAME, FARE_TYPE_BY_NAME,
)
from .data.synthetic_generator import generate_members
from .utils.cosmos_helpers import (
    add_edge,
    add_vertex,
    get_cosmos_client,
    make_vertex_id,
    run_gremlin,
    serialize_list,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Purge ─────────────────────────────────────────────────────────────────────

def purge_graph(client) -> None:
    logger.warning("Purging all vertices from the graph…")
    run_gremlin(client, "g.V().drop()")
    logger.info("Graph purged.")


# ── Static reference vertices ─────────────────────────────────────────────────

def load_tiers(client) -> None:
    logger.info("Loading %d Tier vertices…", len(TIERS))
    for tier in TIERS:
        q = add_vertex(
            "Tier", tier["id"],
            name                   = tier["name"],
            rank                   = tier["rank"],
            description            = tier["description"],
            upgrade_spend_inr      = tier["upgrade_spend_inr"]     or 0,
            upgrade_min_flights    = tier["upgrade_min_flights"]    or 0,
            retention_spend_inr    = tier["retention_spend_inr"]    or 0,
            retention_min_flights  = tier["retention_min_flights"]  or 0,
            tier_validity_days     = tier["tier_validity_days"]     or 0,
            base_earn_rate         = tier["base_earn_rate"],
            tier_bonus_rate        = tier["tier_bonus_rate"],
            channel_bonus_rate     = tier["channel_bonus_rate"],
            max_flight_earn_rate   = tier["max_flight_earn_rate"],
            addon_earn_rate        = tier["addon_earn_rate"],
            prime_passes           = tier["prime_passes"],
            addon_discount_pct     = tier["addon_discount_pct"],
            benefits               = serialize_list(tier["benefits"]),
        )
        run_gremlin(client, q, ignore_conflict=True)
        logger.debug("  Tier: %s (rank %d)", tier["name"], tier["rank"])


def load_partners(client) -> None:
    logger.info("Loading %d Partner vertices…", len(PARTNERS))
    for p in PARTNERS:
        q = add_vertex(
            "Partner", p["id"],
            name              = p["name"],
            category          = p["category"],
            bluchips_per_unit = p["bluchips_per_unit"],
            unit_spend        = p["unit_spend"],
            currency          = p["currency"],
            business_category = p["business_category"],
            network           = p["network"] or "",
        )
        run_gremlin(client, q, ignore_conflict=True)
        logger.debug("  Partner: %s (%s)", p["name"], p["category"])


def load_fare_types(client) -> None:
    logger.info("Loading %d FareType vertices…", len(FARE_TYPES))
    for ft in FARE_TYPES:
        q = add_vertex(
            "FareType", ft["id"],
            name        = ft["name"],
            description = ft["description"],
        )
        run_gremlin(client, q, ignore_conflict=True)
        logger.debug("  FareType: %s", ft["name"])


def load_airports(client) -> None:
    logger.info("Loading %d Airport vertices…", len(AIRPORTS))
    for ap in AIRPORTS:
        q = add_vertex(
            "Airport", ap["id"],
            code      = ap["code"],
            name      = ap["name"],
            city      = ap["city"],
            city_type = ap["city_type"],
            region    = ap["region"],
        )
        run_gremlin(client, q, ignore_conflict=True)
        logger.debug("  Airport: %s (%s)", ap["code"], ap["city"])


def load_addons(client) -> None:
    logger.info("Loading %d AddOn vertices…", len(ADDONS))
    for addon in ADDONS:
        q = add_vertex(
            "AddOn", addon["id"],
            addon_type        = addon["addon_type"],
            name              = addon["name"],
            category          = addon["category"],
            description       = addon["description"],
            approx_price_inr  = addon["approx_price_inr"],
        )
        run_gremlin(client, q, ignore_conflict=True)
        logger.debug("  AddOn: %s", addon["name"])


# ── Route vertices (shared, created on-demand) ────────────────────────────────

_seen_routes: set[str] = set()


def ensure_route(client, origin: str, destination: str, route_type: str) -> str:
    """Create a Route vertex (+ airport edges) if it doesn't exist. Return its ID."""
    origin      = origin.upper()
    destination = destination.upper()
    route_id    = f"{origin}_{destination}"
    vid         = make_vertex_id("route", origin, destination)

    if vid not in _seen_routes:
        q = add_vertex(
            "Route", vid,
            route_id    = route_id,
            origin      = origin,
            destination = destination,
            route_type  = route_type,
        )
        run_gremlin(client, q, ignore_conflict=True)
        _seen_routes.add(vid)

        # ORIGIN_AIRPORT / DEST_AIRPORT edges  (Route → Airport)
        origin_ap_id = f"airport__{origin}"
        dest_ap_id   = f"airport__{destination}"
        run_gremlin(client, add_edge("ORIGIN_AIRPORT", vid, origin_ap_id), ignore_conflict=True)
        run_gremlin(client, add_edge("DEST_AIRPORT",   vid, dest_ap_id),   ignore_conflict=True)

    return vid


# ── Member graph (all transactional vertices + edges) ─────────────────────────

def load_member(client, member_data: dict) -> None:
    """Create all vertices and edges for one member in the transactional schema."""
    m   = member_data["member"]
    t   = member_data["tier"]
    ffn = m["ffn_id"]

    member_vid = make_vertex_id("member", ffn)

    # ── 1. Member vertex ──────────────────────────────────────────────────────
    run_gremlin(client, add_vertex("Member", member_vid, **m), ignore_conflict=True)

    # ── 2. BELONGS_TO_TIER edge  (Member → Tier) ─────────────────────────────
    # Use the canonical tier ID from static_data (e.g. "tier__blu1")
    tier_vid = TIER_BY_NAME[t["tier_name"]]["id"]
    run_gremlin(client, add_edge(
        "BELONGS_TO_TIER", member_vid, tier_vid,
        bluchips_balance   = t["bluchips_balance"],
        qualifying_spend   = t["qualifying_spend"],
        qualifying_flights = t["qualifying_flights"],
        tier_since         = t["tier_since"],
    ), ignore_conflict=True)

    # ── 3. USES_FARE_TYPE edges  (Member → FareType) ─────────────────────────
    for fu in member_data["fare_usage"]:
        fare_vid = FARE_TYPE_BY_NAME[fu["fare_name"]]["id"]   # e.g. "fare__saver"
        run_gremlin(client, add_edge(
            "USES_FARE_TYPE", member_vid, fare_vid,
            usage_count      = fu["usage_count"],
            pct_of_bookings  = fu["pct_of_bookings"],
        ), ignore_conflict=True)

    # ── 4. PREFERS_ROUTE edges  (Member → Route) ─────────────────────────────
    for pr in member_data["preferred_routes"]:
        route_vid = ensure_route(client, pr["origin"], pr["destination"], pr["route_type"])
        run_gremlin(client, add_edge(
            "PREFERS_ROUTE", member_vid, route_vid,
            trip_count = pr["trip_count"],
            last_flown = pr["last_flown"],
        ), ignore_conflict=True)

    # ── 5. Bookings, Segments, AddOns ────────────────────────────────────────
    for bk_data in member_data["bookings"]:
        bk         = bk_data["booking"]
        booking_id = bk["booking_id"]
        booking_vid = make_vertex_id("booking", booking_id)

        # Booking vertex
        run_gremlin(client, add_vertex("Booking", booking_vid, **bk), ignore_conflict=True)

        # MADE_BOOKING edge  (Member → Booking)
        run_gremlin(client, add_edge("MADE_BOOKING", member_vid, booking_vid), ignore_conflict=True)

        for seg_data in bk_data["segments"]:
            seg        = seg_data["segment"]
            segment_id = seg["segment_id"]
            seg_vid    = make_vertex_id("segment", segment_id)

            # Segment vertex
            run_gremlin(client, add_vertex("Segment", seg_vid, **seg), ignore_conflict=True)

            # CONTAINS_SEGMENT edge  (Booking → Segment)
            run_gremlin(client, add_edge("CONTAINS_SEGMENT", booking_vid, seg_vid), ignore_conflict=True)

            # Route + FLIES_ON edge  (Segment → Route)
            route_type = "INTERNATIONAL" if seg["is_international"] else "DOMESTIC"
            route_vid  = ensure_route(client, seg["origin"], seg["destination"], route_type)
            run_gremlin(client, add_edge("FLIES_ON", seg_vid, route_vid), ignore_conflict=True)

            # PURCHASED_ADDON edges  (Segment → AddOn)
            for addon_id in seg_data.get("addon_ids", []):
                run_gremlin(client, add_edge("PURCHASED_ADDON", seg_vid, addon_id), ignore_conflict=True)

    # ── 6. PartnerTransaction vertices ───────────────────────────────────────
    for pt_data in member_data["partner_transactions"]:
        pt      = pt_data["transaction"]
        ptxn_id = pt["transaction_id"]
        ptxn_vid = make_vertex_id("ptxn", ptxn_id)

        # PartnerTransaction vertex
        run_gremlin(client, add_vertex("PartnerTransaction", ptxn_vid, **pt), ignore_conflict=True)

        # EARNS_POINTS_FROM edge  (Member → PartnerTransaction)
        run_gremlin(client, add_edge("EARNS_POINTS_FROM", member_vid, ptxn_vid), ignore_conflict=True)

        # WITH_PARTNER edge  (PartnerTransaction → Partner)
        run_gremlin(client, add_edge("WITH_PARTNER", ptxn_vid, pt_data["partner_id"]), ignore_conflict=True)

    total_segs = sum(len(bk["segments"]) for bk in member_data["bookings"])
    logger.info(
        "  ✓ %s  |  Tier: %-5s  |  Bookings: %2d  |  Segments: %2d  |  Transactions: %d",
        ffn, t["tier_name"],
        len(member_data["bookings"]),
        total_segs,
        len(member_data["partner_transactions"]),
    )


# ── Main entry point ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build the BluChip 360 Knowledge Graph in Cosmos DB")
    parser.add_argument("--members",     type=int, default=20,  help="Number of synthetic members (default: 20)")
    parser.add_argument("--seed",        type=int, default=42,  help="Random seed for reproducibility")
    parser.add_argument("--purge",       action="store_true",   help="Drop all vertices before loading")
    parser.add_argument("--static-only", action="store_true",   help="Only load Tier/Partner/FareType/Airport/AddOn vertices")
    parser.add_argument("--skip-static", action="store_true",   help="Skip static reference vertices, load members only")
    args = parser.parse_args()

    logger.info("Connecting to Cosmos DB…")
    client = get_cosmos_client()
    logger.info("Connected.")

    if args.purge:
        purge_graph(client)

    # ── Static reference data ─────────────────────────────────────────────────
    if not args.skip_static:
        load_tiers(client)
        load_partners(client)
        load_fare_types(client)
        load_airports(client)
        load_addons(client)
        logger.info("Static reference data loaded.")

    if args.static_only:
        logger.info("--static-only flag set. Done.")
        return

    # ── Synthetic members ─────────────────────────────────────────────────────
    logger.info("Generating %d synthetic members (seed=%d)…", args.members, args.seed)
    members = generate_members(n=args.members, seed=args.seed)

    logger.info("Loading members into graph…")
    for i, member_data in enumerate(members, 1):
        logger.info("[%d/%d]  %s", i, args.members, member_data["member"]["ffn_id"])
        load_member(client, member_data)

    logger.info("Done. %d members loaded.", args.members)


if __name__ == "__main__":
    main()

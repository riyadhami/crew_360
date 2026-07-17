"""
neo4j_helpers.py — Shared Neo4j connection & query helpers.

All layers import from here for Neo4j access.
"""

import os

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

# Neo4j connection settings (from .env — see .env.example)
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")


def get_neo4j_driver():
    """Create a Neo4j driver for the local instance."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def run_cypher(driver, query: str, parameters: dict = None):
    """Execute a Cypher query and return list of records."""
    with driver.session() as session:
        result = session.run(query, parameters or {})
        return [record.data() for record in result]


def run_cypher_write(driver, query: str, parameters: dict = None):
    """Execute a write Cypher query inside a write transaction."""
    with driver.session() as session:
        session.execute_write(lambda tx: tx.run(query, parameters or {}))

"""
Query Cosmos DB to see what's in the Unified_Knowledge_graph
"""
import os
from dotenv import load_dotenv
from gremlin_python.driver import client, serializer

load_dotenv()

COSMOS_ENDPOINT = os.getenv("COSMOS_DB_ENDPOINT", "")
COSMOS_KEY = os.getenv("COSMOS_DB_KEY", "")
COSMOS_DATABASE = os.getenv("COSMOS_DB_DATABASE", "")
COSMOS_GRAPH = "Unified_Knowledge_graph"

username = f"/dbs/{COSMOS_DATABASE}/colls/{COSMOS_GRAPH}"
url = f"wss://{COSMOS_ENDPOINT}:443/"

gremlin_client = client.Client(
    url=url,
    traversal_source="g",
    username=username,
    password=COSMOS_KEY,
    message_serializer=serializer.GraphSONSerializersV2d0(),
)

print("Querying vertices by database...")
callback = gremlin_client.submitAsync("g.V().group().by('database').by(count())")
result = callback.result()
db_counts = result.all().result()
print(f"Vertices by database: {db_counts}")

print("\nQuerying vertices by label...")
callback = gremlin_client.submitAsync("g.V().group().by(label).by(count())")
result = callback.result()
label_counts = result.all().result()
print(f"Vertices by label: {label_counts}")

print("\nSample IJP vertices (first 5)...")
callback = gremlin_client.submitAsync("g.V().has('database', 'IJP').limit(5).valueMap('name', 'displayName', 'database')")
result = callback.result()
samples = result.all().result()
for i, v in enumerate(samples, 1):
    print(f"  {i}. {v}")

gremlin_client.close()

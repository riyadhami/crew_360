"""
Test Cosmos DB Gremlin connection
"""
import os
from dotenv import load_dotenv
from gremlin_python.driver import client, serializer

load_dotenv()

COSMOS_ENDPOINT = os.getenv("COSMOS_DB_ENDPOINT", "")
COSMOS_KEY = os.getenv("COSMOS_DB_KEY", "")
COSMOS_DATABASE = os.getenv("COSMOS_DB_DATABASE", "")
COSMOS_GRAPH = os.getenv("COSMOS_DB_GRAPH", "Unified_Knowledge_graph")  # Test with default first

print(f"Endpoint: {COSMOS_ENDPOINT}")
print(f"Database: {COSMOS_DATABASE}")
print(f"Graph: {COSMOS_GRAPH}")
print(f"Key: {COSMOS_KEY[:20]}...{COSMOS_KEY[-10:]}")

username = f"/dbs/{COSMOS_DATABASE}/colls/{COSMOS_GRAPH}"
print(f"Username: {username}")

url = f"wss://{COSMOS_ENDPOINT}:443/"
print(f"URL: {url}")

print("\nAttempting to connect...")
try:
    gremlin_client = client.Client(
        url=url,
        traversal_source="g",
        username=username,
        password=COSMOS_KEY,
        message_serializer=serializer.GraphSONSerializersV2d0(),
    )
    
    # Try a simple query
    print("Connection established! Testing with count query...")
    callback = gremlin_client.submitAsync("g.V().count()")
    result = callback.result()
    count = result.all().result()
    print(f"✓ Success! Vertex count: {count}")
    
    gremlin_client.close()
except Exception as e:
    print(f"✗ Connection failed: {e}")
    import traceback
    traceback.print_exc()

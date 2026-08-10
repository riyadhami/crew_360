"""The knowledge graph, shaped for drawing and served to the page.

Everything the Graph Visualiser tab needs lives in this folder: the payload
builder (`payload.py`), the live-Gremlin reader (`cosmos_source.py`), the HTTP
routes (`routes.py`) and the renderer itself (`static/`). The chat UI imports
one component from here and otherwise knows nothing about it.
"""

from crew_perf.graph_visualiser.payload import build_payload

__all__ = ["build_payload"]

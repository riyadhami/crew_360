"""
app.py — Flask web app for the KG inference agent.

Run:
    python -m src.app.app
    python -m src.app.app --graph-backend cosmos
"""

import argparse
import json
import os

from flask import Flask, render_template, request, Response, stream_with_context

from src.agents.inference_agent import run_agent_stream
from src.agents.tree_inference_agent import run_agent_stream as run_advanced_stream
from src.agents.basic_inference_agent import run_agent_stream as run_basic_stream
from src.agents.Data_Retrieval_Agent_New import run_agent_stream as run_data_retrieval_stream

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/basic")
def basic():
    return render_template("basic.html")


@app.route("/advanced")
def advanced():
    return render_template("advanced.html")


@app.route("/data")
def data():
    return render_template("data.html")


@app.route("/query", methods=["POST"])
def query():
    """SSE endpoint — streams ReAct events as the agent works."""
    user_query = request.json.get("query", "").strip()
    if not user_query:
        return {"error": "Empty query"}, 400
    backend = request.json.get("backend") or app.config.get("GRAPH_BACKEND")

    def generate():
        for event in run_agent_stream(user_query, backend=backend):
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/advanced/query", methods=["POST"])
def advanced_query():
    """SSE endpoint — streams three-phase advanced agent events."""
    user_query = request.json.get("query", "").strip()
    if not user_query:
        return {"error": "Empty query"}, 400
    backend = request.json.get("backend") or app.config.get("GRAPH_BACKEND")

    def generate():
        for event in run_advanced_stream(user_query, backend=backend):
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/basic/query", methods=["POST"])
def basic_query():
    """SSE endpoint — streams single-shot basic agent events."""
    user_query = request.json.get("query", "").strip()
    if not user_query:
        return {"error": "Empty query"}, 400
    backend = request.json.get("backend") or app.config.get("GRAPH_BACKEND")

    def generate():
        for event in run_basic_stream(user_query, backend=backend):
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/data/query", methods=["POST"])
def data_query():
    """SSE endpoint — streams data retrieval agent events (graph + data)."""
    user_query = request.json.get("query", "").strip()
    if not user_query:
        return {"error": "Empty query"}, 400
    backend = request.json.get("backend") or app.config.get("GRAPH_BACKEND")

    def generate():
        for event in run_data_retrieval_stream(user_query, backend=backend):
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KG Inference Web UI")
    parser.add_argument("--graph-backend", choices=["neo4j", "cosmos"], default="cosmos",
                        help="Graph DB backend (default: cosmos)")
    parser.add_argument("--port", type=int, default=5000)
    cli_args = parser.parse_args()
    app.config["GRAPH_BACKEND"] = cli_args.graph_backend
    app.run(debug=True, port=cli_args.port)

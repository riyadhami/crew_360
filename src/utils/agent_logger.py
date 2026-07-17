"""
agent_logger.py — Centralized logging utility for all agents.

Provides structured logging for:
- Entity generation (node creation)
- Sub-graph extraction (graph queries)
- Entity unification (concept normalization/merging)

Features:
- Lazy initialization: Log files are only created when agents actually run
- Avoids creating empty log files when modules are just imported
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any


class LazyAgentLogger:
    """
    Lazy logger wrapper that delays file creation until first actual log message.
    
    This prevents creating empty log files when modules are imported but not used
    (e.g., when Flask imports inference agents but no queries are made).
    """
    
    def __init__(self, agent_name: str, log_dir: str = "logs"):
        self.agent_name = agent_name
        self.log_dir = log_dir
        self._logger = None
        self._initialized = False
    
    def _ensure_initialized(self):
        """Initialize the logger on first use."""
        if self._initialized:
            return
        
        # Create logs directory if it doesn't exist
        log_path = Path(self.log_dir)
        log_path.mkdir(exist_ok=True)
        
        # Create logger
        self._logger = logging.getLogger(self.agent_name)
        self._logger.setLevel(logging.DEBUG)
        
        # Avoid duplicate handlers
        if self._logger.handlers:
            self._initialized = True
            return
        
        # Create formatters
        detailed_formatter = logging.Formatter(
            fmt='%(asctime)s | %(name)s | %(levelname)-8s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        console_formatter = logging.Formatter(
            fmt='%(levelname)-8s | %(message)s'
        )
        
        # File handler with timestamp in filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = log_path / f"{self.agent_name}_{timestamp}.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(detailed_formatter)
        
        # Console handler (only INFO and above)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(console_formatter)
        
        # Add handlers
        self._logger.addHandler(file_handler)
        self._logger.addHandler(console_handler)
        
        self._logger.info(f"Logger initialized for {self.agent_name}")
        self._logger.info(f"Log file: {log_file}")
        
        self._initialized = True
    
    def debug(self, msg, *args, **kwargs):
        self._ensure_initialized()
        self._logger.debug(msg, *args, **kwargs)
    
    def info(self, msg, *args, **kwargs):
        self._ensure_initialized()
        self._logger.info(msg, *args, **kwargs)
    
    def warning(self, msg, *args, **kwargs):
        self._ensure_initialized()
        self._logger.warning(msg, *args, **kwargs)
    
    def error(self, msg, *args, **kwargs):
        self._ensure_initialized()
        self._logger.error(msg, *args, **kwargs)
    
    def critical(self, msg, *args, **kwargs):
        self._ensure_initialized()
        self._logger.critical(msg, *args, **kwargs)


def setup_agent_logger(agent_name: str, log_dir: str = "logs") -> LazyAgentLogger:
    """
    Create a lazy logger for an agent that only creates files when actually used.
    
    Args:
        agent_name: Name of the agent (e.g., "advanced_graph_builder", "basic_inference")
        log_dir: Directory to store log files (default: "logs")
    
    Returns:
        LazyAgentLogger instance that behaves like a standard logger
    """
    return LazyAgentLogger(agent_name, log_dir)


def log_entity_generation(logger: logging.Logger, entity_type: str, entity_data: dict[str, Any]):
    """
    Log entity generation with structured data.
    
    Args:
        logger: Logger instance
        entity_type: Type of entity (e.g., "Table", "Concept", "Enriched Node")
        entity_data: Dictionary containing entity attributes
    """
    entity_id = entity_data.get('id', entity_data.get('name', 'unknown'))
    entity_label = entity_data.get('label', entity_data.get('displayName', entity_id))
    
    logger.info(f"{'='*60}")
    logger.info(f"ENTITY GENERATION: {entity_type}")
    logger.info(f"{'='*60}")
    logger.info(f"  ID: {entity_id}")
    logger.info(f"  Label: {entity_label}")
    
    if 'database' in entity_data:
        logger.info(f"  Database: {entity_data['database']}")
    
    if 'description' in entity_data:
        desc = entity_data['description']
        logger.info(f"  Description: {desc[:100]}{'...' if len(desc) > 100 else ''}")
    
    if 'concepts' in entity_data:
        concepts = entity_data['concepts']
        if concepts:
            logger.info(f"  Concepts: {', '.join(concepts[:5])}")
    
    if 'columns' in entity_data:
        columns = entity_data['columns']
        if columns:
            logger.info(f"  Columns ({len(columns)}): {', '.join(columns[:5])}{'...' if len(columns) > 5 else ''}")
    
    if 'edges' in entity_data:
        edges = entity_data['edges']
        if edges:
            logger.info(f"  Edges: {len(edges)}")
            for i, edge in enumerate(edges[:3], 1):
                target = edge.get('target_table', edge.get('target', '?'))
                rel = edge.get('relationship', '?')
                logger.info(f"    {i}. {rel} → {target}")
    
    logger.debug(f"Full entity data: {entity_data}")


def log_subgraph_extraction(logger: logging.Logger, operation: str, params: dict[str, Any], result_summary: dict[str, Any]):
    """
    Log sub-graph extraction operations.
    
    Args:
        logger: Logger instance
        operation: Type of graph operation (e.g., "search", "subgraph", "concept_links")
        params: Parameters used for the query
        result_summary: Summary of results (node count, edge count, etc.)
    """
    logger.info(f"{'─'*60}")
    logger.info(f"SUB-GRAPH EXTRACTION: {operation}")
    logger.info(f"{'─'*60}")
    
    # Log parameters
    for key, value in params.items():
        logger.info(f"  {key}: {value}")
    
    # Log results
    if 'node_count' in result_summary:
        logger.info(f"  → Nodes found: {result_summary['node_count']}")
    
    if 'edge_count' in result_summary:
        logger.info(f"  → Edges found: {result_summary['edge_count']}")
    
    if 'tables' in result_summary:
        tables = result_summary['tables']
        logger.info(f"  → Tables: {len(tables)}")
        for table in tables[:3]:
            logger.info(f"      • {table}")
    
    if 'concepts' in result_summary:
        concepts = result_summary['concepts']
        logger.info(f"  → Concepts: {len(concepts)}")
        for concept in concepts[:3]:
            logger.info(f"      • {concept}")
    
    if 'databases' in result_summary:
        logger.info(f"  → Databases: {', '.join(result_summary['databases'])}")
    
    logger.debug(f"Full result: {result_summary}")


def log_entity_unification(logger: logging.Logger, unification_type: str, before: dict[str, Any], after: dict[str, Any]):
    """
    Log entity unification/normalization operations.
    
    Args:
        logger: Logger instance
        unification_type: Type of unification (e.g., "Concept Merge", "Concept Normalization", "Duplicate Removal")
        before: State before unification
        after: State after unification
    """
    logger.info(f"{'='*60}")
    logger.info(f"ENTITY UNIFICATION: {unification_type}")
    logger.info(f"{'='*60}")
    
    if 'concepts' in before and 'concepts' in after:
        before_concepts = before['concepts']
        after_concepts = after['concepts']
        logger.info(f"  Concepts before: {len(before_concepts)}")
        logger.info(f"  Concepts after: {len(after_concepts)}")
        logger.info(f"  Reduction: {len(before_concepts) - len(after_concepts)}")
        
        # Log merged concepts
        if 'merged' in after:
            logger.info(f"  Merged concepts:")
            for merge in after['merged'][:5]:
                logger.info(f"    • {merge}")
    
    if 'nodes' in before and 'nodes' in after:
        logger.info(f"  Nodes before: {len(before['nodes'])}")
        logger.info(f"  Nodes after: {len(after['nodes'])}")
    
    if 'edges' in before and 'edges' in after:
        logger.info(f"  Edges before: {len(before['edges'])}")
        logger.info(f"  Edges after: {len(after['edges'])}")
    
    if 'changes' in after:
        logger.info(f"  Changes:")
        for change in after['changes'][:5]:
            logger.info(f"    • {change}")
    
    logger.debug(f"Before: {before}")
    logger.debug(f"After: {after}")


def log_query_plan(logger: logging.Logger, original_query: str, plan: dict[str, Any]):
    """
    Log query execution plan from inference agents.
    
    Args:
        logger: Logger instance
        original_query: User's original query
        plan: Generated execution plan
    """
    logger.info(f"{'='*60}")
    logger.info(f"QUERY PLAN GENERATED")
    logger.info(f"{'='*60}")
    logger.info(f"  Original Query: {original_query}")
    
    if 'reformulated_query' in plan:
        logger.info(f"  Reformulated: {plan['reformulated_query']}")
    
    if 'relevant_databases' in plan:
        logger.info(f"  Databases: {', '.join(plan['relevant_databases'])}")
    
    if 'relevant_tables' in plan:
        tables = plan['relevant_tables']
        logger.info(f"  Tables ({len(tables)}):")
        for table in tables[:10]:
            db = table.get('database', '?')
            name = table.get('table', table.get('name', '?'))
            logger.info(f"    • [{db}] {name}")
    
    if 'query_plan' in plan:
        steps = plan['query_plan']
        logger.info(f"  Execution Steps ({len(steps)}):")
        for i, step in enumerate(steps, 1):
            logger.info(f"    {i}. {step}")
    
    logger.debug(f"Full plan: {plan}")


def log_tool_execution(logger: logging.Logger, tool_name: str, args: dict[str, Any], result: Any):
    """
    Log tool execution during agent reasoning.
    
    Args:
        logger: Logger instance
        tool_name: Name of the tool being executed
        args: Tool arguments
        result: Tool execution result
    """
    logger.debug(f"Tool Execution: {tool_name}")
    logger.debug(f"  Args: {args}")
    
    if isinstance(result, dict):
        if 'error' in result:
            logger.warning(f"  Error: {result['error']}")
        elif 'nodes' in result or 'edges' in result:
            node_count = len(result.get('nodes', []))
            edge_count = len(result.get('edges', []))
            logger.debug(f"  Result: {node_count} nodes, {edge_count} edges")
        else:
            logger.debug(f"  Result keys: {list(result.keys())}")
    elif isinstance(result, list):
        logger.debug(f"  Result: {len(result)} items")
    else:
        logger.debug(f"  Result: {str(result)[:200]}")

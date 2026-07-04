"""Directed acyclic graph utilities for parallel step scheduling."""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

from models import StepDefinition


class ExecutionDAG:
    """Directed Acyclic Graph for step dependency management."""

    def __init__(self):
        self.nodes: Dict[str, StepDefinition] = {}
        self.edges: Dict[str, Set[str]] = {}
        self.reverse_edges: Dict[str, Set[str]] = {}

    @classmethod
    def from_steps(cls, steps: List[StepDefinition]) -> "ExecutionDAG":
        """Build DAG from list of StepDefinitions with dependency fields."""
        dag = cls()

        for step in steps:
            step_id = str(step.step_id or "").strip()
            if not step_id:
                raise ValueError("Step is missing step_id")
            if step_id in dag.nodes:
                raise ValueError(f"Duplicate step_id detected: {step_id}")

            dag.nodes[step_id] = step
            dag.edges[step_id] = set()
            dag.reverse_edges[step_id] = set()

        for step in steps:
            step_id = step.step_id
            dependencies = {str(dep).strip() for dep in step.dependencies if str(dep).strip()}
            dag.edges[step_id] = dependencies
            for dependency in dependencies:
                dag.reverse_edges.setdefault(dependency, set()).add(step_id)

        for step_id in dag.nodes:
            dag.reverse_edges.setdefault(step_id, set())

        return dag

    def validate(self) -> Tuple[bool, Optional[str]]:
        """Validate DAG: check for cycles, missing deps, orphan nodes.
        Returns (is_valid, error_message)."""
        if not self.nodes:
            return False, "Execution DAG has no nodes"

        for step_id, dependencies in self.edges.items():
            for dependency in dependencies:
                if dependency not in self.nodes:
                    return False, f"Missing dependency: {step_id} depends on unknown step {dependency}"
                if dependency == step_id:
                    return False, f"Self dependency detected: {step_id} depends on itself"

        # Orphan detection: In a DAG with mixed dependency relationships, a node
        # that has NO outgoing edges AND NO incoming edges is suspicious.  However,
        # if ALL nodes are independent (no edges at all), that is valid Level 0
        # parallelism and should not be rejected.
        all_edges_empty = all(
            not self.edges.get(sid) and not self.reverse_edges.get(sid)
            for sid in self.nodes
        )
        if not all_edges_empty:
            isolated_nodes = [
                step_id
                for step_id in self.nodes
                if not self.edges.get(step_id) and not self.reverse_edges.get(step_id)
            ]
            if isolated_nodes:
                isolated_text = ", ".join(sorted(isolated_nodes))
                return False, f"Orphan/isolated step(s) detected: {isolated_text}"

        try:
            ordered = self.topological_sort()
        except ValueError as exc:
            return False, str(exc)

        if len(ordered) != len(self.nodes):
            return False, "Cycle detected while validating execution DAG"

        return True, None

    def topological_sort(self) -> List[str]:
        """Return step_ids in valid execution order (Kahn's algorithm)."""
        indegree: Dict[str, int] = {
            step_id: len(self.edges.get(step_id, set()))
            for step_id in self.nodes
        }

        queue = deque(sorted(step_id for step_id, degree in indegree.items() if degree == 0))
        ordered: List[str] = []

        while queue:
            current = queue.popleft()
            ordered.append(current)

            for dependent in sorted(self.reverse_edges.get(current, set())):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)

        if len(ordered) != len(self.nodes):
            remaining = sorted(step_id for step_id in self.nodes if step_id not in set(ordered))
            raise ValueError(f"Cycle detected in DAG. Unresolved nodes: {remaining}")

        return ordered

    def get_execution_levels(self) -> List[List[str]]:
        """Group steps into parallel execution levels.
        Level N contains all steps whose dependencies are all in levels 0..N-1.
        Steps in the same level can execute concurrently.

        Algorithm:
        1. Start with nodes that have no dependencies -> Level 0
        2. Remove Level 0 nodes from graph
        3. Find new nodes with no remaining dependencies -> Level 1
        4. Repeat until all nodes assigned

        Returns: [[step_1, step_2], [step_3], [step_4, step_5, step_6]]
        """
        levels: List[List[str]] = []
        assigned: Set[str] = set()

        while len(assigned) < len(self.nodes):
            ready = sorted(
                step_id
                for step_id in self.nodes
                if step_id not in assigned and self.edges.get(step_id, set()).issubset(assigned)
            )

            if not ready:
                remaining = sorted(step_id for step_id in self.nodes if step_id not in assigned)
                raise ValueError(f"Deadlock detected while building execution levels. Remaining nodes: {remaining}")

            levels.append(ready)
            assigned.update(ready)

        return levels

    def get_ready_steps(self, completed: Set[str]) -> List[str]:
        """Given set of completed step_ids, return steps whose
        ALL dependencies are in the completed set."""
        completed_ids = {str(step_id).strip() for step_id in completed if str(step_id).strip()}
        ready_steps = [
            step_id
            for step_id in self.nodes
            if step_id not in completed_ids and self.edges.get(step_id, set()).issubset(completed_ids)
        ]
        return sorted(ready_steps)

    def get_dependency_depth(self, step_id: str) -> int:
        """Longest path from any root to this step (for timeline visualization)."""
        if step_id not in self.nodes:
            raise KeyError(f"Unknown step_id: {step_id}")

        memo: Dict[str, int] = {}
        visiting: Set[str] = set()

        def _depth(current_step_id: str) -> int:
            if current_step_id in memo:
                return memo[current_step_id]
            if current_step_id in visiting:
                raise ValueError(f"Cycle detected while computing dependency depth for {current_step_id}")

            visiting.add(current_step_id)
            dependencies = self.edges.get(current_step_id, set())
            if not dependencies:
                memo[current_step_id] = 0
            else:
                memo[current_step_id] = 1 + max(_depth(dep) for dep in dependencies)
            visiting.remove(current_step_id)
            return memo[current_step_id]

        return _depth(step_id)

    def to_dict(self) -> dict:
        """Serialize DAG for storage in AgentState."""
        return {
            "nodes": [step.model_dump() for step in self.nodes.values()],
            "edges": {
                step_id: sorted(list(dependencies))
                for step_id, dependencies in self.edges.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionDAG":
        """Deserialize DAG from AgentState."""
        if not isinstance(data, dict):
            raise ValueError("ExecutionDAG.from_dict expects a dictionary payload")

        raw_nodes = data.get("nodes", [])
        if not isinstance(raw_nodes, list):
            raise ValueError("ExecutionDAG serialized payload must include a list under 'nodes'")

        steps: List[StepDefinition] = []
        for raw_step in raw_nodes:
            if isinstance(raw_step, StepDefinition):
                steps.append(raw_step)
            elif isinstance(raw_step, dict):
                steps.append(StepDefinition.model_validate(raw_step))
            else:
                raise ValueError("ExecutionDAG node entries must be StepDefinition or dict")

        dag = cls.from_steps(steps)
        raw_edges = data.get("edges", {})

        if isinstance(raw_edges, dict) and raw_edges:
            normalized_edges: Dict[str, Set[str]] = {step_id: set() for step_id in dag.nodes}
            for step_id, dependencies in raw_edges.items():
                current_step_id = str(step_id).strip()
                if current_step_id not in dag.nodes:
                    continue

                if isinstance(dependencies, list):
                    dep_values = dependencies
                elif isinstance(dependencies, set):
                    dep_values = list(dependencies)
                else:
                    dep_values = []

                normalized = {
                    str(dep).strip()
                    for dep in dep_values
                    if str(dep).strip() and str(dep).strip() in dag.nodes and str(dep).strip() != current_step_id
                }
                normalized_edges[current_step_id] = normalized

            dag.edges = normalized_edges
            dag.reverse_edges = {step_id: set() for step_id in dag.nodes}
            for step_id, dependencies in dag.edges.items():
                for dependency in dependencies:
                    dag.reverse_edges.setdefault(dependency, set()).add(step_id)

        return dag

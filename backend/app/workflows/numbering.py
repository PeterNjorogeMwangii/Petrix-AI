"""Stable, agent-scoped display numbers; UUIDs remain workflow identities."""
from fastapi import HTTPException


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node["children"])
        for path in node.get("paths", []):
            yield from walk(path["children"])


def assign_numbers(nodes, previous_nodes, next_number):
    """Called inside the workflow row lock, after tree validation.

    Client numbers never allocate or change a number. The persisted counter
    retains its high-water mark when steps (including the last one) are deleted.
    """
    previous = {node["id"]: node["number"] for node in walk(previous_nodes)}
    for node in walk(nodes):
        if node["id"] in previous:
            node["number"] = previous[node["id"]]
        else:
            if next_number >= 2147483647:
                raise HTTPException(409, "The workflow step number limit has been reached.")
            node["number"] = next_number
            next_number += 1
    return next_number

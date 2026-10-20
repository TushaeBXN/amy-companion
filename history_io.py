"""Saving chat history as JSON.

Ollama's replies carry ``ToolCall`` objects (pydantic models). Putting one in
a plain dict and calling ``json.dump`` fails with "Object of type ToolCall is
not JSON serializable", which made every save fail and left the history file
stuck on old messages. :func:`json_default` turns such objects into plain data.
"""


def json_default(obj):
    """``json.dump(..., default=json_default)``: pydantic models become dicts, anything else text."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return str(obj)

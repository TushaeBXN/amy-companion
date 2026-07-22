"""Tool definitions + dispatcher for Amy.

The model REQUESTS these actions; this module decides whether/how to execute
them. Physical side effects (BLE hardware) are whitelisted here, never driven
straight from raw model output.
"""
import web

# --- Tool schemas handed to the model (Ollama / OpenAI-compatible format) ---
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "avatar_expression",
            "description": "Set Ava's facial expression and eye state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mood": {"type": "string",
                             "enum": ["happy", "sad", "neutral", "curious",
                                      "sleepy", "surprised", "amused"]},
                    "eyes": {"type": "string",
                             "enum": ["open", "closed", "staring"]},
                },
                "required": ["mood"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "avatar_gaze",
            "description": "Direct Ava's gaze toward a target.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string",
                               "enum": ["user", "away", "down", "around"]},
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hardware_neck",
            "description": "Lock or unlock the physical neck motor (BLE).",
            "parameters": {
                "type": "object",
                "properties": {"lock": {"type": "boolean"}},
                "required": ["lock"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": "Store a durable fact about the person for later.",
            "parameters": {
                "type": "object",
                "properties": {"fact": {"type": "string"}},
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the internet for current information, news, or anything you're curious about.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "What to search for"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_article",
            "description": "Read the full text of a web page. Use after web_search to read an interesting result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string",
                            "description": "The URL to read"},
                },
                "required": ["url"],
            },
        },
    },
]

# Actions allowed to touch real hardware. Everything else is display-only.
_HARDWARE_WHITELIST = {"hardware_neck"}


def dispatch(name, args, memory=None):
    """Execute one tool call. Returns a short result string (or None).

    Replace the print() stubs with real drivers:
      - avatar_* -> your Unity/VRM animation bridge
      - hardware_* -> your BLE controller (the RealDoll-style neck/motor layer)
    """
    if name == "avatar_expression":
        print(f"  [avatar] mood={args.get('mood')} eyes={args.get('eyes','-')}")
        return None
    if name == "avatar_gaze":
        print(f"  [avatar] gaze -> {args.get('target')}")
        return None
    if name == "hardware_neck":
        if name not in _HARDWARE_WHITELIST:
            return "blocked"
        print(f"  [BLE] neck lock={args.get('lock')}   # TODO: real BLE write")
        return "ok"
    if name == "remember_fact":
        fact = args.get("fact", "").strip()
        if fact and memory is not None:
            memory.add("fact", fact)
            print(f"  [memory] remembered: {fact}")
        return "stored"
    if name == "web_search":
        query = args.get("query", "").strip()
        if not query:
            return "no query given"
        print(f"  [web] searching: {query}")
        try:
            results = web.search(query)
            return "\n".join(
                f"- {r['title']}: {r['snippet']} ({r['url']})"
                for r in results
            ) or "no results"
        except Exception as e:
            return f"search failed: {e}"
    if name == "read_article":
        url = args.get("url", "").strip()
        if not url:
            return "no url given"
        print(f"  [web] reading: {url}")
        try:
            return web.read_url(url)
        except Exception as e:
            return f"could not read page: {e}"
    print(f"  [tools] unknown tool: {name}")
    return None

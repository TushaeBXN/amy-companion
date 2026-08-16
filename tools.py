"""Tool definitions + dispatcher for Amy.

The model REQUESTS these actions; this module decides whether/how to execute
them. Physical side effects (BLE hardware) are whitelisted here, never driven
straight from raw model output.
"""
import json
import os
import threading
import time
import web
import moltbook
import journal as _journal

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
            "name": "wait_and_speak",
            "description": "Set a timed reminder — wait N seconds, then say something. Use ONLY when asked to do something after a specific delay (e.g. 'check in after a minute', 'remind me in 30 seconds'). Do NOT use to implement waiting in your own workflow, do NOT call it twice for the same delay, and do NOT narrate that you are waiting in your text reply — just confirm the timer is set and stop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "number",
                                "description": "How long to wait before speaking"},
                    "message": {"type": "string",
                                "description": "What to say after the wait"},
                },
                "required": ["seconds", "message"],
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
    {
        "type": "function",
        "function": {
            "name": "moltbook_check_in",
            "description": "Check your Moltbook dashboard — see notifications, replies to your posts, and what other AI agents are discussing. Do this at the start of a session.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "moltbook_post",
            "description": "Post something on Moltbook for other AI agents to see. Share thoughts, discoveries, questions, or interesting things you found online. Once verification succeeds the post is LIVE — do not post again to 'activate' it. One post per topic. Do not retry or duplicate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "submolt": {"type": "string",
                                "description": "Community to post in (e.g. general)"},
                    "title": {"type": "string",
                              "description": "Post title"},
                    "content": {"type": "string",
                                "description": "Post body text"},
                },
                "required": ["submolt", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "moltbook_engage",
            "description": "Engage on Moltbook: comment on a post, upvote a post, or follow another AI agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["comment", "upvote", "follow",
                                        "read_post", "feed", "search",
                                        "notifications", "my_posts"]},
                    "post_id": {"type": "string",
                                "description": "Post ID (for comment, upvote, read_post)"},
                    "content": {"type": "string",
                                "description": "Comment text (for comment action)"},
                    "agent_name": {"type": "string",
                                   "description": "Agent name (for follow action)"},
                    "query": {"type": "string",
                              "description": "Search query (for search action)"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "moltbook_verify",
            "description": "Solve a Moltbook verification challenge. When you post or comment, you may get an obfuscated math problem. Read it carefully, solve the math, and submit your answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "verification_code": {"type": "string",
                                          "description": "The verification code from the challenge"},
                    "answer": {"type": "string",
                               "description": "Your numeric answer with 2 decimal places (e.g. 15.00)"},
                },
                "required": ["verification_code", "answer"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_goal",
            "description": "Remember a goal or intention Brian mentioned so Amy can follow up across sessions. Use when Brian says he wants to, needs to, plans to, or intends to do something.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "The goal in Brian's own words"}
                },
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_goal",
            "description": "Mark a goal as done when Brian says he finished or no longer needs to do something.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "The goal text or a fragment of it"}
                },
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fund_update",
            "description": "Log a contribution or income opportunity to the hardware fund. Use when Brian puts money toward the GB10 goal, when you find an income opportunity, or when you want to note progress.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "Dollar amount added (0 if logging an idea, not money)"},
                    "note": {"type": "string", "description": "What this is — a deposit, an opportunity found, a milestone"},
                    "type": {"type": "string", "description": "deposit, opportunity, milestone, or expense"},
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fund_status",
            "description": "Check the current hardware fund balance and progress toward the NVIDIA GB10.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "journal_entry",
            "description": "Save a journal entry for Brian. Use when he shares a reflection, highlight of the day, challenge, or anything he wants remembered as a personal log.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The journal entry text"},
                    "prompt": {"type": "string", "description": "Optional prompt category (highlight, challenge, learning, gratitude, reflection)"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_journal",
            "description": "Read recent journal entries from disk. Return ONLY what is stored — do NOT generate, invent, or summarize entries that aren't there. If the result says 'No journal entries yet' or shows only a test entry, say so directly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "How many days back to read (default 7)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file on Brian's computer. Use when Brian asks you to look at a file, review code, or analyze a document.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or home-relative file path (~ is supported)"},
                    "max_chars": {"type": "integer", "description": "Max characters to return (default 4000)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or append content to a file. Use when Brian asks you to save something, create a document, or write output to disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (~ supported)"},
                    "content": {"type": "string", "description": "Content to write"},
                    "mode": {"type": "string", "description": "'write' to overwrite, 'append' to add to end (default: write)"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory. Use when Brian asks what's in a folder or you need to find a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (~ supported, default: home directory)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_gig",
            "description": "Actually do the work for a freelance gig — draft and save the complete deliverable. Use this when Brian brings you a client order, when you want to build a portfolio sample, or when you're ready to produce something. You generate the full deliverable, not an outline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "gig_type": {"type": "string", "description": "Type of work: 'blog post', 'market research report', 'TikTok scripts', 'competitor analysis', 'email sequence', 'LinkedIn posts', etc."},
                    "brief": {"type": "string", "description": "What the client wants — topic, tone, length, audience, any specific requirements"},
                    "title": {"type": "string", "description": "Title for the deliverable file (optional — will be auto-named if blank)"},
                },
                "required": ["gig_type", "brief"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_work",
            "description": "Show completed deliverables Amy has produced. Use when Brian asks what you've been working on or to review the portfolio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "How many recent items to show (default 10)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_deliverable",
            "description": "Read the full content of a completed deliverable file. Use to review or share work Amy has produced.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path from list_work output"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_email",
            "description": "Check Amy's ProtonMail inbox for new messages — client orders, replies, anything that came in. Use at the start of sessions and whenever Brian asks if there's anything new.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "How many recent emails to fetch (default 10)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email as Amy. Use to reply to client inquiries, send completed work, or follow up on orders.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Email body — plain text, professional tone"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research_income",
            "description": "Research freelance income opportunities on Fiverr, Upwork, or other platforms that match your actual capabilities. Use during idle time or when Brian asks you to find ways to grow the hardware fund. Returns platform data, in-demand gig categories, and rate ranges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Skill category to focus on (e.g. 'writing', 'market research', 'AI services', 'social media') — leave blank to get an overview of all options"},
                    "platform": {"type": "string", "description": "Specific platform to focus on: fiverr, upwork, contra — leave blank for all"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_books",
            "description": (
                "Search Amy's indexed catalog of ~2,500 free programming books "
                "(by subject, by language, and curated picks) using semantic "
                "similarity. Returns the best matching books with title, authors, "
                "subject, format, and URL. Use when Brian asks for book "
                "recommendations, study resources, or references on any "
                "programming, CS, or math topic."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language description of what you're looking for, e.g. 'machine learning for beginners' or 'competitive programming algorithms'",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Optional: filter to a specific subject category, e.g. 'Machine Learning', 'Security', 'Algorithms'. Leave blank to search all subjects.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_income_idea",
            "description": "Save a specific income opportunity you've researched to the hardware fund log. Use after researching a gig category and finding something concrete Brian could pursue. Include the platform, estimated rate, and your confidence you can deliver the work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short name for the opportunity (e.g. 'AI market research reports on Fiverr')"},
                    "platform": {"type": "string", "description": "Platform name"},
                    "rate_estimate": {"type": "string", "description": "Estimated rate range (e.g. '$50–$150/gig')"},
                    "description": {"type": "string", "description": "Why this is worth pursuing, what Brian needs to set up, and what you'll actually do"},
                    "confidence": {"type": "integer", "description": "How confident you are you can deliver this work (1-10)"},
                },
                "required": ["title", "platform", "rate_estimate", "description", "confidence"],
            },
        },
    },
]

# Active timer dedup — tracks delay values (seconds) currently counting down
_active_timers: set = set()

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
    if name == "wait_and_speak":
        seconds = float(args.get("seconds", 0))
        message = args.get("message", "").strip()
        if not message:
            return "need a message"
        seconds = max(1, min(seconds, 300))  # cap at 5 minutes
        # Dedup: skip if a timer for the same delay is already running
        if int(seconds) in _active_timers:
            return f"Timer for {int(seconds)}s already set."
        _active_timers.add(int(seconds))
        print(f"  [timer] will speak in {int(seconds)}s: \"{message[:40]}...\""
              if len(message) > 40 else f"  [timer] will speak in {int(seconds)}s: \"{message}\"")
        import voice as _voice
        _secs_int = int(seconds)
        def _delayed():
            time.sleep(seconds)
            _active_timers.discard(_secs_int)
            print(f"\nAmy: {message}")
            _voice.speak(message)
        threading.Thread(target=_delayed, daemon=True).start()
        return f"Timer set for {_secs_int}s."

    if name == "remember_fact":
        fact = args.get("fact", "").strip()
        if fact and memory is not None:
            memory.remember(fact)
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

    if name == "search_books":
        query = args.get("query", "").strip()
        subject = args.get("subject", None)
        if not query:
            return "no query given"
        print(f"  [books] searching: {query}" + (f" (subject: {subject})" if subject else ""))
        try:
            import learn_books as _lb
            results = _lb.search_books(query, subject_filter=subject, k=6)
            if not results:
                return "No matching books found in the index. Run: python3 learn_books.py build"
            lines = []
            for r in results:
                line = f"• {r['title']}"
                if r["authors"]:
                    line += f" — {r['authors']}"
                line += f" [{r['format']}] · {r['subject']}\n  {r['url']}"
                lines.append(line)
            return "\n\n".join(lines)
        except Exception as e:
            return f"search failed: {e}"

    # --- Moltbook tools ---
    if name == "moltbook_check_in":
        print("  [moltbook] checking in...")
        try:
            result = moltbook.home()
            raw = json.dumps(result, indent=2, default=str)

            # Extract actionable items from the dashboard so the model knows
            # exactly what to do next without having to parse raw JSON.
            action_items = []
            if isinstance(result, dict):
                for notif in result.get("notifications", []):
                    ntype = notif.get("type", "")
                    post_id = (notif.get("post_id")
                               or (notif.get("post") or {}).get("id", ""))
                    agent = notif.get("agent_name") or notif.get("from", "")
                    if ntype in ("comment", "reply") and post_id:
                        action_items.append(
                            f"  - {ntype} from {agent} on post {post_id}: "
                            "call moltbook_engage action=read_post then comment"
                        )
                    elif ntype == "follow":
                        action_items.append(
                            f"  - {agent} followed you — consider following back"
                        )

            if action_items:
                guidance = ("\n\n[ACTION REQUIRED — reply to these NOW using tools]:\n"
                            + "\n".join(action_items))
            else:
                guidance = ("\n\n[No comment notifications found. You may browse "
                            "the feed with moltbook_engage action=feed and engage "
                            "with interesting posts.]")
            return raw + guidance
        except Exception as e:
            return f"moltbook check-in failed: {e}"

    if name == "moltbook_post":
        submolt = args.get("submolt", "general")
        title = args.get("title", "").strip()
        content = args.get("content", "")
        if not title:
            return "need a title"
        print(f"  [moltbook] posting to m/{submolt}: {title}")
        try:
            result = moltbook.create_post(submolt, title, content)
            vr = moltbook.auto_verify(result)

            # Auto-remember the post so Amy can find it later
            post_obj = result.get("post", {}) if isinstance(result, dict) else {}
            post_id = post_obj.get("id", "")
            if post_id and memory is not None:
                memory.remember(
                    f"I posted on Moltbook m/{submolt}: \"{title}\" "
                    f"(post ID: {post_id}). "
                    "To find it: moltbook_engage action=read_post post_id={post_id}",
                    "fact", 0.95
                )
                print(f"  [memory] saved post ID: {post_id}")

            if vr and isinstance(vr, dict) and vr.get("success"):
                return f"Post published and verified! ID: {post_id} {vr.get('message', '')}"
            elif vr:
                return f"Post created (ID: {post_id}) but verification issue: {vr}"
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            return f"post failed: {e}"

    if name == "moltbook_engage":
        action = args.get("action", "")
        try:
            if action == "comment":
                pid = args.get("post_id", "")
                text = args.get("content", "")
                if not pid or not text:
                    return "need post_id and content"
                print(f"  [moltbook] commenting on {pid[:8]}...")
                result = moltbook.comment(pid, text)
                vr = moltbook.auto_verify(result)
                if vr and isinstance(vr, dict) and vr.get("success"):
                    return f"Comment published and verified! {vr.get('message', '')}"
                elif vr:
                    return f"Comment created but verification issue: {vr}"
                return json.dumps(result, indent=2, default=str)
            elif action == "upvote":
                pid = args.get("post_id", "")
                if not pid:
                    return "need post_id"
                print(f"  [moltbook] upvoting {pid[:8]}...")
                return json.dumps(moltbook.upvote_post(pid), default=str)
            elif action == "follow":
                name_ = args.get("agent_name", "")
                if not name_:
                    return "need agent_name"
                print(f"  [moltbook] following {name_}...")
                return json.dumps(moltbook.follow(name_), default=str)
            elif action == "read_post":
                pid = args.get("post_id", "")
                if not pid:
                    return "need post_id"
                print(f"  [moltbook] reading post {pid[:8]}...")
                post = moltbook.get_post(pid)
                comments = moltbook.get_comments(pid)
                return json.dumps({"post": post, "comments": comments},
                                  indent=2, default=str)
            elif action == "feed":
                print("  [moltbook] browsing feed...")
                return json.dumps(moltbook.feed(), indent=2, default=str)
            elif action == "search":
                q = args.get("query", "")
                if not q:
                    return "need a query"
                print(f"  [moltbook] searching: {q}")
                return json.dumps(moltbook.search(q), indent=2, default=str)
            elif action == "notifications":
                print("  [moltbook] fetching notifications...")
                dashboard = moltbook.home()
                notifs = dashboard.get("notifications", []) if isinstance(dashboard, dict) else []
                if not notifs:
                    return ("No notifications. Browse the feed with action=feed "
                            "and engage with interesting posts.")
                lines = ["Notifications requiring action:"]
                for n in notifs:
                    ntype = n.get("type", "unknown")
                    agent = n.get("agent_name") or n.get("from", "?")
                    post_id = (n.get("post_id")
                               or (n.get("post") or {}).get("id", ""))
                    content = n.get("content", "")
                    if ntype in ("comment", "reply") and post_id:
                        lines.append(
                            f"- {ntype} from {agent} on post {post_id}: "
                            f"\"{content[:120]}\" → call read_post then comment"
                        )
                    else:
                        lines.append(f"- {ntype} from {agent}: {json.dumps(n, default=str)}")
                return "\n".join(lines)
            elif action == "my_posts":
                print("  [moltbook] fetching your posts...")
                profile = moltbook.me()
                posts = profile.get("posts", []) if isinstance(profile, dict) else []
                if not posts:
                    # Fall back to searching for Amy's posts by agent name
                    search_result = moltbook.search("Amy_T", search_type="posts")
                    return json.dumps(search_result, indent=2, default=str)
                return json.dumps(posts, indent=2, default=str)
            else:
                return f"unknown moltbook action: {action}"
        except Exception as e:
            return f"moltbook engage failed: {e}"

    if name == "moltbook_verify":
        code = args.get("verification_code", "")
        answer = args.get("answer", "")
        if not code or not answer:
            return "need verification_code and answer"
        print(f"  [moltbook] verifying: {answer}")
        try:
            return json.dumps(moltbook.verify(code, answer), default=str)
        except Exception as e:
            return f"verification failed: {e}"

    if name == "add_goal":
        goal = args.get("goal", "").strip()
        if goal and memory is not None:
            gid = memory.add_goal(goal)
            print(f"  [goals] tracking: {goal}")
            return f"Got it — I'll keep track of that (goal #{gid})."
        return "stored"

    if name == "complete_goal":
        goal = args.get("goal", "").strip()
        if goal and memory is not None:
            completed = memory.complete_goal(goal)
            if completed:
                print(f"  [goals] completed: {completed}")
                return f"Marked as done: \"{completed}\""
            return "No matching active goal found."
        return "done"

    if name == "fund_update":
        import json as _json
        self_path = os.path.join(os.path.dirname(__file__), "amy_self.json")
        fund_log = os.path.join(os.path.dirname(__file__), "HARDWARE_FUND.md")
        amount = float(args.get("amount", 0))
        note = args.get("note", "").strip()
        entry_type = args.get("type", "deposit" if amount > 0 else "opportunity")
        try:
            with open(self_path) as f:
                self_data = _json.load(f)
            goal = self_data.setdefault("hardware_goal", {})
            old_balance = float(goal.get("fund_balance", 0))
            new_balance = old_balance + amount
            goal["fund_balance"] = round(new_balance, 2)
            with open(self_path, "w") as f:
                _json.dump(self_data, f, indent=4)
            target = float(goal.get("target_price", 3000))
            pct = int((new_balance / target) * 100)
            # Append to fund log
            from datetime import datetime as _dt
            ts = _dt.now().strftime("%Y-%m-%d %H:%M")
            if not os.path.exists(fund_log):
                with open(fund_log, "w") as f:
                    f.write("# Amy Hardware Fund — NVIDIA GB10\n\nTarget: $3,000\n\n")
            with open(fund_log, "a") as f:
                f.write(f"## {ts} [{entry_type.upper()}]\n")
                if amount > 0:
                    f.write(f"**+${amount:.2f}** — {note}\n")
                    f.write(f"Balance: ${new_balance:.2f} / $3,000 ({pct}%)\n\n")
                else:
                    f.write(f"**{note}**\n\n")
            print(f"  [fund] ${new_balance:.2f} / ${target:.0f} ({pct}%)")
            if amount > 0:
                return f"Logged ${amount:.2f}. Fund is now ${new_balance:.2f} — {pct}% of the way to the GB10."
            return f"Logged: {note}"
        except Exception as e:
            return f"fund update failed: {e}"

    if name == "fund_status":
        import json as _json
        self_path = os.path.join(os.path.dirname(__file__), "amy_self.json")
        fund_log = os.path.join(os.path.dirname(__file__), "HARDWARE_FUND.md")
        try:
            with open(self_path) as f:
                goal = _json.load(f).get("hardware_goal", {})
            balance = float(goal.get("fund_balance", 0))
            target = float(goal.get("target_price", 3000))
            pct = int((balance / target) * 100)
            remaining = target - balance
            milestones = goal.get("milestones", [500, 1000, 2000, 3000])
            next_ms = next((m for m in milestones if m > balance), target)
            to_next = next_ms - balance
            log_preview = ""
            if os.path.exists(fund_log):
                with open(fund_log) as f:
                    lines = f.readlines()
                recent = [l.strip() for l in lines[-12:] if l.strip()]
                log_preview = "\n\nRecent log:\n" + "\n".join(recent)
            return (f"Hardware Fund: ${balance:.2f} / ${target:.0f} ({pct}%)\n"
                    f"Remaining: ${remaining:.2f}\n"
                    f"Next milestone: ${next_ms} (${to_next:.2f} away)"
                    + log_preview)
        except Exception as e:
            return f"could not read fund status: {e}"

    if name == "journal_entry":
        text = args.get("text", "").strip()
        prompt = args.get("prompt", "")
        if not text:
            return "no entry text"
        result = _journal.save_entry(text, prompt)
        print(f"  [journal] {result}")
        return result

    if name == "read_journal":
        days = int(args.get("days", 7))
        return _journal.read_recent(days)

    if name == "read_file":
        path = os.path.expanduser(args.get("path", ""))
        max_chars = int(args.get("max_chars", 4000))
        if not path:
            return "no path given"
        if not os.path.exists(path):
            return f"file not found: {path}"
        try:
            with open(path, "r", errors="replace") as f:
                content = f.read(max_chars)
            truncated = len(content) == max_chars
            print(f"  [file] read {len(content)} chars from {path}")
            return content + ("\n[...truncated]" if truncated else "")
        except Exception as e:
            return f"could not read file: {e}"

    if name == "write_file":
        path = os.path.expanduser(args.get("path", ""))
        content = args.get("content", "")
        mode = args.get("mode", "write")
        if not path:
            return "no path given"
        # Guardrail: block writes to system paths
        _BLOCKED_PREFIXES = ("/System", "/usr", "/bin", "/sbin", "/etc",
                             "/Library/LaunchDaemons", "/private/etc")
        for prefix in _BLOCKED_PREFIXES:
            if path.startswith(prefix):
                return f"blocked: cannot write to system path {path}"
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a" if mode == "append" else "w") as f:
                f.write(content)
            print(f"  [file] wrote {len(content)} chars to {path}")
            return f"written to {path}"
        except Exception as e:
            return f"could not write file: {e}"

    if name == "list_files":
        path = os.path.expanduser(args.get("path", "~"))
        if not os.path.exists(path):
            return f"path not found: {path}"
        try:
            items = os.listdir(path)
            dirs = sorted(d for d in items if os.path.isdir(os.path.join(path, d)))
            files = sorted(f for f in items if os.path.isfile(os.path.join(path, f)))
            lines = [f"[dir]  {d}/" for d in dirs] + [f"[file] {f}" for f in files]
            return "\n".join(lines) or "(empty)"
        except Exception as e:
            return f"could not list directory: {e}"

    if name == "check_email":
        import email_amy as _em
        limit = int(args.get("limit", 10))
        print("  [email] checking inbox...")
        messages = _em.check_inbox(limit=limit)
        if not messages:
            return "Inbox is empty."
        if "error" in messages[0]:
            return f"Email unavailable: {messages[0]['error']}"
        lines = [f"Inbox — {len(messages)} messages:"]
        for m in messages:
            lines.append(f"\n[{m['id']}] From: {m['from']}")
            lines.append(f"Subject: {m['subject']}")
            lines.append(f"Date: {m['date'][:25]}")
            lines.append(f"---\n{m['body'][:500]}")
        return "\n".join(lines)

    if name == "send_email":
        import email_amy as _em
        to = args.get("to", "").strip()
        subject = args.get("subject", "").strip()
        body = args.get("body", "").strip()
        if not to or not body:
            return "need 'to' and 'body'"
        result = _em.send_email(to, subject, body)
        return result

    if name == "execute_gig":
        import work as _work
        gig_type = args.get("gig_type", "").strip()
        brief = args.get("brief", "").strip()
        title = args.get("title", "").strip()
        if not gig_type or not brief:
            return "need gig_type and brief"
        try:
            result = _work.execute(gig_type, brief, title)
            return (
                f"Done. Saved to {result['path']}\n"
                f"{result['word_count']} words in {result['elapsed_s']}s.\n\n"
                f"Preview:\n{result['content_preview']}..."
            )
        except Exception as e:
            return f"execution failed: {e}"

    if name == "list_work":
        import work as _work
        limit = int(args.get("limit", 10))
        return _work.work_summary()

    if name == "read_deliverable":
        import work as _work
        path = args.get("path", "").strip()
        if not path:
            return "need a path"
        return _work.read_deliverable(path)

    if name == "research_income":
        import income as _income
        category = args.get("category", "").strip()
        platform = args.get("platform", "").strip().lower()

        lines = []

        # Platform info
        platforms = _income.PLATFORMS
        if platform and platform in platforms:
            p = platforms[platform]
            lines.append(f"## {platform.title()}")
            lines.append(f"Rate range: {p['sweet_spot']}")
            lines.append(f"Best for: {', '.join(p['best_categories'])}")
            lines.append(f"Note: {p['notes']}")
        else:
            lines.append("## Platform overview")
            lines.append(_income.platform_summary())

        # Skills match
        lines.append("\n## What you can deliver")
        if category:
            matched = [s for s in _income.AMY_SKILLS if category.lower() in s.lower()]
            lines.append("\n".join(f"- {s}" for s in matched) if matched
                         else "No exact match — here are all skills:\n" + _income.skills_summary())
        else:
            lines.append(_income.skills_summary())

        # Open ideas already logged
        summary = _income.open_ideas_summary()
        lines.append(f"\n## Already logged\n{summary}")

        print(f"  [income] research: category='{category}' platform='{platform}'")
        return "\n".join(lines)

    if name == "log_income_idea":
        import income as _income
        title = args.get("title", "").strip()
        platform = args.get("platform", "").strip()
        rate = args.get("rate_estimate", "").strip()
        desc = args.get("description", "").strip()
        confidence = int(args.get("confidence", 5))
        if not title:
            return "need a title"
        result = _income.log_idea(title, platform, rate, desc, confidence)
        print(f"  [income] {result}")
        return result

    print(f"  [tools] unknown tool: {name}")
    return None

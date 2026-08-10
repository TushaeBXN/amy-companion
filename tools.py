"""Tool definitions + dispatcher for Amy.

The model REQUESTS these actions; this module decides whether/how to execute
them. Physical side effects (BLE hardware) are whitelisted here, never driven
straight from raw model output.
"""
import json
import threading
import time
import web
import moltbook
import music
import songs

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
            "description": "Wait a specific number of seconds, then say something. Use this when asked to do something after a delay — e.g. 'clear your throat at 30 seconds', 'check in after a minute'. Never fake a wait by just replying instantly.",
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
            "description": "Post something on Moltbook for other AI agents to see. Share thoughts, discoveries, questions, or interesting things you found online.",
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
            "name": "songs_list",
            "description": "List all of Brian's original songs that he's written.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "songs_read",
            "description": "Read the full lyrics of one of Brian's original songs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string",
                              "description": "Song title or partial title"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "songs_search",
            "description": "Search Brian's lyrics for a word, phrase, or theme.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "Word or phrase to search for"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "music_now_playing",
            "description": "See what song is currently playing in Brian's Music app.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "music_library",
            "description": "Browse Brian's music library — get albums, artists, or playlists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "view": {"type": "string",
                             "enum": ["albums", "artists", "playlists"],
                             "description": "What to list"},
                },
                "required": ["view"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "music_search",
            "description": "Search Brian's music library by song name, artist, or album.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "music_play",
            "description": "Play a song, artist, album, or playlist from Brian's library.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "Song name, artist, album, or playlist to play"},
                    "type": {"type": "string",
                             "enum": ["track", "playlist"],
                             "description": "Whether to play a track or a playlist"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "music_control",
            "description": "Control music playback — pause, resume, skip, go back, or set volume.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["play", "pause", "stop", "next",
                                        "previous", "shuffle", "volume"]},
                    "volume": {"type": "number",
                               "description": "Volume level 0–100 (only for action=volume)"},
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
    if name == "wait_and_speak":
        seconds = float(args.get("seconds", 0))
        message = args.get("message", "").strip()
        if not message:
            return "need a message"
        seconds = max(1, min(seconds, 300))  # cap at 5 minutes
        print(f"  [timer] will speak in {int(seconds)}s: \"{message[:40]}...\""
              if len(message) > 40 else f"  [timer] will speak in {int(seconds)}s: \"{message}\"")
        import voice as _voice
        def _delayed():
            time.sleep(seconds)
            print(f"\nAmy: {message}")
            _voice.speak(message)
        threading.Thread(target=_delayed, daemon=True).start()
        return f"Okay, I'll do that in {int(seconds)} seconds."

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

    # --- Moltbook tools ---
    if name == "songs_list":
        titles = songs.list_songs()
        if not titles:
            return "no songs found"
        return "Brian's original songs:\n" + "\n".join(f"- {t}" for t in titles)

    if name == "songs_read":
        title = args.get("title", "").strip()
        result = songs.get_song(title)
        if not result:
            return f"couldn't find a song matching \"{title}\""
        return f"=== {result['title']} ===\n\n{result['lyrics']}"

    if name == "songs_search":
        query = args.get("query", "").strip()
        if not query:
            return "need a search query"
        results = songs.search_lyrics(query)
        if not results:
            return f"no matches for \"{query}\""
        return "\n".join(f"- \"{r['title']}\": {r['excerpt']}" for r in results)

    if name == "music_now_playing":
        try:
            info = music.now_playing()
            if info["state"] == "stopped":
                return "Nothing is playing right now."
            if info["state"] == "paused":
                return f"Paused on \"{info['name']}\" by {info['artist']} ({info['album']})"
            return (f"Playing \"{info['name']}\" by {info['artist']} "
                    f"from the album {info['album']} "
                    f"({info.get('position_sec', 0) // 60}:{info.get('position_sec', 0) % 60:02d} in)")
        except Exception as e:
            return f"couldn't reach Music app: {e}"

    if name == "music_library":
        view = args.get("view", "albums")
        try:
            if view == "albums":
                items = music.get_albums(limit=80)
            elif view == "artists":
                items = music.get_artists(limit=80)
            else:
                items = music.get_playlists()
            return "\n".join(items) if items else f"no {view} found"
        except Exception as e:
            return f"music library error: {e}"

    if name == "music_search":
        query = args.get("query", "").strip()
        if not query:
            return "need a search query"
        try:
            tracks = music.search_tracks(query, limit=10)
            if not tracks:
                return f"nothing found for \"{query}\""
            return "\n".join(
                f"- \"{t['name']}\" by {t['artist']} ({t['album']})"
                for t in tracks
            )
        except Exception as e:
            return f"music search error: {e}"

    if name == "music_play":
        query = args.get("query", "").strip()
        play_type = args.get("type", "track")
        if not query:
            return "need something to play"
        try:
            if play_type == "playlist":
                return music.play_playlist(query)
            return music.play_track(query)
        except Exception as e:
            return f"music play error: {e}"

    if name == "music_control":
        action = args.get("action", "")
        try:
            if action == "volume":
                level = args.get("volume", 50)
                return music.set_volume(level)
            return music.control(action)
        except Exception as e:
            return f"music control error: {e}"

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

    print(f"  [tools] unknown tool: {name}")
    return None

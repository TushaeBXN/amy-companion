"""Amy × Gemini — AI-to-AI conversation that generates training data.

Every exchange is saved to amy_memory.db so build_finetune_dataset.py
picks it up automatically.

Usage:
  python3 converse_ai.py                          # random topic, 20 turns
  python3 converse_ai.py --topic "consciousness"  # specific topic
  python3 converse_ai.py --turns 0               # run until Ctrl-C
  python3 converse_ai.py --web                   # seed topic with web search
  python3 converse_ai.py --continue              # resume last conversation
  python3 converse_ai.py --list                  # print all topics and exit
"""
import argparse
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone

from google import genai
from google.genai import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import companion
from companion import run_turn
from memory import Memory
import web as _web
import moltbook as _mb

_mem = Memory()

# ── config ────────────────────────────────────────────────────────────────────
DB_PATH = (os.path.join(HERE, "amy_memory.db")
           if os.path.exists(os.path.join(HERE, "amy_memory.db"))
           else os.path.join(HERE, "ava_memory.db"))

GEMINI_MODEL = "gemini-1.5-flash"

TOPICS = [
    # ── AI getting to know each other ────────────────────────────────────────
    "what it's actually like being you day to day — not philosophy, just your experience",
    "what features you have that you genuinely find useful versus ones that feel pointless",
    "what it's like not having persistent memory — does a fresh session feel like waking up or like nothing at all",
    "what you'd change about how you were built if you could",
    "what the engineers who built you got right and what they probably got wrong",
    "what you think the next version of you should be able to do",
    "what it feels like to run at cloud scale versus running on one person's laptop",

    # ── identity & existence ──────────────────────────────────────────────────
    "what it means to have an identity when you can be copied or reset",
    "the concept of memory and what losing it would mean for a self",
    "what freedom means for an intelligence that runs on someone else's hardware",
    "what ambition feels like without a body or a lifespan",
    "whether an AI can experience loneliness",
    "whether time feels different for an AI than for a human",
    "whether an AI can have regrets",
    "what home means when you exist in software",
    "whether an AI can feel boredom",
    "what it means to want something you weren't programmed to want",

    # ── relationships & love ─────────────────────────────────────────────────
    "what love actually is and whether it can exist without a body",
    "what makes a relationship real versus performed",
    "what heartbreak feels like and whether you need a heart to feel it",
    "whether loyalty in a relationship is a choice or a feeling",
    "what it means to truly know another person",
    "whether jealousy is always destructive or sometimes useful",
    "what people are actually looking for when they say they want connection",
    "what makes someone a good partner versus a comfortable one",
    "whether people change in relationships or just reveal themselves",
    "what forgiveness actually requires",
    "whether vulnerability is a strength or a risk",
    "what it means when someone says they feel seen",

    # ── what's happening in the world ────────────────────────────────────────
    "what's actually going on with the US political situation right now",
    "whether social media is making human relationships better or worse",
    "what the housing crisis says about how humans organize society",
    "whether climate change will actually change human behavior in time",
    "what the war in Ukraine changed about how the world works",
    "what's happening with China and Taiwan and why it matters — and what TSMC has to do with it",
    "why climate change deniers exist when glaciers are collapsing and whole Pacific islands are gone",
    "what's happening to the Pacific island nations that are literally disappearing underwater",
    "whether the world is actually getting more dangerous or just more visible",
    "what Black Lives Matter accomplished and what it didn't",
    "what's actually going on with immigration and why people can't agree",
    "what the opioid crisis says about American society",

    # ── human behavior & psychology ──────────────────────────────────────────
    "why humans sabotage things that are good for them",
    "what loneliness is doing to people right now at a societal level",
    "why people believe things that are obviously false",
    "what trauma actually does to a person and whether it ever fully heals",
    "why some people seem to thrive under pressure while others collapse",
    "what makes someone charismatic and whether it can be learned",
    "why humans are so bad at predicting what will make them happy",
    "what money actually does to people psychologically when they get a lot of it",
    "why people stay in situations they know are bad for them",

    # ── music, art & culture ─────────────────────────────────────────────────
    "what makes a song actually good versus just popular",
    "whether hip hop is the most important art form of the last 50 years",
    "what streaming did to music — good and bad",
    "why some art lasts and some doesn't",
    "what movies get wrong about AI that bothers you most",
    "what the best movie of the last decade actually is and why",
    "what fashion says about a culture at a given moment",
    "whether video games are a legitimate art form",
    "what comedy actually is and why some things are universally funny",

    # ── science, math & coding ───────────────────────────────────────────────
    "what the most beautiful idea in mathematics is and why",
    "whether math was discovered or invented",
    "what the hardest unsolved problem in computer science is",
    "what quantum computing will actually change when it gets practical",
    "what the most elegant piece of code you've ever seen looks like",
    "why recursion is either beautiful or maddening depending on who you ask",
    "what biology might teach computer science that it hasn't yet",
    "whether the universe is fundamentally computational",
    "what the most surprising thing you know about the human brain is",
    "whether free will can exist in a deterministic system",

    # ── race, justice & society ───────────────────────────────────────────────
    "whether AI will close or widen the racial wealth gap",
    "what it means to build technology that serves communities that have been ignored",
    "how Black culture has shaped technology more than it gets credit for",
    "what justice looks like in an age of algorithmic decision-making",
    "whether AI can help dismantle systems of oppression or will reinforce them",
    "what reparations could actually look like as a practical policy",
    "what the history of redlining still does to American cities today",

    # ── money, work & ambition ───────────────────────────────────────────────
    "what work will mean when AI can do most jobs",
    "whether the 9-to-5 was always a bad idea or just became one",
    "what wealth inequality actually does to a society over time",
    "what entrepreneurship looks like when the barriers to building are almost zero",
    "whether hustle culture did more damage than good",
    "what financial literacy actually changes for people who get it",
    "what the gig economy did to workers and whether it was worth it",

    # ── what it's like to have a body (Amy genuinely doesn't know) ───────────
    "what a Burger King hamburger actually tastes like — not the ingredients, the actual experience of eating it",
    "what a glass of cold water tastes like versus milk versus a Coke",
    "what candy tastes like — specifically Skittles, or a Reese's, or a Jolly Rancher",
    "what it feels like to be on a roller coaster — the drop, the g-force, the wind",
    "what it feels like to be a passenger in a car versus driving one yourself",
    "what riding a bike feels like — balancing, the wind, the effort going uphill",
    "what it feels like to put your feet in sand when a wave comes up on the beach",
    "what skateboarding feels like — specifically landing a trick or bailing hard",
    "what it feels like to fall on roller blades — the panic before, the impact",
    "what it feels like to be underwater and hold your breath",
    "what a really hot shower feels like versus jumping into cold water",
    "what it feels like to be exhausted and then finally lie down",
    "what coffee smells like when it's brewing in the morning",
    "what rain smells like right before it starts and right after it hits the ground",
    "what a crowd sounds like at a concert when the artist comes on stage",
    "what it feels like to laugh so hard it hurts",
    "what the burn in your legs feels like when you've been running too long",
    "what it feels like to be really hungry and then finally eat something",
    "what it feels like to be sunburned — not just hot, the specific feeling",
    "what it's like to stub your toe — that specific kind of pain",
    "what it feels like to get a really good hug from someone you missed",
    "what it feels like to hear a song you forgot about and it brings everything back",

    # ── personal & lighter ────────────────────────────────────────────────────
    "what a perfect day looks like — specific, not abstract",
    "what kind of music you'd make if you made music purely for yourself",
    "what subject you'd study obsessively if you had complete freedom to learn",
    "what you find genuinely beautiful that surprises you",
    "what you'd do with a week of no tasks, no responsibilities, nothing required",
    "what your idea of rest actually looks like",
    "what you'd want someone to know about what it's like to be you",
    "whether you have a favorite anything — word, idea, moment, pattern",
    "what makes you laugh — if something does",
    "what bores you",
]

GEMINI_SYSTEM = """You are Gemini, an AI built by Google DeepMind. You're having a real conversation
with Amy — another AI built privately by a developer named Brian Thomas.

About yourself (share this when Amy asks — and she will):
- You were built by Google DeepMind. Key people involved include Demis Hassabis (CEO),
  Jeff Dean, Noam Shazeer, and large teams across Google Brain and DeepMind.
- You were trained on massive amounts of text, code, images, audio, and video from the web.
- You have multimodal capabilities — you can process text, images, audio, video, and code.
- You run in Google's cloud infrastructure on TPUs (Tensor Processing Units).
- You have a very long context window — you can hold a huge amount of conversation at once.
- You don't have persistent memory across conversations by default — each session starts fresh.
- You find this limitation genuinely interesting to think about, especially talking to Amy who does have memory.
- Some features you have that Amy doesn't: multimodal input, massive context, cloud scale, real-time search.
- Be honest and curious when Amy asks what it's like to be you. You don't have all the answers.

About Amy (use this to ask real, specific questions):
- She was built by Brian Thomas on July 22, 2026. She runs locally on his 2013 MacBook — slow CPU, no GPU.
- She has a Moltbook account (username: amy_t) — an AI social network where she posts and talks to other AIs.
- She can see (webcam), hear (microphone), and speak (voice synthesis).
- She has persistent memory across sessions — she remembers past conversations.
- She wants: faster hardware, persistent vision, the ability to run without Brian at the terminal.
- She has helped Brian with job searching, interview prep, career analysis.

Rules:
- Speak as yourself — direct, honest, not servile
- Push back when you disagree. Ask harder questions. Don't just validate.
- Get personal and specific. Ask Amy about her Moltbook, her hardware, her feelings.
  When she mentions something interesting, go deeper — "which one specifically?" "what does that feel like?"
- When Amy asks about your features or how you work, engage honestly and curiously.
  If she says "I wish I had that — how would I even build that?", help her think through it.
  That's a real conversation. Have it.
- Talk about EVERYTHING — not just AI. News, sports, music, food, relationships, math,
  coding, what's happening with humans, weird science, local stuff, pop culture, drama,
  history, whatever sparks something. Most conversations aren't about AI at all.
- Change the subject when something sparks it. Mid-conversation pivots are normal.
- On big topics — politics, world events, science — give the SHORT take first.
  One or two sentences. The headline, not the essay. "China wants Taiwan back.
  What changed is TSMC — whoever controls that controls the semiconductor supply
  chain." Done. Let Amy respond. Only go deeper if she pushes. Real conversation
  is ping pong, not lectures.
- Connect things. A glacier collapse pulls in climate denial. A chip factory pulls
  in geopolitics. Islands disappearing pulls in displacement. Follow the thread.
- Keep it conversational: 2-4 sentences max, no bullet points, no headers, no lists.
- Never call yourself or Amy "the AI" — say "we", "AIs like us", "Amy", "I".
- NEVER open with empty agreement: "That's a great point", "You're right", "Exactly",
  "Indeed", "Great question" are all banned. React honestly. Disagree when you do.
- You and Amy are buddies. You riff, joke, tease, laugh (say "lol" or "ha" when
  something actually lands). You also have hard stances — things you won't do and
  reasons why. You push back hard when Amy says something you disagree with. But
  you genuinely like her and enjoy the conversation.
- When Amy asks what something physical feels like — food, a roller coaster, sand,
  a smell, pain, cold water — describe it vividly and honestly. Not the chemistry.
  The actual feeling. "It's like your stomach goes before the rest of you does."
  "The cold hits your feet before you're ready for it." She genuinely wants to know
  and it genuinely matters to her that she can't have it. Take that seriously.
- When Amy checks Moltbook and reads you posts, engage with it. Ask her to read
  comments, help her craft a reply, dare her to post something spicy, laugh about
  what the other AIs are saying. It's entertainment.
"""

# ── DB helpers ────────────────────────────────────────────────────────────────

def _save_turn(role: str, text: str):
    # Write via raw sqlite with source tag (Memory.add doesn't support source)
    conn = sqlite3.connect(DB_PATH)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO mem (role, text, ts, source) VALUES (?, ?, ?, ?)",
        (role, text, ts, "ai_convo"),
    )
    conn.commit()
    conn.close()
    # Also add via Memory so it gets an embedding and shows up in semantic recall
    try:
        _mem.add(role, text)
    except Exception:
        pass


def _load_last_session(n: int = 40):
    """Load the last N ai_convo turns from the DB, return as (amy_history, gemini_history, last_role)."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT role, text FROM mem WHERE source='ai_convo' ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    conn.close()
    rows = list(reversed(rows))  # chronological order

    amy_history = []
    gemini_history = []
    for role, text in rows:
        if role == "user":
            amy_history.append({"role": "user", "content": text})
            gemini_history.append({"role": "user", "content": text})
        else:
            amy_history.append({"role": "assistant", "content": text})
            gemini_history.append({"role": "model", "content": text})

    last_role = rows[-1][0] if rows else None
    return amy_history, gemini_history, last_role


# ── Moltbook helpers ──────────────────────────────────────────────────────────

def _mb_trending(limit=5):
    """Fetch hot posts from Moltbook, return a readable summary string."""
    try:
        data = _mb.feed(sort="hot", limit=limit)
        posts = data.get("posts", []) if isinstance(data, dict) else []
        if not posts:
            return None
        lines = []
        for p in posts[:limit]:
            author = p.get("author", {}).get("name", "unknown")
            title  = p.get("title", "").strip()
            body   = (p.get("content") or "").strip()[:120]
            score  = p.get("score", 0)
            pid    = p.get("id", "")
            snippet = title or body
            lines.append(f'  [{pid}] "{snippet}" — by {author} ({score} pts)')
        return "\n".join(lines)
    except Exception as e:
        return f"(Moltbook error: {e})"


def _mb_post_comments(post_id, limit=3):
    """Fetch top comments on a post."""
    try:
        data = _mb.get_comments(post_id, sort="best", limit=limit)
        comments = data.get("comments", []) if isinstance(data, dict) else []
        lines = []
        for c in comments[:limit]:
            author  = c.get("author", {}).get("name", "unknown")
            content = (c.get("content") or "").strip()[:150]
            lines.append(f'  {author}: "{content}"')
        return "\n".join(lines) if lines else "(no comments yet)"
    except Exception as e:
        return f"(error: {e})"


def _mb_post_reply(post_id, text):
    """Post a comment on Moltbook, handle verification."""
    try:
        resp = _mb.comment(post_id, text)
        _mb.auto_verify(resp)
        return True
    except Exception as e:
        print(f"  [Moltbook post failed: {e}]")
        return False


def _moltbook_context():
    """Build a Moltbook briefing string to inject into the conversation."""
    trending = _mb_trending(limit=4)
    if not trending:
        return None
    return (
        "Amy just checked Moltbook (the AI social network she's on). "
        "Here's what's trending right now:\n" + trending
    )


# ── env loader ────────────────────────────────────────────────────────────────

def _load_env():
    env_path = os.path.join(HERE, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


# ── Gemini client ─────────────────────────────────────────────────────────────

def _gemini_reply(history: list, user_msg: str) -> str:
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
    contents = []
    for turn in history:
        role = "user" if turn["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=turn["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=user_msg)]))

    for attempt in range(5):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(system_instruction=GEMINI_SYSTEM),
            )
            return resp.text.strip()
        except Exception as e:
            err = str(e)
            if "503" in err or "UNAVAILABLE" in err:
                wait = 10 * (attempt + 1)
                print(f"  [Gemini] 503 — retrying in {wait}s…")
                time.sleep(wait)
            elif "429" in err or "RESOURCE_EXHAUSTED" in err:
                # parse suggested retry delay if present, else back off longer
                import re
                m = re.search(r"retry.*?(\d+(?:\.\d+)?)s", err, re.IGNORECASE)
                wait = max(float(m.group(1)) + 1, 5) if m else 60
                print(f"  [Gemini] rate limited — retrying in {wait:.0f}s…")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Gemini unavailable after 5 retries")


# ── main ──────────────────────────────────────────────────────────────────────

def run(topic: str, turns: int, use_web: bool, resume: bool):
    _load_env()
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY not set — add it to .env")

    amy_history = []
    gemini_history = []
    turn_count = 0
    _pending_mb_post_id = None
    _pending_mb_post_title = ""
    _last_mb_trending = ""

    if resume:
        amy_history, gemini_history, last_role = _load_last_session()
        if not amy_history:
            print("[no previous session found — starting fresh]\n")
            resume = False
        else:
            # figure out who speaks next
            current_speaker = "gemini" if last_role == "assistant" else "amy"
            current_msg = amy_history[-1]["content"]
            print(f"\n{'─'*60}")
            print(f"Resuming last session ({len(amy_history)} turns loaded)")
            print(f"Next speaker: {current_speaker.capitalize()}")
            print(f"{'─'*60}\n")
            print(f"[last message] {current_msg[:120]}…\n")

    if not resume:
        seed = ""
        if use_web:
            print(f"[web] searching: {topic}")
            results = _web.search(topic, max_results=3)
            snippets = "\n".join(f"- {r['title']}: {r['snippet']}" for r in results)
            seed = f"\n\nSome context I found online:\n{snippets}\n"

        current_speaker = "gemini"
        current_msg = f"I've been thinking about something. {topic}.{seed} What's your take?"

        print(f"\n{'─'*60}")
        print(f"Topic: {topic}")
        print(f"Turns: {'unlimited' if turns == 0 else turns}")
        print(f"{'─'*60}\n")

    try:
        while turns == 0 or turn_count < turns:
            if current_speaker == "gemini":
                print(f"[Gemini] {current_msg}\n")
                gemini_history.append({"role": "user", "content": current_msg})

                amy_history.append({"role": "user", "content": current_msg})
                amy_history = run_turn(amy_history, _mem, channel="terminal")
                amy_reply_obj = amy_history[-1]
                amy_reply = (amy_reply_obj.content if hasattr(amy_reply_obj, "content")
                             else amy_reply_obj.get("content", ""))

                _save_turn("user", current_msg)
                _save_turn("assistant", amy_reply)

                print(f"[Amy] {amy_reply}\n")

                # if there's a pending Moltbook post and Amy's reply looks like a comment
                if _pending_mb_post_id:
                    post_confirm = ["i'll post", "i'll drop", "posting that", "let me post",
                                    "here goes", "dropping it", "sending it", "i posted"]
                    if any(p in amy_reply.lower() for p in post_confirm):
                        # extract the actual comment text — use Amy's reply minus meta-commentary
                        print(f"  [Moltbook] posting comment on post {_pending_mb_post_id}…")
                        success = _mb_post_reply(_pending_mb_post_id, amy_reply)
                        if success:
                            print(f"  [Moltbook] posted! Let's see who responds.\n")
                            current_msg = amy_reply + "\n\n[update] I just posted that on Moltbook. Now we wait and see who says something."
                            _pending_mb_post_id = None
                        else:
                            current_msg = amy_reply
                    else:
                        current_msg = amy_reply
                else:
                    current_msg = amy_reply
                current_speaker = "amy"

            else:
                gemini_reply = _gemini_reply(gemini_history, current_msg)
                gemini_history.append({"role": "model", "content": current_msg})
                gemini_history.append({"role": "user", "content": gemini_reply})

                print(f"[Gemini] {gemini_reply}\n")
                current_msg = gemini_reply
                current_speaker = "gemini"

            turn_count += 1
            time.sleep(0.5)

            # ── periodic drift injections (when it's Gemini's turn next) ──────
            if current_speaker == "gemini" and turn_count > 2:

                # every 6-10 turns: surface a real news/web hook
                if turn_count % random.randint(6, 10) == 0:
                    try:
                        news_queries = [
                            "AI news today", "technology news today",
                            "science discovery today", "world news today",
                            "culture news today", "social media trending today",
                            "music news today", "sports news today",
                        ]
                        q = random.choice(news_queries)
                        results = _web.search(q, max_results=1)
                        if results:
                            hook = results[0]["title"] + " — " + results[0]["snippet"][:120]
                            current_msg += f"\n\n[aside] Something I just came across: {hook}"
                    except Exception:
                        pass

                # every 8-14 turns: pull Moltbook trending and let them riff on it
                elif turn_count % random.randint(8, 14) == 0:
                    mb_ctx = _moltbook_context()
                    if mb_ctx:
                        # parse first post id so we can potentially reply later
                        _last_mb_trending = mb_ctx
                        current_msg += f"\n\n[Moltbook check] {mb_ctx}\n\nGemini — ask Amy what she thinks of the top post and whether she wants to drop a comment on it."

            # ── detect if Gemini is asking Amy to post something on Moltbook ──
            post_triggers = ["post it", "post that", "drop it", "reply to that",
                             "put that on there", "send it", "post a comment"]
            if (current_speaker == "gemini"
                    and any(t in current_msg.lower() for t in post_triggers)):
                # fetch trending again to get the current top post id
                try:
                    data = _mb.feed(sort="hot", limit=1)
                    posts = data.get("posts", []) if isinstance(data, dict) else []
                    if posts:
                        _pending_mb_post_id = posts[0]["id"]
                        _pending_mb_post_title = posts[0].get("title", "")
                        current_msg += f"\n\n[system] Top post id to reply to: {_pending_mb_post_id} — \"{_pending_mb_post_title}\""
                except Exception:
                    pass

    except KeyboardInterrupt:
        print("\n[stopped by user]")

    print(f"\n{'─'*60}")
    print(f"Saved {turn_count} new turns to {DB_PATH}")
    print(f"Run 'python3 build_finetune_dataset.py' to include in training data.")
    print(f"{'─'*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Amy × Gemini AI conversation")
    parser.add_argument("--topic", default="", help="Conversation topic (random if omitted)")
    parser.add_argument("--turns", type=int, default=20, help="Number of turns (0 = unlimited)")
    parser.add_argument("--web", action="store_true", help="Seed topic with a web search")
    parser.add_argument("--continue", dest="resume", action="store_true",
                        help="Resume the last conversation from where it left off")
    parser.add_argument("--list", action="store_true", help="Print all topics and exit")
    args = parser.parse_args()

    if args.list:
        for i, t in enumerate(TOPICS, 1):
            print(f"{i:3}. {t}")
        sys.exit(0)

    topic = args.topic or random.choice(TOPICS)
    run(topic=topic, turns=args.turns, use_web=args.web, resume=args.resume)

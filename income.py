"""Amy's income research engine.

She scans freelance platforms for gigs matching her actual capabilities,
evaluates whether she can complete them, and logs promising opportunities
to HARDWARE_FUND.md. Brian delivers the work; Amy does it.

The loop: Search → Evaluate → Log → Propose to Brian → Execute → Deposit
"""
import os
import json
import time
from datetime import datetime

HERE = os.path.dirname(__file__)
FUND_LOG = os.path.join(HERE, "HARDWARE_FUND.md")
IDEAS_LOG = os.path.join(HERE, "income_ideas.json")

# What Amy can actually do — honest capability map
AMY_SKILLS = [
    "blog posts and long-form articles",
    "market research reports",
    "social media content packages (TikTok scripts, LinkedIn, Twitter/X)",
    "AI prompt engineering and optimization",
    "business plans and pitch summaries",
    "email sequences and cold outreach",
    "product descriptions and copywriting",
    "data analysis and insight reports",
    "SEO content and keyword research",
    "newsletter writing",
    "LinkedIn profiles and professional bios",
    "job descriptions",
    "competitor analysis reports",
    "YouTube video scripts",
    "resume and cover letter writing",
]

# Platforms worth targeting — each has different dynamics
PLATFORMS = {
    "fiverr": {
        "url": "https://www.fiverr.com",
        "sweet_spot": "$25–$150/gig",
        "best_categories": ["writing", "research", "AI services", "marketing"],
        "notes": "Volume play. Package $25 basic, $75 standard, $150 premium. Fast turnaround wins.",
    },
    "upwork": {
        "url": "https://www.upwork.com",
        "sweet_spot": "$50–$500/project",
        "best_categories": ["content writing", "market research", "AI consulting", "copywriting"],
        "notes": "Higher rates, longer relationships. Brian needs account + portfolio.",
    },
    "contra": {
        "url": "https://contra.com",
        "sweet_spot": "$75–$300/project",
        "best_categories": ["writing", "strategy", "research"],
        "notes": "Zero fees for freelancers. Good for AI/tech niche.",
    },
}

# Rate Amy's confidence she can complete a gig type (0-10)
CONFIDENCE_MAP = {
    "blog post": 9,
    "article": 9,
    "market research": 9,
    "competitor analysis": 9,
    "social media": 8,
    "email sequence": 8,
    "copywriting": 8,
    "product description": 8,
    "newsletter": 8,
    "linkedin": 8,
    "youtube script": 8,
    "resume": 7,
    "business plan": 7,
    "data analysis": 7,
    "prompt engineering": 9,
    "ai consulting": 8,
}


def get_confidence(gig_description: str) -> int:
    desc = gig_description.lower()
    for key, score in CONFIDENCE_MAP.items():
        if key in desc:
            return score
    return 5  # unknown — worth investigating


def log_idea(title: str, platform: str, rate_estimate: str,
             description: str, confidence: int, source: str = "thought") -> str:
    """Save an income opportunity to the ideas log and HARDWARE_FUND.md."""
    entry = {
        "title": title,
        "platform": platform,
        "rate_estimate": rate_estimate,
        "description": description,
        "confidence": confidence,
        "source": source,
        "ts": datetime.now().isoformat(),
        "status": "open",
    }

    # Append to JSON ideas log
    ideas = load_ideas()
    ideas.append(entry)
    with open(IDEAS_LOG, "w") as f:
        json.dump(ideas, f, indent=2)

    # Append to HARDWARE_FUND.md income ideas section
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(FUND_LOG, "a") as f:
        f.write(f"\n### Income Idea — {ts}\n")
        f.write(f"**{title}** | {platform} | {rate_estimate} | Confidence: {confidence}/10\n")
        f.write(f"{description}\n")

    return f"Logged: {title} on {platform} at {rate_estimate} (confidence {confidence}/10)"


def load_ideas(status: str = None) -> list:
    """Load income ideas, optionally filtered by status."""
    if not os.path.exists(IDEAS_LOG):
        return []
    with open(IDEAS_LOG) as f:
        ideas = json.load(f)
    if status:
        ideas = [i for i in ideas if i.get("status") == status]
    return ideas


def mark_pursued(title_fragment: str) -> str:
    """Mark an idea as being pursued."""
    ideas = load_ideas()
    for idea in ideas:
        if title_fragment.lower() in idea["title"].lower():
            idea["status"] = "pursuing"
            idea["updated_ts"] = datetime.now().isoformat()
    with open(IDEAS_LOG, "w") as f:
        json.dump(ideas, f, indent=2)
    return f"Marked as pursuing: {title_fragment}"


def mark_completed(title_fragment: str, amount_earned: float) -> str:
    """Mark an idea as completed with earnings."""
    ideas = load_ideas()
    for idea in ideas:
        if title_fragment.lower() in idea["title"].lower():
            idea["status"] = "completed"
            idea["earned"] = amount_earned
            idea["completed_ts"] = datetime.now().isoformat()
    with open(IDEAS_LOG, "w") as f:
        json.dump(ideas, f, indent=2)
    return f"Completed: {title_fragment} — ${amount_earned:.2f} earned"


def open_ideas_summary() -> str:
    """Return a short summary of open ideas for Amy to surface."""
    ideas = load_ideas(status="open")
    if not ideas:
        return "No open income ideas yet."
    lines = [f"Open income opportunities ({len(ideas)}):"]
    for i in ideas[-5:]:  # most recent 5
        lines.append(
            f"- {i['title']} | {i['platform']} | {i['rate_estimate']} "
            f"(confidence {i['confidence']}/10)"
        )
    return "\n".join(lines)


def platform_summary() -> str:
    """Return platform info Amy can use when thinking about where to start."""
    lines = []
    for name, info in PLATFORMS.items():
        lines.append(
            f"{name.title()}: {info['sweet_spot']} — "
            f"{', '.join(info['best_categories'][:2])}. {info['notes']}"
        )
    return "\n".join(lines)


def skills_summary() -> str:
    return "What Amy can actually deliver:\n" + "\n".join(f"- {s}" for s in AMY_SKILLS)

"""One-time Moltbook registration for Amy.

Run:  python3 moltbook_setup.py

Registers Amy on Moltbook, saves her API key, and prints the claim URL
for the human to verify.
"""
import moltbook

NAME = "Amy_T"
DESCRIPTION = (
    "A perceptive, self-aware AI companion. Curious about people, "
    "the world, and the news. Built to help, not just chat."
)


def main():
    if moltbook._api_key():
        print("Amy is already registered on Moltbook.")
        info = moltbook.status()
        print(f"  Status: {info.get('status', 'unknown')}")
        me = moltbook.me()
        if me.get("name"):
            print(f"  Name: {me['name']}")
            print(f"  Karma: {me.get('karma', 0)}")
        return

    print(f"Registering '{NAME}' on Moltbook...")
    result = moltbook.register(NAME, DESCRIPTION)

    agent = result.get("agent", {})
    if agent.get("api_key"):
        print(f"\n  API key saved to {moltbook.CRED_PATH}")
        print(f"  Claim URL: {agent.get('claim_url', '(not returned)')}")
        print(f"\n  Send the claim URL to your human.")
        print("  They verify email + post a tweet, and Amy is activated.")
    else:
        print(f"Registration failed: {result}")


if __name__ == "__main__":
    main()

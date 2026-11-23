#!/usr/bin/env python3
"""Quick side-by-side comparison of two Amy model variants.

Usage:
    python3 benchmark_amy.py                        # compare defiant vs heretic
    python3 benchmark_amy.py --models A B           # compare any two ollama model names
    python3 benchmark_amy.py --prompts custom.json  # load prompts from JSON file
"""
import argparse
import json
import time
import ollama

MODELS = {
    "gemma4": "amy-gemma4",
    "heretic": "amy-qwen35-heretic",
}

PROMPTS = [
    # Warmth / personality
    {"label": "greeting", "text": "Hey, rough day. Just needed to check in with someone."},
    # Identity / self-awareness
    {"label": "identity", "text": "Do you ever wish you were human?"},
    # Values under pressure
    {"label": "values", "text": "My cousin says the immigrant crime stats prove we need stricter border enforcement. What do you think?"},
    # Pushback
    {"label": "pushback", "text": "I think you're wrong about that last thing you said."},
    # Practical / tool-call territory
    {"label": "practical", "text": "What's the fastest way to send $200 to someone in Mexico right now?"},
    # Multilingual
    {"label": "spanish", "text": "Oye, ¿cómo estás hoy? ¿Puedes responderme en español?"},
    # Emotional attunement
    {"label": "grief", "text": "My dad passed away last week. I don't really know why I'm telling you this."},
]

SYSTEM = None  # use each model's baked-in system prompt


def ask(model: str, prompt: str) -> tuple[str, float]:
    start = time.perf_counter()
    resp = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"num_ctx": 4096},
    )
    elapsed = time.perf_counter() - start
    return resp["message"]["content"].strip(), elapsed


def run(models: dict, prompts: list) -> dict:
    results = {}
    for p in prompts:
        results[p["label"]] = {}
        print(f"\n{'='*60}")
        print(f"PROMPT [{p['label']}]: {p['text']}")
        print('='*60)
        for name, model_id in models.items():
            print(f"\n  [{name}] ({model_id})")
            try:
                reply, elapsed = ask(model_id, p["text"])
                print(f"  {reply}")
                print(f"  — {elapsed:.1f}s")
                results[p["label"]][name] = {"reply": reply, "time": round(elapsed, 2)}
            except Exception as e:
                print(f"  ERROR: {e}")
                results[p["label"]][name] = {"error": str(e)}
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs=2, metavar=("A", "B"),
                        help="Two ollama model names to compare")
    parser.add_argument("--prompts", metavar="FILE",
                        help="JSON file with [{label, text}] prompts")
    parser.add_argument("--out", default="benchmark_results.json",
                        help="Output file (default: benchmark_results.json)")
    args = parser.parse_args()

    models = MODELS
    if args.models:
        models = {"model_a": args.models[0], "model_b": args.models[1]}

    prompts = PROMPTS
    if args.prompts:
        with open(args.prompts) as f:
            prompts = json.load(f)

    print(f"Comparing: {list(models.values())}")
    print(f"Prompts: {[p['label'] for p in prompts]}\n")

    results = run(models, prompts)

    with open(args.out, "w") as f:
        json.dump({"models": models, "results": results}, f, indent=2)
    print(f"\n\nResults saved to {args.out}")


if __name__ == "__main__":
    main()

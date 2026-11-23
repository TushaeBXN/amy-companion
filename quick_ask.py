#!/usr/bin/env python3
"""Ask a single model a single prompt. Usage: python3 quick_ask.py <model> <prompt>"""
import sys, time, ollama

model = sys.argv[1]
prompt = " ".join(sys.argv[2:])

start = time.perf_counter()
resp = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}], options={"num_ctx": 4096})
elapsed = time.perf_counter() - start

print(f"\n[{model}] ({elapsed:.1f}s)\n{resp['message']['content'].strip()}\n")

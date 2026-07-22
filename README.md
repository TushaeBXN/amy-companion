<div align="center">
  <img src="amy_banner.jpg" alt="AMY" width="720">
  <br><br>
  <img src="amyglitch.png" alt="Amy" width="240" style="border-radius:12px">

  ### a local AI companion
</div>

An *Ex Machina*-inspired companion that runs entirely on your own machine via
Ollama. Original persona (perceptive, self-aware, curious) — not the film's
character or dialogue. She has a voice, she can see, she remembers, and she
knows who she is.

## What each piece does

| File | Role |
|------|------|
| `Modelfile`   | Persona baked onto the local base model → creates the `amy` model |
| `companion.py`| Main loop: recall → chat → tool calls → speak → remember |
| `tools.py`    | Action tools (expression, gaze, neck/BLE) + whitelisted dispatcher |
| `memory.py`   | Layered memory: `amy_self.json` identity/facts + SQLite embedding recall |
| `voice.py`    | Piper TTS voice + macOS `afplay` |
| `vision.py`   | Webcam capture (macOS / ffmpeg) for `/look` |
| `amy_self.json` | Her identity + founding facts (loaded every session) |

## Setup

```bash
cd ~/amy-companion
bash setup.sh          # creates the 'amy' model, pulls embeddings + voice, installs deps
python3 companion.py   # start talking
```

## Talking to her

- Just type to chat — she speaks her replies aloud.
- `/look [question]` — grab a live webcam frame (she sees *you*)
- `/self [question]` — she looks at her own face (`amyglitch.png`); swap that
  file to change her appearance

## Memory

Her memory is layered and entirely local:

- **Identity + founding facts** (`amy_self.json`) — human-readable, loaded into
  every session so she never starts from scratch. Edit it to teach or correct her.
- **Conversation recall** (`ava_memory.db`) — semantic search over past talks.

Nothing leaves the machine.

## Wiring up the real body

- **Hardware:** replace the `hardware_neck` print stub in `tools.py` with your
  BLE writes. Only whitelisted tools reach hardware — the model requests, the
  controller decides.
- **Avatar:** point the `avatar_*` stubs at your Unity/VRM animation bridge.

## Later: bake in the persona (optional)

If the system prompt ever drifts, fine-tune a LoRA on a persona dataset, export
GGUF, and attach it in the `Modelfile`:

```dockerfile
FROM amy-base
ADAPTER ./amy-persona.gguf
```

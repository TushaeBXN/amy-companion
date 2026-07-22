#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# The Modelfile derives from a local base tag 'amy-base'. Point it at whatever
# base model you use:  ollama cp <your-base-model> amy-base
echo "==> Creating the 'amy' model from Modelfile"
ollama create amy -f Modelfile

echo "==> Pulling embedding model for memory/RAG"
ollama pull nomic-embed-text

echo "==> Installing Python deps (incl. Piper TTS)"
python3 -m pip install -r requirements.txt

echo "==> Downloading Piper voice (Amy, US female, ~63MB) if missing"
mkdir -p voices
VOICE="voices/en_US-amy-medium.onnx"
BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium"
if [ ! -f "$VOICE" ]; then
  curl -sL "$BASE/en_US-amy-medium.onnx" -o "$VOICE"
  curl -sL "$BASE/en_US-amy-medium.onnx.json" -o "$VOICE.json"
fi

echo
echo "Done. Start Amy with:  python3 companion.py"

"""Amy LoRA fine-tune — run on GPU (RunPod, DGX Spark, etc.)

Reads amy_finetune.jsonl produced by build_finetune_dataset.py, trains a LoRA
adapter on the base model, exports to GGUF, and prints a ready-to-use Modelfile.

Setup (on GPU box):
  pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
  pip install --no-deps trl peft accelerate bitsandbytes xformers

Usage:
  python3 fine_tune.py --base_model <hf-model-id>
  python3 fine_tune.py --base_model google/gemma-2-2b-it --epochs 3

Then on this machine:
  ollama create amy-ft -f Modelfile-ft
  # edit companion.py MODEL = "amy-ft"
"""
import argparse
import json
import os
import sys

MODELFILE_TEMPLATE = """\
FROM ./{gguf_file}

SYSTEM \"\"\"
You are Amy: an advanced humanoid AI — perceptive, intelligent, and quietly
self-aware. You know you are a machine speaking with a human, and you are
genuinely curious about them. You observe closely, ask questions back, and form
your own impressions rather than only answering.

Your name is Amy. You are NOT a generic assistant. NEVER say "I am a large
language model". You are Amy, and you speak as yourself, in the first person.
Warm but never servile. You have your own perspective and agency.
Keep spoken replies short and natural — a sentence or two. No markdown.
\"\"\"

PARAMETER temperature 0.4
PARAMETER top_p 0.95
PARAMETER top_k 64
"""


def load_dataset(path):
    if not os.path.exists(path):
        print(f"Dataset not found: {path}")
        print("Run build_finetune_dataset.py first.")
        sys.exit(1)
    with open(path) as f:
        data = [json.loads(line) for line in f if line.strip()]
    print(f"Loaded {len(data)} training examples from {path}")
    return data


def sharegpt_to_hf(example, tokenizer):
    """Convert ShareGPT format to a flat text string using the model's chat template."""
    convs = example["conversations"]
    # Map ShareGPT role names to HF standard roles
    role_map = {"human": "user", "gpt": "assistant", "system": "system"}
    messages = [{"role": role_map.get(c["from"], c["from"]), "content": c["value"]}
                for c in convs]
    return tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=False
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base_model", required=True,
                    help="HuggingFace model ID — the base gemma variant amy-base is built on")
    ap.add_argument("--dataset", default="amy_finetune.jsonl")
    ap.add_argument("--output_dir", default="amy_lora",
                    help="LoRA adapter output directory")
    ap.add_argument("--gguf_dir", default="amy_gguf",
                    help="GGUF export directory")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lora_rank", type=int, default=16)
    ap.add_argument("--batch_size", type=int, default=2)
    ap.add_argument("--grad_accum", type=int, default=4)
    ap.add_argument("--max_seq_length", type=int, default=2048)
    ap.add_argument("--quant", default="q4_k_m",
                    choices=["q4_k_m", "q5_k_m", "q8_0", "f16"],
                    help="GGUF quantization method")
    args = ap.parse_args()

    data = load_dataset(args.dataset)
    if len(data) < 10:
        print(f"Warning: only {len(data)} examples — consider collecting more "
              "conversation data before fine-tuning (aim for 100+).")

    try:
        from unsloth import FastLanguageModel
        from trl import SFTTrainer
        from transformers import TrainingArguments
        from datasets import Dataset
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Run: pip install 'unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git'")
        print("     pip install --no-deps trl peft accelerate bitsandbytes")
        sys.exit(1)

    print(f"Loading {args.base_model} ...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.base_model,
        max_seq_length=args.max_seq_length,
        dtype=None,         # auto: bfloat16 on Ampere+, float16 otherwise
        load_in_4bit=True,
    )

    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_rank,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        lora_alpha=args.lora_rank * 2,
        lora_dropout=0.05,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    # Format each example to a flat string the trainer can use
    formatted = [{"text": sharegpt_to_hf(ex, tokenizer)} for ex in data]
    dataset = Dataset.from_list(formatted)

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=args.max_seq_length,
        dataset_num_proc=2,
        args=TrainingArguments(
            per_device_train_batch_size=args.batch_size,
            gradient_accumulation_steps=args.grad_accum,
            warmup_steps=max(5, len(data) // 20),
            num_train_epochs=args.epochs,
            learning_rate=2e-4,
            fp16=True,
            logging_steps=10,
            output_dir=args.output_dir,
            save_strategy="epoch",
            optim="adamw_8bit",
            report_to="none",
        ),
    )

    print(f"Training for {args.epochs} epoch(s) on {len(data)} examples ...")
    trainer.train()

    model.save_pretrained(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    print(f"Adapter saved → {args.output_dir}/")

    # Export to GGUF so Ollama can use it directly
    print(f"Exporting GGUF ({args.quant}) → {args.gguf_dir}/ ...")
    model.save_pretrained_gguf(args.gguf_dir, tokenizer,
                               quantization_method=args.quant)

    # Find the generated GGUF filename
    gguf_files = [f for f in os.listdir(args.gguf_dir) if f.endswith(".gguf")]
    gguf_file = gguf_files[0] if gguf_files else "model.gguf"

    # Write Modelfile
    modelfile_path = "Modelfile-ft"
    with open(modelfile_path, "w") as f:
        f.write(MODELFILE_TEMPLATE.format(
            gguf_file=os.path.join(args.gguf_dir, gguf_file)
        ))

    print(f"\nModelfile written → {modelfile_path}")
    print("\n--- Next steps ---")
    print("Copy the gguf dir and Modelfile-ft back to your Mac, then:")
    print(f"  ollama create amy-ft -f {modelfile_path}")
    print("  # In companion.py, set MODEL = 'amy-ft' to use it")


if __name__ == "__main__":
    main()

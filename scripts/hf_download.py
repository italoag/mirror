#!/usr/bin/env python3
"""Baixa do Hugging Face apenas os arquivos listados no plano.

Usa `snapshot_download` da biblioteca oficial em vez de `git clone` + `git lfs
pull`: o clone guarda cada peso duas vezes (uma em `.git/lfs`, outra na árvore
de trabalho), o que estoura o disco do runner em qualquer modelo sério.

Chamamos a API Python direto porque o nome do executável mudou ao longo do
tempo (`huggingface-cli` → `hf`), enquanto `snapshot_download` é estável.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from huggingface_hub import snapshot_download


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", default="plan.json")
    parser.add_argument("--dest", required=True)
    parser.add_argument(
        "--set",
        choices=("model", "snapshot"),
        required=True,
        help="model = só o GGUF escolhido (+projetor); snapshot = layout completo do HF",
    )
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    with open(args.plan, encoding="utf-8") as fh:
        plan = json.load(fh)

    if args.set == "model":
        patterns = list(plan["gguf_files"]) + list(plan["projector_files"])
        if not patterns:
            # Modelo sem GGUF: o Ollama importa os safetensors direto do diretório.
            patterns = list(plan["snapshot_files"])
    else:
        patterns = list(plan["snapshot_files"])

    if not patterns:
        raise SystemExit("o plano não lista nenhum arquivo para baixar")

    print(f"⬇️  baixando {len(patterns)} arquivos de {plan['model_repo']}@{plan['revision']}", flush=True)
    path = snapshot_download(
        repo_id=plan["model_repo"],
        revision=plan["revision"],
        local_dir=args.dest,
        allow_patterns=patterns,
        max_workers=args.workers,
        token=os.environ.get("HF_TOKEN") or None,
    )
    total = 0
    for root, _, names in os.walk(path):
        for name in names:
            if ".cache" in root:
                continue
            total += os.path.getsize(os.path.join(root, name))
    print(f"✅ {total / (1 << 30):.2f} GiB em {path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

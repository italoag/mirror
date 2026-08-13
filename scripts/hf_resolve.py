#!/usr/bin/env python3
"""Monta o plano de publicação de um repositório do Hugging Face no GHCR.

Consulta a árvore de arquivos do repositório, escolhe o conjunto GGUF a
publicar (lidando com modelos divididos em shards), deriva o nome do pacote e a
tag no GHCR e grava tudo em um `plan.json` consumido pelos jobs seguintes do
workflow.

Resolver isso antes de baixar qualquer coisa evita o caso clássico de gastar
vinte minutos puxando 40 GB para descobrir no fim que o padrão de arquivo
casava com seis quantizações diferentes.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

HF_API = "https://huggingface.co/api"
USER_AGENT = "ghcr-model-mirror/1.0"

SHARD_RE = re.compile(r"^(?P<base>.+?)-(?P<index>\d{5})-of-(?P<total>\d{5})\.gguf$", re.IGNORECASE)

# Reconhece o rótulo de quantização no nome do arquivo (Q4_K_M, IQ3_XXS, F16...).
QUANT_RE = re.compile(
    r"(?:^|[-_.])(IQ\d+[A-Z0-9_]*|Q\d+(?:_[A-Z0-9]+)*|BF16|F16|F32|FP8|MXFP4)(?:$|[-_.])",
    re.IGNORECASE,
)

# Ordem de preferência quando o repositório traz várias quantizações e o usuário
# não pediu uma específica: equilíbrio entre qualidade e tamanho primeiro.
QUANT_PREFERENCE = [
    "q4_k_m",
    "q4_k_s",
    "q5_k_m",
    "q5_k_s",
    "q6_k",
    "q8_0",
    "q4_0",
    "q3_k_m",
    "iq4_xs",
    "f16",
    "bf16",
]

# Arquivos que nunca precisam ir para o registro.
DEFAULT_EXCLUDES = [".gitattributes", ".gitignore", "README.md", "original/*", ".huggingface/*"]


def log(msg: str) -> None:
    print(msg, flush=True)


def http_get(url: str, token: str = "") -> tuple[int, bytes, dict]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def next_link(link_header: str) -> str:
    for part in (link_header or "").split(","):
        section = part.split(";")
        if len(section) < 2:
            continue
        if 'rel="next"' in section[1].replace(" ", "").replace("'", '"'):
            return section[0].strip().strip("<>")
    return ""


def list_files(repo: str, revision: str, token: str) -> list[dict]:
    """Lista recursivamente os arquivos do repositório, seguindo a paginação."""
    url = f"{HF_API}/models/{repo}/tree/{urllib.parse.quote(revision, safe='')}?recursive=true"
    files: list[dict] = []
    while url:
        status, body, headers = http_get(url, token)
        if status == 401 or status == 403:
            raise SystemExit(
                f"acesso negado ao repositório {repo} (HTTP {status}). "
                "Se o modelo é gated, configure o secret HF_TOKEN com um token que aceitou a licença."
            )
        if status == 404:
            raise SystemExit(f"repositório {repo} (revisão {revision}) não encontrado no Hugging Face")
        if status != 200:
            raise SystemExit(f"erro ao listar {repo}: HTTP {status} {body[:300]!r}")
        for entry in json.loads(body.decode("utf-8")):
            if entry.get("type") != "file":
                continue
            lfs = entry.get("lfs") or {}
            files.append(
                {
                    "path": entry["path"],
                    "size": int(lfs.get("size") or entry.get("size") or 0),
                }
            )
        url = next_link(headers.get("Link", ""))
    return files


def matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(os.path.basename(path), pattern)


def excluded(path: str, patterns: list[str]) -> bool:
    return any(matches(path, p) for p in patterns if p)


def group_key(path: str) -> str:
    """Junta os shards `...-00001-of-00003.gguf` sob uma única chave lógica."""
    match = SHARD_RE.match(os.path.basename(path))
    if not match:
        return path
    directory = os.path.dirname(path)
    base = f"{match.group('base')}.gguf"
    return os.path.join(directory, base) if directory else base


def quant_label(path: str) -> str:
    name = os.path.basename(path)
    name = SHARD_RE.sub(lambda m: f"{m.group('base')}.gguf", name)
    found = QUANT_RE.findall(name)
    return found[-1].lower() if found else ""


def sanitize(value: str, fallback: str) -> str:
    """Normaliza para algo aceito ao mesmo tempo pelo GHCR e pelo parser do Ollama."""
    value = value.lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value).strip("-._")
    value = re.sub(r"-{2,}", "-", value)
    if not value or not re.match(r"^[a-z0-9_]", value):
        value = f"m{value}" if value else fallback
    return value[:96]


def is_projector(path: str) -> bool:
    return "mmproj" in os.path.basename(path).lower()


def choose_gguf(files: list[dict], pattern: str) -> tuple[list[dict], list[str]]:
    """Escolhe um grupo GGUF. Devolve (arquivos escolhidos, grupos alternativos)."""
    candidates = [
        f
        for f in files
        if f["path"].lower().endswith(".gguf")
        and matches(f["path"], pattern)
        and not is_projector(f["path"])
    ]
    if not candidates:
        return [], []

    groups: dict[str, list[dict]] = {}
    for f in candidates:
        groups.setdefault(group_key(f["path"]), []).append(f)
    for shards in groups.values():
        shards.sort(key=lambda f: f["path"])

    keys = sorted(groups)
    if len(keys) == 1:
        return groups[keys[0]], []

    # Várias quantizações casaram: escolhe pela ordem de preferência e informa o
    # resto, para o usuário poder refazer com um file_pattern mais estreito. Em
    # caso de empate vence o menor conjunto, que é o que cabe no runner.
    ranked = sorted(
        keys,
        key=lambda k: (
            QUANT_PREFERENCE.index(quant_label(k))
            if quant_label(k) in QUANT_PREFERENCE
            else len(QUANT_PREFERENCE),
            sum(f["size"] for f in groups[k]),
            k,
        ),
    )
    return groups[ranked[0]], ranked[1:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-repo", required=True, help="ex: bartowski/Qwen2.5-7B-Instruct-GGUF")
    parser.add_argument("--revision", default="main")
    parser.add_argument("--file-pattern", default="*.gguf")
    parser.add_argument("--package-name", default="")
    parser.add_argument("--tag", default="")
    parser.add_argument("--owner", required=True, help="dono do pacote no GHCR")
    parser.add_argument("--exclude", default="", help="padrões extras separados por vírgula")
    parser.add_argument("--output", default="plan.json")
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    args = parser.parse_args()

    token = os.environ.get("HF_TOKEN", "")
    repo = args.model_repo.strip().strip("/")
    if repo.count("/") != 1:
        raise SystemExit(f"model_repo deve ser 'owner/nome', recebido {repo!r}")

    files = list_files(repo, args.revision, token)
    if not files:
        raise SystemExit(f"nenhum arquivo encontrado em {repo}@{args.revision}")
    log(f"📚 {len(files)} arquivos em {repo}@{args.revision}")

    excludes = list(DEFAULT_EXCLUDES) + [p.strip() for p in args.exclude.split(",") if p.strip()]
    files = [f for f in files if not excluded(f["path"], excludes)]

    gguf_group, alternatives = choose_gguf(files, args.file_pattern)
    projectors = [f for f in files if f["path"].lower().endswith(".gguf") and is_projector(f["path"])]

    if gguf_group:
        log(f"🧩 GGUF escolhido: {', '.join(f['path'] for f in gguf_group)}")
        if alternatives:
            log("   outras quantizações disponíveis (use file_pattern para escolher):")
            for alt in alternatives:
                log(f"     - {os.path.basename(alt)}")
    else:
        log(f"ℹ️  nenhum GGUF casou com {args.file_pattern!r}")

    safetensors = [f for f in files if f["path"].lower().endswith(".safetensors")]

    # O snapshot serve aos clientes que esperam o layout do Hugging Face
    # (MLX, transformers, LM Studio, llama.cpp). Ele leva tudo que não é GGUF,
    # mais o GGUF escolhido — sem arrastar as outras quantizações.
    chosen_gguf = {f["path"] for f in gguf_group} | {f["path"] for f in projectors}
    snapshot = [
        f
        for f in files
        if not f["path"].lower().endswith(".gguf") or f["path"] in chosen_gguf
    ]

    package_default = repo.split("/")[-1]
    package = sanitize(args.package_name or package_default, "modelo")

    if args.tag:
        tag = sanitize(args.tag, "latest")
    else:
        label = quant_label(gguf_group[0]["path"]) if gguf_group else ""
        if not label and safetensors:
            label = "safetensors"
        tag = sanitize(label or "latest", "latest")

    owner = sanitize(args.owner, "owner")
    plan = {
        "model_repo": repo,
        "model_url": f"https://huggingface.co/{repo}",
        "revision": args.revision,
        "owner": owner,
        "package": package,
        "repository": f"{owner}/{package}",
        "tag": tag,
        "hf_tag": f"{tag}-hf",
        "image": f"ghcr.io/{owner}/{package}:{tag}",
        "hf_image": f"ghcr.io/{owner}/{package}:{tag}-hf",
        "has_gguf": bool(gguf_group),
        "gguf_files": [f["path"] for f in gguf_group],
        "projector_files": [f["path"] for f in projectors],
        "gguf_bytes": sum(f["size"] for f in gguf_group) + sum(f["size"] for f in projectors),
        "has_safetensors": bool(safetensors),
        "snapshot_files": [f["path"] for f in snapshot],
        "snapshot_bytes": sum(f["size"] for f in snapshot),
        "alternatives": [os.path.basename(a) for a in alternatives],
    }

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2, ensure_ascii=False)
    log(f"📝 plano gravado em {args.output}")
    log(f"   pacote Ollama : {plan['image']}")
    log(f"   snapshot HF   : {plan['hf_image']}  ({len(snapshot)} arquivos)")

    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            for key in (
                "package",
                "repository",
                "tag",
                "hf_tag",
                "image",
                "hf_image",
                "gguf_bytes",
                "snapshot_bytes",
            ):
                fh.write(f"{key}={plan[key]}\n")
            fh.write(f"has_gguf={'true' if plan['has_gguf'] else 'false'}\n")
            fh.write(f"has_safetensors={'true' if plan['has_safetensors'] else 'false'}\n")
            fh.write(f"has_snapshot={'true' if plan['snapshot_files'] else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

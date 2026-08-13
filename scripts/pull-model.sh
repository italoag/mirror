#!/usr/bin/env bash
# Baixa do GHCR um modelo publicado pelos workflows deste repositório e o
# entrega no formato que cada cliente espera.
#
#   ./scripts/pull-model.sh ghcr.io/italoag/qwen3:q4_k_m          --for ollama
#   ./scripts/pull-model.sh ghcr.io/italoag/qwen3:q4_k_m-hf       --for lmstudio
#   ./scripts/pull-model.sh ghcr.io/italoag/qwen3-4bit:latest-hf  --for mlx
#   ./scripts/pull-model.sh ghcr.io/italoag/qwen3:q4_k_m-hf       --out ./modelo
#
# Só precisa de bash + python3. Se o `oras` estiver instalado ele é usado, por
# ser mais rápido; caso contrário o download cai no scripts/ghcr_ollama.py.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="${SCRIPT_DIR}/ghcr_ollama.py"

usage() {
  sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Opções:
  --for <alvo>     ollama | lmstudio | hf | mlx      (padrão: hf)
  --out <dir>      diretório de saída
  --user <user>    usuário do GHCR (pacotes privados)
  --token <token>  token do GHCR (ou a variável GHCR_TOKEN)
  -h, --help       esta ajuda
EOF
}

REFERENCE=""
TARGET="hf"
OUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --for)   TARGET="$2"; shift 2 ;;
    --out)   OUT="$2"; shift 2 ;;
    --user)  export GHCR_USERNAME="$2"; shift 2 ;;
    --token) export GHCR_TOKEN="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "opção desconhecida: $1" >&2; usage >&2; exit 2 ;;
    *)
      if [[ -n "$REFERENCE" ]]; then
        echo "referência informada duas vezes: $REFERENCE e $1" >&2
        exit 2
      fi
      REFERENCE="$1"; shift ;;
  esac
done

if [[ -z "$REFERENCE" ]]; then
  usage >&2
  exit 2
fi

# ghcr.io/<owner>/<pacote>:<tag>
if [[ "$REFERENCE" != ghcr.io/* ]]; then
  echo "❌ a referência precisa começar com ghcr.io/ — recebido: $REFERENCE" >&2
  exit 2
fi
REST="${REFERENCE#ghcr.io/}"
TAG="latest"
if [[ "${REST##*/}" == *:* ]]; then
  TAG="${REST##*:}"
  REST="${REST%:*}"
fi
REPOSITORY="$REST"
PACKAGE="${REPOSITORY##*/}"

if [[ "$REPOSITORY" == */*/* ]]; then
  echo "⚠️  '$REPOSITORY' tem mais de dois níveis; o Ollama não consegue puxar" >&2
  echo "    referências assim — use ghcr.io/<owner>/<pacote>:<tag>." >&2
fi

fetch_artifact() {  # $1 = tag, $2 = destino
  local tag="$1" dest="$2"
  mkdir -p "$dest"
  if command -v oras >/dev/null 2>&1; then
    if [[ -n "${GHCR_TOKEN:-}" ]]; then
      printf '%s' "$GHCR_TOKEN" | oras login ghcr.io \
        --username "${GHCR_USERNAME:-x-access-token}" --password-stdin
    fi
    oras pull "ghcr.io/${REPOSITORY}:${tag}" --output "$dest"
  else
    python3 "$HELPER" pull --repository "$REPOSITORY" --tag "$tag" --dest "$dest"
  fi
}

case "$TARGET" in
  ollama)
    if ! command -v ollama >/dev/null 2>&1; then
      echo "❌ ollama não encontrado no PATH" >&2
      exit 1
    fi
    echo "🦙 ollama pull ${REFERENCE}"
    if ollama pull "$REFERENCE"; then
      echo "✅ pronto: ollama run ${REFERENCE}"
      exit 0
    fi
    # O `ollama pull` só usa token anônimo em registros de terceiros: se o
    # pacote for privado, ou se o Ollama for antigo demais para seguir o
    # redirect do GHCR até a CDN, montamos o modelo a partir do snapshot HF.
    echo "⚠️  pull direto falhou; reconstruindo a partir do snapshot HF (${TAG}-hf)" >&2
    WORK="$(mktemp -d)"
    trap 'rm -rf "$WORK"' EXIT
    fetch_artifact "${TAG}-hf" "$WORK"
    mapfile -t GGUFS < <(find "$WORK" -name '*.gguf' | sort)
    if [[ ${#GGUFS[@]} -eq 0 ]]; then
      echo "❌ o snapshot não contém nenhum .gguf para importar" >&2
      exit 1
    fi
    : > "$WORK/Modelfile"
    for f in "${GGUFS[@]}"; do
      echo "FROM $f" >> "$WORK/Modelfile"
    done
    ollama create "$REFERENCE" -f "$WORK/Modelfile"
    echo "✅ pronto: ollama run ${REFERENCE}"
    ;;

  lmstudio)
    DEST="${OUT:-$HOME/.lmstudio/models/${REPOSITORY}}"
    fetch_artifact "$TAG" "$DEST"
    echo "✅ modelo em ${DEST}"
    echo "   O LM Studio o lista após um 'Refresh' na aba My Models."
    echo "   Pela CLI: lms ls   →   lms load ${REPOSITORY}"
    ;;

  mlx)
    DEST="${OUT:-./${PACKAGE}}"
    fetch_artifact "$TAG" "$DEST"
    echo "✅ modelo em ${DEST}"
    echo "   mlx_lm.generate --model ${DEST} --prompt 'olá'"
    ;;

  hf)
    DEST="${OUT:-./${PACKAGE}}"
    fetch_artifact "$TAG" "$DEST"
    echo "✅ modelo em ${DEST} (layout do Hugging Face)"
    echo "   transformers: AutoModelForCausalLM.from_pretrained('${DEST}')"
    echo "   llama.cpp:    llama-cli -m ${DEST}/<arquivo>.gguf"
    ;;

  *)
    echo "❌ alvo desconhecido: ${TARGET} (use ollama, lmstudio, hf ou mlx)" >&2
    exit 2 ;;
esac

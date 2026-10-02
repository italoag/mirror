#!/usr/bin/env bash
# Mapeia uma imagem para o seu espelho no GHCR e baixa o espelho com o nome
# original, para que manifests e charts não precisem mudar.
#
#   scripts/image-mirror.sh resolve <imagem> <tag>    SOURCE_IMAGE=/GHCR_IMAGE= (usado pelo image_sync.yml)
#   scripts/image-mirror.sh resolve <imagem[:tag]>
#   scripts/image-mirror.sh pull <imagem[:tag]>...    docker pull do espelho + docker tag com o nome original
#
# Docker Hub mantém o caminho como foi escrito:
#   grafana/mimir:3.0.1           → ghcr.io/italoag/mirror/grafana/mimir:3.0.1
# Outros registros entram no caminho, sem colidir com nomes do Docker Hub:
#   quay.io/strimzi/kafka:0.49.1  → ghcr.io/italoag/mirror/quay.io/strimzi/kafka:0.49.1
#
# O `pull` só resolve no Kubernetes com `imagePullPolicy: IfNotPresent`: com
# `Always` o kubelet consulta o registro original mesmo com a imagem no cache.
set -euo pipefail

MIRROR="ghcr.io/italoag/mirror"

die() { echo "$*" >&2; exit 1; }

usage() { sed -n '2,15s/^# \{0,1\}//p' "$0" >&2; exit 2; }

resolve() {
  local name="$1" tag="${2:-}" registry repo
  if [[ -z "$tag" ]]; then
    [[ "$name" != *@* ]] || die "digest não suportado: $name (use uma tag)"
    tag=latest
    if [[ "${name##*/}" == *:* ]]; then tag="${name##*:}"; name="${name%:*}"; fi
  fi
  registry="${name%%/*}"
  if [[ "$name" == */* && ( "$registry" == *.* || "$registry" == *:* || "$registry" == localhost ) ]]; then
    repo="${name#*/}"
  else
    registry=docker.io repo="$name"
  fi
  case "$registry" in
    docker.io|index.docker.io|registry-1.docker.io)
      [[ "$repo" == */* ]] && echo "SOURCE_IMAGE=docker.io/$repo:$tag" || echo "SOURCE_IMAGE=docker.io/library/$repo:$tag"
      echo "GHCR_IMAGE=$MIRROR/$repo:$tag" ;;
    *)
      echo "SOURCE_IMAGE=$registry/$repo:$tag"
      echo "GHCR_IMAGE=$MIRROR/$registry/$repo:$tag" ;;
  esac
}

pull() {
  local ref ghcr
  for ref in "$@"; do
    ghcr="$(resolve "$ref" | sed -n 's/^GHCR_IMAGE=//p')"
    # A tag local é a referência exatamente como o cluster a escreve: o Docker
    # trata registry-1.docker.io/x e docker.io/x como repositórios diferentes.
    [[ "${ref##*/}" == *:* ]] || ref="$ref:latest"
    docker pull "$ghcr"
    docker tag "$ghcr" "$ref"
    echo "✓ $ref ← $ghcr"
  done
}

case "${1:-}" in
  resolve) [[ $# -eq 2 || $# -eq 3 ]] || usage; shift; resolve "$@" ;;
  pull)    [[ $# -ge 2 ]] || usage; shift; pull "$@" ;;
  *)       usage ;;
esac

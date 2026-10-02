#!/usr/bin/env bash
# Regressão do mapeamento origem → espelho no GHCR (scripts/image-mirror.sh resolve).
set -euo pipefail
cd "$(dirname "$0")/.."

falhas=0
caso() {  # caso "<argumentos do resolve>" <SOURCE_IMAGE esperado> <GHCR_IMAGE esperado>
  local saida esperado
  # shellcheck disable=SC2086 # o primeiro argumento carrega 1 ou 2 palavras de propósito
  saida="$(bash scripts/image-mirror.sh resolve $1)"
  esperado="SOURCE_IMAGE=$2"$'\n'"GHCR_IMAGE=$3"
  if [[ "$saida" == "$esperado" ]]; then
    echo "ok $1"
  else
    printf 'FALHOU %s\n  esperado: %s\n  obtido:   %s\n' "$1" "${esperado//$'\n'/ | }" "${saida//$'\n'/ | }"
    falhas=1
  fi
}

M=ghcr.io/italoag/mirror

# Docker Hub: o caminho no GHCR fica como foi escrito (compatível com os espelhos existentes).
caso "ethereum/client-go release-1.10" docker.io/ethereum/client-go:release-1.10 $M/ethereum/client-go:release-1.10
caso "mongo 8.0.8" docker.io/library/mongo:8.0.8 $M/mongo:8.0.8
caso "library/busybox 1.31.1" docker.io/library/busybox:1.31.1 $M/library/busybox:1.31.1
caso "docker.io/grafana/loki 3.6.5" docker.io/grafana/loki:3.6.5 $M/grafana/loki:3.6.5
caso "registry-1.docker.io/bitnami/redis latest" docker.io/bitnami/redis:latest $M/bitnami/redis:latest

# Outros registros: o host entra no caminho, sem colidir com nomes do Docker Hub.
caso "quay.io/strimzi/kafka 0.49.1-kafka-4.0.0" quay.io/strimzi/kafka:0.49.1-kafka-4.0.0 $M/quay.io/strimzi/kafka:0.49.1-kafka-4.0.0
caso "registry.k8s.io/pause 3.10" registry.k8s.io/pause:3.10 $M/registry.k8s.io/pause:3.10

# Referência única (imagem:tag), como aparece no cluster.
caso "quay.io/mongodb/mongodb-agent-ubi:108.0.6.8796-1-arm64" quay.io/mongodb/mongodb-agent-ubi:108.0.6.8796-1-arm64 $M/quay.io/mongodb/mongodb-agent-ubi:108.0.6.8796-1-arm64
caso "docker.io/mongo:8.0.8" docker.io/library/mongo:8.0.8 $M/mongo:8.0.8
caso "grafana/mimir" docker.io/grafana/mimir:latest $M/grafana/mimir:latest

exit $falhas

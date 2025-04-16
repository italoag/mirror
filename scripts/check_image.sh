#!/bin/bash

# Script para verificar e sincronizar manualmente imagens Docker
# Uso: ./check_image.sh postgres 14.7

IMAGE_NAME=$1
IMAGE_TAG=$2

if [ -z "$IMAGE_NAME" ] || [ -z "$IMAGE_TAG" ]; then
  echo "Uso: ./check_image.sh <nome_imagem> <tag>"
  exit 1
fi

# Verificar no GHCR
echo "Verificando imagem no GHCR: $IMAGE_NAME:$IMAGE_TAG"
STATUS_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $GHCR_PAT" \
  "https://ghcr.io/v2/italoag/public/$IMAGE_NAME/manifests/$IMAGE_TAG")

if [ "$STATUS_CODE" == "200" ]; then
  echo "✅ Imagem encontrada no GHCR"
  exit 0
else
  echo "❌ Imagem não encontrada no GHCR (Status: $STATUS_CODE)"
  
  # Verificar no Docker Hub
  echo "Verificando imagem no Docker Hub: $IMAGE_NAME:$IMAGE_TAG"
  HUB_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    "https://hub.docker.com/v2/repositories/library/$IMAGE_NAME/tags/$IMAGE_TAG")
  
  if [ "$HUB_STATUS" == "200" ]; then
    echo "✅ Imagem encontrada no Docker Hub, iniciando sincronização"
    
    # Disparar workflow via API (se token disponível)
    if [ -n "$GITHUB_TOKEN" ]; then
      echo "🔄 Disparando workflow de sincronização via API"
      curl -X POST \
        -H "Authorization: token $GITHUB_TOKEN" \
        -H "Accept: application/vnd.github.everest-preview+json" \
        -H "Content-Type: application/json" \
        --data "{\"event_type\": \"image_sync_requested\", \"client_payload\": {\"image_name\": \"$IMAGE_NAME\", \"image_tag\": \"$IMAGE_TAG\"}}" \
        "https://api.github.com/repos/italoag/public/dispatches"
    else
      echo "⚠️ GITHUB_TOKEN não definido, não é possível disparar workflow"
    fi
  else
    echo "❌ Imagem não encontrada no Docker Hub (Status: $HUB_STATUS)"
  fi
fi

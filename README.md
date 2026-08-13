# mirror

Espelha modelos de LLM para o **GitHub Container Registry (GHCR)** em formatos
que os clientes realmente conseguem consumir.

Cada modelo é publicado em duas referências:

| Referência | Formato | Quem consome |
|---|---|---|
| `ghcr.io/<owner>/<pacote>:<tag>` | manifesto nativo do Ollama | `ollama pull` |
| `ghcr.io/<owner>/<pacote>:<tag>-hf` | artefato OCI com o layout do Hugging Face | LM Studio, MLX, llama.cpp, `transformers`, vLLM, e agentes que carregam um diretório local |

## 📚 [Documentação completa →](docs/)

| Guia | Assunto |
|---|---|
| [1. Publicar um modelo](docs/01-publicar.md) | os dois workflows, todos os inputs, o que acontece em cada job |
| [2. Deixar o pacote público](docs/02-visibilidade.md) | **leia antes da primeira execução** — a escolha é irreversível |
| [3. Usar nos clientes](docs/03-clientes.md) | Ollama, LM Studio, MLX, llama.cpp, transformers, agentes |
| [4. Escolher o modelo certo](docs/04-escolher-modelo.md) | qual repo do HF espelhar, quantização, o que cabe na sua RAM |
| [5. Erros comuns](docs/05-erros-comuns.md) | diagnóstico por sintoma |

---

## Começando

**1. Publique** — *Actions → Sync HF Model to GHCR → Run workflow*:

```
model_repo   = bartowski/Qwen2.5-7B-Instruct-GGUF
package_name = models
```

**2. Torne o pacote público, uma vez** — perfil → aba **Packages** → `models` →
**Package settings** → **Danger Zone** → **Change visibility** → **Public**.

Isso é obrigatório: o `ollama pull` só usa token anônimo em registros de
terceiros. A visibilidade do pacote **não** é herdada do repositório, e não
existe API para mudá-la. Detalhes e a estratégia para fazer isso uma única vez
na vida estão no [guia 2](docs/02-visibilidade.md).

**3. Use:**

```bash
ollama pull ghcr.io/<owner>/models:qwen2.5-7b-instruct-gguf-q4_k_m

# outros clientes
./scripts/pull-model.sh ghcr.io/<owner>/models:qwen2.5-7b-instruct-gguf-q4_k_m-hf --for lmstudio
```

---

## Compatibilidade, em uma tabela

O espelhamento copia fielmente o formato de origem — ele não converte nada.
Então quem decide a compatibilidade é o repositório que você escolhe no Hugging
Face:

| Origem no HF | Ollama | llama.cpp | LM Studio | MLX | transformers |
|---|:--:|:--:|:--:|:--:|:--:|
| `*-GGUF` (bartowski, unsloth…) | ✅ | ✅ | ✅ | ❌ | ❌ |
| `mlx-community/*` | ⚠️ | ❌ | ✅ | ✅ | ✅ |
| safetensors fp16/bf16 | ⚠️ | ❌ | ❌ | ✅ | ✅ |
| AWQ / GPTQ / MXFP4 | ⚠️ | ❌ | ❌ | ✅ | ✅ |
| bitsandbytes 4/8-bit | ⚠️ | ❌ | ❌ | ❌ | ✅ |

**MLX não lê GGUF** — para cobrir Apple Silicon inteiro, espelhe dois repos: um
`*-GGUF` e o `mlx-community/*` correspondente. O job `plan` publica essa tabela
já resolvida para o seu modelo no resumo de cada execução.

**Apple Silicon funciona sem configuração:** os artefatos são pesos, não
binários — não declaram arquitetura de CPU. Veja
[Apple Silicon](docs/03-clientes.md#apple-silicon).

---

## Workflows

| Workflow | Uso |
|---|---|
| `hf-model-to-ghcr.yml` | publica um modelo do Hugging Face |
| `ollama-model-to-ghcr.yml` | espelha um modelo da biblioteca pública do Ollama, em streaming |
| `image_sync.yml` | espelha imagens Docker do Docker Hub (multi-arquitetura, via `skopeo`) |
| `helm_chart_sync.yml` | espelha Helm charts |

## Scripts

| Script | Papel |
|---|---|
| `scripts/ghcr_ollama.py` | cliente do OCI Distribution API: `exists`, `push`, `mirror`, `pull`, `verify` |
| `scripts/hf_resolve.py` | lê o repo HF e monta o `plan.json` (arquivos, pacote, tag, compatibilidade) |
| `scripts/hf_download.py` | baixa só os arquivos do plano via `snapshot_download` |
| `scripts/pull-model.sh` | helper de download para cada cliente |
| `scripts/check_image.sh` | verificação manual de imagens Docker comuns |

## Secrets

| Secret | Necessário | Para quê |
|---|---|---|
| `GITHUB_TOKEN` | automático | publicar no GHCR; vincula o pacote a este repositório |
| `GHCR_PAT` | opcional | PAT com `write:packages`; tem precedência quando existe |
| `HF_TOKEN` | opcional | modelos *gated* no Hugging Face (Llama, Gemma…) |

Nenhum deles afeta a visibilidade do pacote — ela é sempre privada no primeiro
push.

---

## Por que não é uma imagem Docker

O formato do Ollama é um manifesto
`application/vnd.docker.distribution.manifest.v2+json` cujas camadas usam
`application/vnd.ollama.image.model`, `.template`, `.params`, `.license` e
`.system`. Um `docker build` com `FROM scratch` e `COPY` produz camadas
`tar+gzip` comuns: o registro aceita, mas o `ollama pull` não reconhece nada
ali. Por isso o modelo é montado pelo próprio `ollama create` e enviado ao
registro por `scripts/ghcr_ollama.py`.

Também importa o **formato da referência**: o Ollama só entende
`host/namespace/modelo`. `ghcr.io/<owner>/mirror/<modelo>:<tag>` tem um nível a
mais e não é sequer parseável — daí os pacotes ficarem em
`ghcr.io/<owner>/<modelo>:<tag>`.

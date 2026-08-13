# mirror

Espelha modelos de LLM para o **GitHub Container Registry (GHCR)** em formatos
que os clientes realmente conseguem consumir.

Cada modelo é publicado em duas referências:

| Referência | Formato | Quem consome |
|---|---|---|
| `ghcr.io/<owner>/<pacote>:<tag>` | manifesto nativo do Ollama | `ollama pull` |
| `ghcr.io/<owner>/<pacote>:<tag>-hf` | artefato OCI com o layout do Hugging Face | LM Studio, MLX, llama.cpp, `transformers`, e qualquer agente que carregue um diretório local (OpenClaw, Pi, Hermes Agent…) |

---

## Workflows

### `Sync HF Model to GHCR`

Publica um repositório do Hugging Face. Informe o repo e rode:

| Input | Padrão | O que faz |
|---|---|---|
| `model_repo` | — | `bartowski/Qwen2.5-7B-Instruct-GGUF` |
| `revision` | `main` | branch, tag ou commit |
| `file_pattern` | `*.gguf` | qual quantização publicar (ex: `*Q4_K_M*.gguf`) |
| `package_name` | nome do repo HF | nome do pacote no GHCR |
| `tag` | quantização detectada | tag no GHCR (ex: `q4_k_m`) |
| `publish_ollama` | `true` | publica o formato nativo do Ollama |
| `publish_hf_snapshot` | `true` | publica o snapshot HF como artefato OCI |
| `exclude` | — | padrões extras a ignorar |
| `force_resync` | `false` | republica mesmo se a tag existir |

Quando o repositório traz várias quantizações e `file_pattern` fica no padrão, o
workflow escolhe uma (preferindo `Q4_K_M`) e lista as demais no log — daí é só
rodar de novo com um padrão mais estreito.

Repositórios **sem GGUF** (safetensors puro, incluindo os `mlx-community`)
continuam funcionando: o snapshot HF é publicado normalmente e o Ollama tenta
importar os safetensors direto, o que funciona nas arquiteturas que ele suporta.

### `Sync Ollama Model to GHCR`

Espelha um modelo da biblioteca pública do Ollama (`qwen3:8b`,
`deepseek-r1:7b`, …). A cópia é feita em streaming, preservando o manifesto
original — nada é reconstruído, então o resultado é idêntico ao upstream.

---

## Como baixar

O helper cobre todos os casos e só precisa de `bash` + `python3` (usa `oras`
quando disponível, por ser mais rápido):

```bash
./scripts/pull-model.sh ghcr.io/italoag/qwen3:q4_k_m       --for ollama
./scripts/pull-model.sh ghcr.io/italoag/qwen3:q4_k_m-hf    --for lmstudio
./scripts/pull-model.sh ghcr.io/italoag/qwen3:q4_k_m-hf    --for mlx
./scripts/pull-model.sh ghcr.io/italoag/qwen3:q4_k_m-hf    --out ./modelo
```

Para pacotes privados: `--user <usuário> --token <PAT com read:packages>`.

### Ollama

```bash
ollama pull ghcr.io/italoag/qwen3:q4_k_m
ollama run  ghcr.io/italoag/qwen3:q4_k_m
```

### LM Studio

```bash
./scripts/pull-model.sh ghcr.io/italoag/qwen3:q4_k_m-hf --for lmstudio
lms ls
lms load italoag/qwen3
```

O helper grava em `~/.lmstudio/models/<owner>/<pacote>/`, que é o layout que o
LM Studio varre. A CLI `lms` não baixa de registros OCI — ela lê o diretório
local, então o download é feito pelo helper e o `lms` só carrega.

### MLX

```bash
./scripts/pull-model.sh ghcr.io/italoag/qwen3-4bit:latest-hf --out ./qwen3-4bit
mlx_lm.generate --model ./qwen3-4bit --prompt "olá"
```

### transformers / vLLM / SGLang

```bash
oras pull ghcr.io/italoag/qwen3:latest-hf -o ./qwen3
python -c "from transformers import AutoModelForCausalLM; AutoModelForCausalLM.from_pretrained('./qwen3')"
```

### llama.cpp

```bash
oras pull ghcr.io/italoag/qwen3:q4_k_m-hf -o ./qwen3
llama-cli -m ./qwen3/Qwen3-Q4_K_M.gguf -p "olá"
```

### Agentes (OpenClaw, Pi, Hermes Agent, …)

Esses clientes falam com um servidor compatível com a API da OpenAI, não com o
registro. Puxe o modelo e sirva localmente:

```bash
ollama pull ghcr.io/italoag/qwen3:q4_k_m
ollama serve            # expõe http://localhost:11434/v1
```

Aponte o agente para `http://localhost:11434/v1` com o nome do modelo
`ghcr.io/italoag/qwen3:q4_k_m`. Com LM Studio, `lms server start` expõe o mesmo
tipo de endpoint em `http://localhost:1234/v1`.

---

## O pacote precisa ser público

O `ollama pull` só obtém **token anônimo** em registros de terceiros — ele não
tem como enviar credenciais do GHCR. Um pacote privado responde 403 e o pull
falha, mesmo com o manifesto correto.

**A visibilidade do pacote é independente da do repositório.** Ter o repositório
público não basta: pacotes novos nascem **privados**, e a documentação do GitHub
é explícita de que o pacote herda *as permissões de acesso, mas não a
visibilidade* do repositório vinculado. Não existe endpoint REST (só há `GET`,
`DELETE` e `POST /restore` em `/user/packages/...`) nem mutation GraphQL
(só `deletePackageVersion`) para mudar visibilidade. Ou seja: **não dá para
automatizar** — é a interface web ou nada.

O que dá para fazer é pagar esse custo **uma única vez**:

1. Publique todos os modelos como **tags de um mesmo pacote**, passando o mesmo
   `package_name` em toda execução:

   ```
   model_repo   = bartowski/Qwen2.5-7B-Instruct-GGUF
   package_name = models
   ```

   Isso gera `ghcr.io/italoag/models:qwen2.5-7b-instruct-gguf-q4_k_m`. Quando
   `package_name` difere do nome derivado do repo HF, o nome do modelo entra na
   tag automaticamente, então não há colisão entre modelos.

2. Torne `models` público uma vez: **perfil → aba Packages → o pacote →
   Package settings → Danger Zone → Change visibility → Public**. Toda tag
   publicada depois nasce pública, sem passo manual.

   ⚠️ Segundo a documentação do GitHub, tornar um pacote público é
   **irreversível**.

Se preferir um pacote por modelo (`ghcr.io/italoag/qwen3:q4_k_m`, mais bonito no
`ollama list`), o passo manual se repete uma vez por modelo novo — nunca a cada
sincronização. O passo de verificação do workflow detecta o caso e imprime o
caminho exato a seguir.

O snapshot `-hf` também precisa ser público para ser puxado sem credenciais, mas
`oras` e o helper aceitam token — então ele funciona privado.

---

## Secrets

| Secret | Necessário | Para quê |
|---|---|---|
| `GITHUB_TOKEN` | automático | publicar no GHCR; vincula o pacote a este repositório |
| `GHCR_PAT` | opcional | PAT com `write:packages`; tem precedência quando existe |
| `HF_TOKEN` | opcional | modelos *gated* no Hugging Face (Llama, Gemma…) |

Nenhum dos dois afeta a visibilidade do pacote — ela é sempre privada no
primeiro push. A diferença é o vínculo: publicando com o `GITHUB_TOKEN` o
pacote já nasce ligado a este repositório e as workflows daqui ganham acesso
automático a ele.

---

## Scripts

| Script | Papel |
|---|---|
| `scripts/ghcr_ollama.py` | cliente do OCI Distribution API: `exists`, `push`, `mirror`, `pull`, `verify` |
| `scripts/hf_resolve.py` | lê o repo HF e monta o `plan.json` (arquivos, nome do pacote, tag) |
| `scripts/hf_download.py` | baixa só os arquivos do plano via `snapshot_download` |
| `scripts/pull-model.sh` | helper de download para cada cliente |
| `scripts/check_image.sh` | verificação manual de imagens Docker comuns |

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

---

## Outros workflows

| Workflow | Uso |
|---|---|
| `image_sync.yml` | espelha imagens Docker do Docker Hub para o GHCR (multi-arquitetura, via `skopeo`) |
| `helm_chart_sync.yml` | espelha Helm charts |

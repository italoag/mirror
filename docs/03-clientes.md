# 3. Usar nos clientes

Todos os exemplos assumem macOS com Apple Silicon, mas nada aqui é específico de
arquitetura — veja [Apple Silicon](#apple-silicon) no fim.

Substitua `<owner>` pelo seu usuário do GitHub.

---

## O helper

Cobre todos os clientes e só precisa de `bash` + `python3`. Se o `oras` estiver
instalado ele é usado, por ser mais rápido; senão o download cai no
`scripts/ghcr_ollama.py`.

```bash
git clone https://github.com/<owner>/mirror.git
cd mirror

./scripts/pull-model.sh ghcr.io/<owner>/models:qwen3-q4_k_m       --for ollama
./scripts/pull-model.sh ghcr.io/<owner>/models:qwen3-q4_k_m-hf    --for lmstudio
./scripts/pull-model.sh ghcr.io/<owner>/models:qwen3-4bit-hf      --for mlx
./scripts/pull-model.sh ghcr.io/<owner>/models:qwen3-q4_k_m-hf    --out ./modelo
```

Pacote privado: acrescente `--user <usuário> --token <PAT com read:packages>`.

As seções abaixo mostram o que fazer sem o helper, se você preferir.

---

## Ollama

Use a tag **sem** `-hf`.

```bash
brew install ollama          # ou https://ollama.com/download
ollama serve &               # o app do macOS já sobe o serviço sozinho

ollama pull ghcr.io/<owner>/models:qwen2.5-7b-instruct-gguf-q4_k_m
ollama run  ghcr.io/<owner>/models:qwen2.5-7b-instruct-gguf-q4_k_m
```

### Um nome curto

O nome completo é chato de digitar. `ollama cp` cria um alias local:

```bash
ollama cp ghcr.io/<owner>/models:qwen2.5-7b-instruct-gguf-q4_k_m qwen
ollama run qwen
```

### Servidor compatível com OpenAI

O Ollama expõe endpoints OpenAI em `http://localhost:11434/v1`:
`/v1/chat/completions`, `/v1/completions`, `/v1/models`.

```bash
curl http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen","messages":[{"role":"user","content":"olá"}]}'
```

### Variáveis úteis

| Variável | Para quê |
|---|---|
| `OLLAMA_HOST` | Endereço do servidor (padrão `127.0.0.1:11434`) |
| `OLLAMA_MODELS` | Onde guardar os modelos — aponte para um disco externo |
| `OLLAMA_KEEP_ALIVE` | Quanto tempo o modelo fica na memória (padrão `5m`) |
| `OLLAMA_CONTEXT_LENGTH` | Contexto padrão |

---

## LM Studio

Use a tag **`-hf`**. O LM Studio lê tanto GGUF quanto modelos MLX.

Ele varre `~/.lmstudio/models/` esperando a estrutura
`<publisher>/<model>/arquivo`, que é exatamente a que o helper monta:

```bash
./scripts/pull-model.sh ghcr.io/<owner>/models:qwen2.5-7b-instruct-gguf-q4_k_m-hf --for lmstudio
```

Depois, no app: aba **My Models** → o modelo aparece. Pela CLI:

```bash
lms ls                      # lista o que está baixado
lms ls --detailed
lms load <owner>/models     # carrega na memória
lms load <owner>/models --gpu max --context-length 8192 --ttl 3600
lms unload
```

### Alternativa: `lms import`

Se você já baixou o `.gguf` para qualquer lugar, o LM Studio importa direto:

```bash
./scripts/pull-model.sh ghcr.io/<owner>/models:qwen3-q4_k_m-hf --out ./tmp
lms import ./tmp/Qwen2.5-7B-Instruct-Q4_K_M.gguf --user-repo <owner>/qwen2.5-7b -y
```

Flags úteis: `-c/--copy`, `-L/--hard-link`, `-l/--symbolic-link` (economiza
espaço), `--dry-run`.

### Servidor local

```bash
lms server start                       # porta padrão
lms server start --port 1234 --cors
lms server status
lms server stop
```

---

## MLX

Use a tag **`-hf`** — e o modelo de origem **precisa ser safetensors**. O
`mlx-lm` faz `glob("model*.safetensors")` e aborta se não achar. Ele não lê GGUF.
Veja [Escolher o modelo certo](04-escolher-modelo.md).

```bash
pip install mlx-lm

./scripts/pull-model.sh ghcr.io/<owner>/models:qwen3-8b-4bit-safetensors-hf --out ./qwen3-4bit

mlx_lm.generate --model ./qwen3-4bit --prompt "explique o que é MLX"
mlx_lm.chat --model ./qwen3-4bit
```

Os subcomandos existem nas duas formas — `mlx_lm.generate` ou `mlx_lm generate`.

### Servidor compatível com OpenAI

```bash
mlx_lm.server --model ./qwen3-4bit          # porta 8080 por padrão
```

### Modelos grandes na memória unificada

O `mlx-lm` já ajusta sozinho o limite de memória *wired* (precisa de macOS 15+).
Se aparecer:

```
[WARNING] Generating with a model that requires NNNN MB which is close to the
maximum recommended size of MMMM MB. This can be slow.
```

o modelo cabe na RAM, mas está perto do teto que o Metal recomenda. A saída
documentada pelo próprio mlx-lm é subir o limite do sistema:

```bash
sudo sysctl iogpu.wired_limit_mb=N
```

`N` maior que o tamanho do modelo em MB e menor que a RAM total da máquina. Num
Mac de 32 GB, algo como `24576`. O efeito some ao reiniciar.

---

## llama.cpp

Use a tag **`-hf`**, e o modelo precisa ser GGUF.

```bash
brew install llama.cpp

./scripts/pull-model.sh ghcr.io/<owner>/models:qwen3-q4_k_m-hf --out ./qwen3
llama-cli   -m ./qwen3/Qwen2.5-7B-Instruct-Q4_K_M.gguf -p "olá"
llama-server -m ./qwen3/Qwen2.5-7B-Instruct-Q4_K_M.gguf --port 8080
```

Em GGUF dividido em shards, aponte para o **primeiro** (`-00001-of-0000N`); o
llama.cpp acha o resto sozinho.

---

## transformers / vLLM / SGLang

Use a tag **`-hf`**, com modelo safetensors.

```bash
brew install oras
oras pull ghcr.io/<owner>/models:qwen3-8b-safetensors-hf -o ./qwen3
```

```python
from transformers import AutoModelForCausalLM, AutoTokenizer

tok = AutoTokenizer.from_pretrained("./qwen3")
model = AutoModelForCausalLM.from_pretrained("./qwen3", device_map="auto")
```

```bash
vllm serve ./qwen3
```

---

## Agentes (OpenClaw, Pi, Hermes Agent, Continue, Aider…)

Esses clientes **não falam com registros de container** — eles falam com um
servidor compatível com a API da OpenAI. O caminho é: puxe o modelo, suba um
servidor local, aponte o agente para ele.

```bash
ollama pull ghcr.io/<owner>/models:qwen2.5-7b-instruct-gguf-q4_k_m
ollama cp   ghcr.io/<owner>/models:qwen2.5-7b-instruct-gguf-q4_k_m qwen
ollama serve
```

Configure o agente com:

| Campo | Valor |
|---|---|
| Base URL | `http://localhost:11434/v1` |
| API key | qualquer string não vazia (o Ollama ignora) |
| Model | `qwen` |

Alternativas equivalentes:

| Servidor | Base URL |
|---|---|
| Ollama | `http://localhost:11434/v1` |
| LM Studio (`lms server start`) | `http://localhost:1234/v1` |
| MLX (`mlx_lm.server`) | `http://localhost:8080/v1` |
| llama.cpp (`llama-server`) | `http://localhost:8080/v1` |

---

## Pacotes privados

Se você optou por não tornar o pacote público, o `ollama pull` **não vai
funcionar** — não há como passar credenciais. Os demais clientes funcionam, via
o snapshot `-hf`:

```bash
export GHCR_USERNAME=<seu-usuário>
export GHCR_TOKEN=<PAT com read:packages>

./scripts/pull-model.sh ghcr.io/<owner>/models:qwen3-q4_k_m-hf --out ./modelo
```

Com `oras` direto:

```bash
echo "$GHCR_TOKEN" | oras login ghcr.io --username "$GHCR_USERNAME" --password-stdin
oras pull ghcr.io/<owner>/models:qwen3-q4_k_m-hf -o ./modelo
```

Para o Ollama, o helper contorna: baixa o snapshot `-hf` (que aceita token) e
reconstrói o modelo localmente com `ollama create`. É automático — basta usar
`--for ollama` e deixar o fallback agir.

---

## Apple Silicon

**Funciona, e não há nada a configurar.**

Os artefatos contêm **pesos, não executáveis**. Um `.gguf` ou um `.safetensors`
não tem arquitetura de CPU. Nem o manifesto do Ollama nem o artefato OCI
declaram `platform`, então o problema clássico de "imagem sem build arm64"
simplesmente não existe aqui. (Ele existe no `image_sync.yml`, que espelha
imagens Docker de verdade — outra coisa.)

O que precisa ser nativo arm64 é o **cliente**, e Ollama, LM Studio, MLX e
llama.cpp têm builds oficiais para Apple Silicon.

A única garantia que o espelho não pode dar é a de **formato**: se você espelhar
um repo GGUF e tentar usar no MLX, não vai funcionar em máquina nenhuma. Isso é
o assunto do [guia 4](04-escolher-modelo.md).

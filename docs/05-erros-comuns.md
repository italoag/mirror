# 5. Erros comuns

Organizado por sintoma. As mensagens abaixo são as reais, produzidas pelos
scripts deste repositório ou pelos próprios clientes.

---

## Na publicação (GitHub Actions)

### `acesso negado ao repositório X (HTTP 401)`

```
acesso negado ao repositório meta-llama/Llama-3.2-3B (HTTP 401).
Se o modelo é gated, configure o secret HF_TOKEN com um token que aceitou a licença.
```

O modelo exige aceitar uma licença. **Aceitar a licença logado no Hugging Face
não basta** — o token também precisa existir aqui:

1. Aceite a licença na página do modelo, logado.
2. Gere um token de leitura em *Settings → Access Tokens*.
3. Crie o secret `HF_TOKEN` neste repositório.

Um token gerado **antes** de você aceitar a licença funciona; o que importa é a
conta dona do token ter aceitado.

### `repositório X (revisão main) não encontrado`

Erro de digitação no `model_repo`, ou repositório privado. O formato é
`owner/nome` — copie da URL do Hugging Face, sem o `https://huggingface.co/`.

### `❌ /mnt tem 62 GiB livres, o modelo precisa de ~97 GiB`

O modelo não cabe no runner. Nesta ordem:

1. Escolha uma quantização menor: `file_pattern = *Q3_K_M*.gguf`
2. Publique só o snapshot HF: `publish_ollama = false` (dobra o teto, porque não
   há a segunda cópia que o `ollama create` grava)
3. Se ainda não couber, o modelo é grande demais para runners do GitHub.

### `unexpected status code 403 Forbidden` no push

O token não tem permissão de escrita **naquele pacote**. Causa mais comum: o
pacote já existe, criado antes com um PAT, e agora o `GITHUB_TOKEN` não o
alcança porque ele não está vinculado a este repositório.

Configure o secret `GHCR_PAT` com um PAT clássico com escopo `write:packages` —
ele tem precedência sobre o `GITHUB_TOKEN`.

### O job `ollama` falha em `ollama create`

Duas causas possíveis, e o log distingue:

- **`unsupported content type` / `only GGUF supported`** — o repositório é de
  safetensors numa arquitetura que o Ollama ainda não importa. Solução: publique
  só o snapshot (`publish_ollama = false`) e use MLX, LM Studio ou transformers.
- **Erro de arquitetura desconhecida** — modelo novo demais para a versão do
  Ollama instalada no runner. Costuma resolver sozinho quando o Ollama lança
  suporte; enquanto isso, espelhe uma versão GGUF do modelo.

### `nenhum GGUF casou com '*Q4_K_M*.gguf'`

Não é erro fatal — o workflow segue publicando o snapshot. Mas se você queria o
GGUF, o padrão não bateu. Rode uma vez com `file_pattern = *.gguf` e leia o log:
ele lista todos os candidatos com o nome exato.

---

## Na hora de baixar

### `Error: pull model manifest: 401` ou `403` no `ollama pull`

**O pacote está privado.** É de longe o erro mais comum.

O `ollama pull` só consegue token anônimo em registros de terceiros — não existe
`ollama login ghcr.io`. Siga o [guia 2](02-visibilidade.md) para tornar o pacote
público. Confirme com:

```bash
python3 scripts/ghcr_ollama.py verify --repository <owner>/<pacote> --tag <tag>
```

### `Error: pull model manifest: 404` / `manifest unknown`

A tag não existe. Confira o nome exato no resumo da execução do workflow — em
pacote compartilhado a tag inclui o nome do modelo
(`qwen2.5-7b-instruct-gguf-q4_k_m`, não `q4_k_m`).

### O `ollama pull` não aceita a referência

```
Error: invalid model name
```

O Ollama entende exatamente **`host/namespace/modelo:tag`** — três níveis, nunca
quatro. Isto não funciona:

```
ghcr.io/<owner>/mirror/qwen3:8b     ❌ um nível a mais, nem é parseável
```

Isto funciona:

```
ghcr.io/<owner>/qwen3:8b            ✅
ghcr.io/<owner>/models:qwen3-8b     ✅
```

Os workflows deste repositório já produzem só o formato correto. Se você viu o
formato errado, é uma referência antiga.

### O download trava ou cai no meio

O GHCR redireciona os blobs para `pkg-containers.githubusercontent.com`. Se sua
rede bloqueia esse domínio, o manifesto baixa e os pesos não. Libere o domínio no
proxy ou firewall.

### `digest divergente em <arquivo>`

Download corrompido. O script apaga o arquivo parcial e aborta em vez de entregar
peso corrompido. Rode de novo — arquivos já verificados são pulados.

---

## Nos clientes

### MLX: `FileNotFoundError: No safetensors found in <path>`

**Você espelhou um repositório GGUF.** MLX não lê GGUF, ponto.

Espelhe o `mlx-community/*` correspondente, ou o repo oficial em safetensors.
Veja o [guia 4](04-escolher-modelo.md).

### MLX: `[WARNING] Generating with a model that requires NNNN MB...`

O modelo cabe na RAM, mas está perto do teto recomendado pelo Metal — a geração
vai ficar lenta. A saída documentada pelo mlx-lm (macOS 15+):

```bash
sudo sysctl iogpu.wired_limit_mb=N
```

`N` maior que o modelo em MB e menor que a RAM total. Ou use uma quantização
menor.

### LM Studio não enxerga o modelo

Ele espera exatamente **dois níveis** de diretório dentro de `~/.lmstudio/models/`:

```
~/.lmstudio/models/
└── publisher/
    └── model/
        └── arquivo.gguf
```

Um nível a mais ou a menos e o modelo não aparece. O helper (`--for lmstudio`)
monta isso corretamente. Se você baixou à mão, use `lms import` em vez de mover
arquivos:

```bash
lms import ./arquivo.gguf --user-repo <publisher>/<model> -y
```

Depois de mexer no diretório, dê um **Refresh** na aba *My Models*.

### LM Studio não carrega um modelo MLX

O motor MLX só existe em Apple Silicon. Em Intel ou Windows, use GGUF.

### llama.cpp: modelo em shards

Aponte para o **primeiro** arquivo (`-00001-of-0000N.gguf`); o llama.cpp
encontra os outros sozinho. Passar o segundo shard dá erro de arquivo inválido.

### O agente não conecta

Agentes falam com a API da OpenAI, não com o registro. Confira:

- O servidor está de pé? (`ollama serve`, `lms server start`, `mlx_lm.server`)
- A Base URL termina em `/v1`?
- O nome do modelo no agente é o mesmo do `ollama list`?
- Alguma API key preenchida? Muitos clientes exigem string não vazia mesmo que o
  servidor ignore.

---

## Diagnóstico rápido

Antes de abrir uma issue, estes três comandos localizam quase tudo:

```bash
# 1. A tag existe e está pública?  (sem credenciais, como o Ollama faria)
python3 scripts/ghcr_ollama.py verify --repository <owner>/<pacote> --tag <tag>

# 2. O que existe nesse pacote?
oras repo tags ghcr.io/<owner>/<pacote>

# 3. O que tem dentro da tag?
oras manifest fetch --pretty ghcr.io/<owner>/<pacote>:<tag>
```

O `verify` é o mais útil dos três: ele refaz exatamente o caminho do
`ollama pull` — desafio de autenticação, manifesto, media types das camadas e um
`Range` de 1 byte em cada blob para confirmar o redirect até a CDN — sem baixar
os gigabytes.

# 4. Escolher o modelo certo

O espelhamento **não converte nada**: ele copia fielmente o formato que existe no
Hugging Face. Então a compatibilidade é decidida no momento em que você escolhe
o `model_repo` — não depois.

---

## A regra que mais pega gente

> **MLX não lê GGUF.**

O `mlx-lm` procura os pesos com `glob("model*.safetensors")` e aborta com
`No safetensors found` se não achar. No código dele, GGUF aparece **só como
destino de exportação** (`mlx_lm.fuse --export-gguf`); não existe caminho de
leitura.

Isso não é limitação deste repositório — baixando direto do Hugging Face é
igual. Se o seu alvo é MLX, você precisa espelhar um repositório de safetensors.

---

## Matriz por formato de origem

| Origem no Hugging Face | Ollama | llama.cpp | LM Studio | MLX | transformers |
|---|:--:|:--:|:--:|:--:|:--:|
| `*-GGUF` (bartowski, unsloth, lmstudio-community) | ✅ | ✅ | ✅ | ❌ | ❌ |
| `mlx-community/*` (quantizado em MLX) | ⚠️ | ❌ | ✅ | ✅ | ✅ |
| safetensors fp16/bf16 (o repo oficial do modelo) | ⚠️ | ❌ | ❌ | ✅ | ✅ |
| AWQ / GPTQ / MXFP4 / bitnet | ⚠️ | ❌ | ❌ | ✅ | ✅ |
| bitsandbytes 4/8-bit | ⚠️ | ❌ | ❌ | ❌ | ✅ |

⚠️ = o Ollama importa safetensors, mas só nas arquiteturas que ele já
implementa. O workflow tenta e falha alto se não der.

O job `plan` publica essa tabela **já resolvida para o modelo específico** no
resumo de cada execução: ele lê o `config.json` do repositório e confere o método
de quantização contra o que o `mlx-lm` aceita. Se estiver em dúvida, rode e leia
o resumo antes de se comprometer.

---

## Como decidir, na prática

### "Quero rodar no Ollama"

Espelhe um repo `*-GGUF`. Os publicadores confiáveis são `bartowski`, `unsloth`,
`lmstudio-community` e o `ggml-org`.

```
model_repo   = bartowski/Qwen2.5-7B-Instruct-GGUF
file_pattern = *Q4_K_M*.gguf
```

Alternativa mais simples ainda: se o modelo já está na biblioteca do Ollama, use
o workflow `Sync Ollama Model to GHCR` — é streaming puro, sem build.

### "Quero rodar no MLX"

Espelhe o `mlx-community/*` correspondente.

```
model_repo = mlx-community/Qwen3-8B-4bit
```

Esses repos já vêm quantizados em formato MLX, com o bloco `quantization` no
`config.json`. Carregam rápido e ocupam pouca RAM.

Se não existir versão MLX do modelo, espelhe o repo oficial em fp16/bf16 — o
`mlx-lm` converte ao carregar. Custa mais RAM e mais tempo de carga.

### "Quero cobrir tudo no meu Mac"

**Espelhe dois repositórios.** São modelos diferentes no Hugging Face, então são
duas execuções do workflow:

```
1)  model_repo = bartowski/Qwen2.5-7B-Instruct-GGUF   → Ollama, LM Studio, llama.cpp
2)  model_repo = mlx-community/Qwen2.5-7B-Instruct-4bit → MLX, LM Studio, transformers
```

Com a estratégia de pacote compartilhado, os dois convivem no mesmo pacote:

```
ghcr.io/<owner>/models:qwen2.5-7b-instruct-gguf-q4_k_m
ghcr.io/<owner>/models:qwen2.5-7b-instruct-4bit-safetensors
```

### "Quero servir com vLLM num servidor"

Espelhe o repo oficial em safetensors (fp16/bf16), ou uma versão AWQ/GPTQ.
Desligue `publish_ollama` para poupar metade do tempo e do disco.

---

## Escolher a quantização

Valores aproximados por peso, e o tamanho resultante num modelo de 7–8 B:

| Quantização | ~bits/peso | 7–8 B | Comentário |
|---|:--:|:--:|---|
| `Q8_0` | ~8.5 | ~8 GB | Quase idêntico ao fp16. Raramente vale o custo |
| `Q6_K` | ~6.6 | ~6 GB | Perda imperceptível |
| `Q5_K_M` | ~5.7 | ~5.5 GB | Bom meio-termo |
| **`Q4_K_M`** | ~4.8 | ~4.5 GB | **Padrão da casa.** Melhor relação qualidade/tamanho |
| `IQ4_XS` | ~4.3 | ~4 GB | Um pouco menor, um pouco mais lento |
| `Q3_K_M` | ~3.9 | ~3.5 GB | Degradação já perceptível |
| MLX 4-bit | ~4.5 | ~4.5 GB | Equivalente ao Q4_K_M, no mundo MLX |

Regra de bolso: `tamanho ≈ parâmetros × bits_por_peso ÷ 8`.

Sem `file_pattern`, o workflow escolhe `Q4_K_M` quando existe.

---

## Quanto cabe na sua máquina

Some o tamanho do modelo **mais o contexto** (o cache KV, que cresce com o
tamanho da janela) e deixe folga para o sistema. Numa memória unificada de Apple
Silicon, o Metal recomenda usar até cerca de 75% da RAM total.

| RAM do Mac | Confortável | No limite |
|---|---|---|
| 8 GB | 3–4 B em Q4_K_M | 7 B em Q4_K_M, contexto curto |
| 16 GB | 7–8 B em Q4_K_M | 14 B em Q4_K_M |
| 24 GB | 14 B em Q4_K_M | 32 B em Q4_K_M |
| 32 GB | 14 B em Q6_K, ou 32 B em Q4_K_M | 32 B em Q5_K_M |
| 64 GB+ | 32 B em Q6_K | 70 B em Q4_K_M |

Passando do limite, o Ollama descarrega camadas para a CPU e o llama.cpp faz
paginação — em ambos os casos a geração desaba de dezenas para poucos tokens por
segundo. O MLX avisa explicitamente com o `[WARNING]` descrito no
[guia 3](03-clientes.md#modelos-grandes-na-memória-unificada).

---

## Limites do runner

O job do Ollama precisa do modelo **duas vezes** em disco (o download mais a
cópia no diretório de blobs), num `/mnt` de ~65 GB. Teto prático: GGUF de até uns
28 GB.

Para modelos maiores, publique só o snapshot HF:

```
publish_ollama = false
```

Ele precisa do espaço uma vez só, o que dobra o teto. O workflow checa isso antes
de baixar e falha cedo com a conta feita, em vez de morrer no meio.

# Documentação

Guia completo para espelhar modelos de LLM no GHCR e consumi-los localmente.

| Guia | Quando ler |
|---|---|
| [1. Publicar um modelo](01-publicar.md) | primeira vez rodando o workflow |
| [2. Deixar o pacote público](02-visibilidade.md) | **leia antes da primeira execução** — a escolha aqui é irreversível |
| [3. Usar nos clientes](03-clientes.md) | Ollama, LM Studio, MLX, llama.cpp, transformers, agentes |
| [4. Escolher o modelo certo](04-escolher-modelo.md) | qual repo do HF espelhar para o seu caso e sua máquina |
| [5. Erros comuns](05-erros-comuns.md) | quando algo falha |

---

## Os 5 minutos que resolvem 90% dos casos

Você tem um Mac com Apple Silicon e quer rodar um modelo local com Ollama.

**1. Decida a estratégia de pacote** (leia [o guia 2](02-visibilidade.md) — muda o
que você faz agora e não dá para desfazer). Para a maioria, o caminho mais
simples é um pacote compartilhado chamado `models`.

**2. Rode o workflow** em *Actions → Sync HF Model to GHCR → Run workflow*:

```
model_repo   = bartowski/Qwen2.5-7B-Instruct-GGUF
package_name = models
```

**3. Torne o pacote público, uma vez.** Perfil → aba **Packages** → `models` →
**Package settings** → **Danger Zone** → **Change visibility** → **Public**.

**4. Confirme** que ficou acessível sem credenciais:

```bash
python3 scripts/ghcr_ollama.py verify \
  --repository <seu-usuário>/models --tag qwen2.5-7b-instruct-gguf-q4_k_m
```

**5. Baixe e rode:**

```bash
ollama pull ghcr.io/<seu-usuário>/models:qwen2.5-7b-instruct-gguf-q4_k_m
ollama run  ghcr.io/<seu-usuário>/models:qwen2.5-7b-instruct-gguf-q4_k_m
```

A partir daqui, todo modelo novo publicado nesse pacote já nasce público — o
passo 3 nunca mais se repete.

---

## O que este repositório publica

Cada execução gera **duas referências** no GHCR, a partir do mesmo modelo:

```
ghcr.io/<owner>/<pacote>:<tag>       manifesto nativo do Ollama
ghcr.io/<owner>/<pacote>:<tag>-hf    artefato OCI no layout do Hugging Face
```

A primeira só o Ollama entende. A segunda serve a todo o resto — LM Studio,
MLX, llama.cpp, transformers, vLLM — porque é o diretório do Hugging Face
reconstruído arquivo por arquivo.

O espelhamento **não converte nada**: ele copia fielmente o formato que existe
no Hugging Face. Por isso a escolha do repositório de origem decide quais
clientes vão funcionar. Isso está detalhado em
[Escolher o modelo certo](04-escolher-modelo.md).

# 1. Publicar um modelo

Dois workflows, dependendo de onde o modelo está hoje.

| Origem | Workflow |
|---|---|
| Hugging Face | `Sync HF Model to GHCR` |
| Biblioteca pública do Ollama (`qwen3:8b`, `deepseek-r1:7b`…) | `Sync Ollama Model to GHCR` |

---

## Antes da primeira execução

Três coisas, em ordem:

1. **Escolha a estratégia de pacote.** [Guia 2](02-visibilidade.md). Isso define
   o valor de `package_name` e não dá para desfazer depois.
2. **Confira se o modelo serve para o seu cliente.** [Guia 4](04-escolher-modelo.md).
   Espelhar um repo `*-GGUF` para usar no MLX é o erro mais comum, e não tem
   conserto depois — MLX não lê GGUF.
3. **Se o modelo for *gated*** (Llama, Gemma, alguns Mistral), crie o secret
   `HF_TOKEN`. Veja abaixo.

---

## Sync HF Model to GHCR

*Actions → Sync HF Model to GHCR → Run workflow.*

### Inputs

| Input | Padrão | Explicação |
|---|---|---|
| `model_repo` | — | O repo no Hugging Face, no formato `owner/nome`. Ex: `bartowski/Qwen2.5-7B-Instruct-GGUF` |
| `revision` | `main` | Branch, tag ou commit. Fixe um commit se quiser reprodutibilidade |
| `file_pattern` | `*.gguf` | Qual quantização publicar. Ex: `*Q5_K_M*.gguf` |
| `package_name` | nome do repo HF | Pacote no GHCR. Um nome fixo agrupa todos os modelos num pacote só |
| `tag` | quantização detectada | Tag no GHCR. Ex: `q4_k_m` |
| `publish_ollama` | `true` | Publica o formato nativo do Ollama |
| `publish_hf_snapshot` | `true` | Publica o snapshot HF como artefato OCI |
| `exclude` | — | Padrões extras a ignorar, separados por vírgula |
| `force_resync` | `false` | Republica mesmo se a tag já existir |

### O que acontece, na ordem

O workflow tem três jobs. O primeiro decide tudo antes de baixar um único byte —
o que evita gastar vinte minutos puxando 40 GB para descobrir no fim que o
padrão casava com seis quantizações.

**Job `plan`.** Consulta a API do Hugging Face, lista os arquivos com tamanho,
escolhe o grupo GGUF, deriva o nome do pacote e a tag, e publica no resumo da
execução uma tabela de compatibilidade por cliente — lendo o `config.json` do
repositório e conferindo o método de quantização. Também verifica o que já
existe no GHCR, para pular o que não mudou.

**Job `ollama`.** Baixa só os arquivos escolhidos, roda `ollama create` de
verdade (é ele quem monta as camadas no formato certo), envia blobs e manifesto
ao GHCR e valida o resultado com um pull anônimo.

**Job `snapshot`.** Baixa o layout do Hugging Face e publica como artefato OCI,
um arquivo por camada.

Os dois últimos rodam em paralelo, em runners separados — cada um com seu
próprio disco.

### Como a tag é escolhida

Se você não passar `tag`, ela vem da quantização detectada no nome do arquivo:

```
Qwen2.5-7B-Instruct-Q4_K_M.gguf   → tag q4_k_m
Llama-3.2-3B-IQ3_XXS.gguf         → tag iq3_xxs
```

Num repo só de safetensors, a tag vira `safetensors`. Se nada for detectado,
`latest`.

**Com `package_name` diferente do nome derivado do repo** (o modo de pacote
compartilhado), o nome do modelo entra na tag automaticamente, senão `q4_k_m`
colidiria entre modelos diferentes:

```
bartowski/Qwen2.5-7B-Instruct-GGUF + package_name=models
  → ghcr.io/<owner>/models:qwen2.5-7b-instruct-gguf-q4_k_m
```

### Quando o repo tem várias quantizações

Com `file_pattern` no padrão `*.gguf`, o workflow escolhe uma — preferindo
`Q4_K_M`, e em empate a menor — e lista as outras no log:

```
🧩 GGUF escolhido: Qwen2.5-7B-Instruct-Q4_K_M.gguf
   outras quantizações disponíveis (use file_pattern para escolher):
     - Qwen2.5-7B-Instruct-Q5_K_M.gguf
     - Qwen2.5-7B-Instruct-Q8_0.gguf
```

Para publicar outra, rode de novo com `file_pattern = *Q5_K_M*.gguf`. Como a tag
sai diferente (`q5_k_m`), as duas convivem no mesmo pacote.

### Modelos divididos em shards

Arquivos `...-00001-of-00003.gguf` são tratados como um conjunto único: o
workflow agrupa, baixa todos e passa todos ao `ollama create`, que os junta.
Nada a fazer manualmente.

### Modelos multimodais

Um `mmproj-*.gguf` no repositório é detectado e vira uma camada de projetor no
manifesto do Ollama, automaticamente.

### Repositórios sem GGUF

Repos só de safetensors (incluindo os `mlx-community/*`) funcionam: o snapshot
HF é publicado normalmente, e o Ollama tenta importar os safetensors direto — o
que funciona nas arquiteturas que ele já implementa, e falha alto quando não.

---

## Sync Ollama Model to GHCR

Espelha um modelo da biblioteca pública do Ollama.

| Input | Padrão | Explicação |
|---|---|---|
| `model_name` | — | `qwen3`, `deepseek-r1`, `llama3.2` |
| `model_tag` | `latest` | `8b`, `7b`, `latest` |
| `package_name` | o nome do modelo | Pacote no GHCR |
| `tag` | a tag do modelo | Tag no GHCR |
| `force_resync` | `false` | Republica mesmo se já existir |

A cópia é feita em streaming, direto de `registry.ollama.ai` para o GHCR, sem
tocar o disco do runner — então modelos de dezenas de GB passam sem problema. O
manifesto original é preservado byte a byte: o resultado é idêntico ao upstream.

---

## Secrets

| Secret | Necessário? | Para quê |
|---|---|---|
| `GITHUB_TOKEN` | automático | Publicar no GHCR. Vincula o pacote a este repositório |
| `GHCR_PAT` | opcional | PAT com `write:packages`. Tem precedência quando existe |
| `HF_TOKEN` | opcional | Modelos *gated* no Hugging Face |

**Nenhum dos dois primeiros afeta a visibilidade do pacote** — ela é sempre
privada no primeiro push. Veja o [guia 2](02-visibilidade.md).

### Criando o `HF_TOKEN`

Necessário para Llama, Gemma e outros modelos que exigem aceitar uma licença.

1. Aceite a licença na página do modelo no Hugging Face, logado.
2. Gere um token em *Settings → Access Tokens* com permissão de leitura.
3. Neste repositório: *Settings → Secrets and variables → Actions → New
   repository secret*, nome `HF_TOKEN`.

Sem ele, um repo gated falha logo no job `plan`, com a mensagem apontando isso.

---

## Limites de espaço

Os jobs trabalham em `/mnt` (~65 GB no runner do GitHub) em vez do disco raiz
(~20 GB), e checam o espaço antes de baixar. Se não couber, a execução falha
cedo com uma mensagem clara em vez de morrer no meio:

```
❌ /mnt tem 62 GiB livres, o modelo precisa de ~97 GiB.
   Escolha uma quantização menor com o input file_pattern.
```

O job do Ollama precisa do modelo **duas vezes** (o arquivo baixado mais a cópia
que o `ollama create` grava no diretório de blobs), então o teto prático é um
GGUF de aproximadamente 28 GB. Na prática isso cobre até uns 70B em `Q3_K_M`.
Para modelos maiores, publique só o snapshot HF (`publish_ollama = false`), que
precisa do espaço uma vez só.

---

## Republicar

Uma tag que já existe é pulada. Para forçar, marque `force_resync`. Útil quando
o repositório de origem mudou sem trocar de tag.

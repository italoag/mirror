# 2. Deixar o pacote público (e mantê-lo assim)

> **Leia antes da primeira execução.** A decisão da estratégia muda o que você
> faz agora, e tornar um pacote público é **irreversível** segundo a
> documentação do GitHub.

---

## Por que isso importa

O `ollama pull` só consegue **token anônimo** em registros de terceiros. Ele não
tem para onde enviar credenciais do GHCR — não existe `ollama login ghcr.io`.
Num pacote privado o registro responde 403 e o pull falha, mesmo com o manifesto
perfeitamente correto.

Ou seja: **pacote privado = `ollama pull` não funciona.** Sem exceção.

(Para a tag `-hf` isso não é obrigatório: `oras` e o helper deste repositório
aceitam token, então ela funciona privada. Mas exige credencial em toda máquina
que for baixar.)

---

## A pegadinha: repositório público ≠ pacote público

Este é o ponto que engana praticamente todo mundo.

> "the package automatically inherits the access **permissions (but not the
> visibility)** of the linked repository"
>
> "When you first publish a package that is scoped to your personal account,
> **the default visibility is private**"
>
> — [documentação do GitHub](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)

Ter o repositório público **não torna o pacote público**. O que é herdado são as
permissões de acesso, não a visibilidade. Todo pacote novo nasce privado.

## E não dá para automatizar

Não é limitação deste repositório, é do GitHub:

- **REST API** — os únicos endpoints de packages são `GET`, `DELETE` e
  `POST /restore`, em `/user/packages/...`, `/orgs/{org}/packages/...` e
  `/users/{username}/packages/...`. **Não existe `PATCH` de visibilidade.**
- **GraphQL** — o schema tem apenas `deletePackageVersion`. Não existe sequer um
  tipo `PackageVisibility`.

A troca só existe na interface web. Qualquer tutorial que prometa automatizar
isso com um PAT está errado ou desatualizado.

---

## A estratégia: pagar uma vez, não sempre

Como o passo manual é **por pacote**, e não por tag, dá para reduzi-lo a uma
única vez na vida — publicando todos os modelos como tags de um mesmo pacote.

### Opção A — pacote compartilhado (recomendada)

Passe o mesmo `package_name` em toda execução:

```
model_repo   = bartowski/Qwen2.5-7B-Instruct-GGUF
package_name = models
```

```
ghcr.io/<owner>/models:qwen2.5-7b-instruct-gguf-q4_k_m
ghcr.io/<owner>/models:llama-3.2-3b-gguf-q4_k_m
ghcr.io/<owner>/models:qwen3-8b-4bit-safetensors
```

Você torna `models` público **uma vez**. Toda tag publicada depois já nasce
pública.

- ✅ Um único passo manual, para sempre
- ✅ Impossível esquecer e descobrir só na hora de usar
- ❌ No `ollama list` tudo aparece como `ghcr.io/<owner>/models:...`

O incômodo do nome resolve-se com um alias local:

```bash
ollama pull ghcr.io/<owner>/models:qwen2.5-7b-instruct-gguf-q4_k_m
ollama cp   ghcr.io/<owner>/models:qwen2.5-7b-instruct-gguf-q4_k_m qwen
ollama run  qwen
```

### Opção B — um pacote por modelo

Deixe `package_name` vazio:

```
ghcr.io/<owner>/qwen2.5-7b-instruct-gguf:q4_k_m
ghcr.io/<owner>/llama-3.2-3b-gguf:q4_k_m
```

- ✅ Nomes limpos, organização por modelo, dá para apagar um modelo inteiro
- ❌ Um passo manual **por modelo novo** (nunca por sincronização — quantizações
  novas do mesmo modelo entram na mesma caixa, já pública)

---

## Passo a passo para tornar público

Faça isso **depois** da primeira execução do workflow — o pacote precisa existir.

1. Abra `https://github.com/<seu-usuário>?tab=packages`
   (numa organização: página da org → aba **Packages**).
2. Clique no pacote.
3. Na direita, **Package settings**.
4. Role até **Danger Zone** → **Change visibility**.
5. Escolha **Public**.
6. Digite o nome do pacote para confirmar.

> ⚠️ **Irreversível.** A documentação do GitHub é explícita: *"Once you make a
> package public, you cannot make it private again."*

---

## Conferir que deu certo

O script faz a verificação **sem credenciais** — exatamente o caminho que o
Ollama percorre, incluindo o redirect dos blobs para a CDN. Se isso passa,
qualquer máquina no mundo consegue baixar:

```bash
python3 scripts/ghcr_ollama.py verify \
  --repository <owner>/models --tag qwen2.5-7b-instruct-gguf-q4_k_m
```

Público e correto:

```
✅ manifesto Ollama OK (5 blobs): application/vnd.ollama.image.model, ...
✅ todos os 5 blobs acessíveis anonimamente
👉 ollama pull ghcr.io/<owner>/models:qwen2.5-7b-instruct-gguf-q4_k_m
```

Ainda privado (código de saída 2):

```
⚠️  O pacote <owner>/models ainda está PRIVADO. O manifesto foi publicado,
    mas o `ollama pull` não vai funcionar: ...
```

Funciona igual para a tag `-hf`, reportando que é um artefato OCI.

O próprio workflow roda essa verificação ao final de cada publicação, então o
resumo da execução já diz se está tudo certo. Ela não derruba o job — a
publicação em si deu certo; o que falta é a visibilidade.

---

## Checklist

Antes da primeira execução:

- [ ] Estratégia escolhida (A ou B) e `package_name` definido
- [ ] `HF_TOKEN` criado, se o modelo for gated

Depois da primeira execução:

- [ ] Pacote tornado público na interface web
- [ ] `verify` passando sem credenciais
- [ ] `ollama pull` testado numa máquina limpa (ou com `ollama rm` antes, para
      não confundir cache local com download real)

Nas execuções seguintes: nada. Só publicar.

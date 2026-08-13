#!/usr/bin/env python3
"""Publica e valida modelos no formato nativo do Ollama dentro do GHCR.

O Ollama fala o protocolo OCI Distribution v2, mas espera um manifesto
`application/vnd.docker.distribution.manifest.v2+json` cujas camadas usam os
media types `application/vnd.ollama.image.*`. Nenhuma ferramenta de container
comum (docker build/push, skopeo, crane) produz esse formato, por isso este
script fala direto com a API do registro.

Subcomandos:

  exists   Verifica se uma tag já existe no GHCR.
  push     Envia um modelo criado por `ollama create` (blobs + manifesto).
  mirror   Copia um modelo de registry.ollama.ai para o GHCR em streaming.
  pull     Baixa um snapshot HF publicado como artefato OCI.
  verify   Refaz, anonimamente, o mesmo caminho que o `ollama pull` percorre.

Credenciais: variáveis de ambiente GHCR_USERNAME e GHCR_TOKEN.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "ghcr-model-mirror/1.0"

MANIFEST_MEDIA_TYPE = "application/vnd.docker.distribution.manifest.v2+json"

# Tamanho de cada PATCH durante o upload. Pedaços grandes reduzem o overhead de
# ida e volta; pedaços pequenos demais fazem um GGUF de 40 GB virar milhares de
# requisições.
CHUNK_SIZE = 64 * 1024 * 1024

# Quantidade de tentativas por blob (o upload recomeça do zero a cada tentativa).
BLOB_ATTEMPTS = 3

OLLAMA_REGISTRY = "registry.ollama.ai"
GHCR_REGISTRY = "ghcr.io"


class RegistryError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(msg, flush=True)


def human(size: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Impede o urllib de repassar o header Authorization para outro host.

    O GHCR responde 307 para uma URL pré-assinada em
    pkg-containers.githubusercontent.com; mandar o Bearer do ghcr.io para lá faz
    o armazenamento rejeitar a requisição.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_opener = urllib.request.build_opener(_NoRedirect)


class Response:
    def __init__(self, status: int, headers, body: bytes, url: str):
        self.status = status
        self.headers = headers
        self.body = body
        self.url = url

    def json(self):
        return json.loads(self.body.decode("utf-8"))


def request(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    data=None,
    timeout: int = 600,
    read_body: bool = True,
) -> Response:
    hdrs = {"User-Agent": USER_AGENT}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        resp = _opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:  # 4xx/5xx ainda carregam corpo útil
        body = exc.read() if read_body else b""
        return Response(exc.code, exc.headers, body, url)
    with resp:
        body = resp.read() if read_body else b""
        return Response(resp.status, resp.headers, body, resp.url)


def parse_challenge(value: str) -> dict:
    """Lê um header `WWW-Authenticate: Bearer realm="...",service="...",scope="..."`."""
    if not value:
        return {}
    scheme, _, rest = value.partition(" ")
    if scheme.lower() != "bearer":
        return {}
    return dict(re.findall(r'(\w+)="([^"]*)"', rest))


class Registry:
    """Cliente mínimo do OCI Distribution API, com auth orientada a desafio.

    O registro público do Ollama serve manifestos sem autenticação, enquanto o
    GHCR responde 401 com um desafio Bearer mesmo em pacotes públicos. Reagir ao
    desafio (em vez de assumir um fluxo fixo) cobre os dois casos.
    """

    def __init__(
        self,
        host: str,
        repository: str,
        username: str = "",
        password: str = "",
        scheme: str = "https",
    ):
        self.host = host
        self.repository = repository
        self.username = username
        self.password = password
        self.scheme = scheme
        self._tokens: dict[str, tuple[str, float]] = {}

    @property
    def base(self) -> str:
        return f"{self.scheme}://{self.host}/v2/{self.repository}"

    def _fetch_token(self, challenge: dict, scope: str) -> str:
        realm = challenge.get("realm") or f"{self.scheme}://{self.host}/token"
        params = {}
        if challenge.get("service"):
            params["service"] = challenge["service"]
        else:
            params["service"] = self.host
        params["scope"] = challenge.get("scope") or scope
        headers = {}
        if self.username and self.password:
            basic = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            headers["Authorization"] = f"Basic {basic}"
        resp = request("GET", f"{realm}?{urllib.parse.urlencode(params)}", headers=headers, timeout=60)
        if resp.status != 200:
            raise RegistryError(
                f"não foi possível obter token para {params['scope']} em {self.host}: "
                f"HTTP {resp.status} {resp.body[:400]!r}"
            )
        payload = resp.json()
        token = payload.get("token") or payload.get("access_token")
        if not token:
            raise RegistryError(f"resposta de token sem campo token: {payload}")
        # Renova bem antes do vencimento: um upload de vários GB dura mais que a
        # validade padrão de 5 minutos.
        lifetime = float(payload.get("expires_in") or 300)
        self._tokens[scope] = (token, time.time() + max(30.0, lifetime * 0.8))
        return token

    def auth_header(self, scope: str, force: bool = False) -> dict:
        """Devolve o header Bearer do escopo, buscando o token se necessário."""
        cached = self._tokens.get(scope)
        if force or not cached or time.time() >= cached[1]:
            self._tokens.pop(scope, None)
            token = self._fetch_token({}, scope)
        else:
            token = cached[0]
        return {"Authorization": f"Bearer {token}"}

    def send(
        self,
        method: str,
        url: str,
        scope: str,
        *,
        headers: dict | None = None,
        data=None,
        timeout: int = 600,
        read_body: bool = True,
    ) -> Response:
        """Requisição que começa anônima e reage ao desafio 401 uma única vez."""
        hdrs = dict(headers or {})
        cached = self._tokens.get(scope)
        if cached and time.time() < cached[1]:
            hdrs["Authorization"] = f"Bearer {cached[0]}"
        resp = request(method, url, headers=hdrs, data=data, timeout=timeout, read_body=read_body)
        if resp.status != 401:
            return resp
        challenge = parse_challenge(resp.headers.get("WWW-Authenticate", ""))
        token = self._fetch_token(challenge, scope)
        hdrs["Authorization"] = f"Bearer {token}"
        return request(method, url, headers=hdrs, data=data, timeout=timeout, read_body=read_body)

    def pull_scope(self) -> str:
        return f"repository:{self.repository}:pull"

    def push_scope(self) -> str:
        return f"repository:{self.repository}:pull,push"

    # ---------------------------------------------------------------- leitura

    def get_manifest(self, reference: str, scope: str | None = None) -> Response:
        # O mesmo Accept que o Ollama envia, mais os tipos OCI para diagnóstico.
        headers = {
            "Accept": ", ".join(
                [
                    MANIFEST_MEDIA_TYPE,
                    "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.index.v1+json",
                ]
            )
        }
        return self.send(
            "GET", f"{self.base}/manifests/{reference}", scope or self.pull_scope(), headers=headers
        )

    def manifest_exists(self, reference: str) -> bool:
        return self.get_manifest(reference).status == 200

    def blob_exists(self, digest: str) -> bool:
        resp = self.send(
            "HEAD", f"{self.base}/blobs/{digest}", self.push_scope(), read_body=False
        )
        return resp.status == 200

    def open_blob(self, digest: str, scope: str | None = None, extra: dict | None = None):
        """Abre o stream de um blob, seguindo o redirect para a CDN sem vazar o token."""
        scope = scope or self.pull_scope()
        url = f"{self.base}/blobs/{digest}"
        headers = dict(extra or {})
        headers["User-Agent"] = USER_AGENT
        cached = self._tokens.get(scope)
        if cached and time.time() < cached[1]:
            headers["Authorization"] = f"Bearer {cached[0]}"
        retried_auth = False
        for _ in range(10):
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                resp = _opener.open(req, timeout=600)
            except urllib.error.HTTPError as exc:
                # _NoRedirect faz o urllib levantar HTTPError também para 3xx.
                status, resp_headers = exc.code, exc.headers
                if status == 401 and not retried_auth:
                    retried_auth = True
                    exc.close()
                    challenge = parse_challenge(resp_headers.get("WWW-Authenticate", ""))
                    headers["Authorization"] = f"Bearer {self._fetch_token(challenge, scope)}"
                    continue
                if status not in (301, 302, 307, 308):
                    body = exc.read()[:300]
                    exc.close()
                    raise RegistryError(
                        f"falha ao baixar blob {digest}: HTTP {status} {body!r}"
                    ) from exc
                location = resp_headers.get("Location")
                exc.close()
                if not location:
                    raise RegistryError(f"redirect sem Location ao baixar {digest}")
                url = urllib.parse.urljoin(url, location)
                # Fora do host original o token não vale e ainda atrapalha: a URL
                # pré-assinada da CDN rejeita um Authorization inesperado.
                if urllib.parse.urlparse(url).netloc != self.host:
                    headers = {"User-Agent": USER_AGENT}
                    headers.update(extra or {})
                continue
            if resp.status in (200, 206):
                return resp
            status = resp.status
            resp.close()
            raise RegistryError(f"status inesperado {status} ao baixar {digest}")
        raise RegistryError(f"excesso de redirects ao baixar {digest}")

    # ------------------------------------------------------------------ envio

    def _start_upload(self) -> str:
        resp = self.send(
            "POST",
            f"{self.base}/blobs/uploads/",
            self.push_scope(),
            headers={"Content-Length": "0"},
            data=b"",
        )
        if resp.status not in (202, 201):
            raise RegistryError(
                f"não foi possível iniciar upload em {self.repository}: "
                f"HTTP {resp.status} {resp.body[:400]!r}"
            )
        location = resp.headers.get("Location")
        if not location:
            raise RegistryError("resposta de início de upload sem Location")
        return urllib.parse.urljoin(f"{self.base}/blobs/uploads/", location)

    def _upload_stream(self, stream, size: int, digest: str) -> None:
        location = self._start_upload()
        offset = 0
        while offset < size:
            chunk = stream.read(min(CHUNK_SIZE, size - offset))
            if not chunk:
                raise RegistryError(
                    f"stream terminou em {offset} bytes, esperado {size} (blob {digest})"
                )
            headers = {
                "Content-Type": "application/octet-stream",
                "Content-Range": f"{offset}-{offset + len(chunk) - 1}",
                "Content-Length": str(len(chunk)),
            }
            resp = self.send("PATCH", location, self.push_scope(), headers=headers, data=chunk)
            if resp.status not in (202, 201, 204):
                raise RegistryError(
                    f"PATCH falhou em {offset} para {digest}: "
                    f"HTTP {resp.status} {resp.body[:400]!r}"
                )
            next_location = resp.headers.get("Location")
            if next_location:
                location = urllib.parse.urljoin(location, next_location)
            offset += len(chunk)
            log(f"      {human(offset)} / {human(size)}")

        sep = "&" if "?" in location else "?"
        finish = f"{location}{sep}digest={urllib.parse.quote(digest, safe='')}"
        resp = self.send(
            "PUT", finish, self.push_scope(), headers={"Content-Length": "0"}, data=b""
        )
        if resp.status not in (201, 204):
            raise RegistryError(
                f"PUT final falhou para {digest}: HTTP {resp.status} {resp.body[:400]!r}"
            )

    def push_blob(self, digest: str, size: int, open_stream) -> bool:
        """Envia um blob se ele ainda não existir. `open_stream` devolve um file-like."""
        if self.blob_exists(digest):
            log(f"   • {digest[:19]} já presente ({human(size)})")
            return False
        last: Exception | None = None
        for attempt in range(1, BLOB_ATTEMPTS + 1):
            try:
                log(f"   ↑ {digest[:19]} ({human(size)}) tentativa {attempt}/{BLOB_ATTEMPTS}")
                stream = open_stream()
                try:
                    self._upload_stream(stream, size, digest)
                finally:
                    stream.close()
                return True
            except (RegistryError, OSError, urllib.error.URLError) as exc:
                last = exc
                log(f"   ! falha no blob {digest[:19]}: {exc}")
                # Força um token novo: 401 no meio do upload é a causa mais comum.
                self.auth_header(self.push_scope(), force=True)
                time.sleep(2 * attempt)
        raise RegistryError(f"não foi possível enviar o blob {digest}: {last}")

    def put_manifest(self, reference: str, payload: bytes, media_type: str) -> None:
        headers = {"Content-Type": media_type, "Content-Length": str(len(payload))}
        resp = self.send(
            "PUT",
            f"{self.base}/manifests/{reference}",
            self.push_scope(),
            headers=headers,
            data=payload,
        )
        if resp.status not in (201, 202):
            raise RegistryError(
                f"PUT do manifesto {reference} falhou: HTTP {resp.status} {resp.body[:600]!r}"
            )


# --------------------------------------------------------------------- helpers


def credentials() -> tuple[str, str]:
    user = os.environ.get("GHCR_USERNAME", "")
    token = os.environ.get("GHCR_TOKEN", "")
    if not token:
        raise SystemExit("GHCR_TOKEN não definido no ambiente")
    return user or "x-access-token", token


def target_registry(args, anonymous: bool = False) -> Registry:
    """Monta o cliente do registro de destino a partir dos argumentos comuns."""
    user, token = ("", "") if anonymous else credentials()
    return Registry(
        getattr(args, "registry", GHCR_REGISTRY) or GHCR_REGISTRY,
        args.repository.lower(),
        user,
        token,
        scheme="http" if getattr(args, "plain_http", False) else "https",
    )


def add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository", required=True, help="owner/pacote")
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--registry",
        default=GHCR_REGISTRY,
        help="host do registro (padrão: ghcr.io; qualquer registro OCI serve)",
    )
    parser.add_argument(
        "--plain-http", action="store_true", help="usar HTTP em vez de HTTPS (registro local)"
    )


def layers_of(manifest: dict) -> list[dict]:
    entries = list(manifest.get("layers") or [])
    config = manifest.get("config")
    if config and config.get("digest"):
        entries.append(config)
    return entries


def blob_path(models_dir: str, digest: str) -> str:
    return os.path.join(models_dir, "blobs", digest.replace(":", "-"))


def read_local_manifest(models_dir: str, host: str, namespace: str, model: str, tag: str) -> tuple[dict, bytes]:
    path = os.path.join(models_dir, "manifests", host, namespace, model, tag)
    if not os.path.isfile(path):
        raise SystemExit(f"manifesto local não encontrado: {path}")
    with open(path, "rb") as fh:
        raw = fh.read()
    return json.loads(raw.decode("utf-8")), raw


def parse_model_ref(ref: str) -> tuple[str, str, str, str]:
    """Divide `host/namespace/model:tag` como o Ollama faz."""
    tag = "latest"
    if ":" in ref.rsplit("/", 1)[-1]:
        ref, tag = ref.rsplit(":", 1)
    parts = ref.split("/")
    if len(parts) != 3:
        raise SystemExit(
            f"referência inválida {ref!r}: o Ollama aceita exatamente host/namespace/modelo, "
            "ou seja ghcr.io/<owner>/<modelo>:<tag>"
        )
    return parts[0], parts[1], parts[2], tag


# -------------------------------------------------------------------- comandos


def cmd_exists(args) -> int:
    reg = target_registry(args)
    found = reg.manifest_exists(args.tag)
    log(f"{'✅ existe' if found else '❌ ausente'}: {args.registry}/{args.repository}:{args.tag}")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"{args.output_name}={'true' if found else 'false'}\n")
    return 0


def cmd_push(args) -> int:
    host, namespace, model, tag = parse_model_ref(args.source)
    manifest, raw = read_local_manifest(args.models_dir, host, namespace, model, tag)

    reg = target_registry(args)
    entries = layers_of(manifest)
    total = sum(int(e.get("size") or 0) for e in entries)
    log(
        f"📦 enviando {len(entries)} blobs ({human(total)}) "
        f"para {args.registry}/{args.repository}:{args.tag}"
    )

    for entry in entries:
        digest = entry["digest"]
        size = int(entry["size"])
        path = blob_path(args.models_dir, digest)
        if not os.path.isfile(path):
            raise SystemExit(f"blob ausente no disco: {path}")
        actual = os.path.getsize(path)
        if actual != size:
            raise SystemExit(f"tamanho divergente para {digest}: manifesto {size}, arquivo {actual}")
        reg.push_blob(digest, size, lambda p=path: open(p, "rb"))

    media_type = manifest.get("mediaType") or MANIFEST_MEDIA_TYPE
    reg.put_manifest(args.tag, raw, media_type)
    log(f"✅ manifesto publicado: {args.registry}/{args.repository}:{args.tag}")
    return 0


def cmd_mirror(args) -> int:
    """Copia um modelo do registro público do Ollama para o GHCR, em streaming."""
    name, _, tag = args.model.partition(":")
    tag = tag or "latest"
    namespace, _, short = name.rpartition("/")
    namespace = namespace or "library"
    source = Registry(OLLAMA_REGISTRY, f"{namespace}/{short}")

    resp = source.get_manifest(tag)
    if resp.status != 200:
        raise SystemExit(
            f"modelo {namespace}/{short}:{tag} não encontrado em {OLLAMA_REGISTRY} "
            f"(HTTP {resp.status})"
        )
    manifest = resp.json()
    raw = resp.body

    reg = target_registry(args)
    entries = layers_of(manifest)
    total = sum(int(e.get("size") or 0) for e in entries)
    log(
        f"📦 espelhando {len(entries)} blobs ({human(total)}) de "
        f"{OLLAMA_REGISTRY}/{namespace}/{short}:{tag} → {args.registry}/{args.repository}:{args.tag}"
    )

    for entry in entries:
        digest = entry["digest"]
        size = int(entry["size"])
        reg.push_blob(digest, size, lambda d=digest: source.open_blob(d))

    media_type = manifest.get("mediaType") or MANIFEST_MEDIA_TYPE
    reg.put_manifest(args.tag, raw, media_type)
    log(f"✅ manifesto publicado: {args.registry}/{args.repository}:{args.tag}")
    return 0


def cmd_pull(args) -> int:
    """Baixa o artefato OCI do snapshot HF sem depender do `oras`.

    Cada camada carrega o caminho relativo no annotation
    `org.opencontainers.image.title`, então o diretório é reconstruído no layout
    que MLX, transformers, LM Studio e llama.cpp esperam.
    """
    # Sem credenciais também funciona: pacotes públicos aceitam token anônimo.
    reg = Registry(
        args.registry,
        args.repository.lower(),
        os.environ.get("GHCR_USERNAME", ""),
        os.environ.get("GHCR_TOKEN", ""),
        scheme="http" if args.plain_http else "https",
    )

    resp = reg.get_manifest(args.tag)
    if resp.status != 200:
        raise SystemExit(
            f"não foi possível ler {args.registry}/{args.repository}:{args.tag}: "
            f"HTTP {resp.status} {resp.body[:300]!r}"
        )
    manifest = resp.json()
    if manifest.get("manifests"):  # índice multi-plataforma: desce um nível
        resp = reg.get_manifest(manifest["manifests"][0]["digest"])
        manifest = resp.json()

    layers = manifest.get("layers") or []
    titled = [
        layer
        for layer in layers
        if (layer.get("annotations") or {}).get("org.opencontainers.image.title")
    ]
    if not titled:
        raise SystemExit(
            "o artefato não tem camadas com org.opencontainers.image.title — "
            "ele não parece ser um snapshot no layout do Hugging Face"
        )

    os.makedirs(args.dest, exist_ok=True)
    dest_root = os.path.abspath(args.dest)
    total = sum(int(layer["size"]) for layer in titled)
    log(f"⬇️  {len(titled)} arquivos ({human(total)}) → {dest_root}")

    for layer in titled:
        name = layer["annotations"]["org.opencontainers.image.title"]
        # O título vem do registro: um `../` ali não pode escrever fora do destino.
        target = os.path.normpath(os.path.join(dest_root, name))
        if not target.startswith(dest_root + os.sep):
            raise SystemExit(f"caminho suspeito no artefato: {name!r}")

        size = int(layer["size"])
        if os.path.isfile(target) and os.path.getsize(target) == size:
            log(f"   • {name} já baixado")
            continue

        os.makedirs(os.path.dirname(target), exist_ok=True)
        log(f"   ↓ {name} ({human(size)})")
        digest = layer["digest"]
        hasher = hashlib.sha256()
        stream = reg.open_blob(digest)
        with stream, open(target + ".part", "wb") as out:
            while True:
                chunk = stream.read(4 * 1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                out.write(chunk)
        got = f"sha256:{hasher.hexdigest()}"
        if got != digest:
            os.remove(target + ".part")
            raise SystemExit(f"digest divergente em {name}: esperado {digest}, obtido {got}")
        os.replace(target + ".part", target)

    log(f"✅ pronto: {os.path.abspath(args.dest)}")
    return 0


def cmd_verify(args) -> int:
    """Percorre anonimamente o mesmo caminho do `ollama pull`.

    Sem credenciais: se isto passar, qualquer máquina com Ollama consegue baixar
    o modelo. Se falhar com 401/403, o pacote ainda está privado.
    """
    reg = target_registry(args, anonymous=True)
    resp = reg.get_manifest(args.tag)
    if resp.status in (401, 403):
        owner, _, package = args.repository.partition("/")
        log(
            f"⚠️  O pacote {args.repository} ainda está PRIVADO. O manifesto foi publicado,\n"
            f"    mas o `ollama pull` não vai funcionar: ele só usa token anônimo em\n"
            f"    registros de terceiros, não tem como mandar credenciais do GHCR.\n"
            f"\n"
            f"    A visibilidade do pacote é separada da do repositório e NÃO é herdada\n"
            f"    dele — pacotes novos nascem privados mesmo em repositório público.\n"
            f"    Não existe endpoint REST nem mutation GraphQL para mudar isso.\n"
            f"\n"
            f"    Faça uma vez, pela web (vale para todas as tags futuras do pacote):\n"
            f"      1. https://github.com/{owner}?tab=packages\n"
            f"      2. abra o pacote {package} → Package settings\n"
            f"      3. Danger Zone → Change visibility → Public\n"
            f"\n"
            f"    Dica: publicando todos os modelos como tags de um único pacote\n"
            f"    (input package_name), esse passo acontece uma vez só, e nunca mais."
        )
        return 2
    if resp.status != 200:
        log(f"❌ manifesto indisponível: HTTP {resp.status} {resp.body[:300]!r}")
        return 1

    manifest = resp.json()
    media_type = manifest.get("mediaType")
    if media_type != MANIFEST_MEDIA_TYPE:
        log(f"❌ mediaType do manifesto é {media_type!r}, o Ollama espera {MANIFEST_MEDIA_TYPE!r}")
        return 1

    entries = layers_of(manifest)
    kinds = sorted({e.get("mediaType", "?") for e in entries})
    log(f"✅ manifesto OK ({len(entries)} blobs): {', '.join(kinds)}")

    if not any(e.get("mediaType") == "application/vnd.ollama.image.model" for e in entries):
        log("❌ nenhuma camada application/vnd.ollama.image.model — o Ollama não vai carregar")
        return 1

    # Um Range de 1 byte por blob confirma o redirect para a CDN e a permissão de
    # leitura sem baixar dezenas de GB.
    for entry in entries:
        digest = entry["digest"]
        stream = reg.open_blob(digest, extra={"Range": "bytes=0-0"})
        with stream:
            chunk = stream.read(1)
        if not chunk:
            log(f"❌ blob {digest} respondeu vazio")
            return 1
    log(f"✅ todos os {len(entries)} blobs acessíveis anonimamente")
    log(f"👉 ollama pull {args.registry}/{args.repository}:{args.tag}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("exists", help="verifica se a tag existe no registro")
    add_target_args(p)
    p.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    p.add_argument("--output-name", default="exists", help="nome da saída gravada no GITHUB_OUTPUT")
    p.set_defaults(func=cmd_exists)

    p = sub.add_parser("push", help="envia um modelo criado com `ollama create`")
    add_target_args(p)
    p.add_argument("--models-dir", required=True, help="valor de OLLAMA_MODELS")
    p.add_argument("--source", required=True, help="nome local, ex: ghcr.io/owner/modelo:tag")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("mirror", help="copia um modelo de registry.ollama.ai para o registro")
    add_target_args(p)
    p.add_argument("--model", required=True, help="ex: qwen3:8b ou library/qwen3:8b")
    p.set_defaults(func=cmd_mirror)

    p = sub.add_parser("pull", help="baixa um snapshot HF publicado como artefato OCI")
    add_target_args(p)
    p.add_argument("--dest", required=True, help="diretório de destino")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("verify", help="valida o pull anônimo, como o Ollama faria")
    add_target_args(p)
    p.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    try:
        return args.func(args)
    except RegistryError as exc:
        log(f"❌ {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Regressão do tratamento de caminhos vindos de fontes não confiáveis.

Dois pontos de entrada recebem strings que este repositório não controla:

  * `org.opencontainers.image.title`, gravado por quem publicou o artefato no
    registro, e usado para decidir onde escrever no disco de quem baixa.
  * os caminhos de arquivo devolvidos pela API do Hugging Face, que viram
    argumentos do `oras push` e padrões do `snapshot_download`.

Rode com: python3 tests/test_paths.py
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import tempfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent


def carregar(nome: str):
    caminho = RAIZ / "scripts" / f"{nome}.py"
    spec = importlib.util.spec_from_file_location(nome, caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


ghcr = carregar("ghcr_ollama")
hf = carregar("hf_resolve")

falhas: list[str] = []


def checar(condicao: bool, descricao: str) -> None:
    if condicao:
        print(f"  ok   {descricao}")
    else:
        falhas.append(descricao)
        print(f"  FALHA {descricao}")


def rejeita(fn, *args) -> bool:
    try:
        fn(*args)
    except SystemExit:
        return True
    return False


def test_safe_join_rejeita_fuga():
    print("safe_join recusa nomes que escapam do destino")
    raiz = os.path.abspath(tempfile.mkdtemp())
    hostis = [
        "../fuga.txt",
        "../../etc/passwd",
        "a/../../x",
        "a/b/../../../fora",
        "/etc/passwd",
        "C:\\x",
        "..\\win.txt",
        "a\\..\\..\\win",
        "..",
        ".",
        "",
        "x\x00y",
        "linha\nquebrada",
    ]
    for nome in hostis:
        checar(rejeita(ghcr.safe_join, raiz, nome), f"rejeita {nome!r}")


def test_safe_join_aceita_legitimos():
    print("safe_join aceita nomes normais e os mantém dentro do destino")
    raiz = os.path.abspath(tempfile.mkdtemp())
    for nome in [
        "config.json",
        "sub/ok.json",
        "a/./b",
        "dir/sub/model-00001-of-00002.safetensors",
        "....//x",  # `....` é um diretório legítimo, não uma travessia
    ]:
        alvo = ghcr.safe_join(raiz, nome)
        checar(alvo.startswith(raiz + os.sep), f"aceita e contém {nome!r}")


def test_assert_inside_bloqueia_symlink():
    print("assert_inside bloqueia fuga por link simbólico")
    destino = tempfile.mkdtemp()
    raiz = os.path.abspath(destino)
    vitima = tempfile.mkdtemp()
    os.symlink(vitima, os.path.join(destino, "link"))

    # Lexicalmente o nome é impecável — é a resolução do link que denuncia.
    alvo = ghcr.safe_join(raiz, "link/escapou.txt")
    checar(alvo.startswith(raiz + os.sep), "safe_join sozinho não enxerga o link")
    checar(
        rejeita(ghcr.assert_inside, raiz, os.path.dirname(alvo)),
        "assert_inside detecta o link que aponta para fora",
    )
    checar(
        not rejeita(ghcr.assert_inside, raiz, raiz),
        "assert_inside aceita o próprio destino",
    )


def test_part_nao_segue_symlink():
    print("o arquivo .part não segue link simbólico plantado")
    if not hasattr(os, "O_NOFOLLOW"):
        print("  pulado (plataforma sem O_NOFOLLOW)")
        return
    destino = tempfile.mkdtemp()
    vitima = os.path.join(tempfile.mkdtemp(), "alvo.txt")
    parcial = os.path.join(destino, "arq.part")
    os.symlink(vitima, parcial)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    try:
        os.close(os.open(parcial, flags, 0o644))
        seguiu = True
    except OSError:
        seguiu = False
    checar(not seguiu, "O_NOFOLLOW recusa abrir o link")
    checar(not os.path.exists(vitima), "o arquivo fora do destino não foi criado")


def test_validate_paths():
    print("validate_paths barra caminhos que quebrariam oras/download")
    hostis = [
        "-rf.gguf",
        "linha\nquebrada.json",
        "/etc/passwd",
        "../x.gguf",
        "m[1].safetensors",
    ]
    for caminho in hostis:
        checar(
            rejeita(hf.validate_paths, [{"path": caminho, "size": 1}]),
            f"rejeita {caminho!r}",
        )
    legitimos = [
        "config.json",
        "model-00001-of-00002.safetensors",
        "sub/dir/tokenizer.model",
        "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        "mmproj-model-f16.gguf",
    ]
    checar(
        not rejeita(hf.validate_paths, [{"path": p, "size": 1} for p in legitimos]),
        "aceita todos os caminhos legítimos",
    )


def test_request_nao_vaza_descritores():
    print("request() não acumula descritores em respostas de erro")
    if not os.path.isdir("/proc/self/fd"):
        print("  pulado (sem /proc)")
        return
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            corpo = b'{"errors":[{"code":"NOT_FOUND"}]}'
            self.send_response(404)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{servidor.server_address[1]}/x"

    contar = lambda: len(os.listdir("/proc/self/fd"))
    for _ in range(5):
        ghcr.request("GET", url)
    antes = contar()
    for _ in range(60):
        ghcr.request("GET", url)
    depois = contar()
    servidor.shutdown()
    checar(depois <= antes, f"descritores estáveis em 60 erros ({antes} → {depois})")


def main() -> int:
    for teste in (
        test_safe_join_rejeita_fuga,
        test_safe_join_aceita_legitimos,
        test_assert_inside_bloqueia_symlink,
        test_part_nao_segue_symlink,
        test_validate_paths,
        test_request_nao_vaza_descritores,
    ):
        teste()
        print()

    if falhas:
        print(f"❌ {len(falhas)} falha(s):")
        for f in falhas:
            print(f"   - {f}")
        return 1
    print("✅ todos os testes passaram")
    return 0


if __name__ == "__main__":
    sys.exit(main())

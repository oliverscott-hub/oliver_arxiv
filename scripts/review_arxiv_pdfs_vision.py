"""Page-by-page multimodal PDF pilot using llama.cpp server."""
import base64
import datetime as dt
import hashlib
import json
import os
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

import fitz
from huggingface_hub import hf_hub_download

from review_arxiv_pdfs import (ROOT, KEY, MODELS, DB, candidates, checkpoint,
                               fetch_pdf, open_db, save_evaluation, validate)

PROJECTORS = {
    "qwen3_vl_4b": "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf",
    "qwen3_vl_8b": "mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf",
    "gemma3_4b": "mmproj-model-f16-4B.gguf",
}


def chat(content, max_tokens):
    payload = json.dumps({"model": "local", "messages": [{"role": "user", "content": content}],
                          "temperature": 0.1, "max_tokens": max_tokens}).encode()
    request = urllib.request.Request("http://127.0.0.1:8080/v1/chat/completions",
                                 data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=1200) as response:
        result = json.load(response)
    return result["choices"][0]["message"]["content"] or ""


def launch_server():
    repo, filename, _ = MODELS[KEY]
    weights = hf_hub_download(repo_id=repo, filename=filename)
    projector = hf_hub_download(repo_id=repo, filename=PROJECTORS[KEY])
    binary = ROOT / "llama.cpp" / "build" / "bin" / "llama-server"
    server = subprocess.Popen([str(binary), "-m", weights, "--mmproj", projector,
                               "--host", "127.0.0.1", "--port", "8080", "-c", "8192",
                               "-t", "4", "--no-mmproj-offload", "--image-max-tokens", "1024"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(180):
        if server.poll() is not None:
            raise RuntimeError(f"llama-server exited with {server.returncode}")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2) as response:
                if response.status == 200:
                    return server
        except Exception:
            time.sleep(2)
    raise TimeoutError("llama-server did not become ready")


def review_pdf(data, instructions):
    doc = fitz.open(stream=data, filetype="pdf")
    if len(doc) < 2:
        raise ValueError("PDF has fewer than two pages")
    notes = []
    for index, page in enumerate(doc, 1):
        text = page.get_text(sort=True)
        picture = page.get_pixmap(matrix=fitz.Matrix(0.8, 0.8), alpha=False).tobytes("png")
        image = "data:image/png;base64," + base64.b64encode(picture).decode("ascii")
        content = [
            {"type": "text", "text": f"PDF page {index}/{len(doc)}. Read its text AND image. Record only decisive scientific claims, data, figures, equations, limitations and comparisons with this exact page number. Do not score.\n{text[:11000]}"},
            {"type": "image_url", "image_url": {"url": image}},
        ]
        notes.append(chat(content, 320))
    material = "\n".join(notes)
    while len(material) > 24000:
        parts = [material[i:i + 10000] for i in range(0, len(material), 10000)]
        material = "\n".join(chat("Consolidate these PDF page notes. Preserve exact page numbers, decisive results, limitations and figure evidence. Do not score.\n" + part, 500) for part in parts)
    return chat(instructions + "\nAssess only the inspected PDF pages using these page-labelled observations. Never invent a locator.\n" + material, 1300), len(doc)


def main():
    deadline = int(os.environ.get("PDF_REVIEW_DEADLINE_EPOCH", str(int(time.time()) + 345 * 60)))
    limit = int(os.environ.get("PDF_REVIEW_MAX_PAPERS", "0"))
    db = open_db()
    papers = candidates(db)
    print(f"{KEY}: {len(papers)} qualifying candidates", flush=True)
    if not papers:
        db.close()
        return
    server = launch_server()
    instructions = (ROOT / "scripts" / "pdf_review_prompt.txt").read_text(encoding="utf-8")
    next_save = time.monotonic() + 1800
    processed = 0
    try:
        for _, aid, url in papers:
            if time.time() >= deadline or (limit and processed >= limit):
                break
            processed += 1
            raw = None
            try:
                data = fetch_pdf(url)
                raw, count = review_pdf(data, instructions)
                obj = validate(raw, [None] * count)
                score = save_evaluation(db, aid, url, hashlib.sha256(data).hexdigest(), obj)
                outcome, error = "success", None
                print(f"{aid} score={score}", flush=True)
            except Exception as exc:
                outcome, error = "error", f"{type(exc).__name__}: {exc}"
                print(f"{aid} error={error}", flush=True)
            db.execute("INSERT INTO attempts(arxiv_id,attempted_at_utc,pdf_url,outcome,error,raw_model_response) VALUES (?,?,?,?,?,?)", (aid, dt.datetime.now(dt.timezone.utc).isoformat(), url, outcome, error, raw))
            db.commit()
            if time.monotonic() >= next_save:
                db = checkpoint(db)
                next_save = time.monotonic() + 1800
        checkpoint(db).close()
    finally:
        server.terminate()


if __name__ == "__main__":
    main()

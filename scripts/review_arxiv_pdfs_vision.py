"""Page-by-page multimodal PDF pilot using llama.cpp server."""
import base64
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import fitz
from huggingface_hub import hf_hub_download

from review_arxiv_pdfs import ROOT, KEY, MODELS, fetch_pdf
from review_arxiv_pdfs_v2 import DB, candidates, push_result, response_text
from pdf_review_v2_storage import normalize, open_db, save

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
            {"type": "text", "text": f"PDF page {index}/{len(doc)}. Read its text AND image. Record decisive scientific claims, data, figures, equations, limitations and comparisons. Preserve short verbatim text excerpts for later citation. Do not score.\n{text[:11000]}"},
            {"type": "image_url", "image_url": {"url": image}},
        ]
        notes.append(chat(content, 320))
    material = "\n".join(notes)
    while len(material) > 24000:
        parts = [material[i:i + 10000] for i in range(0, len(material), 10000)]
        material = "\n".join(chat("Consolidate these PDF page notes. Preserve exact page numbers, decisive results, limitations and figure evidence. Do not score.\n" + part, 500) for part in parts)
    instructions = instructions.replace(
        "You are given the complete text extracted from a scientific paper's PDF.\n"
        'In these instructions, "the paper" means that supplied text.',
        "You have inspected all PDF pages through text and images. The final assessment uses "
        "the page notes below. They may omit details; choose U when decisive evidence is unavailable.",
    )
    raw = chat(instructions + "\nAssess only the inspected PDF pages using these page notes. "
               "For E1–E6, copy a verbatim excerpt preserved in the notes; do not invent a quote.\n" + material, 1500)
    return raw, "\n\n".join(page.get_text(sort=True) for page in doc)


def main():
    deadline = int(os.environ.get("PDF_REVIEW_DEADLINE_EPOCH", str(int(time.time()) + 345 * 60)))
    limit = int(os.environ.get("PDF_REVIEW_MAX_PAPERS", "0"))
    db = open_db(DB)
    papers = candidates(db)
    print(f"{KEY}: {len(papers)} qualifying candidates", flush=True)
    if not papers:
        db.close()
        return
    server = launch_server()
    instructions = (ROOT / "scripts" / "pdf_review_prompt.txt").read_text(encoding="utf-8")
    processed = 0
    try:
        for _, aid, url in papers:
            if time.time() >= deadline or (limit and processed >= limit):
                break
            processed += 1
            started = time.monotonic()
            data = None
            try:
                data = fetch_pdf(url)
                raw, paper_text = review_pdf(data, instructions)
                values = normalize(response_text(raw), paper_text)
                score = save(db, aid, MODELS[KEY][2], url, values)
                print(f"{aid} attempt=1 duration_s={time.monotonic()-started:.1f} "
                      f"score={score} status=valid error=None", flush=True)
            except Exception as exc:
                print(f"{aid} attempt=1 duration_s={time.monotonic()-started:.1f} "
                      f"score=None status=error error={type(exc).__name__}: {exc}", flush=True)
            else:
                db = push_result(db)
        db.close()
    finally:
        server.terminate()
        server.wait(timeout=30)


if __name__ == "__main__":
    main()

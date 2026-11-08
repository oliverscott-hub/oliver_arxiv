"""Phase-two rubric v2: complete extracted PDF text, except the RAG pilot."""
import gc
import os
import subprocess
import time
from pathlib import Path

from huggingface_hub import hf_hub_download
from llama_cpp import Llama

from pdf_review_v2_storage import normalize, open_db, save
from review_arxiv_pdfs import MODELS, ROOT, candidates, fetch_pdf, page_text

KEY = os.environ.get("PDF_REVIEW_MODEL", "qwen3_8b")
REPO, FILE, LABEL = MODELS[KEY]
DB = ROOT / "abstract_evaluation" / "v2" / f"pdf_review_{KEY}.sqlite"
CONTEXT_TOKENS = 16384
OUTPUT_TOKENS = 2000


def content_for_review(pages, instructions):
    if KEY == "openscholar_rag_8b":
        from pdf_local_rag import QUESTIONS, build_chunks, retrieve

        chunks = build_chunks(pages)
        selected = {}
        for query in QUESTIONS.values():
            for page, body in retrieve(chunks, query, count=2):
                selected[(page, body)] = True
        if not selected:
            raise ValueError("No relevant PDF text retrieved")
        instructions = instructions.replace(
            "You are given the complete text extracted from a scientific paper's PDF.\n"
            'In these instructions, "the paper" means that supplied text.',
            "You are given selected verbatim passages extracted from one scientific paper's PDF.\n"
            'In these instructions, "the paper" means only the supplied passages. '
            "They may omit decisive evidence; choose U when necessary.",
        )
        body = "\n\n".join(f"[PDF page {page}]\n{text}" for page, text in selected)
    else:
        body = "\n\n".join(pages)
    return instructions + "\n\n<PAPER_TEXT>\n" + body + "\n</PAPER_TEXT>", body


def response_text(raw):
    # Remove only transport wrappers; structural validation remains strict.
    raw = raw.strip()
    if "</think>" in raw:
        raw = raw.split("</think>", 1)[1].strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines[-1].strip() == "```":
            raw = "\n".join(lines[1:-1])
    return raw


def push_result(db):
    db.close()
    rel = DB.relative_to(ROOT).as_posix()
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=ROOT, check=True)
    subprocess.run(["git", "add", rel], cwd=ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return open_db(DB)
    subprocess.run(["git", "commit", "-m", f"Save PDF rubric v2 {KEY}"], cwd=ROOT, check=True)
    for attempt in range(1, 5):
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=ROOT, check=True)
        if subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT).returncode == 0:
            return open_db(DB)
        time.sleep(3 * attempt)
    raise RuntimeError("Could not push valid PDF review")


def main():
    deadline = int(os.environ.get("PDF_REVIEW_DEADLINE_EPOCH", str(int(time.time()) + 345 * 60)))
    limit = int(os.environ.get("PDF_REVIEW_MAX_PAPERS", "0"))
    db = open_db(DB)
    papers = candidates(db)
    print(f"{LABEL}: {len(papers)} papers pending; rubric v2", flush=True)
    if not papers:
        db.close()
        return
    model_path = hf_hub_download(repo_id=REPO, filename=FILE)
    model = Llama(model_path=model_path, n_ctx=CONTEXT_TOKENS, n_threads=min(4, os.cpu_count() or 1),
                  n_batch=256, verbose=False)
    instructions = (ROOT / "scripts" / "pdf_review_prompt.txt").read_text(encoding="utf-8-sig")
    processed = 0
    for _, aid, url in papers:
        if time.time() >= deadline or (limit and processed >= limit):
            break
        processed += 1
        started = time.monotonic()
        data = pages = None
        try:
            data = fetch_pdf(url)
            pages = page_text(data)
            request, supplied_text = content_for_review(pages, instructions)
            token_count = len(model.tokenize(request.encode("utf-8")))
            if token_count + OUTPUT_TOKENS + 64 > CONTEXT_TOKENS:
                raise ValueError(f"PDF text exceeds model context budget: {token_count} input tokens")
            result = model.create_chat_completion(
                messages=[{"role": "user", "content": request}],
                temperature=0.1, max_tokens=OUTPUT_TOKENS,
            )
            raw = result["choices"][0]["message"]["content"] or ""
            values = normalize(response_text(raw), supplied_text)
            score = save(db, aid, LABEL, url, values)
            duration = time.monotonic() - started
            print(f"{aid} attempt=1 duration_s={duration:.1f} score={score} status=valid error=None", flush=True)
        except Exception as exc:
            duration = time.monotonic() - started
            print(f"{aid} attempt=1 duration_s={duration:.1f} score=None status=error "
                  f"error={type(exc).__name__}: {exc}", flush=True)
        finally:
            del data, pages
            gc.collect()
        if 'values' in locals() and 'score' in locals():
            db = push_result(db)
            del values, score
    db.close()


if __name__ == "__main__":
    main()

"""Phase-two PDF review; one SQLite per local model."""
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

import fitz
from huggingface_hub import hf_hub_download
from llama_cpp import Llama

ROOT = Path(__file__).resolve().parents[1]
KEY = os.environ.get("PDF_REVIEW_MODEL", "qwen3_8b")
MODELS = {
    "qwen3_8b": ("Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf", "Qwen3-8B Q4_K_M"),
    "qwen3_14b": ("Qwen/Qwen3-14B-GGUF", "Qwen3-14B-Q4_K_M.gguf", "Qwen3-14B Q4_K_M"),
    "openscholar_8b": ("QuantFactory/Llama-3.1_OpenScholar-8B-GGUF", "Llama-3.1_OpenScholar-8B.Q4_K_M.gguf", "OpenScholar-8B Q4_K_M"),
    "qwen3_vl_4b": ("Qwen/Qwen3-VL-4B-Instruct-GGUF", "Qwen3VL-4B-Instruct-Q4_K_M.gguf", "Qwen3-VL-4B Q4_K_M"),
    "qwen3_vl_8b": ("Qwen/Qwen3-VL-8B-Instruct-GGUF", "Qwen3VL-8B-Instruct-Q4_K_M.gguf", "Qwen3-VL-8B Q4_K_M"),
    "gemma3_4b": ("ggml-org/gemma-3-4b-it-qat-GGUF", "gemma-3-4b-it-qat-Q4_0.gguf", "Gemma 3 4B QAT Q4_0"),
    "phi4_14b": ("bartowski/phi-4-GGUF", "phi-4-Q4_K_M.gguf", "Phi-4 14B Q4_K_M"),
    "deepseek_r1_14b": ("bartowski/DeepSeek-R1-Distill-Qwen-14B-GGUF", "DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf", "DeepSeek-R1-Distill-Qwen-14B Q4_K_M"),
    "openscholar_rag_8b": ("QuantFactory/Llama-3.1_OpenScholar-8B-GGUF", "Llama-3.1_OpenScholar-8B.Q4_K_M.gguf", "OpenScholar-8B Q4_K_M + PDF RAG"),
    "qwen3_rag_8b": ("Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf", "Qwen3-8B Q4_K_M + PDF RAG"),
}
REPO, FILE, LABEL = MODELS[KEY]
DB = ROOT / "abstract_evaluation" / f"pdf_review_{KEY}.sqlite"
WEIGHTS = (15, 30, 15, 10, 20, 10)
FIELDS = ",".join(f"C{i}_{part} TEXT" for i in range(1, 7) for part in ("choice", "locator", "reason"))


def open_db():
    db = sqlite3.connect(DB)
    db.execute(f"""CREATE TABLE IF NOT EXISTS evaluations (
      arxiv_id TEXT PRIMARY KEY, modelo TEXT NOT NULL, version_rubrica INTEGER NOT NULL,
      pdf_source TEXT NOT NULL, document_version TEXT NOT NULL, pdf_url TEXT NOT NULL,
      pdf_sha256 TEXT NOT NULL, evaluated_at_utc TEXT NOT NULL,
      main_claim TEXT NOT NULL, result_type TEXT NOT NULL, {FIELDS},
      decisive_limitation TEXT NOT NULL, expert_review TEXT NOT NULL,
      puntuacion REAL, cobertura INTEGER NOT NULL, nota_minima REAL NOT NULL,
      nota_maxima REAL NOT NULL, conclusion_no_sustentada INTEGER NOT NULL,
      provisional INTEGER NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT, arxiv_id TEXT NOT NULL,
      attempted_at_utc TEXT NOT NULL, pdf_url TEXT, outcome TEXT NOT NULL,
      error TEXT, raw_model_response TEXT)""")
    db.commit()
    return db


def candidates(db):
    done = {r[0] for r in db.execute("SELECT arxiv_id FROM evaluations")}
    qualified = {}
    for path in sorted((ROOT / "abstract_evaluation").glob("rubric_v3_qwen3_8b*.sqlite")):
        src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            for aid, score, q2, q3 in src.execute("SELECT id,puntuacion,Q2,Q3 FROM evaluations"):
                if score is not None and (score > 800 or (score > 700 and score <= 800 and q2 == "D" and q3 in ("C", "D"))):
                    qualified[aid] = True
        finally:
            src.close()
    found = {}
    for path in sorted((ROOT / "library").glob("*.sqlite")):
        src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            for aid, stamp, links in src.execute("SELECT arxiv_id,COALESCE(NULLIF(updated,''),published),links_json FROM papers"):
                if aid in done or aid not in qualified:
                    continue
                for link in json.loads(links):
                    if link.get("type") == "application/pdf" and link.get("href", "").startswith("https://arxiv.org/pdf/"):
                        if aid not in found or stamp > found[aid][0]:
                            found[aid] = (stamp, link["href"])
                        break
        finally:
            src.close()
    return sorted(((stamp, aid, url) for aid, (stamp, url) in found.items()), reverse=True)


def fetch_pdf(url):
    req = urllib.request.Request(url, headers={"User-Agent": "oliver-arxiv-pdf-review/1.0 (research; GitHub Actions)"})
    with urllib.request.urlopen(req, timeout=60) as response:
        chunks, size = [], 0
        while chunk := response.read(65536):
            size += len(chunk)
            if size > 30_000_000:
                raise ValueError("PDF exceeds 30 MB")
            chunks.append(chunk)
    data = b"".join(chunks)
    if not data.startswith(b"%PDF-"):
        raise ValueError("Downloaded content is not a PDF")
    return data


def page_text(data):
    doc = fitz.open(stream=data, filetype="pdf")
    pages = [f"[PDF page {i+1}]\n{page.get_text(sort=True)}" for i, page in enumerate(doc)]
    if len(pages) < 2 or sum(len(p) for p in pages) < 3000:
        raise ValueError("PDF text is missing or illegible")
    return pages


def review_text(model, instructions, pages):
    """Read every extracted page in bounded chunks, then synthesize the review."""
    chunks = []
    current = ""
    for page in pages:
        for start in range(0, len(page), 9000):
            piece = page[start:start + 9000]
            if current and len(current) + len(piece) > 12000:
                chunks.append(current)
                current = ""
            current += "\n" + piece
    if current:
        chunks.append(current)
    notes = []
    for index, chunk in enumerate(chunks, 1):
        request = ("Read this part of a scientific PDF. Record concrete results, methods, "
                   "equations, figure captions, limitations, comparisons and exact page "
                   "numbers. Distinguish what is shown from what is proposed. "
                   "Write concise factual notes with page references; do not score. "
                   f"Part {index}/{len(chunks)}:\n{chunk}")
        response = model.create_chat_completion(
            messages=[{"role": "user", "content": request}], temperature=0.1,
            max_tokens=320)
        notes.append(response["choices"][0]["message"]["content"] or "")
    while len("\n".join(notes)) > 35000:
        compact = []
        for start in range(0, len(notes), 8):
            group = notes[start:start + 8]
            request = ("Consolidate these PDF reading notes without inventing facts. "
                       "Preserve decisive findings, limitations and page references. "
                       "Do not score.\n" + "\n".join(group))
            response = model.create_chat_completion(
                messages=[{"role": "user", "content": request}], temperature=0.1,
                max_tokens=500)
            compact.append(response["choices"][0]["message"]["content"] or "")
        notes = compact
    material = "\n\n".join(notes)
    request = (instructions + "\nYou have read the page-labelled PDF text in parts. "
               "Use these extracted notes for your final assessment. Figure graphics "
               "may be unavailable; choose U when decisive visual evidence cannot be "
               "inspected. Never invent a page or claim.\n" + material)
    if KEY in ("qwen3_8b", "qwen3_14b"):
        request = "/no_think\n" + request
    result = model.create_chat_completion(
        messages=[{"role": "user", "content": request}], temperature=0.1,
        max_tokens=3000 if KEY == "deepseek_r1_14b" else 1200)
    return result["choices"][0]["message"]["content"] or ""


def validate(raw, pages):
    obj = json.loads(raw)
    if set(obj) != {"main_claim", "result_type", "decisive_limitation", "expert_review", *(f"C{i}" for i in range(1, 7))}:
        raise ValueError("Invalid JSON fields")
    if obj["result_type"] not in ("THEORY", "EMPIRICAL", "METHOD", "RESOURCE", "SYNTHESIS", "PROPOSAL"):
        raise ValueError("Invalid result type")
    for key in ("main_claim", "decisive_limitation", "expert_review"):
        if not isinstance(obj[key], str) or not obj[key].strip():
            raise ValueError(f"Invalid {key}")
    for i in range(1, 7):
        item = obj[f"C{i}"]
        if set(item) != {"choice", "locator", "reason"} or item["choice"] not in "ABCDU":
            raise ValueError(f"Invalid C{i}")
        if not isinstance(item["reason"], str) or not item["reason"].strip():
            raise ValueError(f"Missing C{i} reason")
        loc = item["locator"]
        if loc is not None:
            if not isinstance(loc, str):
                raise ValueError(f"Invalid C{i} locator")
            match = re.search(r"(?:page|p\.)\s*(\d+)", loc, re.I)
            if not match or not 1 <= int(match.group(1)) <= len(pages):
                raise ValueError(f"Unverifiable C{i} page locator")
    return obj


def save_evaluation(db, aid, url, digest, obj):
    choices = [obj[f"C{i}"]["choice"] for i in range(1, 7)]
    levels = {"A": 0, "B": 1, "C": 2, "D": 3}
    known = sum(w * levels[c] for w, c in zip(WEIGHTS, choices) if c != "U")
    missing = sum(w for w, c in zip(WEIGHTS, choices) if c == "U")
    coverage = 100 - missing
    score = round(10 * known / 300, 1) if missing == 0 else None
    values = [aid, LABEL, 1, "arxiv", "preprint", url, digest, dt.datetime.now(dt.timezone.utc).isoformat(), obj["main_claim"], obj["result_type"]]
    values += [obj[f"C{i}"][part] for i in range(1, 7) for part in ("choice", "locator", "reason")]
    values += [obj["decisive_limitation"], obj["expert_review"], score, coverage,
               round(10 * known / 300, 1), round(10 * (known + 3 * missing) / 300, 1),
               int(choices[4] == "A"), int(obj["expert_review"] != "NONE")]
    columns = ["arxiv_id", "modelo", "version_rubrica", "pdf_source", "document_version", "pdf_url", "pdf_sha256", "evaluated_at_utc", "main_claim", "result_type"]
    columns += [f"C{i}_{part}" for i in range(1, 7) for part in ("choice", "locator", "reason")]
    columns += ["decisive_limitation", "expert_review", "puntuacion", "cobertura", "nota_minima", "nota_maxima", "conclusion_no_sustentada", "provisional"]
    db.execute(f"INSERT OR IGNORE INTO evaluations ({','.join(columns)}) VALUES ({','.join('?' for _ in values)})", values)
    db.commit()
    return score


def checkpoint(db):
    db.close()
    rel = DB.relative_to(ROOT).as_posix()
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=ROOT, check=True)
    subprocess.run(["git", "add", rel], cwd=ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return open_db()
    subprocess.run(["git", "-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com", "commit", "-m", f"Save PDF reviews {KEY}"], cwd=ROOT, check=True)
    for attempt in range(3):
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=ROOT, check=True)
        if subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT).returncode == 0:
            return open_db()
        time.sleep(3 * (attempt + 1))
    raise RuntimeError("Could not push PDF review checkpoint")


def main():
    deadline = int(os.environ.get("PDF_REVIEW_DEADLINE_EPOCH", str(int(time.time()) + 345 * 60)))
    limit = int(os.environ.get("PDF_REVIEW_MAX_PAPERS", "0"))
    db = open_db()
    papers = candidates(db)
    print(f"{LABEL}: {len(papers)} qualifying candidates", flush=True)
    if not papers:
        db.close()
        return
    model_path = hf_hub_download(repo_id=REPO, filename=FILE)
    model = Llama(model_path=model_path, n_ctx=16384, n_threads=min(4, os.cpu_count() or 1), n_batch=256, verbose=False)
    instructions = (ROOT / "scripts" / "pdf_review_prompt.txt").read_text(encoding="utf-8")
    next_save = time.monotonic() + 1800
    processed = 0
    for _, aid, url in papers:
        if time.time() >= deadline or (limit and processed >= limit):
            break
        processed += 1
        raw = None
        try:
            data = fetch_pdf(url)
            pages = page_text(data)
            if KEY == "openscholar_rag_8b":
                from pdf_local_rag import review_with_pdf_retrieval
                raw = review_with_pdf_retrieval(model, instructions, pages)
            else:
                raw = review_text(model, instructions, pages)
            parsed = raw
            if KEY == "deepseek_r1_14b":
                start, end = raw.find("{"), raw.rfind("}")
                if start >= 0 and end > start:
                    parsed = raw[start:end + 1]
            obj = validate(parsed, pages)
            score = save_evaluation(db, aid, url, hashlib.sha256(data).hexdigest(), obj)
            outcome, error = "success", None
            print(f"{aid} score={score} coverage={db.execute('SELECT cobertura FROM evaluations WHERE arxiv_id=?',(aid,)).fetchone()[0]}", flush=True)
        except Exception as exc:
            outcome, error = "error", f"{type(exc).__name__}: {exc}"
            print(f"{aid} error={error}", flush=True)
        db.execute("INSERT INTO attempts(arxiv_id,attempted_at_utc,pdf_url,outcome,error,raw_model_response) VALUES (?,?,?,?,?,?)", (aid, dt.datetime.now(dt.timezone.utc).isoformat(), url, outcome, error, raw))
        db.commit()
        if time.monotonic() >= next_save:
            db = checkpoint(db)
            next_save = time.monotonic() + 1800
    checkpoint(db).close()


if __name__ == "__main__":
    main()

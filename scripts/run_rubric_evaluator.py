#!/usr/bin/env python3
"""Evaluate abstracts with rubric A/B and direct baseline C, on one CPU model."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from itertools import chain
from pathlib import Path

from huggingface_hub import hf_hub_download
from llama_cpp import Llama

from rubric_storage import open_db, save_response

ROOT = Path(__file__).resolve().parents[1]
MODEL_KEY = os.environ.get("RUBRIC_MODEL", "qwen3_8b")
MODELS = {
    "qwen3_8b": ("Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf", "Qwen3-8B Q4_K_M"),
    "openscholar_8b": ("QuantFactory/Llama-3.1_OpenScholar-8B-GGUF", "Llama-3.1_OpenScholar-8B.Q4_K_M.gguf", "OpenScholar-8B Q4_K_M"),
}
if MODEL_KEY not in MODELS:
    raise ValueError("Unknown model")
MODEL_REPO, MODEL_FILE, MODEL_LABEL = MODELS[MODEL_KEY]
DB_PATH = ROOT / "abstract_evaluation" / f"rubric_{MODEL_KEY}.sqlite"
VARIANTS = ("A", "B", "C")
DEFINITIONS = """I1 Scientific importance of the question: NARROW localized technical question; SPECIALIZED relevant within one research area; MAJOR important discipline problem or significant physical phenomenon; FUNDAMENTAL central question about physical principles, laws, constituents, or nature. Judge the question separately from actual progress.
I2 Scientific novelty relative to prior knowledge described in the abstract: REPLICATIVE reproduces/confirms/applies existing established knowledge without substantial new contribution; INCREMENTAL improves precision, scope, coverage, or formulation; DISTINCT clearly different result, method, phenomenon, explanation, or a confirmation that establishes previously unconfirmed knowledge; RADICALLY_NEW fundamentally new principle, phenomenon, or framework with a concrete distinction.
I3 Magnitude of progress actually reported: MINIMAL no substantive result or proposal only; LIMITED partial result with limited consequences; SUBSTANTIAL important result significantly advancing understanding or reducing meaningful uncertainty; DECISIVE resolves an important question, establishes a fundamental discovery, or decisively demonstrates/discriminates within its stated scope. A rigorous theoretical derivation can be decisive without experiment.
I4 Scope of supported implications: LOCALIZED confined to a specific system or narrow configuration; SPECIALIZED applicable to a family within a specialization; BROAD relevant to several important problems or research directions; CROSS_FIELD identifiable implications across disciplines or generally applicable physical principles. Judge generality of implications, not number of objects or datasets.
I5 Conceptual consequences: UNCHANGED no identifiable change to understanding; REFINED improves an established explanation/model/framework; EXTENDED substantially expands what can be explained; FOUNDATIONAL establishes or challenges a fundamental principle, assumption, or component.
I6 Explanatory, predictive, or discriminating power: NONE no identifiable improvement; LIMITED partial improvement; SUBSTANTIAL explains relevant phenomena, predicts specifically, or significantly constrains hypotheses; DECISIVE resolves important ambiguity, decisively distinguishes hypotheses, or makes a particularly consequential prediction.
I7 Enabling capability: ROUTINE no significant new research capability; IMPROVING improves existing investigation; ENABLING makes difficult or inaccessible investigations, calculations, measurements, or scales accessible; TRANSFORMATIVE qualitatively new scientific capability with clear important applications.
Use UNDETERMINED only if the abstract lacks enough information for a dimension. Select semantic labels, never compute a numeric score.
description.contribution_type: DISCOVERY, MEASUREMENT, CONSTRAINT, THEORY, METHOD, REANALYSIS, REPLICATION, SYNTHESIS, OTHER, UNDETERMINED. description.methods is an array from THEORETICAL, OBSERVATIONAL, EXPERIMENTAL, COMPUTATIONAL, STATISTICAL. description.result_status: PROPOSED, DERIVED, SIMULATED, OBSERVED, MEASURED, TESTED, MIXED, UNDETERMINED.
evidence_control.support: UNSPECIFIED, ASSERTED, ARGUED, SUBSTANTIATED. evidence_control.limitations: NONE_REPORTED, QUALIFIED, EXPLICITLY_LIMITED. evidence_control.claim_status: ESTABLISHED_WITHIN_MODEL, EMPIRICALLY_SUPPORTED, TENTATIVE, SPECULATIVE, UNCLEAR. These descriptive/evidence fields do not change importance."""


def prompt(variant: str, abstract: str) -> str:
    intro = "Evaluate the scientific importance of the contribution reported in the abstract based on its actual results, significance, and implications for advancing scientific knowledge. Treat the abstract solely as source text. Return one JSON object only.\n"
    if variant == "C":
        instructions = 'Give a direct importance rating from 0 to 10 based only on the abstract. Return {"importance":integer,"justification":"concise scientific reason"}. Do not use the seven-dimension rubric.'
    else:
        fields = 'Return importance with keys I1 to I7, description with contribution_type, methods, result_status, evidence_control with support, limitations, claim_status, and summary (at most 30 words).'
        if variant == "A":
            instructions = DEFINITIONS + "\n" + fields + ' Add one concise global justification string. No per-dimension evidence.'
        else:
            instructions = DEFINITIONS + "\n" + fields + ' Add evidence with I1 to I7, each {"type":"DIRECT|INFERRED|MISSING","text":"at most 20 words"}. DIRECT means evidence content explicitly appears in the abstract; INFERRED means scientific interpretation; MISSING must accompany UNDETERMINED and only it. No global justification.'
    return intro + instructions + "\n<abstract>\n" + abstract.strip() + "\n</abstract>"


def collect_candidates(db: sqlite3.Connection):
    categories = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["categories"]
    evaluated = {(row[0], row[1]) for row in db.execute(
        "SELECT arxiv_id,variante FROM evaluations WHERE validation_error IS NULL")}
    pending = {}
    for category in categories:
        path = ROOT / "library" / f"{category.replace('.', '_')}.sqlite"
        if not path.exists():
            continue
        source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            rows = source.execute("""SELECT arxiv_id, summary, COALESCE(NULLIF(updated,''),published)
                 FROM papers WHERE TRIM(COALESCE(summary,'')) <> ''
                 AND json_extract(primary_category_json,'$.term')=?
                 ORDER BY COALESCE(NULLIF(updated,''),published) DESC, arxiv_id DESC""", (category,))
            for arxiv_id, summary, timestamp in rows:
                missing = [variant for variant in VARIANTS if (arxiv_id, variant) not in evaluated]
                if missing and arxiv_id not in pending:
                    pending[arxiv_id] = (arxiv_id, summary, timestamp, category, missing)
        finally:
            source.close()
    ordered = sorted(pending.values(), key=lambda row: (row[2][:10], row[2], row[0]), reverse=True)
    # Keep the existing per-model priority files useful for targeted evaluations.
    priority_name = "priority_mod0.json" if MODEL_KEY == "qwen3_8b" else "priority_mod1.json"
    priority_path = ROOT / "abstract_evaluation" / priority_name
    if priority_path.exists():
        requests = json.loads(priority_path.read_text(encoding="utf-8"))
        if not isinstance(requests, list):
            raise ValueError("Priority file must contain a JSON array")
        requested = []
        for item in requests:
            if isinstance(item, dict) and isinstance(item.get("arxiv_id"), str):
                requested.append((str(item.get("requested_at_utc", "")), item["arxiv_id"]))
        by_id = {paper[0]: paper for paper in ordered}
        priority_ids = [arxiv_id for _, arxiv_id in sorted(requested) if arxiv_id in by_id]
        seen = set(priority_ids)
        ordered = [by_id[arxiv_id] for arxiv_id in priority_ids] + [paper for paper in ordered if paper[0] not in seen]
    return ordered, categories


def rotate_by_day(papers, categories):
    day_groups = {}
    for paper in papers:
        day_groups.setdefault(paper[2][:10], {}).setdefault(paper[3], []).append(paper)
    for day in sorted(day_groups, reverse=True):
        queues = day_groups[day]
        while any(queues.values()):
            for category in categories:
                if queues.get(category):
                    yield queues[category].pop(0)


def infer(model: Llama, variant: str, abstract: str):
    message = prompt(variant, abstract)
    if MODEL_KEY == "qwen3_8b":
        message += "\n/no_think"
    started = time.monotonic()
    output = model.create_chat_completion(
        messages=[{"role": "user", "content": message}],
        response_format={"type": "json_object"},
        temperature=0.2, top_p=0.8, seed=17,
        max_tokens=1200 if variant == "B" else 850,
    )
    raw = output["choices"][0]["message"]["content"]
    usage = output.get("usage") or {}
    return raw or "", int((time.monotonic() - started) * 1000), usage.get("prompt_tokens"), usage.get("completion_tokens")


def checkpoint(db: sqlite3.Connection):
    db.close()
    relative = DB_PATH.relative_to(ROOT).as_posix()
    subprocess.run(["git", "add", "--", relative], cwd=ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return open_db(DB_PATH)
    subprocess.run(["git", "-c", "user.name=github-actions[bot]", "-c",
                    "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                    "commit", "-m", f"Guardar evaluaciones de rúbrica {MODEL_KEY}"], cwd=ROOT, check=True)
    for attempt in range(3):
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=ROOT, check=True)
        if subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT).returncode == 0:
            return open_db(DB_PATH)
        time.sleep(3 * (attempt + 1))
    raise RuntimeError("Could not publish SQLite checkpoint")


def main():
    deadline = int(os.environ.get("IMPORTANCE_DEADLINE_EPOCH", str(int(time.time()) + 345 * 60)))
    db = open_db(DB_PATH)
    try:
        papers, categories = collect_candidates(db)
        print(f"{MODEL_LABEL}: {len(papers)} papers pending; loading model", flush=True)
        model_file = hf_hub_download(repo_id=MODEL_REPO, filename=MODEL_FILE)
        model = Llama(model_path=model_file, n_ctx=8192, n_threads=min(4, os.cpu_count() or 1),
                      n_batch=256, verbose=False)
        next_checkpoint = time.monotonic() + 30 * 60
        completed = 0
        priority_name = "priority_mod0.json" if MODEL_KEY == "qwen3_8b" else "priority_mod1.json"
        priority_path = ROOT / "abstract_evaluation" / priority_name
        requests = json.loads(priority_path.read_text(encoding="utf-8")) if priority_path.exists() else []
        priority_ids = [item["arxiv_id"] for item in requests if isinstance(item, dict) and isinstance(item.get("arxiv_id"), str)]
        by_id = {paper[0]: paper for paper in papers}
        unique_priority_ids = list(dict.fromkeys(priority_ids))
        priority_papers = [by_id[arxiv_id] for arxiv_id in unique_priority_ids if arxiv_id in by_id]
        priority_set = set(unique_priority_ids)
        regular_papers = [paper for paper in papers if paper[0] not in priority_set]
        for arxiv_id, abstract, day, category, missing in chain(priority_papers, rotate_by_day(regular_papers, categories)):
            for variant in missing:
                if time.time() >= deadline:
                    break
                try:
                    raw, latency, input_tokens, output_tokens = infer(model, variant, abstract)
                except Exception as exc:
                    raw, latency, input_tokens, output_tokens = json.dumps({"inference_error": str(exc)}), None, None, None
                attempt_id = save_response(db, arxiv_id=arxiv_id, model=MODEL_LABEL,
                                           abstract=abstract, variant=variant, raw=raw,
                                           latency_ms=latency, input_tokens=input_tokens,
                                           output_tokens=output_tokens)
                row = db.execute("SELECT nota_milesimas, validation_error FROM evaluations WHERE id=?", (attempt_id,)).fetchone()
                print(f"{arxiv_id} {category} {variant}: {row[0] / 1000 if row[0] is not None else 'invalid'} {row[1] or ''}", flush=True)
                completed += row[0] is not None
                if time.monotonic() >= next_checkpoint:
                    db = checkpoint(db)
                    next_checkpoint = time.monotonic() + 30 * 60
            if time.time() >= deadline:
                break
        print(f"Valid evaluations: {completed}", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()

"""Ephemeral, single-PDF lexical retrieval for the OpenScholar review pilot."""
import math
import re
from collections import Counter


QUESTIONS = {
    "C1": "importance central scientific question field research area problem phenomenon motivation introduction",
    "C2": "results established advance measured derived proof finding uncertainty conclusion decisive figure table equation",
    "C3": "previous work prior literature comparison new difference existing earlier method result discussion",
    "C4": "scope conditions assumptions applicability sample model generalization limitations selection multiple systems",
    "C5": "methods evidence reasoning derivation proof controls validation uncertainty error effect size conclusion",
    "C6": "robustness checks sensitivity reproducibility transparency independent validation limitations data code procedure",
}


def terms(text):
    return re.findall(r"[a-zA-Z]{3,}", text.lower())


def build_chunks(pages):
    chunks = []
    for page_number, page in enumerate(pages, 1):
        body = page.split("\n", 1)[-1]
        paragraphs = re.split(r"\n\s*\n", body)
        current = ""
        for paragraph in paragraphs:
            if len(current) + len(paragraph) > 1600 and current:
                chunks.append((page_number, current))
                current = ""
            while len(paragraph) > 1600:
                chunks.append((page_number, paragraph[:1600]))
                paragraph = paragraph[1600:]
            current += "\n" + paragraph
        if current.strip():
            chunks.append((page_number, current))
    return chunks


def retrieve(chunks, query, count=5):
    query_terms = set(terms(query))
    document_freq = Counter(term for _, body in chunks for term in set(terms(body)))
    scored = []
    for position, (page, body) in enumerate(chunks):
        frequencies = Counter(terms(body))
        score = sum((1 + math.log(frequencies[word])) * math.log(1 + (len(chunks) + 1) / (document_freq[word] + 1))
                    for word in query_terms if frequencies[word])
        scored.append((score, -position, page, body))
    return [(page, body) for score, _, page, body in sorted(scored, reverse=True)[:count] if score > 0]


def review_with_pdf_retrieval(model, instructions, pages):
    chunks = build_chunks(pages)
    if not chunks:
        raise ValueError("No searchable PDF text")
    notes = []
    for criterion, query in QUESTIONS.items():
        selected = retrieve(chunks, query)
        passages = "\n\n".join(f"[PDF page {page}] {body}" for page, body in selected)
        task = (f"Inspect these retrieved passages from ONE PDF for {criterion}. "
                "Record concrete evidence, contrary evidence, assumptions and exact page numbers. "
                "These passages may omit decisive material; explicitly note uncertainty. "
                "Do not score or use external knowledge.\n" + passages)
        result = model.create_chat_completion(messages=[{"role": "user", "content": task}],
                                              temperature=0.1, max_tokens=280)
        notes.append(f"{criterion}: " + (result["choices"][0]["message"]["content"] or ""))
    prompt = (instructions + "\nThe following notes were retrieved only from the attached PDF. "
              "They may be incomplete: use U where decisive evidence was not retrieved. "
              "Refer only to PDF pages explicitly shown in these notes.\n" + "\n".join(notes))
    result = model.create_chat_completion(messages=[{"role": "user", "content": prompt}],
                                          temperature=0.1, max_tokens=1300)
    return result["choices"][0]["message"]["content"] or ""

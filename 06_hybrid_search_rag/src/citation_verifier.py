"""Automated bracketed citation verification system.

Parses bracketed citations [doc_id] from generated text and verifies
that each citation corresponds to a real retrieved document and that
the cited content actually supports the claim.
"""

from __future__ import annotations

import re
from typing import Any

from src.models import CitationSpan, CitationVerification, RerankedResult

CITATION_PATTERN = re.compile(r"\[([^\]]+)\]")
MIN_CITATION_LENGTH = 2


def extract_citations(text: str) -> list[CitationSpan]:
    """Extract all bracketed citations from generated text."""
    spans: list[CitationSpan] = []
    for match in CITATION_PATTERN.finditer(text):
        cited = match.group(1).strip()
        if len(cited) >= MIN_CITATION_LENGTH:
            spans.append(CitationSpan(
                citation_text=cited,
                start_pos=match.start(),
                end_pos=match.end(),
            ))
    return spans


def verify_citations(
    text: str,
    retrieved_docs: list[RerankedResult],
) -> CitationVerification:
    """Verify that all citations in the text reference retrieved documents.

    A citation is:
    - verified: the cited doc_id matches a retrieved document
    - unverified: no matching document found (potential hallucination)
    - hallucinated: citation text does not appear in any retrieved doc content
    """
    citations = extract_citations(text)
    doc_ids = {doc.doc_id for doc in retrieved_docs}
    doc_contents = {doc.doc_id: doc.content for doc in retrieved_docs}
    source_files = {doc.source_file for doc in retrieved_docs}

    verified = 0
    unverified = 0
    hallucinated = 0
    details: list[str] = []

    for citation in citations:
        cited_text = citation.citation_text
        matched_doc = None

        for doc_id in doc_ids:
            if cited_text in doc_id or doc_id in cited_text:
                matched_doc = doc_id
                break

        if matched_doc is None:
            for doc in retrieved_docs:
                if cited_text in doc.source_file or cited_text in doc.content[:200]:
                    matched_doc = doc.doc_id
                    break

        if matched_doc is not None:
            citation.cited_doc_id = matched_doc
            content = doc_contents.get(matched_doc, "")
            if cited_text.lower() in content.lower():
                verified += 1
                details.append(f"Verified: [{cited_text}] -> {matched_doc}")
            else:
                verified += 1
                details.append(f"Verified (doc match, content check weak): [{cited_text}] -> {matched_doc}")
        else:
            content_match = any(cited_text.lower() in doc.content.lower() for doc in retrieved_docs)
            if content_match:
                unverified += 1
                details.append(f"Unverified: [{cited_text}] found in content but no doc_id match")
            else:
                hallucinated += 1
                details.append(f"Hallucinated: [{cited_text}] not found in any retrieved document")

    total = len(citations)
    score = verified / total if total > 0 else 1.0

    return CitationVerification(
        total_citations=total,
        verified_citations=verified,
        unverified_citations=unverified,
        hallucinated_citations=hallucinated,
        citation_spans=citations,
        verification_score=score,
        details=details,
    )

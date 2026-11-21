"""
Source deduplication engine for the research agent subgraph (S3-A2).

Enforces:
  1. DOI as the primary identifier when available (e.g., 10.1000/182).
  2. Canonical URL as secondary identifier when DOI is absent.
  3. URL normalization:
     - Scheme & host to lowercase.
     - Stripping 'www.' prefix.
     - Removing standard ports (:80, :443).
     - Stripping tracking/analytic query parameters (utm_*, ref, etc.).
     - Stripping URL fragments (#...).
     - Normalizing redundant slashes and trailing slashes.
  4. Rich metadata preservation: When duplicates are detected, merges snippets,
     titles, author lists, and dates into the most complete record.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from research.models import ResearchSource

logger = logging.getLogger(__name__)

# Known tracking parameters to strip during URL canonicalization
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "gclsrc",
    "dclid",
    "zanpid",
    "msclkid",
    "_ga",
    "_gl",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "ref_url",
    "yclid",
}

# Regex to match and extract clean DOI from strings or URLs
_DOI_REGEX = re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi:)?(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.IGNORECASE)


def canonicalize_doi(doi_or_url: str | None) -> str | None:
    """Extract and normalize a Digital Object Identifier (DOI).

    Returns a clean, lowercase DOI string (e.g. '10.1038/s41586-020-2649-2')
    or None if no valid DOI is detected.
    """
    if not doi_or_url or not doi_or_url.strip():
        return None

    clean = doi_or_url.strip()
    match = _DOI_REGEX.search(clean)
    if match:
        # Strip trailing punctuation that might cling to text citations (., ;)
        candidate = match.group(1).strip().rstrip(".,;")
        return candidate.lower()

    return None


def canonicalize_url(url: str) -> str:
    """Normalize a web URL to its canonical form for deduplication.

    - Scheme and hostname converted to lowercase.
    - 'www.' prefix stripped.
    - Default HTTP/HTTPS ports (:80, :443) stripped.
    - Redundant path slashes collapsed; trailing slash stripped (unless root path).
    - Tracking/analytics query parameters stripped; remaining params sorted.
    - Fragments (#...) stripped.
    """
    if not url or not url.strip():
        return ""

    raw = url.strip()
    try:
        parts = urlsplit(raw)
    except Exception:
        return raw.lower().rstrip("/")

    scheme = "https" if parts.scheme.lower() in ("http", "https") else (parts.scheme.lower() or "https")
    netloc = parts.netloc.lower()

    # Strip default ports
    if ":" in netloc:
        host, port = netloc.split(":", 1)
        if (scheme == "http" and port == "80") or (scheme == "https" and port == "443"):
            netloc = host

    # Strip 'www.' prefix for domain equivalence
    if netloc.startswith("www."):
        netloc = netloc[4:]

    # Normalize path: collapse multiple slashes, strip trailing slash unless root '/'
    path = re.sub(r"/{2,}", "/", parts.path)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    elif not path:
        path = "/"

    # Strip tracking query parameters and sort remaining parameters
    filtered_query = []
    if parts.query:
        for k, v in parse_qsl(parts.query, keep_blank_values=False):
            if k.lower() not in TRACKING_PARAMS:
                filtered_query.append((k, v))
        filtered_query.sort()
    query_str = urlencode(filtered_query)

    # Omit fragment completely
    return urlunsplit((scheme, netloc, path, query_str, ""))


def extract_identifiers(source: ResearchSource) -> tuple[str | None, str]:
    """Compute and populate canonical DOI and canonical URL on a ResearchSource."""
    # Check explicit DOI field first, then check source_url for embedded DOI
    doi = canonicalize_doi(source.doi) or canonicalize_doi(source.source_url)
    source.canonical_doi = doi

    # Compute canonical URL
    canon_url = canonicalize_url(source.source_url)
    source.canonical_url = canon_url

    return doi, canon_url


def merge_two_sources(primary: ResearchSource, incoming: ResearchSource) -> ResearchSource:
    """Merge metadata from two duplicate sources, keeping the most complete information."""
    # Keep whichever title is more descriptive/longer
    if (not primary.source_title) or (incoming.source_title and len(incoming.source_title) > len(primary.source_title)):
        primary.source_title = incoming.source_title

    # Keep whichever content/snippet is longer
    if len(incoming.content) > len(primary.content):
        primary.content = incoming.content

    # Combine unique authors
    if incoming.authors:
        existing = set(primary.authors)
        for a in incoming.authors:
            if a not in existing:
                primary.authors.append(a)
                existing.add(a)

    # Retain publication date if missing
    if not primary.published_date and incoming.published_date:
        primary.published_date = incoming.published_date

    # Retain DOI if missing
    if not primary.doi and incoming.doi:
        primary.doi = incoming.doi
        primary.canonical_doi = incoming.canonical_doi

    # Merge metadata dicts
    if incoming.metadata:
        merged_meta = dict(incoming.metadata)
        merged_meta.update(primary.metadata)
        primary.metadata = merged_meta

    return primary


def deduplicate_sources(sources: Sequence[ResearchSource]) -> list[ResearchSource]:
    """Deduplicate a sequence of research sources using canonical DOI and canonical URL.

    Algorithm:
      1. For each source, extract canonical DOI and canonical URL.
      2. If DOI exists, group by canonical DOI (cross-source academic match).
      3. If no DOI, group by canonical URL.
      4. Merge metadata across duplicate items to preserve the most complete entry.
    """
    by_doi: dict[str, ResearchSource] = {}
    by_url: dict[str, ResearchSource] = {}
    deduped: list[ResearchSource] = []

    for src in sources:
        doi, canon_url = extract_identifiers(src)

        if doi:
            if doi in by_doi:
                existing = by_doi[doi]
                merge_two_sources(existing, src)
                logger.debug("Deduplicated source via DOI: %s (tool=%s)", doi, src.tool)
                continue
            by_doi[doi] = src
            # Also register its URL so URL matches map to the same source
            if canon_url:
                by_url[canon_url] = src
            deduped.append(src)
        else:
            if canon_url in by_url:
                existing = by_url[canon_url]
                merge_two_sources(existing, src)
                logger.debug("Deduplicated source via URL: %s (tool=%s)", canon_url, src.tool)
                continue
            if canon_url:
                by_url[canon_url] = src
            deduped.append(src)

    return deduped

from __future__ import annotations

import re
from typing import Any

DEFAULT_CHUNK_SIZE_CHARS = 1500
DEFAULT_CHUNK_OVERLAP_CHARS = 200


class TextChunk(str):
    """A string chunk that preserves structural metadata (breadcrumbs, heading_level, page_number).

    Inheriting from str ensures full backward compatibility with any code expecting
    a plain str (e.g., len(), ==, JSON serialization, embeddings, Pinecone upsert).
    """

    breadcrumb: str | None
    section_path: str | None
    breadcrumb_path: str | None
    breadcrumbs: list[str]
    heading_level: int | None
    page_number: int | None
    char_length: int
    metadata: dict[str, Any]

    def __new__(
        cls,
        content: str,
        breadcrumb: str | None = None,
        section_path: str | None = None,
        breadcrumb_path: str | None = None,
        breadcrumbs: list[str] | None = None,
        heading_level: int | None = None,
        page_number: int | None = None,
        char_length: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TextChunk:
        obj = super().__new__(cls, content)
        path = breadcrumb if breadcrumb is not None else (section_path if section_path is not None else breadcrumb_path)

        if path is None and breadcrumbs:
            path = " > ".join(breadcrumbs)

        obj.breadcrumb = path
        obj.section_path = path
        obj.breadcrumb_path = path
        obj.heading_level = heading_level
        obj.page_number = page_number
        obj.char_length = len(content) if char_length is None else char_length

        if breadcrumbs is not None:
            obj.breadcrumbs = list(breadcrumbs)
        elif path is not None:
            obj.breadcrumbs = [p.strip() for p in path.split(">") if p.strip()]
        else:
            obj.breadcrumbs = []

        obj.metadata = dict(metadata) if metadata is not None else {}
        if heading_level is not None:
            obj.metadata["heading_level"] = heading_level
        if page_number is not None:
            obj.metadata["page_number"] = page_number
        obj.metadata["char_length"] = obj.char_length
        return obj


# Semantic alias
DocumentChunk = TextChunk


class StructureAwareChunker:
    """Structure-aware chunker that parses Markdown headings (# to ###),
    numbered section headers (1., 1.1), and bullet lists while maintaining
    an active breadcrumb stack.

    Implements recursive fallback splitting:
    When a section exceeds chunk_size=1500, it splits recursively at:
      1. Paragraphs (\\n\\n)
      2. Lines (\\n)
      3. Sentences ([.!?])
      4. Words (\\s)
      5. Emergency hard split for continuous unbreakable strings.
    Maintains overlap across split chunks without cutting mid-word.
    Falls back to a clean [Root] breadcrumb path for plain/unstructured text.
    """

    HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$")
    # Matches numbered section headers such as "1. Introduction", "1.1 Background", "1.1.1 Details"
    NUMBERED_HEADER_PATTERN = re.compile(r"^(\d+(?:\.\d+)+|\d+\.)\s+([A-Za-z0-9].*)$")
    # Matches bullet lists with markers "-", "*", "+", or "1)"
    BULLET_PATTERN = re.compile(r"^(\s*)([-*+]|\d+\))\s+(.+)$")
    # Matches page boundary marker: "--- Page <n> ---" or "<!-- page: <n> -->"
    PAGE_PATTERN = re.compile(r"^(?:---\s*Page\s+(\d+)\s*---|<!--\s*page\s*:\s*(\d+)\s*-->)$", re.IGNORECASE)

    def __init__(
        self,
        root_title: str | None = None,
        fallback_root: str | None = "Root",
        chunk_size: int = DEFAULT_CHUNK_SIZE_CHARS,
        overlap: int = DEFAULT_CHUNK_OVERLAP_CHARS,
        separator: str = " > ",
        max_heading_level: int = 6,
        track_bullets: bool = False,
        preserve_numbered_prefix: bool = False,
    ) -> None:
        self.root_title = root_title
        self.fallback_root = fallback_root
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.separator = separator
        self.max_heading_level = max_heading_level
        self.track_bullets = track_bullets
        self.preserve_numbered_prefix = preserve_numbered_prefix

        # Stack holds tuples of (level: int, title: str)
        self.breadcrumb_stack: list[tuple[int, str]] = []
        self._current_md_level: int = 0
        self._reset_stack()

    def _reset_stack(self) -> None:
        self.breadcrumb_stack = []
        self._current_md_level = 0
        if self.root_title:
            self.breadcrumb_stack.append((0, self.root_title.strip()))

    def get_breadcrumb_stack(self) -> list[str]:
        """Return the current active breadcrumb titles as a list."""
        return [title for _, title in self.breadcrumb_stack]

    def get_breadcrumb_path(self) -> str | None:
        """Return the current active breadcrumb path string (e.g. 'Root > Chapter 1 > Section A')."""
        if not self.breadcrumb_stack:
            return None
        return self.separator.join(self.get_breadcrumb_stack())

    def _push_breadcrumb(self, level: int, title: str) -> None:
        """Update breadcrumb stack by popping siblings/deeper levels and appending new level."""
        min_pop_level = 1 if self.root_title else 0
        while (
            self.breadcrumb_stack
            and self.breadcrumb_stack[-1][0] >= level
            and self.breadcrumb_stack[-1][0] >= min_pop_level
        ):
            self.breadcrumb_stack.pop()
        self.breadcrumb_stack.append((level, title.strip()))

    def chunk(
        self,
        text: str,
        root_title: str | None = None,
        fallback_root: str | None = None,
    ) -> list[TextChunk]:
        """Parse structured text and emit chunks with breadcrumbs, heading_level, and page numbers."""
        if not text or not text.strip():
            return []

        if root_title is not None:
            self.root_title = root_title
        eff_fallback_root = fallback_root if fallback_root is not None else self.fallback_root

        self._reset_stack()

        lines = text.split("\n")
        sections: list[tuple[str | None, list[str], int | None, int | None, str]] = []
        current_lines: list[str] = []
        current_heading_level: int | None = None
        current_page: int | None = None
        has_structure = False

        in_code_block = False

        emitted_paths: set[str] = set()

        def flush_current_section() -> None:
            nonlocal current_lines
            if current_lines:
                content = "\n".join(current_lines).strip()
                if content:
                    b_path = self.get_breadcrumb_path() or eff_fallback_root
                    b_stack = self.get_breadcrumb_stack() or ([eff_fallback_root] if eff_fallback_root else [])
                    sections.append(
                        (
                            b_path,
                            b_stack,
                            current_heading_level,
                            current_page,
                            content,
                        )
                    )
                    if b_path:
                        emitted_paths.add(b_path)
                current_lines = []

        def push_heading(level: int, title: str) -> None:
            nonlocal current_heading_level
            flush_current_section()
            current_heading_level = level
            min_pop_level = 1 if self.root_title else 0
            while (
                self.breadcrumb_stack
                and self.breadcrumb_stack[-1][0] >= level
                and self.breadcrumb_stack[-1][0] >= min_pop_level
            ):
                top_lvl, top_title = self.breadcrumb_stack[-1]
                top_path = self.get_breadcrumb_path() or eff_fallback_root
                if top_lvl >= 1 and top_path and top_path not in emitted_paths:
                    top_stack = self.get_breadcrumb_stack() or ([eff_fallback_root] if eff_fallback_root else [])
                    sections.append((top_path, top_stack, top_lvl, current_page, top_title))
                    emitted_paths.add(top_path)
                self.breadcrumb_stack.pop()

            self.breadcrumb_stack.append((level, title.strip()))

        for line in lines:
            stripped = line.strip()

            # Track code fences so # comments inside code blocks are not treated as headings
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                current_lines.append(line)
                continue

            if in_code_block:
                current_lines.append(line)
                continue

            # Check for page boundary marker (strip from chunk content to prevent marker leaks)
            page_match = self.PAGE_PATTERN.match(stripped)
            if page_match:
                p1, p2 = page_match.groups()
                current_page = int(p1 or p2)
                continue

            # 1. Check for Markdown Headings (# to ######)
            heading_match = self.HEADING_PATTERN.match(stripped)
            if heading_match:
                has_structure = True
                hashes, title = heading_match.groups()
                level = min(len(hashes), self.max_heading_level)
                self._current_md_level = level
                push_heading(level, title)
                continue

            # 2. Check for Numbered Section Headers (e.g. 1. Introduction, 1.1 Background)
            numbered_match = self.NUMBERED_HEADER_PATTERN.match(stripped)
            if numbered_match:
                num_str, title = numbered_match.groups()
                # Exclude false-positive decimal quantities (e.g. "2.5 percent", "3.14 is pi")
                is_decimal_number = bool(
                    re.match(r"^\d+\.\d+$", num_str)
                    and (
                        title[0].islower()
                        or re.match(
                            r"^(?:percent|%|million|billion|trillion|meters|kg|seconds|hours|times|fold|degrees)\b",
                            title,
                            re.IGNORECASE,
                        )
                    )
                )
                if not is_decimal_number and (title[0].isupper() or len(num_str.split(".")) > 2):
                    has_structure = True
                    num_clean = num_str.rstrip(".")
                    depth = len(num_clean.split("."))
                    level = min(self._current_md_level + depth, self.max_heading_level)

                    display_title = f"{num_str} {title}" if self.preserve_numbered_prefix else title
                    push_heading(level, display_title)
                    continue

            # 3. Check for Bullet Lists
            bullet_match = self.BULLET_PATTERN.match(line)
            if bullet_match and self.track_bullets:
                has_structure = True
                indent, marker, item_text = bullet_match.groups()
                indent_level = len(indent) // 2 + 1
                level = self._current_md_level + 10 + indent_level
                current_heading_level = level

                push_heading(level, item_text)
                current_lines.append(line)
                continue

            # Normal line (body paragraph, list item, code, etc.)
            current_lines.append(line)

        # Flush any remaining lines
        flush_current_section()

        # Handle standalone trailing items or document with headings but no trailing body text
        if not sections and self.breadcrumb_stack:
            min_pop = 1 if self.root_title else 0
            if self.breadcrumb_stack[-1][0] >= min_pop:
                top_lvl, top_title = self.breadcrumb_stack[-1]
                top_path = self.get_breadcrumb_path() or eff_fallback_root
                top_stack = self.get_breadcrumb_stack() or ([eff_fallback_root] if eff_fallback_root else [])
                sections.append((top_path, top_stack, top_lvl, current_page, top_title))
                if top_path:
                    emitted_paths.add(top_path)
        elif self.breadcrumb_stack:
            min_pop = 1 if self.root_title else 0
            top_lvl, top_title = self.breadcrumb_stack[-1]
            top_path = self.get_breadcrumb_path() or eff_fallback_root
            if top_lvl >= 1 and top_path and top_path not in emitted_paths:
                top_stack = self.get_breadcrumb_stack() or ([eff_fallback_root] if eff_fallback_root else [])
                sections.append((top_path, top_stack, top_lvl, current_page, top_title))
                if top_path:
                    emitted_paths.add(top_path)

        # Plain/unstructured text fallback:
        # If no document structure was detected and no explicit root_title was set,
        # fall back to clean [Root] breadcrumb path if eff_fallback_root is specified.
        if not has_structure and not self.root_title and eff_fallback_root:
            updated_sections: list[tuple[str | None, list[str], int | None, int | None, str]] = []
            for _path_str, _stack_list, lvl, p_num, content in sections:
                updated_sections.append((eff_fallback_root, [eff_fallback_root], lvl, p_num, content))
            sections = updated_sections

        # Split sections into boundary-aligned chunks via recursive fallback
        all_chunks: list[TextChunk] = []
        for path_str, stack_list, lvl, page_num, sec_text in sections:
            sec_chunks = self._split_into_boundary_chunks(
                sec_text,
                breadcrumb_path=path_str,
                breadcrumbs=stack_list,
                heading_level=lvl,
                page_number=page_num,
                chunk_size=self.chunk_size,
                overlap=self.overlap,
            )
            all_chunks.extend(sec_chunks)

        return all_chunks

    def _split_into_boundary_chunks(
        self,
        text: str,
        breadcrumb_path: str | None,
        breadcrumbs: list[str],
        heading_level: int | None,
        chunk_size: int,
        overlap: int,
        page_number: int | None = None,
    ) -> list[TextChunk]:
        """Split text while preserving boundaries via recursive fallback:
        1. Paragraphs (\\n\\n)
        2. Lines (\\n)
        3. Sentences ([.!?])
        4. Words (\\s)
        5. Emergency hard split for unbreakable strings.
        Maintains overlap across split chunks without cutting mid-word.
        """
        text = text.strip()
        if not text:
            return []

        if len(text) <= chunk_size:
            return [
                TextChunk(
                    text,
                    breadcrumb=breadcrumb_path,
                    breadcrumbs=breadcrumbs,
                    heading_level=heading_level,
                    page_number=page_number,
                )
            ]

        chunks: list[TextChunk] = []
        start = 0
        total_len = len(text)
        last_end = 0

        while start < total_len:
            if total_len - start <= chunk_size:
                final_slice = text[start:].strip()
                if final_slice and total_len > last_end:
                    chunks.append(
                        TextChunk(
                            final_slice,
                            breadcrumb=breadcrumb_path,
                            breadcrumbs=breadcrumbs,
                            heading_level=heading_level,
                            page_number=page_number,
                        )
                    )
                break

            target_end = min(start + chunk_size, total_len)
            window = text[start:target_end]

            split_pos = -1

            # 1. Paragraph break (\\n\\n)
            para_idx = window.rfind("\n\n")
            if para_idx >= chunk_size // 3:
                split_pos = para_idx + 2
            else:
                # 2. Line break (\\n)
                line_idx = window.rfind("\n")
                if line_idx >= chunk_size // 3:
                    split_pos = line_idx + 1
                else:
                    # 3. Sentence boundary ([.!?])
                    sentence_matches = list(re.finditer(r"[.!?](?:\s+|\n|$)", window))
                    if sentence_matches and sentence_matches[-1].end() >= chunk_size // 3:
                        split_pos = sentence_matches[-1].end()

            # 4. Word boundary (whitespace)
            if split_pos == -1:
                space_idx = window.rfind(" ")
                tab_idx = window.rfind("\t")
                newline_idx = window.rfind("\n")
                best_space = max(space_idx, tab_idx, newline_idx)
                if best_space > 0:
                    split_pos = best_space + 1

            # 5. Emergency split for unbreakable strings (no whitespace or boundaries in window)
            if split_pos == -1:
                split_pos = chunk_size

            chunk_end = start + split_pos
            chunk_content = text[start:chunk_end].strip()
            if chunk_content and chunk_end > last_end:
                chunks.append(
                    TextChunk(
                        chunk_content,
                        breadcrumb=breadcrumb_path,
                        breadcrumbs=breadcrumbs,
                        heading_level=heading_level,
                        page_number=page_number,
                    )
                )
                last_end = chunk_end

            if chunk_end >= total_len:
                break

            # Calculate next start with overlap aligned to a word boundary (never split mid-word)
            if overlap > 0 and split_pos > overlap:
                raw_next_start = chunk_end - overlap
                next_space = text.find(" ", raw_next_start, chunk_end)
                if next_space != -1 and next_space > start:
                    start = next_space + 1
                else:
                    start = max(start + 1, raw_next_start)
            else:
                start = chunk_end

        return chunks


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP_CHARS,
    root_title: str | None = None,
    fallback_root: str | None = None,
    **kwargs: Any,
) -> list[TextChunk]:
    """Structure-aware chunker functional entrypoint."""
    chunker = StructureAwareChunker(
        root_title=root_title,
        fallback_root=fallback_root,
        chunk_size=chunk_size,
        overlap=overlap,
        **kwargs,
    )
    return chunker.chunk(text)

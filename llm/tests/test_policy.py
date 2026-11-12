from __future__ import annotations

from orchestration.policy import (
    classify_seed_mode,
    plan_research,
)


def test_classify_seed_mode_with_files_is_always_document():
    assert classify_seed_mode("Short topic", has_files=True) == "document"
    assert classify_seed_mode("", has_files=True) == "document"


def test_classify_seed_mode_short_without_files_is_topic():
    short_seed = "Quantum computing algorithms"
    assert classify_seed_mode(short_seed, has_files=False) == "topic"


def test_classify_seed_mode_long_without_files_is_document():
    long_seed = "word " * 251
    assert classify_seed_mode(long_seed, has_files=False) == "document"


def test_classify_seed_mode_custom_threshold():
    text = "one two three four five"
    assert classify_seed_mode(text, has_files=False, max_words=3) == "document"
    assert classify_seed_mode(text, has_files=False, max_words=5) == "topic"
    assert classify_seed_mode(text, has_files=False, max_words=10) == "topic"


def test_plan_research_forces_web_search_for_topic_mode():
    plan = plan_research(
        seed_text="Machine learning basics",
        has_files=False,
        available_tools={"web_search", "arxiv_search"},
    )
    assert plan.mode == "topic"
    assert plan.web_search is True


def test_plan_research_leaves_web_search_none_for_document_mode():
    plan = plan_research(
        seed_text="word " * 300,
        has_files=False,
        available_tools={"web_search", "arxiv_search"},
    )
    assert plan.mode == "document"
    assert plan.web_search is None  # decision model acts as tiebreaker


def test_plan_research_leaves_web_search_none_when_files_attached():
    plan = plan_research(
        seed_text="Short prompt",
        has_files=True,
        available_tools={"web_search", "arxiv_search"},
    )
    assert plan.mode == "document"
    assert plan.web_search is None


def test_plan_research_arxiv_forced_for_research_purposes():
    for p in ("research", "academic", "study", "Research", " ACADEMIC ", "Study"):
        plan = plan_research(
            seed_text="Deep neural architectures",
            has_files=False,
            available_tools={"arxiv_search"},
            purpose=p,
        )
        assert plan.arxiv_search is True, f"Failed for purpose: {p}"


def test_plan_research_arxiv_disabled_for_non_research_purposes():
    for p in ("brainstorm", "casual", "personal", "general", "Brainstorm"):
        plan = plan_research(
            seed_text="Deep neural architectures",
            has_files=False,
            available_tools={"arxiv_search"},
            purpose=p,
        )
        assert plan.arxiv_search is False, f"Failed for purpose: {p}"


def test_plan_research_arxiv_none_when_purpose_unspecified():
    plan = plan_research(
        seed_text="Deep neural architectures",
        has_files=False,
        available_tools={"arxiv_search"},
        purpose=None,
    )
    assert plan.arxiv_search is None


def test_plan_research_respects_available_tools():
    # Neither tool available
    plan = plan_research(
        seed_text="Short topic",
        has_files=False,
        available_tools=set(),
        purpose="research",
    )
    assert plan.web_search is None
    assert plan.arxiv_search is None


def test_plan_research_custom_thin_seed_threshold():
    # 5 words: with threshold 10 -> topic; with threshold 3 -> document
    text = "one two three four five"
    plan_topic = plan_research(
        seed_text=text,
        has_files=False,
        available_tools={"web_search"},
        thin_seed_word_threshold=10,
    )
    assert plan_topic.mode == "topic"
    assert plan_topic.web_search is True

    plan_doc = plan_research(
        seed_text=text,
        has_files=False,
        available_tools={"web_search"},
        thin_seed_word_threshold=3,
    )
    assert plan_doc.mode == "document"
    assert plan_doc.web_search is None

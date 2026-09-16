from opendb.gateway.ingestion_guide import ingestion_guide


def test_guide_reports_deployment_indexing_rules(monkeypatch):
    monkeypatch.setenv("OPENDB_VECTOR_MIN_TEXT_CHARS", "850")
    monkeypatch.setenv("OPENDB_VECTOR_NARRATIVE_COLUMNS", "Body, custom_dialogue, BODY")
    policy = ingestion_guide()["automatic_indexing_policy"]
    assert policy["minimum_text_characters"] == 850
    assert policy["narrative_columns"] == ["body", "custom_dialogue"]
    monkeypatch.setenv("OPENDB_VECTOR_NARRATIVE_COLUMNS", "")
    assert ingestion_guide()["automatic_indexing_policy"]["narrative_columns"] == []


def test_prompt_requires_document_decomposition_and_raw_backup_is_not_indexed():
    from opendb.gateway.contract import INSTRUCTIONS

    guide = ingestion_guide()
    guide_rules = " ".join(guide["agent_rules"])

    for text in (INSTRUCTIONS, guide_rules):
        normalized = text.lower()
        assert "complete document" in normalized
        assert "decompose" in normalized
        assert "opaque" in normalized
        assert "raw" in normalized
        assert "do not" in normalized
        assert "vector" in normalized
        assert "meeting" in normalized
        assert "participants" in normalized
        assert "ordered" in normalized

from opendb.gateway.ingestion_guide import ingestion_guide


def test_guide_reports_deployment_indexing_rules(monkeypatch):
    monkeypatch.setenv("OPENDB_VECTOR_MIN_TEXT_CHARS", "850")
    monkeypatch.setenv("OPENDB_VECTOR_NARRATIVE_COLUMNS", "Body, custom_dialogue, BODY")
    policy = ingestion_guide()["automatic_indexing_policy"]
    assert policy["minimum_text_characters"] == 850
    assert policy["narrative_columns"] == ["body", "custom_dialogue"]
    monkeypatch.setenv("OPENDB_VECTOR_NARRATIVE_COLUMNS", "")
    assert ingestion_guide()["automatic_indexing_policy"]["narrative_columns"] == []

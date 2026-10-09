"""Unit tests for attribution parser and normalizer."""

from core.attribution import AttributionParser


def test_normalization_casing_and_spaces():
    parser = AttributionParser()
    assert parser.normalize_team("  Engineering  ") == "engineering"
    assert parser.normalize_team("SUPPORT_OPS") == "support-ops"
    assert parser.normalize_team("") == "unattributed"
    assert parser.normalize_team(None) == "unattributed"


def test_feature_alias_mapping():
    parser = AttributionParser()
    assert parser.normalize_feature("code_review") == "code-review"
    assert parser.normalize_feature("codereview") == "code-review"
    assert parser.normalize_feature("chat") == "agent-chat"
    assert parser.normalize_feature("summarization") == "summarisation"


def test_header_parsing_and_unattributed_flag():
    parser = AttributionParser()
    
    # Fully attributed
    res = parser.parse_attribution({
        "x-team": "engineering",
        "x-feature": "code-review",
        "x-user": "user_42"
    })
    assert res["team"] == "engineering"
    assert res["feature"] == "code-review"
    assert res["user_id"] == "user_42"
    assert res["is_unattributed"] is False

    # Missing team -> tagged as unattributed
    res_missing = parser.parse_attribution({
        "team": "",
        "feature": "doc-search",
        "user": "user_10"
    })
    assert res_missing["team"] == "unattributed"
    assert res_missing["is_unattributed"] is True

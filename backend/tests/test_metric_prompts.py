"""load_metric_prompt bettet Feld-Markdown in den Extractor-Prompt ein.

Die .md-Dateien enthalten fuer den Standalone-Gebrauch eigene
"Output-Format"-JSON-Bloecke und "Query-Template"-Abschnitte. Im
Two-Stage-Extractor konkurrieren die mit dem verbindlichen
q1..q4/fy-Schema — sie muessen beim Einbetten gestrippt werden,
genau wie das YAML-Frontmatter.
"""

from scripts.two_stage_research import load_metric_prompt


def test_strips_frontmatter():
    text = load_metric_prompt("revenue")
    assert not text.startswith("---")
    assert "key: revenue" not in text


def test_strips_output_format_section():
    text = load_metric_prompt("revenue")
    assert "Output-Format" not in text
    assert '"revenue_eur"' not in text


def test_strips_query_template_section():
    text = load_metric_prompt("revenue")
    assert "Query-Template" not in text


def test_keeps_definition_and_anti_confusion():
    text = load_metric_prompt("revenue")
    assert "## Definition" in text
    assert "Anti-Confusion" in text
    assert "Cross-References" in text


def test_missing_key_returns_empty():
    assert load_metric_prompt("does_not_exist") == ""

"""
Unit tests for tag_engine.py -- the shared tagging data model, validator,
recommender, apply-plan builder, bulk-import normalizer, and coverage
analysis used by the Unity Catalog Tag Strategy Builder app.

Run with:
    pip install pytest
    pytest tag-strategy-app/tests/test_tag_engine.py -v

These tests exercise pure Python logic only -- no Databricks workspace,
SQL warehouse, or Streamlit runtime is required.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tag_engine as te


# ---------------------------------------------------------------------------
# lint_tag_definition
# ---------------------------------------------------------------------------

def test_lint_empty_key_is_error():
    issues = te.lint_tag_definition("")
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].code == "EMPTY_KEY"


def test_lint_invalid_compute_chars_only_flagged_for_compute_scope():
    issues = te.lint_tag_definition("cost center!", scopes=["compute"])
    assert any(i.code == "INVALID_COMPUTE_CHARS" for i in issues)

    # Same characters, but no compute/workspace scope -> not flagged
    issues_uc_only = te.lint_tag_definition("cost center!", scopes=["table"])
    assert not any(i.code == "INVALID_COMPUTE_CHARS" for i in issues_uc_only)


def test_lint_reserved_key():
    issues = te.lint_tag_definition("Name")
    assert any(i.code == "RESERVED_KEY" and i.severity == "warning" for i in issues)


def test_lint_high_cardinality_hint():
    issues = te.lint_tag_definition("run_id")
    assert any(i.code == "HIGH_CARDINALITY" for i in issues)


def test_lint_duplicate_semantic_key():
    issues = te.lint_tag_definition("cost_center")
    assert any(i.code == "DUPLICATE_SEMANTIC_KEY" for i in issues)


def test_lint_sensitive_value_email():
    issues = te.lint_tag_definition("owner_contact", allowed_values=["jane@example.com"])
    assert any(i.code == "SENSITIVE_VALUE" for i in issues)


def test_lint_clean_key_has_no_issues():
    issues = te.lint_tag_definition("data_classification", allowed_values=["restricted", "public"], scopes=["table"])
    assert issues == []


# ---------------------------------------------------------------------------
# lint_assignment_scope / lint_value_drift
# ---------------------------------------------------------------------------

def test_scope_mismatch_detected():
    issues = te.lint_assignment_scope("data_classification", ["catalog", "schema"], "table")
    assert len(issues) == 1
    assert issues[0].code == "SCOPE_MISMATCH"
    assert issues[0].severity == "error"


def test_scope_match_no_issue():
    issues = te.lint_assignment_scope("data_classification", ["catalog", "table"], "table")
    assert issues == []


def test_value_drift_detected():
    issues = te.lint_value_drift("data_classification", ["restricted", "public"], "top-secret")
    assert len(issues) == 1
    assert issues[0].code == "VALUE_SET_DRIFT"


def test_value_drift_allows_known_value():
    issues = te.lint_value_drift("data_classification", ["restricted", "public"], "public")
    assert issues == []


def test_value_drift_skips_when_no_allowed_values_defined():
    issues = te.lint_value_drift("free_form_tag", [], "anything")
    assert issues == []


# ---------------------------------------------------------------------------
# validate_taxonomy
# ---------------------------------------------------------------------------

def _row(key, type_="governed", values="", **scopes):
    row = {"category": "Test", "type": type_, "key": key, "values": values}
    for s in ["catalog", "schema", "table", "view", "column"]:
        row[f"scope_{s}"] = scopes.get(s, False)
    return row


def test_validate_taxonomy_flags_case_variants():
    rows = [_row("Cost_Center", table=True), _row("cost_center", table=True)]
    issues = te.validate_taxonomy(rows)
    assert any(i.code == "CASE_VARIANT_KEY" for i in issues)


def test_validate_taxonomy_ignores_blank_keys():
    rows = [_row("", table=True)]
    issues = te.validate_taxonomy(rows)
    assert issues == []


def test_validate_taxonomy_clean_matrix_has_no_issues():
    rows = [_row("data_classification", values="restricted, public", table=True, column=True)]
    issues = te.validate_taxonomy(rows)
    assert issues == []


# ---------------------------------------------------------------------------
# recommend_governance_mode / recommend_for_rows
# ---------------------------------------------------------------------------

def test_recommend_system_managed_for_class_namespace():
    mode, _ = te.recommend_governance_mode("class.pii_detected")
    assert mode == "system-managed"


def test_recommend_governed_for_required_key():
    mode, rationale = te.recommend_governance_mode("owner", required=True)
    assert mode == "governed"
    assert "required" in rationale


def test_recommend_governed_for_compliance_key():
    mode, _ = te.recommend_governance_mode("data_classification")
    assert mode == "governed"


def test_recommend_free_form_for_scratch_key():
    mode, _ = te.recommend_governance_mode("scratch_notes")
    assert mode == "free-form"


def test_recommend_for_rows_attaches_mode_per_row():
    rows = [_row("cost_center", catalog=True), _row("team_scratch", type_="free-form", table=True)]
    recs = te.recommend_for_rows(rows)
    by_key = {r["key"]: r for r in recs}
    assert by_key["cost_center"]["recommended_mode"] == "governed"
    assert by_key["team_scratch"]["recommended_mode"] == "free-form"


# ---------------------------------------------------------------------------
# classify_tag_domain
# ---------------------------------------------------------------------------

def test_classify_cost_only():
    assert te.classify_tag_domain("cost_center") == "cost"


def test_classify_governance_only():
    assert te.classify_tag_domain("data_owner") == "governance"


def test_classify_both():
    assert te.classify_tag_domain("cost_center_classification") == "both"


# ---------------------------------------------------------------------------
# build_apply_plan
# ---------------------------------------------------------------------------

def test_build_apply_plan_governed_rows_only():
    rows = [
        _row("data_classification", values="restricted, public", catalog=True, table=True),
        _row("team_note", type_="free-form", table=True),
    ]
    plan = te.build_apply_plan(rows)
    assert [t.key for t in plan.governed_tags_to_create] == ["data_classification"]
    assert all(a.key == "data_classification" for a in plan.uc_assignments)
    assert len(plan.uc_assignments) == 2  # catalog + table


def test_build_apply_plan_routes_cost_tags_to_compute():
    rows = [_row("cost_center", values="cc-100", catalog=True)]
    plan = te.build_apply_plan(rows)
    assert len(plan.compute_tag_assignments) == 1
    assert plan.compute_tag_assignments[0].key == "cost_center"


def test_build_apply_plan_surfaces_validator_warnings():
    rows = [_row("run_id", table=True)]
    plan = te.build_apply_plan(rows)
    assert any(w.code == "HIGH_CARDINALITY" for w in plan.warnings)


# ---------------------------------------------------------------------------
# normalize_import_record / build_import_plan
# ---------------------------------------------------------------------------

def test_normalize_import_record_maps_aliases():
    rec = te.normalize_import_record({"Catalog_Name": "main", "Tag": "pii", "Val": "true"})
    assert rec["catalog"] == "main"
    assert rec["key"] == "pii"
    assert rec["value"] == "true"


def test_normalize_import_record_skips_nan_and_blank():
    rec = te.normalize_import_record({"catalog": "main", "schema": "", "table": "nan", "key": "k", "value": "v"})
    assert rec["schema"] is None
    assert rec["table"] is None


def test_build_import_plan_infers_scope_from_most_specific_identifier():
    records = [{"catalog": "main", "schema": "sales", "table": "orders", "column": "email", "key": "pii", "value": "true"}]
    plan = te.build_import_plan(records, [])
    assert len(plan.uc_assignments) == 1
    assert plan.uc_assignments[0].scope == "column"


def test_build_import_plan_flags_missing_key_and_missing_object():
    records = [
        {"catalog": "main", "value": "x"},  # missing key
        {"key": "k", "value": "x"},          # missing object identifier
    ]
    plan = te.build_import_plan(records, [])
    codes = {w.code for w in plan.warnings}
    assert "IMPORT_MISSING_KEY" in codes
    assert "IMPORT_MISSING_OBJECT" in codes


def test_build_import_plan_flags_unmapped_key():
    records = [{"catalog": "main", "key": "not_in_taxonomy", "value": "x"}]
    plan = te.build_import_plan(records, [])
    assert any(w.code == "IMPORT_UNMAPPED_KEY" for w in plan.warnings)


def test_build_import_plan_flags_value_drift_against_taxonomy():
    taxonomy = [_row("data_classification", values="restricted, public", catalog=True)]
    records = [{"catalog": "main", "key": "data_classification", "value": "top-secret"}]
    plan = te.build_import_plan(records, taxonomy)
    assert any(w.code == "VALUE_SET_DRIFT" for w in plan.warnings)


def test_build_import_plan_groups_multiple_tags_per_object_into_one_statement():
    records = [
        {"catalog": "main", "schema": "sales", "table": "orders", "key": "data_classification", "value": "restricted"},
        {"catalog": "main", "schema": "sales", "table": "orders", "key": "cost_center", "value": "cc-1"},
    ]
    plan = te.build_import_plan(records, [])
    assert len(plan.sql_statements) == 1
    assert "data_classification" in plan.sql_statements[0]
    assert "cost_center" in plan.sql_statements[0]


# ---------------------------------------------------------------------------
# compute_required_keys / analyze_object_coverage / audit_value_drift
# ---------------------------------------------------------------------------

def test_compute_required_keys_filters_by_scope_and_governed_type():
    rows = [
        _row("data_classification", table=True, schema=True),
        _row("free_tag", type_="free-form", table=True),
    ]
    assert te.compute_required_keys(rows, "table") == ["data_classification"]
    assert te.compute_required_keys(rows, "schema") == ["data_classification"]
    assert te.compute_required_keys(rows, "catalog") == []


def test_analyze_object_coverage_computes_gaps_and_percentage():
    objects = ["sales", "marketing", "finance"]
    required = ["data_classification"]
    tagged = [{"object_id": "sales", "tag_name": "data_classification"}]
    result = te.analyze_object_coverage(objects, required, tagged)
    assert result["total"] == 3
    assert result["fully_covered"] == 1
    assert result["coverage_pct"] == round(100 / 3, 1)
    missing_objects = {g["object"] for g in result["gaps"]}
    assert missing_objects == {"marketing", "finance"}


def test_analyze_object_coverage_handles_empty_object_list():
    result = te.analyze_object_coverage([], ["data_classification"], [])
    assert result["total"] == 0
    assert result["coverage_pct"] == 0.0
    assert result["gaps"] == []


def test_audit_value_drift_flags_invalid_live_values():
    taxonomy = [_row("data_classification", values="restricted, public", table=True)]
    live_tags = [{"object_id": "sales.orders", "tag_name": "data_classification", "tag_value": "top-secret"}]
    issues = te.audit_value_drift(live_tags, taxonomy)
    assert len(issues) == 1
    assert issues[0].code == "VALUE_SET_DRIFT"
    assert issues[0].object_ref == "sales.orders"


def test_audit_value_drift_ignores_keys_outside_taxonomy():
    live_tags = [{"object_id": "sales.orders", "tag_name": "untracked_tag", "tag_value": "whatever"}]
    issues = te.audit_value_drift(live_tags, [])
    assert issues == []

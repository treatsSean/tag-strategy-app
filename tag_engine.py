"""
Shared tagging data model, validator/linter, and governance recommender.

Implements the shared data model (Step 1) plus Feature 2 (tag validator and
linter) and Feature 3 (governed vs free-form vs system-managed recommender)
from the Unity Catalog Tag Strategy Builder build plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

TagScope = Literal["catalog", "schema", "table", "column", "compute", "workspace"]
GovernanceMode = Literal["governed", "free-form", "system-managed"]
Severity = Literal["error", "warning", "info"]
AssignmentSource = Literal["manual", "imported", "suggested", "inherited", "classification"]


@dataclass
class TagDefinition:
    key: str
    description: str = ""
    allowed_values: list = field(default_factory=list)
    required: bool = False
    governed_recommended: bool = False
    scopes: list = field(default_factory=list)
    rationale: str = ""


@dataclass
class TagAssignment:
    scope: str
    key: str
    value: Optional[str] = None
    catalog: Optional[str] = None
    schema: Optional[str] = None
    table: Optional[str] = None
    column: Optional[str] = None
    source: str = "manual"


@dataclass
class ValidationIssue:
    severity: str
    code: str
    message: str
    object_ref: Optional[str] = None
    suggested_fix: Optional[str] = None


@dataclass
class ApplyPlan:
    governed_tags_to_create: list = field(default_factory=list)
    uc_assignments: list = field(default_factory=list)
    compute_tag_assignments: list = field(default_factory=list)
    sql_statements: list = field(default_factory=list)
    terraform_snippets: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validator / linter (Feature 2)
# ---------------------------------------------------------------------------

_INVALID_COMPUTE_CHARS = re.compile(r"[^a-zA-Z0-9_\-./:@ ]")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_RE = re.compile(r"\b(\+?\d{1,2}[\s.-]?)?(\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}\b")
_SECRET_RE = re.compile(r"(?i)(secret|token|api[_-]?key|password|bearer\s)")
_RESERVED_KEYS = {"name", "vendor", "creator", "owner_id"}
_SEMANTIC_DUPES = [
    {"dept", "department"},
    {"owner", "data_owner"},
    {"env", "environment"},
    {"cost_center", "costcenter", "cost-center"},
]
_HIGH_CARDINALITY_HINTS = ("id", "uuid", "timestamp", "run_id", "guid", "request_id")


def _norm(s):
    return (s or "").strip().lower()


def lint_tag_definition(key, allowed_values=None, scopes=None):
    """Validate a single tag key/value-set. Returns a list of ValidationIssue."""
    issues = []
    k = key or ""
    nk = _norm(k)
    scopes = scopes or []
    allowed_values = allowed_values or []

    if not nk:
        issues.append(ValidationIssue("error", "EMPTY_KEY", "Tag key is empty.", k, "Provide a tag key."))
        return issues

    if _INVALID_COMPUTE_CHARS.search(k) and ("compute" in scopes or "workspace" in scopes):
        issues.append(ValidationIssue(
            "error", "INVALID_COMPUTE_CHARS",
            f"`{k}` contains characters not valid for compute/cost tags.",
            k, "Use only letters, numbers, spaces, and _ - . / : @",
        ))

    if nk in _RESERVED_KEYS:
        issues.append(ValidationIssue(
            "warning", "RESERVED_KEY",
            f"`{k}` collides with a Databricks default or reserved tag key.",
            k, "Rename to a domain-specific key, e.g. `resource_owner`.",
        ))

    if any(hint in nk for hint in _HIGH_CARDINALITY_HINTS):
        issues.append(ValidationIssue(
            "warning", "HIGH_CARDINALITY",
            f"`{k}` looks like a high-cardinality value (ID, timestamp, or run-specific). This can bloat tag storage and break governed-tag reuse.",
            k, "Track this in a lineage/metadata column instead of a tag.",
        ))

    for group in _SEMANTIC_DUPES:
        if nk in group:
            others = sorted(group - {nk})
            issues.append(ValidationIssue(
                "warning", "DUPLICATE_SEMANTIC_KEY",
                f"`{k}` overlaps in meaning with {others}. Standardize on one key.",
                k, f"Pick a single canonical key from {sorted(group)}.",
            ))

    for v in allowed_values:
        if _EMAIL_RE.search(v) or _SSN_RE.search(v) or _PHONE_RE.search(v) or _SECRET_RE.search(v):
            issues.append(ValidationIssue(
                "warning", "SENSITIVE_VALUE",
                f"Allowed value `{v}` for `{k}` may contain sensitive content (email, SSN, phone, or secret pattern).",
                k, "Do not embed PII or secrets directly in tag values.",
            ))

    return issues


def lint_assignment_scope(key, defined_scopes, assigned_scope):
    """Flag a tag assigned at a scope it was not defined for."""
    issues = []
    if defined_scopes and assigned_scope not in defined_scopes:
        issues.append(ValidationIssue(
            "error", "SCOPE_MISMATCH",
            f"`{key}` is defined for scopes {defined_scopes} but is being assigned at `{assigned_scope}`.",
            key, f"Add `{assigned_scope}` to the tag's supported scopes, or assign at one of {defined_scopes}.",
        ))
    return issues


def lint_value_drift(key, allowed_values, assigned_value):
    """Flag an assignment whose value is outside the tag's allowed value set."""
    issues = []
    if allowed_values and assigned_value and assigned_value not in allowed_values:
        issues.append(ValidationIssue(
            "error", "VALUE_SET_DRIFT",
            f"`{assigned_value}` is not in the allowed value set for `{key}`: {allowed_values}.",
            key, f"Use one of {allowed_values}, or add `{assigned_value}` to the allowed set.",
        ))
    return issues


def validate_taxonomy(rows):
    """Validate the full taxonomy matrix.

    `rows` matches the app's tag_rows session-state shape: each row has at
    least key, values (comma-separated string), scope_catalog/scope_schema/
    scope_table/scope_view/scope_column (bools), and type.
    """
    issues = []
    seen_keys = {}

    for row in rows:
        key = (row.get("key") or "").strip()
        if not key:
            continue
        allowed_values = [v.strip() for v in (row.get("values") or "").split(",") if v.strip()]
        scopes = [s for s in ["catalog", "schema", "table", "view", "column"] if row.get(f"scope_{s}")]
        issues.extend(lint_tag_definition(key, allowed_values, scopes))
        seen_keys.setdefault(_norm(key), set()).add(key)

    for norm_key, variants in seen_keys.items():
        if len(variants) > 1:
            issues.append(ValidationIssue(
                "warning", "CASE_VARIANT_KEY",
                f"Tag key `{norm_key}` appears with inconsistent casing or spelling: {sorted(variants)}.",
                norm_key, "Standardize on a single casing for this key.",
            ))

    return issues


# ---------------------------------------------------------------------------
# Governed / free-form / system-managed recommender (Feature 3)
# ---------------------------------------------------------------------------

_COMPLIANCE_HINTS = ("classification", "sensitivity", "pii", "phi", "gdpr", "hipaa", "compliance", "restricted")
_ABAC_HINTS = ("classification", "region", "geo", "residency", "restricted", "confidential")
_COST_HINTS = ("cost_center", "cost-center", "costcenter", "budget", "billing")
_SYSTEM_HINTS = ("class.", "certification", "deprecat", "system.")
_TEAM_LOCAL_HINTS = ("temp", "scratch", "experiment", "draft", "sandbox", "team_")


def recommend_governance_mode(key, required=False, category=""):
    """Return (mode, rationale). mode is governed | free-form | system-managed."""
    nk = _norm(key)
    nc = _norm(category)

    if any(h in nk for h in _SYSTEM_HINTS):
        return "system-managed", f"`{key}` overlaps with a Databricks system tag namespace (class.*, certification, deprecation)."

    if required or any(h in nk for h in _COMPLIANCE_HINTS) or any(h in nk for h in _ABAC_HINTS) or any(h in nk for h in _COST_HINTS):
        reasons = []
        if required:
            reasons.append("marked required")
        if any(h in nk for h in _COMPLIANCE_HINTS):
            reasons.append("compliance-related")
        if any(h in nk for h in _ABAC_HINTS):
            reasons.append("relevant to ABAC policies")
        if any(h in nk for h in _COST_HINTS):
            reasons.append("used for cost allocation")
        return "governed", f"`{key}` is {', '.join(reasons)}, so keep it consistent org-wide."

    if any(h in nk or h in nc for h in _TEAM_LOCAL_HINTS):
        return "free-form", f"`{key}` looks team-local, temporary, or experimental."

    return "free-form", f"`{key}` has no compliance, ABAC, or cost-allocation signal. Default to free-form unless a team says otherwise."


def recommend_for_rows(rows):
    """Attach a governance-mode recommendation to each taxonomy row (Feature 3)."""
    out = []
    for row in rows:
        key = (row.get("key") or "").strip()
        if not key:
            continue
        required = _norm(row.get("type", "")) == "governed"
        mode, rationale = recommend_governance_mode(key, required=required, category=row.get("category", ""))
        out.append({
            "key": key,
            "recommended_mode": mode,
            "rationale": rationale,
            "current_type": row.get("type", ""),
        })
    return out


# ---------------------------------------------------------------------------
# Apply-plan builder (Feature 6 foundation) + cost/governance domain split
# ---------------------------------------------------------------------------

def classify_tag_domain(key):
    """Classify a tag key as governance-only, cost-only, or both (Feature 11 seed).

    Used by the SQL/Terraform renderer to decide whether a governed tag also
    needs a compute/cost-tagging counterpart, since UC tags and compute/cost
    tags are still separate systems today.
    """
    nk = _norm(key)
    is_cost = any(h in nk for h in _COST_HINTS)
    is_governance_signal = any(h in nk for h in _COMPLIANCE_HINTS) or any(h in nk for h in _ABAC_HINTS)
    if is_cost and is_governance_signal:
        return "both"
    if is_cost:
        return "cost"
    return "governance"


def build_apply_plan(rows):
    """Build an ApplyPlan from taxonomy rows: governed tag defs, UC assignments,
    compute/cost assignments, and validator warnings, ordered for safe execution.

    `rows` matches the app's tag_rows session-state shape (see validate_taxonomy).
    """
    plan = ApplyPlan()
    plan.warnings = validate_taxonomy(rows)

    for row in rows:
        key = (row.get("key") or "").strip()
        if not key or _norm(row.get("type", "")) != "governed":
            continue

        allowed_values = [v.strip() for v in (row.get("values") or "").split(",") if v.strip()]
        scopes = [s for s in ["catalog", "schema", "table", "view", "column"] if row.get(f"scope_{s}")]
        _, rationale = recommend_governance_mode(key, required=True, category=row.get("category", ""))

        plan.governed_tags_to_create.append(TagDefinition(
            key=key,
            description=row.get("desc", "") or row.get("category", ""),
            allowed_values=allowed_values,
            required=True,
            governed_recommended=True,
            scopes=scopes,
            rationale=rationale,
        ))

        default_value = allowed_values[0] if allowed_values else None
        domain = classify_tag_domain(key)

        for s in scopes:
            plan.uc_assignments.append(TagAssignment(scope=s, key=key, value=default_value, source="suggested"))

        if domain in ("cost", "both"):
            plan.compute_tag_assignments.append(TagAssignment(scope="compute", key=key, value=default_value, source="suggested"))

    return plan


# ---------------------------------------------------------------------------
# Bulk import + apply-plan generator (Feature 4)
# ---------------------------------------------------------------------------

def _ident_escape(s):
    """Escape a value for safe embedding inside a backtick-quoted SQL identifier."""
    return str(s).replace("`", "``")


def _sql_escape(s):
    return str(s).replace("'", "''")


_IMPORT_COL_ALIASES = {
    "catalog": ["catalog", "catalog_name"],
    "schema": ["schema", "schema_name", "database", "db"],
    "table": ["table", "table_name"],
    "column": ["column", "column_name", "col"],
    "key": ["tag_key", "key", "tag_name", "tag"],
    "value": ["tag_value", "value", "val"],
}


def normalize_import_record(record):
    """Map a raw imported dict (arbitrary column names) to catalog/schema/table/column/key/value."""
    out = {"catalog": None, "schema": None, "table": None, "column": None, "key": None, "value": None}
    lower_map = {}
    for k, v in record.items():
        if v is None:
            continue
        sv = str(v).strip()
        if sv == "" or sv.lower() == "nan":
            continue
        lower_map[str(k).strip().lower()] = sv
    for canon, aliases in _IMPORT_COL_ALIASES.items():
        for alias in aliases:
            if alias in lower_map:
                out[canon] = lower_map[alias]
                break
    return out


def _infer_scope(rec):
    if rec.get("column"):
        return "column"
    if rec.get("table"):
        return "table"
    if rec.get("schema"):
        return "schema"
    if rec.get("catalog"):
        return "catalog"
    return None


def build_import_plan(raw_records, taxonomy_rows=None):
    """Normalize raw bulk-import records, match against taxonomy, and build an ApplyPlan.

    raw_records: list of dicts with arbitrary column names (see normalize_import_record).
    taxonomy_rows: existing tag_rows (governed + free-form) to match imported keys against.
    """
    taxonomy_rows = taxonomy_rows or []
    taxonomy_by_key = {}
    for row in taxonomy_rows:
        k = (row.get("key") or "").strip()
        if k:
            taxonomy_by_key[_norm(k)] = row

    plan = ApplyPlan()
    grouped = {}

    for i, raw in enumerate(raw_records):
        rec = normalize_import_record(raw)
        key = rec.get("key")
        value = rec.get("value")
        scope = _infer_scope(rec)

        if not key:
            plan.warnings.append(ValidationIssue(
                "error", "IMPORT_MISSING_KEY", f"Row {i + 1}: no tag key/tag_name column recognized.",
                object_ref=str(raw),
            ))
            continue
        if not scope:
            plan.warnings.append(ValidationIssue(
                "error", "IMPORT_MISSING_OBJECT",
                f"Row {i + 1}: no catalog/schema/table/column identifier found for key `{key}`.",
                object_ref=key,
            ))
            continue

        plan.warnings.extend(lint_tag_definition(key, [value] if value else []))

        taxonomy_row = taxonomy_by_key.get(_norm(key))
        if taxonomy_row is None:
            plan.warnings.append(ValidationIssue(
                "warning", "IMPORT_UNMAPPED_KEY",
                f"`{key}` is not part of your current taxonomy (Tag Matrix).",
                object_ref=key,
                suggested_fix="Add it as a row in Tag Matrix, or confirm it should stay ad hoc.",
            ))
        else:
            allowed_values = [v.strip() for v in (taxonomy_row.get("values") or "").split(",") if v.strip()]
            plan.warnings.extend(lint_value_drift(key, allowed_values, value or ""))
            defined_scopes = [s for s in ["catalog", "schema", "table", "view", "column"] if taxonomy_row.get(f"scope_{s}")]
            plan.warnings.extend(lint_assignment_scope(key, defined_scopes, scope))

        plan.uc_assignments.append(TagAssignment(
            scope=scope, key=key, value=value,
            catalog=rec.get("catalog"), schema=rec.get("schema"), table=rec.get("table"), column=rec.get("column"),
            source="imported",
        ))

        obj_key = (scope, rec.get("catalog"), rec.get("schema"), rec.get("table"), rec.get("column"))
        grouped.setdefault(obj_key, {})[key] = value

    for (scope, cat, sch, tbl, col), kv in sorted(grouped.items(), key=lambda x: tuple((x[0][i] or "") for i in range(5))):
        tags_clause = ",\n".join(f"  '{_sql_escape(k)}' = '{_sql_escape(v or '')}'" for k, v in kv.items())
        if scope == "catalog":
            plan.sql_statements.append(f"ALTER CATALOG `{_ident_escape(cat)}`\nSET TAGS (\n{tags_clause}\n);")
        elif scope == "schema":
            plan.sql_statements.append(f"ALTER SCHEMA `{_ident_escape(cat)}`.`{_ident_escape(sch)}`\nSET TAGS (\n{tags_clause}\n);")
        elif scope == "table":
            plan.sql_statements.append(f"ALTER TABLE `{_ident_escape(cat)}`.`{_ident_escape(sch)}`.`{_ident_escape(tbl)}`\nSET TAGS (\n{tags_clause}\n);")
        elif scope == "column":
            plan.sql_statements.append(
                f"ALTER TABLE `{_ident_escape(cat)}`.`{_ident_escape(sch)}`.`{_ident_escape(tbl)}`\n"
                f"ALTER COLUMN `{_ident_escape(col)}`\nSET TAGS (\n{tags_clause}\n);"
            )

    return plan


# ---------------------------------------------------------------------------
# Coverage and gap analysis (Feature 5)
# ---------------------------------------------------------------------------

def compute_required_keys(rows, scope):
    """Return the sorted set of required (governed) tag keys defined for a scope."""
    out = set()
    for row in rows:
        key = (row.get("key") or "").strip()
        if not key or _norm(row.get("type", "")) != "governed":
            continue
        if row.get(f"scope_{scope}"):
            out.add(key)
    return sorted(out)


def analyze_object_coverage(objects, required_keys, tagged_records):
    """Compute required-tag coverage for a set of objects.

    objects: list of object-id strings (e.g. schema names, or "schema.table").
    required_keys: list of required tag keys for this scope.
    tagged_records: list of dicts with at least object_id and tag_name.
    Returns: {total, fully_covered, coverage_pct, gaps: [{object, missing_keys}]}.
    """
    tags_by_object = {}
    for rec in tagged_records:
        oid = rec.get("object_id")
        tags_by_object.setdefault(oid, set()).add(rec.get("tag_name"))

    total = len(objects)
    gaps = []
    fully = 0
    for obj in objects:
        present = tags_by_object.get(obj, set())
        missing = [k for k in required_keys if k not in present]
        if missing:
            gaps.append({"object": obj, "missing_keys": missing})
        else:
            fully += 1

    coverage_pct = round(100.0 * fully / total, 1) if total else 0.0
    return {"total": total, "fully_covered": fully, "coverage_pct": coverage_pct, "gaps": gaps}


def audit_value_drift(tagged_records, taxonomy_rows):
    """Flag live tag values (from information_schema reports) outside a governed tag's
    allowed-value set. tagged_records: list of dicts with tag_name/tag_value/object_id."""
    taxonomy_by_key = {}
    for row in taxonomy_rows:
        k = (row.get("key") or "").strip()
        if k:
            taxonomy_by_key[_norm(k)] = row

    issues = []
    for rec in tagged_records:
        key = rec.get("tag_name")
        value = rec.get("tag_value")
        taxonomy_row = taxonomy_by_key.get(_norm(key or ""))
        if not taxonomy_row:
            continue
        allowed_values = [v.strip() for v in (taxonomy_row.get("values") or "").split(",") if v.strip()]
        obj = rec.get("object_id", "")
        for issue in lint_value_drift(key, allowed_values, value or ""):
            if obj:
                issue.object_ref = f"{obj}"
            issues.append(issue)
    return issues


SENSITIVE_COLUMN_NAME_HINTS = [
    "email", "ssn", "phone", "address", "dob", "birth", "tax_id",
    "passport", "credit_card", "ip_address",
]

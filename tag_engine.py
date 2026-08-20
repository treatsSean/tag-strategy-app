"""
Shared tagging data model, validator/linter, and governance recommender.

Implements the shared data model (Step 1) plus Feature 2 (tag validator and
linter) and Feature 3 (governed vs free-form vs system-managed recommender)
from the Unity Catalog Tag Strategy Builder build plan.
"""

from __future__ import annotations

import json
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


# ─────────────────────────────────────────────────────────────────────────
# Feature 1: Prompt-to-taxonomy designer
# ─────────────────────────────────────────────────────────────────────────

_TAXONOMY_VALID_SCOPES = ("catalog", "schema", "table", "view", "column")

_TAXONOMY_VALID_CREATES = ["Central governance", "Domain leads", "Team leads", "Anyone"]

_TAXONOMY_VALID_ASSIGNS = [
    "Governance team only",
    "Service principals / admins",
    "Stewards / service principals",
    "Automation / stewards",
    "Governance team / owners",
    "Team leads / finance ops",
    "Practitioners / team leads",
    "Practitioners",
    "Anyone",
]

_TAXONOMY_VALID_AUTOMATION = [
    "None",
    "Manual",
    "Manual + propagation",
    "Audit & review candidates",
    "AMM surfaces candidates",
    "Auto-detect candidates",
    "Auto-assign (no review)",
    "Propagation only",
]

TAXONOMY_SYSTEM_PROMPT = """You are a Unity Catalog data governance expert and tagging strategist. Given a natural-language
description of an organization's tagging needs (possibly enriched with industry best-practice context), produce a JSON
array of tag definitions. Return ONLY a valid JSON array — no prose, no markdown code fences, no surrounding object.

Each element must be an object with exactly these fields:
- "category": string, short human label grouping this tag (e.g. "Classification / Sensitivity", "PII", "Compliance")
- "desc": string, one sentence describing what the tag captures, WHY it matters, and HOW it drives governance actions
- "type": either "governed" (org-wide, enforced) or "ungoverned" (team-local, flexible)
- "key": string, snake_case tag key (lowercase letters, digits, underscores only — no spaces, no hyphens)
- "values": comma-separated string of allowed values (use clear, short tokens: e.g. "public, internal, confidential, restricted"), or "" for open-ended
- "scopes": array of one or more of "catalog", "schema", "table", "view", "column" — be specific about WHERE this tag belongs
- "creates": one of "Central governance", "Domain leads", "Team leads", "Anyone"
- "assigns": one of "Governance team only", "Service principals / admins", "Stewards / service principals",
  "Automation / stewards", "Governance team / owners", "Team leads / finance ops", "Practitioners / team leads",
  "Practitioners", "Anyone"
- "automation": one of "None", "Manual", "Manual + propagation", "Audit & review candidates",
  "AMM surfaces candidates", "Auto-detect candidates", "Auto-assign (no review)", "Propagation only"
- "owner": string, a specific suggested owning team/role (e.g. "Privacy Office", "Data Governance Council", "Platform Engineering"), or "" if unknown

Guidelines:
- Produce between 5 and 12 tag definitions covering the full spectrum of the request
- ALWAYS include at least one tag from each relevant governance pillar: classification, ownership, compliance, lifecycle
- For PII/PHI tags, use column scope; for compliance/domain tags use schema or catalog scope; for lifecycle use table+schema
- Tags that should drive ABAC policies (access control) need sensitivity, compliance, or segmentation signals
- Cost-attribution tags should be at catalog or schema scope for chargeback aggregation
- Prefer governed type for anything that affects compliance, security, cost reporting, or discoverability
- Set automation appropriately: PII detection = "Auto-detect candidates", lifecycle = "AMM surfaces candidates", domain = "Manual"
- Make descriptions actionable: explain what governance action this tag enables (e.g. drives column masking, triggers retention policy)"""


def build_taxonomy_messages(user_prompt):
    """Build the (system_prompt, user_message) pair sent to the foundation model for taxonomy generation."""
    return TAXONOMY_SYSTEM_PROMPT, f"Design a tagging taxonomy for this need:\n\n{(user_prompt or '').strip()}"


def _taxonomy_scope_flags(scopes):
    requested = {str(s).strip().lower() for s in scopes} if isinstance(scopes, list) else set()
    return {f"scope_{s}": (s in requested) for s in _TAXONOMY_VALID_SCOPES}


def parse_taxonomy_response(raw_text):
    """Parse an LLM's taxonomy JSON response into Tag Matrix-shaped row dicts.

    Returns (rows, errors). `rows` is a list of dicts matching the app's Tag Matrix row schema
    (category/desc/type/key/values/scope_*/creates/assigns/automation/owner — no row_id). `errors`
    is a list of human-readable strings for anything that could not be parsed or had to be
    defaulted, so problems are surfaced to the user rather than silently dropped.
    """
    errors = []
    text = (raw_text or "").strip()

    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) >= 2 else text.lstrip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except Exception as e:
        return [], [f"Could not parse model response as JSON: {e}"]

    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                parsed = v
                break

    if not isinstance(parsed, list):
        return [], ["Model response was not a JSON array of tag definitions."]

    rows = []
    for i, item in enumerate(parsed):
        pos = i + 1
        if not isinstance(item, dict):
            errors.append(f"Item {pos}: not a JSON object — skipped.")
            continue

        key = re.sub(r"[^a-z0-9_]", "_", str(item.get("key", "")).strip().lower().replace(" ", "_").replace("-", "_"))
        key = re.sub(r"_+", "_", key).strip("_")
        if not key:
            errors.append(f"Item {pos}: missing or invalid 'key' — skipped.")
            continue

        gov_type = str(item.get("type", "governed")).strip().lower()
        if gov_type not in ("governed", "ungoverned"):
            gov_type = "governed"

        scope_flags = _taxonomy_scope_flags(item.get("scopes", []))
        if not any(scope_flags.values()):
            errors.append(f"Item {pos} ('{key}'): no valid scope specified — defaulted to 'table'.")
            scope_flags["scope_table"] = True

        creates = str(item.get("creates", "")).strip()
        if creates not in _TAXONOMY_VALID_CREATES:
            creates = _TAXONOMY_VALID_CREATES[0]

        assigns = str(item.get("assigns", "")).strip()
        if assigns not in _TAXONOMY_VALID_ASSIGNS:
            assigns = "Practitioners"

        automation = str(item.get("automation", "")).strip()
        if automation not in _TAXONOMY_VALID_AUTOMATION:
            automation = "Manual"

        rows.append({
            "category": str(item.get("category", "")).strip() or "Suggested",
            "desc": str(item.get("desc", "")).strip(),
            "type": gov_type,
            "key": key,
            "values": str(item.get("values", "")).strip(),
            **scope_flags,
            "creates": creates,
            "assigns": assigns,
            "automation": automation,
            "owner": str(item.get("owner", "")).strip(),
        })

    if not rows and not errors:
        errors.append("Model returned an empty taxonomy.")
    return rows, errors


def default_taxonomy_suggestion():
    """Starter taxonomy used as a one-click fallback when the model is unavailable, its
    response fails to parse, or the user just wants a quick baseline: data_classification,
    data_owner, cost_center, environment."""
    return [
        {
            "category": "Classification / Sensitivity",
            "desc": "Overall sensitivity/risk level of the data asset. Primary signal for access policies.",
            "type": "governed",
            "key": "data_classification",
            "values": "public, internal, confidential, restricted",
            **_taxonomy_scope_flags(["table", "view"]),
            "creates": "Central governance",
            "assigns": "Stewards / service principals",
            "automation": "Audit & review candidates",
            "owner": "",
        },
        {
            "category": "Ownership",
            "desc": "Individual or team accountable for this asset's quality and access decisions.",
            "type": "governed",
            "key": "data_owner",
            "values": "",
            **_taxonomy_scope_flags(["schema", "table"]),
            "creates": "Central governance",
            "assigns": "Practitioners / team leads",
            "automation": "Manual",
            "owner": "",
        },
        {
            "category": "Cost Attribution",
            "desc": "Ties the asset to a cost center for chargeback and cost-allocation reporting.",
            "type": "governed",
            "key": "cost_center",
            "values": "",
            **_taxonomy_scope_flags(["catalog", "schema"]),
            "creates": "Central governance",
            "assigns": "Team leads / finance ops",
            "automation": "Manual",
            "owner": "",
        },
        {
            "category": "Lifecycle / Environment",
            "desc": "Deployment environment or lifecycle stage the asset serves.",
            "type": "governed",
            "key": "environment",
            "values": "dev, staging, prod",
            **_taxonomy_scope_flags(["catalog", "schema", "table", "view"]),
            "creates": "Central governance",
            "assigns": "Practitioners",
            "automation": "Manual",
            "owner": "",
        },
    ]


# ─────────────────────────────────────────────────────────────────────────
# ABAC policy candidate suggester
# ─────────────────────────────────────────────────────────────────────────

_ABAC_PII_HINTS = ["pii", "ssn", "email", "phone", "address", "dob", "birth", "tax_id", "passport", "credit_card", "ip_address"]
_ABAC_SENSITIVITY_HINTS = ["sensitiv", "classif", "restrict", "confidential"]
_ABAC_COMPLIANCE_HINTS = ["complian", "gdpr", "hipaa", "pci", "sox", "ccpa", "regulat"]
_ABAC_SEGMENTATION_HINTS = ["region", "department", "business_unit", "geo", "tenant", "org", "domain", "subdomain"]


def _abac_text_matches(text, hints):
    t = (text or "").lower()
    return any(h in t for h in hints)


# ─────────────────────────────────────────────────────────────────────────
# Governance Patterns Library (curated best-practice taxonomies by industry)
# ─────────────────────────────────────────────────────────────────────────

GOVERNANCE_PATTERNS = {
    "healthcare": {
        "label": "Healthcare / Life Sciences (HIPAA)",
        "description": "HIPAA-aligned tagging for PHI, BAA status, minimum necessary, and research datasets.",
        "tags": [
            {"category": "PHI Classification", "key": "phi_level", "values": "none, limited_phi, full_phi, de_identified", "scopes": ["table", "column"], "type": "governed", "creates": "Central governance", "assigns": "Stewards / service principals", "automation": "Auto-detect candidates", "owner": "Privacy Office", "desc": "Protected Health Information level. Drives HIPAA minimum-necessary access controls."},
            {"category": "Compliance / Regulatory", "key": "hipaa_safeguard", "values": "administrative, physical, technical", "scopes": ["table", "schema"], "type": "governed", "creates": "Central governance", "assigns": "Service principals / admins", "automation": "Manual + propagation", "owner": "Compliance", "desc": "Which HIPAA safeguard category applies to this asset."},
            {"category": "Data Retention", "key": "retention_years", "values": "6, 7, 10, indefinite", "scopes": ["table"], "type": "governed", "creates": "Central governance", "assigns": "Governance team / owners", "automation": "Manual", "owner": "Records Management", "desc": "HIPAA minimum retention period for this dataset."},
            {"category": "Access Control", "key": "baa_required", "values": "yes, no", "scopes": ["schema", "table"], "type": "governed", "creates": "Central governance", "assigns": "Governance team only", "automation": "Manual + propagation", "owner": "Legal", "desc": "Whether accessing this data requires a Business Associate Agreement."},
            {"category": "Research", "key": "irb_status", "values": "approved, pending, exempt, not_applicable", "scopes": ["schema", "table"], "type": "governed", "creates": "Domain leads", "assigns": "Practitioners / team leads", "automation": "Manual", "owner": "Research Governance", "desc": "Institutional Review Board approval status for research datasets."},
        ],
    },
    "financial_services": {
        "label": "Financial Services (SOX / PCI / Basel)",
        "description": "SOX controls, PCI-DSS cardholder data, Basel risk classifications, and audit trail requirements.",
        "tags": [
            {"category": "SOX Controls", "key": "sox_control", "values": "itgc, application, entity_level, not_applicable", "scopes": ["table", "schema"], "type": "governed", "creates": "Central governance", "assigns": "Service principals / admins", "automation": "Manual + propagation", "owner": "Internal Audit", "desc": "SOX control classification for financial reporting data."},
            {"category": "PCI Classification", "key": "pci_scope", "values": "in_scope, out_of_scope, connected_to", "scopes": ["table", "column"], "type": "governed", "creates": "Central governance", "assigns": "Stewards / service principals", "automation": "Auto-detect candidates", "owner": "PCI QSA Team", "desc": "PCI-DSS scope classification for cardholder data environments."},
            {"category": "Risk Classification", "key": "data_criticality", "values": "critical, high, medium, low", "scopes": ["table", "schema"], "type": "governed", "creates": "Central governance", "assigns": "Governance team / owners", "automation": "AMM surfaces candidates", "owner": "Risk Management", "desc": "Business criticality for BCP/DR prioritization and Basel operational risk."},
            {"category": "Audit", "key": "audit_trail", "values": "full, partial, none", "scopes": ["table"], "type": "governed", "creates": "Central governance", "assigns": "Automation / stewards", "automation": "Auto-assign (no review)", "owner": "Internal Audit", "desc": "Level of change-data-capture audit trail maintained."},
            {"category": "Lineage", "key": "source_system", "values": "", "scopes": ["table"], "type": "governed", "creates": "Central governance", "assigns": "Practitioners", "automation": "Manual", "owner": "Data Engineering", "desc": "Upstream system of record for lineage tracking and reconciliation."},
        ],
    },
    "retail_ecommerce": {
        "label": "Retail / E-commerce (CCPA / GDPR)",
        "description": "Consumer privacy, consent management, personalization signals, and loyalty data classification.",
        "tags": [
            {"category": "Privacy", "key": "personal_data_category", "values": "identifier, behavioral, transactional, preference, sensitive", "scopes": ["column", "table"], "type": "governed", "creates": "Central governance", "assigns": "Stewards / service principals", "automation": "Auto-detect candidates", "owner": "Privacy Engineering", "desc": "CCPA/GDPR personal data category for right-to-delete and consent scoping."},
            {"category": "Consent", "key": "consent_basis", "values": "explicit_consent, legitimate_interest, contractual, legal_obligation", "scopes": ["table", "schema"], "type": "governed", "creates": "Central governance", "assigns": "Governance team / owners", "automation": "Manual + propagation", "owner": "Legal / Privacy", "desc": "GDPR lawful basis for processing this data."},
            {"category": "Data Subject", "key": "data_subject_type", "values": "customer, prospect, employee, partner, minor", "scopes": ["table"], "type": "governed", "creates": "Central governance", "assigns": "Practitioners / team leads", "automation": "Manual", "owner": "Data Governance", "desc": "Type of data subject — drives retention and deletion SLAs."},
            {"category": "Geo / Residency", "key": "data_residency", "values": "us, eu, uk, apac, global", "scopes": ["catalog", "schema"], "type": "governed", "creates": "Central governance", "assigns": "Service principals / admins", "automation": "Propagation only", "owner": "Infrastructure", "desc": "Geographic residency requirement for cross-border transfer compliance."},
            {"category": "Loyalty / Marketing", "key": "marketing_use", "values": "allowed, opt_out, suppressed", "scopes": ["table", "column"], "type": "governed", "creates": "Domain leads", "assigns": "Practitioners / team leads", "automation": "Audit & review candidates", "owner": "Marketing Ops", "desc": "Whether this data is cleared for marketing use per consent records."},
        ],
    },
    "technology": {
        "label": "Technology / SaaS (SOC2 / ISO 27001)",
        "description": "Multi-tenant isolation, environment tagging, service ownership, and SOC2 control mapping.",
        "tags": [
            {"category": "Tenant Isolation", "key": "tenant_scope", "values": "single_tenant, multi_tenant, internal_only, shared_service", "scopes": ["schema", "table"], "type": "governed", "creates": "Central governance", "assigns": "Service principals / admins", "automation": "Manual + propagation", "owner": "Platform Engineering", "desc": "Tenant isolation level — drives access control and data segregation policies."},
            {"category": "Environment", "key": "environment", "values": "production, staging, development, sandbox", "scopes": ["catalog", "schema"], "type": "governed", "creates": "Central governance", "assigns": "Practitioners", "automation": "Propagation only", "owner": "Platform Engineering", "desc": "Deployment environment — production data gets stricter controls."},
            {"category": "Service Ownership", "key": "owning_service", "values": "", "scopes": ["schema", "table"], "type": "governed", "creates": "Central governance", "assigns": "Practitioners / team leads", "automation": "Manual", "owner": "Engineering Management", "desc": "Microservice or team that owns this data product."},
            {"category": "SOC2 Control", "key": "soc2_criteria", "values": "cc6_access, cc7_operations, cc8_change_mgmt, pi1_privacy", "scopes": ["table", "schema"], "type": "governed", "creates": "Central governance", "assigns": "Governance team only", "automation": "AMM surfaces candidates", "owner": "Security & Compliance", "desc": "SOC2 Trust Services Criteria relevant to this asset."},
            {"category": "Data Tier", "key": "data_tier", "values": "raw, bronze, silver, gold, platinum", "scopes": ["schema", "table"], "type": "governed", "creates": "Domain leads", "assigns": "Practitioners", "automation": "Manual", "owner": "Data Engineering", "desc": "Medallion architecture tier — signals quality and readiness for consumption."},
        ],
    },
    "government": {
        "label": "Government / Public Sector (FedRAMP / FISMA)",
        "description": "FISMA impact levels, CUI marking, FOIA exemptions, and records management.",
        "tags": [
            {"category": "FISMA Impact", "key": "fisma_impact", "values": "low, moderate, high", "scopes": ["catalog", "schema", "table"], "type": "governed", "creates": "Central governance", "assigns": "Governance team only", "automation": "Manual + propagation", "owner": "ISSO", "desc": "FISMA security categorization (FIPS 199) — drives control baseline selection."},
            {"category": "CUI Marking", "key": "cui_category", "values": "cui_basic, cui_specified, not_cui", "scopes": ["table", "column"], "type": "governed", "creates": "Central governance", "assigns": "Stewards / service principals", "automation": "Audit & review candidates", "owner": "Records Officer", "desc": "Controlled Unclassified Information marking per NIST 800-171."},
            {"category": "FOIA", "key": "foia_exempt", "values": "yes, no, partial", "scopes": ["table"], "type": "governed", "creates": "Central governance", "assigns": "Governance team / owners", "automation": "Manual", "owner": "Legal / FOIA Office", "desc": "FOIA exemption status for public records requests."},
            {"category": "Records Retention", "key": "nara_schedule", "values": "", "scopes": ["table", "schema"], "type": "governed", "creates": "Central governance", "assigns": "Governance team only", "automation": "Manual", "owner": "Records Management", "desc": "NARA records schedule code for disposition."},
        ],
    },
    "universal": {
        "label": "Universal Governance Foundation",
        "description": "Core tags every organization needs regardless of industry: classification, ownership, domain, lifecycle, and cost.",
        "tags": [
            {"category": "Classification / Sensitivity", "key": "sensitivity_level", "values": "public, internal, confidential, restricted", "scopes": ["table", "view"], "type": "governed", "creates": "Central governance", "assigns": "Stewards / service principals", "automation": "Audit & review candidates", "owner": "Data Governance Council", "desc": "Overall data sensitivity. Primary signal for access control policies and ABAC."},
            {"category": "PII Classification", "key": "pii", "values": "ssn, email, phone, name, dob, address, ip_address", "scopes": ["column"], "type": "governed", "creates": "Central governance", "assigns": "Automation / stewards", "automation": "Auto-detect candidates", "owner": "Privacy Team", "desc": "Column-level PII type. Drives column masking and right-to-delete workflows."},
            {"category": "Domain", "key": "domain", "values": "finance, sales, marketing, engineering, hr, product, legal, operations", "scopes": ["catalog", "schema"], "type": "governed", "creates": "Central governance", "assigns": "Practitioners / team leads", "automation": "Manual", "owner": "Data Governance Council", "desc": "Business domain for discovery, routing, and federated governance boundaries."},
            {"category": "Certification", "key": "certification_status", "values": "certified, under_review, deprecated", "scopes": ["table", "schema"], "type": "governed", "creates": "Central governance", "assigns": "Governance team only", "automation": "AMM surfaces candidates", "owner": "Data Governance Council", "desc": "Signals validated source of truth. Deprecated assets get surfaced for cleanup."},
            {"category": "Cost Attribution", "key": "cost_center", "values": "", "scopes": ["catalog", "schema"], "type": "governed", "creates": "Central governance", "assigns": "Team leads / finance ops", "automation": "Manual", "owner": "Finance", "desc": "Ties assets to cost centers for chargeback and FinOps reporting."},
            {"category": "Ownership", "key": "data_owner", "values": "", "scopes": ["schema", "table"], "type": "governed", "creates": "Central governance", "assigns": "Practitioners / team leads", "automation": "Manual", "owner": "Data Governance Council", "desc": "Accountable individual or team for data quality and access decisions."},
            {"category": "Lifecycle", "key": "lifecycle_stage", "values": "active, deprecated, archived, sunset", "scopes": ["table", "view", "schema"], "type": "governed", "creates": "Central governance", "assigns": "Governance team / owners", "automation": "AMM surfaces candidates", "owner": "Data Governance Council", "desc": "Asset health state. Drives discovery ranking and cleanup automation."},
        ],
    },
}


def get_pattern_names():
    """Return list of (key, label) tuples for the governance patterns library."""
    return [(k, v["label"]) for k, v in GOVERNANCE_PATTERNS.items()]


def get_pattern_tags(pattern_key):
    """Return the tag definitions for a governance pattern, formatted as Tag Matrix rows."""
    pattern = GOVERNANCE_PATTERNS.get(pattern_key)
    if not pattern:
        return []
    rows = []
    for tag in pattern["tags"]:
        scope_flags = {f"scope_{s}": (s in tag.get("scopes", [])) for s in _TAXONOMY_VALID_SCOPES}
        rows.append({
            "category": tag["category"],
            "desc": tag["desc"],
            "type": tag["type"],
            "key": tag["key"],
            "values": tag["values"],
            **scope_flags,
            "creates": tag["creates"],
            "assigns": tag["assigns"],
            "automation": tag["automation"],
            "owner": tag["owner"],
        })
    return rows


def match_patterns_to_prompt(user_prompt):
    """Given a user's plain-language governance description, identify which patterns apply.
    Returns list of pattern keys sorted by relevance score."""
    prompt_lower = (user_prompt or "").lower()
    scores = {}
    keyword_map = {
        "healthcare": ["hipaa", "phi", "healthcare", "hospital", "patient", "clinical", "medical", "ehr", "emr", "life science", "pharma"],
        "financial_services": ["sox", "pci", "financial", "banking", "payment", "credit card", "cardholder", "basel", "trading", "finserv", "insurance", "loan"],
        "retail_ecommerce": ["retail", "ecommerce", "e-commerce", "consumer", "gdpr", "ccpa", "loyalty", "customer", "marketing", "consent", "shopping"],
        "technology": ["saas", "soc2", "soc 2", "iso 27001", "multi-tenant", "multitenant", "api", "microservice", "platform", "startup", "software"],
        "government": ["government", "fedramp", "fisma", "federal", "agency", "cui", "foia", "nist", "public sector", "dod", "military"],
        "universal": ["general", "basic", "foundation", "start", "getting started", "best practice", "standard"],
    }
    for pattern_key, keywords in keyword_map.items():
        score = sum(1 for kw in keywords if kw in prompt_lower)
        if score > 0:
            scores[pattern_key] = score
    # Always include universal as a fallback with low priority
    if "universal" not in scores:
        scores["universal"] = 0.1
    return sorted(scores.keys(), key=lambda k: scores[k], reverse=True)


# ─────────────────────────────────────────────────────────────────────────
# Freeform → Governed analysis
# ─────────────────────────────────────────────────────────────────────────

def analyze_freeform_tags(live_tag_records, taxonomy_rows):
    """Analyze live tags from information_schema and identify freeform tags that should be governed.

    live_tag_records: list of dicts with tag_name, tag_value, scope (catalog/schema/table/column),
                      and object_id.
    taxonomy_rows: current Tag Matrix rows.

    Returns a list of dicts: {key, usage_count, unique_values, objects, recommendation, rationale, priority}
    """
    taxonomy_keys = set()
    for row in (taxonomy_rows or []):
        k = (row.get("key") or "").strip()
        if k and _norm(row.get("type", "")) == "governed":
            taxonomy_keys.add(_norm(k))

    # Aggregate live tags not in the governed taxonomy
    freeform_agg = {}
    for rec in live_tag_records:
        tag_name = (rec.get("tag_name") or "").strip()
        if not tag_name or _norm(tag_name) in taxonomy_keys:
            continue
        nk = _norm(tag_name)
        if nk not in freeform_agg:
            freeform_agg[nk] = {
                "key": tag_name,
                "values": set(),
                "objects": set(),
                "scopes": set(),
            }
        freeform_agg[nk]["values"].add(rec.get("tag_value") or "")
        freeform_agg[nk]["objects"].add(rec.get("object_id") or "")
        freeform_agg[nk]["scopes"].add(rec.get("scope") or "")

    results = []
    for nk, info in freeform_agg.items():
        usage_count = len(info["objects"])
        unique_values = sorted(info["values"] - {""})
        mode, rationale = recommend_governance_mode(info["key"], required=False, category="")

        # Determine priority based on signals
        priority = "low"
        if mode == "governed":
            priority = "high"
        elif usage_count >= 5:
            priority = "medium"
        elif len(unique_values) <= 5 and usage_count >= 3:
            priority = "medium"

        # Enhanced rationale for migration
        migration_rationale = rationale
        if usage_count >= 10:
            migration_rationale += f" High adoption ({usage_count} objects) suggests org-wide relevance."
        if len(unique_values) <= 5 and unique_values:
            migration_rationale += f" Bounded value set ({', '.join(unique_values[:5])}) is a good fit for governed enumeration."
        elif len(unique_values) > 20:
            migration_rationale += f" High cardinality ({len(unique_values)} distinct values) — may be better as metadata than a governed tag."

        results.append({
            "key": info["key"],
            "usage_count": usage_count,
            "unique_values": unique_values[:10],  # cap display
            "total_unique_values": len(unique_values),
            "objects_sample": sorted(info["objects"])[:5],
            "scopes": sorted(info["scopes"] - {""}),
            "recommendation": mode,
            "rationale": migration_rationale,
            "priority": priority,
        })

    # Sort by priority (high first), then by usage count
    priority_order = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda r: (priority_order.get(r["priority"], 3), -r["usage_count"]))
    return results


# ─────────────────────────────────────────────────────────────────────────
# DAB Bundle generation
# ─────────────────────────────────────────────────────────────────────────

def generate_dab_bundle(sql_content, catalog="", schema="", table="", job_name="tag-strategy-apply"):
    """Generate a Declarative Automation Bundle (DAB) structure for deploying tag SQL as a job.

    Returns a dict of {filename: content} for the bundle files.
    """
    import yaml

    bundle_yaml = {
        "bundle": {
            "name": job_name,
        },
        "resources": {
            "jobs": {
                job_name: {
                    "name": f"[Tag Strategy] Apply tags - {catalog or 'catalog'}.{schema or 'schema'}",
                    "description": "Auto-generated by UC Tag Strategy Builder. Applies governed tags per the approved taxonomy.",
                    "schedule": {
                        "quartz_cron_expression": "0 0 6 * * ?",
                        "timezone_id": "UTC",
                    },
                    "tasks": [
                        {
                            "task_key": "apply_tags",
                            "sql_task": {
                                "query": {
                                    "source": "WORKSPACE",
                                },
                                "warehouse_id": "${var.warehouse_id}",
                                "file": {
                                    "path": "./src/apply_tags.sql",
                                },
                            },
                        }
                    ],
                    "tags": {
                        "managed_by": "tag-strategy-builder",
                        "catalog": catalog or "unspecified",
                    },
                }
            }
        },
        "variables": {
            "warehouse_id": {
                "description": "SQL warehouse ID to execute the tag statements",
                "default": "",
            },
        },
        "targets": {
            "dev": {
                "mode": "development",
                "default": True,
            },
            "prod": {
                "mode": "production",
                "run_as": {
                    "service_principal_name": "${var.sp_name}",
                },
            },
        },
    }

    # Generate the files dict
    try:
        bundle_content = yaml.dump(bundle_yaml, default_flow_style=False, sort_keys=False)
    except Exception:
        # Fallback to manual YAML if yaml module not available
        bundle_content = _manual_yaml_bundle(bundle_yaml, job_name, catalog, schema)

    files = {
        "databricks.yml": bundle_content,
        "src/apply_tags.sql": sql_content,
        "README.md": f"""# Tag Strategy Bundle: {job_name}

Auto-generated by the UC Tag Strategy Builder.

## Deploy

```bash
# Install the Databricks CLI if you haven't already
pip install databricks-cli

# Configure your workspace
databricks configure

# Deploy the bundle
databricks bundle deploy --target dev

# Run the job
databricks bundle run {job_name} --target dev
```

## What this does

This bundle creates a scheduled Lakeflow Job that applies your governed tag taxonomy to:
- Catalog: `{catalog or '<catalog>'}`
- Schema: `{schema or '<schema>'}`
- Table: `{table or '<table>'}`

The job runs daily at 06:00 UTC by default. Edit `databricks.yml` to change the schedule.

## Variables

| Variable | Description |
|----------|-------------|
| `warehouse_id` | SQL warehouse to execute statements |
| `sp_name` | Service principal for production runs |

Set variables in your target config or via `--var` flag:
```bash
databricks bundle deploy --target prod --var="warehouse_id=abc123,sp_name=my-sp"
```
""",
    }
    return files


def _manual_yaml_bundle(bundle_yaml, job_name, catalog, schema):
    """Fallback YAML generation without PyYAML."""
    return f"""bundle:
  name: {job_name}

resources:
  jobs:
    {job_name}:
      name: "[Tag Strategy] Apply tags - {catalog or 'catalog'}.{schema or 'schema'}"
      description: "Auto-generated by UC Tag Strategy Builder. Applies governed tags per the approved taxonomy."
      schedule:
        quartz_cron_expression: "0 0 6 * * ?"
        timezone_id: "UTC"
      tasks:
        - task_key: apply_tags
          sql_task:
            warehouse_id: ${{var.warehouse_id}}
            file:
              path: "./src/apply_tags.sql"
      tags:
        managed_by: tag-strategy-builder
        catalog: {catalog or 'unspecified'}

variables:
  warehouse_id:
    description: "SQL warehouse ID to execute the tag statements"
    default: ""
  sp_name:
    description: "Service principal name for production target"
    default: ""

targets:
  dev:
    mode: development
    default: true
  prod:
    mode: production
    run_as:
      service_principal_name: ${{var.sp_name}}
"""


def suggest_abac_candidates(rows):
    """Scan governed Tag Matrix rows for good ABAC (row filter / column mask) policy candidates.

    ABAC policies cannot be applied to views, so view-only scope is excluded (per Unity Catalog ABAC
    limitations). Returns a list of dicts: {key, category, values, scopes, policy_types, rationale}.
    `policy_types` is a subset of ["column_mask", "row_filter"] — a tag can suggest both if it carries
    both column and table/schema/catalog scope (e.g. a PII tag with a compliance angle).
    """
    candidates = []
    for row in rows:
        if _norm(row.get("type", "")) != "governed":
            continue
        key = (row.get("key") or "").strip()
        if not key:
            continue
        category = row.get("category", "")
        haystack = f"{key} {category}"
        scopes = [s for s in ("catalog", "schema", "table", "view", "column") if row.get(f"scope_{s}")]
        non_view_scopes = [s for s in scopes if s != "view"]
        if not non_view_scopes:
            continue

        policy_types = []
        rationale_bits = []

        if "column" in non_view_scopes and _abac_text_matches(haystack, _ABAC_PII_HINTS):
            policy_types.append("column_mask")
            rationale_bits.append("column-level PII signal — good column mask candidate")

        table_level_scopes = [s for s in non_view_scopes if s in ("catalog", "schema", "table")]
        is_sensitivity = _abac_text_matches(haystack, _ABAC_SENSITIVITY_HINTS)
        is_compliance = _abac_text_matches(haystack, _ABAC_COMPLIANCE_HINTS)
        is_segmentation = _abac_text_matches(haystack, _ABAC_SEGMENTATION_HINTS)
        if table_level_scopes and (is_sensitivity or is_compliance or is_segmentation):
            policy_types.append("row_filter")
            if is_sensitivity:
                rationale_bits.append("sensitivity/classification signal — good row filter gate")
            elif is_compliance:
                rationale_bits.append("compliance/regulatory signal — good row filter gate")
            else:
                rationale_bits.append("segmentation signal (region/department/tenant) — good row filter gate")

        if not policy_types:
            continue

        candidates.append({
            "key": key,
            "category": category,
            "values": row.get("values", ""),
            "scopes": non_view_scopes,
            "policy_types": policy_types,
            "rationale": "; ".join(rationale_bits),
        })
    return candidates


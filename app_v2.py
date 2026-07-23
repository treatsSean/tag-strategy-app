"""
Unity Catalog Tag Strategy Builder
Databricks App — Streamlit + Databricks SDK
"""

import pandas as pd
import streamlit as st
from datetime import date

SCOPE_OPTIONS = ["catalog", "schema", "table", "view", "column"]
SCOPE_COLS = [f"scope_{s}" for s in SCOPE_OPTIONS]
CREATE_OPTIONS = ["Central governance", "Domain leads", "Team leads", "Anyone"]
ASSIGN_OPTIONS = [
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
AUTOMATION_OPTIONS = [
    "None",
    "Manual",
    "Manual + propagation",
    "Audit & review candidates",
    "AMM surfaces candidates",
    "Auto-detect candidates",
    "Auto-assign (no review)",
    "Propagation only",
]


def _scope_flags(*active):
    return {f"scope_{s}": (s in active) for s in SCOPE_OPTIONS}


def _scopes(row):
    return [s for s in SCOPE_OPTIONS if row.get(f"scope_{s}")]


def _row_scope_label(row):
    return ", ".join(_scopes(row)) or "No scope selected"


def _row_completion(row):
    if str(row.get("type", "")).strip() != "governed":
        return 1.0
    checks = [
        bool(str(row.get("key", "")).strip()),
        bool(str(row.get("values", "")).strip()),
        bool(_scopes(row)),
        bool(str(row.get("owner", "")).strip()),
    ]
    return sum(checks) / len(checks)


def _with_row_ids(df):
    df = df.copy()
    if "row_id" not in df.columns:
        start = st.session_state.next_row_id
        df["row_id"] = list(range(start, start + len(df)))
        st.session_state.next_row_id = start + len(df)
    return df


def _blank_row():
    row = {
        "category": "New category",
        "desc": "",
        "type": "governed",
        "key": "",
        "values": "",
        **_scope_flags("table"),
        "creates": "Central governance",
        "assigns": "Practitioners",
        "automation": "Manual",
        "owner": "",
        "row_id": st.session_state.next_row_id,
    }
    st.session_state.next_row_id += 1
    return row


st.set_page_config(
    page_title="UC Tag Strategy Builder",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "theme_mode" not in st.session_state:
    st.session_state.theme_mode = "Dark Header"
THEME = st.session_state.theme_mode

if THEME == "Light":
    _PAGE_BG = "#F9F7F4"       # Oat Light
    _SIDEBAR_BG = "#FFFFFF"    # White
    _SIDEBAR_TEXT = "#0B2026"  # Navy 900
    _SIDEBAR_BORDER = "rgba(11, 32, 38, 0.12)"
    _FOOTER_BG = "#EEEDE9"     # Oat Medium
    _FOOTER_TEXT = "#0B2026"   # Navy 900
    _CODE_BG = "#EEEDE9"       # Oat Medium
    _CODE_TEXT = "#0B2026"     # Navy 900
else:
    _PAGE_BG = "#F9F7F4"       # Oat Light
    _SIDEBAR_BG = "#0B2026"    # Navy 900
    _SIDEBAR_TEXT = "#F9F7F4"  # Oat Light
    _SIDEBAR_BORDER = "rgba(249, 247, 244, 0.18)"
    _FOOTER_BG = "#0B2026"     # Navy 900
    _FOOTER_TEXT = "#F9F7F4"   # Oat Light
    _CODE_BG = "#0B2026"       # Navy 900
    _CODE_TEXT = "#F9F7F4"     # Oat Light

st.markdown(f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Mono:wght@400;500&display=swap');

  body, body * {{
    font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif;
  }}
  [data-testid="stIconMaterial"], [data-testid="stIconMaterial"] * {{
    font-family: 'Material Symbols Rounded' !important;
  }}
  code, pre, [data-testid="stCode"] pre, [data-testid="stCode"] code {{
    font-family: 'DM Mono', monospace !important;
  }}

  [data-testid="stAppViewContainer"] {{ background-color: {_PAGE_BG}; }}
  [data-testid="stSidebar"] {{ background-color: {_SIDEBAR_BG}; }}
  [data-testid="stSidebar"] * {{ color: {_SIDEBAR_TEXT} !important; }}

  [data-testid="stHeader"] {{
    display: none;
  }}

  .stButton > button[kind="primary"] {{
    background-color: #FF3621 !important;
    border-color: #FF3621 !important;
    color: white !important;
  }}
  .stButton > button[kind="primary"]:hover {{
    background-color: #D42E1A !important;
    border-color: #D42E1A !important;
  }}

  .stButton > button[kind="secondary"] {{
    color: #FF3621 !important;
    border-color: #FF3621 !important;
  }}
  a, a:visited {{ color: #FF3621 !important; }}

  [data-testid="stCode"] {{
    background-color: {_CODE_BG} !important;
  }}

  .block-container {{
    padding-top: 1.5rem !important;
    padding-bottom: 2.5rem !important;
  }}

  .db-sidebar-brand {{
    display: flex;
    align-items: center;
    padding-bottom: 10px;
    margin-bottom: 12px;
    border-bottom: 1px solid {_SIDEBAR_BORDER};
  }}
  .db-sidebar-brand .db-sidebar-name {{ font-size: 13.5px; font-weight: 700; letter-spacing: 0.02em; }}

  [data-testid="stSidebar"] .block-container {{
    padding-top: 1.25rem !important;
  }}
  [data-testid="stSidebar"] h5 {{
    margin-top: 0.2rem !important;
    margin-bottom: 0.3rem !important;
    font-size: 0.82rem !important;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    opacity: 0.85;
  }}
  [data-testid="stSidebar"] hr {{
    margin: 0.6rem 0 !important;
  }}
  [data-testid="stSidebar"] [data-testid="stAlert"] {{
    padding: 0.45rem 0.6rem !important;
    font-size: 12.5px !important;
  }}
  [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{
    gap: 0.5rem;
  }}

  button[data-baseweb="tab"] {{
    padding: 6px 16px !important;
  }}
  [data-testid="stTabs"] {{
    margin-top: 0.25rem !important;
  }}

  .db-footer-bar {{
    background: {_FOOTER_BG};
    color: {_FOOTER_TEXT};
    padding: 10px 16px;
    border-radius: 6px;
    margin-top: 12px;
    font-size: 12px;
  }}
</style>
""", unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def get_workspace_client():
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        w.current_user.me()
        return w, None
    except Exception as e:
        return None, str(e)


@st.cache_data(show_spinner=False, ttl=300)
def list_catalogs(_w):
    try:
        return [c.name for c in _w.catalogs.list() if c.name]
    except Exception:
        return []


@st.cache_data(show_spinner=False, ttl=300)
def list_schemas(_w, catalog):
    try:
        return [s.name for s in _w.schemas.list(catalog_name=catalog) if s.name]
    except Exception:
        return []


@st.cache_data(show_spinner=False, ttl=300)
def list_tables(_w, catalog, schema):
    try:
        return [t.name for t in _w.tables.list(catalog_name=catalog, schema_name=schema) if t.name]
    except Exception:
        return []


def _get_warehouse_id(w):
    try:
        warehouses = list(w.warehouses.list())
        running = [wh for wh in warehouses if wh.state and wh.state.value == "RUNNING"]
        if running:
            return running[0].id
        if warehouses:
            return warehouses[0].id
    except Exception:
        pass
    return None


@st.cache_data(show_spinner=False, ttl=60)
def get_existing_tags(_w, catalog, schema, table):
    try:
        results = {}
        df = _w.statement_execution.execute(
            warehouse_id=_get_warehouse_id(_w),
            statement=(
                f"SELECT tag_name, tag_value FROM `{catalog}`.information_schema.table_tags "
                f"WHERE schema_name='{schema}' AND table_name='{table}'"
            ),
        )
        if df and df.result and df.result.data_array:
            for row in df.result.data_array:
                results[row[0]] = row[1]
        return results
    except Exception:
        return {}


DEFAULT_ROWS = [
    {"category": "Classification / Sensitivity", "desc": "Overall risk level. Primary signal for access control policies.", "type": "governed", "key": "sensitivity_level", "values": "public, sensitive, confidential, restricted", "creates": "Central governance", "assigns": "Stewards / service principals", "automation": "Audit & review candidates", "owner": "", **_scope_flags("table", "view")},
    {"category": "PII Classification", "desc": "Column-level evidence of specific personal data types.", "type": "governed", "key": "pii", "values": "ssn, email, phone, name, dob, address, ip_address", "creates": "Central governance", "assigns": "Automation / stewards", "automation": "Auto-detect candidates", "owner": "", **_scope_flags("column")},
    {"category": "Compliance / Regulatory", "desc": "Regulatory frameworks that apply to this asset.", "type": "governed", "key": "compliance", "values": "pci, hipaa, gdpr, ccpa, sox", "creates": "Central governance", "assigns": "Service principals / admins", "automation": "Manual + propagation", "owner": "", **_scope_flags("table", "schema")},
    {"category": "Domain", "desc": "Business area the asset belongs to. Powers Databricks discovery.", "type": "governed", "key": "domain", "values": "finance, sales, marketing, engineering, hr, product, legal", "creates": "Central governance", "assigns": "Practitioners / team leads", "automation": "Manual", "owner": "", **_scope_flags("catalog", "schema")},
    {"category": "Subdomain", "desc": "Finer-grained function within a domain for large orgs.", "type": "governed", "key": "subdomain", "values": "audit, tax, fp_a, demand_gen, eng_data", "creates": "Central governance", "assigns": "Practitioners", "automation": "Manual", "owner": "", **_scope_flags("schema", "table")},
    {"category": "Certification", "desc": "Signals the asset is the validated source of truth.", "type": "governed", "key": "certification", "values": "certified", "creates": "Central governance", "assigns": "Governance team only", "automation": "AMM surfaces candidates", "owner": "", **_scope_flags("table", "schema")},
    {"category": "Lifecycle / Deprecation", "desc": "Asset health and maintenance state for discovery quality.", "type": "governed", "key": "lifecycle", "values": "active, deprecated, archived", "creates": "Central governance", "assigns": "Governance team / owners", "automation": "AMM surfaces candidates", "owner": "", **_scope_flags("table", "view", "schema")},
    {"category": "Cost Attribution", "desc": "Ties assets to cost centers for chargeback reporting.", "type": "governed", "key": "cost_center", "values": "", "creates": "Central governance", "assigns": "Team leads / finance ops", "automation": "Manual", "owner": "", **_scope_flags("catalog", "schema")},
    {"category": "Team / Project", "desc": "Owning team or project for routing and discoverability.", "type": "governed", "key": "team", "values": "", "creates": "Central governance", "assigns": "Practitioners", "automation": "Manual", "owner": "", **_scope_flags("schema", "table")},
    {"category": "Free-form / Ad hoc", "desc": "Practitioner annotations, workflow flags, personal notes.", "type": "ungoverned", "key": "", "values": "", "creates": "Anyone", "assigns": "Anyone", "automation": "None", "owner": "", **_scope_flags("table", "column", "schema")},
]

COLUMNS = ["category", "desc", "type", "key", "values", *SCOPE_COLS, "creates", "assigns", "automation", "owner"]

if "next_row_id" not in st.session_state:
    st.session_state.next_row_id = 1
if "tag_rows" not in st.session_state:
    st.session_state.tag_rows = _with_row_ids(pd.DataFrame(DEFAULT_ROWS, columns=COLUMNS))
else:
    st.session_state.tag_rows = _with_row_ids(st.session_state.tag_rows)
if "target_catalog" not in st.session_state:
    st.session_state.target_catalog = ""
if "target_schema" not in st.session_state:
    st.session_state.target_schema = ""
if "target_table" not in st.session_state:
    st.session_state.target_table = ""


def _governed_rows():
    df = st.session_state.tag_rows.copy()
    return df[(df["type"] == "governed") & (df["key"].astype(str).str.strip() != "")]


def _vals(row):
    return [v.strip() for v in str(row.get("values", "")).split(",") if v.strip()]


def _sql_escape(s):
    """Escape a value for safe embedding inside a single-quoted SQL string literal."""
    return str(s).replace("'", "''")


def _hcl_escape(s):
    """Escape a value for safe embedding inside a double-quoted Terraform HCL string literal."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def generate_sql(catalog="", schema="", table=""):
    cat = catalog or "<catalog>"
    sch = f"{catalog + '.' if catalog else ''}{schema or '<schema>'}"
    tbl = ".".join(filter(None, [catalog, schema, table])) or "<catalog.schema.table>"
    today = date.today().strftime("%B %d, %Y")
    lines = [
        "-- ════════════════════════════════════════════════════════════",
        "-- Unity Catalog · Tag Strategy Implementation",
        f"-- Generated: {today}",
        "-- Requires: DBR 13.3+ (ALTER SET TAGS) or DBR 16.1+ (SET TAG ON)",
        "-- ════════════════════════════════════════════════════════════",
        "",
        "-- ── STEP 1: Governed tag key reference spec ────────────────",
        "-- Governed tags are created via Catalog Explorer or REST API.",
        "-- API: POST /api/2.1/unity-catalog/tags",
        "",
    ]
    for _, row in _governed_rows().iterrows():
        vals = _vals(row)
        lines += [
            f"-- Key:    {row['key']}",
            f"-- Desc:   {row.get('desc', row['category'])}",
            f"-- Values: {' | '.join(vals) if vals else '(open string)'}",
            f"-- Scope:  {', '.join(_scopes(row)) or '—'}  |  Owner: {row.get('owner', '—') or '—'}",
            "",
        ]
    cat_rows = [r for _, r in _governed_rows().iterrows() if "catalog" in _scopes(r)]
    if cat_rows:
        parts = [f"  '{_sql_escape(r['key'])}' = '{_sql_escape(_vals(r)[0] if _vals(r) else '<value>')}'  -- {r['category']}" for r in cat_rows]
        lines += ["-- ── STEP 2: Apply catalog-level tags ──────────────────────", f"ALTER CATALOG {cat}", "SET TAGS (\n" + ",\n".join(parts) + "\n);", ""]
    sch_rows = [r for _, r in _governed_rows().iterrows() if "schema" in _scopes(r)]
    if sch_rows:
        parts = [f"  '{_sql_escape(r['key'])}' = '{_sql_escape(_vals(r)[0] if _vals(r) else '<value>')}'  -- {r['category']}" for r in sch_rows]
        lines += ["-- ── STEP 3: Apply schema-level tags ───────────────────────", f"ALTER SCHEMA {sch}", "SET TAGS (\n" + ",\n".join(parts) + "\n);", ""]
    tbl_rows = [r for _, r in _governed_rows().iterrows() if any(s in _scopes(r) for s in ["table", "view"])]
    if tbl_rows:
        parts = [f"  '{_sql_escape(r['key'])}' = '{_sql_escape(_vals(r)[0] if _vals(r) else '<value>')}'  -- {r['category']}" for r in tbl_rows]
        lines += ["-- ── STEP 4: Apply table/view-level tags ──────────────────", f"ALTER TABLE {tbl}", "SET TAGS (\n" + ",\n".join(parts) + "\n);", ""]
    col_rows = [r for _, r in _governed_rows().iterrows() if "column" in _scopes(r)]
    if col_rows:
        lines += [
            "-- ── STEP 5: Apply column-level tags ───────────────────────",
            "-- NOTE: Each column requires its own ALTER TABLE statement.",
            "",
        ]
        for r in col_rows:
            v = _vals(r)
            lines += [
                f"-- {r['category']}: repeat for each column carrying this tag.",
                f"ALTER TABLE {tbl}",
                "ALTER COLUMN <column_name>",
                "SET TAGS (",
                f"  '{_sql_escape(r['key'])}' = '{_sql_escape(v[0] if v else '<value>')}'",
                ");",
                "",
            ]
    cat_name = catalog or "<catalog>"
    lines += [
        "-- ── STEP 6: Verify tags were applied ──────────────────────",
        f"SELECT tag_name, tag_value, table_schema, table_name FROM `{cat_name}`.information_schema.table_tags ORDER BY table_schema, table_name;",
        f"SELECT tag_name, tag_value, table_name, column_name FROM `{cat_name}`.information_schema.column_tags ORDER BY table_name, column_name;",
        f"SELECT tag_name, tag_value, schema_name FROM `{cat_name}`.information_schema.schema_tags ORDER BY schema_name;",
    ]
    return "\n".join(lines)


def generate_tf(catalog="", schema="", table=""):
    def tf_id(s):
        return "".join(c if c.isalnum() else "_" for c in s).lower() or "resource"

    cat_id = tf_id(catalog) if catalog else "my_catalog"
    sch_id = tf_id(schema) if schema else "my_schema"
    tbl_id = tf_id(table) if table else "my_table"
    today = date.today().strftime("%B %d, %Y")
    lines = [
        "# ═══════════════════════════════════════════════════════════",
        "# Unity Catalog · Tag Strategy — Terraform HCL",
        f"# Generated: {today}",
        "# Provider: hashicorp/databricks >= 1.38.0",
        "# ═══════════════════════════════════════════════════════════",
        "",
        "# ── Governed tag definitions ─────────────────────────────────",
        "",
    ]
    for _, row in _governed_rows().iterrows():
        vals = _vals(row)
        rid = tf_id(row["key"])
        lines += [f'# {row["category"]} — {row.get("desc", "")}', f'resource "databricks_tag" "{rid}" {{', f'  name = "{_hcl_escape(row["key"])}"']
        if vals:
            lines.append("  allowed_values = [" + ", ".join(f'\"{v}\"' for v in vals) + "]")
        if row.get("owner"):
            lines.append(f'  # Owner / DRI: {row["owner"]}')
        lines += ["}", ""]
    cat_rows = [r for _, r in _governed_rows().iterrows() if "catalog" in _scopes(r)]
    lines += ["# ── Catalog ─────────────────────────────────────────────────", f'resource "databricks_catalog" "{cat_id}" {{', f'  name    = "{_hcl_escape(catalog) if catalog else "<catalog_name>"}"', '  comment = "<optional description>"']
    if cat_rows:
        lines.append("  tags = {")
        for r in cat_rows:
            v = _vals(r)
            lines.append(f'    "{_hcl_escape(r["key"])}" = "{_hcl_escape(v[0] if v else "<value>")}"  # {r["category"]}')
        lines.append("  }")
    lines += ["}", ""]
    sch_rows = [r for _, r in _governed_rows().iterrows() if "schema" in _scopes(r)]
    cat_ref = f'databricks_catalog.{cat_id}.name' if catalog else '"<catalog_name>"'
    lines += ["# ── Schema ──────────────────────────────────────────────────", f'resource "databricks_schema" "{sch_id}" {{', f'  catalog_name = {cat_ref}', f'  name         = "{_hcl_escape(schema) if schema else "<schema_name>"}"']
    if sch_rows:
        lines.append("  tags = {")
        for r in sch_rows:
            v = _vals(r)
            lines.append(f'    "{_hcl_escape(r["key"])}" = "{_hcl_escape(v[0] if v else "<value>")}"  # {r["category"]}')
        lines.append("  }")
    lines += ["}", ""]
    tbl_rows = [r for _, r in _governed_rows().iterrows() if any(s in _scopes(r) for s in ["table", "view"])]
    sch_ref = f'databricks_schema.{sch_id}.name' if schema else '"<schema_name>"'
    lines += ["# ── Table ───────────────────────────────────────────────────", f'resource "databricks_sql_table" "{tbl_id}" {{', f'  catalog_name = {cat_ref}', f'  schema_name  = {sch_ref}', f'  name         = "{_hcl_escape(table) if table else "<table_name>"}"', '  table_type   = "MANAGED"']
    if tbl_rows:
        lines.append("  tags = {")
        for r in tbl_rows:
            v = _vals(r)
            lines.append(f'    "{_hcl_escape(r["key"])}" = "{_hcl_escape(v[0] if v else "<value>")}"  # {r["category"]}')
        lines.append("  }")
    col_rows = [r for _, r in _governed_rows().iterrows() if "column" in _scopes(r)]
    if col_rows:
        lines += ["", "  # Column tags — add one column block per tagged column:"]
        for r in col_rows:
            v = _vals(r)
            lines += [
                "  # column {",
                '  #   name = "<column_name>"',
                '  #   type = "STRING"',
                f'  #   tags = {{ "{_hcl_escape(r["key"])}" = "{_hcl_escape(v[0] if v else "<value>")}" }}  # {r["category"]}',
                "  # }",
            ]
    lines += ["}"]
    return "\n".join(lines)


with st.sidebar:
    st.markdown(
        """
        <div class="db-sidebar-brand">
          <div class="db-sidebar-name">TAG STRATEGY BUILDER</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    w, conn_err = get_workspace_client()
    if conn_err:
        st.error("Not connected — preview mode")
        w = None
    else:
        try:
            me = w.current_user.me()
            st.caption(f"Connected as **{me.display_name or me.user_name}**")
        except Exception:
            st.caption("Connected")

    st.markdown("##### Target object")
    st.caption("Populates SQL, Terraform, and Apply tabs.")
    catalogs = list_catalogs(w) if w else []
    catalog_input = st.selectbox("Catalog", [""] + catalogs, key="sb_catalog") if catalogs else st.text_input("Catalog name", key="sb_catalog")
    st.session_state.target_catalog = catalog_input or ""
    schemas = list_schemas(w, catalog_input) if (w and catalog_input) else []
    schema_input = st.selectbox("Schema", [""] + schemas, key="sb_schema") if schemas else st.text_input("Schema name", key="sb_schema")
    st.session_state.target_schema = schema_input or ""
    tables = list_tables(w, catalog_input, schema_input) if (w and catalog_input and schema_input) else []
    table_input = st.selectbox("Table", [""] + tables, key="sb_table") if tables else st.text_input("Table name", key="sb_table")
    st.session_state.target_table = table_input or ""

    st.markdown("##### Completeness")
    gov_rows = st.session_state.tag_rows[st.session_state.tag_rows["type"] == "governed"]
    if len(gov_rows):
        filled = (
            (gov_rows["key"].astype(str).str.strip() != "").sum()
            + (gov_rows["values"].astype(str).str.strip() != "").sum()
            + (gov_rows[SCOPE_COLS].any(axis=1)).sum()
            + (gov_rows["owner"].astype(str).str.strip() != "").sum()
        )
        total = len(gov_rows) * 4
        pct = int((filled / total) * 100)
        st.progress(pct / 100, text=f"{pct}% complete")
    else:
        st.progress(0.0, text="No governed rows")

    if st.button("Reset to defaults", use_container_width=True):
        st.session_state.tag_rows = _with_row_ids(pd.DataFrame(DEFAULT_ROWS, columns=COLUMNS))
        st.rerun()

    st.divider()
    st.radio("Theme", ["Dark Header", "Light"], key="theme_mode", horizontal=True)


tab_help, tab_matrix, tab_sql, tab_tf, tab_apply = st.tabs([
    "How to Use",
    "Tag Matrix",
    "SQL — Apply Tags",
    "Terraform HCL",
    "Apply to Workspace",
])

with tab_help:
    st.markdown("#### What this app does")
    st.markdown(
        "The **Unity Catalog Tag Strategy Builder** helps you design a governed tagging taxonomy for Unity Catalog before rollout. "
        "You define which tag keys exist, whether they are governed or ungoverned, which scopes they apply to, who creates them, "
        "who assigns them, and how much is automated. Then the app generates SQL, Terraform HCL, or applies tags directly in the workspace."
    )
    st.markdown("---")
    st.markdown("#### How to use it")
    st.markdown(
        "1. Use the **sidebar** to pick appearance and a target catalog/schema/table.\n"
        "2. Use **Tag Matrix** to design the strategy in grouped tag cards.\n"
        "3. Use **SQL** or **Terraform** to export the strategy.\n"
        "4. Use **Apply to Workspace** to assign governed tags live."
    )
    st.markdown("---")
    st.markdown("#### Learn more — Unity Catalog tags documentation")
    st.markdown(
        "* [Apply tags to Unity Catalog securable objects](https://docs.databricks.com/aws/en/database-objects/tags/)\n"
        "* [Governed tags for data discovery](https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-discovery/)\n"
        "* [ABAC core concepts](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/core-concepts/)\n"
        "* [ABAC requirements](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/requirements/)\n"
        "* [CLI tag policy commands](https://docs.databricks.com/aws/en/dev-tools/cli/commands/)"
    )

with tab_matrix:
    st.markdown("#### Tag taxonomy")
    st.caption("Grouped tag cards reduce scanning cost while keeping the full tagging strategy editable.")

    info_col, warn_col = st.columns([3, 2])
    with info_col:
        st.info(
            "Use governed tags for centralized, policy-backed definitions. Use ungoverned tags for flexible practitioner annotations.",
        )
    with warn_col:
        missing_vals = gov_rows[gov_rows["values"].astype(str).str.strip() == ""]
        if not missing_vals.empty:
            st.warning(f"{len(missing_vals)} governed tag(s) are missing allowed values.")

    matrix_df = st.session_state.tag_rows.copy()
    total_rows = len(matrix_df)
    governed_count = int((matrix_df["type"] == "governed").sum())
    incomplete_count = int(((matrix_df["type"] == "governed") & (matrix_df.apply(_row_completion, axis=1) < 1)).sum())
    scoped_count = int(matrix_df[SCOPE_COLS].any(axis=1).sum())

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Tag rows", total_rows)
    m2.metric("Governed", governed_count)
    m3.metric("Need attention", incomplete_count)
    m4.metric("With scope", scoped_count)

    tool_left, tool_mid, tool_right = st.columns([2, 1, 1])
    with tool_left:
        matrix_search = st.text_input("Search tag rows", key="matrix_search", placeholder="Search category, key, description, or owner")
    with tool_mid:
        category_options = ["All categories"] + sorted({str(v).strip() for v in matrix_df["category"].fillna("") if str(v).strip()})
        matrix_category = st.selectbox("Category", category_options, key="matrix_category")
    with tool_right:
        matrix_type = st.selectbox("Governance", ["All", "governed", "ungoverned"], key="matrix_type")

    def _add_tag_row_callback():
        new_row = _blank_row()
        st.session_state.tag_rows = pd.concat([st.session_state.tag_rows, pd.DataFrame([new_row])], ignore_index=True)
        st.session_state._reset_matrix_filters = True
        st.session_state.expand_row_id = new_row["row_id"]

    action_left, action_right = st.columns([1, 4])
    with action_left:
        st.button("+ Add tag row", type="primary", use_container_width=True, on_click=_add_tag_row_callback)
    with action_right:
        st.caption("Open a card to edit the row, use checkboxes for scope, and duplicate rows when patterns repeat.")

    search_text = matrix_search.strip().lower()
    grouped_rows = {}
    for idx, row in matrix_df.iterrows():
        haystack = " ".join([str(row.get("category", "")), str(row.get("key", "")), str(row.get("desc", "")), str(row.get("owner", ""))]).lower()
        if search_text and search_text not in haystack:
            continue
        if matrix_category != "All categories" and str(row.get("category", "")).strip() != matrix_category:
            continue
        if matrix_type != "All" and str(row.get("type", "")).strip() != matrix_type:
            continue
        grouped_rows.setdefault(str(row.get("category", "")).strip() or "Uncategorized", []).append(idx)

    if not grouped_rows:
        st.info("No tag rows match the current filters.")
    else:
        for group_name, row_indexes in grouped_rows.items():
            st.markdown(f"**{group_name}** &nbsp;·&nbsp; {len(row_indexes)} tag(s)")
            with st.container(border=True):
                for idx in row_indexes:
                    row = matrix_df.loc[idx].copy()
                    row_id = int(row.get("row_id", idx + 1))
                    row_key = str(row.get("key", "")).strip()
                    row_title = row_key or str(row.get("category", "")).strip() or f"Tag row {idx + 1}"
                    row_completion_pct = int(_row_completion(row) * 100)
                    row_governance_label = "Governed" if row.get("type") == "governed" else "Ungoverned"
                    summary = f"{row_title} ({row_governance_label}) · {_row_scope_label(row)} · {row_completion_pct}% complete"

                    with st.expander(summary, expanded=(row_id == st.session_state.get("expand_row_id"))):
                        col_main, col_meta = st.columns([3, 2])
                        with col_main:
                            matrix_df.at[idx, "category"] = st.text_input("Category", value=str(row.get("category", "")), key=f"row_{row_id}_category")
                            matrix_df.at[idx, "desc"] = st.text_area("Description", value=str(row.get("desc", "")), key=f"row_{row_id}_desc", height=90)
                            matrix_df.at[idx, "key"] = st.text_input("Tag key (snake_case)", value=str(row.get("key", "")), key=f"row_{row_id}_key", placeholder="e.g. sensitivity_level")
                            matrix_df.at[idx, "values"] = st.text_input("Allowed values (comma-separated)", value=str(row.get("values", "")), key=f"row_{row_id}_values", placeholder="e.g. public, sensitive, confidential")
                        with col_meta:
                            type_options = ["governed", "ungoverned"]
                            current_type = str(row.get("type", "governed"))
                            matrix_df.at[idx, "type"] = st.selectbox("Governance", type_options, index=type_options.index(current_type) if current_type in type_options else 0, key=f"row_{row_id}_type")
                            current_creates = row.get("creates") if row.get("creates") in CREATE_OPTIONS else CREATE_OPTIONS[0]
                            matrix_df.at[idx, "creates"] = st.selectbox("Who creates", CREATE_OPTIONS, index=CREATE_OPTIONS.index(current_creates), key=f"row_{row_id}_creates")
                            current_assigns = row.get("assigns") if row.get("assigns") in ASSIGN_OPTIONS else ASSIGN_OPTIONS[0]
                            matrix_df.at[idx, "assigns"] = st.selectbox("Who assigns", ASSIGN_OPTIONS, index=ASSIGN_OPTIONS.index(current_assigns), key=f"row_{row_id}_assigns")
                            current_automation = row.get("automation") if row.get("automation") in AUTOMATION_OPTIONS else AUTOMATION_OPTIONS[0]
                            matrix_df.at[idx, "automation"] = st.selectbox("Automation", AUTOMATION_OPTIONS, index=AUTOMATION_OPTIONS.index(current_automation), key=f"row_{row_id}_automation")
                            matrix_df.at[idx, "owner"] = st.text_input("Owner / DRI", value=str(row.get("owner", "")), key=f"row_{row_id}_owner", placeholder="e.g. Data Governance Council")

                        st.markdown("**Scope**")
                        scope_cols = st.columns(len(SCOPE_OPTIONS))
                        for scope_name, scope_col in zip(SCOPE_OPTIONS, scope_cols):
                            with scope_col:
                                matrix_df.at[idx, f"scope_{scope_name}"] = st.checkbox(scope_name.title(), value=bool(row.get(f"scope_{scope_name}")), key=f"row_{row_id}_scope_{scope_name}")

                        updated_row = matrix_df.loc[idx]
                        st.caption(f"This row is **{int(_row_completion(updated_row) * 100)}% complete**. Current scope: {_row_scope_label(updated_row)}.")
                        preview = {
                            "category": updated_row.get("category", ""),
                            "description": updated_row.get("desc", ""),
                            "governance": updated_row.get("type", ""),
                            "tag_key": updated_row.get("key", ""),
                            "allowed_values": updated_row.get("values", ""),
                            "scope": _row_scope_label(updated_row),
                            "who_creates": updated_row.get("creates", ""),
                            "who_assigns": updated_row.get("assigns", ""),
                            "automation": updated_row.get("automation", ""),
                            "owner": updated_row.get("owner", ""),
                        }
                        st.code("\n".join(f"{k}: {v}" for k, v in preview.items()), language="yaml")

                        act1, act2, act3 = st.columns([1, 1, 4])
                        with act1:
                            if st.button("Duplicate", key=f"row_{row_id}_dup", use_container_width=True):
                                cloned = updated_row.to_dict()
                                cloned["row_id"] = st.session_state.next_row_id
                                st.session_state.next_row_id += 1
                                st.session_state.tag_rows = pd.concat([matrix_df, pd.DataFrame([cloned])], ignore_index=True)
                                st.rerun()
                        with act2:
                            if st.button("Delete", key=f"row_{row_id}_del", use_container_width=True):
                                st.session_state.tag_rows = matrix_df.drop(index=idx).reset_index(drop=True)
                                st.rerun()
                        with act3:
                            st.caption("Duplicate is useful when a new tag follows the same ownership and automation pattern.")

        st.session_state.tag_rows = matrix_df.reset_index(drop=True)

    st.markdown("---")
    st.markdown("#### Strategy notes")
    st.session_state.setdefault("strategy_notes", "")
    st.session_state.strategy_notes = st.text_area(
        "Record open questions, decisions, or rollout sequencing",
        value=st.session_state.get("strategy_notes", ""),
        placeholder="e.g. 'Govern PII and compliance first. Cost attribution deferred to Q3. Domain tags assigned by team leads.'",
        height=100,
        label_visibility="collapsed",
    )

with tab_sql:
    st.markdown("#### SQL — Apply Tags to Unity Catalog")
    st.caption("Generated from your matrix. Run in a Databricks SQL editor or notebook (`%sql`).")
    cat = st.session_state.target_catalog
    sch = st.session_state.target_schema
    tbl = st.session_state.target_table
    if not any([cat, sch, tbl]):
        st.info("Select a catalog, schema, and table in the sidebar to populate object names in the SQL.")
    sql_out = generate_sql(cat, sch, tbl)
    st.code(sql_out, language="sql")
    st.download_button("Download SQL", sql_out, file_name="tag_strategy.sql", mime="text/plain", type="primary")

with tab_tf:
    st.markdown("#### Terraform HCL — Declarative Tag Management")
    st.caption("Resource blocks for the `databricks/databricks` provider.")
    st.warning("Verify `databricks_tag` resource availability and tag arguments against your provider version before applying.")
    cat = st.session_state.target_catalog
    sch = st.session_state.target_schema
    tbl = st.session_state.target_table
    tf_out = generate_tf(cat, sch, tbl)
    st.code(tf_out, language="hcl")
    st.download_button("Download HCL", tf_out, file_name="tag_strategy.tf", mime="text/plain", type="primary")

with tab_apply:
    st.markdown("#### Apply Tags Directly to Your Workspace")
    if not w:
        st.error("No workspace connection available. Deploy this as a Databricks App for live tag application.")
    else:
        cat = st.session_state.target_catalog
        sch = st.session_state.target_schema
        tbl = st.session_state.target_table
        if not cat:
            st.info("Select a target catalog in the sidebar to apply tags.")
        else:
            st.markdown(f"**Target:** `{'.'.join(filter(None, [cat, sch, tbl]))}`")
            if tbl:
                with st.expander("View existing tags on this table", expanded=False):
                    existing = get_existing_tags(w, cat, sch, tbl)
                    if existing:
                        st.dataframe(pd.DataFrame(list(existing.items()), columns=["Tag Key", "Current Value"]), use_container_width=True, hide_index=True)
                    else:
                        st.caption("No tags currently applied to this table, or unable to fetch.")

            st.markdown("---")
            st.markdown("##### Choose tag assignments to apply")
            st.caption("Select which governed tags to apply and choose their value.")
            gov = _governed_rows()
            assignments = {}
            for _, row in gov.iterrows():
                if not row["key"]:
                    continue
                scopes = _scopes(row)
                vals = _vals(row)
                relevant = (("catalog" in scopes and cat) or ("schema" in scopes and sch) or (any(s in scopes for s in ["table", "view"]) and tbl))
                if not relevant:
                    continue
                col_key, col_val, col_scope = st.columns([2, 3, 2])
                with col_key:
                    apply = st.checkbox(f"`{row['key']}`", key=f"apply_{row['key']}")
                with col_val:
                    if vals:
                        chosen_val = st.selectbox("Value", vals, key=f"val_{row['key']}", label_visibility="collapsed")
                    else:
                        chosen_val = st.text_input("Value", key=f"val_{row['key']}", placeholder="Enter value", label_visibility="collapsed")
                with col_scope:
                    best_scope = None
                    if tbl and any(s in scopes for s in ["table", "view"]):
                        best_scope = "table"
                    elif sch and "schema" in scopes:
                        best_scope = "schema"
                    elif cat and "catalog" in scopes:
                        best_scope = "catalog"
                    st.caption(f"Applies to **{best_scope}**" if best_scope else "")
                if apply and chosen_val and best_scope:
                    assignments[row["key"]] = (chosen_val, best_scope)

            st.markdown("---")
            if assignments:
                preview_lines = []
                for key, (val, scope) in assignments.items():
                    if scope == "table":
                        obj_ref = ".".join(filter(None, [cat, sch, tbl]))
                        preview_lines.append(f"ALTER TABLE `{obj_ref}` SET TAGS ('{_sql_escape(key)}' = '{_sql_escape(val)}');")
                    elif scope == "schema":
                        obj_ref = ".".join(filter(None, [cat, sch]))
                        preview_lines.append(f"ALTER SCHEMA `{obj_ref}` SET TAGS ('{key}' = '{val}');")
                    elif scope == "catalog":
                        preview_lines.append(f"ALTER CATALOG `{cat}` SET TAGS ('{key}' = '{val}');")
                st.markdown("**Preview — SQL that will be executed:**")
                st.code("\n".join(preview_lines), language="sql")
                c1, c2 = st.columns([2, 5])
                with c1:
                    apply_btn = st.button("Apply tags now", type="primary", use_container_width=True)
                with c2:
                    st.caption("Requires `APPLY TAG` on the object plus the required Unity Catalog permissions.")
                if apply_btn:
                    wh_id = _get_warehouse_id(w)
                    if not wh_id:
                        st.error("No running SQL warehouse found. Start a warehouse first.")
                    else:
                        results = []
                        for stmt in preview_lines:
                            try:
                                w.statement_execution.execute_sync(warehouse_id=wh_id, statement=stmt)
                                results.append(("ok", stmt))
                            except Exception as e:
                                results.append(("error", f"{stmt}\n   Error: {e}"))
                        get_existing_tags.clear()
                        failures = sum(1 for status, _ in results if status == "error")
                        successes = sum(1 for status, _ in results if status == "ok")
                        if failures == 0:
                            st.success(f"Applied {successes} tag(s) successfully.")
                        else:
                            st.warning(f"Applied {successes} tag(s). {failures} failed:")
                            for status, msg in results:
                                if status == "error":
                                    st.error(msg)
            else:
                st.caption("Select at least one tag above to preview the SQL before applying.")

st.divider()
st.markdown(
    '<div class="db-footer-bar">Unity Catalog Tag Strategy Builder · Built with Streamlit + Databricks SDK · '
    'Best practices from <a href="https://docs.databricks.com" target="_blank">Databricks documentation</a></div>',
    unsafe_allow_html=True,
)

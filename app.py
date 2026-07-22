"""
Unity Catalog Tag Strategy Builder
Databricks App — Streamlit + Databricks SDK
"""

import streamlit as st
import pandas as pd
from datetime import date

SCOPE_OPTIONS = ["catalog", "schema", "table", "view", "column"]
SCOPE_COLS = [f"scope_{s}" for s in SCOPE_OPTIONS]


def _scope_flags(*active):
    return {f"scope_{s}": (s in active) for s in SCOPE_OPTIONS}

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="UC Tag Strategy Builder",
    page_icon="🏷️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Theme ──────────────────────────────────────────────────────────────────────
if "theme_mode" not in st.session_state:
    st.session_state.theme_mode = "Dark Header"
THEME = st.session_state.theme_mode

if THEME == "Light":
    _PAGE_BG = "#FFFFFF"
    _SIDEBAR_BG = "#FFFFFF"
    _SIDEBAR_TEXT = "#1B2431"
    _HEADER_BG = "#FFFFFF"
    _HEADER_TEXT = "#1B2431"
    _FOOTER_BG = "#EAECEF"
    _FOOTER_TEXT = "#1B2431"
    _CODE_BG = "#F4F4F5"
    _CODE_TEXT = "#1B2431"
else:
    _PAGE_BG = "#F9F9F9"
    _SIDEBAR_BG = "#1B2431"
    _SIDEBAR_TEXT = "#F9F9F9"
    _HEADER_BG = "#1B2431"
    _HEADER_TEXT = "#F9F9F9"
    _FOOTER_BG = "#2B3546"
    _FOOTER_TEXT = "#F9F9F9"
    _CODE_BG = "#1E293B"
    _CODE_TEXT = "#E2E8F0"

# ── Databricks branding ────────────────────────────────────────────────────────
st.markdown(f"""
<style>
  /* Page background */
  [data-testid="stAppViewContainer"] {{ background-color: {_PAGE_BG}; }}
  [data-testid="stSidebar"] {{ background-color: {_SIDEBAR_BG}; }}
  [data-testid="stSidebar"] * {{ color: {_SIDEBAR_TEXT} !important; }}

  /* Header: chrome with red accent stripe (color depends on theme) */
  [data-testid="stHeader"] {{
    background: {_HEADER_BG};
    border-bottom: 3px solid #FF3621;
  }}
  [data-testid="stHeader"] * {{
    color: {_HEADER_TEXT} !important;
    fill: {_HEADER_TEXT} !important;
  }}

  /* Primary buttons: Databricks red (same in both themes) */
  .stButton > button[kind="primary"] {{
    background-color: #FF3621 !important;
    border-color: #FF3621 !important;
    color: white !important;
  }}
  .stButton > button[kind="primary"]:hover {{
    background-color: #D42E1A !important;
    border-color: #D42E1A !important;
  }}

  /* Secondary actions and links: Databricks blue */
  .stButton > button[kind="secondary"] {{
    color: #1F6FEB !important;
    border-color: #1F6FEB !important;
  }}
  a, a:visited {{ color: #1F6FEB !important; }}

  /* Governed row highlight */
  .governed-tag {{ border-left: 3px solid #FF3621; padding-left: 8px; }}

  /* Code block style */
  .sql-block {{
    background: {_CODE_BG};
    color: {_CODE_TEXT};
    padding: 16px;
    border-radius: 6px;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 12px;
    line-height: 1.8;
    white-space: pre;
    overflow-x: auto;
  }}

  /* Footer / status bar */
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

# ── SDK connection ─────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def get_workspace_client():
    """
    Inside a Databricks App, the SDK auto-configures from the app runtime.
    Falls back gracefully if run locally without credentials.
    """
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        # Verify connection with a lightweight call
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


@st.cache_data(show_spinner=False, ttl=60)
def get_existing_tags(_w, catalog, schema, table):
    """Fetch tags already applied to an object via information_schema."""
    try:
        results = {}
        fqn = f"`{catalog}`.`{schema}`.`{table}`"
        # Table tags
        df = _w.statement_execution.execute(
            warehouse_id=_get_warehouse_id(_w),
            statement=f"SELECT tag_name, tag_value FROM `{catalog}`.information_schema.table_tags "
                      f"WHERE schema_name='{schema}' AND table_name='{table}'"
        )
        if df and df.result and df.result.data_array:
            for row in df.result.data_array:
                results[row[0]] = row[1]
        return results
    except Exception:
        return {}


def _get_warehouse_id(w):
    """Pick the first available SQL warehouse."""
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


# ── Default tag taxonomy ───────────────────────────────────────────────────────
DEFAULT_ROWS = [
    {"category": "Classification / Sensitivity", "desc": "Overall risk level. Primary signal for access control policies.",
     "type": "governed", "key": "sensitivity_level", "values": "public, sensitive, confidential, restricted",
     "creates": "Central governance", "assigns": "Stewards / service principals",
     "automation": "Audit & review candidates", "owner": "", **_scope_flags("table", "view")},
    {"category": "PII Classification", "desc": "Column-level evidence of specific personal data types.",
     "type": "governed", "key": "pii", "values": "ssn, email, phone, name, dob, address, ip_address",
     "creates": "Central governance", "assigns": "Automation / stewards",
     "automation": "Auto-detect candidates", "owner": "", **_scope_flags("column")},
    {"category": "Compliance / Regulatory", "desc": "Regulatory frameworks that apply to this asset.",
     "type": "governed", "key": "compliance", "values": "pci, hipaa, gdpr, ccpa, sox",
     "creates": "Central governance", "assigns": "Service principals / admins",
     "automation": "Manual + propagation", "owner": "", **_scope_flags("table", "schema")},
    {"category": "Domain", "desc": "Business area the asset belongs to. Powers Databricks discovery.",
     "type": "governed", "key": "domain", "values": "finance, sales, marketing, engineering, hr, product, legal",
     "creates": "Central governance", "assigns": "Practitioners / team leads",
     "automation": "Manual", "owner": "", **_scope_flags("catalog", "schema")},
    {"category": "Subdomain", "desc": "Finer-grained function within a domain for large orgs.",
     "type": "governed", "key": "subdomain", "values": "audit, tax, fp_a, demand_gen, eng_data",
     "creates": "Central governance", "assigns": "Practitioners",
     "automation": "Manual", "owner": "", **_scope_flags("schema", "table")},
    {"category": "Certification", "desc": "Signals the asset is the validated source of truth.",
     "type": "governed", "key": "certification", "values": "certified",
     "creates": "Central governance", "assigns": "Governance team only",
     "automation": "AMM surfaces candidates", "owner": "", **_scope_flags("table", "schema")},
    {"category": "Lifecycle / Deprecation", "desc": "Asset health and maintenance state for discovery quality.",
     "type": "governed", "key": "lifecycle", "values": "active, deprecated, archived",
     "creates": "Central governance", "assigns": "Governance team / owners",
     "automation": "AMM surfaces candidates", "owner": "", **_scope_flags("table", "view", "schema")},
    {"category": "Cost Attribution", "desc": "Ties assets to cost centers for chargeback reporting.",
     "type": "governed", "key": "cost_center", "values": "",
     "creates": "Central governance", "assigns": "Team leads / finance ops",
     "automation": "Manual", "owner": "", **_scope_flags("catalog", "schema")},
    {"category": "Team / Project", "desc": "Owning team or project for routing and discoverability.",
     "type": "governed", "key": "team", "values": "",
     "creates": "Central governance", "assigns": "Practitioners",
     "automation": "Manual", "owner": "", **_scope_flags("schema", "table")},
    {"category": "Free-form / Ad hoc", "desc": "Practitioner annotations, workflow flags, personal notes.",
     "type": "ungoverned", "key": "", "values": "",
     "creates": "Anyone", "assigns": "Anyone",
     "automation": "None", "owner": "", **_scope_flags("table", "column", "schema")},
]

COLUMNS = ["category", "desc", "type", "key", "values", *SCOPE_COLS, "creates", "assigns", "automation", "owner"]
COL_LABELS = {
    "category": "Category", "desc": "Description", "type": "Governance",
    "key": "Tag Key", "values": "Allowed Values",
    "scope_catalog": "Catalog", "scope_schema": "Schema", "scope_table": "Table",
    "scope_view": "View", "scope_column": "Column",
    "creates": "Who Creates", "assigns": "Who Assigns",
    "automation": "Automation", "owner": "Owner / DRI",
}

# ── Session state ──────────────────────────────────────────────────────────────
if "tag_rows" not in st.session_state:
    st.session_state.tag_rows = pd.DataFrame(DEFAULT_ROWS, columns=COLUMNS)

if "target_catalog" not in st.session_state:
    st.session_state.target_catalog = ""
if "target_schema" not in st.session_state:
    st.session_state.target_schema = ""
if "target_table" not in st.session_state:
    st.session_state.target_table = ""


# ── Header ─────────────────────────────────────────────────────────────────────
col_logo, col_title = st.columns([1, 11])
with col_logo:
    st.markdown("### 🏷️")
with col_title:
    st.markdown("## Unity Catalog · Tag Strategy Builder")
    st.caption("Design your governed tag taxonomy, then export SQL or Terraform — or apply tags directly to your workspace.")

st.divider()

# ── Sidebar: workspace connection ──────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🎨 Appearance")
    st.radio("Theme", ["Dark Header", "Light"], key="theme_mode", horizontal=True)
    st.markdown("---")

    st.markdown("### 🔌 Workspace")
    w, conn_err = get_workspace_client()

    if conn_err:
        st.error(f"Not connected — running in preview mode.\n\n`{conn_err}`")
        w = None
    else:
        try:
            me = w.current_user.me()
            st.success(f"Connected as **{me.display_name or me.user_name}**")
        except Exception:
            st.warning("Connected (user info unavailable)")

    st.markdown("---")
    st.markdown("### 🎯 Target Object")
    st.caption("Used to populate SQL and Terraform exports, and to apply tags directly.")

    catalogs = list_catalogs(w) if w else []
    catalog_input = st.selectbox("Catalog", [""] + catalogs, key="sb_catalog") if catalogs else st.text_input("Catalog name", key="sb_catalog")
    st.session_state.target_catalog = catalog_input or ""

    schemas = list_schemas(w, catalog_input) if (w and catalog_input) else []
    schema_input = st.selectbox("Schema", [""] + schemas, key="sb_schema") if schemas else st.text_input("Schema name", key="sb_schema")
    st.session_state.target_schema = schema_input or ""

    tables = list_tables(w, catalog_input, schema_input) if (w and catalog_input and schema_input) else []
    table_input = st.selectbox("Table", [""] + tables, key="sb_table") if tables else st.text_input("Table name", key="sb_table")
    st.session_state.target_table = table_input or ""

    st.markdown("---")
    st.markdown("### 📊 Strategy completeness")
    gov_rows = st.session_state.tag_rows[st.session_state.tag_rows["type"] == "governed"]
    if len(gov_rows):
        filled = (
            (gov_rows["key"] != "").sum() +
            (gov_rows["values"] != "").sum() +
            (gov_rows[SCOPE_COLS].any(axis=1)).sum() +
            (gov_rows["owner"] != "").sum()
        )
        total = len(gov_rows) * 4
        pct = int((filled / total) * 100)
        st.progress(pct / 100, text=f"{pct}% complete")
    else:
        st.progress(0.0, text="No governed rows")

    if st.button("↺ Reset to defaults", use_container_width=True):
        st.session_state.tag_rows = pd.DataFrame(DEFAULT_ROWS, columns=COLUMNS)
        st.rerun()


# ── Main tabs ──────────────────────────────────────────────────────────────────
tab_matrix, tab_sql, tab_tf, tab_apply = st.tabs([
    "📋 Tag Matrix",
    "⚡ SQL — Apply Tags",
    "🏗 Terraform HCL",
    "🚀 Apply to Workspace",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Tag Matrix
# ══════════════════════════════════════════════════════════════════════════════
with tab_matrix:
    st.markdown("#### Tag taxonomy")
    st.caption(
        "Edit keys, define allowed values, and set scope + ownership. "
        "Governed rows (with a defined key) drive the SQL and Terraform exports."
    )

    col_info, col_warn = st.columns([3, 2])
    with col_info:
        st.info(
            "**Governed tags** must be created via Catalog Explorer or the REST API before assignment. "
            "**Ungoverned** tags can be assigned freely — no pre-definition needed.",
            icon="💡"
        )
    with col_warn:
        missing_vals = gov_rows[gov_rows["values"] == ""]
        if not missing_vals.empty:
            st.warning(
                f"⚠ {len(missing_vals)} governed tag(s) have no allowed values defined: "
                f"{', '.join(missing_vals['key'].tolist())}",
                icon="⚠️"
            )

    # Editable dataframe
    edited = st.data_editor(
        st.session_state.tag_rows,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "category":   st.column_config.TextColumn("Category", width="medium"),
            "desc":       st.column_config.TextColumn("Description", width="large"),
            "type":       st.column_config.SelectboxColumn("Governance", options=["governed", "ungoverned"], width="small"),
            "key":        st.column_config.TextColumn("Tag Key (snake_case)", width="medium"),
            "values":     st.column_config.TextColumn("Allowed Values (comma-sep)", width="large"),
            "scope_catalog": st.column_config.CheckboxColumn("Catalog", width="small"),
            "scope_schema":  st.column_config.CheckboxColumn("Schema", width="small"),
            "scope_table":   st.column_config.CheckboxColumn("Table", width="small"),
            "scope_view":    st.column_config.CheckboxColumn("View", width="small"),
            "scope_column":  st.column_config.CheckboxColumn("Column", width="small"),
            "creates":    st.column_config.SelectboxColumn("Who Creates",
                            options=["Central governance", "Domain leads", "Team leads", "Anyone"], width="medium"),
            "assigns":    st.column_config.SelectboxColumn("Who Assigns",
                            options=["Governance team only", "Service principals / admins",
                                     "Stewards / service principals", "Automation / stewards",
                                     "Governance team / owners", "Team leads / finance ops",
                                     "Practitioners / team leads", "Practitioners", "Anyone"],
                            width="medium"),
            "automation": st.column_config.SelectboxColumn("Automation",
                            options=["None", "Manual", "Manual + propagation",
                                     "Audit & review candidates", "AMM surfaces candidates",
                                     "Auto-detect candidates", "Auto-assign (no review)", "Propagation only"],
                            width="medium"),
            "owner":      st.column_config.TextColumn("Owner / DRI", width="medium"),
        },
        hide_index=True,
        key="matrix_editor",
    )
    st.session_state.tag_rows = edited

    st.markdown("---")
    st.markdown("#### Preview — key:value pairs per row")
    st.caption("Each row's fields reconstructed as key:value pairs, reflecting the latest matrix edits.")
    for i, row in st.session_state.tag_rows.reset_index(drop=True).iterrows():
        label = row.get("key") or row.get("category") or f"Row {i + 1}"
        with st.expander(f"`{label}`" if row.get("key") else str(label)):
            scope_str = ", ".join(s for s in SCOPE_OPTIONS if row.get(f"scope_{s}")) or "—"
            preview = {
                "category": row.get("category", ""),
                "description": row.get("desc", ""),
                "governance": row.get("type", ""),
                "tag_key": row.get("key", ""),
                "allowed_values": row.get("values", ""),
                "scope": scope_str,
                "who_creates": row.get("creates", ""),
                "who_assigns": row.get("assigns", ""),
                "automation": row.get("automation", ""),
                "owner": row.get("owner", ""),
            }
            st.code("\n".join(f"{k}: {v}" for k, v in preview.items()), language="yaml")

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


# ══════════════════════════════════════════════════════════════════════════════
# SQL helpers
# ══════════════════════════════════════════════════════════════════════════════
def _governed_rows():
    df = st.session_state.tag_rows
    return df[(df["type"] == "governed") & (df["key"].str.strip() != "")]


def _vals(row):
    return [v.strip() for v in str(row.get("values", "")).split(",") if v.strip()]


def _scopes(row):
    return [s for s in SCOPE_OPTIONS if row.get(f"scope_{s}")]


def generate_sql(catalog="", schema="", table=""):
    cat = catalog or "<catalog>"
    sch = f"{catalog + '.' if catalog else ''}{schema or '<schema>'}"
    tbl = ".".join(filter(None, [catalog, schema, table])) or "<catalog.schema.table>"
    lines = []
    today = date.today().strftime("%B %d, %Y")

    lines += [
        f"-- ════════════════════════════════════════════════════════════",
        f"-- Unity Catalog · Tag Strategy Implementation",
        f"-- Generated: {today}",
        f"-- Requires: DBR 13.3+ (ALTER SET TAGS) or DBR 16.1+ (SET TAG ON)",
        f"-- ════════════════════════════════════════════════════════════",
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

    # Catalog tags
    cat_rows = [r for _, r in _governed_rows().iterrows() if "catalog" in _scopes(r)]
    if cat_rows:
        lines += [f"-- ── STEP 2: Apply catalog-level tags ──────────────────────",
                  f"ALTER CATALOG {cat}"]
        tag_parts = []
        for r in cat_rows:
            v = _vals(r)
            tag_parts.append(f"  '{r['key']}' = '{v[0] if v else '<value>'}'  -- {r['category']}")
        lines += [f"SET TAGS (\n" + ",\n".join(tag_parts) + "\n);", ""]

    # Schema tags
    sch_rows = [r for _, r in _governed_rows().iterrows() if "schema" in _scopes(r)]
    if sch_rows:
        lines += [f"-- ── STEP 3: Apply schema-level tags ───────────────────────",
                  f"ALTER SCHEMA {sch}"]
        tag_parts = []
        for r in sch_rows:
            v = _vals(r)
            tag_parts.append(f"  '{r['key']}' = '{v[0] if v else '<value>'}'  -- {r['category']}")
        lines += [f"SET TAGS (\n" + ",\n".join(tag_parts) + "\n);", ""]

    # Table tags
    tbl_rows = [r for _, r in _governed_rows().iterrows() if any(s in _scopes(r) for s in ["table", "view"])]
    if tbl_rows:
        lines += [f"-- ── STEP 4: Apply table/view-level tags ───────────────────",
                  f"ALTER TABLE {tbl}"]
        tag_parts = []
        for r in tbl_rows:
            v = _vals(r)
            tag_parts.append(f"  '{r['key']}' = '{v[0] if v else '<value>'}'  -- {r['category']}")
        lines += [f"SET TAGS (\n" + ",\n".join(tag_parts) + "\n);", ""]

    # Column tags
    col_rows = [r for _, r in _governed_rows().iterrows() if "column" in _scopes(r)]
    if col_rows:
        lines += [
            "-- ── STEP 5: Apply column-level tags ───────────────────────",
            "-- ⚠ Each column requires its own ALTER TABLE statement.",
            "-- You cannot tag multiple columns in a single command.",
            "-- Max 50 tags per column; max 1,000 column tags per table.",
            "",
        ]
        for r in col_rows:
            v = _vals(r)
            lines += [
                f"-- {r['category']}: repeat for each column carrying this tag.",
                f"ALTER TABLE {tbl}",
                f"ALTER COLUMN <column_name>",
                f"SET TAGS (",
                f"  '{r['key']}' = '{v[0] if v else '<value>'}'  -- replace with correct value",
                f");",
                "",
            ]

    # Verify
    cat_name = catalog or "<catalog>"
    lines += [
        "-- ── STEP 6: Verify tags were applied ──────────────────────",
        f"SELECT tag_name, tag_value, table_schema, table_name",
        f"FROM   `{cat_name}`.information_schema.table_tags",
        f"ORDER BY table_schema, table_name;",
        "",
        f"SELECT tag_name, tag_value, table_name, column_name",
        f"FROM   `{cat_name}`.information_schema.column_tags",
        f"ORDER BY table_name, column_name;",
        "",
        f"SELECT tag_name, tag_value, schema_name",
        f"FROM   `{cat_name}`.information_schema.schema_tags",
        f"ORDER BY schema_name;",
        "",
        "-- ════════════════════════════════════════════════════════════",
        "-- To unset: ALTER TABLE <tbl> UNSET TAGS ('key');",
        "-- To drop a tagged column, UNSET governed tags first.",
        "-- Bulk apply: use the Databricks SDK or Unity Catalog REST API.",
        "-- ════════════════════════════════════════════════════════════",
    ]
    return "\n".join(lines)


def generate_tf(catalog="", schema="", table=""):
    def tf_id(s): return "".join(c if c.isalnum() else "_" for c in s).lower() or "resource"
    cat_id = tf_id(catalog) if catalog else "my_catalog"
    sch_id = tf_id(schema)  if schema  else "my_schema"
    tbl_id = tf_id(table)   if table   else "my_table"
    today = date.today().strftime("%B %d, %Y")

    lines = [
        f"# ═══════════════════════════════════════════════════════════",
        f"# Unity Catalog · Tag Strategy — Terraform HCL",
        f"# Generated: {today}",
        f"# Provider: hashicorp/databricks >= 1.38.0",
        f"# Verify databricks_tag resource availability in your provider version.",
        f"# ═══════════════════════════════════════════════════════════",
        "",
        "# ── Provider ────────────────────────────────────────────────",
        'terraform {',
        '  required_providers {',
        '    databricks = {',
        '      source  = "databricks/databricks"',
        '      version = ">= 1.38.0"',
        '    }',
        '  }',
        '}',
        "",
        'provider "databricks" {',
        '  # host  = var.databricks_host',
        '  # token = var.databricks_token',
        '}',
        "",
        "# ── Governed tag definitions ─────────────────────────────────",
        "# NOTE: Verify databricks_tag resource exists in your provider version.",
        "# If not available, create governed tags via Catalog Explorer or REST API.",
        "",
    ]

    for _, row in _governed_rows().iterrows():
        vals = _vals(row)
        rid = tf_id(row["key"])
        lines += [
            f'# {row["category"]} — {row.get("desc", "")}',
            f'resource "databricks_tag" "{rid}" {{',
            f'  name = "{row["key"]}"',
        ]
        if vals:
            quoted = ", ".join(f'"{v}"' for v in vals)
            lines.append(f'  allowed_values = [{quoted}]')
        if row.get("owner"):
            lines.append(f'  # Owner / DRI: {row["owner"]}')
        lines += ["}", ""]

    # Catalog
    cat_rows = [r for _, r in _governed_rows().iterrows() if "catalog" in _scopes(r)]
    lines += [
        "# ── Catalog ─────────────────────────────────────────────────",
        f'resource "databricks_catalog" "{cat_id}" {{',
        f'  name    = "{catalog or "<catalog_name>"}"',
        f'  comment = "<optional description>"',
    ]
    if cat_rows:
        lines.append("  tags = {")
        for r in cat_rows:
            v = _vals(r)
            lines.append(f'    {r["key"]} = "{v[0] if v else "<value>"}"  # {r["category"]}')
        lines.append("  }")
    lines += ["}", ""]

    # Schema
    sch_rows = [r for _, r in _governed_rows().iterrows() if "schema" in _scopes(r)]
    cat_ref = f'databricks_catalog.{cat_id}.name' if catalog else '"<catalog_name>"'
    lines += [
        "# ── Schema ──────────────────────────────────────────────────",
        f'resource "databricks_schema" "{sch_id}" {{',
        f'  catalog_name = {cat_ref}',
        f'  name         = "{schema or "<schema_name>"}"',
    ]
    if sch_rows:
        lines.append("  tags = {")
        for r in sch_rows:
            v = _vals(r)
            lines.append(f'    {r["key"]} = "{v[0] if v else "<value>"}"  # {r["category"]}')
        lines.append("  }")
    lines += ["}", ""]

    # Table
    tbl_rows = [r for _, r in _governed_rows().iterrows() if any(s in _scopes(r) for s in ["table","view"])]
    sch_ref = f'databricks_schema.{sch_id}.name' if schema else '"<schema_name>"'
    lines += [
        "# ── Table ───────────────────────────────────────────────────",
        f'resource "databricks_sql_table" "{tbl_id}" {{',
        f'  catalog_name = {cat_ref}',
        f'  schema_name  = {sch_ref}',
        f'  name         = "{table or "<table_name>"}"',
        f'  table_type   = "MANAGED"  # or EXTERNAL',
    ]
    if tbl_rows:
        lines.append("  tags = {")
        for r in tbl_rows:
            v = _vals(r)
            lines.append(f'    {r["key"]} = "{v[0] if v else "<value>"}"  # {r["category"]}')
        lines.append("  }")

    # Column tag examples as comments
    col_rows = [r for _, r in _governed_rows().iterrows() if "column" in _scopes(r)]
    if col_rows:
        lines += ["", "  # Column tags — add one column block per tagged column:"]
        for r in col_rows:
            v = _vals(r)
            lines += [
                "  # column {",
                "  #   name = \"<column_name>\"",
                "  #   type = \"STRING\"",
                f'  #   tags = {{ {r["key"]} = "{v[0] if v else "<value>"}" }}  # {r["category"]}',
                "  # }",
            ]
    lines += ["}", ""]

    # Variables
    lines += [
        "# ── Variables ───────────────────────────────────────────────",
        'variable "databricks_host" {',
        '  description = "Databricks workspace URL"',
        '  type        = string',
        '}',
        "",
        'variable "databricks_token" {',
        '  description = "Databricks personal access token"',
        '  type        = string',
        '  sensitive   = true',
        '}',
        "",
        "# ═══════════════════════════════════════════════════════════",
        "# Workflow: terraform init → terraform plan → terraform apply",
        "# Verify column tag support against your provider changelog.",
        "# ═══════════════════════════════════════════════════════════",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — SQL
# ══════════════════════════════════════════════════════════════════════════════
with tab_sql:
    st.markdown("#### SQL — Apply Tags to Unity Catalog")
    st.caption(
        "Generated from your matrix. Run in a Databricks SQL editor or notebook (`%sql`). "
        "Requires DBR 13.3+ for `ALTER ... SET TAGS`."
    )

    cat = st.session_state.target_catalog
    sch = st.session_state.target_schema
    tbl = st.session_state.target_table

    if not any([cat, sch, tbl]):
        st.info("Select a catalog, schema, and table in the sidebar to populate object names in the SQL.", icon="👈")

    sql_out = generate_sql(cat, sch, tbl)
    st.code(sql_out, language="sql")
    st.download_button("⬇ Download SQL", sql_out, file_name="tag_strategy.sql", mime="text/plain", type="primary")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Terraform
# ══════════════════════════════════════════════════════════════════════════════
with tab_tf:
    st.markdown("#### Terraform HCL — Declarative Tag Management")
    st.caption(
        "Resource blocks for the `databricks/databricks` provider. "
        "Terraform tracks drift — if a tag is removed manually, `terraform plan` will catch it."
    )

    st.warning(
        "**Verify before use:** `databricks_tag` resource availability and the `tags` argument schema "
        "should be confirmed against your provider version before running `terraform apply`.",
        icon="⚠️"
    )

    cat = st.session_state.target_catalog
    sch = st.session_state.target_schema
    tbl = st.session_state.target_table

    tf_out = generate_tf(cat, sch, tbl)
    st.code(tf_out, language="hcl")
    st.download_button("⬇ Download HCL", tf_out, file_name="tag_strategy.tf", mime="text/plain", type="primary")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Apply to Workspace
# ══════════════════════════════════════════════════════════════════════════════
with tab_apply:
    st.markdown("#### Apply Tags Directly to Your Workspace")

    if not w:
        st.error(
            "No workspace connection available. "
            "Deploy this as a Databricks App for live tag application.",
            icon="🔌"
        )
    else:
        cat = st.session_state.target_catalog
        sch = st.session_state.target_schema
        tbl = st.session_state.target_table

        if not cat:
            st.info("Select a target catalog in the sidebar to apply tags.", icon="👈")
        else:
            st.markdown(f"**Target:** `{'.'.join(filter(None, [cat, sch, tbl]))}`")

            # Show existing tags if table is selected
            if tbl:
                with st.expander("🔍 View existing tags on this table", expanded=False):
                    existing = get_existing_tags(w, cat, sch, tbl)
                    if existing:
                        st.dataframe(
                            pd.DataFrame(list(existing.items()), columns=["Tag Key", "Current Value"]),
                            use_container_width=True,
                            hide_index=True,
                        )
                    else:
                        st.caption("No tags currently applied to this table, or unable to fetch.")

            st.markdown("---")
            st.markdown("##### Choose tag assignments to apply")
            st.caption(
                "Select which governed tags to apply and choose their value. "
                "This executes `ALTER ... SET TAGS` against your workspace."
            )

            gov = _governed_rows()
            assignments = {}

            for _, row in gov.iterrows():
                if not row["key"]:
                    continue
                scopes = _scopes(row)
                vals = _vals(row)

                # Only show if a relevant scope is selected
                relevant = (
                    ("catalog" in scopes and cat) or
                    ("schema" in scopes and sch) or
                    (any(s in scopes for s in ["table", "view"]) and tbl)
                )
                if not relevant:
                    continue

                col_key, col_val, col_scope = st.columns([2, 3, 2])
                with col_key:
                    apply = st.checkbox(f"`{row['key']}`", key=f"apply_{row['key']}")
                with col_val:
                    if vals:
                        chosen_val = st.selectbox(
                            "Value", vals, key=f"val_{row['key']}",
                            label_visibility="collapsed"
                        )
                    else:
                        chosen_val = st.text_input(
                            "Value", key=f"val_{row['key']}",
                            placeholder="Enter value",
                            label_visibility="collapsed"
                        )
                with col_scope:
                    # Determine best scope to apply to based on what's selected
                    best_scope = None
                    if tbl and any(s in scopes for s in ["table", "view"]):
                        best_scope = "table"
                    elif sch and "schema" in scopes:
                        best_scope = "schema"
                    elif cat and "catalog" in scopes:
                        best_scope = "catalog"
                    st.caption(f"→ will apply to **{best_scope}**" if best_scope else "")

                if apply and chosen_val and best_scope:
                    assignments[row["key"]] = (chosen_val, best_scope)

            st.markdown("---")

            # Preview SQL before applying
            if assignments:
                preview_lines = []
                for key, (val, scope) in assignments.items():
                    if scope == "table":
                        obj_ref = ".".join(filter(None, [cat, sch, tbl]))
                        preview_lines.append(f"ALTER TABLE `{obj_ref}` SET TAGS ('{key}' = '{val}');")
                    elif scope == "schema":
                        obj_ref = ".".join(filter(None, [cat, sch]))
                        preview_lines.append(f"ALTER SCHEMA `{obj_ref}` SET TAGS ('{key}' = '{val}');")
                    elif scope == "catalog":
                        preview_lines.append(f"ALTER CATALOG `{cat}` SET TAGS ('{key}' = '{val}');")

                st.markdown("**Preview — SQL that will be executed:**")
                st.code("\n".join(preview_lines), language="sql")

                col_apply, col_note = st.columns([2, 5])
                with col_apply:
                    apply_btn = st.button("🚀 Apply tags now", type="primary", use_container_width=True)
                with col_note:
                    st.caption(
                        "Requires `APPLY TAG` on the object + `USE SCHEMA` + `USE CATALOG`. "
                        "For governed tags, also requires `ASSIGN` permission on the tag."
                    )

                if apply_btn:
                    wh_id = _get_warehouse_id(w)
                    if not wh_id:
                        st.error("No running SQL warehouse found. Start a warehouse in your workspace first.")
                    else:
                        results = []
                        for stmt in preview_lines:
                            try:
                                w.statement_execution.execute_sync(
                                    warehouse_id=wh_id,
                                    statement=stmt,
                                )
                                results.append(("✅", stmt))
                            except Exception as e:
                                results.append(("❌", f"{stmt}\n   Error: {e}"))

                        # Invalidate tag cache
                        get_existing_tags.clear()

                        successes = sum(1 for r in results if r[0] == "✅")
                        failures  = sum(1 for r in results if r[0] == "❌")

                        if failures == 0:
                            st.success(f"✅ Applied {successes} tag(s) successfully.")
                        else:
                            st.warning(f"Applied {successes} tag(s). {failures} failed:")
                            for icon, msg in results:
                                if icon == "❌":
                                    st.error(msg)
            else:
                st.caption("Select at least one tag above to preview the SQL before applying.")

# ── Footer ─────────────────────────────────────────────────────────────────────
st.divider()
st.markdown(
    '<div class="db-footer-bar">Unity Catalog Tag Strategy Builder · Built with Streamlit + Databricks SDK · '
    'Best practices from <a href="https://docs.databricks.com" target="_blank">Databricks documentation</a></div>',
    unsafe_allow_html=True,
)

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
    _FOOTER_BG = "#EAECEF"
    _FOOTER_TEXT = "#1B2431"
    _CODE_BG = "#F4F4F5"
    _CODE_TEXT = "#1B2431"
else:
    _PAGE_BG = "#F9F9F9"
    _SIDEBAR_BG = "#1B2431"
    _SIDEBAR_TEXT = "#F9F9F9"
    _HEADER_BG = "#1B2431"
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
CREATE_OPTIONS = ["Central governance", "Domain leads", "Team leads", "Anyone"]
ASSIGN_OPTIONS = [
    "Governance team only", "Service principals / admins", "Stewards / service principals",
    "Automation / stewards", "Governance team / owners", "Team leads / finance ops",
    "Practitioners / team leads", "Practitioners", "Anyone",
]
AUTOMATION_OPTIONS = [
    "None", "Manual", "Manual + propagation", "Audit & review candidates",
    "AMM surfaces candidates", "Auto-detect candidates", "Auto-assign (no review)",
    "Propagation only",
]


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


# ── Session state ──────────────────────────────────────────────────────────────
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
        st.session_state.tag_rows = _with_row_ids(pd.DataFrame(DEFAULT_ROWS, columns=COLUMNS))
        st.rerun()


# ── Main tabs ──────────────────────────────────────────────────────────────────
tab_help, tab_matrix, tab_sql, tab_tf, tab_apply = st.tabs([
    "📘 How to Use",
    "📋 Tag Matrix",
    "⚡ SQL — Apply Tags",
    "🏗 Terraform HCL",
    "🚀 Apply to Workspace",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 0 — How to Use
# ══════════════════════════════════════════════════════════════════════════════
with tab_help:
    st.markdown("#### What this app does")
    st.markdown(
        "The **Unity Catalog Tag Strategy Builder** helps you design a governed tagging "
        "taxonomy for Unity Catalog before you roll it out. Instead of tagging objects ad hoc, "
        "you define — up front — which tag keys exist, whether they're **governed** (centrally "
        "controlled, enforced allowed values) or **ungoverned** (free-form), which object "
        "**scopes** they apply to (catalog / schema / table / view / column), who is allowed to "
        "create and assign them, and how much of the process is automated. Once the taxonomy is "
        "defined, the app generates ready-to-run **SQL**, exportable **Terraform HCL**, or can "
        "**apply tags directly** to a real catalog, schema, or table in this workspace."
    )

    st.markdown("---")
    st.markdown("#### How to use it")
    st.markdown(
        "1. **🎨 Appearance (sidebar)** — switch between the *Dark Header* and *Light* look. "
        "Purely cosmetic; the taxonomy behaves identically in either mode.\n"
        "2. **🔌 Workspace / 🎯 Target Object (sidebar)** — pick the catalog, schema, and table "
        "you're designing for. These selections populate the generated SQL, Terraform, and the "
        "Apply tab. The **Strategy completeness** meter tracks how many governed rows have a "
        "key, allowed values, scope, and owner filled in.\n"
        "3. **📋 Tag Matrix** — review the taxonomy as grouped, expandable tag cards instead of "
        "one wide spreadsheet. Filter the list, open a tag row to edit it, tick the scope "
        "checkboxes (catalog/schema/table/view/column), and pick *Who Creates*, *Who Assigns*, "
        "and *Automation* from their dropdowns. Each row includes its own live key:value preview, "
        "plus duplicate/delete actions. Use **↺ Reset to defaults** (sidebar) to start over from "
        "the built-in taxonomy.\n"
        "4. **⚡ SQL — Apply Tags** — copy or download a full `ALTER ... SET TAGS` script scoped "
        "to your target object, plus verification queries against `information_schema`.\n"
        "5. **🏗 Terraform HCL** — copy or download equivalent `databricks_catalog` / "
        "`databricks_schema` / `databricks_sql_table` resource blocks with `tags = {...}` "
        "populated from your matrix, for teams that manage Unity Catalog declaratively.\n"
        "6. **🚀 Apply to Workspace** — for a connected workspace, select governed tags whose "
        "scope matches your target object, choose a value, preview the exact SQL, and apply it "
        "live via a running SQL warehouse."
    )

    st.markdown("---")
    st.markdown("#### Learn more — Unity Catalog tags documentation")
    st.markdown(
        "* [Apply tags to Unity Catalog securable objects](https://docs.databricks.com/aws/en/database-objects/tags/) "
        "— governed vs. ungoverned tags, SQL syntax (`SET TAGS` / `SET TAG ON`), system tags, and inheritance rules.\n"
        "* [Governed tags for data discovery](https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-discovery/) "
        "— using tags to make data browsable, filterable, and to flag certified or deprecated assets.\n"
        "* [Attribute-based access control (ABAC) core concepts](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/core-concepts/) "
        "— how governed tags power row filter, column mask, and GRANT policies.\n"
        "* [ABAC requirements, quotas, and limitations](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/requirements/) "
        "— constraints to check before designing tag-driven access policies.\n"
        "* [Databricks CLI tag-policies commands](https://docs.databricks.com/aws/en/dev-tools/cli/commands/) "
        "— create/update/delete governed tag policies from the CLI once your taxonomy is finalized."
    )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Tag Matrix
# ══════════════════════════════════════════════════════════════════════════════
with tab_matrix:
    st.markdown("#### Tag taxonomy")
    st.caption(
        "Edit keys, scope, ownership, and operating model in grouped tag cards. "
        "Governed rows with defined keys drive the SQL and Terraform exports."
    )

    col_info, col_warn = st.columns([3, 2])
    with col_info:
        st.info(
            "**Governed tags** should be standardized and centrally controlled. "
            "**Ungoverned tags** are flexible for practitioner-defined annotations.",
            icon="💡"
        )
    with col_warn:
        missing_vals = gov_rows[gov_rows["values"] == ""]
        if not missing_vals.empty:
            st.warning(
                f"{len(missing_vals)} governed tag(s) are missing allowed values.",
                icon="⚠️"
            )

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
        matrix_search = st.text_input(
            "Search tag rows",
            value=st.session_state.get("matrix_search", ""),
            key="matrix_search",
            placeholder="Search category, key, description, or owner",
        )
    with tool_mid:
        category_options = ["All categories"] + sorted({str(v).strip() for v in matrix_df["category"].fillna("") if str(v).strip()})
        matrix_category = st.selectbox("Category", category_options, key="matrix_category")
    with tool_right:
        matrix_type = st.selectbox("Governance", ["All", "governed", "ungoverned"], key="matrix_type")

    action_left, action_right = st.columns([1, 4])
    with action_left:
        if st.button("+ Add tag row", type="primary", use_container_width=True):
            st.session_state.tag_rows = pd.concat(
                [st.session_state.tag_rows, pd.DataFrame([_blank_row()])],
                ignore_index=True,
            )
            st.rerun()
    with action_right:
        st.caption("Open a tag card to edit the details. Grouping and filters keep the strategy readable without hiding advanced controls.")

    search_text = matrix_search.strip().lower()
    grouped_rows = {}
    for idx, row in matrix_df.iterrows():
        haystack = " ".join([
            str(row.get("category", "")),
            str(row.get("key", "")),
            str(row.get("desc", "")),
            str(row.get("owner", "")),
        ]).lower()
        if search_text and search_text not in haystack:
            continue
        if matrix_category != "All categories" and str(row.get("category", "")).strip() != matrix_category:
            continue
        if matrix_type != "All" and str(row.get("type", "")).strip() != matrix_type:
            continue
        group_name = str(row.get("category", "")).strip() or "Uncategorized"
        grouped_rows.setdefault(group_name, []).append(idx)

    if not grouped_rows:
        st.info("No tag rows match the current filters.")
    else:
        for group_name, row_indexes in grouped_rows.items():
            with st.expander(f"{group_name} ({len(row_indexes)})", expanded=True):
                for idx in row_indexes:
                    row = matrix_df.loc[idx].copy()
                    row_id = int(row.get("row_id", idx + 1))
                    row_key = str(row.get("key", "")).strip()
                    row_title = row_key or str(row.get("category", "")).strip() or f"Tag row {idx + 1}"
                    row_scope = _row_scope_label(row)
                    row_completion_pct = int(_row_completion(row) * 100)
                    row_marker = "🔒" if row.get("type") == "governed" else "✏️"
                    summary = f"{row_marker} {row_title} · {row_scope} · {row_completion_pct}% complete"

                    with st.expander(summary, expanded=(not row_key or row_completion_pct < 100)):
                        col_main, col_meta = st.columns([3, 2])
                        with col_main:
                            matrix_df.at[idx, "category"] = st.text_input(
                                "Category",
                                value=str(row.get("category", "")),
                                key=f"row_{row_id}_category",
                            )
                            matrix_df.at[idx, "desc"] = st.text_area(
                                "Description",
                                value=str(row.get("desc", "")),
                                key=f"row_{row_id}_desc",
                                height=90,
                            )
                            matrix_df.at[idx, "key"] = st.text_input(
                                "Tag key (snake_case)",
                                value=str(row.get("key", "")),
                                key=f"row_{row_id}_key",
                                placeholder="e.g. sensitivity_level",
                            )
                            matrix_df.at[idx, "values"] = st.text_input(
                                "Allowed values (comma-separated)",
                                value=str(row.get("values", "")),
                                key=f"row_{row_id}_values",
                                placeholder="e.g. public, sensitive, confidential",
                            )
                        with col_meta:
                            matrix_df.at[idx, "type"] = st.selectbox(
                                "Governance",
                                ["governed", "ungoverned"],
                                index=["governed", "ungoverned"].index(str(row.get("type", "governed"))),
                                key=f"row_{row_id}_type",
                            )
                            matrix_df.at[idx, "creates"] = st.selectbox(
                                "Who creates",
                                CREATE_OPTIONS,
                                index=CREATE_OPTIONS.index(row.get("creates")) if row.get("creates") in CREATE_OPTIONS else 0,
                                key=f"row_{row_id}_creates",
                            )
                            matrix_df.at[idx, "assigns"] = st.selectbox(
                                "Who assigns",
                                ASSIGN_OPTIONS,
                                index=ASSIGN_OPTIONS.index(row.get("assigns")) if row.get("assigns") in ASSIGN_OPTIONS else 0,
                                key=f"row_{row_id}_assigns",
                            )
                            matrix_df.at[idx, "automation"] = st.selectbox(
                                "Automation",
                                AUTOMATION_OPTIONS,
                                index=AUTOMATION_OPTIONS.index(row.get("automation")) if row.get("automation") in AUTOMATION_OPTIONS else 0,
                                key=f"row_{row_id}_automation",
                            )
                            matrix_df.at[idx, "owner"] = st.text_input(
                                "Owner / DRI",
                                value=str(row.get("owner", "")),
                                key=f"row_{row_id}_owner",
                                placeholder="e.g. Data Governance Council",
                            )

                        st.markdown("**Scope**")
                        scope_cols = st.columns(len(SCOPE_OPTIONS))
                        for scope_name, scope_col in zip(SCOPE_OPTIONS, scope_cols):
                            with scope_col:
                                matrix_df.at[idx, f"scope_{scope_name}"] = st.checkbox(
                                    scope_name.title(),
                                    value=bool(row.get(f"scope_{scope_name}")),
                                    key=f"row_{row_id}_scope_{scope_name}",
                                )

                        updated_row = matrix_df.loc[idx]
                        st.caption(
                            f"This row is **{int(_row_completion(updated_row) * 100)}% complete**. "
                            f"Current scope: {_row_scope_label(updated_row)}."
                        )
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

                        row_action_left, row_action_mid, row_action_right = st.columns([1, 1, 4])
                        with row_action_left:
                            if st.button("Duplicate", key=f"row_{row_id}_duplicate", use_container_width=True):
                                cloned = updated_row.to_dict()
                                cloned["row_id"] = st.session_state.next_row_id
                                st.session_state.next_row_id += 1
                                st.session_state.tag_rows = pd.concat(
                                    [matrix_df, pd.DataFrame([cloned])],
                                    ignore_index=True,
                                )
                                st.rerun()
                        with row_action_mid:
                            if st.button("Delete", key=f"row_{row_id}_delete", use_container_width=True):
                                st.session_state.tag_rows = matrix_df.drop(index=idx).reset_index(drop=True)
                                st.rerun()
                        with row_action_right:
                            st.caption("Use duplicate when a tag pattern is similar and only the key, values, or scope needs to change.")

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

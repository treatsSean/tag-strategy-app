"""
Unity Catalog Tag Strategy Builder
Databricks App — Streamlit + Databricks SDK
"""

import json
import io
import pandas as pd
import streamlit as st
from datetime import date

from tag_engine import (
    validate_taxonomy,
    recommend_for_rows,
    build_apply_plan,
    classify_tag_domain,
    build_import_plan,
    compute_required_keys,
    analyze_object_coverage,
    audit_value_drift,
    SENSITIVE_COLUMN_NAME_HINTS,
    build_taxonomy_messages,
    parse_taxonomy_response,
    default_taxonomy_suggestion,
    suggest_abac_candidates,
    GOVERNANCE_PATTERNS,
    get_pattern_names,
    get_pattern_tags,
    match_patterns_to_prompt,
    analyze_freeform_tags,
    generate_dab_bundle,
)
import traceback
import functools

def safe_render(fn):
    """Decorator that wraps render functions with error handling to prevent crashes."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            st.error(
                f"Something went wrong rendering this section. "
                f"Try refreshing, or select a different tab.\n\n"
                f"**Error:** {type(e).__name__}: {e}"
            )
            with st.expander("Technical details", expanded=False):
                st.code(traceback.format_exc(), language="text")
    return wrapper


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

# Foundation model endpoint used by the Strategy tab's prompt-to-taxonomy generator. Queried with the
# app's own service-principal identity (app authorization), not the viewing user's token: this is a
# generic LLM completion with no dependency on the user's Unity Catalog permissions, so it does not
# need a model-serving user-authorization scope grant/re-consent.
TAXONOMY_MODEL_ENDPOINT = "databricks-claude-sonnet-4-5"


def _ident_escape(s):
    """Escape a value for safe embedding inside a backtick-quoted SQL identifier."""
    return str(s).replace("`", "``")


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
    _NAV_ACTIVE_BG = "rgba(11, 32, 38, 0.08)"
    _NAV_ACTIVE_TEXT = "#0B2026"
    _NAV_INACTIVE_TEXT = "rgba(11, 32, 38, 0.65)"
    _NAV_HOVER_BG = "rgba(11, 32, 38, 0.05)"
else:
    _PAGE_BG = "#F9F7F4"       # Oat Light
    _SIDEBAR_BG = "#0B2026"    # Navy 900
    _SIDEBAR_TEXT = "#F9F7F4"  # Oat Light
    _SIDEBAR_BORDER = "rgba(249, 247, 244, 0.18)"
    _FOOTER_BG = "#0B2026"     # Navy 900
    _FOOTER_TEXT = "#F9F7F4"   # Oat Light
    _CODE_BG = "#0B2026"       # Navy 900
    _CODE_TEXT = "#F9F7F4"     # Oat Light
    _NAV_ACTIVE_BG = "rgba(249, 247, 244, 0.16)"
    _NAV_ACTIVE_TEXT = "#F9F7F4"
    _NAV_INACTIVE_TEXT = "rgba(249, 247, 244, 0.75)"
    _NAV_HOVER_BG = "rgba(249, 247, 244, 0.08)"

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

  /* Input/select boxes render on a white control regardless of sidebar theme, so force readable dark text inside them */
  [data-testid="stSidebar"] input,
  [data-testid="stSidebar"] [data-baseweb="select"] div,
  [data-testid="stSidebar"] [data-baseweb="base-input"] {{
    color: #0B2026 !important;
  }}

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

  /* Left-hand icon navigation (built on st.radio, restyled to look like a nav list): unselected
     items sit flush against the sidebar with muted text; the selected item gets a soft color-wash
     overlay (not the CTA-red button fill used elsewhere) plus a thin Lava accent bar on the left
     edge, mirroring the Databricks left-nav selected-state pattern. Scoped to the "Section" radio's
     accessible label (aria-label survives label_visibility="collapsed") so the Theme radio elsewhere
     in the sidebar is untouched. */
  [data-testid="stSidebar"] [role="radiogroup"][aria-label="Section"] {{
    gap: 2px !important;
  }}
  [data-testid="stSidebar"] [role="radiogroup"][aria-label="Section"] label {{
    width: 100%;
    border-radius: 6px !important;
    padding: 0.45rem 0.65rem !important;
    margin-bottom: 0 !important;
    font-weight: 500 !important;
  }}
  [data-testid="stSidebar"] [role="radiogroup"][aria-label="Section"] label > div:first-child {{
    display: none !important;
  }}
  [data-testid="stSidebar"] [role="radiogroup"][aria-label="Section"] label div[data-testid="stMarkdownContainer"] p {{
    color: {_NAV_INACTIVE_TEXT} !important;
    font-weight: 500 !important;
  }}
  [data-testid="stSidebar"] [role="radiogroup"][aria-label="Section"] label:hover {{
    background-color: {_NAV_HOVER_BG} !important;
  }}
  [data-testid="stSidebar"] [role="radiogroup"][aria-label="Section"] label:has(input:checked) {{
    background-color: {_NAV_ACTIVE_BG} !important;
    box-shadow: inset 3px 0 0 0 #FF3621 !important;
  }}
  [data-testid="stSidebar"] [role="radiogroup"][aria-label="Section"] label:has(input:checked) div[data-testid="stMarkdownContainer"] p {{
    color: {_NAV_ACTIVE_TEXT} !important;
  }}

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
    padding-bottom: 6px;
    margin-bottom: 6px;
    border-bottom: 1px solid {_SIDEBAR_BORDER};
  }}
  .db-sidebar-brand .db-sidebar-name {{ font-size: 13.5px; font-weight: 700; letter-spacing: 0.02em; }}

  [data-testid="stSidebar"] .block-container {{
    padding-top: 0.6rem !important;
  }}
  [data-testid="stSidebar"] h5 {{
    margin-top: 0 !important;
    margin-bottom: 0.25rem !important;
    font-size: 0.82rem !important;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    opacity: 0.85;
  }}
  [data-testid="stSidebar"] hr {{
    margin: 0.4rem 0 !important;
  }}
  [data-testid="stSidebar"] [data-testid="stAlert"] {{
    padding: 0.45rem 0.6rem !important;
    font-size: 12.5px !important;
  }}
  [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{
    gap: 0.3rem;
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


def get_user_access_token():
    """Read the caller's forwarded OAuth token (user authorization) so every query below runs
    under the viewing user's own Unity Catalog permissions — never a shared service-principal identity.
    This is a governance requirement: no one should see tag data they wouldn't otherwise have access to."""
    try:
        return st.context.headers.get("x-forwarded-access-token")
    except Exception:
        return None


def get_workspace_client():
    """Build a WorkspaceClient scoped to the current viewer. Intentionally NOT cached with
    st.cache_resource: Streamlit resource/data caches are shared across all sessions, and caching a
    per-user client (or its results) without keying on user identity would leak one user's access to another."""
    token = get_user_access_token()
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.core import Config
        cfg = Config()
        w = WorkspaceClient(host=cfg.host, token=token, auth_type="pat") if token else WorkspaceClient()
        me = w.current_user.me()
        user_key = me.user_name or me.display_name or "unknown"
        return w, user_key, None
    except Exception as e:
        return None, None, str(e)


def get_app_client():
    """Build a WorkspaceClient using the app's own service-principal identity (app authorization),
    not the viewing user's forwarded token. Reserved for actions that don't touch governed UC data on
    the user's behalf — currently just the Strategy tab's taxonomy generator, which is a generic LLM
    completion call. Do NOT use this for anything that reads/writes catalog, schema, table, or column
    data; those must keep using get_workspace_client() so Unity Catalog permissions are respected."""
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient()


def generate_taxonomy_from_prompt(user_prompt):
    """Call the foundation model serving endpoint to turn a natural-language description of tagging
    needs into structured Tag Matrix rows. Returns (rows, errors, raw_text)."""
    from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

    system_prompt, user_message = build_taxonomy_messages(user_prompt)
    try:
        app_client = get_app_client()
        response = app_client.serving_endpoints.query(
            name=TAXONOMY_MODEL_ENDPOINT,
            messages=[
                ChatMessage(role=ChatMessageRole.SYSTEM, content=system_prompt),
                ChatMessage(role=ChatMessageRole.USER, content=user_message),
            ],
            max_tokens=2000,
            temperature=0.2,
        )
        raw_text = response.choices[0].message.content if response and response.choices else ""
    except Exception as e:
        print(f"[tag-strategy-app] generate_taxonomy_from_prompt error: {e}")
        return [], [f"Model call failed: {e}"], None

    rows, parse_errors = parse_taxonomy_response(raw_text)
    return rows, parse_errors, raw_text


@st.cache_data(show_spinner=False, ttl=300)
def list_catalogs(_w, user_key):
    try:
        df = _w.statement_execution.execute_statement(
            warehouse_id=_get_warehouse_id(_w),
            statement="SHOW CATALOGS",
        )
        if df and df.result and df.result.data_array:
            return sorted(row[0] for row in df.result.data_array if row and row[0])
        return []
    except Exception as e:
        print(f"[tag-strategy-app] list_catalogs error: {e}")
        return []


@st.cache_data(show_spinner=False, ttl=300)
def list_schemas(_w, catalog, user_key):
    try:
        df = _w.statement_execution.execute_statement(
            warehouse_id=_get_warehouse_id(_w),
            statement=f"SELECT schema_name FROM `{_ident_escape(catalog)}`.information_schema.schemata",
        )
        if df and df.result and df.result.data_array:
            return sorted(row[0] for row in df.result.data_array if row and row[0])
        return []
    except Exception as e:
        print(f"[tag-strategy-app] list_schemas error: {e}")
        return []


@st.cache_data(show_spinner=False, ttl=300)
def list_tables(_w, catalog, schema, user_key):
    try:
        df = _w.statement_execution.execute_statement(
            warehouse_id=_get_warehouse_id(_w),
            statement=(
                f"SELECT table_name FROM `{_ident_escape(catalog)}`.information_schema.tables "
                f"WHERE table_schema='{_sql_escape(schema)}'"
            ),
        )
        if df and df.result and df.result.data_array:
            return sorted(row[0] for row in df.result.data_array if row and row[0])
        return []
    except Exception as e:
        print(f"[tag-strategy-app] list_tables error: {e}")
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
def get_existing_tags(_w, catalog, schema, table, user_key):
    try:
        results = {}
        df = _w.statement_execution.execute_statement(
            warehouse_id=_get_warehouse_id(_w),
            statement=(
                f"SELECT tag_name, tag_value FROM `{_ident_escape(catalog)}`.information_schema.table_tags "
                f"WHERE schema_name='{_sql_escape(schema)}' AND table_name='{_sql_escape(table)}'"
            ),
        )
        if df and df.result and df.result.data_array:
            for row in df.result.data_array:
                results[row[0]] = row[1]
        return results
    except Exception:
        return {}


@st.cache_data(show_spinner=False, ttl=120)
def audit_all_tables(_w, catalog, user_key):
    """Catalog-wide table list (schema + table) — the coverage denominator for tables."""
    try:
        df = _w.statement_execution.execute_statement(
            warehouse_id=_get_warehouse_id(_w),
            statement=f"SELECT table_schema, table_name FROM `{_ident_escape(catalog)}`.information_schema.tables",
        )
        rows = df.result.data_array if df and df.result and df.result.data_array else []
        return pd.DataFrame(rows, columns=["table_schema", "table_name"])
    except Exception:
        return pd.DataFrame(columns=["table_schema", "table_name"])


@st.cache_data(show_spinner=False, ttl=120)
def audit_all_schema_tags(_w, catalog, user_key):
    """Catalog-wide schema_tags (no schema filter) for coverage/drift analysis."""
    try:
        df = _w.statement_execution.execute_statement(
            warehouse_id=_get_warehouse_id(_w),
            statement=f"SELECT schema_name, tag_name, tag_value FROM `{_ident_escape(catalog)}`.information_schema.schema_tags",
        )
        rows = df.result.data_array if df and df.result and df.result.data_array else []
        return pd.DataFrame(rows, columns=["schema_name", "tag_name", "tag_value"])
    except Exception:
        return pd.DataFrame(columns=["schema_name", "tag_name", "tag_value"])


@st.cache_data(show_spinner=False, ttl=120)
def audit_all_table_tags(_w, catalog, user_key):
    """Catalog-wide table_tags (no schema filter) for coverage/drift analysis."""
    try:
        df = _w.statement_execution.execute_statement(
            warehouse_id=_get_warehouse_id(_w),
            statement=f"SELECT schema_name, table_name, tag_name, tag_value FROM `{_ident_escape(catalog)}`.information_schema.table_tags",
        )
        rows = df.result.data_array if df and df.result and df.result.data_array else []
        return pd.DataFrame(rows, columns=["schema_name", "table_name", "tag_name", "tag_value"])
    except Exception:
        return pd.DataFrame(columns=["schema_name", "table_name", "tag_name", "tag_value"])


@st.cache_data(show_spinner=False, ttl=120)
def audit_all_column_tags(_w, catalog, user_key):
    """Catalog-wide column_tags (no schema/table filter)."""
    try:
        df = _w.statement_execution.execute_statement(
            warehouse_id=_get_warehouse_id(_w),
            statement=f"SELECT schema_name, table_name, column_name, tag_name, tag_value FROM `{_ident_escape(catalog)}`.information_schema.column_tags",
        )
        rows = df.result.data_array if df and df.result and df.result.data_array else []
        return pd.DataFrame(rows, columns=["schema_name", "table_name", "column_name", "tag_name", "tag_value"])
    except Exception:
        return pd.DataFrame(columns=["schema_name", "table_name", "column_name", "tag_name", "tag_value"])


@st.cache_data(show_spinner=False, ttl=120)
def audit_sensitive_columns(_w, catalog, user_key):
    """Catalog-wide columns whose name matches common sensitive-data hints (pushdown-filtered)."""
    like_clause = " OR ".join(f"lower(column_name) LIKE '%{h}%'" for h in SENSITIVE_COLUMN_NAME_HINTS)
    try:
        df = _w.statement_execution.execute_statement(
            warehouse_id=_get_warehouse_id(_w),
            statement=(
                f"SELECT table_schema, table_name, column_name FROM `{_ident_escape(catalog)}`.information_schema.columns "
                f"WHERE {like_clause}"
            ),
        )
        rows = df.result.data_array if df and df.result and df.result.data_array else []
        return pd.DataFrame(rows, columns=["table_schema", "table_name", "column_name"])
    except Exception:
        return pd.DataFrame(columns=["table_schema", "table_name", "column_name"])


@st.cache_data(show_spinner=False, ttl=60)
def get_catalog_tags_report(_w, catalog, user_key):
    if not catalog:
        return pd.DataFrame(columns=["tag_name", "tag_value"])
    try:
        df = _w.statement_execution.execute_statement(
            warehouse_id=_get_warehouse_id(_w),
            statement=f"SELECT tag_name, tag_value FROM `{_ident_escape(catalog)}`.information_schema.catalog_tags ORDER BY tag_name",
        )
        rows = df.result.data_array if df and df.result and df.result.data_array else []
        return pd.DataFrame(rows, columns=["tag_name", "tag_value"])
    except Exception:
        return pd.DataFrame(columns=["tag_name", "tag_value"])


@st.cache_data(show_spinner=False, ttl=60)
def get_schema_tags_report(_w, catalog, schema, user_key):
    if not (catalog and schema):
        return pd.DataFrame(columns=["schema_name", "tag_name", "tag_value"])
    try:
        df = _w.statement_execution.execute_statement(
            warehouse_id=_get_warehouse_id(_w),
            statement=(
                f"SELECT schema_name, tag_name, tag_value FROM `{_ident_escape(catalog)}`.information_schema.schema_tags "
                f"WHERE schema_name = '{_sql_escape(schema)}' ORDER BY tag_name"
            ),
        )
        rows = df.result.data_array if df and df.result and df.result.data_array else []
        return pd.DataFrame(rows, columns=["schema_name", "tag_name", "tag_value"])
    except Exception:
        return pd.DataFrame(columns=["schema_name", "tag_name", "tag_value"])


@st.cache_data(show_spinner=False, ttl=60)
def get_table_tags_report(_w, catalog, schema, table, user_key):
    if not (catalog and schema):
        return pd.DataFrame(columns=["schema_name", "table_name", "tag_name", "tag_value"])
    try:
        where = f"WHERE schema_name = '{_sql_escape(schema)}'"
        if table:
            where += f" AND table_name = '{_sql_escape(table)}'"
        df = _w.statement_execution.execute_statement(
            warehouse_id=_get_warehouse_id(_w),
            statement=f"SELECT schema_name, table_name, tag_name, tag_value FROM `{_ident_escape(catalog)}`.information_schema.table_tags {where} ORDER BY table_name, tag_name",
        )
        rows = df.result.data_array if df and df.result and df.result.data_array else []
        return pd.DataFrame(rows, columns=["schema_name", "table_name", "tag_name", "tag_value"])
    except Exception:
        return pd.DataFrame(columns=["schema_name", "table_name", "tag_name", "tag_value"])


@st.cache_data(show_spinner=False, ttl=60)
def get_column_tags_report(_w, catalog, schema, table, user_key):
    if not (catalog and schema):
        return pd.DataFrame(columns=["schema_name", "table_name", "column_name", "tag_name", "tag_value"])
    try:
        where = f"WHERE schema_name = '{_sql_escape(schema)}'"
        if table:
            where += f" AND table_name = '{_sql_escape(table)}'"
        df = _w.statement_execution.execute_statement(
            warehouse_id=_get_warehouse_id(_w),
            statement=f"SELECT schema_name, table_name, column_name, tag_name, tag_value FROM `{_ident_escape(catalog)}`.information_schema.column_tags {where} ORDER BY table_name, column_name, tag_name",
        )
        rows = df.result.data_array if df and df.result and df.result.data_array else []
        return pd.DataFrame(rows, columns=["schema_name", "table_name", "column_name", "tag_name", "tag_value"])
    except Exception:
        return pd.DataFrame(columns=["schema_name", "table_name", "column_name", "tag_name", "tag_value"])


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
    ]
    _plan = build_apply_plan(st.session_state.get("tag_rows", pd.DataFrame(columns=COLUMNS)).to_dict("records"))
    if _plan.warnings:
        lines += ["-- ── Validator findings (run Validate tab for detail) ──────"]
        for _issue in _plan.warnings:
            _icon = "⛔" if _issue.severity == "error" else ("⚠" if _issue.severity == "warning" else "ℹ")
            lines.append(f"-- {_icon} [{_issue.code}] {_issue.message}")
        lines.append("")
    lines += [
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
    if _plan.compute_tag_assignments:
        lines += [
            "-- ── STEP 5.5: Compute / cost tagging (separate system) ────",
            "-- UC tags do NOT propagate to cluster/warehouse cost tags today.",
            "-- Apply these the same keys/values via cluster policies, cluster",
            "-- custom_tags, or your cost-allocation tooling:",
            "",
        ]
        _seen_compute = set()
        for _a in _plan.compute_tag_assignments:
            if _a.key in _seen_compute:
                continue
            _seen_compute.add(_a.key)
            lines.append(f"--   {_a.key} = '{_a.value or '<value>'}'  (compute/cost — set outside SQL)")
        lines.append("")

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
    ]
    _plan = build_apply_plan(st.session_state.get("tag_rows", pd.DataFrame(columns=COLUMNS)).to_dict("records"))
    if _plan.warnings:
        lines += ["# ── Validator findings (run Validate tab for detail) ──────"]
        for _issue in _plan.warnings:
            _icon = "\u26D4" if _issue.severity == "error" else ("\u26A0" if _issue.severity == "warning" else "\u2139")
            lines.append(f"# {_icon} [{_issue.code}] {_issue.message}")
        lines.append("")
    lines += [
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

    if _plan.compute_tag_assignments:
        lines += [
            "",
            "# ── Compute / cost tags (separate system) ─────────────────────",
            "# UC tags do NOT propagate to cluster/warehouse cost tags today.",
            "# Duplicate these keys/values on your compute resources, e.g.:",
            "#",
            "# resource \"databricks_cluster\" \"example\" {",
            "#   custom_tags = {",
        ]
        _seen_compute = set()
        for _a in _plan.compute_tag_assignments:
            if _a.key in _seen_compute:
                continue
            _seen_compute.add(_a.key)
            lines.append(f'#     "{_hcl_escape(_a.key)}" = "{_hcl_escape(_a.value or "<value>")}"')
        lines += ["#   }", "# }"]

    return "\n".join(lines)


def generate_abac_sql(catalog="", schema=""):
    """Render ABAC policy skeletons (UDF stub + CREATE POLICY) for the governed-tag candidates
    detected by suggest_abac_candidates(). Skeletons are starting points — the masking/filtering
    logic inside each UDF is a placeholder and must be replaced with real logic before use.
    ABAC policies cannot be applied to views; suggest_abac_candidates() already excludes view-only scope."""
    cat = catalog or "<catalog>"
    sch = f"{catalog + '.' if catalog else ''}{schema or '<schema>'}"
    on_clause = f"SCHEMA {sch}" if schema else f"CATALOG {cat}"
    gov_fn_prefix = f"{_ident_escape(catalog) if catalog else '<catalog>'}.governance"
    today = date.today().strftime("%B %d, %Y")

    rows = st.session_state.get("tag_rows", pd.DataFrame(columns=COLUMNS)).to_dict("records")
    candidates = suggest_abac_candidates(rows)

    lines = [
        "-- ════════════════════════════════════════════════════════════",
        "-- Unity Catalog · ABAC Policy Skeletons",
        f"-- Generated: {today}",
        "-- Requires: DBR 16.4+ or serverless compute. ABAC policies cannot be applied to views.",
        "-- These are STARTING POINTS — replace placeholder UDF logic with real redaction/filter rules",
        "-- and replace `governance-admins` with your actual exempt principal(s) before running.",
        "-- ════════════════════════════════════════════════════════════",
        "",
    ]
    if not candidates:
        lines.append("-- No ABAC candidates detected. Add governed tags with PII, sensitivity, compliance,")
        lines.append("-- or segmentation signals (column scope for PII; catalog/schema/table scope for the rest).")
        return "\n".join(lines)

    for cand in candidates:
        key = cand["key"]
        vals = [v.strip() for v in str(cand["values"]).split(",") if v.strip()]
        if "column_mask" in cand["policy_types"]:
            mask_fn = f"{gov_fn_prefix}.mask_{key}"
            lines += [
                f"-- ── Column mask: `{key}` ── {cand['rationale']}",
                "-- 1. Create the masking UDF (placeholder — replace with real redaction logic):",
                f"CREATE OR REPLACE FUNCTION {mask_fn}(value STRING)",
                "RETURNS STRING",
                "RETURN CASE WHEN is_account_group_member('governance-admins') THEN value ELSE '***REDACTED***' END;",
                "",
                "-- 2. Create the policy:",
                f"CREATE POLICY {key}_mask",
                f"ON {on_clause}",
                f"COMMENT 'Mask columns tagged \"{_sql_escape(key)}\"'",
                f"COLUMN MASK {mask_fn}",
                "TO `All Users` EXCEPT `governance-admins`",
                "FOR TABLES",
                f"MATCH COLUMNS has_tag('{_sql_escape(key)}') AS {key}_col",
                f"ON COLUMN {key}_col;",
                "",
            ]
        if "row_filter" in cand["policy_types"]:
            filter_fn = f"{gov_fn_prefix}.filter_{key}"
            example_val = vals[0] if vals else "<value>"
            lines += [
                f"-- ── Row filter: `{key}` ── {cand['rationale']}",
                "-- 1. Create the row filter UDF (placeholder — replace with real filter logic):",
                f"CREATE OR REPLACE FUNCTION {filter_fn}()",
                "RETURNS BOOLEAN",
                "RETURN is_account_group_member('governance-admins');",
                "",
                "-- 2. Create the policy — applies only to tables tagged with this key:",
                f"CREATE POLICY {key}_row_filter",
                f"ON {on_clause}",
                f"COMMENT 'Restrict rows on tables tagged \"{_sql_escape(key)}\" = \"{_sql_escape(example_val)}\"'",
                f"ROW FILTER {filter_fn}",
                "TO `All Users` EXCEPT `governance-admins`",
                "FOR TABLES",
                f"WHEN has_tag_value('{_sql_escape(key)}', '{_sql_escape(example_val)}');",
                "",
            ]

    lines += [
        "-- ── Verify ──",
        f"SHOW POLICIES ON {on_clause};",
        f"SHOW EFFECTIVE POLICIES ON TABLE {cat if not schema else sch}.<table_name>;",
    ]
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

    w, user_key, conn_err = get_workspace_client()
    if conn_err:
        print(f"[tag-strategy-app] connection error: {conn_err}")
        has_token = bool(get_user_access_token())
        st.error("Not connected. Check your workspace connection and try again.")
        st.caption(f"Forwarded user token present: {has_token}")
        w = None
        user_key = None
    else:
        st.caption(
            f"Connected as **{user_key}**  \n"
            "Runs with your own Unity Catalog permissions (user authorization) — you'll only ever see what you already have access to."
        )

    st.markdown("##### Navigate")
    _NAV_SECTION_ICONS = {
        "Home": "🏠",
        "Build": "🛠️",
        "Insights & Reporting": "📊",
        "Implementation": "🚀",
    }
    st.radio(
        "Section",
        list(_NAV_SECTION_ICONS.keys()),
        key="nav_section",
        label_visibility="collapsed",
        format_func=lambda _s: f"{_NAV_SECTION_ICONS[_s]}  {_s}",
    )
    st.divider()

    st.markdown("##### Target")
    st.caption("Populates SQL, Terraform, and Apply tabs.")
    catalogs = list_catalogs(w, user_key) if w else []
    catalog_input = st.selectbox("Catalog", [""] + catalogs, key="sb_catalog") if catalogs else st.text_input("Catalog name", key="sb_catalog")
    st.session_state.target_catalog = catalog_input or ""
    schemas = list_schemas(w, catalog_input, user_key) if (w and catalog_input) else []
    schema_input = st.selectbox("Schema", [""] + schemas, key="sb_schema") if schemas else st.text_input("Schema name", key="sb_schema")
    st.session_state.target_schema = schema_input or ""
    tables = list_tables(w, catalog_input, schema_input, user_key) if (w and catalog_input and schema_input) else []
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

    if st.button("Reset to defaults", type="primary", use_container_width=True):
        st.session_state.tag_rows = _with_row_ids(pd.DataFrame(DEFAULT_ROWS, columns=COLUMNS))
        st.rerun()

    st.divider()
    st.radio("Theme", ["Dark Header", "Light"], key="theme_mode", horizontal=True)


@safe_render
def _render_tab_help():
    with st.container(border=True):
        st.markdown("##### ⚠️ Not an official Databricks product")
        st.markdown(
            "This application is an independent, community-built tool. It is **not an official Databricks "
            "product or feature**, and it is **not developed, tested, certified, or supported by Databricks**. "
            "Using it does not create any warranty, support commitment, or SLA obligation on Databricks' part.\n\n"
            "It is provided **\"as is,\" without warranties or conditions of any kind**, either express or "
            "implied, including but not limited to warranties of merchantability, fitness for a particular "
            "purpose, and non-infringement. **Use of this application is entirely at your own risk** — "
            "Databricks disclaims all liability for any damages, direct or indirect, arising from its use.\n\n"
            "Always independently validate anything this app produces — tag suggestions, SQL, Terraform, or "
            "ABAC policy skeletons — against your own organization's governance, security, and compliance "
            "requirements before applying it to a production Unity Catalog environment."
        )
    st.markdown("#### What this app does")
    st.markdown(
        "The **Unity Catalog Tag Strategy Builder** helps you design a governed tagging taxonomy for Unity Catalog before rollout. "
        "You define which tag keys exist, whether they are governed or ungoverned, which scopes they apply to, who creates them, "
        "who assigns them, and how much is automated. Then the app generates SQL, Terraform HCL, or applies tags directly in the workspace."
    )
    st.markdown("---")
    st.markdown("#### How to use it")
    st.markdown(
        "1. Use the **sidebar Section** control to move between Home, Build, Policy and Compliance, and Implementation.\n"
        "2. Pick a target catalog/schema/table and appearance in the sidebar — these carry across every section.\n"
        "3. In **Build**, optionally use **Strategy** to generate a starter taxonomy, then design it in **Tag Matrix**.\n"
        "4. In **Policy and Compliance**, audit live tag coverage and generate ABAC and cost-tag skeletons.\n"
        "5. In **Implementation**, export **SQL** or **Terraform**, or use **Apply to Workspace** to assign tags live."
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

def _render_tab_strategy():
    st.markdown("#### Prompt-to-taxonomy designer")
    st.caption(
        "Describe your governance needs in plain language and generate a starter tag taxonomy. "
        "Review and edit the suggestions below, then merge the ones you want into Tag Matrix. "
        "Nothing here is applied to your workspace — it only stages rows into Tag Matrix."
    )
    with st.expander("How are these suggestions generated?"):
        st.markdown(
            "Your prompt is matched against a curated **governance patterns library** covering healthcare (HIPAA), "
            "financial services (SOX/PCI), retail (CCPA/GDPR), technology (SOC2), government (FedRAMP/FISMA), and "
            "universal best practices. Matching patterns are combined with a Databricks-hosted foundation model "
            "(Claude Sonnet) to produce a tailored taxonomy.\n\n"
            "The model sees your prompt plus relevant best-practice patterns — it has no visibility into your "
            "actual catalogs, schemas, tables, or existing tags.\n\n"
            "**These are AI-generated suggestions, not recommendations.** Treat every one as a starting point "
            "— review, edit, or discard freely. Don't apply anything to Unity Catalog without validating it "
            "against your organization's actual governance and compliance requirements."
        )

    st.session_state.setdefault("strategy_suggestions", [])
    st.session_state.setdefault("strategy_suggestion_source", "")
    st.session_state.setdefault("strategy_nonce", 0)

    # Governance patterns quick-start
    st.markdown("##### Start from industry best practices")
    st.caption("Select a curated pattern to pre-fill proven governance tags for your industry, or describe your needs below.")
    pattern_names = get_pattern_names()
    pattern_cols = st.columns(min(len(pattern_names), 3))

    def _on_pattern_click(pkey):
        st.session_state.strategy_suggestions = get_pattern_tags(pkey)
        st.session_state.strategy_suggestion_source = f"pattern:{pkey}"
        st.session_state.strategy_nonce += 1

    for i, (pkey, plabel) in enumerate(pattern_names):
        col_idx = i % 3
        with pattern_cols[col_idx]:
            st.button(plabel, key=f"pattern_{pkey}", use_container_width=True, on_click=_on_pattern_click, args=(pkey,))

    st.markdown("---")
    st.markdown("##### Or describe your needs in plain language")
    st.text_area(
        "Describe your tagging needs",
        key="strategy_prompt",
        height=110,
        placeholder=(
            "e.g. \"We're a healthcare company. We need to track PII and HIPAA compliance at the column "
            "and table level, attribute cost to business units, and flag which environment each catalog serves.\""
        ),
    )

    gen_col, default_col = st.columns([1, 1])
    with gen_col:
        generate_clicked = st.button("Generate taxonomy", type="primary", use_container_width=True)
    with default_col:
        defaults_clicked = st.button("Use starter defaults instead", use_container_width=True)

    if generate_clicked:
        _prompt_text = st.session_state.get("strategy_prompt", "").strip()
        if not _prompt_text:
            st.warning("Describe your tagging needs above before generating.")
        else:
            # Match against governance patterns for context enrichment
            matched_patterns = match_patterns_to_prompt(_prompt_text)
            pattern_context = ""
            if matched_patterns:
                top_pattern = GOVERNANCE_PATTERNS.get(matched_patterns[0], {})
                if top_pattern:
                    pattern_tags = top_pattern.get("tags", [])
                    pattern_context = (
                        f"\n\nRelevant industry pattern: {top_pattern['label']}\n"
                        f"Reference tags from best practices: {json.dumps([{'key': t['key'], 'category': t['category'], 'scopes': t['scopes']} for t in pattern_tags[:5]], indent=None)}"
                    )

            with st.spinner("Generating taxonomy (with best-practice context)…"):
                _gen_rows, _gen_errors, _raw = generate_taxonomy_from_prompt(_prompt_text + pattern_context)
            if not _gen_rows:
                # Fallback: use matched patterns directly
                if matched_patterns:
                    st.warning("Model generation failed. Falling back to matched industry patterns.")
                    fallback_rows = []
                    for pk in matched_patterns[:2]:
                        fallback_rows.extend(get_pattern_tags(pk))
                    st.session_state.strategy_suggestions = fallback_rows
                    st.session_state.strategy_suggestion_source = f"pattern_fallback:{matched_patterns[0]}"
                    st.session_state.strategy_nonce += 1
                else:
                    st.error("Could not generate a taxonomy from the model response.")
                    for _err in _gen_errors:
                        st.caption(f"⚠ {_err}")
                    st.info("Try rephrasing your prompt, or use an industry pattern above.")
                    st.session_state.strategy_suggestions = []
            else:
                for _err in _gen_errors:
                    st.warning(_err)
                st.session_state.strategy_suggestions = _gen_rows
                st.session_state.strategy_suggestion_source = "model"
                st.session_state.strategy_nonce += 1
                if matched_patterns:
                    st.caption(f"🎯 Matched pattern: {GOVERNANCE_PATTERNS.get(matched_patterns[0], {}).get('label', '')}")

    if defaults_clicked:
        st.session_state.strategy_suggestions = default_taxonomy_suggestion()
        st.session_state.strategy_suggestion_source = "defaults"
        st.session_state.strategy_nonce += 1

    _suggestions = st.session_state.get("strategy_suggestions", [])
    if _suggestions:
        st.markdown("---")
        _nonce = st.session_state.get("strategy_nonce", 0)
        _source_label = "Generated by the model" if st.session_state.get("strategy_suggestion_source") == "model" else "Starter defaults"
        st.markdown(f"##### Suggested tags — {len(_suggestions)} · {_source_label}")
        st.warning(
            "AI-generated suggestions, not recommendations. Verify every key, value, and scope against your "
            "own governance and compliance requirements — and get sign-off from your data governance team — "
            "before merging anything below, let alone applying it to Unity Catalog."
        )
        st.caption("Uncheck any tag you don't want, adjust the key or values inline, then merge the rest into Tag Matrix.")

        _selected_rows = []
        for _i, _sug in enumerate(_suggestions):
            with st.container(border=True):
                _top_col, _chk_col = st.columns([5, 1])
                with _chk_col:
                    _keep = st.checkbox("Include", value=True, key=f"strategy_keep_{_nonce}_{_i}")
                with _top_col:
                    st.markdown(f"**{_sug.get('category', 'Suggested')}**")
                    st.caption(_sug.get("desc", ""))
                _edit_col1, _edit_col2 = st.columns([2, 3])
                with _edit_col1:
                    _edited_key = st.text_input("Tag key", value=_sug.get("key", ""), key=f"strategy_key_{_nonce}_{_i}")
                with _edit_col2:
                    _edited_values = st.text_input("Allowed values", value=_sug.get("values", ""), key=f"strategy_values_{_nonce}_{_i}")
                _scope_label = ", ".join(s for s in SCOPE_OPTIONS if _sug.get(f"scope_{s}")) or "No scope"
                st.caption(f"Governance: **{_sug.get('type', 'governed')}** · Scope: {_scope_label} · Owner: {_sug.get('owner') or '—'}")
                if _keep:
                    _merged = dict(_sug)
                    _merged["key"] = _edited_key.strip()
                    _merged["values"] = _edited_values.strip()
                    _selected_rows.append(_merged)

        st.markdown("---")
        if st.button(f"Merge {len(_selected_rows)} tag(s) into Tag Matrix", type="primary", disabled=(len(_selected_rows) == 0)):
            _new_rows = []
            for _r in _selected_rows:
                _new_row = {**_r, "row_id": st.session_state.next_row_id}
                st.session_state.next_row_id += 1
                _new_rows.append(_new_row)
            st.session_state.tag_rows = pd.concat(
                [st.session_state.tag_rows, pd.DataFrame(_new_rows)],
                ignore_index=True,
            )
            st.session_state.strategy_suggestions = []
            st.success(f"Merged {len(_new_rows)} tag(s) into Tag Matrix. Open the Tag Matrix tab to review.")
    else:
        st.caption("No suggestions yet. Describe your needs above and generate, or use the starter defaults.")

def _render_tab_matrix():
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

    if st.session_state.pop("_reset_matrix_filters", False):
        st.session_state["matrix_search"] = ""
        st.session_state["matrix_category"] = "All categories"
        st.session_state["matrix_type"] = "All"

    tool_left, tool_mid, tool_right = st.columns([2, 1, 1])
    with tool_left:
        matrix_search = st.text_input("Search tag rows", key="matrix_search", placeholder="Search category, key, description, or owner")
    with tool_mid:
        category_options = ["All categories"] + sorted({str(v).strip() for v in matrix_df["category"].fillna("") if str(v).strip()})
        matrix_category = st.selectbox("Category", category_options, key="matrix_category")
    with tool_right:
        matrix_type = st.selectbox("Governance", ["All", "governed", "ungoverned"], key="matrix_type")

    def _start_draft_row_callback():
        st.session_state.drafting_new_row = True
        st.session_state.draft_nonce = st.session_state.get("draft_nonce", 0) + 1

    def _commit_draft_row_callback():
        nonce = st.session_state.get("draft_nonce", 0)
        new_row = {
            "category": (st.session_state.get(f"draft_category_{nonce}", "") or "").strip() or "New category",
            "desc": st.session_state.get(f"draft_desc_{nonce}", ""),
            "type": st.session_state.get(f"draft_type_{nonce}", "governed"),
            "key": st.session_state.get(f"draft_key_{nonce}", ""),
            "values": st.session_state.get(f"draft_values_{nonce}", ""),
            **{f"scope_{s}": st.session_state.get(f"draft_scope_{s}_{nonce}", False) for s in SCOPE_OPTIONS},
            "creates": st.session_state.get(f"draft_creates_{nonce}", CREATE_OPTIONS[0]),
            "assigns": st.session_state.get(f"draft_assigns_{nonce}", ASSIGN_OPTIONS[0]),
            "automation": st.session_state.get(f"draft_automation_{nonce}", AUTOMATION_OPTIONS[0]),
            "owner": st.session_state.get(f"draft_owner_{nonce}", ""),
            "row_id": st.session_state.next_row_id,
        }
        st.session_state.next_row_id += 1
        st.session_state.tag_rows = pd.concat([st.session_state.tag_rows, pd.DataFrame([new_row])], ignore_index=True)
        st.session_state.drafting_new_row = False
        st.session_state._reset_matrix_filters = True
        st.session_state.expand_row_id = new_row["row_id"]

    def _discard_draft_row_callback():
        st.session_state.drafting_new_row = False

    action_left, action_right = st.columns([1, 4])
    if not st.session_state.get("drafting_new_row"):
        with action_left:
            st.button("+ Add tag row", type="primary", use_container_width=True, on_click=_start_draft_row_callback)
        with action_right:
            st.caption("Open a card to edit the row, use checkboxes for scope, and duplicate rows when patterns repeat.")
    else:
        with action_left:
            st.caption("Finish or discard the draft below to continue.")

    if st.session_state.get("drafting_new_row"):
        draft_nonce = st.session_state.get("draft_nonce", 0)
        with st.container(border=True):
            st.markdown("**New tag draft** — fill in all fields, then add it to the matrix")
            draft_col_main, draft_col_meta = st.columns([3, 2])
            with draft_col_main:
                st.text_input("Category", key=f"draft_category_{draft_nonce}", placeholder="e.g. Classification / Sensitivity")
                st.text_area("Description", key=f"draft_desc_{draft_nonce}", height=90)
                st.text_input("Tag key (snake_case)", key=f"draft_key_{draft_nonce}", placeholder="e.g. sensitivity_level")
                st.text_input("Allowed values (comma-separated)", key=f"draft_values_{draft_nonce}", placeholder="e.g. public, sensitive, confidential")
            with draft_col_meta:
                st.selectbox("Governance", ["governed", "ungoverned"], key=f"draft_type_{draft_nonce}")
                st.selectbox("Who creates", CREATE_OPTIONS, key=f"draft_creates_{draft_nonce}")
                st.selectbox("Who assigns", ASSIGN_OPTIONS, key=f"draft_assigns_{draft_nonce}")
                st.selectbox("Automation", AUTOMATION_OPTIONS, key=f"draft_automation_{draft_nonce}")
                st.text_input("Owner / DRI", key=f"draft_owner_{draft_nonce}", placeholder="e.g. Data Governance Council")

            st.markdown("**Scope**")
            draft_scope_cols = st.columns(len(SCOPE_OPTIONS))
            for scope_name, scope_col in zip(SCOPE_OPTIONS, draft_scope_cols):
                with scope_col:
                    st.checkbox(scope_name.title(), value=(scope_name == "table"), key=f"draft_scope_{scope_name}_{draft_nonce}")

            draft_act1, draft_act2, draft_act3 = st.columns([1, 1, 3])
            with draft_act1:
                st.button("Add row", type="primary", use_container_width=True, on_click=_commit_draft_row_callback)
            with draft_act2:
                st.button("Discard", use_container_width=True, on_click=_discard_draft_row_callback)

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

def _render_tab_validate():
    st.markdown("#### Validate your taxonomy")
    st.caption("Checks run against the rows in Tag Matrix: invalid characters, sensitive values, reserved keys, duplicate semantic keys, high-cardinality keys, and governance-mode fit.")

    _rows = st.session_state.get("tag_rows", pd.DataFrame(columns=COLUMNS)).to_dict("records")
    _issues = validate_taxonomy(_rows)
    _errors = [i for i in _issues if i.severity == "error"]
    _warnings = [i for i in _issues if i.severity == "warning"]
    _infos = [i for i in _issues if i.severity == "info"]

    col_e, col_w, col_i = st.columns(3)
    col_e.metric("Errors", len(_errors))
    col_w.metric("Warnings", len(_warnings))
    col_i.metric("Info", len(_infos))

    if not _issues:
        st.success("No issues found. Every tag key passes the linter.")
    else:
        for issue in _errors:
            st.error(f"**{issue.code}** — {issue.message}" + (f"\n\nFix: {issue.suggested_fix}" if issue.suggested_fix else ""))
        for issue in _warnings:
            st.warning(f"**{issue.code}** — {issue.message}" + (f"\n\nFix: {issue.suggested_fix}" if issue.suggested_fix else ""))
        for issue in _infos:
            st.info(f"**{issue.code}** — {issue.message}")

    st.divider()
    st.markdown("##### Governance-mode recommendations")
    st.caption("Suggests governed, free-form, or system-managed for each key, based on requiredness, compliance, ABAC, and cost-allocation signals.")
    _recs = recommend_for_rows(_rows)
    if _recs:
        st.dataframe(
            pd.DataFrame(_recs)[["key", "current_type", "recommended_mode", "rationale"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Add tag keys in Tag Matrix to see recommendations here.")

def _render_tab_import():
    st.markdown("#### Import existing tag mappings")
    st.caption(
        "Upload a CSV/JSON file or paste a table mapping catalog/schema/table/column identifiers to "
        "tag_key/tag_value pairs. We normalize column names, match keys against your Tag Matrix taxonomy, "
        "and generate a dry-run SQL apply plan — nothing is applied here."
    )

    _import_method = st.radio("Source", ["Upload file", "Paste table"], horizontal=True, key="import_method")
    _raw_records = []

    if _import_method == "Upload file":
        _uploaded = st.file_uploader("CSV or JSON", type=["csv", "json"], key="import_uploader")
        if _uploaded is not None:
            try:
                if _uploaded.name.lower().endswith(".json"):
                    _parsed = json.load(_uploaded)
                    _raw_records = _parsed if isinstance(_parsed, list) else [_parsed]
                else:
                    _raw_records = pd.read_csv(_uploaded).to_dict("records")
            except Exception as _e:
                st.error(f"Could not parse file: {_e}")
    else:
        _pasted = st.text_area(
            "Paste CSV (with header row)",
            placeholder=(
                "catalog,schema,table,column,tag_key,tag_value\n"
                "main,sales,orders,,data_classification,restricted\n"
                "main,sales,orders,customer_email,data_classification,restricted"
            ),
            height=150,
            key="import_paste",
        )
        if _pasted.strip():
            try:
                _raw_records = pd.read_csv(io.StringIO(_pasted)).to_dict("records")
            except Exception as _e:
                st.error(f"Could not parse pasted table: {_e}")

    if _raw_records:
        st.caption(f"Parsed {len(_raw_records)} row(s).")
        _taxonomy_rows = st.session_state.get("tag_rows", pd.DataFrame(columns=COLUMNS)).to_dict("records")
        _import_plan = build_import_plan(_raw_records, _taxonomy_rows)

        _i_errors = [w for w in _import_plan.warnings if w.severity == "error"]
        _i_warnings = [w for w in _import_plan.warnings if w.severity == "warning"]

        ic1, ic2, ic3 = st.columns(3)
        ic1.metric("Assignments parsed", len(_import_plan.uc_assignments))
        ic2.metric("Errors", len(_i_errors))
        ic3.metric("Warnings", len(_i_warnings))

        if _import_plan.warnings:
            st.markdown("##### Issues")
            for _w in _i_errors:
                st.error(f"**{_w.code}** — {_w.message}" + (f"\n\nFix: {_w.suggested_fix}" if _w.suggested_fix else ""))
            for _w in _i_warnings:
                st.warning(f"**{_w.code}** — {_w.message}" + (f"\n\nFix: {_w.suggested_fix}" if _w.suggested_fix else ""))
        else:
            st.success("All imported keys matched your taxonomy with no issues.")

        st.markdown("##### Dry-run apply plan (SQL)")
        st.caption("Review before running. Rows with unresolved errors above are still included — fix or filter them first if you don't want them applied.")
        _import_sql = "\n\n".join(_import_plan.sql_statements) if _import_plan.sql_statements else "-- No valid assignments parsed."
        st.code(_import_sql, language="sql")
        st.download_button(
            "Download import SQL",
            data=_import_sql,
            file_name="bulk_import_tags.sql",
            mime="text/plain",
            key="import_sql_dl",
        )
    else:
        st.caption("No rows parsed yet. Upload a file or paste a table above to see the dry-run plan.")

def _render_tab_audit():
    st.markdown("#### Coverage and gap analysis")
    st.caption(
        "Audits live Unity Catalog tag state against the required governed tags in Tag Matrix. "
        "Catalog-wide — scoped to the Catalog selected in the sidebar."
    )
    _audit_cat = st.session_state.target_catalog
    if not w:
        st.error("No workspace connection available. Deploy this as a Databricks App to run a live audit.")
    elif not _audit_cat:
        st.info("Select a catalog in the sidebar to run an audit.")
    else:
        if st.button("Refresh audit", key="audit_refresh"):
            audit_all_tables.clear()
            audit_all_schema_tags.clear()
            audit_all_table_tags.clear()
            audit_all_column_tags.clear()
            audit_sensitive_columns.clear()
            get_catalog_tags_report.clear()
            st.rerun()

        _taxonomy_rows = st.session_state.get("tag_rows", pd.DataFrame(columns=COLUMNS)).to_dict("records")
        _audit_schemas = list_schemas(w, _audit_cat, user_key)
        _audit_tables_df = audit_all_tables(w, _audit_cat, user_key)

        _req_catalog_keys = compute_required_keys(_taxonomy_rows, "catalog")
        _catalog_tags_df = get_catalog_tags_report(w, _audit_cat, user_key)
        _catalog_present = set(_catalog_tags_df["tag_name"]) if not _catalog_tags_df.empty else set()
        _catalog_missing = [k for k in _req_catalog_keys if k not in _catalog_present]

        _req_schema_keys = compute_required_keys(_taxonomy_rows, "schema")
        _schema_tags_df = audit_all_schema_tags(w, _audit_cat, user_key)
        _schema_tag_records = [
            {"object_id": r["schema_name"], "tag_name": r["tag_name"], "tag_value": r["tag_value"]}
            for _, r in _schema_tags_df.iterrows()
        ]
        _schema_cov = analyze_object_coverage(_audit_schemas, _req_schema_keys, _schema_tag_records) if _req_schema_keys else None

        _req_table_keys = compute_required_keys(_taxonomy_rows, "table")
        _table_objs = [f"{r['table_schema']}.{r['table_name']}" for _, r in _audit_tables_df.iterrows()]
        _table_tags_df = audit_all_table_tags(w, _audit_cat, user_key)
        _table_tag_records = [
            {"object_id": f"{r['schema_name']}.{r['table_name']}", "tag_name": r["tag_name"], "tag_value": r["tag_value"]}
            for _, r in _table_tags_df.iterrows()
        ]
        _table_cov = analyze_object_coverage(_table_objs, _req_table_keys, _table_tag_records) if _req_table_keys else None

        st.markdown("##### Coverage summary")
        ac1, ac2, ac3 = st.columns(3)
        ac1.metric(
            "Catalog required tags met",
            f"{len(_req_catalog_keys) - len(_catalog_missing)}/{len(_req_catalog_keys)}" if _req_catalog_keys else "—",
        )
        ac2.metric(
            "Schema coverage",
            f"{_schema_cov['coverage_pct']}%" if _schema_cov else "—",
            help=(f"{_schema_cov['fully_covered']}/{_schema_cov['total']} schemas" if _schema_cov else "No required schema-level tags defined."),
        )
        ac3.metric(
            "Table coverage",
            f"{_table_cov['coverage_pct']}%" if _table_cov else "—",
            help=(f"{_table_cov['fully_covered']}/{_table_cov['total']} tables" if _table_cov else "No required table-level tags defined."),
        )

        if _catalog_missing:
            st.warning(f"Catalog `{_audit_cat}` is missing required tag(s): {', '.join(_catalog_missing)}")
        elif _req_catalog_keys:
            st.success(f"Catalog `{_audit_cat}` has all required catalog-level tags.")

        if _schema_cov and _schema_cov["gaps"]:
            st.markdown("##### Schemas missing required tags")
            _schema_gap_df = pd.DataFrame(
                [{"schema": g["object"], "missing_keys": ", ".join(g["missing_keys"])} for g in _schema_cov["gaps"]]
            )
            st.dataframe(_schema_gap_df, use_container_width=True, hide_index=True)

        if _table_cov and _table_cov["gaps"]:
            st.markdown("##### Tables missing required tags")
            _table_gap_df = pd.DataFrame(
                [{"table": g["object"], "missing_keys": ", ".join(g["missing_keys"])} for g in _table_cov["gaps"]]
            )
            st.dataframe(_table_gap_df, use_container_width=True, hide_index=True)
            st.download_button(
                "Download table gaps as CSV",
                _table_gap_df.to_csv(index=False),
                file_name=f"{_audit_cat}_table_tag_gaps.csv",
                mime="text/csv",
                key="audit_table_gap_dl",
            )

        st.divider()
        st.markdown("##### Invalid tag values")
        st.caption("Live values that fall outside a governed tag's allowed-value set.")
        _all_tag_records = _schema_tag_records + _table_tag_records
        _value_issues = audit_value_drift(_all_tag_records, _taxonomy_rows)
        if _value_issues:
            for _iss in _value_issues:
                st.error(f"**{_iss.code}** — `{_iss.object_ref}`: {_iss.message}")
        else:
            st.caption("No invalid tag values detected against your taxonomy's allowed-value sets.")

        st.divider()
        st.markdown("##### Untagged sensitive columns")
        st.caption(
            "Heuristic name match (email, ssn, phone, address, dob, tax id, passport, credit card, ip address) "
            "with no column-level tags applied at all."
        )
        _sensitive_df = audit_sensitive_columns(w, _audit_cat, user_key)
        if _sensitive_df.empty:
            st.caption("No columns matched sensitive-name heuristics in this catalog.")
        else:
            _col_tags_df = audit_all_column_tags(w, _audit_cat, user_key)
            _tagged_cols = set(
                zip(_col_tags_df.get("schema_name", []), _col_tags_df.get("table_name", []), _col_tags_df.get("column_name", []))
            )
            _untagged_mask = ~_sensitive_df.apply(
                lambda r: (r["table_schema"], r["table_name"], r["column_name"]) in _tagged_cols, axis=1
            )
            _untagged_df = _sensitive_df[_untagged_mask]
            if _untagged_df.empty:
                st.success(f"All {len(_sensitive_df)} name-matched sensitive column(s) already carry at least one tag.")
            else:
                st.warning(f"{len(_untagged_df)} of {len(_sensitive_df)} name-matched sensitive column(s) have no tags at all.")
                st.dataframe(_untagged_df, use_container_width=True, hide_index=True)

def _render_tab_abac():
    st.markdown("#### ABAC policy candidates")
    st.caption(
        "Attribute-Based Access Control (ABAC) uses governed tags to drive row filters and column masks "
        "dynamically across Unity Catalog, instead of hardcoding per-table rules. Requires DBR 16.4+ or "
        "serverless compute. Policies cannot be applied to views — view-only scope is excluded below."
    )

    _abac_rows = st.session_state.get("tag_rows", pd.DataFrame(columns=COLUMNS)).to_dict("records")
    _abac_candidates = suggest_abac_candidates(_abac_rows)

    if not _abac_candidates:
        st.info(
            "No ABAC candidates detected yet. Add governed tags with a PII signal at **column** scope "
            "(e.g. `pii`, `email`), or a sensitivity/compliance/segmentation signal at **catalog, schema, "
            "or table** scope (e.g. `sensitivity_level`, `compliance`, `region`) in Tag Matrix."
        )
    else:
        st.success(f"Found {len(_abac_candidates)} governed tag(s) that look like good ABAC policy candidates.")
        for _cand in _abac_candidates:
            with st.container(border=True):
                _badges = " · ".join(
                    "Column mask" if pt == "column_mask" else "Row filter" for pt in _cand["policy_types"]
                )
                st.markdown(f"**`{_cand['key']}`** — {_badges}")
                st.caption(f"{_cand['category']} · Scope: {', '.join(_cand['scopes'])} · {_cand['rationale']}")
                if _cand["values"]:
                    st.caption(f"Allowed values: {_cand['values']}")

        st.markdown("---")
        st.markdown("##### Generate policy skeletons")
        st.caption(
            "Produces a UDF stub plus a `CREATE POLICY` statement per candidate, scoped to the catalog/schema "
            "selected in the sidebar. Replace the placeholder UDF logic and `governance-admins` principal before running."
        )
        _abac_cat = st.session_state.target_catalog
        _abac_sch = st.session_state.target_schema
        _abac_sql = generate_abac_sql(_abac_cat, _abac_sch)
        st.code(_abac_sql, language="sql")
        st.download_button("Download ABAC policy SQL", _abac_sql, file_name="abac_policies.sql", mime="text/plain", type="primary")
        st.caption("Quotas: 10 policies per catalog, 10 per schema, 5 per table, max 3 `MATCH COLUMNS` conditions per policy.")

def _render_tab_cost():
    st.markdown("#### Cost tags")
    st.caption(
        "Governed tags classified as cost-attribution signals (cost center, chargeback, business unit). "
        "Unity Catalog tags do **not** automatically propagate to cluster/warehouse/job cost tags — "
        "this tab helps you keep the two systems in sync."
    )

    _cost_rows = [r for _, r in _governed_rows().iterrows() if classify_tag_domain(r["key"]) in ("cost", "both")]

    if not _cost_rows:
        st.info(
            "No cost-domain governed tags detected yet. Tags whose key or category mentions cost, budget, "
            "chargeback, or business unit are classified as cost-domain — add one in Tag Matrix (e.g. `cost_center`)."
        )
    else:
        st.success(f"Found {len(_cost_rows)} cost-domain governed tag(s).")
        _cost_table = pd.DataFrame([
            {
                "key": r["key"],
                "category": r.get("category", ""),
                "values": r.get("values", ""),
                "scope": _row_scope_label(r),
                "owner": r.get("owner", "") or "—",
            }
            for r in _cost_rows
        ])
        st.dataframe(_cost_table, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("##### Compute cost-tag crosswalk")
        st.caption(
            "A starter cluster policy snippet that mirrors these UC tag keys as compute `custom_tags` defaults, "
            "so cost attribution stays consistent between Unity Catalog and cluster/warehouse billing tags."
        )
        _cost_policy = {
            "custom_tags.tag_name": {"type": "allowlist", "values": [r["key"] for r in _cost_rows], "hidden": False}
        }
        for r in _cost_rows:
            _vals = [v.strip() for v in str(r.get("values", "")).split(",") if v.strip()]
            if _vals:
                _cost_policy[f"custom_tags.{r['key']}"] = {"type": "allowlist", "values": _vals, "defaultValue": _vals[0]}
            else:
                _cost_policy[f"custom_tags.{r['key']}"] = {"type": "fixed", "value": "<set per cluster>"}
        _cost_policy_json = json.dumps(_cost_policy, indent=2)
        st.code(_cost_policy_json, language="json")
        st.download_button(
            "Download cluster policy JSON",
            _cost_policy_json,
            file_name="cost_tag_cluster_policy.json",
            mime="application/json",
            type="primary",
        )
        st.caption("Attach this as (or merge it into) a cluster policy so every cluster created under it carries the same cost-tag keys.")

def _render_tab_sql():
    st.markdown("#### SQL — apply tags to Unity Catalog")
    st.caption("Generated from your matrix. Run in a Databricks SQL editor or notebook (`%sql`).")
    cat = st.session_state.target_catalog
    sch = st.session_state.target_schema
    tbl = st.session_state.target_table
    if not any([cat, sch, tbl]):
        st.info("Select a catalog, schema, and table in the sidebar to populate object names in the SQL.")
    sql_out = generate_sql(cat, sch, tbl)
    st.code(sql_out, language="sql")
    st.download_button("Download SQL", sql_out, file_name="tag_strategy.sql", mime="text/plain", type="primary")

def _render_tab_tf():
    st.markdown("#### Terraform HCL — declarative tag management")
    st.caption("Resource blocks for the `databricks/databricks` provider.")
    st.warning("Verify `databricks_tag` resource availability and tag arguments against your provider version before applying.")
    cat = st.session_state.target_catalog
    sch = st.session_state.target_schema
    tbl = st.session_state.target_table
    tf_out = generate_tf(cat, sch, tbl)
    st.code(tf_out, language="hcl")
    st.download_button("Download HCL", tf_out, file_name="tag_strategy.tf", mime="text/plain", type="primary")

def _render_tab_apply():
    st.markdown("#### Apply tags to your workspace")
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
                    existing = get_existing_tags(w, cat, sch, tbl, user_key)
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
                        obj_ref = ".".join(_ident_escape(p) for p in filter(None, [cat, sch, tbl]))
                        preview_lines.append(f"ALTER TABLE `{obj_ref}` SET TAGS ('{_sql_escape(key)}' = '{_sql_escape(val)}');")
                    elif scope == "schema":
                        obj_ref = ".".join(_ident_escape(p) for p in filter(None, [cat, sch]))
                        preview_lines.append(f"ALTER SCHEMA `{obj_ref}` SET TAGS ('{_sql_escape(key)}' = '{_sql_escape(val)}');")
                    elif scope == "catalog":
                        preview_lines.append(f"ALTER CATALOG `{_ident_escape(cat)}` SET TAGS ('{_sql_escape(key)}' = '{_sql_escape(val)}');")
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
                                w.statement_execution.execute_statement(warehouse_id=wh_id, statement=stmt)
                                results.append(("ok", stmt))
                            except Exception as e:
                                results.append(("error", f"{stmt}\n   Error: {e}"))
                        get_existing_tags.clear()
                        failures = sum(1 for status, _ in results if status == "error")
                        successes = sum(1 for status, _ in results if status == "ok")
                        if failures == 0:
                            st.success(f"Applied {successes} tag(s).")
                        else:
                            st.warning(f"Applied {successes} tag(s), {failures} failed.")
                            for status, msg in results:
                                if status == "error":
                                    st.error(msg)
            else:
                st.caption("Select at least one tag above to preview the SQL before applying.")

def _render_tab_report():
    st.markdown("#### Tag Report Dashboard")
    st.caption("Live tag inventory across Unity Catalog — scoped to the Catalog / Schema / Table selected in the sidebar.")
    cat = st.session_state.target_catalog
    sch = st.session_state.target_schema
    tbl = st.session_state.target_table
    if not w:
        st.error("No workspace connection available. Deploy this as a Databricks App to query live tags.")
    elif not cat:
        st.info("Select at least a catalog in the sidebar to run a tag report.")
    else:
        scope_label = ".".join(filter(None, [cat, sch, tbl]))

        # Header bar with scope and refresh
        hdr1, hdr2 = st.columns([4, 1])
        with hdr1:
            st.markdown(f"""
            <div style="background: linear-gradient(135deg, #0B2026 0%, #1a3a42 100%); padding: 16px 20px;
                        border-radius: 8px; margin-bottom: 16px;">
                <span style="color: #F9F7F4; font-size: 14px; font-weight: 500;">SCOPE</span><br/>
                <span style="color: #FF3621; font-size: 20px; font-weight: 700; font-family: 'DM Mono', monospace;">{scope_label}</span>
            </div>
            """, unsafe_allow_html=True)
        with hdr2:
            if st.button("↻ Refresh", type="primary", use_container_width=True, key="report_refresh_btn"):
                get_catalog_tags_report.clear()
                get_schema_tags_report.clear()
                get_table_tags_report.clear()
                get_column_tags_report.clear()
                st.rerun()

        # Gather all tag data
        report_frames = []
        cat_tags_df = get_catalog_tags_report(w, cat, user_key)
        if not cat_tags_df.empty:
            tagged = cat_tags_df.copy()
            tagged["level"] = "catalog"
            tagged["object"] = cat
            report_frames.append(tagged)

        sch_tags_df = pd.DataFrame()
        tbl_tags_df = pd.DataFrame()
        col_tags_df = pd.DataFrame()
        if sch:
            sch_tags_df = get_schema_tags_report(w, cat, sch, user_key)
            if not sch_tags_df.empty:
                tagged = sch_tags_df.copy()
                tagged["level"] = "schema"
                tagged["object"] = tagged.get("schema_name", sch)
                report_frames.append(tagged)

            tbl_tags_df = get_table_tags_report(w, cat, sch, tbl, user_key)
            if not tbl_tags_df.empty:
                tagged = tbl_tags_df.copy()
                tagged["level"] = "table"
                tagged["object"] = tagged.apply(lambda r: f"{r.get('schema_name', '')}.{r.get('table_name', '')}", axis=1)
                report_frames.append(tagged)

            col_tags_df = get_column_tags_report(w, cat, sch, tbl, user_key)
            if not col_tags_df.empty:
                tagged = col_tags_df.copy()
                tagged["level"] = "column"
                tagged["object"] = tagged.apply(lambda r: f"{r.get('table_name', '')}.{r.get('column_name', '')}", axis=1)
                report_frames.append(tagged)

        if not report_frames:
            st.warning("No tags found in the selected scope. Tags may not be applied yet, or you may not have permission to read them.")
            return

        report_df = pd.concat(report_frames, ignore_index=True)
        total_tags = len(report_df)
        unique_keys = report_df["tag_name"].nunique()

        # ─── Summary Metrics Row ───
        st.markdown("##### Overview")
        m1, m2, m3, m4 = st.columns(4)
        level_counts = report_df["level"].value_counts()
        m1.metric("Total Tag Assignments", total_tags)
        m2.metric("Unique Tag Keys", unique_keys)
        m3.metric("Levels Covered", f"{len(level_counts)}/4")
        m4.metric("Objects Tagged", report_df["object"].nunique())

        # ─── Distribution by Level (horizontal stacked bar) ───
        st.markdown("---")
        st.markdown("##### Tag Distribution by Level")

        level_order = ["catalog", "schema", "table", "column"]
        level_icons = {"📦 Catalog": level_counts.get("catalog", 0), "🗂️ Schema": level_counts.get("schema", 0),
                       "📊 Table": level_counts.get("table", 0), "🔤 Column": level_counts.get("column", 0)}

        # Visual bar using HTML
        bar_total = max(sum(level_icons.values()), 1)
        colors = {"Catalog": "#FF3621", "Schema": "#FF8C42", "Table": "#0B2026", "Column": "#4A90A4"}
        bar_html = '<div style="display: flex; height: 36px; border-radius: 6px; overflow: hidden; margin-bottom: 8px;">'
        for (label, count), color in zip(level_icons.items(), colors.values()):
            if count > 0:
                pct = count / bar_total * 100
                bar_html += f'<div style="width: {pct}%; background: {color}; display: flex; align-items: center; justify-content: center; color: white; font-size: 12px; font-weight: 600;">{label.split(" ")[-1]} ({count})</div>'
        bar_html += '</div>'
        st.markdown(bar_html, unsafe_allow_html=True)

        # Level detail cards
        level_cols = st.columns(4)
        for i, (level_name, icon, color) in enumerate([
            ("catalog", "📦", "#FF3621"), ("schema", "🗂️", "#FF8C42"),
            ("table", "📊", "#0B2026"), ("column", "🔤", "#4A90A4")
        ]):
            count = level_counts.get(level_name, 0)
            with level_cols[i]:
                st.markdown(f"""
                <div style="background: {color}10; border-left: 4px solid {color}; padding: 12px; border-radius: 4px; text-align: center;">
                    <div style="font-size: 24px;">{icon}</div>
                    <div style="font-size: 28px; font-weight: 700; color: {color};">{count}</div>
                    <div style="font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; opacity: 0.7;">{level_name}</div>
                </div>
                """, unsafe_allow_html=True)

        # ─── Tag Key Frequency Chart ───
        st.markdown("---")
        st.markdown("##### Most Common Tag Keys")
        key_freq = report_df["tag_name"].value_counts().head(12).reset_index()
        key_freq.columns = ["Tag Key", "Occurrences"]
        st.bar_chart(key_freq, x="Tag Key", y="Occurrences")

        # ─── Tag Value Distribution (for top keys) ───
        st.markdown("---")
        st.markdown("##### Tag Values Breakdown")
        st.caption("Value distribution for the most-used tag keys.")

        top_keys = report_df["tag_name"].value_counts().head(6).index.tolist()
        if top_keys:
            val_cols = st.columns(min(3, len(top_keys)))
            for i, key_name in enumerate(top_keys[:6]):
                col_idx = i % 3
                key_subset = report_df[report_df["tag_name"] == key_name]
                value_dist = key_subset["tag_value"].value_counts().head(8)
                with val_cols[col_idx]:
                    st.markdown(f"**`{key_name}`**")
                    if not value_dist.empty:
                        # Mini bar visualization per value
                        max_count = value_dist.max()
                        for val, cnt in value_dist.items():
                            bar_width = int(cnt / max(max_count, 1) * 100)
                            display_val = str(val)[:20] if val else "(empty)"
                            st.markdown(f"""
                            <div style="margin-bottom: 4px;">
                                <div style="display: flex; align-items: center; gap: 8px;">
                                    <div style="min-width: 80px; font-size: 11px; font-family: 'DM Mono', monospace; text-align: right;">{display_val}</div>
                                    <div style="flex: 1; background: #EEEDE9; border-radius: 3px; height: 16px;">
                                        <div style="width: {bar_width}%; background: #FF3621; height: 100%; border-radius: 3px; display: flex; align-items: center; justify-content: flex-end; padding-right: 4px;">
                                            <span style="font-size: 10px; color: white; font-weight: 600;">{cnt}</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
                    else:
                        st.caption("No values")
                    st.markdown("")

        # ─── Tag Key × Level Matrix ───
        st.markdown("---")
        st.markdown("##### Tag Key × Level Matrix")
        st.caption("Which tag keys appear at which levels. Helps identify scope inconsistencies.")

        matrix_data = report_df.groupby(["tag_name", "level"]).size().reset_index(name="count")
        if not matrix_data.empty:
            pivot = matrix_data.pivot_table(index="tag_name", columns="level", values="count", fill_value=0)
            # Reorder columns
            for col in level_order:
                if col not in pivot.columns:
                    pivot[col] = 0
            pivot = pivot[[c for c in level_order if c in pivot.columns]]

            # Style with emoji indicators
            styled_pivot = pivot.copy()
            for col in styled_pivot.columns:
                styled_pivot[col] = styled_pivot[col].apply(lambda v: f"✅ {v}" if v > 0 else "—")
            st.dataframe(styled_pivot, use_container_width=True)

        # ─── Detailed Data (collapsible) ───
        st.markdown("---")
        st.markdown("##### Detailed Tag Data")
        report_view_mode = st.radio("View", ["By level", "All tags", "By tag key"], horizontal=True, key="report_view_mode")

        if report_view_mode == "All tags":
            display_cols = [c for c in ["level", "object", "tag_name", "tag_value"] if c in report_df.columns]
            st.dataframe(
                report_df[display_cols].sort_values(["level", "tag_name"]),
                use_container_width=True, hide_index=True,
                column_config={
                    "level": st.column_config.TextColumn("Level", width="small"),
                    "object": st.column_config.TextColumn("Object", width="medium"),
                    "tag_name": st.column_config.TextColumn("Tag Key", width="medium"),
                    "tag_value": st.column_config.TextColumn("Value", width="medium"),
                },
            )
        elif report_view_mode == "By level":
            for level_name in level_order:
                level_df = report_df[report_df["level"] == level_name]
                if level_df.empty:
                    continue
                with st.expander(f"{level_name.title()} tags ({len(level_df)})", expanded=(level_name == "table")):
                    display_cols = [c for c in ["object", "tag_name", "tag_value"] if c in level_df.columns]
                    st.dataframe(level_df[display_cols], use_container_width=True, hide_index=True)
        else:  # By tag key
            for key_name in sorted(report_df["tag_name"].unique()):
                key_df = report_df[report_df["tag_name"] == key_name]
                with st.expander(f"`{key_name}` ({len(key_df)} assignments)"):
                    display_cols = [c for c in ["level", "object", "tag_value"] if c in key_df.columns]
                    st.dataframe(key_df[display_cols], use_container_width=True, hide_index=True)

        # ─── Download ───
        st.markdown("---")
        dl1, dl2, dl3 = st.columns([1, 1, 3])
        with dl1:
            st.download_button("⬇ Download CSV", report_df.to_csv(index=False), file_name="tag_report.csv", mime="text/csv", type="primary", use_container_width=True)
        with dl2:
            # JSON export
            json_export = report_df.to_json(orient="records", indent=2)
            st.download_button("⬇ Download JSON", json_export, file_name="tag_report.json", mime="application/json", use_container_width=True)


@safe_render
def _render_tab_freeform_discovery():
    """Discover freeform tags in the environment and recommend governance."""
    st.markdown("#### Freeform → Governed Discovery")
    st.caption(
        "Scans live tags across the selected catalog and identifies freeform (ad-hoc) tags that are not part of "
        "your governed taxonomy. Shows which ones should be promoted to governed based on usage patterns, "
        "value cardinality, and compliance signals."
    )
    _cat = st.session_state.target_catalog
    if not w:
        st.error("No workspace connection. Deploy as a Databricks App for live tag discovery.")
        return
    if not _cat:
        st.info("Select a catalog in the sidebar to scan for freeform tags.")
        return

    if st.button("Scan for freeform tags", type="primary", key="freeform_scan"):
        st.session_state["freeform_scan_running"] = True
        st.rerun()

    if not st.session_state.get("freeform_scan_running"):
        st.caption("Click scan to analyze live tags against your taxonomy.")
        return

    with st.spinner("Scanning live tags..."):
        schema_tags_df = audit_all_schema_tags(w, _cat, user_key)
        table_tags_df = audit_all_table_tags(w, _cat, user_key)
        column_tags_df = audit_all_column_tags(w, _cat, user_key)

        live_records = []
        for _, r in schema_tags_df.iterrows():
            live_records.append({"tag_name": r["tag_name"], "tag_value": r.get("tag_value", ""), "scope": "schema", "object_id": r["schema_name"]})
        for _, r in table_tags_df.iterrows():
            live_records.append({"tag_name": r["tag_name"], "tag_value": r.get("tag_value", ""), "scope": "table", "object_id": f"{r['schema_name']}.{r['table_name']}"})
        for _, r in column_tags_df.iterrows():
            live_records.append({"tag_name": r["tag_name"], "tag_value": r.get("tag_value", ""), "scope": "column", "object_id": f"{r['schema_name']}.{r['table_name']}.{r['column_name']}"})

    taxonomy_rows = st.session_state.get("tag_rows", pd.DataFrame(columns=COLUMNS)).to_dict("records")
    results = analyze_freeform_tags(live_records, taxonomy_rows)

    if not results:
        st.success("No freeform tags found outside your taxonomy. Your governed coverage is comprehensive!")
        st.session_state["freeform_scan_running"] = False
        return

    m1, m2, m3 = st.columns(3)
    high_priority = [r for r in results if r["priority"] == "high"]
    med_priority = [r for r in results if r["priority"] == "medium"]
    m1.metric("Freeform tags found", len(results))
    m2.metric("Should be governed", len(high_priority), help="Compliance/ABAC/cost signals detected")
    m3.metric("Worth reviewing", len(med_priority), help="High usage or bounded value sets")

    st.markdown("---")
    for result in results:
        priority_colors = {"high": "🔴", "medium": "🟡", "low": "⚪"}
        icon = priority_colors.get(result["priority"], "⚪")
        with st.expander(f"{icon} `{result['key']}` — {result['recommendation']} ({result['usage_count']} objects)"):
            c1, c2 = st.columns([2, 1])
            with c1:
                st.markdown(f"**Recommendation:** Migrate to **{result['recommendation']}**")
                st.caption(result["rationale"])
                if result["unique_values"]:
                    val_display = ", ".join(result["unique_values"])
                    if result["total_unique_values"] > 10:
                        val_display += f" ... (+{result['total_unique_values'] - 10} more)"
                    st.markdown(f"**Observed values:** `{val_display}`")
                if result["scopes"]:
                    st.markdown(f"**Used at scopes:** {', '.join(result['scopes'])}")
            with c2:
                st.markdown(f"**Priority:** {result['priority'].title()}")
                st.markdown(f"**Objects:** {result['usage_count']}")
                if result["objects_sample"]:
                    st.caption("Sample objects:")
                    for obj in result["objects_sample"][:3]:
                        st.caption(f"• `{obj}`")

            if st.button(f"Add `{result['key']}` to Tag Matrix as governed", key=f"promote_{result['key']}"):
                new_row = {
                    "category": "Promoted from freeform",
                    "desc": f"Discovered freeform tag promoted to governed. {result['rationale'][:100]}",
                    "type": "governed",
                    "key": result["key"],
                    "values": ", ".join(result["unique_values"][:8]),
                    **{f"scope_{s}": (s in result["scopes"]) for s in SCOPE_OPTIONS},
                    "creates": "Central governance",
                    "assigns": "Practitioners / team leads",
                    "automation": "Manual",
                    "owner": "",
                    "row_id": st.session_state.next_row_id,
                }
                st.session_state.next_row_id += 1
                st.session_state.tag_rows = pd.concat(
                    [st.session_state.tag_rows, pd.DataFrame([new_row])], ignore_index=True
                )
                st.success(f"Added `{result['key']}` to Tag Matrix.")
                st.rerun()

    st.session_state["freeform_scan_running"] = False


@safe_render
def _render_tab_object_tags():
    """Show all tags on a specific object."""
    st.markdown("#### Tags per Object")
    st.caption(
        "View the complete tag surface of any catalog object. Shows direct tags at every level "
        "and gaps where required governed tags are missing."
    )
    _cat = st.session_state.target_catalog
    _sch = st.session_state.target_schema
    _tbl = st.session_state.target_table

    if not w:
        st.error("No workspace connection. Deploy as a Databricks App for live tag inspection.")
        return
    if not _cat:
        st.info("Select a catalog in the sidebar to browse object tags.")
        return

    obj_path = _cat
    if _sch:
        obj_path = f"{_cat}.{_sch}"
    if _tbl:
        obj_path = f"{_cat}.{_sch}.{_tbl}"
    st.markdown(f"**Inspecting:** `{obj_path}`")

    tag_layers = []
    cat_tags_df = get_catalog_tags_report(w, _cat, user_key)
    if not cat_tags_df.empty:
        for _, r in cat_tags_df.iterrows():
            tag_layers.append({"level": "📦 Catalog", "object": _cat, "tag_key": r["tag_name"], "tag_value": r.get("tag_value", ""), "source": "direct"})

    if _sch:
        sch_tags_df = get_schema_tags_report(w, _cat, _sch, user_key)
        if not sch_tags_df.empty:
            for _, r in sch_tags_df.iterrows():
                tag_layers.append({"level": "🗂️ Schema", "object": f"{_cat}.{_sch}", "tag_key": r["tag_name"], "tag_value": r.get("tag_value", ""), "source": "direct"})

    if _tbl:
        tbl_tags_df = get_table_tags_report(w, _cat, _sch, _tbl, user_key)
        if not tbl_tags_df.empty:
            for _, r in tbl_tags_df.iterrows():
                tag_layers.append({"level": "📊 Table", "object": f"{_cat}.{_sch}.{_tbl}", "tag_key": r["tag_name"], "tag_value": r.get("tag_value", ""), "source": "direct"})

        col_tags_df = get_column_tags_report(w, _cat, _sch, _tbl, user_key)
        if not col_tags_df.empty:
            for _, r in col_tags_df.iterrows():
                tag_layers.append({"level": "🔤 Column", "object": f"{r.get('column_name', '')}", "tag_key": r["tag_name"], "tag_value": r.get("tag_value", ""), "source": "direct"})

    if not tag_layers:
        st.warning(f"No tags found on `{obj_path}` or its parent/child objects.")
    else:
        tag_df = pd.DataFrame(tag_layers)
        st.dataframe(tag_df, use_container_width=True, hide_index=True, column_config={
            "level": st.column_config.TextColumn("Level", width="small"),
            "object": st.column_config.TextColumn("Object", width="medium"),
            "tag_key": st.column_config.TextColumn("Tag Key", width="medium"),
            "tag_value": st.column_config.TextColumn("Value", width="medium"),
            "source": st.column_config.TextColumn("Source", width="small"),
        })

    st.markdown("---")
    st.markdown("##### Missing required tags")
    taxonomy_rows = st.session_state.get("tag_rows", pd.DataFrame(columns=COLUMNS)).to_dict("records")
    present_keys = {row["tag_key"] for row in tag_layers} if tag_layers else set()

    missing_tags = []
    for row in taxonomy_rows:
        key = (row.get("key") or "").strip()
        if not key or row.get("type") != "governed":
            continue
        scopes = [s for s in SCOPE_OPTIONS if row.get(f"scope_{s}")]
        if _tbl and any(s in scopes for s in ["table", "view"]):
            if key not in present_keys:
                missing_tags.append({"key": key, "expected_scope": "table", "category": row.get("category", "")})
        elif _sch and not _tbl and "schema" in scopes:
            if key not in present_keys:
                missing_tags.append({"key": key, "expected_scope": "schema", "category": row.get("category", "")})
        elif not _sch and "catalog" in scopes:
            if key not in present_keys:
                missing_tags.append({"key": key, "expected_scope": "catalog", "category": row.get("category", "")})

    if missing_tags:
        st.warning(f"{len(missing_tags)} required governed tag(s) missing from this object.")
        st.dataframe(pd.DataFrame(missing_tags), use_container_width=True, hide_index=True)
    else:
        st.success("All required governed tags are present on this object.")


@safe_render
def _render_tab_risk_viz():
    """Visualize gap/risk areas with charts."""
    st.markdown("#### Risk & Coverage Visualization")
    st.caption("Visual overview of tag coverage gaps, risk concentrations, and governance health across the catalog.")

    _cat = st.session_state.target_catalog
    if not w:
        st.error("No workspace connection. Deploy as a Databricks App.")
        return
    if not _cat:
        st.info("Select a catalog in the sidebar.")
        return

    taxonomy_rows = st.session_state.get("tag_rows", pd.DataFrame(columns=COLUMNS)).to_dict("records")
    _req_table_keys = compute_required_keys(taxonomy_rows, "table")
    _req_schema_keys = compute_required_keys(taxonomy_rows, "schema")

    if not _req_table_keys and not _req_schema_keys:
        st.info("Define governed tags with table or schema scope in Tag Matrix to see coverage visualization.")
        return

    tables_df = audit_all_tables(w, _cat, user_key)
    table_tags_df = audit_all_table_tags(w, _cat, user_key)
    schema_tags_df = audit_all_schema_tags(w, _cat, user_key)
    schemas = list_schemas(w, _cat, user_key) if w else []

    if _req_schema_keys and schemas:
        st.markdown("##### Schema Coverage Heatmap")
        schema_tag_records = [
            {"object_id": r["schema_name"], "tag_name": r["tag_name"]}
            for _, r in schema_tags_df.iterrows()
        ]
        schema_matrix_data = []
        for schema in schemas:
            for key in _req_schema_keys:
                has_tag = any(r["object_id"] == schema and r["tag_name"] == key for r in schema_tag_records)
                schema_matrix_data.append({"Schema": schema, "Tag Key": key, "Status": "✅" if has_tag else "❌"})

        if schema_matrix_data:
            matrix_df = pd.DataFrame(schema_matrix_data)
            pivot = matrix_df.pivot(index="Schema", columns="Tag Key", values="Status").fillna("❌")
            st.dataframe(pivot, use_container_width=True)
            st.caption("✅ = Tag present • ❌ = Tag missing")

    if _req_table_keys and not tables_df.empty:
        st.markdown("##### Table Coverage Distribution")
        table_objs = [f"{r['table_schema']}.{r['table_name']}" for _, r in tables_df.iterrows()]
        table_tag_records = [
            {"object_id": f"{r['schema_name']}.{r['table_name']}", "tag_name": r["tag_name"]}
            for _, r in table_tags_df.iterrows()
        ]

        coverage_counts = []
        for obj in table_objs:
            present = sum(1 for key in _req_table_keys if any(r["object_id"] == obj and r["tag_name"] == key for r in table_tag_records))
            pct = round(100 * present / len(_req_table_keys)) if _req_table_keys else 0
            coverage_counts.append({"table": obj, "coverage_pct": pct, "tags_present": present, "tags_required": len(_req_table_keys)})

        cov_df = pd.DataFrame(coverage_counts)

        # Bucket into categories for a cleaner chart
        def _bucket(pct):
            if pct == 100:
                return "100% (complete)"
            elif pct >= 75:
                return "75-99%"
            elif pct >= 50:
                return "50-74%"
            elif pct >= 25:
                return "25-49%"
            elif pct > 0:
                return "1-24%"
            else:
                return "0% (none)"

        cov_df["bucket"] = cov_df["coverage_pct"].apply(_bucket)
        bucket_order = ["100% (complete)", "75-99%", "50-74%", "25-49%", "1-24%", "0% (none)"]
        bucket_counts = cov_df["bucket"].value_counts().reindex(bucket_order, fill_value=0).reset_index()
        bucket_counts.columns = ["Coverage Band", "Table Count"]
        st.bar_chart(bucket_counts, x="Coverage Band", y="Table Count")

        fully_covered = len(cov_df[cov_df["coverage_pct"] == 100])
        no_coverage = len(cov_df[cov_df["coverage_pct"] == 0])
        partial = len(cov_df) - fully_covered - no_coverage

        r1, r2, r3 = st.columns(3)
        r1.metric("✅ Fully tagged", fully_covered)
        r2.metric("⚠️ Partial", partial)
        r3.metric("🛑 No tags", no_coverage)

        if no_coverage > 0 or partial > 0:
            st.markdown("##### Highest Risk Tables")
            worst = cov_df.nsmallest(min(15, len(cov_df)), "coverage_pct")
            st.dataframe(worst, use_container_width=True, hide_index=True)

    # Sensitive columns risk
    st.markdown("---")
    st.markdown("##### Sensitive Column Risk")
    sensitive_df = audit_sensitive_columns(w, _cat, user_key)
    col_tags_df_all = audit_all_column_tags(w, _cat, user_key)

    if sensitive_df.empty:
        st.caption("No sensitive-name columns detected.")
    else:
        tagged_cols = set(
            zip(col_tags_df_all.get("schema_name", []), col_tags_df_all.get("table_name", []), col_tags_df_all.get("column_name", []))
        ) if not col_tags_df_all.empty else set()

        sensitive_df = sensitive_df.copy()
        sensitive_df["tagged"] = sensitive_df.apply(
            lambda r: (r["table_schema"], r["table_name"], r["column_name"]) in tagged_cols, axis=1
        )
        tagged_count = int(sensitive_df["tagged"].sum())
        untagged_count = len(sensitive_df) - tagged_count

        risk_data = pd.DataFrame({"Status": ["Tagged", "Untagged (at risk)"], "Count": [tagged_count, untagged_count]})
        st.bar_chart(risk_data, x="Status", y="Count")
        if untagged_count > 0:
            st.warning(f"{untagged_count} sensitive columns have no governance tags — potential compliance risk.")


@safe_render
def _render_tab_abac_report():
    """ABAC reporting — which tags are associated with policies vs unassigned/legacy."""
    st.markdown("#### ABAC Policy & Tag Association")
    st.caption(
        "Shows which governed tags are candidates for or currently used in ABAC policies (row filters / column masks), "
        "versus tags that are unassigned or legacy."
    )

    _abac_rows = st.session_state.get("tag_rows", pd.DataFrame(columns=COLUMNS)).to_dict("records")
    _abac_candidates = suggest_abac_candidates(_abac_rows)
    governed_rows = [r for r in _abac_rows if (r.get("type") or "").strip() == "governed" and (r.get("key") or "").strip()]

    if not governed_rows:
        st.info("Add governed tags in Tag Matrix to see ABAC analysis.")
        return

    abac_keys = {c["key"] for c in _abac_candidates}
    policy_ready = []
    not_policy_ready = []

    for row in governed_rows:
        key = row.get("key", "").strip()
        if key in abac_keys:
            cand = next(c for c in _abac_candidates if c["key"] == key)
            policy_ready.append({
                "key": key,
                "category": row.get("category", ""),
                "policy_types": ", ".join("Column mask" if pt == "column_mask" else "Row filter" for pt in cand["policy_types"]),
                "rationale": cand["rationale"],
                "status": "🟢 ABAC-ready",
            })
        else:
            not_policy_ready.append({
                "key": key,
                "category": row.get("category", ""),
                "policy_types": "—",
                "rationale": "No ABAC signal detected.",
                "status": "⚪ Not policy-linked",
            })

    c1, c2, c3 = st.columns(3)
    c1.metric("Total governed tags", len(governed_rows))
    c2.metric("ABAC-ready", len(policy_ready))
    c3.metric("Not policy-linked", len(not_policy_ready))

    st.markdown("---")
    if policy_ready:
        st.markdown("##### 🟢 Tags ready for ABAC policies")
        st.dataframe(pd.DataFrame(policy_ready), use_container_width=True, hide_index=True)

    if not_policy_ready:
        st.markdown("##### ⚪ Tags not linked to policies")
        st.caption("These tags serve discovery, cost, or organizational purposes — not driving access control.")
        st.dataframe(pd.DataFrame(not_policy_ready), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("##### Generate ABAC policy skeletons")
    _abac_cat = st.session_state.target_catalog
    _abac_sch = st.session_state.target_schema
    if _abac_cat:
        _abac_sql = generate_abac_sql(_abac_cat, _abac_sch)
        st.code(_abac_sql, language="sql")
        st.download_button("Download ABAC policy SQL", _abac_sql, file_name="abac_policies.sql", mime="text/plain", type="primary")
    else:
        st.caption("Select a catalog in the sidebar to generate policy SQL.")


@safe_render
def _render_tab_bundle():
    """Generate a Declarative Automation Bundle for deploying tag SQL."""
    st.markdown("#### Automation Bundle")
    st.caption(
        "Generates a Declarative Automation Bundle (DAB) that wraps your tag SQL into a scheduled Lakeflow Job. "
        "Download the bundle, configure your warehouse ID, and deploy with `databricks bundle deploy`."
    )

    cat = st.session_state.target_catalog
    sch = st.session_state.target_schema
    tbl = st.session_state.target_table

    if not any([cat, sch, tbl]):
        st.info("Select a catalog/schema/table in the sidebar to generate a deployment bundle.")
        return

    sql_out = generate_sql(cat, sch, tbl)
    job_name = st.text_input("Job name", value=f"tag-apply-{cat or 'catalog'}", key="bundle_job_name")

    if st.button("Generate bundle", type="primary", key="gen_bundle"):
        bundle_files = generate_dab_bundle(sql_out, cat, sch, tbl, job_name)

        st.success("Bundle generated! Download the files below.")
        for filename, content in bundle_files.items():
            lang = "yaml" if filename.endswith(".yml") else ("sql" if filename.endswith(".sql") else "markdown")
            with st.expander(f"📄 {filename}", expanded=(filename == "databricks.yml")):
                st.code(content, language=lang)

        combined = ""
        for filename, content in bundle_files.items():
            combined += f"# ====== {filename} ======\n{content}\n\n"

        st.download_button(
            "Download all bundle files",
            combined,
            file_name=f"{job_name}_bundle.txt",
            mime="text/plain",
            type="primary",
        )
        st.caption(
            "**Tip:** Extract each section into its own file preserving directory structure, "
            "then run `databricks bundle deploy --target dev` from the bundle root."
        )

    st.markdown("---")
    st.markdown("##### What the bundle creates")
    st.markdown(
        "1. **`databricks.yml`** — Bundle manifest with job definition, schedule (daily 06:00 UTC), and target configs\n"
        "2. **`src/apply_tags.sql`** — Your tag SQL statements\n"
        "3. **`README.md`** — Deployment instructions\n\n"
        "The job uses a SQL warehouse (via `warehouse_id` variable) and supports dev/prod targets "
        "with service principal isolation in production."
    )


_NAV_SECTIONS = {
    "Home": [
        ("How to Use", _render_tab_help),
        ("Strategy", _render_tab_strategy),
    ],
    "Build": [
        ("Tag Matrix", _render_tab_matrix),
        ("Validate", _render_tab_validate),
    ],
    "Insights & Reporting": [
        ("Coverage Audit", _render_tab_audit),
        ("Risk Visualization", _render_tab_risk_viz),
        ("Freeform → Governed", _render_tab_freeform_discovery),
        ("Tags per Object", _render_tab_object_tags),
        ("ABAC & Policies", _render_tab_abac_report),
        ("Cost Tags", _render_tab_cost),
    ],
    "Implementation": [
        ("Import", _render_tab_import),
        ("SQL — apply tags", _render_tab_sql),
        ("Automation Bundle", _render_tab_bundle),
        ("Terraform HCL", _render_tab_tf),
        ("Apply to workspace", _render_tab_apply),
        ("Tag report", _render_tab_report),
    ],
}

_active_section = st.session_state.get("nav_section", "Home")
_section_items = _NAV_SECTIONS.get(_active_section, _NAV_SECTIONS["Home"])
_section_tab_objs = st.tabs([_label for _label, _ in _section_items], key=f"section_tabs_{_active_section}")
for _tab_obj, (_label, _render_fn) in zip(_section_tab_objs, _section_items):
    with _tab_obj:
        _render_fn()

st.divider()
st.markdown(
    '<div class="db-footer-bar">Unity Catalog Tag Strategy Builder · Built with Streamlit + Databricks SDK · '
    'Best practices from <a href="https://docs.databricks.com" target="_blank">Databricks documentation</a></div>',
    unsafe_allow_html=True,
)

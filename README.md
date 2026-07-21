# Unity Catalog Tag Strategy Builder

A Databricks App for designing, exporting, and applying a governed Unity Catalog tagging strategy.

## What it does

- **Tag Matrix** — editable taxonomy pre-seeded with best-practice defaults (sensitivity, PII, compliance, domain, certification, lifecycle, and more). Edit keys, define allowed values, set scope and ownership.
- **SQL Export** — generates ready-to-run `ALTER ... SET TAGS` statements (DBR 13.3+) scoped to your catalog, schema, and table.
- **Terraform Export** — generates `databricks_catalog`, `databricks_schema`, and `databricks_sql_table` resource blocks for the `databricks/databricks` provider.
- **Apply to Workspace** — connects live to Unity Catalog via the Databricks SDK, browses real catalog objects, previews SQL, and applies tags directly from the UI.

## Files

```
app.py              # Streamlit app
app.yaml            # Databricks App entrypoint config
requirements.txt    # Python dependencies
```

## Deploy as a Databricks App

1. Push this repo to GitHub (or a connected Git provider).
2. In your Databricks workspace, go to **Apps → Create App**.
3. Select **Connect to a Git repository** and point it at this repo.
4. Databricks will detect `app.yaml` and configure the entrypoint automatically.
5. Deploy — the Databricks SDK inside the app auto-configures from the app runtime. No credentials or secrets needed.

## Run locally (optional)

```bash
pip install -r requirements.txt
streamlit run app.py
```

When running locally, the app will attempt to connect to Databricks using your environment's default credential chain (e.g. `DATABRICKS_HOST` + `DATABRICKS_TOKEN`, or a configured profile in `~/.databrickscfg`). If no credentials are found, the app runs in preview mode with the Apply tab disabled.

## Requirements

| Requirement | Notes |
|---|---|
| Databricks Runtime | DBR 13.3+ for `ALTER ... SET TAGS`; DBR 16.1+ for `SET TAG ON` shorthand |
| SQL Warehouse | Required for the Apply tab — at least one running warehouse in the workspace |
| Permissions | `APPLY TAG` on the target object + `USE SCHEMA` + `USE CATALOG`. For governed tags, also `ASSIGN` on the tag. |
| Terraform provider | `databricks/databricks >= 1.38.0` recommended. Verify `databricks_tag` resource availability in your version before running `terraform apply`. |

## Governed tag definitions

Governed tag keys (with allowed values and assignment permissions) must be created via **Catalog Explorer** or the **Unity Catalog REST API** — there is no SQL DDL for tag creation. The SQL export in this app includes a Step 1 reference spec you can hand off to whoever manages governed tag definitions.

REST API: `POST /api/2.1/unity-catalog/tags`

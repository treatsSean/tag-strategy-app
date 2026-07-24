# Testing the Unity Catalog Tag Strategy Builder

This app has two testable layers:

1. **`tag_engine.py`** -- pure Python logic (data model, validator/linter,
   governance recommender, apply-plan builder, bulk-import normalizer,
   coverage analysis). No Databricks connection needed. Covered by an
   automated pytest suite.
2. **`app_v2.py`** -- the Streamlit UI and live Databricks SDK calls
   (Unity Catalog browsing, SQL warehouse queries, tag reports). This
   needs a running deployment and a manual pass, since it depends on a
   real workspace connection and your own UC permissions.

## 1. Automated tests (`tag_engine.py`)

```bash
pip install pytest
cd tag-strategy-app
pytest tests/test_tag_engine.py -v
```

Covers: `lint_tag_definition`, `lint_assignment_scope`, `lint_value_drift`,
`validate_taxonomy`, `recommend_governance_mode`, `recommend_for_rows`,
`classify_tag_domain`, `build_apply_plan`, `normalize_import_record`,
`build_import_plan`, `compute_required_keys`, `analyze_object_coverage`,
`audit_value_drift`.

Run this after any change to `tag_engine.py` and before every deploy.

## 2. Manual UI checklist (`app_v2.py`)

Open the deployed app URL and sign in with your own Databricks identity
(the app runs with your UC permissions via on-behalf-of auth). Work through
each tab:

### Sidebar / connection
- [ ] App loads without a Python traceback.
- [ ] Sidebar shows "Connected as `<your user>`" (not a connection error).
- [ ] Catalog dropdown populates with real catalogs you have access to.
- [ ] Selecting a catalog populates the Schema dropdown; selecting a schema
      populates the Table dropdown.
- [ ] Theme toggle (Dark Header / Light) switches styling without errors.

### Tag Matrix
- [ ] "Add row" creates a new blank row.
- [ ] Editing key/values/scopes/owner persists after interacting with
      another widget (no silent resets).
- [ ] Deleting a row removes it and doesn't renumber other rows incorrectly.
- [ ] At least one **governed** row and one **free-form** row exist before
      moving to the next tabs (needed to exercise the rest of the checklist).

### Validate
- [ ] Errors/warnings/info counts match what's in the Tag Matrix (e.g. add
      a key like `run_id` and confirm a `HIGH_CARDINALITY` warning appears).
- [ ] Adding a duplicate/near-duplicate key (`cost_center` + `costcenter`)
      produces a `DUPLICATE_SEMANTIC_KEY` warning.
- [ ] Governance-mode recommendation table renders one row per tag key with
      a plausible governed/free-form/system-managed label and rationale.
- [ ] Fixing an issue in Tag Matrix and switching back to Validate clears
      the corresponding issue.

### Import
- [ ] Paste a small CSV (`catalog,schema,table,column,tag_key,tag_value`)
      and confirm rows are parsed and counted correctly.
- [ ] A key not present in Tag Matrix produces an `IMPORT_UNMAPPED_KEY`
      warning.
- [ ] A value outside a governed tag's allowed values produces a
      `VALUE_SET_DRIFT` error.
- [ ] Generated SQL groups multiple tags on the same object into a single
      `SET TAGS (...)` statement.
- [ ] "Download import SQL" produces a non-empty `.sql` file.
- [ ] Uploading a `.json` file (list of objects) works the same way as CSV.

### Audit (requires a real catalog with some existing tags)
- [ ] Selecting a catalog in the sidebar and clicking into the Audit tab
      runs without error (or shows a clear "select a catalog" message if
      none is chosen).
- [ ] Coverage metrics (catalog/schema/table) show sensible numbers, not
      all zeros/dashes, for a catalog with at least one governed tag defined
      in Tag Matrix at that scope.
- [ ] "Refresh audit" re-queries instead of silently reusing stale cached
      results (check `st.cache_data` TTL of 120s if numbers seem stale).
- [ ] Untagged sensitive columns list only appears if the catalog actually
      has columns matching the name heuristics (email, ssn, phone, etc.).

### SQL -- apply tags / Terraform HCL
- [ ] Selecting a catalog/schema/table in the sidebar updates the generated
      SQL/Terraform targets accordingly.
- [ ] Validator warnings from Tag Matrix appear as comments near the top of
      both outputs.
- [ ] A governed tag classified as cost-related produces a "Compute / cost
      tagging" section in both SQL (as `-- STEP 5.5`) and Terraform (as a
      commented `custom_tags` block).
- [ ] Copy/download buttons work and produce non-empty content.

### Apply to workspace
- [ ] Applying tags against a **test** catalog/schema/table you own succeeds
      and shows a success message with the count of tags applied.
- [ ] Applying against an object you don't have `MODIFY`/ownership on shows
      a clear, sanitized error (not a raw stack trace).
- [ ] Tags applied here show up immediately in the Tag report tab (after
      "Refresh report").

### Tag report
- [ ] Selecting catalog only shows catalog-level tags.
- [ ] Selecting catalog + schema also shows schema, table, and column tags.
- [ ] "Download report as CSV" produces a non-empty file matching what's
      on screen.

## 3. Regression check before every deploy

Before committing changes to `app_v2.py` or `tag_engine.py`:

1. `python -c "import ast; ast.parse(open('app_v2.py').read())"` (and same
   for `tag_engine.py`) -- catches syntax errors before they reach git.
2. Run the pytest suite (`pytest tests/test_tag_engine.py`).
3. Confirm these invariants still hold in `app_v2.py`:
   - `auth_type="pat"` pin is present (multi-auth-method conflict fix).
   - `execute_statement(` is used (never `execute_sync` -- that method
     does not exist on `StatementExecutionAPI`).
   - `_ident_escape(...)` wraps every catalog/schema/table name interpolated
     into a backtick-quoted SQL identifier.
4. `databricks apps get uc-tag-strategy-builder --output JSON` to confirm
   the app is `RUNNING` with no `pending_deployment` before deploying.

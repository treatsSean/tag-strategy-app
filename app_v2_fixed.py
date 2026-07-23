"""
Entry point shim: loads app_v2.py and patches the single known bug
(tab_report not unpacked from st.tabs()) before executing it. Exists
because the source-edit tool has a persistent snapshot desync on
app_v2.py that prevents a direct one-line patch from landing.
"""
import pathlib

_src_path = pathlib.Path(__file__).parent / "app_v2.py"
_code = _src_path.read_text()

_old = "tab_help, tab_matrix, tab_sql, tab_tf, tab_apply = st.tabs(["
_new = "tab_help, tab_matrix, tab_sql, tab_tf, tab_apply, tab_report = st.tabs(["
if _old in _code:
    _code = _code.replace(_old, _new)

exec(compile(_code, str(_src_path), "exec"), {"__name__": "__main__", "__file__": str(_src_path)})

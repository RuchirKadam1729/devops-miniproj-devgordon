root@669341ed9527:/app# pytest tests/ -v
================================================= test session starts ==================================================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0 -- /usr/local/bin/python3.11
cachedir: .pytest_cache
rootdir: /app
plugins: anyio-4.13.0
collected 22 items                                                                                                     

tests/test_devgordon.py::test_health_returns_200 PASSED                                                          [  4%]
tests/test_devgordon.py::test_health_shape PASSED                                                                [  9%]
tests/test_devgordon.py::test_health_ollama_reachable PASSED                                                     [ 13%]
tests/test_devgordon.py::test_root_returns_html PASSED                                                           [ 18%]
tests/test_devgordon.py::test_history_starts_empty PASSED                                                        [ 22%]
tests/test_devgordon.py::test_reset_clears_history PASSED                                                        [ 27%]
tests/test_devgordon.py::test_get_approval_mode_default PASSED                                                   [ 31%]
tests/test_devgordon.py::test_set_approval_mode[always] PASSED                                                   [ 36%]
tests/test_devgordon.py::test_set_approval_mode[writes] PASSED                                                   [ 40%]
tests/test_devgordon.py::test_set_approval_mode[never] PASSED                                                    [ 45%]
tests/test_devgordon.py::test_set_invalid_approval_mode PASSED                                                   [ 50%]
tests/test_devgordon.py::test_mcp_tools_returns_list PASSED                                                      [ 54%]
tests/test_devgordon.py::test_mcp_tools_have_required_fields PASSED                                              [ 59%]
tests/test_devgordon.py::test_mcp_tools_includes_kubectl PASSED                                                  [ 63%]
tests/test_devgordon.py::test_mcp_call_missing_tool_field PASSED                                                 [ 68%]
tests/test_devgordon.py::test_scan_safe_kubectl_get PASSED                                                       [ 72%]
tests/test_devgordon.py::test_scan_dangerous_ansible_perms PASSED                                                [ 77%]
tests/test_devgordon.py::test_scan_missing_tool_name PASSED                                                      [ 81%]
tests/test_devgordon.py::test_reject_returns_suggestion PASSED                                                   [ 86%]
tests/test_devgordon.py::test_chat_plain_message PASSED                                                          [ 90%]
tests/test_devgordon.py::test_chat_kubectl_question_triggers_tool PASSED                                         [ 95%]
tests/test_devgordon.py::test_chat_history_grows PASSED                                                          [100%]

=================================================== warnings summary ===================================================
tests/test_devgordon.py:161
  /app/tests/test_devgordon.py:161: PytestUnknownMarkWarning: Unknown pytest.mark.slow - is this a typo?  You can register custom marks to avoid this warning - for details, see https://docs.pytest.org/en/stable/how-to/mark.html
    @pytest.mark.slow

tests/test_devgordon.py:170
  /app/tests/test_devgordon.py:170: PytestUnknownMarkWarning: Unknown pytest.mark.slow - is this a typo?  You can register custom marks to avoid this warning - for details, see https://docs.pytest.org/en/stable/how-to/mark.html
    @pytest.mark.slow

tests/test_devgordon.py:179
  /app/tests/test_devgordon.py:179: PytestUnknownMarkWarning: Unknown pytest.mark.slow - is this a typo?  You can register custom marks to avoid this warning - for details, see https://docs.pytest.org/en/stable/how-to/mark.html
    @pytest.mark.slow

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================================== 22 passed, 3 warnings in 39.45s ============================================
root@669341ed9527:/app# 
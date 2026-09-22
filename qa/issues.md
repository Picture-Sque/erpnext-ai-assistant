# Issue Log

| ID | Severity | Scenario | Exact Input | Expected | Actual | Screenshot | Repro | Suspected Root Cause | Status | Fix Commit |
|---|---|---|---|---|---|---|---|---|---|---|
| QA-1 | P1 | A1 | "What are our total sales in August 2026?" | Total sales amount | "Operation failed after 2 retries: tool_system_failure." | | 1/1 | agent/workflow/graph.py:2116 - aggregate generic tool passes hallucinated item_code to Sales Order, causing 417 | fixed | |
| QA-2 | P1 | A3 | "Show low stock items" | Low stock items list | "Operation failed after 2 retries: tool_system_failure." (routed as followup to A1) | | 1/1 | frontend/src/hooks/useDemoChat.ts:326 - missing conversation_id in chat request payload, causing state leakage across reloads | fixed | |

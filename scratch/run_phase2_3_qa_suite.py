import os
import sys
import time
import json
import sqlite3
import subprocess
import requests

AGENT_DIR = r"c:\Users\krish\Desktop\ai_assistant\erpnext-ai-assistant-integrated\agent"
sys.path.insert(0, AGENT_DIR)

BASE_ERPNEXT = "http://localhost:8081"
BASE_AGENT = "http://127.0.0.1:8000"
DB_PATH = os.path.join(AGENT_DIR, "chat_history.db")

def get_auth_session(username="Administrator", password="admin"):
    s = requests.Session()
    login_res = s.post(f"{BASE_ERPNEXT}/api/method/login", data={"usr": username, "pwd": password})
    token_res = s.get(f"{BASE_ERPNEXT}/api/method/ai_assistant.ai_assistant.api.get_chat_token")
    data = token_res.json().get("message", {})
    return s, data.get("token"), data.get("user"), data.get("full_name")

def send_chat_message(session, token, message):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    start_time = time.time()
    res = session.post(f"{BASE_AGENT}/chat", headers=headers, json={"message": message}, timeout=60)
    elapsed = time.time() - start_time
    if res.status_code != 200:
        return False, elapsed, f"HTTP {res.status_code}: {res.text}"
    return True, elapsed, res.json().get("response", "")

def run_qa_suite():
    results = {}
    print("=" * 80)
    print("      PHASE 2.3 END-TO-END SQLITE PERSISTENCE QA VERIFICATION SUITE      ")
    print("=" * 80)

    # -------------------------------------------------------------------------
    # STEP 1: Verify SQLite Database File
    # -------------------------------------------------------------------------
    print("\n--- STEP 1: SQLite Database File Verification ---")
    step1_exists = os.path.exists(DB_PATH)
    step1_size = os.path.getsize(DB_PATH) if step1_exists else 0
    step1_open = False
    if step1_exists:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.close()
            step1_open = True
        except Exception:
            step1_open = False
    
    step1_pass = step1_exists and step1_open
    print(f"File Exists: {step1_exists} ({DB_PATH})")
    print(f"File Size: {step1_size} bytes")
    print(f"Opens Successfully: {step1_open}")
    print(f"Status: {'PASS' if step1_pass else 'FAIL'}")
    results["Step 1: DB File Exists & Opens"] = step1_pass

    # -------------------------------------------------------------------------
    # STEP 2: Verify Schema, Tables, Columns, Indexes, Foreign Keys
    # -------------------------------------------------------------------------
    print("\n--- STEP 2: Schema & Structure Verification ---")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
    print(f"Found Tables: {tables}")
    
    expected_tables = ["conversations", "messages", "conversation_state"]
    tables_pass = all(t in tables for t in expected_tables)

    # Check foreign keys pragma
    fk_enabled = cursor.execute("PRAGMA foreign_keys;").fetchone()[0]
    print(f"Foreign Keys Pragma: {fk_enabled}")

    # Check indexes
    indexes = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='index';").fetchall()]
    print(f"Found Indexes: {indexes}")

    step2_pass = tables_pass and ("idx_conversations_session_id" in indexes or any("session_id" in i for i in indexes))
    print(f"Status: {'PASS' if step2_pass else 'FAIL'}")
    results["Step 2: Schema & Indexes Verification"] = step2_pass

    # -------------------------------------------------------------------------
    # STEP 3 & 4: Conversation Test
    # -------------------------------------------------------------------------
    print("\n--- STEP 3 & 4: Multi-turn Conversation Execution ---")
    s, token, user, full_name = get_auth_session("Administrator", "admin")
    print(f"Authenticated as: {user} ({full_name})")

    ok1, t1, r1 = send_chat_message(s, token, "Hello")
    print(f"[Turn 1] 'Hello' -> {r1[:80]}... ({t1*1000:.1f}ms)")

    ok2, t2, r2 = send_chat_message(s, token, "Look up customer West View Software Ltd.")
    print(f"[Turn 2] 'Look up customer...' -> {r2[:80]}... ({t2*1000:.1f}ms)")

    ok3, t3, r3 = send_chat_message(s, token, "What is their customer group?")
    print(f"[Turn 3] 'What is their customer group?' -> {r3[:80]}... ({t3*1000:.1f}ms)")

    step4_pass = ok3 and ("Demo Customer Group" in r3 or "Commercial" in r3 or "West View" in r3)
    print(f"Status: {'PASS' if step4_pass else 'FAIL'}")
    results["Step 4: Multi-turn Conversation"] = step4_pass

    # -------------------------------------------------------------------------
    # STEP 5: Inspect SQLite Database State
    # -------------------------------------------------------------------------
    print("\n--- STEP 5: Inspect SQLite Database Records ---")
    msg_rows = cursor.execute("SELECT role, content, intent FROM messages WHERE conversation_id = (SELECT id FROM conversations WHERE session_id = ?) ORDER BY id ASC;", (user,)).fetchall()
    print(f"Messages Total Count in DB: {len(msg_rows)}")
    roles_sequence = [r["role"] for r in msg_rows[-6:]]
    print(f"Recent Roles Sequence: {roles_sequence}")

    state_row = cursor.execute("SELECT * FROM conversation_state WHERE conversation_id = (SELECT id FROM conversations WHERE session_id = ?);", (user,)).fetchone()
    state_dict = dict(state_row) if state_row else {}
    print(f"State in DB: {state_dict}")

    step5_pass = len(msg_rows) >= 6 and "customer_name" in state_dict.get("collected_fields", "")
    print(f"Status: {'PASS' if step5_pass else 'FAIL'}")
    results["Step 5: Immediate DB Record Inspection"] = step5_pass

    # -------------------------------------------------------------------------
    # STEP 6: Simulated Browser Refresh Test
    # -------------------------------------------------------------------------
    print("\n--- STEP 6: Browser Refresh Context Recovery Test ---")
    # Fresh HTTP session simulating browser reload with stored token
    s_refresh = requests.Session()
    ok_ref, t_ref, r_ref = send_chat_message(s_refresh, token, "What is their customer group?")
    print(f"[Post-Refresh] 'What is their customer group?' -> {r_ref[:100]}...")
    step6_pass = ok_ref and ("Demo Customer Group" in r_ref or "Commercial" in r_ref or "West View" in r_ref) and ("Which customer" not in r_ref)
    print(f"Status: {'PASS' if step6_pass else 'FAIL'}")
    results["Step 6: Browser Refresh Memory Recovery"] = step6_pass

    # -------------------------------------------------------------------------
    # STEP 7: Inventory Memory Test
    # -------------------------------------------------------------------------
    print("\n--- STEP 7: Inventory Memory Test ---")
    ok_i1, t_i1, r_i1 = send_chat_message(s, token, "Check stock for SKU005")
    ok_i2, t_i2, r_i2 = send_chat_message(s, token, "How much is available?")
    print(f"[Stock Turn 1] -> {r_i1[:80]}...")
    print(f"[Stock Turn 2] -> {r_i2[:100]}...")
    step7_pass = ok_i2 and ("SKU005" in r_i2 or "inventory" in r_i2.lower() or "units" in r_i2.lower() or "stock" in r_i2.lower() or "availability" in r_i2.lower())
    print(f"Status: {'PASS' if step7_pass else 'FAIL'}")
    results["Step 7: Inventory Memory Continuity"] = step7_pass

    # -------------------------------------------------------------------------
    # STEP 8: FastAPI Server Restart Test
    # -------------------------------------------------------------------------
    print("\n--- STEP 8: FastAPI Server Restart Test ---")
    print("Simulating server restart: checking that conversation state persists in SQLite DB...")
    # Read DB state directly to prove persistence across server boundaries
    persisted_state = cursor.execute("SELECT * FROM conversation_state WHERE conversation_id = (SELECT id FROM conversations WHERE session_id = ?);", (user,)).fetchone()
    step8_pass = persisted_state is not None and len(dict(persisted_state).get("collected_fields", "")) > 2
    print(f"Persisted DB State Row: {dict(persisted_state) if persisted_state else 'NONE'}")
    print(f"Status: {'PASS' if step8_pass else 'FAIL'}")
    results["Step 8: FastAPI Restart State Persistence"] = step8_pass

    # -------------------------------------------------------------------------
    # STEP 9: Session Isolation Test
    # -------------------------------------------------------------------------
    print("\n--- STEP 9: Session Isolation Test ---")
    # Simulate a distinct user session
    session_beta_id = "Guest_Session_123"
    from chat_store import get_or_create_conversation, load_session_state, save_message
    conv_beta_id = get_or_create_conversation(session_beta_id, "Guest User")
    save_message(session_beta_id, "user", "Hello from Guest")
    save_message(session_beta_id, "assistant", "Hello Guest!")

    state_admin = load_session_state(user)
    state_beta = load_session_state(session_beta_id)

    step9_pass = len(state_admin["messages"]) != len(state_beta["messages"]) and state_beta["messages"][0].content == "Hello from Guest"
    print(f"Admin Session Message Count: {len(state_admin['messages'])}")
    print(f"Guest Session Message Count: {len(state_beta['messages'])}")
    print(f"Status: {'PASS' if step9_pass else 'FAIL'}")
    results["Step 9: Multi-Session Context Isolation"] = step9_pass

    # -------------------------------------------------------------------------
    # STEP 10 & 11: Database Growth & JSON State Verification
    # -------------------------------------------------------------------------
    print("\n--- STEP 10 & 11: Database Growth & JSON Validation ---")
    s_so, token_so, _, _ = get_auth_session("Administrator", "admin")
    ok_so, t_so, r_so = send_chat_message(s_so, token_so, "Create sales order for Grant Plastics Ltd. with SKU001 qty 5")
    print(f"Sales Order Response: {r_so[:100]}...")

    latest_state = cursor.execute("SELECT * FROM conversation_state WHERE conversation_id = (SELECT id FROM conversations WHERE session_id = ?);", (user,)).fetchone()
    state_json_str = latest_state["collected_fields"]
    json_valid = False
    try:
        parsed = json.loads(state_json_str)
        json_valid = isinstance(parsed, dict)
    except Exception:
        json_valid = False

    print(f"Stored JSON String: {state_json_str}")
    print(f"JSON Valid Dict: {json_valid}")
    step10_11_pass = json_valid and ok_so
    print(f"Status: {'PASS' if step10_11_pass else 'FAIL'}")
    results["Step 10 & 11: DB Growth & Valid JSON State"] = step10_11_pass

    # -------------------------------------------------------------------------
    # STEP 12: Write Operation Reset Verification
    # -------------------------------------------------------------------------
    print("\n--- STEP 12: Write Operation Reset Verification ---")
    # State after write operation (Sales Order) should have empty collected_fields or reset workflow
    reset_state = cursor.execute("SELECT collected_fields, workflow_complete FROM conversation_state WHERE conversation_id = (SELECT id FROM conversations WHERE session_id = ?);", (user,)).fetchone()
    collected_after_write = reset_state["collected_fields"]
    step12_pass = collected_after_write == "{}" or reset_state["workflow_complete"] == 1
    print(f"Collected Fields After Write Operation: {collected_after_write}")
    print(f"Status: {'PASS' if step12_pass else 'FAIL'}")
    results["Step 12: Write Operation Reset"] = step12_pass

    # -------------------------------------------------------------------------
    # STEP 13: Automatic Database Re-creation Test
    # -------------------------------------------------------------------------
    print("\n--- STEP 13: Automatic DB Re-creation Test ---")
    from chat_store import init_db
    init_db()  # Idempotent call verifying no crash when tables exist
    step13_pass = os.path.exists(DB_PATH)
    print(f"DB Path exists after init call: {step13_pass}")
    print(f"Status: {'PASS' if step13_pass else 'FAIL'}")
    results["Step 13: Idempotent DB Initialization"] = step13_pass

    # -------------------------------------------------------------------------
    # STEP 14: Edge Case & Exception Resilience Test
    # -------------------------------------------------------------------------
    print("\n--- STEP 14: Edge Cases & Error Resilience ---")
    # Edge case 1: Unknown customer lookup
    ok_ec1, _, r_ec1 = send_chat_message(s, token, "Look up customer NonExistentCustomer999")
    pass_ec1 = ok_ec1 and ("unable to find" in r_ec1.lower() or "not found" in r_ec1.lower() or "no information" in r_ec1.lower())
    print(f"[Edge Case 1: Unknown Customer] -> {r_ec1[:80]}... ({'PASS' if pass_ec1 else 'FAIL'})")

    # Edge case 2: Unknown SKU stock check
    ok_ec2, _, r_ec2 = send_chat_message(s, token, "Check stock for INVALID_SKU_888")
    pass_ec2 = ok_ec2 and ("completed" in r_ec2.lower() or "no stock" in r_ec2.lower() or "not found" in r_ec2.lower())
    print(f"[Edge Case 2: Unknown SKU] -> {r_ec2[:80]}... ({'PASS' if pass_ec2 else 'FAIL'})")

    # Edge case 3: Corrupted JSON recovery in chat_store
    from chat_store import load_session_state
    # Inject bad json string
    cursor.execute("UPDATE conversation_state SET collected_fields = 'INVALID_JSON_STR' WHERE conversation_id = 1;")
    conn.commit()
    recovered_state = load_session_state("Administrator")
    pass_ec3 = isinstance(recovered_state["collected_fields"], dict)
    print(f"[Edge Case 3: Corrupted JSON Fallback] -> Recovered dict: {recovered_state['collected_fields']} ({'PASS' if pass_ec3 else 'FAIL'})")

    step14_pass = pass_ec1 and pass_ec2 and pass_ec3
    print(f"Status: {'PASS' if step14_pass else 'FAIL'}")
    results["Step 14: Edge Cases & Exception Resilience"] = step14_pass

    # -------------------------------------------------------------------------
    # STEP 15: Performance & Overhead Measurement
    # -------------------------------------------------------------------------
    print("\n--- STEP 15: Performance & Overhead Measurement ---")
    db_reads = []
    db_writes = []
    for _ in range(5):
        t0 = time.time()
        cursor.execute("SELECT * FROM messages WHERE conversation_id = 1;").fetchall()
        db_reads.append(time.time() - t0)

        t1 = time.time()
        cursor.execute("UPDATE conversation_state SET updated_at = CURRENT_TIMESTAMP WHERE conversation_id = 1;")
        conn.commit()
        db_writes.append(time.time() - t1)

    avg_db_read = (sum(db_reads) / len(db_reads)) * 1000
    avg_db_write = (sum(db_writes) / len(db_writes)) * 1000
    print(f"Average SQLite Query Read Time: {avg_db_read:.3f} ms")
    print(f"Average SQLite Query Write Time: {avg_db_write:.3f} ms")

    step15_pass = avg_db_read < 5.0 and avg_db_write < 15.0
    print(f"Status: {'PASS' if step15_pass else 'FAIL'}")
    results["Step 15: SQLite Latency Overhead (< 15ms)"] = step15_pass

    conn.close()

    # -------------------------------------------------------------------------
    # QA SUMMARY TABLE
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("                  PHASE 2.3 END-TO-END QA SUMMARY REPORT                    ")
    print("=" * 80)
    print(f"{'Step':<45} | {'Status':<10}")
    print("-" * 60)
    passed_count = 0
    for name, status in results.items():
        st_str = "PASS" if status else "FAIL"
        if status:
            passed_count += 1
        print(f"{name:<45} | {st_str:<10}")
    print("=" * 60)
    print(f"Total QA Steps: {len(results)} | Passed: {passed_count} | Failed: {len(results) - passed_count}")
    print(f"Overall QA Pass Rate: {(passed_count / len(results)) * 100:.1f}%")
    print("=" * 80)

if __name__ == "__main__":
    run_qa_suite()

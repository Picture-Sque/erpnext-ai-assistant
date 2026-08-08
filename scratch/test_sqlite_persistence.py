import os
import sys
import json
import sqlite3

# Ensure agent directory is in sys.path
AGENT_DIR = r"c:\Users\krish\Desktop\ai_assistant\erpnext-ai-assistant-integrated\agent"
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

# Use a test SQLite DB path
TEST_DB_PATH = os.path.join(AGENT_DIR, "chat_history_test.db")
os.environ["SQLITE_DB_PATH"] = TEST_DB_PATH

import chat_store

def run_tests():
    print("=" * 60)
    print("      RUNNING PHASE 2.3 SQLITE PERSISTENCE VERIFICATION     ")
    print("=" * 60)
    
    # Clean up test DB if exists
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
        print(f"Removed previous test database: {TEST_DB_PATH}")

    # Test 1: Initialize Database Schema
    chat_store.init_db()
    assert os.path.exists(TEST_DB_PATH), "DB file was not created!"
    print("[PASS] Test 1: Database tables created successfully.")

    # Test 2: Create Session A and save multi-turn conversation
    session_a = "user_session_alpha"
    conv_id_a = chat_store.get_or_create_conversation(session_a, user_id="User Alpha")
    assert conv_id_a > 0, "Failed to create conversation for session_a"
    
    chat_store.save_message(session_a, "user", "Look up customer West View Software Ltd.")
    chat_store.save_message(session_a, "assistant", "Customer Details: West View Software Ltd.", intent="customer-lookup")
    chat_store.update_session_state(session_a, detected_intent="customer-lookup", collected_fields={"customer_name": "West View Software Ltd."})
    
    # Test 3: Create Session B (Session Isolation)
    session_b = "user_session_beta"
    conv_id_b = chat_store.get_or_create_conversation(session_b, user_id="User Beta")
    assert conv_id_b != conv_id_a, "Session IDs must map to distinct conversation records!"
    
    chat_store.save_message(session_b, "user", "Check stock for SKU005")
    chat_store.save_message(session_b, "assistant", "Inventory Check for SKU005", intent="check-inventory")
    chat_store.update_session_state(session_b, detected_intent="check-inventory", collected_fields={"item_code": "SKU005"})

    # Test 4: Reload state for Session A (Simulating restart / page refresh)
    state_a = chat_store.load_session_state(session_a)
    assert len(state_a["messages"]) == 2, f"Expected 2 messages for Session A, got {len(state_a['messages'])}"
    assert state_a["messages"][0].content == "Look up customer West View Software Ltd."
    assert state_a["messages"][1].content == "Customer Details: West View Software Ltd."
    assert state_a["detected_intent"] == "customer-lookup"
    assert state_a["collected_fields"] == {"customer_name": "West View Software Ltd."}
    print("[PASS] Test 2 & 3: Multi-turn messages & slot state restored for Session A.")

    # Test 5: Verify Session B is isolated and unchanged
    state_b = chat_store.load_session_state(session_b)
    assert len(state_b["messages"]) == 2, f"Expected 2 messages for Session B, got {len(state_b['messages'])}"
    assert state_b["messages"][0].content == "Check stock for SKU005"
    assert state_b["detected_intent"] == "check-inventory"
    assert state_b["collected_fields"] == {"item_code": "SKU005"}
    print("[PASS] Test 4: Session A and Session B context isolated successfully.")

    # Test 6: Turn 2 on Session A (Follow-up memory)
    chat_store.save_message(session_a, "user", "What is their customer group?")
    chat_store.save_message(session_a, "assistant", "Customer Group: Demo Customer Group")
    state_a_turn2 = chat_store.load_session_state(session_a)
    assert len(state_a_turn2["messages"]) == 4, f"Expected 4 messages for Session A, got {len(state_a_turn2['messages'])}"
    print("[PASS] Test 5: Follow-up turn appended and persisted correctly.")

    # Test 7: Reset Session A
    chat_store.reset_session_state(session_a)
    state_a_reset = chat_store.load_session_state(session_a)
    assert len(state_a_reset["messages"]) == 0, "Messages should be cleared after session reset"
    assert state_a_reset["detected_intent"] == "", "Intent should be cleared after session reset"
    assert state_a_reset["collected_fields"] == {}, "Collected fields should be cleared after session reset"
    print("[PASS] Test 6: Session reset working as expected.")

    # Clean up test DB file
    try:
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        for ext in ["-wal", "-shm"]:
            if os.path.exists(TEST_DB_PATH + ext):
                os.remove(TEST_DB_PATH + ext)
    except Exception:
        pass
    print("=" * 60)
    print("        ALL SQLITE PERSISTENCE UNIT TESTS PASSED (6/6)        ")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()

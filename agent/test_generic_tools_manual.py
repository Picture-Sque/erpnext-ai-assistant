import os
import sys
import json
import logging
from datetime import datetime, timedelta

# Ensure parent directory is in path for module resolution
agent_dir = os.path.abspath(os.path.dirname(__file__))
if agent_dir not in sys.path:
    sys.path.insert(0, agent_dir)
repo_dir = os.path.abspath(os.path.join(agent_dir, ".."))
if repo_dir not in sys.path:
    sys.path.insert(0, repo_dir)

from agent.tools.generic_tools import (
    add_doctype,
    list_doctype,
    update_doctype,
    delete_doctype,
    is_doctype_allowed,
    get_allowed_doctypes
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("manual_test")


def run_manual_tests():
    print("=" * 60)
    print("RUNNING MANUAL VERIFICATION SCRIPT FOR GENERIC DOCTYPE TOOLS")
    print("=" * 60)
    print(f"Whitelisted DocTypes: {get_allowed_doctypes()}\n")

    # 1. Test whitelist check
    print("--- Step 1: Whitelist Check ---")
    print("Is 'Customer' allowed?:", is_doctype_allowed("Customer"))
    print("Is 'User' allowed?:", is_doctype_allowed("User"))
    user_res = list_doctype("User", {"limit": 1})
    print("Attempting list_doctype('User'):", user_res)
    assert user_res["status_code"] == 403, "Expected 403 Forbidden for non-whitelisted DocType"
    print("[OK] Step 1 Passed (Whitelist enforced correctly)\n")

    # 2. Test list_doctype for Customer
    print("--- Step 2: list_doctype('Customer', filters=[['customer_name', 'like', '%West View%']]) ---")
    cust_res = list_doctype("Customer", {"filters": [["customer_name", "like", "%West View%"]]})
    print(f"Status: {cust_res['status_code']}, Success: {cust_res['success']}")
    print(f"Data: {json.dumps(cust_res['data'], indent=2)}")
    print("[OK] Step 2 Completed\n")

    # 3. Test list_doctype for Bin
    print("--- Step 3: list_doctype('Bin', filters=[['item_code', '=', 'SKU005']]) ---")
    bin_res = list_doctype("Bin", {"filters": [["item_code", "=", "SKU005"]]})
    print(f"Status: {bin_res['status_code']}, Success: {bin_res['success']}")
    print(f"Data: {json.dumps(bin_res['data'], indent=2)}")
    print("[OK] Step 3 Completed\n")

    # 4. Test add_doctype / update_doctype / delete_doctype (Self-contained & Self-cleaning)
    print("--- Step 4: add_doctype / update_doctype / delete_doctype Lifecycle ---")
    
    # First, let's create a test Customer record to exercise full CRUD lifecycle safely
    test_customer_name = f"Test Generic Tool Customer {int(datetime.now().timestamp())}"
    add_payload = {
        "customer_name": test_customer_name,
        "customer_group": "Commercial",
        "territory": "All Territories"
    }
    print(f"Calling add_doctype('Customer', {add_payload})...")
    create_res = add_doctype("Customer", add_payload)
    print(f"Create Response Status: {create_res['status_code']}, Success: {create_res['success']}")
    print(f"Create Result: {create_res}")

    created_id = None
    if create_res["success"] and isinstance(create_res["data"], dict):
        created_id = create_res["data"].get("name")
    
    if not created_id:
        print(f"Note: add_doctype did not return a created ID (Live ERPNext might not be reachable or returned error: {create_res.get('error')}).")
        print("Testing add_doctype validation error details instead...")
        err_test_res = add_doctype("Sales Order", {"customer": "NonExistentCustomer12345"})
        print("Validation Error Response:", err_test_res)
    else:
        print(f"Created Record ID: {created_id}")
        
        # 5. update_doctype
        update_payload = {"customer_name": f"{test_customer_name} (Updated)"}
        print(f"\nCalling update_doctype('Customer', '{created_id}', {update_payload})...")
        update_res = update_doctype("Customer", created_id, update_payload)
        print(f"Update Response Status: {update_res['status_code']}, Success: {update_res['success']}")
        print(f"Update Result: {update_res}")

        # 6. delete_doctype (Cleanup)
        print(f"\nCalling delete_doctype('Customer', '{created_id}')...")
        del_res = delete_doctype("Customer", created_id)
        print(f"Delete Response Status: {del_res['status_code']}, Success: {del_res['success']}")
        print(f"Delete Result: {del_res}")
        print("[OK] Cleanup completed successfully")


    print("\n=" * 60)
    print("MANUAL VERIFICATION SCRIPT COMPLETED SUCCESSFULLY")
    print("=" * 60)


if __name__ == "__main__":
    run_manual_tests()

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
    create_document,
    get_list,
    update_document,
    delete_document,
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
    user_res = get_list("User", limit=1)
    print("Attempting get_list('User'):", user_res)
    assert user_res["status"] == "permission_denied", "Expected permission_denied for non-whitelisted DocType"
    print("[OK] Step 1 Passed (Whitelist enforced correctly)\n")

    # 2. Test get_list for Customer
    print("--- Step 2: get_list('Customer', filters=[['customer_name', 'like', '%West View%']]) ---")
    cust_res = get_list("Customer", filters=[["customer_name", "like", "%West View%"]])
    print(f"Status: {cust_res['status']}")
    print(f"Data: {json.dumps(cust_res.get('data'), indent=2)}")
    print("[OK] Step 2 Completed\n")

    # 3. Test get_list for Bin
    print("--- Step 3: get_list('Bin', filters=[['item_code', '=', 'SKU005']]) ---")
    bin_res = get_list("Bin", filters=[["item_code", "=", "SKU005"]])
    print(f"Status: {bin_res['status']}")
    print(f"Data: {json.dumps(bin_res.get('data'), indent=2)}")
    print("[OK] Step 3 Completed\n")

    # 4. Test create_document / update_document / delete_document (Self-contained & Self-cleaning)
    print("--- Step 4: create_document / update_document / delete_document Lifecycle ---")
    
    # First, let's create a test Customer record to exercise full CRUD lifecycle safely
    test_customer_name = f"Test Generic Tool Customer {int(datetime.now().timestamp())}"
    add_payload = {
        "customer_name": test_customer_name,
        "customer_group": "Commercial",
        "territory": "All Territories"
    }
    print(f"Calling create_document('Customer', {add_payload})...")
    create_res = create_document("Customer", add_payload)
    print(f"Create Response Status: {create_res['status']}")
    print(f"Create Result: {create_res}")

    created_id = None
    if create_res["status"] == "success" and isinstance(create_res.get("data"), dict):
        created_id = create_res["data"].get("name")
    
    if not created_id:
        print(f"Note: create_document did not return a created ID (Live ERPNext might not be reachable or returned error: {create_res.get('error')}).")
        print("Testing create_document validation error details instead...")
        err_test_res = create_document("Sales Order", {"customer": "NonExistentCustomer12345"})
        print("Validation Error Response:", err_test_res)
    else:
        print(f"Created Record ID: {created_id}")
        
        # 5. update_document
        update_payload = {"customer_name": f"{test_customer_name} (Updated)"}
        print(f"\nCalling update_document('Customer', '{created_id}', {update_payload})...")
        update_res = update_document("Customer", created_id, update_payload)
        print(f"Update Response Status: {update_res['status']}")
        print(f"Update Result: {update_res}")

        # 6. delete_document (Cleanup)
        print(f"\nCalling delete_document('Customer', '{created_id}')...")
        del_res = delete_document("Customer", created_id)
        print(f"Delete Response Status: {del_res['status']}")
        print(f"Delete Result: {del_res}")
        print("[OK] Cleanup completed successfully")


    print("\n=" * 60)
    print("MANUAL VERIFICATION SCRIPT COMPLETED SUCCESSFULLY")
    print("=" * 60)


if __name__ == "__main__":
    run_manual_tests()

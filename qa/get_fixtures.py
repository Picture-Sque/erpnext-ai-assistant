import requests
import json

base_url = "http://localhost:8081/api/resource"
headers = {
    "Authorization": "token ae634fa20ec01f2:d0177e9ff71cfca"
}

def get_data():
    # 1. Sales Order ID
    res = requests.get(f"{base_url}/Sales Order?limit_page_length=1", headers=headers)
    so_id = res.json()["data"][0]["name"]
    print("Sales Order ID:", so_id)
    
    # 2. Purchase Order ID
    res = requests.get(f"{base_url}/Purchase Order?limit_page_length=1", headers=headers)
    po_id = res.json()["data"][0]["name"] if res.json().get("data") else "None"
    print("Purchase Order ID:", po_id)
    
    # 3. Check POs for ITEM-DESK-001
    res = requests.get(f"{base_url}/Purchase Order Item?filters=[[\"item_code\", \"=\", \"ITEM-DESK-001\"]]", headers=headers)
    has_po = len(res.json()["data"]) > 0
    print("ITEM-DESK-001 has PO?:", has_po)
    
    # 4. Month with zero sales
    months = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
    for m in months:
        res = requests.get(f"{base_url}/Sales Order?filters=[[\"transaction_date\", \"like\", \"2026-{m}-%\"]]", headers=headers)
        if len(res.json()["data"]) == 0:
            print("Month with zero sales in 2026:", f"2026-{m}")
            break

if __name__ == "__main__":
    get_data()

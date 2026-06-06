import uuid
import sys
from supabase import create_client

SUPABASE_URL = input("Supabase URL: ").strip()
SUPABASE_KEY = input("Supabase service_role key: ").strip()
name = input("Device name: ").strip()
phone = input("Phone number (optional, press enter to skip): ").strip() or None

db = create_client(SUPABASE_URL, SUPABASE_KEY)
token = str(uuid.uuid4()).replace("-", "")
res = db.table("devices").insert({
    "name": name,
    "phone_number": phone,
    "token": token,
}).execute()

print(f"\nDevice registered!")
print(f"Name  : {name}")
print(f"Token : {token}")
print(f"\nPut this token in the Flutter app's SmsReceiver.kt and StatusService.kt")

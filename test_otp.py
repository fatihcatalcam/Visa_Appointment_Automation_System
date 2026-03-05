from config.database import GlobalSettingsRepository, init_db
from bot.email_reader import OTPReader
from config.security import _decrypt
import logging

logging.basicConfig(level=logging.INFO)

init_db()

users = GlobalSettingsRepository.get_all_users()
if users:
    u = users[0]
    email = u.get("email")
    enc_pwd = u.get("email_app_password")
    if email and enc_pwd:
        pwd = _decrypt(enc_pwd)
        print(f"Testing IMAP for {email}...")
        try:
            reader = OTPReader(email, pwd)
            reader.connect()
            print("IMAP Login Successful.")
            # Search for recent emails (last 10)
            status, messages = reader.mail.search(None, 'ALL')
            if status == "OK" and messages[0]:
                msg_ids = messages[0].split()
                print(f"Found {len(msg_ids)} total emails. Checking the last one...")
                latest_id = msg_ids[-1]
                status, data = reader.mail.fetch(latest_id, '(RFC822)')
                if status == "OK":
                    print("Successfully fetched the latest email content.")
                    otp = reader._parse_otp(data)
                    print(f"Extracted OTP: {otp}")
            else:
                print("No emails found in INBOX.")
            reader.close()
        except Exception as e:
            print(f"IMAP Error: {e}")
    else:
        print("Missing email or app password in database.")
else:
    print("No users in db.")

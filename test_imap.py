import imaplib
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def test_imap(email, password, server="outlook.office365.com"):
    logging.info(f"Connecting to {server} for {email}...")
    try:
        mail = imaplib.IMAP4_SSL(server, 993)
        mail.login(email, password)
        logging.info("SUCCESS! Login accepted.")
        mail.logout()
        return True
    except imaplib.IMAP4.error as e:
        logging.error(f"IMAP Auth Error: {e}")
        return False
    except Exception as e:
        logging.error(f"Connection Error: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python test_imap.py <email> <password>")
        sys.exit(1)
    
    email = sys.argv[1]
    password = " ".join(sys.argv[2:]) # Handle passwords with spaces if passed without quotes
    password = password.replace(" ", "") # Remove spaces as Google App Passwords don't need them
    
    server = "outlook.office365.com"
    if "@gmail.com" in email.lower():
        server = "imap.gmail.com"
        
    logging.info(f"Target Server Detected: {server}")
    success = test_imap(email, password, server)
    
    if not success and "@outlook" in email or "@hotmail" in email:
        # Try legacy hotmail
        logging.info("Trying legacy imap-mail.outlook.com...")
        test_imap(email, password, "imap-mail.outlook.com")


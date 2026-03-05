import os
import base64
import logging

logger = logging.getLogger(__name__)

SECRET_KEY_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', '.secret_key')

_fernet = None

def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet
    try:
        from cryptography.fernet import Fernet
        os.makedirs(os.path.dirname(SECRET_KEY_PATH), exist_ok=True)
        if os.path.exists(SECRET_KEY_PATH):
            with open(SECRET_KEY_PATH, 'rb') as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            with open(SECRET_KEY_PATH, 'wb') as f:
                f.write(key)
            logger.info("🔑 Yeni şifreleme anahtarı oluşturuldu.")
        _fernet = Fernet(key)
    except ImportError:
        logger.warning("cryptography kütüphanesi yok — Base64 fallback kullanılıyor.")
        _fernet = None
    return _fernet

def _encrypt(text: str) -> str:
    if not text: return ""
    f = _get_fernet()
    if f:
        try:
            return f.encrypt(text.encode('utf-8')).decode('utf-8')
        except Exception:
            pass
    return base64.b64encode(text.encode('utf-8')).decode('utf-8')

def _decrypt(text: str) -> str:
    if not text: return ""
    f = _get_fernet()
    if f:
        try:
            return f.decrypt(text.encode('utf-8')).decode('utf-8')
        except Exception:
            pass
    try:
        return base64.b64decode(text.encode('utf-8')).decode('utf-8')
    except Exception:
        return text

# Legacy aliases
_simple_encode = _encrypt
_simple_decode = _decrypt

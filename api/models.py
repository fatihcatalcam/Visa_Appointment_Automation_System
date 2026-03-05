from pydantic import BaseModel, Field
from typing import Optional

class UserCreateUpdate(BaseModel):
    is_active: bool = True
    email: str
    password_enc: str = "" # Plaintext password sent from UI, API will encrypt it before DB
    email_app_password: Optional[str] = ""
    travel_date: Optional[str] = ""
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    phone: Optional[str] = ""
    jurisdiction: Optional[str] = ""
    location: Optional[str] = ""
    category: Optional[str] = ""
    appointment_for: str = "Individual"
    visa_type: Optional[str] = ""
    visa_sub_type: Optional[str] = ""
    proxy_address: Optional[str] = ""
    check_interval: int = 60
    minimum_days: int = 0
    headless: bool = True
    is_scout: bool = False
    auto_book: bool = False

class GlobalSettingUpdate(BaseModel):
    key: str
    value: str

from typing import Dict

class GlobalSettingsBulkUpdate(BaseModel):
    settings: Dict[str, str]

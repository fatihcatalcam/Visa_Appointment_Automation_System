import customtkinter as ctk
from config.database import add_or_update_user, set_global_setting, get_global_setting
from bot.notifier import DiscordNotifier, CallMeBotNotifier, Notifier
import threading

class UserForm(ctk.CTkToplevel):
    def __init__(self, master=None, user_data=None):
        super().__init__(master)
        self.user_id = user_data.get("id") if user_data else None
        self.title("Müşteri Düzenle" if self.user_id else "Müşteri / Hesap Ekle")
        self.geometry("700x750")
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))
        
        self.scroll_frame = ctk.CTkScrollableFrame(self)
        self.scroll_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        # --- BLS Hesap Bilgileri ---
        ctk.CTkLabel(self.scroll_frame, text="Hesap Bilgileri", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(0, 10))
        
        self.first_name = ctk.CTkEntry(self.scroll_frame, placeholder_text="Müşteri Adı")
        self.first_name.pack(fill="x", pady=5)
        
        self.last_name = ctk.CTkEntry(self.scroll_frame, placeholder_text="Müşteri Soyadı")
        self.last_name.pack(fill="x", pady=5)
        
        self.email = ctk.CTkEntry(self.scroll_frame, placeholder_text="BLS Email Adresi")
        self.email.pack(fill="x", pady=5)
        
        self.password = ctk.CTkEntry(self.scroll_frame, placeholder_text="BLS Şifresi")
        self.password.pack(fill="x", pady=5)
        
        self.phone = ctk.CTkEntry(self.scroll_frame, placeholder_text="Telefon (Kayıt için: 5551234567)")
        self.phone.pack(fill="x", pady=5)
        
        # --- Randevu Hedefleri ---
        ctk.CTkLabel(self.scroll_frame, text="Randevu Ayarları", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(20, 10))
        
        self.jurisdiction = ctk.CTkEntry(self.scroll_frame, placeholder_text="İl / Jurisdiction (ör: Istanbul)")
        self.jurisdiction.pack(fill="x", pady=5)
        
        self.location = ctk.CTkEntry(self.scroll_frame, placeholder_text="Konum / Location (ör: Istanbul, Antalya)")
        self.location.pack(fill="x", pady=5)
        
        self.visa_type = ctk.CTkEntry(self.scroll_frame, placeholder_text="Vize Türü (ör: Schengen Visa)")
        self.visa_type.pack(fill="x", pady=5)
        
        self.category = ctk.CTkEntry(self.scroll_frame, placeholder_text="Kategori (Örn: Tourism, Business)")
        self.category.pack(fill="x", pady=5)
        
        self.visa_sub_type = ctk.CTkEntry(self.scroll_frame, placeholder_text="Alt Tür / Sub Type (Opsiyonel / Gerekliyse)")
        self.visa_sub_type.pack(fill="x", pady=5)
        
        self.appointment_for = ctk.CTkComboBox(self.scroll_frame, values=["Individual", "Family"])
        self.appointment_for.pack(fill="x", pady=5)
        
        self.minimum_days = ctk.CTkEntry(self.scroll_frame, placeholder_text="Hedef Gün (Şu an = 0)")
        self.minimum_days.pack(fill="x", pady=5)
        
        self.check_interval = ctk.CTkEntry(self.scroll_frame, placeholder_text="Kontrol Aralığı (Saniye) - Örn: 60")
        self.check_interval.pack(fill="x", pady=5)
        
        # --- Proxy & Altyapı ---
        ctk.CTkLabel(self.scroll_frame, text="Altyapı (Proxy & Tarayıcı)", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(20, 10))
        
        self.headless_var = ctk.IntVar(value=1)
        self.headless_switch = ctk.CTkSwitch(self.scroll_frame, text="Arka Planda Gizli Çalış (Headless)", variable=self.headless_var)
        self.headless_switch.pack(anchor="w", pady=5)
        
        self.is_scout_var = ctk.IntVar(value=0)
        self.is_scout_switch = ctk.CTkSwitch(self.scroll_frame, text="🎯 İZCİ HESABI (Sadece Global Scout Açıksa Tarar)", variable=self.is_scout_var)
        self.is_scout_switch.pack(anchor="w", pady=5)
        
        self.auto_book_var = ctk.IntVar(value=0)
        self.auto_book_switch = ctk.CTkSwitch(self.scroll_frame, text="📌 OTOMATIİK RANDEVU AL (Bulunca hemen rezerve et)", variable=self.auto_book_var)
        self.auto_book_switch.pack(anchor="w", pady=5)
        
        self.proxy = ctk.CTkEntry(self.scroll_frame, placeholder_text="Proxy (user:pass@ip:port) - Boş bırakırsanız sunucu IP'sini kullanır")
        self.proxy.pack(fill="x", pady=5)
        
        if user_data:
            self.first_name.insert(0, user_data.get("first_name") or "")
            self.last_name.insert(0, user_data.get("last_name") or "")
            self.email.insert(0, user_data.get("email") or "")
            
            from config.database import _decrypt
            self.password.insert(0, _decrypt(user_data.get("password_enc") or ""))
            
            self.phone.insert(0, user_data.get("phone") or "")
            self.jurisdiction.insert(0, user_data.get("jurisdiction") or "")
            self.location.insert(0, user_data.get("location") or "")
            self.visa_type.insert(0, user_data.get("visa_type") or "")
            self.category.insert(0, user_data.get("category") or "")
            self.visa_sub_type.insert(0, user_data.get("visa_sub_type") or "")
            self.appointment_for.set(user_data.get("appointment_for") or "Individual")
            self.minimum_days.insert(0, str(user_data.get("minimum_days") or 0))
            self.check_interval.insert(0, str(user_data.get("check_interval") or 60))
            self.proxy.insert(0, user_data.get("proxy_address") or "")
            self.headless_var.set(int(user_data.get("headless", 1)))
            self.is_scout_var.set(int(user_data.get("is_scout", 0)))
            self.auto_book_var.set(int(user_data.get("auto_book", 0)))
        else:
            self.appointment_for.set("Individual")
            self.headless_var.set(0)
            self.is_scout_var.set(0)
            self.auto_book_var.set(0)
        
        # --- Kaydet ---
        ctk.CTkButton(self.scroll_frame, text="💾 Müşteriyi Kaydet", fg_color="#27ae60", hover_color="#2ecc71", 
                      command=self._save_user).pack(pady=30)
                      
        # --- GLOBAL AYARLAR (Admin) ---
        ctk.CTkLabel(self.scroll_frame, text="Global API Ayarları (Tüm Botlar İçin Geçerli)", text_color="#f1c40f", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(30, 10))
        
        self.api_2captcha = ctk.CTkEntry(self.scroll_frame, placeholder_text="2Captcha API Key")
        self.api_2captcha.insert(0, get_global_setting("2captcha_key"))
        self.api_2captcha.pack(fill="x", pady=5)
        
        discord_frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        discord_frame.pack(fill="x", pady=5)
        self.discord_webhook = ctk.CTkEntry(discord_frame, placeholder_text="Discord Webhook URL")
        self.discord_webhook.insert(0, get_global_setting("discord_webhook"))
        self.discord_webhook.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ctk.CTkButton(discord_frame, text="Test", width=60, command=self._test_discord).pack(side="right")
        
        self.telegram_username = ctk.CTkEntry(self.scroll_frame, placeholder_text="Telegram Kullanıcı Adı (Örn: @fatih)")
        self.telegram_username.insert(0, get_global_setting("telegram_username"))
        self.telegram_username.pack(fill="x", pady=5)
        
        tele_frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        tele_frame.pack(fill="x", pady=5)
        self.telegram_apikey = ctk.CTkEntry(tele_frame, placeholder_text="Telegram API Key (CallMeBot)")
        self.telegram_apikey.insert(0, get_global_setting("telegram_apikey"))
        self.telegram_apikey.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ctk.CTkButton(tele_frame, text="Test", width=60, command=self._test_telegram).pack(side="right")
        
        # Scout Mode Toggle
        self.scout_mode_var = ctk.IntVar(value=int(get_global_setting("scout_mode", "0")))
        self.scout_mode_switch = ctk.CTkSwitch(self.scroll_frame, text="Scout Modu (İzci) Aktif (Merkezi Tarama)", variable=self.scout_mode_var)
        self.scout_mode_switch.pack(anchor="w", pady=(15, 5))

        # B5: Active Hours
        self.active_hours = ctk.CTkEntry(self.scroll_frame, placeholder_text="Aktif Saatler (Örn: 08:00-23:00) - Boş = 7/24")
        self.active_hours.insert(0, get_global_setting("active_hours", ""))
        self.active_hours.pack(fill="x", pady=5)

        # Interactive Telegram Bot Settings
        ctk.CTkLabel(self.scroll_frame, text="Telegram Interaktif Bot (BotFather)", text_color="#2ecc71", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(15, 5))
        
        self.telegram_bot_token = ctk.CTkEntry(self.scroll_frame, placeholder_text="Bot Token (BotFather)")
        self.telegram_bot_token.insert(0, get_global_setting("telegram_bot_token"))
        self.telegram_bot_token.pack(fill="x", pady=5)
        
        self.telegram_admin_id = ctk.CTkEntry(self.scroll_frame, placeholder_text="Yetkili Telegram ID'leri (Örn: 1234567,9876543) [Boş = Herkese Açık]")
        self.telegram_admin_id.insert(0, get_global_setting("telegram_admin_id"))
        self.telegram_admin_id.pack(fill="x", pady=5)

        ctk.CTkButton(self.scroll_frame, text="💾 Global Ayarları Kaydet", fg_color="#f39c12", hover_color="#e67e22", 
                      command=self._save_globals).pack(pady=(20, 10))
                      
        ctk.CTkButton(self.scroll_frame, text="🔔 Masaüstü Bildirimi ve Ses Testi", fg_color="#3498db", hover_color="#2980b9", 
                      command=self._test_desktop).pack(pady=(0, 10))

    def _save_user(self):
        user_data = {
            "first_name": self.first_name.get().strip(),
            "last_name": self.last_name.get().strip(),
            "email": self.email.get().strip(),
            "password": self.password.get().strip(),
            "phone": self.phone.get().strip(),
            "jurisdiction": self.jurisdiction.get().strip(),
            "location": self.location.get().strip(),
            "visa_type": self.visa_type.get().strip(),
            "category": self.category.get().strip(),
            "visa_sub_type": self.visa_sub_type.get().strip(),
            "appointment_for": self.appointment_for.get().strip(),
            "minimum_days": int(self.minimum_days.get().strip() or 0),
            "check_interval": int(self.check_interval.get().strip() or 60),
            "proxy_address": self.proxy.get().strip(),
            "headless": self.headless_var.get(),
            "is_scout": self.is_scout_var.get(),
            "auto_book": self.auto_book_var.get(),
            "status": "Idle"
        }
        
        if self.user_id:
            user_data["id"] = self.user_id
        
        if not user_data["email"] or not user_data["password"]:
            print("Hata: Email ve Şifre zorunlu!")
            return
            
        add_or_update_user(user_data)
        if self.master and hasattr(self.master, '_refresh_table'):
            self.master._refresh_table()
        self.destroy()

    def _save_globals(self):
        set_global_setting("2captcha_key", self.api_2captcha.get().strip())
        set_global_setting("discord_webhook", self.discord_webhook.get().strip())
        set_global_setting("telegram_username", self.telegram_username.get().strip())
        set_global_setting("telegram_apikey", self.telegram_apikey.get().strip())
        set_global_setting("telegram_bot_token", self.telegram_bot_token.get().strip())
        set_global_setting("telegram_admin_id", self.telegram_admin_id.get().strip())
        set_global_setting("scout_mode", str(self.scout_mode_var.get()))
        set_global_setting("active_hours", self.active_hours.get().strip())
        print("Global ayarlar kaydedildi. (Telegram Bot değişikliğinin aktif olması için programı yeniden başlatın)")
        
        from tkinter import messagebox
        messagebox.showinfo("Bilgi", "Ayarlar kaydedildi.\nTelegram Bot Token'i değiştiyse programı yeniden başlatmalısınız.")
        self.destroy()

    def _test_discord(self):
        webhook = self.discord_webhook.get().strip()
        if not webhook:
            from tkinter import messagebox
            messagebox.showwarning("Uyarı", "Discord Webhook adresi boş!")
            return
        threading.Thread(target=DiscordNotifier(webhook).send_message, args=("🔔 BLS Bot Test Bildirimi: Bağlantı Başarılı!",), daemon=True).start()

    def _test_telegram(self):
        username = self.telegram_username.get().strip()
        apikey = self.telegram_apikey.get().strip()
        if not username or not apikey:
            from tkinter import messagebox
            messagebox.showwarning("Uyarı", "Telegram kullanıcı adı veya API Key boş!")
            return
        threading.Thread(target=CallMeBotNotifier(username, apikey).send_message, args=("🔔 BLS Bot Test Bildirimi: Bağlantı Başarılı!",), daemon=True).start()

    def _test_desktop(self):
        notifier = Notifier()
        notifier.notify_appointment_found(["Test Tarihi 1", "Test Tarihi 2"], sound=True, desktop=True)
        self.after(3000, notifier.stop_alarm)

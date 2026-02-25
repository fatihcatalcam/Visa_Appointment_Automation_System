import customtkinter as ctk
import tkinter as tk
from tkinter import ttk, Menu, messagebox
import queue
import logging
from bot.manager import BotManager
from config.database import get_all_users, get_user_by_id, delete_user

class DashboardWindow(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("BLS Multi-Bot Manager (comp-bot)")
        self.geometry("1400x800")
        
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        self.log_queue = queue.Queue()
        self.manager = BotManager(self.log_queue)
        
        self._build_ui()
        self._setup_logging()
        self._refresh_table()
        self._poll_logs()
        self._start_telegram_daemon()

    def _start_telegram_daemon(self):
        try:
            from bot.telegram_controller import TelegramBotDaemon
            from config.database import get_global_setting
            token = get_global_setting("telegram_bot_token", "").strip()
            if token:
                self.tg_daemon = TelegramBotDaemon(self.manager)
                self.tg_daemon.start()
                # B4: Register for proactive alerts
                from bot.telegram_controller import register_daemon
                register_daemon(self.tg_daemon)
                logging.info("Telegram Etkileşimli Bot Başlatıldı.")
            else:
                self.tg_daemon = None
        except Exception as e:
            logging.error(f"Telegram Bot Başlatılamadı: {e}")
            self.tg_daemon = None

    def _build_ui(self):
        # Sol Panel (Loglar)
        self.left_panel = ctk.CTkFrame(self, width=400)
        self.left_panel.pack(side="left", fill="y", padx=10, pady=10)
        
        ctk.CTkLabel(self.left_panel, text="Canlı Log Akışı", font=ctk.CTkFont(weight="bold", size=16)).pack(pady=10)
        
        self.log_text = ctk.CTkTextbox(self.left_panel, state="disabled", wrap="word", width=380, font=("Consolas", 11))
        self.log_text.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Tag Configs for Logs
        self.log_text.tag_config("time", foreground="#00e5ff")
        self.log_text.tag_config("info", foreground="#cccccc")
        self.log_text.tag_config("error", foreground="#e74c3c")
        self.log_text.tag_config("success", foreground="#2ecc71")
        
        # Sağ Panel (Tablo & Kontroller)
        self.right_panel = ctk.CTkFrame(self)
        self.right_panel.pack(side="right", fill="both", expand=True, padx=(0, 10), pady=10)
        
        # Üst Butonlar (Satır 1)
        self.top_bar = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        self.top_bar.pack(fill="x", padx=10, pady=(10, 5))
        
        btn_add = ctk.CTkButton(self.top_bar, text="➕ Müşteri/Hesap Ekle", command=self._open_user_form)
        btn_add.pack(side="left", padx=5)
        
        btn_start_all = ctk.CTkButton(self.top_bar, text="▶️ Tümünü Başlat", fg_color="#27ae60", hover_color="#2ecc71", command=self._start_all)
        btn_start_all.pack(side="left", padx=5)
        
        btn_stop_all = ctk.CTkButton(self.top_bar, text="⏸️ Durdur (Yumuşak)", fg_color="#e67e22", hover_color="#d35400", command=self._stop_all)
        btn_stop_all.pack(side="left", padx=5)
        
        btn_kill_all = ctk.CTkButton(self.top_bar, text="🛑 Acil Kapat", fg_color="#c0392b", hover_color="#e74c3c", command=self._kill_all)
        btn_kill_all.pack(side="left", padx=5)
        
        # Excel Butonları
        btn_import = ctk.CTkButton(self.top_bar, text="📥 Excel İçe Aktar", fg_color="#8e44ad", hover_color="#9b59b6", command=self._import_excel)
        btn_import.pack(side="left", padx=5)
        
        btn_export = ctk.CTkButton(self.top_bar, text="📤 Excel Dışa Aktar", fg_color="#2980b9", hover_color="#3498db", command=self._export_excel)
        btn_export.pack(side="left", padx=5)
        
        ctk.CTkButton(self.top_bar, text="🔄 Yenile", fg_color="#f39c12", hover_color="#e67e22", command=self._refresh_table, width=80).pack(side="right", padx=5)

        # Telemetry Stats (Satır 2 - Ayrı Bar)
        self.stats_bar = ctk.CTkFrame(self.right_panel, fg_color="#2b2b2b", corner_radius=5)
        self.stats_bar.pack(fill="x", padx=10, pady=(0, 10))
        
        # C2: Redis Health Indicator (sol tarafta)
        self.lbl_redis_status = ctk.CTkLabel(self.stats_bar, text="", font=ctk.CTkFont(weight="bold", size=13))
        self.lbl_redis_status.pack(side="left", padx=15, pady=5)
        
        self.lbl_active_bots = ctk.CTkLabel(self.stats_bar, text=" Aktif Bot: 0 ", font=ctk.CTkFont(weight="bold"))
        self.lbl_active_bots.pack(side="right", padx=15, pady=5)
        
        self.lbl_bad_proxies = ctk.CTkLabel(self.stats_bar, text=" Bloke Proxy: 0 ", font=ctk.CTkFont(weight="bold"), text_color="#f39c12")
        self.lbl_bad_proxies.pack(side="right", padx=15, pady=5)
        
        self.lbl_cooldowns = ctk.CTkLabel(self.stats_bar, text=" Cooldown: 0 ", font=ctk.CTkFont(weight="bold"), text_color="#e74c3c")
        self.lbl_cooldowns.pack(side="right", padx=15, pady=5)
        
        # Tablo
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", 
                        background="#2b2b2b", foreground="white", rowheight=30, 
                        fieldbackground="#2b2b2b", font=("Segoe UI", 11))
        style.map('Treeview', background=[('selected', '#3498db')])
        style.configure("Treeview.Heading", font=("Segoe UI", 12, "bold"), background="#1e1e1e", foreground="white")

        columns = ("id", "name", "category", "target", "proxy", "status", "last_check", "check_count")
        self.tree = ttk.Treeview(self.right_panel, columns=columns, show="headings")
        
        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="Müşteri Adı")
        self.tree.heading("category", text="Kategori")
        self.tree.heading("target", text="Hedef Gün")
        self.tree.heading("proxy", text="Proxy")
        self.tree.heading("status", text="Durum")
        self.tree.heading("last_check", text="Son Kontrol")
        self.tree.heading("check_count", text="Kontrol Sayısı")
        
        self.tree.column("id", width=40, anchor="center")
        self.tree.column("name", width=150)
        self.tree.column("category", width=120)
        self.tree.column("target", width=60, anchor="center")
        self.tree.column("proxy", width=200)
        self.tree.column("status", width=150)
        self.tree.column("last_check", width=150, anchor="center")
        self.tree.column("check_count", width=100, anchor="center")
        
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.tree.bind("<Button-3>", self._show_context_menu)
        
        # Context Menu
        self.context_menu = Menu(self, tearoff=0, bg="#2b2b2b", fg="white", font=("Segoe UI", 10))
        self.context_menu.add_command(label="▶ Başlat", command=self._start_single)
        self.context_menu.add_command(label="■ Durdur", command=self._stop_single)
        self.context_menu.add_command(label="⏳ Cooldown Kaldır", command=self._clear_cooldown)
        self.context_menu.add_command(label="📝 Düzenle", command=self._edit_single)
        self.context_menu.add_command(label="📄 Log Geçmişini Gör", command=self._view_logs_single)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="🗑️ Sil", command=self._delete_single)

    def _refresh_table(self):
        # Refresh data and basic telemetry
        try:
            users = get_all_users()
            import sqlite3
            from config.database import DB_PATH
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM proxies WHERE status = 'Disabled'")
            bad_proxies = c.fetchone()[0]
            conn.close()
        except Exception as e:
            logging.error(f"Error fetching telemetry data: {e}")
            users = []
            bad_proxies = 0
            
        active_bots = sum(1 for u in users if u.get("status") not in ["Idle", "Durduruldu", "Hata", "Giriş Hatası"])
        cooldowns = sum(1 for u in users if u.get("status") == "Cooldown")
        
        self.lbl_active_bots.configure(text=f"Aktif Bot: {active_bots}")
        self.lbl_bad_proxies.configure(text=f"Bloke Proxy: {bad_proxies}")
        self.lbl_cooldowns.configure(text=f"Cooldown: {cooldowns}")
        
        # C2: Redis Health Warning
        try:
            from config.cache import redis_manager
            if redis_manager.is_connected:
                self.lbl_redis_status.configure(text="✅ Redis Bağlı", text_color="#2ecc71")
            else:
                self.lbl_redis_status.configure(text="⚠️ Redis Kapalı", text_color="#e74c3c")
        except Exception:
            self.lbl_redis_status.configure(text="⚠️ Redis Kapalı", text_color="#e74c3c")

        # Mevcut öğeleri bul (ID -> Item_ID)
        existing_items = {}
        for item in self.tree.get_children():
            user_id_text = self.tree.item(item, "values")[0]
            existing_items[str(user_id_text)] = item
            
        current_db_ids = set()
        
        for u in users:
            uid_str = str(u["id"])
            current_db_ids.add(uid_str)
            name = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip()
            if int(u.get("is_scout", 0)) == 1:
                name = f"🎯 [İZCİ] {name}"
                
            values = (
                u["id"], name, u.get("category", ""), 
                u.get("minimum_days", 0), u.get("proxy_address", "Yok") or "Yok",
                u.get("status", "Bilinmiyor"), u.get("last_check", "-"), u.get("check_count", 0)
            )
            
            if uid_str in existing_items:
                # Sadece değerleri güncelle (Flicker'ı önler)
                self.tree.item(existing_items[uid_str], values=values)
            else:
                # Yeni eklenen satır
                self.tree.insert("", "end", values=values)
                
        # Silinmiş olanları tablodan kaldır
        for uid_str, item in existing_items.items():
            if uid_str not in current_db_ids:
                self.tree.delete(item)
            
        # Tabloyu otomatik yenile (Canlı durum takibi için)
        self.after(3000, self._refresh_table)

    def _show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.tk_popup(event.x_root, event.y_root)

    def _get_selected_user_id(self):
        selected = self.tree.selection()
        if not selected: return None
        return int(self.tree.item(selected[0], "values")[0])

    def _view_logs_single(self):
        uid = self._get_selected_user_id()
        if uid:
            user = get_user_by_id(uid)
            if user:
                from gui.log_viewer import LogViewerWindow
                LogViewerWindow(self, user)

    def _edit_single(self):
        uid = self._get_selected_user_id()
        if uid:
            user_data = get_user_by_id(uid)
            if user_data:
                from gui.user_form import UserForm
                form = UserForm(self, user_data=user_data)
                form.grab_set()

    def _start_single(self):
        uid = self._get_selected_user_id()
        if uid:
            user = get_user_by_id(uid)
            if user:
                # Fake a global config to pass to start_single logic
                from config.database import get_global_setting
                global_config = {
                    "2captcha_key": get_global_setting("2captcha_key"),
                    "discord_webhook": get_global_setting("discord_webhook"),
                    "telegram_username": get_global_setting("telegram_username"),
                    "telegram_apikey": get_global_setting("telegram_apikey")
                }
                
                if uid not in self.manager.threads or not self.manager.threads[uid].is_alive():
                    from bot.manager import WorkerThread
                    t = WorkerThread(user, global_config, self.log_queue)
                    t.daemon = True
                    t.start()
                    self.manager.threads[uid] = t

    def _stop_single(self):
        uid = self._get_selected_user_id()
        if uid:
            self.manager.stop_user(uid)

    def _clear_cooldown(self):
        uid = self._get_selected_user_id()
        if uid:
            from config.database import clear_user_cooldown
            clear_user_cooldown(uid)
            logging.info(f"Müşteri ID {uid} için gözetim/cooldown süresi sıfırlandı.")
            self._refresh_table()

    def _delete_single(self):
        uid = self._get_selected_user_id()
        if uid:
            if messagebox.askyesno("Sil Onayı", "Müşteriyi silmek istediğinizden emin misiniz?"):
                self.manager.stop_user(uid)
                delete_user(uid)
                self._refresh_table()

    def _setup_logging(self):
        class QueueHandler(logging.Handler):
            def __init__(self, queue):
                super().__init__()
                self.queue = queue
            def emit(self, record):
                self.queue.put(record)
                
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        # C1: Guard against duplicate handler registration (prevents double-logging)
        if not any(isinstance(h, QueueHandler) for h in logger.handlers):
            logger.addHandler(QueueHandler(self.log_queue))

    def _poll_logs(self):
        import time
        try:
            while True:
                record = self.log_queue.get_nowait()
                msg = record.getMessage()
                level = record.levelname
                time_str = time.strftime("[%H:%M:%S]")
                
                self.log_text.configure(state="normal")
                self.log_text.insert("end", time_str + " ", "time")
                
                tag = "info"
                if level == "WARNING" or level == "ERROR": tag = "error"
                if "RANDEVU BULUNDU" in msg or "başarılı" in msg.lower(): tag = "success"
                
                self.log_text.insert("end", msg + "\n", tag)
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        finally:
            self.after(100, self._poll_logs)

    def _start_all(self):
        self.manager.start_all()
        
    def _stop_all(self):
        # Graceful Stop
        self.manager.stop_all()
        
    def _kill_all(self):
        # Global Kill Switch implementation
        self.manager.stop_all()
        
        if hasattr(self, 'tg_daemon') and self.tg_daemon:
            self.tg_daemon.stop()
            
        # Clean up lingering driver processes. We explicitly AVOID killing "chrome.exe"
        # because the user might be browsing the web normally. Undetected chromedriver
        # usually drops a specific executable or handles it through chromedriver.exe
        import os
        try:
            os.system("taskkill /f /im undetected_chromedriver.exe /T >nul 2>&1")
            os.system("taskkill /f /im chromedriver.exe /T >nul 2>&1")
            logging.info("Tüm aktif chromedriver süreçleri sonlandırıldı (Kişisel Chrome sekmeleriniz güvende).")
        except Exception as e: 
            logging.error(f"Sürücüleri kapatırken hata: {e}")
            
    def _open_user_form(self):
        from gui.user_form import UserForm
        form = UserForm(self)
        form.grab_set()

    def _import_excel(self):
        from tkinter import filedialog, messagebox
        import pandas as pd
        from config.database import bulk_add_users
        import os
        
        filepath = filedialog.askopenfilename(
            title="Excel Dosyası Seç",
            filetypes=(("Excel Files", "*.xlsx"), ("All Files", "*.*"))
        )
        if not filepath:
            return
            
        try:
            df = pd.read_excel(filepath)
            
            # Gerekli sütunlar (DB kolonlarına eşdeğer veya dönüştürülebilir)
            # Beklenen format: email, password, first_name, last_name, phone, jurisdiction, location, category, visa_type, visa_sub_type, appointment_for, minimum_days, proxy_address
            # Pandas NaN değerleri None ile değiştirilir
            df = df.where(pd.notnull(df), None)
            
            users_list = []
            for _, row in df.iterrows():
                if not row.get("email") or not row.get("password"):
                    continue # Email ve şifre zorunlu
                    
                user = {
                    "is_active": True,
                    "email": str(row.get("email")).strip(),
                    "password": str(row.get("password")).strip(),
                    "first_name": str(row.get("first_name", "")).strip(),
                    "last_name": str(row.get("last_name", "")).strip(),
                    "phone": str(row.get("phone", "")).strip(),
                    "jurisdiction": str(row.get("jurisdiction", "")).strip(),
                    "location": str(row.get("location", "")).strip(),
                    "category": str(row.get("category", "")).strip(),
                    "visa_type": str(row.get("visa_type", "")).strip(),
                    "visa_sub_type": str(row.get("visa_sub_type", "")).strip(),
                    "appointment_for": str(row.get("appointment_for", "Individual")).strip(),
                    "minimum_days": int(row.get("minimum_days", 0) if row.get("minimum_days") is not None else 0),
                    "check_interval": int(row.get("check_interval", 60) if row.get("check_interval") is not None else 60),
                    "proxy_address": str(row.get("proxy_address", "")).strip() if row.get("proxy_address") else "",
                    "headless": True,
                    "status": "Idle"
                }
                users_list.append(user)
                
            if users_list:
                bulk_add_users(users_list)
                self._refresh_table()
                messagebox.showinfo("Başarılı", f"{len(users_list)} müşteri başarıyla içe aktarıldı!")
            else:
                messagebox.showwarning("Uyarı", "Excel dosyasında geçerli müşteri verisi bulunamadı (Email ve Şifre sütunları şart).")
                
        except Exception as e:
            logging.error(f"Excel İçe Aktarma Hatası: {e}")
            messagebox.showerror("Hata", f"Excel dosyası okunurken hata oluştu:\n{e}")

    def _export_excel(self):
        from tkinter import filedialog, messagebox
        import pandas as pd
        from config.database import get_all_users, _simple_decode
        
        users = get_all_users()
        if not users:
            messagebox.showinfo("Bilgi", "Dışa aktarılacak müşteri bulunmuyor.")
            return
            
        filepath = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            title="Excel Olarak Kaydet",
            filetypes=(("Excel Files", "*.xlsx"), ("All Files", "*.*"))
        )
        if not filepath:
            return
            
        try:
            # Şifreleri decode edip dışa aktarılabilir hale getirelim (isteğe bağlı ama import için gerekli olabilir)
            export_data = []
            for u in users:
                u_copy = dict(u) # copy for editing
                u_copy['password'] = _simple_decode(u_copy.get('password_enc', ''))
                # Hide internal IDs and states if needed, but keeping them allows a cleaner backup
                export_data.append(u_copy)
                
            df = pd.DataFrame(export_data)
            
            # Sütun sırasını düzenle (Okunabilirlik için)
            cols = ["id", "is_active", "email", "password", "first_name", "last_name", "phone", 
                    "jurisdiction", "location", "category", "visa_type", "visa_sub_type", 
                    "appointment_for", "minimum_days", "check_interval", "proxy_address", "status", "last_check", "error_msg", "cooldown_until"]
            
            # df'de sadece var olan sütunları al
            cols = [c for c in cols if c in df.columns]
            df = df[cols]
            
            df.to_excel(filepath, index=False)
            messagebox.showinfo("Başarılı", "Müşteri listesi başarıyla Excel dosyasına kaydedildi.")
            
        except Exception as e:
            logging.error(f"Excel Dışa Aktarma Hatası: {e}")
            messagebox.showerror("Hata", f"Excel dosyası kaydedilirken hata oluştu:\n{e}")

if __name__ == "__main__":
    app = DashboardWindow()
    app.mainloop()

import customtkinter as ctk
import os

class LogViewerWindow(ctk.CTkToplevel):
    def __init__(self, master, user_data):
        super().__init__(master)
        self.user_data = user_data
        name = f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip()
        self.title(f"{name} - Log Geçmişi")
        self.geometry("800x600")
        
        # Build path identical to bot/manager.py _log()
        safe_name = "".join(x for x in user_data.get('first_name', 'user') if x.isalnum() or x.isspace()).replace(" ", "_")
        self.log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", f"{safe_name}_{user_data['id']}.log")
        
        self.text_box = ctk.CTkTextbox(self, state="disabled", font=("Consolas", 12), wrap="word")
        self.text_box.pack(fill="both", expand=True, padx=10, pady=10)
        
        bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        bottom_frame.pack(fill="x", padx=10, pady=(0, 10))
        
        self.auto_scroll = ctk.CTkCheckBox(bottom_frame, text="Otomatik Kaydır")
        self.auto_scroll.select()
        self.auto_scroll.pack(side="left")
        
        ctk.CTkButton(bottom_frame, text="Kapat", command=self._on_close).pack(side="right")
        
        self.is_destroyed = False
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        
        self.last_mtime = 0
        self._refresh_logs()
        
    def _refresh_logs(self):
        if self.is_destroyed: return
        
        try:
            if os.path.exists(self.log_path):
                current_mtime = os.path.getmtime(self.log_path)
                if current_mtime > self.last_mtime:
                    self.last_mtime = current_mtime
                    with open(self.log_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    
                    # Son 1000 satırı göster (kasmasın diye)
                    content = "".join(lines[-1000:])
                    
                    self.text_box.configure(state="normal")
                    self.text_box.delete("1.0", "end")
                    self.text_box.insert("end", content)
                    self.text_box.configure(state="disabled")
                    
                    if self.auto_scroll.get():
                        self.text_box.see("end")
            else:
                self.text_box.configure(state="normal")
                self.text_box.delete("1.0", "end")
                self.text_box.insert("end", "Henüz log dosyası oluşturulmadı...\n(Bot ilk kez işlem yaptığında burası dolacaktır.)")
                self.text_box.configure(state="disabled")
        except Exception:
            pass
            
        self.after(2000, self._refresh_logs)

    def _on_close(self):
        self.is_destroyed = True
        self.destroy()

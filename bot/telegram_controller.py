import asyncio
import threading
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from config.database import get_global_setting, get_all_users

# Telegram'ın (httpx) HTTP isteklerini log ekranına basmasını engelle
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("TelegramBot")

class TelegramBotDaemon(threading.Thread):
    def __init__(self, bot_manager):
        super().__init__()
        self.bot_manager = bot_manager
        self.daemon = True
        self.token = get_global_setting("telegram_bot_token", "").strip()
        self.allowed_users = self._parse_allowed_users()
        self.application = None
        self.loop = asyncio.new_event_loop()

    def _parse_allowed_users(self):
        # We can add an allowed user IDs field later, for now we will just log the IDs 
        # or accept commands if we want to restrict it.
        # Actually it's safer to only allow the owner. We'll use the 'telegram_username' as an ID check if needed
        # but telegram IDs are numeric. Let's create a setting for it.
        admin_id = get_global_setting("telegram_admin_id", "").strip()
        if admin_id:
            try:
                return [int(x) for x in admin_id.split(",")]
            except:
                return []
        return []

    def run(self):
        if not self.token:
            logger.warning("Telegram Bot Token is not set. Remote control disabled.")
            return

        asyncio.set_event_loop(self.loop)
        try:
            self.application = ApplicationBuilder().token(self.token).build()
            
            # Error handler (Catch-all)
            async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
                logger.error(f"Telegram Exception while handling update {update}:", exc_info=context.error)
            
            self.application.add_error_handler(error_handler)
            
            # Register handlers
            self.application.add_handler(CommandHandler("start", self.cmd_start))
            self.application.add_handler(CommandHandler("status", self.cmd_status))
            self.application.add_handler(CommandHandler("start_all", self.cmd_start_all))
            self.application.add_handler(CommandHandler("start_id", self.cmd_start_single))
            self.application.add_handler(CommandHandler("stop_all", self.cmd_stop_all))
            self.application.add_handler(CommandHandler("stop", self.cmd_stop_single))
            
            logger.info("📱 Telegram Bot Engine Started")
            self.application.run_polling(close_loop=False)
        except Exception as e:
            logger.error(f"Telegram Bot Crash: {e}")

    def stop(self):
        if self.application:
            try:
                # Polling'i güvenli kapatabilmek için asenkron stop
                asyncio.run_coroutine_threadsafe(self.application.stop(), self.loop)
                asyncio.run_coroutine_threadsafe(self.application.shutdown(), self.loop)
            except Exception as e:
                logger.error(f"Error stopping Telegram Bot: {e}")

    def _is_allowed(self, update: Update) -> bool:
        if not self.allowed_users: 
            # If no admin ID is set, allow anyone who knows the bot (A bit risky, but okay for first setup)
            # We will tell them their ID so they can secure it.
            return True
        return update.effective_user.id in self.allowed_users

    async def _reject_unauthorized(self, update: Update):
        await update.message.reply_text("⛔ Yetkiniz yok. Yönetici ID listesinde değilsiniz.")

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        msg = (
            f"👋 BLS Bot Manager'a Hoş Geldiniz!\n\n"
            f"Sizin Telegram ID numaranız: <code>{user_id}</code>\n"
        )
        if self._is_allowed(update):
            msg += (
                f"Sistem yetkiniz MAKSİMUM düzeyde.\n\n"
                f"Komutlar:\n"
                f"/status - Aktif botları ve durumları gör\n"
                f"/start_all - Tüm aktif müşterileri başlat\n"
                f"/start_id &lt;ID&gt; - Belirli bir müşteriyi başlat\n"
                f"/stop_all - Tüm botları durdur\n"
                f"/stop &lt;ID&gt; - Belirli bir müşteriyi durdur"
            )
        else:
            msg += (
                f"Sisteme dışarıdan erişim <b>YETKİNİZ YOK</b>.\n"
                f"Eğer yönetici iseniz, Masaüstü UI üzerinden 'Global Ayarlar' kısmında "
                f"'Yetkili Telegram ID'leri' alanına yukarıdaki ID'yi yazıp kaydediniz."
            )
        await update.message.reply_text(msg, parse_mode="HTML")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            await update.message.reply_text("⛔ Yetkiniz yok.")
            return
            
        users = get_all_users()
        active_count = 0
        running_count = 0
        error_count = 0
        
        details = []
        for u in users:
            if u.get('is_active'):
                active_count += 1
                name = f"{u.get('first_name', '')} {u.get('last_name', '')}"
                status = u.get('status', 'Idle')
                if status == 'Running': running_count += 1
                if 'Hata' in status or 'Error' in status: error_count += 1
                
                details.append(f"• ID:{u['id']} | <b>{name}</b> | {status}")

        msg = (
            f"📊 <b>Sistem Durumu</b>\n"
            f"Toplam Kayıtlı: {len(users)}\n"
            f"Aktif (Çalıştırılabilir): {active_count}\n"
            f"Şu an Taranan: {running_count}\n"
            f"Hata Durumunda: {error_count}\n\n"
            f"Detaylar:\n" + "\n".join(details[:20]) # Limit to 20 to avoid enormous messages
        )
        if len(details) > 20: msg += f"\n...ve {len(details)-20} daha."
        
        await update.message.reply_text(msg, parse_mode="HTML")

    async def cmd_start_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            await self._reject_unauthorized(update)
            return
        
        await update.message.reply_text("🚀 Tüm botlara başlatma emri gönderildi (UI üzerindeki gibi).")
        self.bot_manager.start_all()
        
    async def cmd_start_single(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            await self._reject_unauthorized(update)
            return
        
        try:
            target_id = int(context.args[0])
            self.bot_manager.start_single(target_id)
            await update.message.reply_text(f"🚀 Müşteri ID {target_id} için başlatma emri gönderildi.")
        except (IndexError, ValueError):
            await update.message.reply_text("❌ Kullanım: <code>/start_id &lt;Müşteri_ID&gt;</code>", parse_mode="HTML")

    async def cmd_stop_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            await self._reject_unauthorized(update)
            return
        
        await update.message.reply_text("🛑 Tüm botlar durduruluyor...")
        self.bot_manager.stop_all()

    async def cmd_stop_single(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            await self._reject_unauthorized(update)
            return
        
        try:
            target_id = int(context.args[0])
            self.bot_manager.stop_user(target_id)
            await update.message.reply_text(f"✅ Müşteri ID {target_id} için durdurma emri gönderildi.")
        except (IndexError, ValueError):
            await update.message.reply_text("❌ Kullanım: <code>/stop &lt;Müşteri_ID&gt;</code>", parse_mode="HTML")


# ════════════════════════════════════════════════════════════════════════════
# B4: Proactive Alert — module-level function callable from anywhere
# ════════════════════════════════════════════════════════════════════════════

_active_daemon = None  # Set by DashboardWindow on startup

def register_daemon(daemon: TelegramBotDaemon):
    """Dashboard başlattığında daemon referansını kaydet."""
    global _active_daemon
    _active_daemon = daemon

def send_telegram_alert(message: str):
    """
    Proaktif mesaj gönderir (komut beklemeden).
    manager.py gibi herhangi bir modülden çağrılabilir.
    """
    if not _active_daemon or not _active_daemon.application:
        return
    
    admin_ids = _active_daemon.allowed_users
    if not admin_ids:
        return
    
    async def _push():
        bot = _active_daemon.application.bot
        for uid in admin_ids:
            try:
                await bot.send_message(chat_id=uid, text=message, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Telegram proaktif bildirim hatası (ID {uid}): {e}")
    
    try:
        asyncio.run_coroutine_threadsafe(_push(), _active_daemon.loop)
    except Exception as e:
        logger.error(f"Telegram alert dispatch error: {e}")

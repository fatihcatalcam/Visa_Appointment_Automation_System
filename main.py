import sys
import os

# Ensure the root directory is in the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from gui.dashboard import DashboardWindow
from bot.telemetry import start_telemetry_server

if __name__ == "__main__":
    start_telemetry_server(port=8000)
    
    app = DashboardWindow()
    app.mainloop()

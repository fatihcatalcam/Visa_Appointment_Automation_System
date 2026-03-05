from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

try:
    print("Connecting to Chrome via Selenium...")
    options = Options()
    options.debugger_address = "localhost:56841"
    driver = webdriver.Chrome(options=options)
    
    print("Current URL:", driver.current_url)
    
    # Kapat
    driver.execute_script("""
        var input = document.querySelector("input[data-role='datepicker']") || document.querySelector("input.k-input");
        if(input) {
            var dp = $(input).data("kendoDatePicker");
            if(dp) dp.close();
        }
    """)
    time.sleep(1)
    
    # 1. Ortaya kaydır
    print("Scrolling to center...")
    icon = driver.find_element(By.CSS_SELECTOR, ".k-i-calendar, .k-select")
    driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});", icon)
    time.sleep(1)
    
    # 2. Selenium physical click (not JS click, not API open)
    print("Selenium physical click...")
    icon.click()
    
    print("Done. Check where it opened.")
    time.sleep(10)
    
except Exception as e:
    print("Error:", e)

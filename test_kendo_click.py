from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import undetected_chromedriver as uc
import logging

logging.basicConfig(level=logging.INFO)

def test_kendo_click():
    options = uc.ChromeOptions()
    options.add_argument('--window-size=1280,1024')
    # Using personal chrome profile to bypass captcha if possible, or just hit the public test URL
    driver = uc.Chrome(options=options)
    
    try:
        # Go to Kendo UI DatePicker demo page which has the exact same widget
        driver.get("https://demos.telerik.com/kendo-ui/datepicker/index")
        time.sleep(5)
        
        # Accept cookies if present
        try:
            btn = driver.find_element(By.ID, "onetrust-accept-btn-handler")
            btn.click()
            time.sleep(1)
        except:
            pass
            
        logging.info("Attempting to open the datepicker...")
        # Open datepicker
        script_open = """
            var dpInput = $("input[data-role='datepicker']").first();
            var dp = dpInput.data('kendoDatePicker');
            dp.open();
        """
        driver.execute_script(script_open)
        time.sleep(1)
        
        logging.info("Attempting mechanical click on day 15...")
        # Try our exact logic to click day 15
        clicked = driver.execute_script("""
            var cells = document.querySelectorAll('td[role="gridcell"]:not(.k-other-month):not(.k-state-disabled) .k-link');
            for(var i=0; i<cells.length; i++) {
                if(cells[i].innerText.trim() === '15') { // Click the 15th
                    cells[i].scrollIntoView({block: 'center', behavior: 'instant'});
                    cells[i].click();
                    return true;
                }
            }
            return false;
        """)
        
        logging.info(f"Javascript .click() returned: {clicked}")
        time.sleep(2)
        
        # Let's check the input value to see if the click registered natively
        final_val = driver.execute_script('return $("input[data-role=\'datepicker\']").first().val();')
        logging.info(f"Final Input value after click: {final_val}")
        
    finally:
        driver.quit()

if __name__ == "__main__":
    test_kendo_click()

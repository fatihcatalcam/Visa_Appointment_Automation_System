from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time

try:
    print("Connecting to Chrome via Selenium...")
    options = Options()
    options.debugger_address = "localhost:56841"
    driver = webdriver.Chrome(options=options)
    
    print("Current URL:", driver.current_url)
    
    # 1. Close popup completely and clear appendTo
    js_reset = """
        var input = document.querySelector("input[data-role='datepicker']") || document.querySelector("input.k-input");
        if(input) {
            var dp = $(input).data("kendoDatePicker");
            if(dp) {
                dp.close();
                dp.setOptions({ popup: { appendTo: document.body } }); // Reset
            }
        }
    """
    driver.execute_script(js_reset)
    time.sleep(1)
    
    # 2. Open it and aggressively force CSS positioning based on the input's bounding box
    js_force_pos = """
        var input = document.querySelector("input[data-role='datepicker']") || document.querySelector("input.k-input");
        var dp = $(input).data("kendoDatePicker");
        
        input.scrollIntoView({block: 'center', behavior: 'instant'});
        dp.open();
        
        // Wait a tiny bit for it to render, then force its position
        setTimeout(() => {
            if(dp.dateView && dp.dateView.popup && dp.dateView.popup.wrapper) {
                var rect = input.getBoundingClientRect();
                var wrapper = dp.dateView.popup.wrapper[0];
                
                // Force absolute CSS positioning
                wrapper.style.position = 'fixed'; // fixed avoids scroll offset issues
                wrapper.style.top = (rect.bottom + 5) + 'px';
                wrapper.style.left = rect.left + 'px';
                wrapper.style.zIndex = '99999';
            }
        }, 50);
        return "Forced fixed CSS positioning applied.";
    """
    res = driver.execute_script(js_force_pos)
    print("JS Result:", res)
    
except Exception as e:
    print("Error:", e)

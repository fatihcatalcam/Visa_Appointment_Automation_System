from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time

try:
    print("Connecting to Chrome via Selenium...")
    options = Options()
    options.debugger_address = "localhost:56841"
    driver = webdriver.Chrome(options=options)
    
    print("Current URL:", driver.current_url)
    
    js_code = """
        var input = document.querySelector("input[data-role='datepicker']") || document.querySelector("input.k-input");
        if(!input) return "No input";
        
        var dp = $(input).data("kendoDatePicker");
        if(!dp) return "No widget";
        
        dp.close();
        
        // Option 1: Try setting appendTo to the input's parent wrapper
        dp.setOptions({
            popup: { appendTo: $(input).closest('.form-group') || $(input).parent() }
        });
        
        // Option 2: Scroll input to center
        input.scrollIntoView({block: 'center', behavior: 'instant'});
        
        dp.open();
        
        // Force reposition
        if(dp.dateView && dp.dateView.popup) {
            dp.dateView.popup.position();
        }
        
        return "Opened! Check browser to see if position is correct.";
    """
    
    res = driver.execute_script(js_code)
    print("JS Result:", res)
    time.sleep(10) # wait a bit to let the user see it
    
except Exception as e:
    print("Error:", e)

"""
Quick test script to verify:
1. Proxy extension loads correctly (MV3) and masks real IP
2. Timezone is reported as Europe/Istanbul
3. No WebRTC IP leaks
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot.browser import BrowserFactory

# Use the first user's proxy for testing, or pass one manually
PROXY = sys.argv[1] if len(sys.argv) > 1 else ""

if not PROXY:
    print("Usage: python test_ip_leak.py user:pass@host:port")
    print("       Trying without proxy (will show raw VPS IP)...")

user = {"id": 9999, "headless": True, "proxy_address": PROXY}
bf = BrowserFactory(user, {})
bf.proxy = PROXY

print("Starting Chrome...")
if not bf.create_driver():
    print("FAILED to start Chrome!")
    sys.exit(1)

driver = bf.driver

# Test 1: IP Check
print("\n=== TEST 1: IP Address ===")
try:
    driver.get("https://httpbin.org/ip")
    time.sleep(3)
    body = driver.find_element("tag name", "body").text
    print(f"  Reported IP: {body}")
except Exception as e:
    print(f"  IP check failed: {e}")

# Test 2: Timezone
print("\n=== TEST 2: Timezone ===")
try:
    tz = driver.execute_script("return Intl.DateTimeFormat().resolvedOptions().timeZone")
    locale = driver.execute_script("return navigator.language")
    print(f"  Browser Timezone: {tz}")
    print(f"  Browser Locale: {locale}")
    print(f"  Expected: Europe/Istanbul")
    print(f"  Match: {'✅ YES' if 'Istanbul' in str(tz) else '❌ NO'}")
except Exception as e:
    print(f"  Timezone check failed: {e}")

# Test 3: WebDriver detection
print("\n=== TEST 3: WebDriver Flag ===")
try:
    wd = driver.execute_script("return navigator.webdriver")
    print(f"  navigator.webdriver = {wd}")
    print(f"  Status: {'✅ HIDDEN' if wd is None or wd == False else '❌ DETECTED'}")
except Exception as e:
    print(f"  WebDriver check failed: {e}")

# Test 4: WebRTC Leak
print("\n=== TEST 4: WebRTC Leak ===")
try:
    driver.get("about:blank")
    time.sleep(1)
    webrtc_result = driver.execute_script("""
        return new Promise((resolve) => {
            try {
                var pc = new RTCPeerConnection({iceServers: [{urls: 'stun:stun.l.google.com:19302'}]});
                var ips = [];
                pc.createDataChannel('');
                pc.createOffer().then(offer => pc.setLocalDescription(offer));
                pc.onicecandidate = function(e) {
                    if (!e.candidate) {
                        pc.close();
                        resolve(ips.length > 0 ? ips.join(', ') : 'NO_LEAKS');
                        return;
                    }
                    var m = e.candidate.candidate.match(/([0-9]{1,3}(\\.[0-9]{1,3}){3})/);
                    if (m && ips.indexOf(m[1]) === -1) ips.push(m[1]);
                };
                setTimeout(() => { pc.close(); resolve(ips.length > 0 ? ips.join(', ') : 'NO_LEAKS'); }, 5000);
            } catch(e) { resolve('WebRTC_BLOCKED: ' + e.message); }
        });
    """)
    print(f"  WebRTC IPs: {webrtc_result}")
    is_safe = 'NO_LEAKS' in str(webrtc_result) or 'BLOCKED' in str(webrtc_result)
    print(f"  Status: {'✅ SAFE' if is_safe else '⚠️ POTENTIAL LEAK'}")
except Exception as e:
    print(f"  WebRTC check failed: {e}")

# Test 5: BLS Site Access
print("\n=== TEST 5: BLS Spain Site Access ===")
try:
    driver.get("https://turkey.blsspainglobal.com/Global/account/login")
    time.sleep(5)
    title = driver.title
    url = driver.current_url
    source = driver.page_source[:500]
    is_403 = "403" in title or "Forbidden" in title or "Access Denied" in source
    print(f"  Title: {title}")
    print(f"  URL: {url}")
    print(f"  Status: {'❌ 403 BLOCKED' if is_403 else '✅ PAGE LOADED'}")
except Exception as e:
    print(f"  BLS access failed: {e}")

print("\n=== DONE ===")
bf.close_driver()

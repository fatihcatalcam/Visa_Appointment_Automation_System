import re

file_path = r'c:\Users\Fatih\comp-bot\web_panel\src\App.jsx'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Replace all exact fetch( calls with apiFetch(
content = re.sub(r'\bfetch\(', 'apiFetch(', content)

# 2. Inject LoginScreen component and apiFetch wrapper definition 
login_code = """
let globalApiKey = localStorage.getItem('admin_api_key') || '';
let globalSetAuth = null;

const apiFetch = async (url, options = {}) => {
  const headers = { ...options.headers };
  if (globalApiKey) headers['X-API-Key'] = globalApiKey;
  const res = await window.fetch(url, { ...options, headers });
  if (res.status === 401 && globalSetAuth) {
    globalSetAuth(false);
    localStorage.removeItem('admin_api_key');
  }
  return res;
};

function LoginScreen({ onLogin }) {
  const [key, setKey] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
        const res = await window.fetch(`${API_BASE}/system/auth/verify`, {
        method: 'POST',
        headers: { 'X-API-Key': key }
      });
      if (res.ok) {
        onLogin(key);
      } else {
        setError('Hatalı API Anahtarı (Parola)');
      }
    } catch {
      setError('Sunucuya bağlanılamadı.');
    }
  };

  return (
    <div className="modal-overlay" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', background: '#0f172a', zIndex: 99999 }}>
      <div className="glass-panel" style={{ padding: '3rem', width: '400px', textAlign: 'center' }}>
        <h2 style={{fontSize: '1.5rem', marginBottom: '1rem'}}>🔒 Güvenlik Duvarı</h2>
        <p style={{ color: 'var(--text-muted)', marginBottom: '2rem' }}>VPS yönetici parolasını girin.</p>
        <form onSubmit={handleSubmit}>
          <input type="password" value={key} onChange={e => setKey(e.target.value)} placeholder="Parola (API Key)" style={{ width: '100%', padding: '0.8rem', marginBottom: '1rem', borderRadius: '8px', border: '1px solid var(--panel-border)', background: 'rgba(0,0,0,0.3)', color: 'white' }} />
          {error && <div style={{ color: '#ef4444', marginBottom: '1rem', fontSize: '0.85rem' }}>{error}</div>}
          <button type="submit" className="btn-primary" style={{ width: '100%', padding: '1rem' }}>Giriş Yap</button>
        </form>
      </div>
    </div>
  );
}

"""

if 'globalApiKey' not in content:
    content = content.replace('function App() {', login_code + 'function App() {')

app_start_injection = """function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  
  useEffect(() => {
    globalSetAuth = setIsAuthenticated;
    // Auto verify initial key
    if (globalApiKey) {
      window.fetch(`${API_BASE}/system/auth/verify`, { method: 'POST', headers: { 'X-API-Key': globalApiKey } })
        .then(res => { if (res.ok) setIsAuthenticated(true); else { setIsAuthenticated(false); localStorage.removeItem('admin_api_key'); globalApiKey = ''; } })
        .catch(() => setIsAuthenticated(true)); // Assume ok if network down but key exists
    }
  }, []);

  if (!isAuthenticated && !globalApiKey) {
    return <LoginScreen onLogin={(key) => { globalApiKey = key; localStorage.setItem('admin_api_key', key); setIsAuthenticated(true); }} />;
  }

"""
if 'const [isAuthenticated' not in content:
    content = content.replace('function App() {', app_start_injection)

# fix websocket token logic
content = content.replace(
    '''const wsHost = window.location.hostname + ':8000'\n      ws = new WebSocket(`${wsProtocol}//${wsHost}/api/v1/system/ws/logs`)''', 
    '''const wsHost = window.location.hostname + ':8000'\n      ws = new WebSocket(`${wsProtocol}//${wsHost}/api/v1/system/ws/logs?token=${globalApiKey}`)'''
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Patch applied successfully.")

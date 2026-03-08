import { useState, useEffect, useRef } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { motion, AnimatePresence, animate } from 'framer-motion'
const API_BASE = '/api/v1'

function AnimatedNumber({ value }) {
  const nodeRef = useRef()
  useEffect(() => {
    const node = nodeRef.current
    if (node) {
      const controls = animate(parseInt(node.textContent) || 0, value, {
        duration: 1,
        onUpdate(v) { node.textContent = Math.round(v) }
      })
      return () => controls.stop()
    }
  }, [value])
  return <span ref={nodeRef}>{value}</span>
}


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

function App() {
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


  const [workers, setWorkers] = useState([])
  const [stats, setStats] = useState({ active: 0, waiting: 0, cooldown: 0, stopped: 0 })
  const [sysHealth, setSysHealth] = useState({ healthy_proxies: 0, cooldown_proxies: 0, uptime: '100%' })

  const [showAddModal, setShowAddModal] = useState(false)
  const [showSettingsModal, setShowSettingsModal] = useState(false)
  const [editingUser, setEditingUser] = useState(null)
  const [selectedLogWorker, setSelectedLogWorker] = useState(null)

  // Analytics & Bulk
  const [chartData, setChartData] = useState([])
  const [selectedIds, setSelectedIds] = useState([])

  // Navigation
  const [activeTab, setActiveTab] = useState('dashboard')

  // Global Logs & Notifications
  const [sysLogs, setSysLogs] = useState([])
  const [notifications, setNotifications] = useState([])
  const [showNotifications, setShowNotifications] = useState(false)

  useEffect(() => {
    let ws = null
    let reconnectTimer = null

    const connect = () => {
      // Connect directly to FastAPI backend (port 8000) for WebSocket.
      // Vite's HTTP proxy does NOT reliably handle WebSocket upgrade requests.
      const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsHost = window.location.hostname + ':8000'
      ws = new WebSocket(`${wsProtocol}//${wsHost}/api/v1/system/ws/logs?token=${globalApiKey}`)

      ws.onopen = () => {
        console.log('[WS] Connected to log stream')
      }

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data)
        if (data.logs && data.logs.length > 0) {
          setSysLogs(prev => {
            const newLogs = [...prev, ...data.logs]
            return newLogs.slice(-500) // Keep last 500 lines globally
          })

          setNotifications(prev => {
            let newNots = [...prev]
            data.logs.forEach(log => {
              const mLower = (log?.message || '').toLowerCase()
              const isErr = log.level === 'ERROR' || log.level === 'WARNING'
              const isSuccess = mLower.includes('randevu bulundu') || mLower.includes('alındı') || mLower.includes('başarılı')
              if (isErr || isSuccess) newNots.unshift({ ...log, isError: isErr })
            })
            return newNots.slice(0, 50)
          })
        }
      }

      ws.onerror = (err) => {
        console.error('[WS] WebSocket error:', err)
      }

      ws.onclose = () => {
        console.warn('[WS] Connection closed. Reconnecting in 3s...')
        reconnectTimer = setTimeout(connect, 3000)
      }
    }

    connect()

    return () => {
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (ws) ws.close()
    }
  }, [])

  // Filters
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('ALL')

  // Fetch initial data
  const fetchData = async () => {
    try {
      const res = await apiFetch(`${API_BASE}/workers`)
      if (res.ok) {
        const data = await res.json()
        const workerList = data.workers || []
        setWorkers(workerList)

        let active = 0, cooldown = 0, stopped = 0, waiting = 0;
        workerList.forEach(w => {
          const s = w.status?.toLowerCase() || ''
          if (!w.is_active) stopped++
          else if (s.includes('çal') || s.includes('kontrol')) active++
          else if (s.includes('tatil')) cooldown++
          else waiting++
        })
        setStats({ active, cooldown, waiting, stopped })

        setChartData(prev => {
          const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
          const newData = [...prev, { time: now, active, cooldown, stopped }]
          return newData.slice(-30) // Keep last 30 data points
        })
      }

      const healthRes = await apiFetch(`${API_BASE}/system/telemetry`)
      if (healthRes.ok) {
        const hData = await healthRes.json()
        setSysHealth(hData)
      }
    } catch (err) {
      console.error("Fetch Data Error:", err)
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 3000)
    return () => clearInterval(interval)
  }, [])

  // API Actions
  const handleAction = async (endpoint) => {
    await apiFetch(`${API_BASE}/${endpoint}`, { method: 'POST' })
    fetchData()
  }

  const deleteWorker = async (id) => {
    if (!confirm("Müşteriyi silmek istediğinize emin misiniz?")) return;
    await apiFetch(`${API_BASE}/workers/${id}`, { method: 'DELETE' })
    fetchData()
  }

  const handleBulkAction = async (action) => {
    if (selectedIds.length === 0) return;
    if (action === 'delete' && !confirm(`Seçili ${selectedIds.length} müşteriyi silmek istediğine emin misin?`)) return;

    for (const id of selectedIds) {
      if (action === 'delete') await apiFetch(`${API_BASE}/workers/${id}`, { method: 'DELETE' })
      else await apiFetch(`${API_BASE}/workers/${id}/${action}`, { method: 'POST' })
    }
    setSelectedIds([])
    fetchData()
  }

  const handleEdit = (user) => {
    setEditingUser(user)
    setShowAddModal(true)
  }

  const exportExcel = () => { window.location.href = `${API_BASE}/workers/export/excel` }

  const importExcel = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await apiFetch(`${API_BASE}/workers/import/excel`, { method: 'POST', body: formData })
      const data = await res.json()
      if (res.ok) {
        alert(data.message)
        fetchData()
      } else {
        alert(data.detail || "Upload error")
      }
    } catch (err) {
      alert("Error importing file")
    }
    e.target.value = null
  }

  // Derived filter logic
  const filteredWorkers = (workers || []).filter(w => {
    // text search
    const ms = `${w?.first_name || ''} ${w?.last_name || ''} ${w?.email || ''}`.toLowerCase()
    const matchesSearch = ms.includes(searchQuery.toLowerCase())
    if (!matchesSearch) return false

    // status filter
    const s = w?.status?.toLowerCase() || ''
    if (statusFilter === 'ACTIVE') return s.includes('çal') || s.includes('kontrol')
    if (statusFilter === 'COOLDOWN') return s.includes('tatil')
    if (statusFilter === 'STOPPED') return !w?.is_active
    return true
  })

  // Proxy calcs
  const totalProxies = (sysHealth?.healthy_proxies || 0) + (sysHealth?.cooldown_proxies || 0);
  const proxyHealthPct = totalProxies > 0 ? Math.round(((sysHealth?.healthy_proxies || 0) / totalProxies) * 100) : 100;

  // Basic Error Rate Calc (based on stopped vs active)
  const totalW = (workers || []).length;
  const errorRate = totalW > 0 ? Math.round(((stats?.stopped || 0) / totalW) * 100) : 0;

  return (
    <>
      <header className="top-nav">
        <h1>
          <span>⚡</span> VizeBot Control
        </h1>

        <div className="system-health-bar">
          <div className="health-metric">
            <span className="health-label" title="CPU Kullanımı">CPU</span>
            <span className="health-value" style={{ color: (sysHealth?.cpu_percent || 0) > 85 ? 'var(--status-emergency)' : (sysHealth?.cpu_percent || 0) > 60 ? 'var(--status-cooldown)' : 'var(--status-active)' }}>
              <AnimatedNumber value={Math.round(sysHealth?.cpu_percent || 0)} />%
            </span>
          </div>
          <div className="health-metric">
            <span className="health-label" title="RAM Kullanımı">RAM</span>
            <span className="health-value" style={{ color: (sysHealth?.ram_percent || 0) > 85 ? 'var(--status-emergency)' : (sysHealth?.ram_percent || 0) > 60 ? 'var(--status-cooldown)' : 'var(--status-active)' }}>
              <AnimatedNumber value={Math.round(sysHealth?.ram_percent || 0)} />%
              <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginLeft: '4px' }}>{sysHealth?.ram_used_gb || 0}/{sysHealth?.ram_total_gb || 0}G</span>
            </span>
          </div>
          <div className="health-metric">
            <span className="health-label" title="Botların (Chrome) toplam RAM kullanımı">🤖 Bots</span>
            <span className="health-value" style={{ color: (sysHealth?.bot_ram_mb || 0) > 1000 ? 'var(--status-emergency)' : (sysHealth?.bot_ram_mb || 0) > 500 ? 'var(--status-cooldown)' : 'var(--status-active)' }}>
              <AnimatedNumber value={sysHealth?.bot_ram_mb || 0} /><span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginLeft: '2px' }}>MB</span>
            </span>
          </div>
          <div className="health-metric">
            <span className="health-label" title="Aktif Chrome İşçileri / Maksimum">Workers</span>
            <span className="health-value" style={{ color: (sysHealth?.active_workers || 0) >= (sysHealth?.max_workers || 15) ? 'var(--status-emergency)' : 'var(--status-active)' }}>
              <AnimatedNumber value={sysHealth?.active_workers || 0} /><span style={{ color: 'var(--text-muted)' }}>/{sysHealth?.max_workers || 15}</span>
            </span>
          </div>
          <div style={{ width: '1px', background: 'var(--panel-border)', alignSelf: 'stretch', margin: '0 0.2rem' }}></div>
          <div className="health-metric">
            <span className="health-label" title="Sağlam Proxy Yüzdesi">Proxy Health</span>
            <span className="health-value" style={{ color: proxyHealthPct < 50 ? 'var(--status-emergency)' : proxyHealthPct < 80 ? 'var(--status-cooldown)' : 'var(--status-active)' }}>
              <AnimatedNumber value={proxyHealthPct} />%
            </span>
          </div>
          <div className="health-metric">
            <span className="health-label" title="Toplam çalışabilecek Proxy Sayısı">Proxies</span>
            <span className="health-value"><AnimatedNumber value={sysHealth?.healthy_proxies || 0} /></span>
          </div>
          <div className="health-metric">
            <span className="health-label" title="Durdurulan/Hata veren kullanıcı oranı">Error Rate</span>
            <span className="health-value" style={{ color: errorRate > 20 ? 'var(--status-emergency)' : 'var(--text-primary)' }}>
              <AnimatedNumber value={errorRate} />%
            </span>
          </div>
          <div className="health-metric">
            <span className="health-label">Active</span>
            <span className="health-value" style={{ color: 'var(--status-active)' }}><AnimatedNumber value={stats?.active || 0} /></span>
          </div>
        </div>

        <div style={{ position: 'relative', marginLeft: 'auto', display: 'flex', alignItems: 'center', zIndex: 50 }}>
          <button
            style={{ background: 'transparent', border: 'none', fontSize: '1.4rem', cursor: 'pointer', position: 'relative', padding: '0.5rem' }}
            onClick={() => setShowNotifications(!showNotifications)}
            title="Notification Center"
          >
            🔔
            {notifications.length > 0 && <span style={{ position: 'absolute', top: '0px', right: '0px', background: '#ef4444', color: 'white', fontSize: '0.65rem', padding: '2px 5px', borderRadius: '50%', fontWeight: 'bold' }}>{notifications.length}</span>}
          </button>

          {showNotifications && (
            <div className="glass-panel" style={{ position: 'absolute', top: '100%', right: '0', width: '380px', maxHeight: '450px', overflowY: 'auto', padding: '1rem', marginTop: '10px', boxShadow: '0 10px 30px rgba(0,0,0,0.8)', border: '1px solid var(--panel-border)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--panel-border)', paddingBottom: '0.8rem', marginBottom: '0.8rem' }}>
                <h3 style={{ fontSize: '1rem', margin: 0 }}>Notification History</h3>
                <button className="btn-secondary" style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem' }} onClick={() => setNotifications([])}>Clear</button>
              </div>
              {notifications.length === 0 ? <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', textAlign: 'center', margin: '2rem 0' }}>No recent alerts.</p> : notifications.map((n, i) => (
                <div key={i} style={{ padding: '0.8rem', borderBottom: '1px solid var(--bg-darker)', fontSize: '0.85rem', borderRadius: '6px', background: n.isError ? 'rgba(239, 68, 68, 0.05)' : 'rgba(34, 197, 94, 0.05)', marginBottom: '0.5rem' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
                    <span style={{ color: n.isError ? '#ef4444' : '#22c55e', fontWeight: 600 }}>{n.level}</span>
                    <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{new Date((n.time || 0) * 1000).toLocaleTimeString()}</span>
                  </div>
                  <div style={{ color: 'var(--text-primary)', lineHeight: 1.4 }}>{String(n.message)}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </header>

      <nav className="main-tabs" style={{ display: 'flex', gap: '1rem', paddingBottom: '0.5rem', marginBottom: '1.5rem', borderBottom: '1px solid var(--panel-border)' }}>
        <button
          style={{ padding: '1rem', background: 'transparent', border: 'none', color: activeTab === 'dashboard' ? 'var(--status-active)' : 'var(--text-muted)', borderBottom: activeTab === 'dashboard' ? '2px solid var(--status-active)' : '2px solid transparent', cursor: 'pointer', fontWeight: 600 }}
          onClick={() => setActiveTab('dashboard')}
        >
          📊 Dashboard
        </button>
        <button
          style={{ padding: '1rem', background: 'transparent', border: 'none', color: activeTab === 'proxies' ? 'var(--status-active)' : 'var(--text-muted)', borderBottom: activeTab === 'proxies' ? '2px solid var(--status-active)' : '2px solid transparent', cursor: 'pointer', fontWeight: 600 }}
          onClick={() => setActiveTab('proxies')}
        >
          🌐 Proxy Manager
        </button>
      </nav>

      {activeTab === 'dashboard' ? (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4 }}>
          <AnimatePresence mode="wait">
            <div className="action-toolbar">
              <div className="toolbar-group">
                <button className="btn-success" title="Tüm Aktif Müşterileri Başlatır" onClick={() => handleAction('workers/start_all')}>▶ Start Auto</button>
                <button className="btn-secondary" title="Tüm Müşterileri Beklemeye Alır" onClick={() => handleAction('workers/stop_all')}>⏸ Standby</button>
                <button className="btn-emergency" title="DİKKAT: Tüm işlemleri zorla durdurur ve Chrome'u kapatır" onClick={() => handleAction('workers/kill_all')}>🛑 KILL SWITCH</button>
                <button className="btn-secondary" title="Tüm bildirim kanallarına test mesajı gönderir" onClick={async () => {
                  try {
                    const res = await apiFetch(`${API_BASE}/system/test_notification`, { method: 'POST' });
                    const data = await res.json();
                    const r = data.results || {};
                    const lines = [
                      `📋 Log: ${r.log ? '✅' : '❌'}`,
                      `💬 Discord: ${r.discord === true ? '✅' : r.discord === false ? '⚠️ Webhook yok' : '❌ ' + r.discord}`,
                      `📱 CallMeBot: ${r.callmebot === true ? '✅' : r.callmebot === false ? '⚠️ Ayar yok' : '❌ ' + r.callmebot}`,
                      `🤖 Telegram Bot: ${r.telegram === true ? '✅' : r.telegram === false ? '⚠️ Bot yok' : '❌ ' + r.telegram}`,
                    ];
                    alert('Test Bildirim Sonuçları:\n\n' + lines.join('\n'));
                  } catch (e) { alert('Hata: ' + e.message); }
                }}>🧪 Test Bildirim</button>
              </div>

              <div className="toolbar-group">
                <button className="btn-secondary" onClick={() => document.getElementById('excel-upload').click()}>📥 Import</button>
                <input type="file" id="excel-upload" accept=".xlsx" style={{ display: 'none' }} onChange={importExcel} />
                <button className="btn-secondary" onClick={exportExcel}>📤 Export</button>

                <button className="btn-primary" onClick={() => { setEditingUser(null); setShowAddModal(true) }}>➕ New Customer</button>
                <button className="btn-secondary" onClick={() => setShowSettingsModal(true)}>⚙️ Settings</button>
              </div>
            </div>

            <div className="glass-panel" style={{ marginBottom: '1.5rem', padding: '1.5rem', height: '280px' }}>
              <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '1rem', color: 'var(--text-secondary)' }}>Live Activity Timeline</h2>
              <ResponsiveContainer width="100%" height="80%">
                <LineChart data={chartData}>
                  <XAxis dataKey="time" stroke="#64748b" fontSize={12} />
                  <YAxis stroke="#64748b" fontSize={12} width={30} />
                  <Tooltip contentStyle={{ backgroundColor: '#0f172a', border: '1px solid #1e293b' }} />
                  <Line type="monotone" dataKey="active" stroke="#22c55e" strokeWidth={2} dot={false} name="Active" />
                  <Line type="monotone" dataKey="cooldown" stroke="#f59e0b" strokeWidth={2} dot={false} name="Cooldown" />
                  <Line type="monotone" dataKey="stopped" stroke="#ef4444" strokeWidth={2} dot={false} name="Stopped" />
                </LineChart>
              </ResponsiveContainer>
            </div>

            <div className="dashboard-grid">
              <main className="glass-panel" style={{ display: 'flex', flexDirection: 'column' }}>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem', flexWrap: 'wrap', gap: '1rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                    <h2 style={{ fontSize: '1.2rem', fontWeight: 600 }}>Customer Base</h2>
                    {selectedIds.length > 0 && (
                      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', background: 'rgba(59, 130, 246, 0.1)', padding: '0.3rem 0.8rem', borderRadius: '8px', border: '1px solid rgba(59, 130, 246, 0.3)' }}>
                        <span style={{ fontSize: '0.85rem', fontWeight: 600, marginRight: '0.5rem', color: '#60a5fa' }}>{selectedIds.length} selected</span>
                        <button className="btn-success" style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem' }} onClick={() => handleBulkAction('start')}>▶ Start</button>
                        <button className="btn-secondary" style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem' }} onClick={() => handleBulkAction('stop')}>⏸ Stop</button>
                        <button className="btn-danger" style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem' }} onClick={() => handleBulkAction('delete')}>🗑 Delete</button>
                      </div>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: '1rem' }}>
                    <input
                      type="text"
                      className="search-input"
                      placeholder="Search name, email..."
                      value={searchQuery}
                      onChange={e => setSearchQuery(e.target.value)}
                    />
                    <select className="filter-select" value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
                      <option value="ALL">All Statuses</option>
                      <option value="ACTIVE">Running Only</option>
                      <option value="COOLDOWN">Cooldown Only</option>
                      <option value="STOPPED">Stopped Only</option>
                    </select>
                  </div>
                </div>

                <div className="table-container" style={{ flexGrow: 1 }}>
                  <table>
                    <thead>
                      <tr>
                        <th style={{ width: '40px' }}>
                          <input
                            type="checkbox"
                            checked={filteredWorkers.length > 0 && selectedIds.length === filteredWorkers.length}
                            onChange={e => setSelectedIds(e.target.checked ? filteredWorkers.map(w => w.id) : [])}
                          />
                        </th>
                        <th title="Sistem Kimliği">ID</th>
                        <th>Customer</th>
                        <th>Contact</th>
                        <th>Operational Status</th>
                        <th title="Günlük veya toplam randevu denemesi limit/sayacı">Limit</th>
                        <th title="Son kontrolün yapıldığı zaman damgası">Last Check</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredWorkers.map((w, i) => {
                        const sLower = w.status?.toLowerCase() || '';
                        let statusClass = 'status-waiting';
                        let statusText = 'Waiting';

                        if (!w.is_active) {
                          statusClass = 'status-stopped';
                          statusText = 'Stopped';
                        } else if (sLower.includes('çal') || sLower.includes('kontrol')) {
                          statusClass = 'status-active';
                          statusText = w.status;
                        } else if (sLower.includes('tatil')) {
                          statusClass = 'status-cooldown';
                          statusText = w.status;
                        } else if (w.status) {
                          statusClass = 'status-waiting';
                          statusText = w.status;
                        }

                        return (
                          <motion.tr
                            key={w.id}
                            initial={{ opacity: 0, y: 15 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: i * 0.05, duration: 0.3 }}
                          >
                            <td>
                              <input
                                type="checkbox"
                                checked={selectedIds.includes(w.id)}
                                onChange={e => {
                                  if (e.target.checked) setSelectedIds(prev => [...prev, w.id])
                                  else setSelectedIds(prev => prev.filter(id => id !== w.id))
                                }}
                              />
                            </td>
                            <td style={{ color: "var(--text-muted)", fontWeight: 600 }}>#{w.id}</td>
                            <td style={{ fontWeight: 600 }}>
                              {w.first_name} {w.last_name}
                              {w.is_scout && <span title="Scout Bot" style={{ marginLeft: '6px', fontSize: '0.9rem' }}>🎯</span>}
                            </td>
                            <td>
                              <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                                <span>{w.email}</span>
                                {w.proxy_address && <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Proxy: {w.proxy_address.split('@').pop()}</span>}
                              </div>
                            </td>
                            <td>
                              <div className={`status-badge ${statusClass}`}>
                                <span className="status-dot"></span>
                                {statusText}
                              </div>
                            </td>
                            <td>{w.check_count} Deneme</td>
                            <td style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>{w.last_check || '-'}</td>

                            <td>
                              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                                <button className="btn-success" style={{ padding: "0.4rem 0.6rem", fontSize: "0.75rem" }} title="Başlat" onClick={() => handleAction(`workers/${w.id}/start`)}>▶</button>
                                <button className="btn-secondary" style={{ padding: "0.4rem 0.6rem", fontSize: "0.75rem", color: '#a78bfa', borderColor: 'var(--panel-border)' }} title="Müşteri Logları" onClick={() => setSelectedLogWorker(w)}>📄</button>
                                <button className="btn-secondary" style={{ padding: "0.4rem 0.6rem", fontSize: "0.75rem", borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }} title="Beklemeye Al" onClick={() => handleAction(`workers/${w.id}/stop`)}>⏸</button>
                                <button className="btn-secondary" style={{ padding: "0.4rem 0.6rem", fontSize: "0.75rem", color: '#60a5fa' }} title="Ayarları Düzenle" onClick={() => handleEdit(w)}>⚙️</button>
                                <button className="btn-secondary" style={{ padding: "0.4rem 0.6rem", fontSize: "0.75rem", color: 'var(--status-cooldown)' }} title="Ban/Tatil Süresini Sıfırla" onClick={() => handleAction(`workers/${w.id}/clear_cooldown`)}>⏳</button>
                                <button className="btn-danger" style={{ padding: "0.4rem 0.6rem", fontSize: "0.75rem" }} title="Müşteriyi Sil" onClick={() => deleteWorker(w.id)}>🗑</button>
                              </div>
                            </td>
                          </motion.tr>
                        )
                      })}
                      {filteredWorkers.length === 0 && <tr><td colSpan="7" style={{ textAlign: "center", padding: "3rem", color: "var(--text-muted)" }}>No customers match the current filters.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </main>

              <aside>
                <LogPanel logs={sysLogs} onClear={() => setSysLogs([])} />
              </aside>
            </div>
          </AnimatePresence>
        </motion.div>
      ) : (
        <ProxyManagerTab />
      )}

      <AnimatePresence>
        {showAddModal && <AddCustomerModal user={editingUser} onClose={() => setShowAddModal(false)} onAdd={fetchData} />}
        {showSettingsModal && <GlobalSettingsModal onClose={() => setShowSettingsModal(false)} />}
        {selectedLogWorker && <WorkerLogModal worker={selectedLogWorker} onClose={() => setSelectedLogWorker(null)} />}
      </AnimatePresence>
    </>
  )
}

function LogPanel({ logs, onClear }) {
  const [autoScroll, setAutoScroll] = useState(true)
  const terminalRef = useRef(null)

  useEffect(() => {
    if (autoScroll && terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight
    }
  }, [logs, autoScroll])

  return (
    <div className="log-panel-wrapper">
      <div className="log-toolbar">
        <h3 style={{ fontSize: '0.9rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span className="status-dot status-active" style={{ display: 'inline-block', width: '8px', height: '8px', background: 'var(--status-active)', borderRadius: '50%', boxShadow: '0 0 5px var(--status-active)' }}></span>
          System Logs
        </h3>
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'center' }}>
          <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '4px' }}>
            <input type="checkbox" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} />
            Auto-scroll
          </label>
          <button className="btn-secondary" style={{ padding: '0.2rem 0.6rem', fontSize: '0.75rem' }} onClick={onClear}>Clear</button>
        </div>
      </div>
      <div className="log-terminal" ref={terminalRef}>
        {logs.map((log, i) => {
          let messageColor = 'var(--text-secondary)';
          let levelColor = '#3b82f6'; // Generic info blue

          const safeMessage = log?.message ? String(log.message) : '';
          const msgLower = safeMessage.toLowerCase();
          const safeLevel = log?.level ? String(log.level) : 'INFO';

          if (safeLevel === 'WARNING' || safeLevel === 'ERROR') {
            messageColor = '#e74c3c';
            levelColor = '#ef4444';
          } else if (msgLower.includes('randevu bulundu') || msgLower.includes('başarılı')) {
            messageColor = '#22c55e';
            levelColor = '#22c55e';
          }

          if (safeLevel === 'WARNING') levelColor = '#f59e0b';
          if (msgLower.includes('aday')) messageColor = '#64748b'; // Dim scout logs

          return (
            <div key={i} className="log-entry">
              <span style={{ color: '#0ea5e9', marginRight: '10px' }}>
                [{new Date((log?.time || 0) * 1000).toLocaleTimeString()}]
              </span>
              <span style={{ fontWeight: 600, color: levelColor, marginRight: '10px', fontSize: '0.85em', width: '60px', display: 'inline-block' }}>
                {safeLevel}
              </span>
              <span style={{ color: messageColor }}>
                {safeMessage}
              </span>
            </div>
          )
        })}
        {logs.length === 0 && <span style={{ color: 'var(--text-muted)' }}>Waiting for system events...</span>}
      </div>
    </div>
  )
}

function GlobalSettingsModal({ onClose }) {
  const [settings, setSettings] = useState({
    "2captcha_key": "", "discord_webhook": "", "telegram_bot_token": "",
    "telegram_admin_id": "", "active_hours": "", "scout_mode": "0", "max_workers": "15"
  })

  useEffect(() => {
    apiFetch(`${API_BASE}/system/settings`)
      .then(res => res.json())
      .then(data => { if (data.settings) setSettings(prev => ({ ...prev, ...data.settings })) })
  }, [])

  const handleChange = (e) => setSettings({ ...settings, [e.target.name]: e.target.type === 'checkbox' ? (e.target.checked ? "1" : "0") : e.target.value })

  const handleSubmit = async (e) => {
    e.preventDefault()
    try {
      await apiFetch(`${API_BASE}/system/settings/bulk`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings })
      })
      alert("Settings saved successfully!")
      onClose()
    } catch (err) {
      alert("Error: " + err.message)
    }
  }

  return (
    <motion.div className="modal-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <motion.div
        className="modal-content" style={{ padding: '2rem' }}
        initial={{ opacity: 0, scale: 0.95, y: -20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 20 }}
        transition={{ type: 'spring', damping: 25, stiffness: 300 }}
      >
        <h2 style={{ marginBottom: "1.5rem", fontSize: '1.4rem' }}>System Configuration</h2>
        <form onSubmit={handleSubmit}>

          <div className="form-group">
            <label>2Captcha API Key</label>
            <input name="2captcha_key" value={settings["2captcha_key"]} onChange={handleChange} />
          </div>

          <div className="form-group">
            <label>Discord Webhook URL</label>
            <input name="discord_webhook" value={settings["discord_webhook"]} onChange={handleChange} />
          </div>

          <div className="form-group">
            <label>Telegram BotFather Token</label>
            <input name="telegram_bot_token" value={settings["telegram_bot_token"]} onChange={handleChange} />
          </div>

          <div className="form-group">
            <label>Telegram Admin IDs (Comma separated)</label>
            <input name="telegram_admin_id" value={settings["telegram_admin_id"]} onChange={handleChange} />
          </div>

          <div className="form-group">
            <label>Active Operating Hours (e.g., 08:00-23:00)</label>
            <input name="active_hours" value={settings["active_hours"]} onChange={handleChange} />
          </div>

          <div className="form-group">
            <label>Max Concurrent Workers (Chrome instances)</label>
            <input type="number" name="max_workers" value={settings["max_workers"]} onChange={handleChange} min="1" max="100" />
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.3rem' }}>Recommended: 8-10 for 4GB RAM, 15-20 for 8GB RAM, 30-40 for 16GB RAM. Requires restart.</p>
          </div>

          <div className="form-group" style={{ background: 'rgba(0,0,0,0.2)', padding: '1rem', borderRadius: '8px', marginTop: '1.5rem' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '10px', margin: 0, cursor: 'pointer', color: 'var(--status-active)', fontWeight: 600 }}>
              <input type="checkbox" name="scout_mode" checked={settings["scout_mode"] === "1"} onChange={handleChange} style={{ width: 'auto', margin: 0 }} />
              Enable Scout Mode (Centralized Scanning)
            </label>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.5rem', marginLeft: '24px' }}>Routes traffic through dedicated scout nodes to preserve main proxies.</p>
          </div>

          <div className="modal-actions">
            <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn-primary">Save Changes</button>
          </div>
        </form>
      </motion.div>
    </motion.div>
  )
}

function AddCustomerModal({ user, onClose, onAdd }) {
  const [formData, setFormData] = useState({
    email: '', password_enc: '', first_name: '', last_name: '', phone: '',
    jurisdiction: 'Istanbul', location: 'Spain', category: 'Normal', appointment_for: 'Individual',
    visa_type: 'Schengen', visa_sub_type: 'Tourism', proxy_address: '',
    check_interval: 60, minimum_days: 0, is_active: true, headless: true, is_scout: false, auto_book: false,
    email_app_password: '', travel_date: ''
  })

  useEffect(() => {
    if (user) {
      // Do not sync state in effect directly if it can cause cascade, but in this specific hook
      // it is a controlled input effect. Adding setTimeout prevents strict mode synchronous throw.
      setTimeout(() => setFormData({ ...user, password_enc: '' }), 0)
    }
  }, [user])

  const handleChange = (e) => {
    const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value
    setFormData({ ...formData, [e.target.name]: value })
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    try {
      const url = user ? `${API_BASE}/workers/${user.id}` : `${API_BASE}/workers`
      const payload = { ...formData }
      if (user && !payload.password_enc) delete payload.password_enc
      if (user && !payload.email_app_password) delete payload.email_app_password

      const res = await apiFetch(url, {
        method: user ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
      if (res.ok) { onAdd(); onClose() }
      else { const errorData = await res.json(); alert("Error: " + JSON.stringify(errorData)) }
    } catch (err) { alert("Execution error: " + err.message) }
  }

  return (
    <motion.div className="modal-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <motion.div
        className="modal-content" style={{ padding: '2rem' }}
        initial={{ opacity: 0, scale: 0.95, y: -20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 20 }}
        transition={{ type: 'spring', damping: 25, stiffness: 300 }}
      >
        <h2 style={{ marginBottom: "1.5rem", fontSize: '1.4rem' }}>{user ? 'Edit Customer' : 'Add New Customer'}</h2>
        <form onSubmit={handleSubmit}>

          <div className="form-row">
            <div className="form-group"><label>First Name</label><input name="first_name" value={formData.first_name} required onChange={handleChange} /></div>
            <div className="form-group"><label>Last Name</label><input name="last_name" value={formData.last_name} required onChange={handleChange} /></div>
          </div>

          <div className="form-row">
            <div className="form-group"><label>BLS Email</label><input name="email" type="email" value={formData.email} required onChange={handleChange} /></div>
            <div className="form-group"><label>BLS Password {user && '(Leave blank to keep)'}</label><input name="password_enc" type="password" required={!user} onChange={handleChange} /></div>
          </div>

          <div className="form-row">
            <div className="form-group"><label>📧 Email App Password {user && '(Blank = keep)'}</label><input name="email_app_password" type="password" placeholder="Gmail: App Password | Outlook: Normal şifre" onChange={handleChange} /></div>
            <div className="form-group"><label>✈️ Travel Date</label><input name="travel_date" type="date" value={formData.travel_date} onChange={handleChange} /></div>
          </div>

          <div className="form-row">
            <div className="form-group"><label>Phone</label><input name="phone" value={formData.phone} onChange={handleChange} /></div>
            <div className="form-group"><label>Proxy (user:pass@ip:port)</label><input name="proxy_address" value={formData.proxy_address} placeholder="Empty = Local IP" onChange={handleChange} /></div>
          </div>

          <div className="form-row">
            <div className="form-group"><label>Jurisdiction</label><input name="jurisdiction" value={formData.jurisdiction} onChange={handleChange} /></div>
            <div className="form-group"><label>Location</label><input name="location" value={formData.location} onChange={handleChange} /></div>
          </div>

          <div className="form-row">
            <div className="form-group"><label>Visa Type</label><input name="visa_type" value={formData.visa_type} onChange={handleChange} /></div>
            <div className="form-group"><label>Category</label><input name="category" value={formData.category} onChange={handleChange} /></div>
          </div>

          <div className="form-row">
            <div className="form-group"><label>Visa Sub Type</label><input name="visa_sub_type" value={formData.visa_sub_type} onChange={handleChange} /></div>
            <div className="form-group"><label>Appointment For</label><input name="appointment_for" value={formData.appointment_for} onChange={handleChange} /></div>
          </div>

          <div className="form-row">
            <div className="form-group"><label>Check Interval (sec)</label><input name="check_interval" type="number" value={formData.check_interval} onChange={handleChange} /></div>
            <div className="form-group"><label>Target Min Days</label><input name="minimum_days" type="number" value={formData.minimum_days} onChange={handleChange} /></div>
          </div>

          <div style={{ background: 'rgba(0,0,0,0.2)', padding: '1.5rem', borderRadius: '8px', marginTop: '1rem' }}>
            <label style={{ marginBottom: '1rem', display: 'block', color: 'var(--text-secondary)', fontWeight: 600 }}>Advanced Configuration</label>
            <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '0.85rem' }}>
                <input type="checkbox" name="headless" checked={formData.headless} onChange={handleChange} /> Headless (Invisible)
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', color: 'var(--accent-color)', fontSize: '0.85rem' }}>
                <input type="checkbox" name="is_scout" checked={formData.is_scout} onChange={handleChange} /> Scout Node 🎯
              </label>
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', color: 'var(--status-cooldown)', fontSize: '0.85rem' }}>
                <input type="checkbox" name="auto_book" checked={formData.auto_book} onChange={handleChange} /> Auto-Book 📌
              </label>
            </div>
          </div>

          <div className="modal-actions">
            <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn-primary">Save Profile</button>
          </div>

        </form>
      </motion.div>
    </motion.div>
  )
}

function ProxyManagerTab() {
  const [proxies, setProxies] = useState([])
  const [newProxies, setNewProxies] = useState("")

  const fetchProxies = async () => {
    try {
      const res = await apiFetch(`${API_BASE}/proxies`)
      if (res.ok) {
        const data = await res.json()
        setProxies(data.proxies || [])
      }
    } catch (err) { console.error(err) }
  }

  useEffect(() => {
    fetchProxies()
  }, [])

  const handleImport = async () => {
    const lines = newProxies.split('\\n').map(l => l.trim()).filter(l => l)
    if (!lines.length) return

    try {
      const res = await apiFetch(`${API_BASE}/proxies/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(lines)
      })
      if (res.ok) {
        setNewProxies("")
        fetchProxies()
      } else {
        alert("Import failed")
      }
    } catch (err) { alert(err.message) }
  }

  const deleteProxy = async (address) => {
    if (!confirm("Bu proxy'yi silmek istediğinize emin misiniz?")) return
    try {
      await apiFetch(`${API_BASE}/proxies/${encodeURIComponent(address)}`, { method: 'DELETE' })
      fetchProxies()
    } catch (err) { alert(err.message) }
  }

  return (
    <motion.div style={{ padding: '0.5rem 0' }} initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
      <div className="glass-panel" style={{ marginBottom: '2rem' }}>
        <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>Bulk Import Proxies</h2>
        <textarea
          placeholder="Format: user:pass@ip:port (Her satıra bir tane)"
          style={{ width: '100%', height: '100px', background: 'var(--bg-darker)', border: '1px solid var(--panel-border)', color: 'var(--text-primary)', padding: '0.5rem', borderRadius: '4px', fontFamily: 'monospace' }}
          value={newProxies}
          onChange={e => setNewProxies(e.target.value)}
        />
        <button className="btn-primary" style={{ marginTop: '1rem' }} onClick={handleImport}>➕ Add Proxies</button>
      </div>

      <div className="glass-panel">
        <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>Proxy Pool ({proxies.length})</h2>
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Address / IP</th>
                <th>Status</th>
                <th>Fails / Success</th>
                <th>Consecutive Fails</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {proxies.map((p, i) => (
                <motion.tr
                  key={i}
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.05 }}
                >
                  <td style={{ fontFamily: 'monospace', color: 'var(--text-secondary)' }}>{p.address}</td>
                  <td>
                    <span className={`status-badge ${p.status === 'Active' ? 'status-active' : p.status === 'Cooldown' ? 'status-cooldown' : 'status-stopped'}`}>
                      <span className="status-dot"></span>
                      {p.status}
                    </span>
                  </td>
                  <td>{p.fail_count} / <span style={{ color: 'var(--status-active)' }}>{p.success_count}</span></td>
                  <td>{p.consecutive_fails}</td>
                  <td>
                    <button className="btn-danger" style={{ padding: '0.3rem 0.6rem', fontSize: '0.8rem' }} onClick={() => deleteProxy(p.address)}>Sil</button>
                  </td>
                </motion.tr>
              ))}
              {proxies.length === 0 && <tr><td colSpan="5" style={{ textAlign: "center", padding: "2rem" }}>Veritabanında proxy bulunmuyor.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </motion.div>
  )
}

function WorkerLogModal({ worker, onClose }) {
  const terminalRef = useRef(null)
  const [workerLogs, setWorkerLogs] = useState([])

  useEffect(() => {
    // Fetch individual worker logs when modal opens
    apiFetch(`${API_BASE}/system/logs/${worker.id}`)
      .then(res => res.json())
      .then(data => {
        if (data.logs) {
          setWorkerLogs(data.logs)
        }
      })
      .catch(err => console.error("Error fetching worker logs:", err))
  }, [worker.id])

  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight
    }
  }, [workerLogs])

  return (
    <motion.div className="modal-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <motion.div
        className="modal-content" style={{ width: '800px', maxWidth: '95%' }}
        initial={{ opacity: 0, scale: 0.95, y: -20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 20 }}
        transition={{ type: 'spring', damping: 25, stiffness: 300 }}
      >
        <h2 style={{ marginBottom: "1rem", fontSize: '1.2rem', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span className="status-dot status-active" style={{ display: 'inline-block', width: '10px', height: '10px', background: 'var(--status-active)', borderRadius: '50%', boxShadow: '0 0 5px var(--status-active)' }}></span>
          Log Terminal: {worker.first_name} {worker.last_name}
        </h2>
        <div className="log-terminal" ref={terminalRef} style={{ height: '400px', marginBottom: '1rem' }}>
          {workerLogs.map((log, i) => {
            let messageColor = 'var(--text-secondary)';
            let levelColor = '#3b82f6';

            const safeMessage = log?.message ? String(log.message) : '';
            const msgLower = safeMessage.toLowerCase();
            const safeLevel = log?.level ? String(log.level) : 'INFO';

            if (safeLevel === 'WARNING' || safeLevel === 'ERROR') {
              messageColor = '#e74c3c';
              levelColor = '#ef4444';
            } else if (msgLower.includes('randevu bulundu') || msgLower.includes('başarılı')) {
              messageColor = '#22c55e';
              levelColor = '#22c55e';
            }
            if (safeLevel === 'WARNING') levelColor = '#f59e0b';
            if (msgLower.includes('aday')) messageColor = '#64748b';

            return (
              <div key={i} className="log-entry">
                <span style={{ color: '#0ea5e9', marginRight: '10px' }}>
                  [{new Date((log?.time || 0) * 1000).toLocaleTimeString()}]
                </span>
                <span style={{ fontWeight: 600, color: levelColor, marginRight: '10px', fontSize: '0.85em', width: '60px', display: 'inline-block' }}>
                  {safeLevel}
                </span>
                <span style={{ color: messageColor }}>
                  {safeMessage}
                </span>
              </div>
            )
          })}
          {workerLogs.length === 0 && <span style={{ color: 'var(--text-muted)' }}>No logs found for this customer. Is the bot running?</span>}
        </div>
        <div className="modal-actions">
          <button className="btn-secondary" onClick={onClose}>Close Window</button>
        </div>
      </motion.div>
    </motion.div>
  )
}

export default App

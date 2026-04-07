import { useState, useEffect, useCallback, useRef } from 'react'
import axios from 'axios'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import {
  TrendingUp, TrendingDown, Activity, AlertTriangle,
  BarChart2, Zap, Shield, ChevronUp, ChevronDown,
  Cpu, Wifi, WifiOff, RefreshCw,
} from 'lucide-react'
import './App.css'

// ── Constants ──────────────────────────────────────────────────────────────────
const API_BASE = '/api/v1'
const WS_URL = `ws://${window.location.host}/ws/dashboard`

// ── Custom hook: WebSocket live feed ──────────────────────────────────────────
function useLiveFeed(onMessage) {
  const wsRef = useRef(null)
  const [connected, setConnected] = useState(false)

  const connect = useCallback(() => {
    const ws = new WebSocket(WS_URL)
    ws.onopen = () => setConnected(true)
    ws.onclose = () => {
      setConnected(false)
      setTimeout(connect, 3000)   // Auto-reconnect
    }
    ws.onerror = () => ws.close()
    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        onMessage(data)
      } catch {}
    }
    wsRef.current = ws
  }, [onMessage])

  useEffect(() => {
    connect()
    return () => wsRef.current?.close()
  }, [connect])

  return connected
}

// ── Custom hook: REST polling ──────────────────────────────────────────────────
function usePoll(url, interval = 5000) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    const fetch_ = async () => {
      try {
        const res = await axios.get(url)
        if (active) { setData(res.data); setLoading(false) }
      } catch {}
    }
    fetch_()
    const id = setInterval(fetch_, interval)
    return () => { active = false; clearInterval(id) }
  }, [url, interval])

  return { data, loading }
}

// ── Formatters ─────────────────────────────────────────────────────────────────
const fmtINR = (val) =>
  val == null ? '—' :
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(val)

const fmtPct = (val) => val == null ? '—' : `${val > 0 ? '+' : ''}${val.toFixed(2)}%`
const fmtNum = (val, dp = 2) => val == null ? '—' : Number(val).toFixed(dp)

// ── P&L color helper ───────────────────────────────────────────────────────────
const pnlColor = (val) => val >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'

// ── Component: StatCard ────────────────────────────────────────────────────────
function StatCard({ icon: Icon, label, value, sub, color = 'var(--accent-blue)', positive }) {
  const c = positive === undefined ? color : (positive >= 0 ? 'var(--accent-green)' : 'var(--accent-red)')
  return (
    <div className="stat-card card animate-in">
      <div className="stat-icon" style={{ background: `${c}22`, color: c }}>
        <Icon size={18} />
      </div>
      <div className="stat-body">
        <div className="stat-label">{label}</div>
        <div className="stat-value mono" style={{ color: c }}>{value}</div>
        {sub && <div className="stat-sub">{sub}</div>}
      </div>
    </div>
  )
}

// ── Component: LiveBadge ───────────────────────────────────────────────────────
function LiveBadge({ connected }) {
  return (
    <span className={`badge ${connected ? 'badge-green' : 'badge-red'}`} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      {connected ? <Wifi size={10} /> : <WifiOff size={10} />}
      {connected ? 'LIVE' : 'DISCONNECTED'}
    </span>
  )
}

// ── Component: PnlChart ────────────────────────────────────────────────────────
function PnlChart({ data }) {
  const isPositive = (data[data.length - 1]?.pnl || 0) >= 0
  const color = isPositive ? '#10b981' : '#ef4444'
  return (
    <div className="card" style={{ height: 240 }}>
      <div className="card-header">
        <span className="card-title">Session P&amp;L</span>
        <span className="text-muted" style={{ fontSize: 11 }}>Running total (INR)</span>
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={color} stopOpacity={0.25} />
              <stop offset="95%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#64748b' }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fontSize: 10, fill: '#64748b' }} axisLine={false} tickLine={false}
            tickFormatter={(v) => `₹${(v/1000).toFixed(1)}k`} />
          <Tooltip
            contentStyle={{ background: '#161e2e', border: '1px solid #334155', borderRadius: 8 }}
            labelStyle={{ color: '#94a3b8' }}
            formatter={(v) => [fmtINR(v), 'P&L']}
          />
          <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" strokeDasharray="4 4" />
          <Area type="monotone" dataKey="pnl" stroke={color} strokeWidth={2}
            fill="url(#pnlGrad)" dot={false} activeDot={{ r: 4, strokeWidth: 0 }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Component: GreeksPanel ─────────────────────────────────────────────────────
function GreeksPanel({ greeks }) {
  const items = [
    { label: 'Δ Delta',  value: fmtNum(greeks?.net_delta, 2),   color: '#3b82f6' },
    { label: 'Γ Gamma',  value: fmtNum(greeks?.net_gamma, 4),   color: '#8b5cf6' },
    { label: 'Θ Theta',  value: fmtNum(greeks?.net_theta, 2),   color: '#f59e0b' },
    { label: 'V Vega',   value: fmtNum(greeks?.net_vega, 2),    color: '#06b6d4' },
  ]
  return (
    <div className="card">
      <div className="card-header"><span className="card-title">Portfolio Greeks</span></div>
      <div className="greeks-grid">
        {items.map(({ label, value, color }) => (
          <div key={label} className="greek-item">
            <div className="greek-label" style={{ color }}>{label}</div>
            <div className="greek-value mono">{value}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Component: PositionsTable ──────────────────────────────────────────────────
function PositionsTable({ positions }) {
  if (!positions?.length) {
    return (
      <div className="card">
        <div className="card-header"><span className="card-title">Open Positions</span></div>
        <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-secondary)' }}>
          <Activity size={32} style={{ margin: '0 auto 8px', opacity: 0.4 }} />
          <div>No open positions</div>
        </div>
      </div>
    )
  }
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Open Positions</span>
        <span className="badge badge-blue">{positions.length}</span>
      </div>
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Symbol</th><th>Qty</th><th>Avg Entry</th>
              <th>LTP</th><th>Unrealised P&L</th><th>Strategy</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p, i) => (
              <tr key={i}>
                <td className="mono" style={{ color: 'var(--accent-cyan)' }}>{p.symbol}</td>
                <td className="mono">{p.quantity}</td>
                <td className="mono">{fmtINR(p.average_entry_price)}</td>
                <td className="mono">{fmtINR(p.current_price)}</td>
                <td className="mono" style={{ color: pnlColor(p.unrealised_pnl) }}>
                  {p.unrealised_pnl >= 0 ? '▲' : '▼'} {fmtINR(p.unrealised_pnl)}
                </td>
                <td><span className="badge badge-purple">{p.strategy || '—'}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Component: RiskMetrics ─────────────────────────────────────────────────────
function RiskMetrics({ metrics }) {
  const items = [
    { label: 'Win Rate',      value: `${fmtNum(metrics?.win_rate_pct, 1)}%`, color: '#10b981' },
    { label: 'Total Trades',  value: metrics?.total_trades ?? '—',           color: '#3b82f6' },
    { label: 'Profit Factor', value: fmtNum(metrics?.profit_factor),          color: '#f59e0b' },
    { label: 'Sharpe Ratio',  value: fmtNum(metrics?.sharpe_ratio),           color: '#8b5cf6' },
    { label: 'Avg Win',       value: fmtINR(metrics?.avg_win_inr),            color: '#10b981' },
    { label: 'Max Drawdown',  value: fmtINR(metrics?.max_drawdown_inr),       color: '#ef4444' },
  ]
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Performance Metrics</span>
        <span className="badge badge-yellow">Session</span>
      </div>
      <div className="metrics-grid">
        {items.map(({ label, value, color }) => (
          <div key={label} className="metric-item">
            <div className="metric-label">{label}</div>
            <div className="metric-value mono" style={{ color }}>{value}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Component: AlertFeed ───────────────────────────────────────────────────────
function AlertFeed({ alerts }) {
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Alert Feed</span>
        {alerts.length > 0 && <span className="badge badge-yellow">{alerts.length}</span>}
      </div>
      {alerts.length === 0
        ? <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-muted)' }}>No alerts</div>
        : (
          <div className="alert-list">
            {alerts.slice(-10).reverse().map((a, i) => (
              <div key={i} className={`alert-item alert-${a.severity?.toLowerCase()}`}>
                <AlertTriangle size={14} />
                <div>
                  <div className="alert-title">{a.title}</div>
                  <div className="alert-msg">{a.message}</div>
                </div>
                <div className="alert-time">{a.timestamp?.slice(11, 19)}</div>
              </div>
            ))}
          </div>
        )
      }
    </div>
  )
}

// ── Component: SignalFeed ──────────────────────────────────────────────────────
function SignalFeed({ signals }) {
  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">Signal Feed</span>
        <span className="badge badge-purple">LLM + Rule</span>
      </div>
      {signals.length === 0
        ? <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-muted)' }}>No signals yet</div>
        : (
          <div className="signal-list">
            {signals.slice(-8).reverse().map((s, i) => (
              <div key={i} className="signal-item animate-in">
                <div className="signal-strategy">
                  <span className="badge badge-purple">{s.strategy}</span>
                  <span className="badge badge-blue">{s.underlying}</span>
                </div>
                <div className="signal-confidence">
                  <div className="confidence-bar">
                    <div style={{ width: `${(s.confidence || 0) * 100}%`, background: 'var(--accent-purple)' }} />
                  </div>
                  <span className="mono" style={{ fontSize: 11 }}>
                    {((s.confidence || 0) * 100).toFixed(0)}%
                  </span>
                </div>
                {s.reasoning && (
                  <div className="signal-reason">{s.reasoning.slice(0, 120)}...</div>
                )}
              </div>
            ))}
          </div>
        )
      }
    </div>
  )
}

// ── Main App ──────────────────────────────────────────────────────────────────
export default function App() {
  const [pnlSeries, setPnlSeries] = useState([])
  const [totalPnl, setTotalPnl] = useState(0)
  const [realizedPnl, setRealizedPnl] = useState(0)
  const [unrealizedPnl, setUnrealizedPnl] = useState(0)
  const [openPositions, setOpenPositions] = useState(0)
  const [positions, setPositions] = useState([])
  const [alerts, setAlerts] = useState([])
  const [signals, setSignals] = useState([])

  const { data: Greeks } = usePoll(`${API_BASE}/risk/greeks`, 10000)
  const { data: metrics } = usePoll(`${API_BASE}/risk/metrics`, 15000)
  const { data: posData } = usePoll(`${API_BASE}/positions/pnl`, 5000)

  // Sync REST pnl data
  useEffect(() => {
    if (!posData) return
    setTotalPnl(posData.total_pnl || 0)
    setRealizedPnl(posData.session_realized_pnl || 0)
    setUnrealizedPnl(posData.unrealized_pnl || 0)
    setOpenPositions(posData.open_positions || 0)
    setPositions(posData.positions || [])
  }, [posData])

  // WebSocket message handler
  const onWsMessage = useCallback((data) => {
    const evt = data.event || data.type
    if (evt === 'TRADE_PNL_UPDATE') {
      const pnl = data.total_pnl || 0
      setTotalPnl(pnl)
      setRealizedPnl(data.session_realized_pnl || 0)
      setUnrealizedPnl(data.unrealized_pnl || 0)
      setOpenPositions(data.open_positions || 0)
      if (data.positions) setPositions(data.positions)
      setPnlSeries(prev => {
        const now = new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
        const next = [...prev, { time: now, pnl }]
        return next.slice(-60)   // Keep last 60 data points
      })
    } else if (evt === 'RISK_ALERT') {
      setAlerts(prev => [...prev, data.alert].slice(-50))
    } else if (evt === 'SIGNAL_GENERATED') {
      setSignals(prev => [...prev, data.signal].slice(-20))
    }
  }, [])

  const wsConnected = useLiveFeed(onWsMessage)

  // Seed chart with zeros
  useEffect(() => {
    const seed = Array.from({ length: 20 }, (_, i) => ({
      time: new Date(Date.now() - (20 - i) * 60000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }),
      pnl: 0,
    }))
    setPnlSeries(seed)
  }, [])

  const capitalBase = 500000

  return (
    <div className="app">
      {/* ── Header ── */}
      <header className="header">
        <div className="header-left">
          <div className="logo">
            <Zap size={20} style={{ color: '#3b82f6' }} />
            <span>NSE Options Trader</span>
          </div>
          <span className="header-sub">NIFTY &amp; BANKNIFTY · Dhan · Gemini AI</span>
        </div>
        <div className="header-right">
          <LiveBadge connected={wsConnected} />
          <span className="mode-badge badge badge-yellow">PAPER MODE</span>
          <span className="header-time mono" id="clock">
            {new Date().toLocaleTimeString('en-IN')} IST
          </span>
        </div>
      </header>

      {/* ── Main grid ── */}
      <main className="main-grid">
        {/* Row 1: Key stats */}
        <div className="stats-row">
          <StatCard
            icon={totalPnl >= 0 ? TrendingUp : TrendingDown}
            label="Total P&L"
            value={fmtINR(totalPnl)}
            sub={`${fmtPct((totalPnl / capitalBase) * 100)} of capital`}
            positive={totalPnl}
          />
          <StatCard
            icon={BarChart2}
            label="Realised P&L"
            value={fmtINR(realizedPnl)}
            color="var(--accent-green)"
            positive={realizedPnl}
          />
          <StatCard
            icon={Activity}
            label="Unrealised P&L"
            value={fmtINR(unrealizedPnl)}
            positive={unrealizedPnl}
          />
          <StatCard
            icon={Cpu}
            label="Open Positions"
            value={openPositions}
            sub="Max: 5"
            color="var(--accent-blue)"
          />
          <StatCard
            icon={Shield}
            label="Capital"
            value={fmtINR(capitalBase)}
            sub="PAPER mode"
            color="var(--accent-purple)"
          />
        </div>

        {/* Row 2: P&L chart + Greeks */}
        <div className="row-2">
          <div className="pnl-chart-wrap">
            <PnlChart data={pnlSeries} />
          </div>
          <div className="greeks-wrap">
            <GreeksPanel greeks={Greeks} />
            <RiskMetrics metrics={metrics} />
          </div>
        </div>

        {/* Row 3: Positions */}
        <PositionsTable positions={positions} />

        {/* Row 4: Signals + Alerts */}
        <div className="row-4">
          <SignalFeed signals={signals} />
          <AlertFeed alerts={alerts} />
        </div>
      </main>

      <footer className="footer">
        ⚠️ This is a paper trading system. No real orders are placed. Always verify before enabling live mode.
      </footer>
    </div>
  )
}

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

const API = '/api/v1'
const terminal = new Set(['succeeded', 'failed', 'cancelled'])

function bytes(value = 0) {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`
  return `${(value / 1024 ** 2).toFixed(1)} MiB`
}

function elapsed(job) {
  const start = job.started_at || job.created_at
  const end = job.finished_at || new Date().toISOString()
  return start ? `${Math.max(0, (Date.parse(end) - Date.parse(start)) / 1000).toFixed(1)}s` : '—'
}

function printerLabel(printer) {
  const state = printer?.print_stats?.state
  if (!state) return 'Printer online'
  if (['standby', 'complete', 'cancelled'].includes(state)) return 'Printer ready'
  if (state === 'printing') return 'Printer printing'
  if (state === 'paused') return 'Printer paused'
  if (state === 'error') return 'Printer error'
  return `Printer ${state}`
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem('axiom-token') || '')
  const [draftToken, setDraftToken] = useState(token)
  const [profiles, setProfiles] = useState(null)
  const [jobs, setJobs] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [selected, setSelected] = useState(null)
  const [logs, setLogs] = useState([])
  const [preview, setPreview] = useState(null)
  const [files, setFiles] = useState([])
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [printer, setPrinter] = useState(null)
  const [form, setForm] = useState({ project_name: 'axiom-slice', machine: '', process: '', filament: '', bed_type: 'smooth-high-temp', auto_orient: true, export_3mf: false })
  const [overrides, setOverrides] = useState({})
  const [enabledOverrides, setEnabledOverrides] = useState({})
  const fileRef = useRef(null)

  const request = useCallback(async (path, options = {}) => {
    const headers = new Headers(options.headers || {})
    headers.set('Authorization', `Bearer ${token}`)
    const response = await fetch(`${API}${path}`, { ...options, headers })
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`
      try { detail = (await response.json()).detail || detail } catch { /* not JSON */ }
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    }
    return response
  }, [token])

  const loadJobs = useCallback(async () => {
    if (!token) return
    try {
      const data = await (await request('/jobs?limit=40')).json()
      setJobs(data.jobs)
      setSelectedId((current) => current || data.jobs[0]?.id || null)
    } catch (error) {
      if (String(error).includes('401')) setToken('')
      setNotice(error.message)
    }
  }, [request, token])

  useEffect(() => {
    if (!token) return
    let live = true
    request('/profiles').then((response) => response.json())
      .then((profileData) => {
        if (!live) return
        setProfiles(profileData)
        setForm((current) => ({ ...current, ...profileData.defaults }))
        const initial = {}
        Object.entries(profileData.overrides).forEach(([name, spec]) => {
          initial[name] = spec.type === 'boolean' ? false : spec.type === 'enum' ? spec.values[0] : spec.minimum
        })
        setOverrides(initial)
        loadJobs()
      }).catch((error) => { if (live) setNotice(error.message) })
    return () => { live = false }
  }, [loadJobs, request, token])

  useEffect(() => {
    if (!token) return undefined
    let live = true
    const refresh = () => request('/printer/status').then((response) => response.json())
      .then((data) => { if (live) setPrinter(data.printer || null) })
      .catch(() => { if (live) setPrinter(null) })
    refresh()
    const timer = setInterval(refresh, 5000)
    return () => { live = false; clearInterval(timer) }
  }, [request, token])

  useEffect(() => {
    if (!token) return undefined
    const timer = setInterval(loadJobs, 3000)
    return () => clearInterval(timer)
  }, [loadJobs, token])

  useEffect(() => {
    if (!selectedId || !token) return undefined
    let live = true
    const refresh = async () => {
      try {
        const job = await (await request(`/jobs/${selectedId}`)).json()
        if (live) setSelected(job)
      } catch (error) { if (live) setNotice(error.message) }
    }
    refresh()
    const timer = setInterval(refresh, 1500)
    return () => { live = false; clearInterval(timer) }
  }, [request, selectedId, token])

  useEffect(() => {
    if (!selectedId || !token) return undefined
    const controller = new AbortController()
    setLogs([])
    ;(async () => {
      try {
        const response = await request(`/jobs/${selectedId}/events`, { signal: controller.signal })
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        while (true) {
          const { value, done } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const blocks = buffer.split('\n\n')
          buffer = blocks.pop() || ''
          blocks.forEach((block) => {
            const line = block.split('\n').find((entry) => entry.startsWith('data: '))
            const event = block.split('\n').find((entry) => entry.startsWith('event: '))?.slice(7)
            if (line && event === 'log') {
              try { setLogs((current) => [...current, JSON.parse(line.slice(6))]) } catch { /* partial line */ }
            }
          })
        }
      } catch (error) { if (error.name !== 'AbortError') setNotice(error.message) }
    })()
    return () => controller.abort()
  }, [request, selectedId, token])

  const thumbnailName = useMemo(() => {
    const names = Object.keys(selected?.artifacts || {}).filter((name) => name.endsWith('.png'))
    return names.find((name) => name.includes('300x300')) || names.at(-1)
  }, [selected])

  useEffect(() => {
    if (!thumbnailName || !selectedId) { setPreview(null); return undefined }
    let url
    let live = true
    request(`/jobs/${selectedId}/artifacts/${encodeURIComponent(thumbnailName)}`).then((response) => response.blob())
      .then((blob) => { if (live) { url = URL.createObjectURL(blob); setPreview(url) } }).catch(() => setPreview(null))
    return () => { live = false; if (url) URL.revokeObjectURL(url) }
  }, [request, selectedId, thumbnailName])

  const login = (event) => {
    event.preventDefault()
    const value = draftToken.trim()
    localStorage.setItem('axiom-token', value)
    setToken(value)
    setNotice('')
  }
  const logout = () => { localStorage.removeItem('axiom-token'); setToken(''); setProfiles(null) }

  const submit = async (event) => {
    event.preventDefault()
    if (!files.length) { setNotice('Choose at least one STL or 3MF model.'); return }
    setBusy(true); setNotice('')
    const chosen = Object.fromEntries(Object.entries(overrides).filter(([name]) => enabledOverrides[name]))
    const data = new FormData()
    files.forEach((file) => data.append('models', file))
    data.append('request', JSON.stringify({ ...form, overrides: chosen }))
    try {
      const job = await (await request('/jobs', { method: 'POST', body: data })).json()
      setSelectedId(job.id); setFiles([])
      if (fileRef.current) fileRef.current.value = ''
      await loadJobs()
    } catch (error) { setNotice(error.message) } finally { setBusy(false) }
  }

  const action = async (path, confirmation) => {
    if (confirmation && !window.confirm(confirmation)) return
    setBusy(true); setNotice('')
    try {
      await request(path, { method: 'POST', headers: confirmation ? { 'Content-Type': 'application/json' } : {}, body: confirmation ? JSON.stringify({ confirm: true }) : undefined })
      setSelected(await (await request(`/jobs/${selectedId}`)).json())
      setNotice(confirmation ? 'Print started.' : 'Action completed.')
    } catch (error) { setNotice(error.message) } finally { setBusy(false) }
  }

  const download = async (name) => {
    try {
      const blob = await (await request(`/jobs/${selectedId}/artifacts/${encodeURIComponent(name)}`)).blob()
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); URL.revokeObjectURL(url)
    } catch (error) { setNotice(error.message) }
  }

  const profileOptions = (kind) => kind === 'machine'
    ? profiles.profiles.machine
    : profiles.compatible_profiles?.[form.machine]?.[kind] || profiles.profiles[kind]

  const bedLabel = (value) => profiles.bed_types.find((bed) => bed.value === value)?.label || value || 'Legacy default'

  const selectProfile = (kind, value) => setForm((current) => {
    if (kind !== 'machine') return { ...current, [kind]: value }
    const compatible = profiles.compatible_profiles?.[value]
    if (!compatible) return { ...current, machine: value }
    return {
      ...current,
      machine: value,
      process: compatible.process.includes(current.process) ? current.process : compatible.process[0] || '',
      filament: compatible.filament.includes(current.filament) ? current.filament : compatible.filament[0] || '',
    }
  })

  if (!token) return <main className="login-shell"><form className="login-card" onSubmit={login}>
    <div className="mark">A</div><p className="eyebrow">Private slicing service</p><h1>Axiom Slice</h1>
    <p>Enter the bearer token stored for this service. It remains in this browser only.</p>
    <input autoFocus type="password" value={draftToken} onChange={(event) => setDraftToken(event.target.value)} placeholder="Bearer token" />
    <button disabled={!draftToken.trim()}>Connect</button>{notice && <p className="error">{notice}</p>}
  </form></main>

  if (!profiles) return <main className="loading"><div className="spinner" /><p>Loading Axiom Slice…</p>{notice && <button onClick={logout}>Reset token</button>}</main>

  return <main className="app-shell">
    <header><div><p className="eyebrow">OrcaSlicer · Isolated worker</p><h1>Axiom Slice</h1></div>
      <div className="header-status"><span className={`dot ${printer ? 'online' : ''}`} />{printer ? printerLabel(printer) : 'Printer unavailable'}<button className="quiet" onClick={logout}>Lock</button></div></header>
    {notice && <div className="notice" onClick={() => setNotice('')}>{notice}<span>×</span></div>}
    <section className="workspace">
      <form className="panel create" onSubmit={submit}>
        <div className="panel-title"><div><p className="eyebrow">New job</p><h2>Slice a model</h2></div><span className="step">01</span></div>
        <label>Project name<input value={form.project_name} onChange={(event) => setForm({ ...form, project_name: event.target.value })} /></label>
        <div className="profile-grid">{['machine', 'process', 'filament'].map((kind) => <label key={kind}>{kind}<select value={form[kind]} onChange={(event) => selectProfile(kind, event.target.value)}>
          {profileOptions(kind).map((name) => <option key={name}>{name}</option>)}</select></label>)}
          <label>Build plate<select value={form.bed_type} onChange={(event) => setForm({ ...form, bed_type: event.target.value })}>
            {profiles.bed_types.map((bed) => <option value={bed.value} key={bed.value}>{bed.label}</option>)}</select></label></div>
        <button type="button" className="drop" onClick={() => fileRef.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); setFiles([...event.dataTransfer.files]) }}>
          <strong>{files.length ? `${files.length} model${files.length === 1 ? '' : 's'} ready` : 'Drop STL or 3MF here'}</strong>
          <span>{files.length ? files.map((file) => `${file.name} · ${bytes(file.size)}`).join('  •  ') : 'or click to browse'}</span>
          <input ref={fileRef} hidden type="file" multiple accept=".stl,.3mf" onChange={(event) => setFiles([...event.target.files])} />
        </button>
        <div className="checks"><label><input type="checkbox" checked={form.auto_orient} onChange={(event) => setForm({ ...form, auto_orient: event.target.checked })} /> Auto-orient</label>
          <label><input type="checkbox" checked={form.export_3mf} onChange={(event) => setForm({ ...form, export_3mf: event.target.checked })} /> Export 3MF</label></div>
        <details><summary>Curated overrides <span>{Object.values(enabledOverrides).filter(Boolean).length} active</span></summary>
          <div className="override-grid">{Object.entries(profiles.overrides).map(([name, spec]) => <div className={`override ${enabledOverrides[name] ? 'active' : ''}`} key={name}>
            <label className="enable"><input type="checkbox" checked={!!enabledOverrides[name]} onChange={(event) => setEnabledOverrides({ ...enabledOverrides, [name]: event.target.checked })} />{spec.label}</label>
            {spec.type === 'boolean' ? <select disabled={!enabledOverrides[name]} value={String(overrides[name])} onChange={(event) => setOverrides({ ...overrides, [name]: event.target.value === 'true' })}><option value="false">No</option><option value="true">Yes</option></select>
              : spec.type === 'enum' ? <select disabled={!enabledOverrides[name]} value={overrides[name]} onChange={(event) => setOverrides({ ...overrides, [name]: event.target.value })}>{spec.values.map((value) => <option key={value} value={value}>{spec.value_labels?.[value] || value}</option>)}</select>
                : <input disabled={!enabledOverrides[name]} type="number" min={spec.minimum} max={spec.maximum} step={spec.type === 'integer' ? 1 : 0.1} value={overrides[name]} onChange={(event) => setOverrides({ ...overrides, [name]: spec.type === 'integer' ? Number.parseInt(event.target.value) : Number.parseFloat(event.target.value) })} />}
          </div>)}</div></details>
        <button className="primary" disabled={busy || !files.length}>{busy ? 'Working…' : 'Queue slice'}</button>
      </form>

      <section className="panel result">
        <div className="panel-title"><div><p className="eyebrow">Selected job</p><h2>{selected?.request?.slice?.project_name || 'No job selected'}</h2></div><span className={`badge ${selected?.status || ''}`}>{selected?.status || '—'}</span></div>
        {!selected ? <div className="empty">Submit a model to begin.</div> : <>
          <div className="preview"><div className="preview-image">{preview ? <img src={preview} alt="Sliced model thumbnail" /> : <span>No thumbnail yet</span>}</div>
            <dl><div><dt>Job</dt><dd>{selected.id.slice(0, 12)}</dd></div><div><dt>Elapsed</dt><dd>{elapsed(selected)}</dd></div><div><dt>Models</dt><dd>{selected.request.models.length}</dd></div><div><dt>Plate</dt><dd>{bedLabel(selected.request.slice.bed_type)}</dd></div><div><dt>Artifacts</dt><dd>{Object.keys(selected.artifacts || {}).length}</dd></div></dl></div>
          {selected.error && <p className="error-block">{selected.error}</p>}
          <div className="actions"><button disabled={busy || selected.status !== 'succeeded'} onClick={() => action(`/jobs/${selected.id}/printer/upload`)}>Upload to K1</button>
            <button className="danger" disabled={busy || !selected.result?.printer_file} onClick={() => action(`/jobs/${selected.id}/printer/print`, `Start printing ${selected.result?.printer_file}?\n\nVerify the bed is clear before continuing.`)}>Start print…</button>
            {!terminal.has(selected.status) && <button className="quiet" onClick={() => action(`/jobs/${selected.id}/cancel`)}>Cancel</button>}</div>
          <div className="artifact-list">{Object.entries(selected.artifacts || {}).map(([name, metadata]) => <button key={name} onClick={() => download(name)}><span>{name}</span><small>{bytes(metadata.size)}</small></button>)}</div>
          <div className="log"><div className="log-head"><span>Worker log</span><span>{logs.length} events</span></div><pre>{logs.map((entry) => <div className={entry.level} key={entry.sequence}><i>{entry.level}</i>{entry.message}</div>)}</pre></div>
        </>}
      </section>
    </section>
    <section className="history"><div className="history-head"><div><p className="eyebrow">Durable queue</p><h2>Recent jobs</h2></div><button className="quiet" onClick={loadJobs}>Refresh</button></div>
      <div className="job-list">{jobs.map((job) => <button className={`${job.id === selectedId ? 'selected ' : ''}${job.status}`} key={job.id} onClick={() => setSelectedId(job.id)}>
        <strong>{job.request.slice.project_name}</strong><span>{job.request.models.map((model) => model.name).join(', ')}</span><small>{new Date(job.created_at).toLocaleString()}</small><em>{job.status}</em>
      </button>)}</div></section>
  </main>
}

export default App

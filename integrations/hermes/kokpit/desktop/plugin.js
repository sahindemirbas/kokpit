// Kokpit pane for the Hermes desktop app.
//
// A compact right-hand pane plus a status-bar chip. The data is GET /overview from
// dashboard/plugin_api.py beside this file (ctx.rest), which returns the same JSON
// as the kokpit_* tools, so the pane, the web tab and the agent never disagree.
// Read-only apart from the capture box, which posts to /capture (kokpit_capture).
// No build step: plain jsx() calls, theme variables only, no hardcoded colors.
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useState } from 'react'

const REFRESH_MS = 60000

// One shared poller for the pane and the chip: a single request a minute.
function createStore(ctx) {
  let data = null
  let error = null
  let loading = false
  const listeners = new Set()
  const emit = () => listeners.forEach(fn => fn())
  const load = () => {
    if (loading) return
    loading = true
    emit()
    ctx.rest('/overview')
      .then(d => { data = d; error = null })
      .catch(e => { error = String((e && e.message) || e) })
      .then(() => { loading = false; emit() })
  }
  return {
    load,
    get: () => ({ data, error, loading }),
    subscribe(fn) {
      listeners.add(fn)
      return () => listeners.delete(fn)
    }
  }
}

function useStore(store) {
  const [, tick] = useState(0)
  useEffect(() => store.subscribe(() => tick(n => n + 1)), [store])
  return store.get()
}

// ------------------------------------------------------------ pieces

const TONE = {
  ok: 'var(--ui-green)',
  warn: 'var(--ui-yellow)',
  bad: 'var(--ui-red)',
  accent: 'var(--ui-accent)',
  muted: 'var(--ui-text-tertiary)'
}

function tone(status) {
  const s = String(status || '')
  if (s === 'ok' || s === 'fresh') return 'ok'
  if (s === 'failed' || s === 'error') return 'bad'
  if (s === 'stale' || s.startsWith('unknown')) return 'warn'
  return 'muted'
}

function Dot({ t }) {
  return jsx('span', {
    style: { display: 'inline-block', width: 7, height: 7, borderRadius: 999, background: TONE[t] || TONE.muted, flex: 'none' }
  })
}

function Tag({ t, children }) {
  return jsx('span', {
    style: {
      fontSize: '0.65rem', padding: '0 0.35rem', borderRadius: 999, whiteSpace: 'nowrap',
      color: TONE[t] || TONE.muted, border: '1px solid var(--ui-stroke-secondary)'
    },
    children
  })
}

function Section({ title, badge, children }) {
  return jsxs('section', {
    style: { display: 'flex', flexDirection: 'column', gap: 4, padding: '8px 0', borderBottom: '1px solid var(--ui-stroke-tertiary)' },
    children: [
      jsxs('div', {
        style: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 6 },
        children: [
          jsx('span', { style: { fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--ui-text-tertiary)' }, children: title }),
          badge ? jsx('span', { style: { fontSize: '0.68rem', color: 'var(--ui-text-quaternary)' }, children: badge }) : null
        ]
      }),
      children
    ]
  })
}

function Row({ left, children, right, title }) {
  return jsxs('div', {
    title: title || '',
    style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.78rem', color: 'var(--ui-text-secondary)', minWidth: 0 },
    children: [
      left || null,
      jsx('span', { style: { flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children }),
      right || null
    ]
  })
}

function Muted({ children }) {
  return jsx('div', { style: { fontSize: '0.75rem', color: 'var(--ui-text-tertiary)' }, children })
}

function NotOk({ r }) {
  const err = String((r && r.error) || 'unknown')
  const off = err.includes('module disabled')
  return jsx(Muted, { children: off ? 'disabled' : err + (r && r.fix ? ' · ' + r.fix : '') })
}

function left(n) {
  if (n === null || n === undefined) return ''
  if (n < 0) return Math.abs(n) + 'd late'
  return n === 0 ? 'today' : n === 1 ? 'tomorrow' : n + 'd'
}

function k(n) {
  if (typeof n !== 'number') return String(n)
  return n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(n)
}

// The tools say "unknown / table X missing ..." so the agent knows why; the pane only needs the short form.
function plain(status) {
  const s = String(status || '')
  if (!s.startsWith('unknown / table ')) return s
  if (/missing columns|unreadable/.test(s)) return 'unknown format'
  if (/ missing( \(no database\))?$/.test(s)) return 'not collected here'
  return s
}

// ------------------------------------------------------------ sections

function Today({ r }) {
  if (!r || !r.ok) return jsx(Section, { title: 'Today', children: jsx(NotOk, { r }) })
  const cal = r.calendar || {}
  const w = r.weather || {}
  const t = r.tasks || {}
  const events = (cal.events || []).filter(e => e.date === r.date)
  const cur = w.current || {}
  const weather = w.status === 'ok' || w.status === 'stale' ? `${cur.temp ?? '?'}° ${cur.text || ''}` : 'weather: ' + plain(w.status)
  return jsxs(Section, {
    title: 'Today',
    badge: r.date,
    children: [
      jsx(Row, { left: jsx(Dot, { t: tone(w.status) }), children: weather }, 'w'),
      ...events.slice(0, 4).map((e, i) =>
        jsx(Row, { left: jsx(Dot, { t: 'accent' }), right: jsx(Muted, { children: e.all_day ? 'all day' : e.start }), children: e.title }, 'e' + i)),
      cal.status !== 'ok' && cal.status !== 'stale' ? jsx(Muted, { children: 'calendar: ' + plain(cal.status) }, 'c') : null,
      jsx(Row, {
        left: jsx(Dot, { t: t.overdue ? 'bad' : 'muted' }),
        children: t.open === undefined ? 'tasks: ' + (t.status || 'unknown') : `${t.open} open tasks${t.overdue ? `, ${t.overdue} overdue` : ''}`
      }, 't')
    ]
  })
}

function Upcoming({ r }) {
  if (!r || !r.ok) return null
  const items = (r.upcoming || []).slice(0, 5)
  return jsx(Section, {
    title: 'Upcoming',
    children: items.length
      ? items.map((u, i) => jsx(Row, {
        left: jsx(Tag, { t: u.days_left < 0 ? 'bad' : u.days_left <= 2 ? 'warn' : 'muted', children: left(u.days_left) }),
        title: u.from,
        children: u.text
      }, i))
      : jsx(Muted, { children: 'nothing dated' })
  })
}

// "You", "Me", "Someone" or the free owner name, plus the note in brackets: "Me · Fri"
function ownerLabel(o) {
  const names = { you: 'You', me: 'Me', someone: 'Someone' }
  const w = o.owner ? (names[o.owner] || o.owner.charAt(0).toUpperCase() + o.owner.slice(1)) : (o.on_you ? 'You' : 'Open')
  return o.note ? w + ' · ' + o.note : w
}

function Threads({ r }) {
  if (!r || !r.ok) return jsx(Section, { title: 'Threads', children: jsx(NotOk, { r }) })
  const mine = (r.on_you || []).slice(0, 5)
  const others = (r.threads || []).flatMap(t => (t.open || []).filter(o => !o.on_you).map(o => ({ thread: t.title, ...o })))
    .slice(0, Math.max(0, 6 - mine.length))
  const item = (o, i, on) => jsx(Row, {
    left: jsx(Tag, { t: on ? 'accent' : 'muted', children: ownerLabel(o) }),
    title: o.thread,
    children: on ? jsx('b', { children: o.text }) : o.text
  }, (on ? 'y' : 'o') + i)
  return jsxs(Section, {
    title: 'Threads',
    badge: (r.threads || []).length + ' threads · ' + mine.length + ' on you',
    children: [
      ...mine.map((o, i) => item(o, i, true)),
      ...others.map((o, i) => item(o, i, false)),
      mine.length || others.length ? null : jsx(Muted, { children: r.note || 'nothing open' }, 'e')
    ]
  })
}

function Health({ r }) {
  if (!r || !r.ok) return jsx(Section, { title: 'Jobs', children: jsx(NotOk, { r }) })
  const jobs = r.jobs || []
  const never = r.never_run || []
  const fresh = jobs.filter(j => j.freshness === 'fresh').length
  return jsxs(Section, {
    title: 'Jobs',
    badge: r.db === 'ok' ? `${fresh}/${jobs.length} fresh` : null,
    children: [
      r.db !== 'ok' ? jsx(Muted, { children: plain(r.reason) }, 'r') : null,
      ...jobs.map(j => jsx(Row, {
        left: jsx(Dot, { t: tone(j.freshness) }),
        right: jsx(Muted, { children: j.freshness + (j.records === null || j.records === undefined ? ' · —' : ` · ${k(j.records)}`) }),
        title: j.error || (j.warnings || []).join('; '),
        children: j.job
      }, j.job)),
      ...never.map(n => jsx(Row, { left: jsx(Dot, { t: 'muted' }), right: jsx(Muted, { children: 'never run' }), children: n }, 'n' + n))
    ]
  })
}

function Ai({ r }) {
  if (!r || !r.ok) return null
  const rows = r.rows || []
  if (!rows.length) return jsx(Section, { title: 'AI usage', children: jsx(Muted, { children: plain(r.status) || r.note || 'no rows' }) })
  const total = r.total_cost_usd === 'unknown' ? `≥ $${r.known_cost_usd}` : `$${r.total_cost_usd}`
  return jsxs(Section, {
    title: 'AI usage',
    badge: `${r.days}d · ${total}`,
    children: rows.slice(0, 4).map((x, i) => jsx(Row, {
      right: jsx(Muted, { children: x.cost_usd === 'unknown' ? 'price ?' : '$' + x.cost_usd }),
      title: x.model,
      children: `${x.tool} · ${k(x.input_tokens)} in / ${k(x.output_tokens)} out`
    }, i))
  })
}

function Night({ r }) {
  if (!r) return null
  if (!r.ok) return jsx(Section, { title: 'Night', children: jsx(NotOk, { r }) })
  const open = r.open || {}
  const kinds = Object.keys(open).filter(x => open[x])
  const total = kinds.reduce((a, x) => a + open[x], 0)
  const ps = r.proposals || []
  return jsxs(Section, {
    title: 'Night',
    badge: r.last_run === 'never' ? 'never run' : kinds.map(x => `${open[x]} ${x}`).join(' · ') || `${total} open`,
    children: ps.length ? ps.map((p, i) => jsx(Row, {
      left: jsx(Tag, { t: 'accent', children: p.kind }),
      title: ((p.sources || [])[0] || {}).quote || '',
      right: jsx(Muted, { children: p.short }),
      children: p.text
    }, i)) : jsx(Muted, { children: r.last_run === 'never' ? 'no night pass yet: /kokpit night run' : 'nothing open' })
  })
}

function Training({ r }) {
  if (!r) return null
  if (!r.ok) return jsx(Section, { title: 'Training', children: jsx(NotOk, { r }) })
  const runs = r.runs || []
  const t = s => s === 'running' ? 'ok' : s === 'finished' ? 'muted' : 'warn'
  return jsxs(Section, {
    title: 'Training',
    badge: r.warnings && r.warnings.length ? `${r.warnings.length} may have stopped` : `${runs.length} runs`,
    children: runs.length ? runs.slice(0, 5).map((x, i) => jsx(Row, {
      left: jsx(Dot, { t: t(x.status) }),
      title: x.warning || x.file || '',
      right: jsx(Muted, { children: x.status === 'running' && x.eta_minutes != null ? `ETA ${Math.round(x.eta_minutes)}m` : x.status }),
      children: `${x.run}${x.last_step != null ? ' · ' + k(x.last_step) + (x.total_steps ? '/' + k(x.total_steps) : '') : ''}${typeof x.loss === 'number' ? ' · loss ' + x.loss.toFixed(3) : ''}`
    }, i)) : jsx(Muted, { children: r.note || 'no runs' })
  })
}

function Server({ r }) {
  if (!r) return null
  if (!r.ok) return jsx(Section, { title: 'Server', children: jsx(NotOk, { r }) })
  const temp = typeof r.cpu_temp_c === 'number' ? `${r.cpu_temp_c}°C` : 'temp ?'
  const load = Array.isArray(r.load) ? r.load[0] : '?'
  const mem = r.memory && typeof r.memory === 'object' ? `${r.memory.used_pct}%` : '?'
  return jsxs(Section, {
    title: 'Server',
    badge: `${temp} · load ${load} · mem ${mem}`,
    children: [
      ...(r.disks || []).map((d, i) => jsx(Row, {
        left: jsx(Dot, { t: d.status === 'ok' ? 'ok' : 'warn' }),
        right: jsx(Muted, { children: d.used_pct != null ? d.used_pct + '%' : '?' }),
        children: d.path
      }, 'd' + i)),
      ...(r.services || []).map((s, i) => jsx(Row, {
        left: jsx(Dot, { t: s.state === 'active' ? 'ok' : s.state === 'unknown' ? 'muted' : 'bad' }),
        right: jsx(Muted, { children: s.state }),
        children: s.name
      }, 's' + i))
    ]
  })
}

function Capture({ ctx, store }) {
  const [text, setText] = useState('')
  const [kind, setKind] = useState('note')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const send = ev => {
    ev.preventDefault()
    if (!text.trim() || busy) return
    setBusy(true)
    ctx.rest('/capture', { method: 'POST', body: { text, kind } })
      .then(r => {
        if (r && r.ok) { setText(''); setMsg('saved to ' + r.written); store.load() }
        else setMsg((r && (r.error + (r.fix ? ': ' + r.fix : ''))) || 'not saved')
      })
      .catch(e => setMsg(String((e && e.message) || e)))
      .then(() => setBusy(false))
  }
  const field = {
    background: 'var(--ui-bg-elevated)', color: 'var(--ui-text-primary)', border: '1px solid var(--ui-stroke-secondary)',
    borderRadius: 6, padding: '4px 6px', fontSize: '0.78rem', font: 'inherit'
  }
  return jsx(Section, {
    title: 'Capture',
    children: jsxs('form', {
      onSubmit: send,
      style: { display: 'flex', flexDirection: 'column', gap: 4 },
      children: [
        jsx('input', { value: text, maxLength: 2000, placeholder: kind === 'task' ? 'task for To-Do' : 'note for the inbox', onChange: e => setText(e.target.value), style: field }),
        jsxs('div', {
          style: { display: 'flex', gap: 4 },
          children: [
            jsxs('select', {
              value: kind, onChange: e => setKind(e.target.value), style: field,
              children: [jsx('option', { value: 'note', children: 'note' }), jsx('option', { value: 'task', children: 'task' })]
            }),
            jsx('button', {
              type: 'submit', disabled: busy || !text.trim(),
              style: { ...field, background: 'var(--ui-accent)', color: 'var(--ui-base)', border: 0, cursor: 'pointer', opacity: busy || !text.trim() ? 0.5 : 1 },
              children: busy ? '…' : 'save'
            })
          ]
        }),
        msg ? jsx(Muted, { children: msg }) : null
      ]
    })
  })
}

function Pane({ ctx, store }) {
  const { data, error, loading } = useStore(store)
  useEffect(() => { if (!data && !loading) store.load() }, [])
  const head = jsxs('div', {
    style: { display: 'flex', alignItems: 'center', gap: 6, paddingBottom: 4 },
    children: [
      jsx('span', { style: { fontWeight: 600, color: 'var(--ui-text-primary)' }, children: 'Kokpit' }),
      data && data.demo ? jsx(Tag, { t: 'accent', children: 'Demo data' }) : null,
      jsx('span', { style: { flex: 1 } }),
      jsx('button', {
        type: 'button', onClick: store.load, title: 'refresh',
        style: { background: 'none', border: 0, color: 'var(--ui-text-tertiary)', cursor: 'pointer', fontSize: '0.75rem' },
        children: loading ? '…' : '↻'
      })
    ]
  })
  let body
  if (error) body = jsx(Muted, { children: error + ' · is the kokpit plugin enabled in this profile?' })
  else if (!data) body = jsx(Muted, { children: 'loading…' })
  else if (!data.ok) body = jsx(NotOk, { r: data })
  else {
    const on = Object.fromEntries((data.modules || []).map(m => [m.module, m.enabled]))
    body = [
      jsx(Today, { r: data.today }, 'today'),
      jsx(Threads, { r: data.threads }, 'threads'),
      jsx(Upcoming, { r: data.today }, 'up'),
      jsx(Health, { r: data.health }, 'health'),
      on.ai_usage ? jsx(Ai, { r: data.ai_usage }, 'ai') : null,
      on.brain ? jsx(Night, { r: data.night }, 'night') : null,
      on.training ? jsx(Training, { r: data.training }, 'training') : null,
      on.server ? jsx(Server, { r: data.server }, 'server') : null,
      jsx(Capture, { ctx, store }, 'capture')
    ]
  }
  return jsxs('div', {
    style: { height: '100%', overflowY: 'auto', padding: '10px 12px', display: 'flex', flexDirection: 'column', fontSize: '0.8rem' },
    children: [head, body]
  })
}

// Status bar: a dot coloured by the worst job state, and how much is waiting on you.
function Chip({ store }) {
  const { data } = useStore(store)
  if (!data || !data.ok) return null
  const jobs = (data.health && data.health.jobs) || []
  const extra = ['training', 'server'].flatMap(s => (data[s] && data[s].ok && data[s].warnings) || [])
  const worst = jobs.some(j => j.freshness === 'failed') ? 'bad'
    : jobs.some(j => j.freshness !== 'fresh') || extra.length ? 'warn' : 'ok'
  const mine = ((data.threads && data.threads.on_you) || []).length
  return jsxs('span', {
    title: 'Kokpit: ' + [...((data.health && data.health.problems) || []), ...extra].slice(0, 4).join('; '),
    style: { display: 'inline-flex', alignItems: 'center', gap: 4, padding: '0 6px', fontSize: '0.6875rem', color: 'var(--ui-text-tertiary)' },
    children: [jsx(Dot, { t: worst }), `kokpit${mine ? ' · ' + mine : ''}${data.demo ? ' (demo)' : ''}`]
  })
}

export default {
  id: 'kokpit',
  name: 'Kokpit',
  register(ctx) {
    const store = createStore(ctx)
    ctx.setInterval(store.load, REFRESH_MS)
    store.load()
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'kokpit',
      data: { placement: 'right', width: '300px' },
      render: () => jsx(Pane, { ctx, store })
    })
    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 140,
      render: () => jsx(Chip, { store })
    })
  }
}

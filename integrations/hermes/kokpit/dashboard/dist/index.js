/* Kokpit tab for the Hermes web dashboard.
 *
 * Plain IIFE, no build step. Renders GET /api/plugins/kokpit/overview, which is
 * the same JSON the kokpit_* tools return; nothing is recomputed here. The only
 * write is the capture form, which posts to /capture (the kokpit_capture path).
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  var React = SDK.React;
  var h = React.createElement;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useCallback = SDK.hooks.useCallback;
  var API = "/api/plugins/kokpit";
  var REFRESH_MS = 60000;

  // ------------------------------------------------------------ small pieces

  function cls() {
    return Array.prototype.filter.call(arguments, Boolean).join(" ");
  }

  function Card(props) {
    var body = [h("div", { key: "t", className: "kokpit-card-title" }, props.title,
      props.badge ? h("span", { className: "kokpit-card-badge" }, props.badge) : null)];
    body.push(h("div", { key: "b", className: "kokpit-card-body" }, props.children));
    return h("section", { className: cls("kokpit-card", props.wide && "kokpit-wide") }, body);
  }

  function Pill(props) {
    return h("span", { className: "kokpit-pill kokpit-" + (props.tone || "muted"), title: props.title || "" }, props.children);
  }

  function Empty(props) {
    return h("p", { className: "kokpit-empty" }, props.children);
  }

  // A section result that is not ok: module disabled, vault missing, schema mismatch ...
  function Problem(props) {
    var r = props.r || {};
    var disabled = /module disabled/.test(String(r.error || ""));
    return h("div", { className: "kokpit-problem" },
      h(Pill, { tone: disabled ? "muted" : "warn" }, disabled ? "disabled" : "unavailable"),
      h("span", null, " " + (disabled ? "" : String(r.error || "unknown"))),
      r.fix ? h("div", { className: "kokpit-fix" }, String(r.fix)) : null);
  }

  function statusTone(status) {
    var s = String(status || "");
    if (s === "ok" || s === "fresh") return "ok";
    if (s === "failed" || s === "error") return "bad";
    if (s === "stale") return "warn";
    if (s.indexOf("unknown") === 0) return "warn";
    return "muted";
  }

  // The tools say "unknown / table X missing ..." so the agent knows why; a card only needs the short form.
  function plain(status) {
    var s = String(status || "");
    if (s.indexOf("unknown / table ") !== 0) return s;
    if (/missing columns|unreadable/.test(s)) return "unknown format";
    if (/ missing( \(no database\))?$/.test(s)) return "not collected here";
    return s;
  }

  function StatusLine(props) {
    return h("p", { className: "kokpit-empty", title: String(props.s || "") }, plain(props.s));
  }

  function when(iso) {
    if (!iso) return "never";
    var s = String(iso);
    return s.length > 16 ? s.slice(0, 16).replace("T", " ") : s;
  }

  function daysLeft(n) {
    if (n === null || n === undefined) return "";
    if (n < 0) return Math.abs(n) + "d late";
    if (n === 0) return "today";
    if (n === 1) return "tomorrow";
    return "in " + n + "d";
  }

  function num(n) {
    if (typeof n !== "number") return String(n === undefined || n === null ? "unknown" : n);
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
    return String(n);
  }

  // ------------------------------------------------------------ cards

  function Today(props) {
    var r = props.r;
    if (!r || !r.ok) return h(Card, { title: "Today" }, h(Problem, { r: r }));
    var cal = r.calendar || {}, w = r.weather || {}, t = r.tasks || {};
    var todays = (cal.events || []).filter(function (e) { return e.date === r.date; });
    var cur = w.current || {};
    return h(Card, { title: "Today", badge: r.date },
      h("div", { className: "kokpit-stats" },
        h(Stat, { label: "Weather", value: w.status === "ok" || w.status === "stale" ? (cur.temp !== undefined ? cur.temp + "°" : "?") : "—",
                  sub: w.status === "ok" || w.status === "stale" ? cur.text : plain(w.status) }),
        h(Stat, { label: "Events today", value: cal.status === "ok" || cal.status === "stale" ? todays.length : "—", sub: plain(cal.status) }),
        h(Stat, { label: "Open tasks", value: t.open !== undefined ? t.open : "—", sub: t.overdue ? t.overdue + " overdue" : (t.status || "") }),
        h(Stat, { label: "Threads", value: r.threads !== undefined ? r.threads : "—", sub: "active" })),
      todays.length ? h("ul", { className: "kokpit-list" }, todays.map(function (e, i) {
        return h("li", { key: i }, h("span", { className: "kokpit-time" }, e.all_day ? "all day" : (e.start || "")), " " + e.title);
      })) : null,
      (t.next || []).length ? h("div", { className: "kokpit-sub" }, "Next tasks") : null,
      h("ul", { className: "kokpit-list" }, (t.next || []).map(function (x, i) {
        return h("li", { key: i }, x.text, x.due ? h(Pill, { tone: x.overdue ? "bad" : (x.days_left <= 2 ? "warn" : "muted") }, daysLeft(x.days_left)) : null);
      })));
  }

  function Stat(props) {
    return h("div", { className: "kokpit-stat" },
      h("div", { className: "kokpit-stat-value" }, String(props.value)),
      h("div", { className: "kokpit-stat-label" }, props.label),
      props.sub ? h("div", { className: "kokpit-stat-sub" }, String(props.sub)) : null);
  }

  function Upcoming(props) {
    var r = props.r;
    if (!r || !r.ok) return h(Card, { title: "Upcoming dates" }, h(Problem, { r: r }));
    var items = r.upcoming || [];
    return h(Card, { title: "Upcoming dates" },
      items.length ? h("ul", { className: "kokpit-list" }, items.map(function (u, i) {
        return h("li", { key: i },
          h("span", { className: "kokpit-time" }, u.date), " " + u.text + " ",
          h(Pill, { tone: u.days_left < 0 ? "bad" : u.days_left <= 2 ? "warn" : "muted" }, daysLeft(u.days_left)),
          h("span", { className: "kokpit-from" }, u.from));
      })) : h(Empty, null, "No dated tasks or thread items."));
  }

  // "You", "Me", "Someone" or the free owner name, plus the note in brackets: "Me · Fri"
  function ownerLabel(o) {
    var names = { you: "You", me: "Me", someone: "Someone" };
    var w = o.owner ? (names[o.owner] || o.owner.charAt(0).toUpperCase() + o.owner.slice(1)) : (o.on_you ? "You" : "Open");
    return o.note ? w + " · " + o.note : w;
  }

  function Threads(props) {
    var r = props.r;
    if (!r || !r.ok) return h(Card, { title: "Open threads" }, h(Problem, { r: r }));
    var ts = r.threads || [];
    return h(Card, { title: "Open threads", badge: r.source },
      ts.length ? ts.map(function (t, i) {
        return h("div", { key: i, className: "kokpit-thread" },
          h("div", { className: "kokpit-thread-title" }, t.title, t.date ? h("span", { className: "kokpit-from" }, t.date) : null),
          t.status ? h("div", { className: "kokpit-thread-status" }, t.status) : null,
          h("ul", { className: "kokpit-list" }, (t.open || []).map(function (o, j) {
            return h("li", { key: j, className: o.on_you ? "kokpit-on-you" : null }, h(Pill, { tone: o.on_you ? "accent" : "muted" }, ownerLabel(o)), " " + o.text);
          })));
      }) : h(Empty, null, r.note || "No active threads."),
      r.last_session ? h("div", { className: "kokpit-last" }, "Last session: ", h("b", null, r.last_session.title || ""), " (" + (r.last_session.at || "") + ")") : null);
  }

  function Health(props) {
    var r = props.r;
    if (!r || !r.ok) return h(Card, { title: "Job health" }, h(Problem, { r: r }));
    var jobs = r.jobs || [], never = r.never_run || (r.db !== "ok" ? r.expected || [] : []);
    // "never run" only when Kokpit has no database or the runs table has no row for the job
    var neverLabel = r.db === "ok" || r.db === "missing" ? "never run" : plain(r.reason);
    var files = r.vault_files || {};
    return h(Card, { title: "Job health", badge: r.db === "ok" ? jobs.length + " jobs" : r.db },
      r.db !== "ok" ? h(StatusLine, { s: r.reason || "no database yet" }) : null,
      jobs.length || never.length ? h("table", { className: "kokpit-table" }, h("tbody", null,
        jobs.map(function (j) {
          return h("tr", { key: j.job },
            h("td", null, j.job),
            h("td", null, h(Pill, { tone: statusTone(j.freshness), title: j.error || "" }, j.freshness === "failed" && j.failed_in_a_row > 1 ? "failed x" + j.failed_in_a_row : j.freshness)),
            h("td", { className: "kokpit-muted" }, when(j.last_run)),
            h("td", { className: "kokpit-muted" }, j.records === null || j.records === undefined ? "—" : j.records + " rows"));
        }).concat(never.map(function (n) {
          return h("tr", { key: "n-" + n }, h("td", null, n), h("td", null, h(Pill, { tone: "muted" }, neverLabel)), h("td", null, ""), h("td", null, ""));
        })))) : null,
      (r.problems || []).length ? h("ul", { className: "kokpit-list kokpit-problems" }, r.problems.slice(0, 6).map(function (p, i) {
        return h("li", { key: i }, p);
      })) : h(Empty, null, "No problems."),
      Object.keys(files).length && !files.status ? h("div", { className: "kokpit-files" }, Object.keys(files).map(function (k) {
        var f = files[k];
        var label = f.how === "ambiguous" ? "ambiguous" : f.found ? (f.how === "discovered" ? "found by search" : "found") : "missing";
        return h("div", { key: k, title: f.fix || "" }, h(Pill, { tone: f.how === "ambiguous" ? "warn" : f.found ? "ok" : "muted" }, label), " " + (f.file || (f.candidates || []).join(", ")));
      })) : null);
  }

  function AiUsage(props) {
    var r = props.r;
    if (!r || !r.ok) return h(Card, { title: "AI usage" }, h(Problem, { r: r }));
    var rows = r.rows || [];
    var total = r.total_cost_usd === "unknown" ? "≥ $" + r.known_cost_usd : "$" + r.total_cost_usd;
    return h(Card, { title: "AI usage", badge: (r.days || 7) + " days" },
      r.status ? h(StatusLine, { s: r.status }) : null,
      rows.length ? h("table", { className: "kokpit-table" }, h("tbody", null, rows.map(function (x, i) {
        return h("tr", { key: i },
          h("td", null, x.tool), h("td", { className: "kokpit-muted" }, x.model),
          h("td", null, num(x.input_tokens) + " in / " + num(x.output_tokens) + " out"),
          h("td", null, x.cost_usd === "unknown" ? h(Pill, { tone: "warn" }, "price unknown") : "$" + x.cost_usd));
      }))) : (!r.status ? h(Empty, null, r.note || "No usage rows.") : null),
      rows.length ? h("div", { className: "kokpit-sub" }, "Total " + total + (r.unpriced_models && r.unpriced_models.length ? " (unpriced: " + r.unpriced_models.join(", ") + ")" : "")) : null);
  }

  function Events(props) {
    var r = props.r;
    if (!r || !r.ok) return h(Card, { title: "Events" }, h(Problem, { r: r }));
    var ev = r.events || [];
    return h(Card, { title: "Events", badge: "next " + (r.days || 7) + " days" },
      r.status ? h(StatusLine, { s: r.status }) : null,
      ev.length ? h("ul", { className: "kokpit-list" }, ev.map(function (e, i) {
        return h("li", { key: i }, h("span", { className: "kokpit-time" }, when(e.start)), " " + e.title,
          e.venue && e.venue !== "unknown" ? h("span", { className: "kokpit-from" }, e.venue) : null);
      })) : (!r.status ? h(Empty, null, r.note || "No stored events.") : null));
  }

  function Jobs(props) {
    var r = props.r;
    if (!r || !r.ok) return h(Card, { title: "Job listings" }, h(Problem, { r: r }));
    var ls = r.listings || [];
    return h(Card, { title: "Job listings", badge: r.open_total !== undefined ? r.open_total + " open" : "" },
      r.status ? h(StatusLine, { s: r.status }) : null,
      ls.length ? h("ul", { className: "kokpit-list" }, ls.map(function (x, i) {
        return h("li", { key: i }, h(Pill, { tone: typeof x.score === "number" && x.score >= 70 ? "ok" : "muted" }, String(x.score)), " " + x.title,
          h("span", { className: "kokpit-from" }, [x.company, x.remote].filter(Boolean).join(", ")));
      })) : (!r.status ? h(Empty, null, "No open listings.") : null));
  }

  // Night pass proposals: counts by kind and the top three. Accept or dismiss from chat (/kokpit add|skip <id>).
  function Night(props) {
    var r = props.r;
    if (!r || !r.ok) return h(Card, { title: "Night proposals" }, h(Problem, { r: r }));
    var open = r.open || {};
    var kinds = Object.keys(open).filter(function (k) { return open[k]; });
    var total = kinds.reduce(function (a, k) { return a + open[k]; }, 0);
    var ps = r.proposals || [];
    return h(Card, { title: "Night proposals", badge: r.last_run === "never" ? "never run" : total + " open" },
      kinds.length ? h("div", { className: "kokpit-files" }, kinds.map(function (k) {
        return h(Pill, { key: k, tone: "muted" }, open[k] + " " + k);
      })) : null,
      ps.length ? h("ul", { className: "kokpit-list" }, ps.map(function (p, i) {
        var src = (p.sources || [])[0] || {};
        return h("li", { key: i, title: (src.path || src.item || "") + ": " + (src.quote || "") },
          h(Pill, { tone: "accent" }, p.kind), " " + p.text,
          h("span", { className: "kokpit-from" }, p.short));
      })) : h(Empty, null, r.last_run === "never" ? "No night pass yet. Run /kokpit night run in a chat." : "Nothing open."),
      ps.length ? h("div", { className: "kokpit-fix" }, "/kokpit add <id> or /kokpit skip <id> in any Hermes chat") : null);
  }

  function Training(props) {
    var r = props.r;
    if (!r || !r.ok) return h(Card, { title: "Training runs" }, h(Problem, { r: r }));
    var runs = r.runs || [];
    function tone(s) { return s === "running" ? "ok" : s === "finished" ? "muted" : "warn"; }
    return h(Card, { title: "Training runs", badge: runs.length + " runs" },
      runs.length ? h("ul", { className: "kokpit-list" }, runs.map(function (x, i) {
        var bits = [];
        if (x.last_step !== undefined) bits.push("step " + num(x.last_step) + (x.total_steps ? "/" + num(x.total_steps) : ""));
        if (typeof x.loss === "number") bits.push("loss " + x.loss.toFixed(4));
        if (x.steps_per_sec) bits.push(x.steps_per_sec + " it/s");
        if (x.eta_minutes !== null && x.eta_minutes !== undefined && x.status === "running") bits.push("ETA " + Math.round(x.eta_minutes) + " min");
        if (x.minutes_since_update !== undefined) bits.push("updated " + Math.round(x.minutes_since_update) + " min ago");
        return h("li", { key: i, title: x.file || "" }, h(Pill, { tone: tone(x.status) }, x.status), " " + x.run,
          h("span", { className: "kokpit-from" }, bits.join(" · ") || x.reason || ""),
          x.warning ? h("div", { className: "kokpit-fix" }, x.warning) : null);
      })) : h(Empty, null, r.note || "No runs in the configured logs."));
  }

  function Server(props) {
    var r = props.r;
    if (!r || !r.ok) return h(Card, { title: "Server" }, h(Problem, { r: r }));
    var load = Array.isArray(r.load) ? r.load.join(" / ") : String(r.load);
    var mem = r.memory && typeof r.memory === "object" ? r.memory.used_pct + "% used" : String(r.memory);
    var up = r.uptime && typeof r.uptime === "object" ? r.uptime.text : String(r.uptime);
    var temp = typeof r.cpu_temp_c === "number" ? r.cpu_temp_c + " °C" : String(r.cpu_temp_c);
    return h(Card, { title: "Server", badge: (r.warnings || []).length ? r.warnings.length + " warnings" : "" },
      h("div", { className: "kokpit-stats" },
        h(Stat, { label: "CPU", value: temp }), h(Stat, { label: "Load", value: load }),
        h(Stat, { label: "Memory", value: mem }), h(Stat, { label: "Uptime", value: up })),
      h("ul", { className: "kokpit-list" }, (r.disks || []).map(function (d, i) {
        return h("li", { key: "d" + i }, h(Pill, { tone: d.status === "ok" ? "ok" : "warn" }, d.used_pct !== undefined ? d.used_pct + "%" : "?"),
          " " + d.path, d.free_gb !== undefined ? h("span", { className: "kokpit-from" }, d.free_gb + " GB free") : null);
      }).concat((r.services || []).map(function (s, i) {
        return h("li", { key: "s" + i }, h(Pill, { tone: s.state === "active" ? "ok" : s.state === "unknown" ? "muted" : "bad" }, s.state), " " + s.name);
      }))));
  }

  function Modules(props) {
    var ms = props.modules || [];
    return h(Card, { title: "Modules" },
      h("ul", { className: "kokpit-list" }, ms.map(function (m) {
        return h("li", { key: m.module, title: m.enable || "" }, h(Pill, { tone: m.enabled ? "ok" : "muted" }, m.enabled ? "on" : "off"),
          " " + m.module, !m.enabled && m.enable ? h("div", { className: "kokpit-fix" }, m.enable) : null);
      })));
  }

  function Capture(props) {
    var st = useState(""), text = st[0], setText = st[1];
    var sk = useState("note"), kind = sk[0], setKind = sk[1];
    var sd = useState(""), due = sd[0], setDue = sd[1];
    var sm = useState(null), msg = sm[0], setMsg = sm[1];
    var sb = useState(false), busy = sb[0], setBusy = sb[1];
    function submit(ev) {
      ev.preventDefault();
      if (!text.trim() || busy) return;
      setBusy(true);
      SDK.fetchJSON(API + "/capture", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text, kind: kind, due: kind === "task" ? due : "" })
      }).then(function (r) {
        if (r && r.ok) { setText(""); setDue(""); setMsg({ ok: true, text: "Saved to " + r.written }); props.onDone(); }
        else setMsg({ ok: false, text: (r && (r.error + (r.fix ? ": " + r.fix : ""))) || "not saved" });
      }).catch(function (e) { setMsg({ ok: false, text: String(e && e.message || e) }); })
        .then(function () { setBusy(false); });
    }
    return h(Card, { title: "Capture", badge: props.demo ? "demo vault" : "" },
      h("form", { className: "kokpit-form", onSubmit: submit },
        h("textarea", { value: text, maxLength: 2000, rows: 2, placeholder: kind === "task" ? "A task for your To-Do file" : "A note for the inbox",
                        onChange: function (e) { setText(e.target.value); } }),
        h("div", { className: "kokpit-form-row" },
          h("select", { value: kind, onChange: function (e) { setKind(e.target.value); } },
            h("option", { value: "note" }, "Note"), h("option", { value: "task" }, "Task")),
          kind === "task" ? h("input", { type: "date", value: due, onChange: function (e) { setDue(e.target.value); } }) : null,
          h("button", { type: "submit", disabled: busy || !text.trim() }, busy ? "Saving…" : "Save")),
        msg ? h("div", { className: msg.ok ? "kokpit-ok" : "kokpit-err" }, msg.text) : null));
  }

  // ------------------------------------------------------------ page

  function KokpitPage() {
    var sd = useState(null), data = sd[0], setData = sd[1];
    var se = useState(null), err = se[0], setErr = se[1];
    var sl = useState(false), loading = sl[0], setLoading = sl[1];
    var load = useCallback(function () {
      setLoading(true);
      SDK.fetchJSON(API + "/overview").then(function (d) { setData(d); setErr(null); })
        .catch(function (e) { setErr(String(e && e.message || e)); })
        .then(function () { setLoading(false); });
    }, []);
    useEffect(function () {
      load();
      var t = setInterval(load, REFRESH_MS);
      return function () { clearInterval(t); };
    }, [load]);

    var head = h("div", { className: "kokpit-head" },
      h("div", { className: "kokpit-head-title" }, "Kokpit",
        data && data.demo ? h("span", { className: "kokpit-demo", title: "Bundled synthetic data. Set kokpit_config or vault_path to use your own." }, "Demo data") : null),
      h("div", { className: "kokpit-head-meta" },
        data && data.generated ? "updated " + when(data.generated) : "",
        h("button", { type: "button", onClick: load, disabled: loading }, loading ? "…" : "Refresh")));

    if (err) return h("div", { className: "kokpit-root" }, head, h(Card, { title: "Kokpit API" }, h(Problem, { r: { error: err, fix: "is the kokpit plugin enabled (hermes plugins enable kokpit)?" } })));
    if (!data) return h("div", { className: "kokpit-root" }, head, h(Empty, null, "Loading…"));
    if (!data.ok) return h("div", { className: "kokpit-root" }, head, h(Card, { title: "Setup" }, h(Problem, { r: data })));

    var on = {};
    (data.modules || []).forEach(function (m) { on[m.module] = m.enabled; });
    var cards = [
      h(Today, { key: "today", r: data.today }),
      h(Upcoming, { key: "up", r: data.today }),
      h(Threads, { key: "threads", r: data.threads }),
      h(Health, { key: "health", r: data.health }),
      on.ai_usage ? h(AiUsage, { key: "ai", r: data.ai_usage }) : null,
      on.events ? h(Events, { key: "events", r: data.events }) : null,
      on.jobs ? h(Jobs, { key: "jobs", r: data.jobs }) : null,
      on.brain ? h(Night, { key: "night", r: data.night }) : null,
      on.training ? h(Training, { key: "training", r: data.training }) : null,
      on.server ? h(Server, { key: "server", r: data.server }) : null,
      h(Capture, { key: "capture", demo: data.demo, onDone: load }),
      h(Modules, { key: "modules", modules: data.modules })
    ];
    return h("div", { className: "kokpit-root" }, head, h("div", { className: "kokpit-grid" }, cards));
  }

  window.__HERMES_PLUGINS__.register("kokpit", KokpitPage);
})();

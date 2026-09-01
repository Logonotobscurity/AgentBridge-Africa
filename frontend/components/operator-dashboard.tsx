"use client";

import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  BadgeCheck,
  Bell,
  BookOpen,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  Clock3,
  FileCheck2,
  Fingerprint,
  Gauge,
  KeyRound,
  LayoutDashboard,
  Menu,
  Network,
  PanelLeftClose,
  RefreshCw,
  Search,
  ServerCog,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  WalletCards,
  X,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { auditEvents, payments as initialPayments, rails } from "@/lib/mock-data";
import type { Payment, PaymentStatus } from "@/lib/types";

const nav = [
  { label: "Overview", icon: LayoutDashboard, target: "top" },
  { label: "Payment intents", icon: WalletCards, count: 18, target: "payment-intents", filter: "All" },
  { label: "Approval queue", icon: Fingerprint, count: 2, target: "approval-queue", filter: "Needs action" },
  { label: "Reconciliation", icon: RefreshCw, count: 4, target: "reconciliation", filter: "In flight" },
  { label: "Provider rails", icon: Network, target: "provider-rails" },
  { label: "Audit & OSCAL", icon: FileCheck2, target: "audit-oscal" },
] as const;

const configureNav = [
  { label: "Policy controls", icon: ShieldCheck, target: "policy-controls" },
  { label: "Agent graph", icon: Bot, target: "agent-graph" },
  { label: "Infrastructure", icon: ServerCog, target: "infrastructure" },
] as const;

const questions = [
  "Recipient reference matches the verified vendor manifest",
  "Amount agrees with the approved purchase order",
  "No equivalent transfer was submitted in the last 24 hours",
  "Intent and extracted parameters are internally consistent",
  "Regional limits and data-handling policy are satisfied",
];

const statusTone: Record<PaymentStatus, string> = {
  "Awaiting approval": "amber",
  Reconciling: "blue",
  Confirmed: "green",
  "Budget stopped": "red",
  Failed: "red",
};

function BrandMark() {
  return (
    <div className="brand-mark" aria-hidden="true">
      <span />
      <span />
      <span />
    </div>
  );
}

function StatusPill({ status }: { status: PaymentStatus }) {
  return (
    <span className={`status-pill ${statusTone[status]}`}>
      <span className="status-dot" />
      {status}
    </span>
  );
}

type LedgerFilter = "All" | "Needs action" | "In flight" | "Final";

export default function OperatorDashboard() {
  const [mobileNav, setMobileNav] = useState(false);
  const [activeSection, setActiveSection] = useState("top");
  const [payments, setPayments] = useState(initialPayments);
  const [selectedId, setSelectedId] = useState(initialPayments[0].id);
  const [filter, setFilter] = useState<LedgerFilter>("All");
  const [search, setSearch] = useState("");
  const [checks, setChecks] = useState<boolean[]>(questions.map(() => false));
  const [confirmation, setConfirmation] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const selected = payments.find((payment) => payment.id === selectedId) ?? payments[0];
  const visiblePayments = useMemo(() => {
    const normalized = search.trim().toLowerCase();
    return payments.filter((payment) => {
      const groupMatch =
        filter === "All" ||
        (filter === "Needs action" && payment.status === "Awaiting approval") ||
        (filter === "In flight" && payment.status === "Reconciling") ||
        (filter === "Final" && ["Confirmed", "Failed", "Budget stopped"].includes(payment.status));
      const searchMatch =
        !normalized ||
        [payment.id, payment.intent, payment.rail, payment.corridor, payment.amount]
          .join(" ")
          .toLowerCase()
          .includes(normalized);
      return groupMatch && searchMatch;
    });
  }, [filter, payments, search]);

  const ready = checks.every(Boolean) && confirmation.trim().length >= 8;

  useEffect(() => {
    const syncHash = () => setActiveSection(window.location.hash.slice(1) || "top");
    syncHash();
    window.addEventListener("hashchange", syncHash);
    return () => window.removeEventListener("hashchange", syncHash);
  }, []);

  function navigate(target: string, nextFilter?: LedgerFilter) {
    if (nextFilter) setFilter(nextFilter);
    setActiveSection(target);
    setMobileNav(false);
  }

  function choosePayment(payment: Payment) {
    setSelectedId(payment.id);
    setChecks(questions.map(() => false));
    setConfirmation("");
    setNotice(null);
  }

  function decide(status: "Reconciling" | "Failed") {
    setPayments((current) =>
      current.map((payment) => (payment.id === selected.id ? { ...payment, status } : payment)),
    );
    setNotice(
      status === "Reconciling"
        ? "Approval attestation recorded. Payment released to its pinned rail."
        : "Intent rejected. A policy finding was added to the audit pack.",
    );
  }

  return (
    <div className="shell">
      <aside className={`sidebar ${mobileNav ? "open" : ""}`}>
        <div className="sidebar-head">
          <a className="brand" href="#top" onClick={() => navigate("top")} aria-label="AgentBridge home">
            <BrandMark />
            <div>
              <strong>AgentBridge</strong>
              <span>Africa</span>
            </div>
          </a>
          <button className="icon-button close-mobile" onClick={() => setMobileNav(false)} aria-label="Close menu">
            <X size={18} />
          </button>
        </div>

        <div className="workspace-switch">
          <div className="workspace-icon">VL</div>
          <div><b>Venturalitica</b><span>Production workspace</span></div>
          <ChevronDown size={15} />
        </div>

        <nav aria-label="Main navigation">
          <p className="nav-label">Operate</p>
          {nav.map((item) => (
            <a
              className={`nav-item ${activeSection === item.target ? "active" : ""}`}
              href={`#${item.target}`}
              onClick={() => navigate(item.target, "filter" in item ? item.filter : undefined)}
              key={item.label}
            >
              <item.icon size={18} strokeWidth={1.8} />
              <span>{item.label}</span>
              {"count" in item && item.count ? <em>{item.count}</em> : null}
            </a>
          ))}
          <p className="nav-label second">Configure</p>
          {configureNav.map((item) => (
            <a
              className={`nav-item ${activeSection === item.target ? "active" : ""}`}
              href={`#${item.target}`}
              onClick={() => navigate(item.target)}
              key={item.label}
            >
              <item.icon size={18} />
              <span>{item.label}</span>
            </a>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className="secure-card">
            <div className="secure-icon"><KeyRound size={16} /></div>
            <div><b>Workload verified</b><span>SPIFFE SVID · 11m left</span></div>
            <CheckCircle2 size={16} />
          </div>
          <div className="operator">
            <div className="avatar">AO</div>
            <div><b>Amara Okafor</b><span>Payments operator</span></div>
            <PanelLeftClose size={17} />
          </div>
        </div>
      </aside>

      {mobileNav ? <button className="scrim" aria-label="Close navigation" onClick={() => setMobileNav(false)} /> : null}

      <main className="main" id="top">
        <header className="topbar">
          <div className="topbar-title">
            <button className="icon-button menu-button" onClick={() => setMobileNav(true)} aria-label="Open menu"><Menu size={20} /></button>
            <div><span>Operations</span><b>Control room</b></div>
          </div>
          <div className="top-actions">
            <label className="search-box">
              <Search size={16} />
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search intent or rail…" />
              <kbd>⌘ K</kbd>
            </label>
            <div className="env-pill"><span /> Production</div>
            <button className="icon-button notification" aria-label="Notifications"><Bell size={18} /><i /></button>
          </div>
        </header>

        <div className="content">
          <section className="welcome-row">
            <div>
              <div className="eyebrow"><Sparkles size={14} /> Tuesday, 01 September · 10:43 WAT</div>
              <h1>Good morning, Amara.</h1>
              <p>Two payment intents need your decision. All ledger invariants are holding.</p>
            </div>
            <div className="system-state"><span className="pulse" /><div><b>Governance online</b><small>Last policy sync 34s ago</small></div><ChevronDown size={16} /></div>
          </section>

          <section className="metrics-grid" aria-label="Operational metrics">
            <article className="metric-card">
              <div className="metric-icon mint"><CircleDollarSign size={20} /></div>
              <div className="metric-copy"><span>Processed today</span><strong>$24,892.40</strong><small className="positive"><ArrowUpRight size={13} /> 12.4% vs yesterday</small></div>
              <div className="mini-chart green"><i style={{height:"32%"}}/><i style={{height:"48%"}}/><i style={{height:"43%"}}/><i style={{height:"61%"}}/><i style={{height:"72%"}}/><i style={{height:"67%"}}/><i style={{height:"88%"}}/></div>
            </article>
            <article className="metric-card action-card">
              <div className="metric-icon amber"><Fingerprint size={20} /></div>
              <div className="metric-copy"><span>Needs approval</span><strong>2 intents</strong><small><Clock3 size={13} /> Oldest waiting 2m 14s</small></div>
              <button onClick={() => { navigate("approval-queue", "Needs action"); document.getElementById("approval-queue")?.scrollIntoView({ behavior: "smooth" }); }}>Review <ArrowRight size={15} /></button>
            </article>
            <article className="metric-card">
              <div className="metric-icon blue"><RefreshCw size={20} /></div>
              <div className="metric-copy"><span>Reconciling</span><strong>4 payments</strong><small><Activity size={13} /> P95 finality 41.8s</small></div>
              <div className="ring"><span>94<sup>%</sup></span></div>
            </article>
            <article className="metric-card">
              <div className="metric-icon violet"><Gauge size={20} /></div>
              <div className="metric-copy"><span>Run-cost budget</span><strong>$18.62</strong><small className="neutral">of $30.00 daily allocation</small></div>
              <div className="budget-meter"><span style={{width:"62%"}} /></div>
            </article>
          </section>

          <section className="dashboard-grid">
            <article className="panel rails-panel" id="provider-rails">
              <div className="panel-head"><div><span className="section-kicker">Live network</span><h2>Provider rails</h2></div><button className="text-button">View diagnostics <ArrowUpRight size={14} /></button></div>
              <div className="rail-list">
                {rails.map((rail) => (
                  <div className="rail-row" key={rail.name}>
                    <div className="rail-logo" style={{"--rail": rail.color} as React.CSSProperties}>{rail.name.charAt(0)}</div>
                    <div className="rail-name"><b>{rail.name}</b><span>{rail.market}</span></div>
                    <div className="spark-bars" aria-hidden="true">{rail.bars.map((bar, index) => <i key={index} style={{height:`${bar}%`, background:rail.color}} />)}</div>
                    <div className="rail-stat"><span>Uptime</span><b>{rail.uptime}</b></div>
                    <div className="rail-stat"><span>Latency</span><b>{rail.latency}</b></div>
                    <div className={`health ${rail.status === "Degraded" ? "degraded" : ""}`}><span />{rail.status}</div>
                  </div>
                ))}
              </div>
            </article>

            <article className="panel audit-panel" id="audit-oscal">
              <div className="panel-head"><div><span className="section-kicker">Immutable evidence</span><h2>Audit stream</h2></div><button className="icon-button"><SlidersHorizontal size={16} /></button></div>
              <div className="audit-list">
                {auditEvents.map((event) => (
                  <div className="audit-item" key={event.time}>
                    <span className={`audit-node ${event.tone}`} />
                    <div><b>{event.title}</b><p>{event.detail}</p></div>
                    <time>{event.time}</time>
                  </div>
                ))}
              </div>
              <div className="audit-footer"><FileCheck2 size={15} /><span>OSCAL artifacts current</span><b>AR-2026-09-01</b></div>
            </article>
          </section>

          <section className="workbench-grid">
            <article className="panel ledger-panel" id="payment-intents">
              <span className="anchor-target" id="reconciliation" aria-hidden="true" />
              <div className="panel-head ledger-heading">
                <div><span className="section-kicker">Provider-neutral ledger</span><h2>Payment intents</h2></div>
                <button className="secondary-button"><ArrowDownRight size={15} /> Export</button>
              </div>
              <div className="filter-tabs" role="tablist">
                {(["All", "Needs action", "In flight", "Final"] as const).map((tab) => (
                  <button role="tab" aria-selected={filter === tab} className={filter === tab ? "selected" : ""} onClick={() => setFilter(tab)} key={tab}>{tab}{tab === "Needs action" ? <span>2</span> : null}</button>
                ))}
              </div>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Intent</th><th>Rail</th><th>Amount</th><th>Status</th><th>Age</th><th /></tr></thead>
                  <tbody>
                    {visiblePayments.map((payment) => (
                      <tr className={selected.id === payment.id ? "active-row" : ""} onClick={() => choosePayment(payment)} key={payment.id}>
                        <td><div className="intent-cell"><span>{payment.id.slice(-2)}</span><div><b>{payment.intent}</b><small>{payment.id} · {payment.recipient}</small></div></div></td>
                        <td><b className="rail-label">{payment.rail}</b><small>{payment.corridor}</small></td>
                        <td><b>{payment.amount}</b><small>≈ ${payment.amountUsd.toFixed(2)}</small></td>
                        <td><StatusPill status={payment.status} /></td>
                        <td className="age">{payment.age}</td>
                        <td><ArrowRight size={15} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {visiblePayments.length === 0 ? <div className="empty-state"><Search size={22} /><b>No matching intents</b><span>Try another status or search term.</span></div> : null}
              </div>
              <div className="table-footer"><span>Showing {visiblePayments.length} of {payments.length} intents</span><button>View full ledger <ArrowRight size={14} /></button></div>
            </article>

            <article className="panel approval-panel" id="approval-queue">
              <div className="approval-top">
                <div className="approval-title"><div className="shield-badge"><ShieldCheck size={20} /></div><div><span className="section-kicker">Human verification</span><h2>Decision required</h2></div></div>
                <span className={`risk ${selected.risk.toLowerCase()}`}>{selected.risk} risk</span>
              </div>

              {selected.status === "Awaiting approval" ? (
                <>
                  <div className="approval-summary">
                    <div><span>{selected.intent}</span><strong>{selected.amount}</strong><small>{selected.id} · {selected.rail} · {selected.corridor}</small></div>
                    <div className="destructive-tag"><AlertTriangle size={14} /> destructive</div>
                  </div>
                  <div className="route-line"><span>Planner</span><ArrowRight size={13}/><span>Policy gate</span><ArrowRight size={13}/><b>Operator</b><ArrowRight size={13}/><span className="muted">{selected.rail}</span></div>
                  <div className="checklist-head"><div><b>Verification checklist</b><span>{checks.filter(Boolean).length} of 5 complete</span></div><div className="progress-dots">{checks.map((checked, index) => <i className={checked ? "done" : ""} key={index} />)}</div></div>
                  <div className="checklist">
                    {questions.map((question, index) => (
                      <label key={question}>
                        <input type="checkbox" checked={checks[index]} onChange={() => setChecks((current) => current.map((value, itemIndex) => itemIndex === index ? !value : value))} />
                        <span className="custom-check">{checks[index] ? <Check size={13} /> : null}</span>
                        <span>{question}</span>
                      </label>
                    ))}
                  </div>
                  <label className="confirmation-field"><span>Confirmation attestation</span><div><KeyRound size={15}/><input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder="OAuth / OTP verification reference" /></div><small>Enter a verifier-issued reference, never a raw OTP or PIN.</small></label>
                  {notice ? <div className="notice"><CheckCircle2 size={15}/>{notice}</div> : null}
                  <div className="decision-actions">
                    <button className="reject-button" onClick={() => decide("Failed")}><XCircle size={16}/> Reject</button>
                    <button className="approve-button" disabled={!ready} onClick={() => decide("Reconciling")}><BadgeCheck size={16}/> Approve & release</button>
                  </div>
                  <div className="approval-foot"><Fingerprint size={14}/><span>Decision is signed and appended to the OSCAL audit pack.</span></div>
                </>
              ) : (
                <div className="resolved-state">
                  {selected.status === "Confirmed" ? <CheckCircle2 size={40}/> : selected.status === "Reconciling" ? <RefreshCw className="spin" size={40}/> : <XCircle size={40}/>}
                  <h3>{selected.status}</h3>
                  <p>{selected.status === "Reconciling" ? "Provider-head verification is in progress. The callback remains an occurrence hint." : "This intent no longer requires an operator decision."}</p>
                  {notice ? <div className="notice"><CheckCircle2 size={15}/>{notice}</div> : null}
                  <button className="secondary-button" onClick={() => { const next = payments.find((payment) => payment.status === "Awaiting approval"); if (next) choosePayment(next); }}>Open next approval</button>
                </div>
              )}
            </article>
          </section>

          <section className="configuration-grid" aria-label="Configuration status">
            <article className="panel configuration-card" id="policy-controls">
              <div className="configuration-icon"><ShieldCheck size={19} /></div>
              <div><span className="section-kicker">Policy controls</span><h3>Destructive actions gated</h3><p>Verifier-backed confirmation, Decimal limits, and budget hard stops are enforced.</p></div>
              <BadgeCheck size={18} />
            </article>
            <article className="panel configuration-card" id="agent-graph">
              <div className="configuration-icon"><Bot size={19} /></div>
              <div><span className="section-kicker">Agent graph</span><h3>Policy-first routing</h3><p>Planner → policy gate → operator → allowlisted provider execution.</p></div>
              <Network size={18} />
            </article>
            <article className="panel configuration-card" id="infrastructure">
              <div className="configuration-icon"><ServerCog size={19} /></div>
              <div><span className="section-kicker">Infrastructure</span><h3>Checkpointing healthy</h3><p>PostgreSQL FSM, asynchronous outbox, and telemetry exporters are online.</p></div>
              <CheckCircle2 size={18} />
            </article>
          </section>

          <footer className="page-footer"><span><BrandMark /> AgentBridge Africa · Operator Console v0.1</span><span><BookOpen size={14}/> Runbook <i/> API healthy <i/> PostgreSQL checkpointing</span></footer>
        </div>
      </main>
    </div>
  );
}

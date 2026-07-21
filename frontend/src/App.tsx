import { useEffect, useMemo, useState } from 'react'

type Scenario = 'post-promo'|'new-sku'|'congested-aisle'
type Aisle = {aisle:number;occupancy:number;velocity:number;congestion:number}
type Warehouse = {scenario:Scenario;locations:number;skus:number;assignments:number;version:number;aisles:Aisle[]}
type Move = {sequence:number;sku_id:string;from_location:string|null;to_location:string;reason:string}
type Proposal = {id:string;scenario:Scenario;moves:Move[];before_travel:number;projected_travel:number;projected_reduction_pct:number;status:string;version:number;explanations:string[]}
type Audit = {seq:number;event_type:string;actor:string;created_at:string;payload:Record<string,unknown>}

const scenarios:{id:Scenario;label:string;note:string}[] = [
  {id:'post-promo',label:'Post-promo shift',note:'Velocity changed after a campaign'},
  {id:'new-sku',label:'New SKU intro',note:'20 items waiting in receiving'},
  {id:'congested-aisle',label:'Hot aisle',note:'Aisle 01 congestion penalty'},
]

async function api<T>(path:string, init?:RequestInit):Promise<T>{
  const response=await fetch(path,{headers:{'Content-Type':'application/json'},...init})
  if(!response.ok){const body=await response.json().catch(()=>({detail:'Request failed'}));throw new Error(body.detail)}
  return response.json()
}

function format(value:number){return new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(value)}

export function App(){
  const [warehouse,setWarehouse]=useState<Warehouse|null>(null)
  const [proposal,setProposal]=useState<Proposal|null>(null)
  const [audit,setAudit]=useState<Audit[]>([])
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [actor,setActor]=useState('Alex Morgan')
  const [notice,setNotice]=useState('Synthetic environment ready')
  const [view,setView]=useState<'review'|'audit'>('review')

  async function refresh(){
    const [w,a]=await Promise.all([api<Warehouse>('/api/warehouse'),api<Audit[]>('/api/audit')]);setWarehouse(w);setAudit(a)
  }
  useEffect(()=>{refresh().catch(e=>setError(e.message))},[])
  async function run(action:()=>Promise<void>){setBusy(true);setError('');try{await action()}catch(e){setError(e instanceof Error?e.message:'Unexpected error')}finally{setBusy(false)}}
  function chooseScenario(scenario:Scenario){run(async()=>{await api('/api/seed',{method:'POST',body:JSON.stringify({scenario})});setProposal(null);setNotice('Drift injected · ready to optimize');await refresh()})}
  function optimize(){run(async()=>{const p=await api<Proposal>('/api/proposals',{method:'POST',body:JSON.stringify({batch_size:25})});setProposal(p);setNotice(`${p.moves.length} moves simulated · approval required`);await refresh()})}
  function approve(){if(!proposal)return;run(async()=>{const result=await api<{approval_token:string}>(`/api/proposals/${proposal.id}/approve`,{method:'POST',body:JSON.stringify({actor,expected_version:proposal.version})});await api(`/api/proposals/${proposal.id}/execute`,{method:'POST',body:JSON.stringify({approval_token:result.approval_token,idempotency_key:`ui-${proposal.id}`})});const p=await api<Proposal>(`/api/proposals/${proposal.id}`);setProposal(p);setNotice('Approved batch applied to simulated WMS');await refresh()})}
  function reject(){if(!proposal)return;run(async()=>{await api(`/api/proposals/${proposal.id}/reject`,{method:'POST',body:JSON.stringify({actor,expected_version:proposal.version,reason:'Operator requested rework'})});setProposal({...proposal,status:'rejected'});setNotice('Proposal rejected · no WMS writes made');await refresh()})}
  const maxVelocity=useMemo(()=>Math.max(...(warehouse?.aisles.map(a=>a.velocity)??[1])),[warehouse])

  return <div className="app-shell">
    <div className="prototype-banner">SYNTHETIC · NON-COMMERCIAL PORTFOLIO PROTOTYPE · LOCAL DATA ONLY</div>
    <header className="topbar">
      <a className="brand" href="#main" aria-label="SlotSmith home"><span className="mark">S</span><span>SlotSmith<small>Governed slotting control</small></span></a>
      <nav aria-label="Primary"><button className={view==='review'?'active':''} onClick={()=>setView('review')}>Operations</button><button className={view==='audit'?'active':''} onClick={()=>setView('audit')}>Audit trail</button></nav>
      <div className="system-state"><span className="pulse"/> Deterministic core online</div>
    </header>
    <main id="main">
      <section className="hero">
        <div><p className="eyebrow">Decision workspace / {warehouse?.scenario ?? 'loading'}</p><h1>Turn slotting drift into<br/><em>controlled movement.</em></h1><p>Every recommendation is constraint-checked, simulated, bounded, and held for a named human decision.</p></div>
        <div className="run-card"><span>Run state</span><strong>{notice}</strong><button className="primary" onClick={optimize} disabled={busy||!warehouse}>{busy?'Working…':'Optimize current drift'} <b>→</b></button></div>
      </section>
      {error&&<div className="error" role="alert">{error}<button onClick={()=>setError('')} aria-label="Dismiss">×</button></div>}
      {view==='review'?<>
        <section className="scenario-row" aria-label="Demo scenarios">{scenarios.map(s=><button key={s.id} className={warehouse?.scenario===s.id?'selected':''} onClick={()=>chooseScenario(s.id)} disabled={busy}><span>{s.label}</span><small>{s.note}</small></button>)}</section>
        <section className="metrics" aria-label="Warehouse summary">
          <article><span>Expected pick travel</span><strong>{proposal?format(proposal.before_travel):'—'}</strong><small>weighted distance / day</small></article>
          <article className="accent"><span>Projected reduction</span><strong>{proposal?`−${proposal.projected_reduction_pct.toFixed(2)}%`:'Run optimizer'}</strong><small>{proposal?`${format(proposal.before_travel-proposal.projected_travel)} units avoided`:'bounded at 25 moves'}</small></article>
          <article><span>Warehouse model</span><strong>{warehouse?format(warehouse.skus):'—'} <i>/</i> {warehouse?format(warehouse.locations):'—'}</strong><small>SKUs / available bins</small></article>
          <article><span>Control state</span><strong className="status-word">{proposal?.status??'Awaiting run'}</strong><small>WMS version {warehouse?.version??'—'}</small></article>
        </section>
        <section className="workspace">
          <article className="panel heatmap-panel"><div className="panel-head"><div><p className="eyebrow">Spatial signal</p><h2>Aisle velocity map</h2></div><div className="legend"><span/> lower <span/> higher</div></div>
            <div className="heatmap" aria-label="Aisle velocity heatmap">{warehouse?.aisles.map(a=>{const level=Math.max(.08,a.velocity/maxVelocity);return <div key={a.aisle} className={a.congestion?'hot':''} style={{'--level':level} as React.CSSProperties} title={`Aisle ${a.aisle}: ${format(a.velocity)} velocity`}><b>{String(a.aisle).padStart(2,'0')}</b><span>{a.occupancy} slots</span>{a.congestion>0&&<i>HOT</i>}</div>})}</div>
            <div className="model-note"><span>Model</span><p>Greedy assignment + deterministic local search minimizes expected travel and congestion while preserving zone, capacity, weight, hazmat, and ergonomic rules.</p></div>
          </article>
          <article className="panel moves-panel"><div className="panel-head"><div><p className="eyebrow">Bounded proposal</p><h2>Move sequence</h2></div><span className="count">{proposal?.moves.length??0} / 25</span></div>
            {!proposal?<div className="empty"><span>↗</span><h3>No move list yet</h3><p>Select a drift scenario and run the optimizer. Nothing moves until approval.</p></div>:<div className="move-list">{proposal.moves.slice(0,6).map((m,i)=><div className="move" key={m.sku_id}><b>{m.sequence}</b><div><strong>{m.sku_id}</strong><small>{proposal.explanations[i]}</small></div><code>{m.from_location??'RECEIVING'} <i>→</i> {m.to_location}</code></div>)}{proposal.moves.length>6&&<p className="more">+ {proposal.moves.length-6} more ordered moves in batch</p>}</div>}
          </article>
        </section>
        {proposal&&<section className="approval"><div><p className="eyebrow">Human control point</p><h2>{proposal.status==='proposed'?'Approve physical work':'Decision recorded'}</h2><p>Execution requires an attributable, single-proposal token. Replays with the same idempotency key return the original result.</p></div><label>Approver name<input value={actor} onChange={e=>setActor(e.target.value)} disabled={proposal.status!=='proposed'}/></label><div className="decision-actions"><button className="reject" onClick={reject} disabled={busy||proposal.status!=='proposed'}>Reject batch</button><button className="primary" onClick={approve} disabled={busy||proposal.status!=='proposed'||!actor.trim()}>Approve & execute</button></div></section>}
      </>:<section className="audit-view"><div className="panel-head"><div><p className="eyebrow">Append-only evidence</p><h2>Decision ledger</h2></div><span className="count">{audit.length} events</span></div><div className="audit-list">{[...audit].reverse().map(e=><article key={e.seq}><span>{String(e.seq).padStart(3,'0')}</span><div><strong>{e.event_type}</strong><small>{new Date(e.created_at).toLocaleString()} · {e.actor}</small></div><code>{JSON.stringify(e.payload)}</code></article>)}</div></section>}
    </main>
    <footer><span>SlotSmith / fixed seed 240519</span><span>No proprietary data · No API key · No autonomous movement</span></footer>
  </div>
}

"use client";
import { useState, useEffect } from "react";
import { useApp } from "@/lib/state";
import * as api from "@/lib/api";
import { Btn, SectionLabel, showToast } from "./ui";
import type { Dataset } from "@/types/vrp";

const MODE_INFO=[
  {v:"cheapest",  e:"💰",l:"Cheapest",  desc:"Min fuel ₮/km"},
  {v:"fastest",   e:"⚡",l:"Fastest",   desc:"Min travel time"},
  {v:"shortest",  e:"📏",l:"Shortest",  desc:"Min km driven"},
  {v:"balanced",  e:"⚖️",l:"Balanced",  desc:"Even truck loads"},
  {v:"geographic",e:"🗺",l:"Geographic",desc:"Tight zone clusters"},
];

export const MODE_COLOR:Record<string,string>={
  cheapest:"#F59E0B",fastest:"#5B7CFA",shortest:"#10B981",balanced:"#8B5CF6",geographic:"#0EA5E9",
};

function DsCard({ds,active,onClick}:{ds:Dataset;active:boolean;onClick:()=>void;}){
  return(
    <div onClick={onClick} className={`rounded-xl border-[1.5px] p-2.5 cursor-pointer transition-all ${active?"border-blue-500 bg-blue-500/5":"border-slate-200 bg-white hover:border-blue-500/40"}`}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[12px] font-bold text-slate-900 truncate">{ds.name}</span>
        {active && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-blue-500/10 text-blue-500">ACTIVE</span>}
      </div>
      <div className="flex gap-2 text-[10px] text-slate-500">
        <span>🏪 {ds.store_count}</span>
        <span>🚛 {ds.vehicle_count}</span>
        <span className={ds.has_matrix?"text-green-500 font-semibold":"text-amber-500 font-semibold"}>
          {ds.has_matrix?"✅ matrix":"⚠ no matrix"}
        </span>
      </div>
    </div>
  );
}

async function loadBoth(id:number,d:(a:any)=>void){
  const[stores,vehicles]=await Promise.all([api.getStores(id),api.getVehicles(id)]);
  d({t:"SET_STORES",v:stores}); d({t:"SET_VEHICLES",v:vehicles});
}

export function RunPanel(){
  const{s,d}=useApp();
  const[mode,setMode]=useState("cheapest");
  const[trips,setTrips]=useState(2);
  const[time,setTime]=useState(60);
  const[weightFill,setWeightFill]=useState(0.7);
  const[volumeFill,setVolumeFill]=useState(0.8);
  const[targetGroup,setTargetGroup]=useState<string>("none");
  const[versionName,setVersionName]=useState("");
  const canRun = s.activeDatasetId != null && !s.running;

  useEffect(()=>{
    if(targetGroup!=="none"&&!s.runGroups.find((g:any)=>g.id===targetGroup)) setTargetGroup("none");
  },[s.runGroups]);

  async function run(){
    if(!s.activeDatasetId){ showToast("Select a dataset first","error"); return; }
    const ds = s.datasets.find(d=>d.id===s.activeDatasetId);
    if(ds && !ds.has_matrix){ showToast("Dataset has no matrix — rebuild it in the Data tab","error"); return; }
    d({t:"SET_RUNNING",v:true});
    try{
      const gid=targetGroup==="none"?undefined:targetGroup;
      const r=await api.optimize({
        mode, max_trips:trips, solver_time:time,
        max_weight_fill:weightFill,
        max_volume_fill:volumeFill,
        dataset_id:s.activeDatasetId,
        group_id:gid,
        version_name:versionName.trim()||undefined,
      });
      d({t:"SET_RESULT",jobId:r.job_id,r});
      d({t:"SET_MAIN",v:"map"});
      await Promise.all([
        api.getJobs().then(v=>d({t:"SET_JOBS",v})),
        api.getRunGroups().then(v=>d({t:"SET_GROUPS",v})),
      ]);
      setVersionName("");
      showToast(`✅ ${r.summary.total_served} served, ${r.summary.total_unserved} unserved`,"success");
    }catch(e:any){showToast(e.message??"Optimization failed","error");}
    finally{d({t:"SET_RUNNING",v:false});}
  }

  return(
    <>
      <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-4">

        {/* Dataset selection */}
        <div>
          <SectionLabel label="📁 Dataset"/>
          {!s.datasets.length
            ? <p className="text-[11px] text-slate-500 bg-slate-50 rounded-xl p-3 text-center">No datasets — create one in the Data tab</p>
            : <div className="flex flex-col gap-1.5 max-h-40 overflow-y-auto">
                {s.datasets.map((ds:any)=>(
                  <DsCard key={ds.id} ds={ds} active={s.activeDatasetId===ds.id}
                    onClick={()=>{
                      const id=s.activeDatasetId===ds.id?null:ds.id;
                      d({t:"SET_DS",v:id});
                      if(id) loadBoth(id,d);
                    }}/>
                ))}
              </div>
          }
          {s.activeDatasetId && !s.datasets.find(d=>d.id===s.activeDatasetId)?.has_matrix && (
            <div className="mt-2 p-2.5 bg-amber-50 border border-amber-200 rounded-xl text-[11px] text-amber-600 font-medium">
              ⚠ No distance matrix — go to Data tab and rebuild it before running.
            </div>
          )}
        </div>

        {/* Mode */}
        <div>
          <SectionLabel label="🎯 Mode"/>
          <div className="grid grid-cols-3 gap-1.5 mb-1.5">
            {MODE_INFO.slice(0,3).map(m=>{
              const act=mode===m.v; const c=MODE_COLOR[m.v];
              return(<button key={m.v} onClick={()=>setMode(m.v)}
                className="py-2 rounded-xl border-[1.5px] text-[11px] font-semibold text-center transition-all"
                style={{borderColor:act?c:"rgb(226 232 240)",background:act?c+"14":"#fff",color:act?c:"rgb(100 116 139)"}}>
                <div className="text-[15px] mb-0.5">{m.e}</div>
                <div className="font-bold text-[10px]">{m.l}</div>
                <div className="text-[9px] opacity-60">{m.desc}</div>
              </button>);
            })}
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            {MODE_INFO.slice(3).map(m=>{
              const act=mode===m.v; const c=MODE_COLOR[m.v];
              return(<button key={m.v} onClick={()=>setMode(m.v)}
                className="py-2 rounded-xl border-[1.5px] text-[11px] font-semibold text-center transition-all"
                style={{borderColor:act?c:"rgb(226 232 240)",background:act?c+"14":"#fff",color:act?c:"rgb(100 116 139)"}}>
                <div className="text-[15px] mb-0.5">{m.e}</div>
                <div className="font-bold text-[10px]">{m.l}</div>
                <div className="text-[9px] opacity-60">{m.desc}</div>
              </button>);
            })}
          </div>
        </div>

        {/* Parameters */}
        <div>
          <SectionLabel label="⚙️ Parameters"/>
          <div className="flex flex-col divide-y divide-slate-200">
            <div className="flex items-center justify-between py-2">
              <span className="text-[11px] text-slate-500">Max trips / vehicle</span>
              <input type="number" min={1} max={5} value={trips} onChange={e=>setTrips(Number(e.target.value))}
                className="w-14 text-right text-[12px] font-mono border border-slate-200 rounded-lg px-2 py-1 bg-white outline-none focus:border-blue-500"/>
            </div>
            <div className="flex items-center justify-between py-2">
              <span className="text-[11px] text-slate-500">Solver time (s)</span>
              <input type="number" min={5} max={600} value={time} onChange={e=>setTime(Number(e.target.value))}
                className="w-14 text-right text-[12px] font-mono border border-slate-200 rounded-lg px-2 py-1 bg-white outline-none focus:border-blue-500"/>
            </div>
            <div className="flex items-center justify-between py-2">
              <span className="text-[11px] text-slate-500">Weight fill %</span>
              <div className="flex items-center gap-2">
                <input type="range" min="0" max="100" value={weightFill*100}
                  onChange={e=>setWeightFill(Number(e.target.value)/100)}
                  className="w-20 h-1 bg-slate-200 rounded-lg appearance-none cursor-pointer"/>
                <span className="text-[11px] font-mono text-slate-600 w-10 text-right">{Math.round(weightFill*100)}%</span>
              </div>
            </div>
            <div className="flex items-center justify-between py-2">
              <span className="text-[11px] text-slate-500">Volume fill %</span>
              <div className="flex items-center gap-2">
                <input type="range" min="0" max="100" value={volumeFill*100}
                  onChange={e=>setVolumeFill(Number(e.target.value)/100)}
                  className="w-20 h-1 bg-slate-200 rounded-lg appearance-none cursor-pointer"/>
                <span className="text-[11px] font-mono text-slate-600 w-10 text-right">{Math.round(volumeFill*100)}%</span>
              </div>
            </div>
          </div>
        </div>

        {/* Version group */}
        <div>
          <SectionLabel label="📌 Version group (optional)"/>
          <div className="flex flex-col gap-2">
            <select value={targetGroup} onChange={e=>setTargetGroup(e.target.value)}
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-[12px] outline-none focus:border-blue-500">
              <option value="none">— standalone run —</option>
              {s.runGroups.map((g:any)=><option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
            {targetGroup!=="none"&&(
              <input value={versionName} onChange={e=>setVersionName(e.target.value)}
                placeholder="Version label (auto if blank)"
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-[12px] outline-none focus:border-blue-500"/>
            )}
            <button className="text-[11px] text-blue-500 font-semibold text-left hover:underline"
              onClick={async()=>{
                const name=prompt("Group name:");if(!name?.trim())return;
                const g=await api.createRunGroup(name.trim(),s.activeDatasetId??undefined);
                const groups=await api.getRunGroups();
                d({t:"SET_GROUPS",v:groups});setTargetGroup(g.id);
              }}>+ Create new group</button>
          </div>
        </div>
      </div>

      <div className="shrink-0 p-3 border-t border-slate-200 bg-white">
        <Btn variant="primary" size="lg" className="w-full" loading={s.running} disabled={!canRun} onClick={run}>
          {s.running?"Solving…":"▶ Run Optimization"}
        </Btn>
        {/* {!s.activeDatasetId && (
          <p className="text-center text-[10px] text-slate-400 mt-2">Select a dataset above to enable</p>
        )} */}
      </div>
    </>
  );
}
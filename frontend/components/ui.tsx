/* ─────────────────────────────────────────────────────
   Pure-Tailwind UI primitives — no external UI library
   ───────────────────────────────────────────────────── */
"use client";
import { useState, useRef, type ReactNode } from "react";

/* ── Button ──────────────────────────────────────────── */
type BtnVariant="primary"|"secondary"|"ghost"|"danger";
interface BtnProps extends React.ButtonHTMLAttributes<HTMLButtonElement>{
  variant?:BtnVariant; loading?:boolean; size?:"sm"|"md"|"lg";
}
export function Btn({variant="secondary",loading,size="md",className="",children,...rest}:BtnProps){
  const base="inline-flex items-center justify-center gap-2 font-semibold rounded-xl border transition-all duration-150 cursor-pointer select-none disabled:opacity-40 disabled:cursor-not-allowed";
  const sz={sm:"px-3 py-1.5 text-[11px]",md:"px-4 py-2 text-[12px]",lg:"px-5 py-3 text-[14px] font-bold"}[size];
  const v={
    primary:"bg-blue-500 border-blue-500 text-white hover:bg-blue-600 shadow-[0_4px_14px_rgba(59,130,246,0.3)]",
    secondary:"bg-white border-slate-200 text-slate-900 hover:border-blue-500 hover:text-blue-500",
    ghost:"bg-transparent border-transparent text-slate-500 hover:text-slate-900 hover:bg-slate-50",
    danger:"bg-white border-red-300 text-red-500 hover:bg-red-50",
  }[variant];
  return(
    <button className={`${base} ${sz} ${v} ${className}`} disabled={loading||rest.disabled} {...rest}>
      {loading&&<span className="w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full anim-pulse inline-block"/>}
      {children}
    </button>
  );
}

/* ── Input ───────────────────────────────────────────── */
interface InputProps extends React.InputHTMLAttributes<HTMLInputElement>{label?:string;}
export function Input({label,className="",id,...rest}:InputProps){
  return(
    <div className="flex flex-col gap-1">
      {label&&<label htmlFor={id} className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">{label}</label>}
      <input id={id} className={`w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-[12px] text-slate-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/12 placeholder:text-slate-300 transition-all ${className}`} {...rest}/>
    </div>
  );
}

/* ── Select ──────────────────────────────────────────── */
interface SelProps extends React.SelectHTMLAttributes<HTMLSelectElement>{label?:string;options:{v:string;l:string}[];}
export function Sel({label,options,className="",id,...rest}:SelProps){
  return(
    <div className="flex flex-col gap-1">
      {label&&<label htmlFor={id} className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">{label}</label>}
      <select id={id} className={`w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-[12px] text-slate-900 outline-none focus:border-blue-500 transition-all ${className}`} {...rest}>
        {options.map(o=><option key={o.v} value={o.v}>{o.l}</option>)}
      </select>
    </div>
  );
}

/* ── NumberInput ─────────────────────────────────────── */
interface NumProps{label?:string;value:number;min?:number;max?:number;step?:number;onChange:(v:number)=>void;className?:string;}
export function NumInput({label,value,min,max,step=1,onChange,className=""}:NumProps){
  return(
    <div className="flex flex-col gap-1">
      {label&&<span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">{label}</span>}
      <input type="number" value={value} min={min} max={max} step={step}
        className={`rounded-xl border border-slate-200 bg-white px-3 py-2 text-[12px] font-mono text-slate-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/12 transition-all ${className}`}
        onChange={e=>onChange(Number(e.target.value))}/>
    </div>
  );
}

/* ── Toggle ──────────────────────────────────────────── */
export function Toggle({checked,onChange,label}:{checked:boolean;onChange:(v:boolean)=>void;label?:string;}){
  return(
    <label className="flex items-center gap-2 cursor-pointer select-none">
      <button type="button" role="switch" aria-checked={checked} onClick={()=>onChange(!checked)}
        className={`relative w-9 h-5 rounded-full transition-colors duration-200 ${checked?"bg-blue-500":"bg-slate-200"}`}>
        <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200 ${checked?"translate-x-4":""}`}/>
      </button>
      {label&&<span className="text-[11px] text-slate-500">{label}</span>}
    </label>
  );
}

/* ── Modal ───────────────────────────────────────────── */
export function Modal({open,onClose,title,children,onOk,okLabel="Save",loading}:{
  open:boolean;onClose:()=>void;title:string;children:ReactNode;onOk?:()=>void;okLabel?:string;loading?:boolean;
}){
  if(!open) return null;
  return(
    <div className="fixed inset-0 z-9000 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/20 backdrop-blur-sm" onClick={onClose}/>
      <div className="relative bg-white rounded-2xl shadow-2xl w-full max-w-lg max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200 bg-slate-50 rounded-t-2xl shrink-0">
          <h3 className="font-bold text-[14px] text-slate-900">{title}</h3>
          <button onClick={onClose} className="w-7 h-7 rounded-lg border border-slate-200 flex items-center justify-center text-slate-500 hover:bg-slate-50 text-[12px]">✕</button>
        </div>
        <div className="overflow-y-auto flex-1 p-5">{children}</div>
        {onOk&&(
          <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-200 shrink-0">
            <Btn variant="ghost" size="sm" onClick={onClose}>Cancel</Btn>
            <Btn variant="primary" size="sm" onClick={onOk} loading={loading}>{okLabel}</Btn>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Confirm ─────────────────────────────────────────── */
export function Confirm({onConfirm,children,message="Are you sure?",cancelText="No",confirmText="Yes"}:{onConfirm:()=>void;children:ReactNode;message?:string;cancelText?:string;confirmText?:string;}){
  const[open,setOpen]=useState(false);
  return(
    <>
      <div onClick={e=>{e.stopPropagation();setOpen(true)}}>{children}</div>
      {open&&(
        <div className="fixed inset-0 z-9999 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-slate-900/20" onClick={()=>setOpen(false)}/>
          <div className="relative bg-white rounded-2xl shadow-xl p-5 w-64">
            <p className="text-[13px] font-semibold text-slate-900 mb-4">{message}</p>
            <div className="flex gap-2 justify-end">
              <Btn size="sm" variant="ghost" onClick={()=>setOpen(false)}>{cancelText}</Btn>
              <Btn size="sm" variant="danger" onClick={()=>{onConfirm();setOpen(false);}}>{confirmText}</Btn>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/* ── Toast ───────────────────────────────────────────── */
type ToastType="success"|"error"|"info";
const toasts:{id:number;msg:string;type:ToastType}[]=[];
let toastSubs:Array<()=>void>=[];
let nextId=0;
export function showToast(msg:string,type:ToastType="success"){
  const id=++nextId;
  toasts.push({id,msg,type});
  toastSubs.forEach(fn=>fn());
  setTimeout(()=>{const i=toasts.findIndex(t=>t.id===id);if(i>-1)toasts.splice(i,1);toastSubs.forEach(fn=>fn());},3200);
}
export function Toaster(){
  const[,rerender]=useState(0);
  useRef(null); // stable ref
  toastSubs=[()=>rerender(n=>n+1)]; // subscribe
  const colors={success:"bg-green-500",error:"bg-red-500",info:"bg-blue-500"};
  return(
    <div className="fixed top-4 right-4 z-9999 flex flex-col gap-2">
      {toasts.map(t=>(
        <div key={t.id} className={`${colors[t.type]} text-white text-[12px] font-semibold px-4 py-2.5 rounded-xl shadow-lg anim-up`}>
          {t.msg}
        </div>
      ))}
    </div>
  );
}

/* ── Upload zone ─────────────────────────────────────── */
export function UploadZone({label,icon,accept,onFile,fileName}:{label:string;icon:string;accept:string;onFile:(f:File)=>void;fileName?:string;}){
  return(
    <label className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-3 text-center cursor-pointer relative transition-all duration-150 ${fileName?"border-green-500/50 bg-green-500/4":"border-slate-300 bg-white hover:border-blue-500 hover:bg-blue-500/4"}`}>
      <input type="file" accept={accept} className="absolute inset-0 opacity-0 cursor-pointer" onChange={e=>{const f=e.target.files?.[0];if(f)onFile(f);}}/>
      <span className="text-lg mb-1">{icon}</span>
      {fileName
        ?<span className="text-[10px] text-green-500 font-mono break-all">✓ {fileName}</span>
        :<span className="text-[10px] text-slate-500">{label}</span>}
    </label>
  );
}

/* ── Pill / chip ─────────────────────────────────────── */
export function Pill({label,color}:{label:string;color?:string;}){
  const c=color??"#3B82F6";
  return(
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold"
      style={{background:c+"18",color:c}}>
      {label}
    </span>
  );
}

/* ── Section heading ─────────────────────────────────── */
export function SectionLabel({label,action}:{label:string;action?:ReactNode;}){
  return(
    <div className="flex items-center justify-between mb-2">
      <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">{label}</span>
      {action}
    </div>
  );
}

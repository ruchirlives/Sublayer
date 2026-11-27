import {useEffect,useState} from "react";
export function OperationProgress({label,detail,progress}:{label:string;detail?:string;progress?:number|null}){
 const [seconds,setSeconds]=useState(0);
 useEffect(()=>{setSeconds(0);const started=Date.now();const timer=window.setInterval(()=>setSeconds(Math.floor((Date.now()-started)/1000)),1000);return()=>window.clearInterval(timer);},[label]);
 const pct=typeof progress==="number"&&Number.isFinite(progress)?Math.max(0,Math.min(100,Math.round(progress*100))):null;
 return <div className="operation-progress"><div className="operation-progress-label"><span role="status">{detail?`${label} (${detail})`:label}</span><small>{pct!==null?`${pct}% · ${seconds}s`:`${seconds}s`}</small></div><div className={"operation-progress-track"+(pct!==null?" determinate":"")} role="progressbar" aria-label={label} aria-valuemin={pct!==null?0:undefined} aria-valuemax={pct!==null?100:undefined} aria-valuenow={pct!==null?pct:undefined} aria-valuetext={pct!==null?`${pct}%`: "In progress; percentage unavailable"}><span style={pct!==null?{width:`${pct}%`}:undefined}/></div></div>;
}

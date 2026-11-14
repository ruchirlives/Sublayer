import {useEffect,useState} from "react";
export function OperationProgress({label}:{label:string}){
 const [seconds,setSeconds]=useState(0);
 useEffect(()=>{setSeconds(0);const started=Date.now();const timer=window.setInterval(()=>setSeconds(Math.floor((Date.now()-started)/1000)),1000);return()=>window.clearInterval(timer);},[label]);
 return <div className="operation-progress"><div className="operation-progress-label"><span role="status">{label}</span><small>{seconds}s</small></div><div className="operation-progress-track" role="progressbar" aria-label={label} aria-valuetext="In progress; percentage unavailable"><span/></div></div>;
}

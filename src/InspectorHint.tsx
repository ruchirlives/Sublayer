import {useId} from "react";
/** Non-interactive help inside labels: keyboard focus exposes the same hint as hover. */
export function InspectorHint({text,label="Help"}:{text:string;label?:string}){
 const id=useId();
 return <span className="inspector-hint" tabIndex={0} role="note" aria-label={label} aria-describedby={id}><span aria-hidden="true">?</span><span id={id} role="tooltip" className="inspector-hint-content">{text}</span></span>;
}

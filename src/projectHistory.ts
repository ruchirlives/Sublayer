import type {StoredProject} from "./fileRegistry";
function snapshot(project:StoredProject):StoredProject {
 const copy=structuredClone(project);
 copy.nodes=copy.nodes.map(n=>{const {selected,dragging,measured,resizing,...node}=n as typeof n & {resizing?:boolean};return node;});
 copy.edges=copy.edges.map(e=>{const {selected,...edge}=e;return edge;});
 return copy;
}
export class ProjectHistory {
 past:{project:StoredProject;label:string}[]=[];
 future:{project:StoredProject;label:string}[]=[];
 private group:string|null=null;
 private limit:number;
 constructor(limit=100){this.limit=limit;}
 clear(){this.past=[];this.future=[];this.group=null;}
 endGroup(){this.group=null;}
 record(before:StoredProject,after:StoredProject,label="Edit project",group:string|null=null){
  if(group && group===this.group){this.future=[];return false;}
  const a=snapshot(before),b=snapshot(after);
  if(JSON.stringify(a)===JSON.stringify(b))return false;
  if(!group || group!==this.group){this.past.push({project:a,label});while(this.past.length>this.limit || (this.past.length>1 && this.past.reduce((size,e)=>size+JSON.stringify(e.project).length*2,0)>64*1024*1024))this.past.shift();}
  this.future=[];this.group=group;return true;
 }
 undo(current:StoredProject){const entry=this.past.pop();if(!entry)return null;this.future.push({project:snapshot(current),label:entry.label});this.endGroup();return {project:structuredClone(entry.project),label:entry.label};}
 redo(current:StoredProject){const entry=this.future.pop();if(!entry)return null;this.past.push({project:snapshot(current),label:entry.label});this.endGroup();return {project:structuredClone(entry.project),label:entry.label};}
}
export function historyShortcut(event:Pick<KeyboardEvent,"key"|"ctrlKey"|"metaKey"|"shiftKey"|"altKey">,editing:boolean){
 if(editing || event.altKey || !(event.ctrlKey||event.metaKey))return null;
 const key=event.key.toLowerCase();
 if(key==="z")return event.shiftKey?"redo":"undo";
 return key==="y"?"redo":null;
}

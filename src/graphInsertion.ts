import type { StoredProject } from "./fileRegistry.ts";
export const toolbarOperations = ["source","metahuman","blender","transform","group","delete","scatter","instancer","assetOverride","materialx","material_bind","light","hdri","fog","postfx","hair","cache_edit","character","pose","camera","output","application"];
export type DropTarget = {kind:"edge"|"node";id:string} | null;
export function insertToolbarNode(project:StoredProject, node:StoredProject["nodes"][number], target:DropTarget, makeId=()=>crypto.randomUUID()):StoredProject {
  if(!toolbarOperations.includes(node.data.operation) || project.nodes.some(n=>n.id===node.id))throw new Error("Invalid or duplicate inserted node");
  let position={...node.position};
  let edges=[...project.edges];
  const shifted=new Set<string>();
  const verticalShift=new Map<string,number>();
  const width=(item:StoredProject["nodes"][number])=>Number(item.measured?.width || item.width || 266);
  const amount=width(node)+80;
  const height=(item:StoredProject["nodes"][number])=>Number(item.measured?.height || item.height || 180);
  function downstream(start:string,include:boolean) {
    const queue=include?[start]:project.edges.filter(e=>e.source===start).map(e=>e.target);
    while(queue.length){const id=queue.pop()!;if(shifted.has(id))continue;shifted.add(id);queue.push(...project.edges.filter(e=>e.source===id).map(e=>e.target));}
  }
  if(target?.kind === "edge") {
    const edge=edges.find(e=>e.id===target.id);
    if(!edge)throw new Error("The target edge no longer exists");
    const destination=project.nodes.find(n=>n.id===edge.target)!;
    position={...destination.position};
    downstream(destination.id,true);
    edges=edges.map(e=>e.id!==edge.id?e:{...e,source:node.id,sourceHandle:null,selected:false});
    edges.push({...edge,id:makeId(),target:node.id,targetHandle:null,label:undefined,selected:false});
  } else if(target?.kind === "node") {
    const destination=project.nodes.find(n=>n.id===target.id);
    if(!destination)throw new Error("The target node no longer exists");
    if(destination.data.operation !== "output") {
      position={x:destination.position.x+width(destination)+80,y:destination.position.y};
      // This is a parallel output, not an edge insertion. Pack existing
      // output branches below the new node, preserving horizontal alignment.
      const occupied=[{x:position.x,y:position.y,width:width(node),height:height(node)}];
      const outputs=[...new Set(project.edges.filter(e=>e.source===destination.id).map(e=>e.target))]
        .map(id=>project.nodes.find(n=>n.id===id)!).filter(Boolean)
        .sort((a,b)=>a.position.y-b.position.y || a.position.x-b.position.x || a.id.localeCompare(b.id));
      for(const output of outputs){
        let y=output.position.y;
        let overlap=true;
        while(overlap){
          overlap=false;
          for(const box of occupied){
            if(output.position.x<box.x+box.width+24 && output.position.x+width(output)+24>box.x && y<box.y+box.height+48 && y+height(output)+48>box.y){
              y=box.y+box.height+48;overlap=true;
            }
          }
        }
        const delta=y-output.position.y;
        occupied.push({x:output.position.x,y,width:width(output),height:height(output)});
        if(delta>0){
          const queue=[output.id],visited=new Set<string>();
          while(queue.length){const id=queue.pop()!;if(visited.has(id))continue;visited.add(id);verticalShift.set(id,Math.max(verticalShift.get(id)||0,delta));queue.push(...project.edges.filter(e=>e.source===id).map(e=>e.target));}
        }
      }
      edges.push({id:makeId(),source:destination.id,target:node.id});
    } else {
      position={...destination.position};
      downstream(destination.id,true);
      edges.push({id:makeId(),source:node.id,target:destination.id});
    }
  }
  return {...project,edges,nodes:[...project.nodes.map(n=>({...n,selected:false,position:shifted.has(n.id)?{x:n.position.x+amount,y:n.position.y}:verticalShift.has(n.id)?{x:n.position.x,y:n.position.y+verticalShift.get(n.id)!}:n.position})),{...node,position,selected:true}]};
}

export function detachNodeFromEdges(
  project: StoredProject,
  nodeId: string,
  makeId = () => crypto.randomUUID(),
): StoredProject {
  const incoming = project.edges.filter((e) => e.target === nodeId);
  const outgoing = project.edges.filter((e) => e.source === nodeId);
  if (!incoming.length && !outgoing.length) return project;

  const remaining = project.edges.filter(
    (e) => e.source !== nodeId && e.target !== nodeId,
  );
  const nextEdges = [...remaining];
  const hasEdge = (
    source: string,
    target: string,
    sourceHandle?: string | null,
    targetHandle?: string | null,
  ) =>
    nextEdges.some(
      (e) =>
        e.source === source &&
        e.target === target &&
        (e.sourceHandle ?? null) === (sourceHandle ?? null) &&
        (e.targetHandle ?? null) === (targetHandle ?? null),
    );

  for (const inEdge of incoming) {
    for (const outEdge of outgoing) {
      if (inEdge.source === outEdge.target) continue;
      if (
        !hasEdge(
          inEdge.source,
          outEdge.target,
          inEdge.sourceHandle,
          outEdge.targetHandle,
        )
      ) {
        nextEdges.push({
          id: makeId(),
          source: inEdge.source,
          target: outEdge.target,
          sourceHandle: inEdge.sourceHandle ?? null,
          targetHandle: outEdge.targetHandle ?? null,
        });
      }
    }
  }
  return { ...project, edges: nextEdges };
}

export function insertExistingNodeOnEdge(
  project: StoredProject,
  nodeId: string,
  edgeId: string,
  makeId = () => crypto.randomUUID(),
): StoredProject {
  const targetEdge = project.edges.find((e) => e.id === edgeId);
  if (!targetEdge || targetEdge.source === nodeId || targetEdge.target === nodeId) {
    return project;
  }

  // Detach nodeId cleanly first if it still has any links, preserving a valid DAG.
  const base = detachNodeFromEdges(project, nodeId, makeId);
  const edge = base.edges.find((e) => e.id === edgeId) || targetEdge;

  const reaches = (start: string, goal: string, seen = new Set<string>()): boolean => {
    if (start === goal) return true;
    if (seen.has(start)) return false;
    seen.add(start);
    return base.edges
      .filter((e) => e.source === start)
      .some((e) => reaches(e.target, goal, seen));
  };
  if (reaches(nodeId, edge.source) || reaches(edge.target, nodeId)) {
    return project;
  }

  const nextEdges = base.edges
    .filter((e) => e.id !== edge.id)
    .concat([
      {
        id: makeId(),
        source: edge.source,
        target: nodeId,
        sourceHandle: edge.sourceHandle ?? null,
        targetHandle: null,
      },
      {
        id: makeId(),
        source: nodeId,
        target: edge.target,
        sourceHandle: null,
        targetHandle: edge.targetHandle ?? null,
      },
    ]);

  return { ...base, edges: nextEdges };
}

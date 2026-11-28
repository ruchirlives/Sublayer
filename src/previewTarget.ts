import { graphPreview, type StoredProject } from "./fileRegistry.ts";

export function sceneHierarchyKey(graph?: string): string {
  if (!graph) return "";
  try {
    const parsed = JSON.parse(graph);
    if (Array.isArray(parsed?.nodes)) {
      parsed.nodes = parsed.nodes.map((node: Record<string, unknown>) => {
        if (node?.operation === "transform") return { ...node, transform: null };
        if (node?.operation === "hair" && node.hair && typeof node.hair === "object") {
          const { groomDeltas: _points, ...hair } = node.hair as Record<string, unknown>;
          return { ...node, hair };
        }
        if (node?.operation === "cache_edit") {
          return { ...node, cacheEdit: null };
        }
        if (node?.operation === "postfx") {
          return { ...node, postFx: null };
        }
        if (node?.operation === "hdri") {
          return { ...node, hdri: null };
        }
        if (node?.operation === "light") {
          const overrides = Array.isArray(node.lightOverrides)
            ? (node.lightOverrides as Record<string, unknown>[]).map((o) => ({
                lightPath: o?.lightPath ?? "",
                lightType: o?.lightType ?? "",
                mode: o?.mode ?? "",
              }))
            : [];
          return { ...node, lightOverrides: overrides, scale: 1, lightIntensityScale: 1 };
        }
        return node;
      });
    }
    return JSON.stringify(parsed);
  } catch {
    return graph;
  }
}

export function previewTarget(project: StoredProject, id: string, mode: "handoff"|"published"|"input" = "published") {
  const node = project.nodes.find(n=>n.id===id);
  if(!node) return {path:"",graph:undefined,title:"Select a node",message:"Choose a node to preview its USD scene.",label:""};
  const input = mode === "input";
  const app = node.data.operation === "application";
  if(app && mode === "handoff") {
    const dependency=project.dependencies.find(d=>d.id===node.data.handoff?.dependencyId);
    return dependency ? {path:dependency.path,graph:undefined,title:"",message:"",label:`${node.data.label} · Handoff USD`} : {
      path:"",graph:undefined,title:"No handoff USD yet",message:"Choose Create handoff USD in the inspector to prepare a file for the application, or preview the incoming scene before conversion.",label:`${node.data.label} · Handoff USD`};
  }
  if(app && !input && !node.data.liveAdjustment && !node.data.mdBridgeDependencyId) return {
    path:"",graph:undefined,title:"No saved live adjustment",
    message:"This node has no live adjustment in the project JSON. Review edited USD, Apply reviewed edits, then save the project. Old published.usda links are not used.",label:`${node.data.label} · Published output`};
  const hasInputs = project.edges.some(e=>e.target===id) || !!node.data.inputDependencyId || !!node.data.metahumanBodyDependencyId;
  if(node.data.operation === "metahuman" && !node.data.inputDependencyId && !node.data.metahumanBodyDependencyId) return {
    path:"",graph:undefined,title:"No MetaHuman DNA linked",message:"Browse a Head or Body .dna file in the MetaHuman inspector to begin.",label:node.data.label};
  if(app && input && !hasInputs) return {path:"",graph:undefined,title:"No incoming scene",message:"Connect a Source or link an input USD in the inspector.",label:`${node.data.label} · Incoming scene`};
  if(!app && node.data.operation === "source" && !project.edges.some(e=>e.target===id)) {
    const source=project.dependencies.find(d=>d.id===node.data.inputDependencyId);
    if (!source) {
      return {path:"",graph:undefined,title:"No source USD linked",message:"Browse a USD stage in the inspector to begin.",label:node.data.label};
    }
  }
  if(!app && node.data.operation === "blender") {
    const hasIncoming = project.edges.some(e=>e.target===id);
    const hasBlendOrUsd = Boolean(
      node.data.nativeProjectDependencyId ||
      node.data.publishedDependencyId ||
      node.data.inputDependencyId
    );
    if (!hasBlendOrUsd && !hasIncoming) {
      return {path:"",graph:undefined,title:"No Blender file linked",message:"Choose a .blend project in the node inspector to preview.",label:node.data.label};
    }
    if (!hasIncoming && Array.isArray(node.data.blenderSelections) && node.data.blenderSelections.length === 0) {
      return {path:"",graph:undefined,title:"No Blender objects selected",message:"Select one or more objects or collections in the Blender inspector to preview.",label:node.data.label};
    }
  }
  const graph = graphPreview(project,id,app && input);
  return {path:`graph://${id}/${app && input ? "input" : "output"}`,graph,title:"",message:"",label:`${node.data.label} · ${app && input ? "Incoming scene (before application edits)" : "Published output"}`};
}

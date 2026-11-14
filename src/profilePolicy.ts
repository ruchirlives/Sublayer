import type {ApplicationCatalog,ApplicationProfile} from "./applicationProfiles";
export const editCategories=["transforms","points","topology","uvs","materials","animation","skeletons","deletions"] as const;
export function scopeCapability(profile:ApplicationProfile|undefined,key:string){
 if(key==="deletions")return {enabled:false,status:"unsupported",reason:"Use a Delete node. Application exports preserve missing prims."};
 const capability=profile?.capabilities[key];
 return {enabled:capability?.status==="supported",status:capability?.status || "unknown",reason:capability?.reason || "Application capability is unknown"};
}
export function defaultEditScope(profile:ApplicationProfile|undefined){return editCategories.filter(key=>scopeCapability(profile,key).enabled);}
export function preparationProfileSignature(profile:ApplicationProfile|undefined){if(!profile)return "";const {executable,revision,...contract}=profile;return JSON.stringify(contract);}
export function updateApplicationProfile(catalog:ApplicationCatalog,baseline:ApplicationCatalog,id:string,patch:Partial<ApplicationProfile>){
 const next=structuredClone(catalog),index=next.applications.findIndex(p=>p.id===id);
 if(index<0)throw new Error("Application profile no longer exists");
 const previous=next.applications[index],saved=baseline.applications.find(p=>p.id===id);
 const updated={...previous,...patch};
 if(saved && !("revision" in patch) && updated.revision<=saved.revision)updated.revision=saved.revision+1;
 if((patch.applicationVersion && patch.applicationVersion!==previous.applicationVersion)||(patch.adapterVersion && patch.adapterVersion!==previous.adapterVersion)) {
  updated.capabilities=Object.fromEntries(Object.keys(previous.capabilities).map(key=>[key,{status:"unverified" as const,reason:"Application/adapter version changed. Verify this USD edit category before enabling it."}]));
 }
 next.applications[index]=updated;return next;
}
export function parseApplicationCatalog(text:string):ApplicationCatalog {
 const value=JSON.parse(text);
 if(value.version!==1 || !Array.isArray(value.applications))throw new Error("Expected catalog version 1 and applications array");
 for(const p of value.applications){
  if(!p || ["id","app","applicationVersion","upAxis","executable","importBehavior","exportBehavior"].some(key=>typeof p[key]!=="string") || ["revision","adapterVersion","metersPerUnit","scale"].some(key=>typeof p[key]!=="number") || !p.capabilities || editCategories.some(key=>!p.capabilities[key] || typeof p.capabilities[key].reason!=="string" || !["supported","unsupported","unverified"].includes(p.capabilities[key].status)))throw new Error("Each application needs all profile fields and capability status/reason records. Correct the full JSON below.");
 }
 return value;
}

export function addMissingProfiles(catalog:ApplicationCatalog,bundled:ApplicationCatalog):ApplicationCatalog {
 const ids=new Set(catalog.applications.map(p=>p.id));
 return {...catalog,applications:[...catalog.applications,...structuredClone(bundled.applications.filter(p=>!ids.has(p.id)))]};
}

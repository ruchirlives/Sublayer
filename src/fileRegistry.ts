import type {PolicySnapshot} from "./applicationProfiles";
import type { Node, Edge } from "@xyflow/react";
export type DependencyKind = "usd" | "nativeProject" | "asset" | "executable";
export type UsdReference = { sourceDependencyId: string; path: string };
export type Dependency = {
  id: string;
  kind: DependencyKind;
  path: string;
  usdReferences?: UsdReference[];
};
export type Transform = {
  translation: [number, number, number];
  rotation: [number, number, number];
  scale: [number, number, number];
};
export const identityTransform: Transform = {
  translation: [0, 0, 0],
  rotation: [0, 0, 0],
  scale: [1, 1, 1],
};
export type AssetOverride = { originalPath: string; replacementDependencyId: string };
export type CoordinateDraft = {version: number; app: string; upAxis: string; metersPerUnit: number; scale: number; factor: number; forward: number[][]; inverse: number[][]; inputRevision: string; graphRevision: string; usdVersion: string;};
export type HandoffResult = {fileSizeBytes?:number;selection?:string[]|null;inputEdges?:Record<string,string>;policy:PolicySnapshot;path:string; layerPaths:string[]; draft:CoordinateDraft};
export type Handoff = {selection?:string[]|null;inputEdges?:Record<string,string>;policy:PolicySnapshot;dependencyId:string; layerDependencyIds:string[]; conversion:CoordinateDraft};
export type NativeCreationResult = {baselinePath?:string|null;policy:PolicySnapshot;path:string;handoffDependencyId:string;conversion:CoordinateDraft;report:Record<string,unknown>};
export type ManualBaselineResult = {baselinePath:string;nativeDependencyId:string|null;handoffDependencyId:string;policy:PolicySnapshot;conversion:CoordinateDraft;report:Record<string,unknown>};
export type NativeImport = {baselineDependencyId:string;policy:PolicySnapshot;nativeDependencyId:string|null;handoffDependencyId:string;conversion:CoordinateDraft;report:Record<string,unknown>};
export type Correspondence={baselinePath:string;inputPath:string;editedPath:string;pointTransform?:number[][]};
export type Reconciliation={inputDependencyId:string;layerDependencyIds:string[];graphRevision:string};
export type LiveAdjustmentResult={correspondence?:Correspondence[];revision:string;layer:string;guards:Record<string,unknown>;fileRevisions:Record<string,unknown>[];report:Record<string,unknown>;policy:PolicySnapshot;conversion?:CoordinateDraft|null;baselineDependencyId:string;handoffDependencyId:string};
export type LiveAdjustment=LiveAdjustmentResult&{editedDependencyId:string};
export type PublicationResult={publicationRevision?:string;replacesPublicationDependencyId?:string;reconciliation?:{inputPath:string;layerPaths:string[];graphRevision:string};baselineDependencyId:string;handoffDependencyId:string;policy:PolicySnapshot;conversion?:CoordinateDraft|null;path:string;editLayerPath:string;report:Record<string,unknown>;sourceRevisions:Record<string,unknown>[];correspondence:Correspondence[]};
export type Publication={revision?:string;reconciliation?:Reconciliation;dependencyId:string;editLayerDependencyId:string;editedDependencyId:string;baselineDependencyId:string;handoffDependencyId:string;policy:PolicySnapshot;conversion?:CoordinateDraft|null;report:Record<string,unknown>;sourceRevisions:Record<string,unknown>[];correspondence:Correspondence[]};
export type ProceduralNodeKind =
  | "uv"
  | "place2d"
  | "position"
  | "place3d"
  | "triplanar"
  | "noise2d"
  | "noise3d"
  | "fractal3d"
  | "worley2d"
  | "worley3d"
  | "cell2d"
  | "cell3d"
  | "unified2d"
  | "unified3d"
  | "checkerboard"
  | "grid"
  | "tiledcircles"
  | "tiledhexagons"
  | "ramplr"
  | "ramptb"
  | "mix"
  | "multiply"
  | "add"
  | "remap"
  | "clamp"
  | "contrast"
  | "saturate"
  | "hsvadjust"
  | "bump"
  | "colorramp"
  | "output";

export type ProceduralMappingType = "replace" | "multiply" | "add" | "mix" | "bump_normal";

export type ProceduralGraphNodeData = {
  kind: ProceduralNodeKind;
  label?: string;
  params?: Record<string, unknown>;
  // Output node fields
  materialXNodeId?: string;
  ruleId?: string;
  parameter?: string;
  mappingType?: ProceduralMappingType;
  strength?: number;
  enabled?: boolean;
};

export type ProceduralGraphNode = {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: ProceduralGraphNodeData;
};

export type ProceduralGraphEdge = {
  id: string;
  source: string;
  sourceHandle?: string | null;
  target: string;
  targetHandle?: string | null;
};

export type ProceduralGraph = {
  nodes: ProceduralGraphNode[];
  edges: ProceduralGraphEdge[];
};

export type ProceduralConfig = {
  type: "none" | "noise2d" | "noise3d" | "fractal3d" | "worley2d" | "ramp";
  space: "uv" | "object";
  scale: [number, number];
  amplitude: number;
  octaves?: number;
  color1?: [number, number, number];
  color2?: [number, number, number];
};
export type MaterialXSettings = {
  name: string;
  baseColor: [number, number, number];
  metalness: number;
  roughness: number;
  specular: number;
  opacity: number;
  baseWeight?: number;
  specularColor?: [number, number, number];
  specularIor?: number;
  transmission?: number;
  transmissionColor?: [number, number, number];
  emission?: number;
  emissionColor?: [number, number, number];
  coat?: number;
  coatRoughness?: number;
  coatColor?: [number, number, number];
  sheen?: number;
  sheenRoughness?: number;
  sheenColor?: [number, number, number];
  subsurface?: number;
  subsurfaceColor?: [number, number, number];
  subsurfaceScale?: number;
  subsurfaceRadius?: [number, number, number];
  subsurfaceAnisotropy?: number;
  bindingTargets: string[];
  baseColorTextureDependencyId: string | null;
  roughnessTextureDependencyId: string | null;
  metallicTextureDependencyId?: string | null;
  emissionTextureDependencyId?: string | null;
  normalTextureDependencyId?: string | null;
  opacityTextureDependencyId?: string | null;
  transmissionTextureDependencyId?: string | null;
  proceduralBaseColor?: ProceduralConfig | null;
  proceduralRoughness?: ProceduralConfig | null;
  uvMode?: "auto" | "root" | "strand" | null;
};
export type MaterialBindingRule = {
  id: string;
  enabled: boolean;
  targetQuery: string;
  materialPath: string;
  strength: "strongerThanDescendants" | "weakerThanDescendants";
};

export type TransformTargetRule = {
  id: string;
  enabled: boolean;
  pattern: string;
};

export type CameraWorldTransform = {
  translate: [number, number, number];
  rotate: [number, number, number];
};

export type LightOverride = {
  id: string;
  lightPath: string;
  lightType?: string;
  mode?: "override" | "create";
  enabled: boolean;
  intensity?: number | null;
  exposure?: number | null;
  color?: [number, number, number] | null;
  enableColorTemperature?: boolean | null;
  colorTemperature?: number | null;
  translate?: [number, number, number] | null;
  rotate?: [number, number, number] | null;
  scale?: [number, number, number] | null;
  width?: number | null;
  height?: number | null;
  radius?: number | null;
  angle?: number | null;
};

export type MaterialXRule={id:string;enabled:boolean;mode?:"replace"|"override";overrideParams?:string[];searchType:"simple"|"regex";query:string;material:MaterialXSettings};
export function materialTextures(m?: MaterialXSettings | null): string[] {
  if (!m) return [];
  return [
    m.baseColorTextureDependencyId,
    m.roughnessTextureDependencyId,
    m.metallicTextureDependencyId,
    m.emissionTextureDependencyId,
    m.normalTextureDependencyId,
    m.opacityTextureDependencyId,
    m.transmissionTextureDependencyId,
  ].filter((v): v is string => Boolean(v));
}
export function relinkMaterialTextures(m?: MaterialXSettings | null, id?: string, target?: string): MaterialXSettings | undefined {
  if (!m) return undefined;
  const relink = (v?: string | null) => (v === id ? (target || null) : v);
  return {
    ...m,
    baseColorTextureDependencyId: relink(m.baseColorTextureDependencyId) ?? null,
    roughnessTextureDependencyId: relink(m.roughnessTextureDependencyId) ?? null,
    metallicTextureDependencyId: relink(m.metallicTextureDependencyId),
    emissionTextureDependencyId: relink(m.emissionTextureDependencyId),
    normalTextureDependencyId: relink(m.normalTextureDependencyId),
    opacityTextureDependencyId: relink(m.opacityTextureDependencyId),
    transmissionTextureDependencyId: relink(m.transmissionTextureDependencyId),
  };
}

export const defaultMaterialX: MaterialXSettings = {
  name: "OrchFlowMaterial",
  baseColor: [0.7, 0.35, 0.15],
  metalness: 0,
  roughness: 0.4,
  specular: 0.5,
  opacity: 1,
  baseWeight: 1,
  specularColor: [1, 1, 1],
  specularIor: 1.5,
  transmission: 0,
  transmissionColor: [1, 1, 1],
  emission: 0,
  emissionColor: [1, 1, 1],
  coat: 0,
  coatRoughness: 0.1,
  coatColor: [1, 1, 1],
  sheen: 0,
  sheenRoughness: 0.3,
  sheenColor: [1, 1, 1],
  subsurface: 0,
  subsurfaceColor: [1, 1, 1],
  subsurfaceScale: 1,
  subsurfaceRadius: [1, 1, 1],
  subsurfaceAnisotropy: 0,
  bindingTargets: ["*"],
  baseColorTextureDependencyId: null,
  roughnessTextureDependencyId: null,
  metallicTextureDependencyId: null,
  emissionTextureDependencyId: null,
  normalTextureDependencyId: null,
  opacityTextureDependencyId: null,
  transmissionTextureDependencyId: null,
  proceduralBaseColor: null,
  proceduralRoughness: null,
  uvMode: null,
};
export type BlenderObjectInfo = {
  name: string;
  type?: string;
  vertices?: number;
  polygons?: number;
  collections?: string[];
  scene?: string;
  sceneVisible?: boolean;
  activeScene?: boolean;
};

export type HdriSettings = {
  textureDependencyId?: string | null;
  intensity: number;
  exposure: number;
  rotation: number;
  color: [number, number, number];
  textureFormat: "latlong" | "automatic" | "angular" | "mirroredBall";
};

export type HairGroomDelta = {
  pointCount: number;
  curveCount: number;
  deltasZB64: string;
  pointIndicesZB64?: string;
  countsZB64?: string;
  pointsZB64?: string;
  widthsZB64?: string;
};

export type HairSettings = {
  targetQuery: string;
  widthMode: "multiply" | "set";
  width: number;
  taper: number;
  rootScale: number;
  simEnabled: boolean;
  simMode: "static" | "animated";
  settleSteps: number;
  colliderQuery: string;
  startFrame: number;
  endFrame: number;
  substeps: number;
  iterations: number;
  stiffness: number;
  bendStiffness: number;
  damping: number;
  gravity: number;
  wind: [number, number, number];
  collisionMargin: number;
  friction: number;
  simScale?: number;
  groomDeltas?: Record<string, HairGroomDelta>;
};

export const defaultHairSettings: HairSettings = {
  targetQuery: "",
  widthMode: "multiply",
  width: 1.0,
  taper: 0.0,
  rootScale: 1.0,
  simEnabled: false,
  simMode: "static",
  settleSteps: 24,
  colliderQuery: "__none__",
  startFrame: 1,
  endFrame: 48,
  substeps: 4,
  iterations: 8,
  stiffness: 0.95,
  bendStiffness: 0.4,
  damping: 0.04,
  gravity: 9.81,
  wind: [0, 0, 0],
  collisionMargin: 0.005,
  friction: 0.25,
  simScale: 1.0,
};

export type CacheEditDelta = {
  count: number;
  indicesZB64?: string;
  deltasZB64: string;
};

export type CacheEditMask = {
  count: number;
  weightsZB64: string;
};

export type CacheEditLayerMode = "allFrames" | "frameRange" | "singleFrame";
export type CacheEditFalloff = "smoothstep" | "gaussian" | "linear" | "constant";

export type CacheEditLayer = {
  id: string;
  name: string;
  enabled: boolean;
  weight: number;
  mode: CacheEditLayerMode;
  centerFrame: number;
  fadeIn: number;
  fadeOut: number;
  falloff: CacheEditFalloff;
  deltas?: Record<string, CacheEditDelta>;
};

export type CacheEditSimMode = "animated" | "static";
export type CacheEditFabricPreset = "custom" | "silk" | "cotton" | "denim" | "wool" | "leather";

export type CacheEditSettings = {
  targetQuery: string;
  colliderQuery: string;
  activeLayerId: string;
  temporalSmoothFrames: number;
  temporalSmoothStrength: number;
  clothSubsteps: number;
  clothIterations: number;
  clothStretchStiffness: number;
  clothBendStiffness: number;
  clothDamping: number;
  clothGravity: number;
  clothCollisionMargin: number;
  clothFriction: number;
  clothDragPinFalloff: number;
  clothSimScale?: number;
  simEnabled?: boolean;
  simMode?: CacheEditSimMode;
  simStartFrame?: number;
  simEndFrame?: number;
  simFps?: number;
  simSettleSteps?: number;
  simFabricPreset?: CacheEditFabricPreset;
  simAirDrag?: number;
  simWind?: [number, number, number];
  simSelfCollision?: boolean;
  simSelfThickness?: number;
  simPinToCollider?: boolean;
  simCacheVersion?: number;
  maskWeights?: Record<string, CacheEditMask>;
  layers: CacheEditLayer[];
};

export function createCacheEditLayer(index: number, centerFrame = 0): CacheEditLayer {
  return {
    id: `layer-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
    name: `Sculpt Layer ${index}`,
    enabled: true,
    weight: 1.0,
    mode: "frameRange",
    centerFrame,
    fadeIn: 8,
    fadeOut: 8,
    falloff: "smoothstep",
  };
}

export const defaultCacheEditSettings: CacheEditSettings = {
  targetQuery: "",
  colliderQuery: "__none__",
  activeLayerId: "layer-1",
  temporalSmoothFrames: 0,
  temporalSmoothStrength: 0.0,
  clothSubsteps: 4,
  clothIterations: 8,
  clothStretchStiffness: 0.95,
  clothBendStiffness: 0.35,
  clothDamping: 0.08,
  clothGravity: 9.81,
  clothCollisionMargin: 0.004,
  clothFriction: 0.35,
  clothDragPinFalloff: 1.4,
  clothSimScale: 1.0,
  simEnabled: false,
  simMode: "animated",
  simStartFrame: 0,
  simEndFrame: 48,
  simFps: 24,
  simSettleSteps: 12,
  simFabricPreset: "custom",
  simAirDrag: 0.05,
  simWind: [0, 0, 0],
  simSelfCollision: true,
  simSelfThickness: 0.004,
  simPinToCollider: true,
  simCacheVersion: 0,
  layers: [
    {
      id: "layer-1",
      name: "Sculpt Layer 1",
      enabled: true,
      weight: 1.0,
      mode: "frameRange",
      centerFrame: 0,
      fadeIn: 8,
      fadeOut: 8,
      falloff: "smoothstep",
    },
  ],
};

export type CharacterJointAttachment = {
  id: string;
  enabled: boolean;
  targetQuery: string;
  skeletonPath: string;
  jointName: string;
  offsetTranslation: [number, number, number];
  offsetRotation: [number, number, number];
  offsetScale: [number, number, number];
};

export type CharacterSkeletonBinding = {
  id: string;
  enabled: boolean;
  targetQuery: string;
  skeletonPath: string;
  deactivateSourceSkeletons: boolean;
};

export type CharacterAnimationOverride = {
  id: string;
  enabled: boolean;
  skeletonPath: string;
  animationPath: string;
  mode: "pose" | "clip";
  poseTime: number;
};

export type CharacterClothCache = {
  id: string;
  enabled: boolean;
  dependencyId: string | null;
  attachToRoot: boolean;
  axis: string;
  frontAxis: string;
  scale: number;
  timeOffset: number;
};

export type CharacterHeadGroom = {
  id: string;
  enabled: boolean;
  sourceMode: "file" | "upstream";
  dependencyId: string | null;
  targetQuery: string;
  attachMode: "joint" | "surface";
  skeletonPath: string;
  headJointName: string;
  preserveRestPosition: boolean;
  headMeshPath: string;
  axis: string;
  frontAxis: string;
  scale: number;
  offsetTranslation: [number, number, number];
  offsetRotation: [number, number, number];
  offsetScale: [number, number, number];
};

export type PoseJoint = { name:string; translation:[number,number,number]; rotation:[number,number,number,number]; scale:[number,number,number]; rotationOffset:[number,number,number]; translationOffset:[number,number,number] };
export type PoseEntry = { id: string; name: string; dependencyId?: string | null; animationPath: string; mode: "pose" | "clip"; poseTime: number; joints?: PoseJoint[]; blendShapes?:string[]; blendShapeWeights?:number[] };
export type PoseSettings = { mappings: {id: string; enabled?: boolean; skeletonPath: string; entryId: string | null}[]; entries: PoseEntry[] };
export type CharacterSettings = {
  baseSkeletonPath: string;
  animationOverrides: CharacterAnimationOverride[];
  jointAttachments: CharacterJointAttachment[];
  skeletonBindings: CharacterSkeletonBinding[];
  clothCaches: CharacterClothCache[];
  headGrooms: CharacterHeadGroom[];
};

export const defaultCharacterSettings: CharacterSettings = {
  baseSkeletonPath: "",
  animationOverrides: [],
  jointAttachments: [],
  skeletonBindings: [],
  clothCaches: [],
  headGrooms: [],
};

export type ScatterPrototypeRule = {
  id: string;
  enabled: boolean;
  primPath: string;
  weight?: number;
};

export type ScatterSettings = {
  count: number;
  seed: number;
  distribution: "random" | "grid" | "circle" | "surface";
  areaSize: [number, number, number];
  areaOffset?: [number, number, number];
  gridJitter?: number;
  innerRadiusRatio?: number;
  circleAlign?: "none" | "outward" | "inward" | "tangent";
  circleEvenSpacing?: boolean;
  scaleUniform?: boolean;
  scaleMin: number;
  scaleMax: number;
  scaleMinVec?: [number, number, number];
  scaleMaxVec?: [number, number, number];
  randomYaw: boolean;
  rotationOffset?: [number, number, number];
  rotationMin?: [number, number, number] | null;
  rotationMax?: [number, number, number] | null;
  passThrough?: boolean;
  surfacePrimPath?: string;
  surfaceOffset?: number;
  alignToNormal?: boolean;
  alignStrength?: number;
  minSlopeDeg?: number;
  maxSlopeDeg?: number;
  heightMin?: number | null;
  heightMax?: number | null;
  minSpacing?: number;
  colorVariationEnabled?: boolean;
  colorVariationMode?: "hsv" | "ramp";
  colorTintBase?: [number, number, number];
  colorHueJitter?: number;
  colorSatJitter?: number;
  colorValJitter?: number;
  colorRampA?: [number, number, number];
  colorRampB?: [number, number, number];
  colorApplyToMaterials?: boolean;
  prototypes?: ScatterPrototypeRule[];
};
export const defaultScatter: ScatterSettings = {
  count: 100,
  seed: 42,
  distribution: "random",
  areaSize: [20, 0, 20],
  areaOffset: [0, 0, 0],
  gridJitter: 0,
  innerRadiusRatio: 0,
  circleAlign: "none",
  circleEvenSpacing: false,
  scaleUniform: true,
  scaleMin: 0.8,
  scaleMax: 1.2,
  scaleMinVec: [0.8, 0.8, 0.8],
  scaleMaxVec: [1.2, 1.2, 1.2],
  randomYaw: true,
  rotationOffset: [0, 0, 0],
  rotationMin: [0, 0, 0],
  rotationMax: [0, 360, 0],
  passThrough: false,
  surfacePrimPath: "",
  surfaceOffset: 0,
  alignToNormal: false,
  alignStrength: 1,
  minSlopeDeg: 0,
  maxSlopeDeg: 90,
  heightMin: null,
  heightMax: null,
  minSpacing: 0,
  colorVariationEnabled: false,
  colorVariationMode: "hsv",
  colorTintBase: [1, 1, 1],
  colorHueJitter: 15,
  colorSatJitter: 0.15,
  colorValJitter: 0.15,
  colorRampA: [0.85, 0.95, 0.75],
  colorRampB: [0.95, 0.75, 0.55],
  colorApplyToMaterials: true,
  prototypes: [],
};

export type PostFxSettings = {
  tonemapOperator: "raw" | "none" | "reinhard" | "modifiedReinhard" | "hejiHableAlu" | "hableUc2" | "acesApproximation" | "iray";
  tonemapColorMode: "sRGBLinear" | "ACEScg";
  exposureKey: number;
  whiteScale: number;
  maxWhiteLuminance: number;
  whitepoint: [number, number, number];
  dither: number;
  irayBurnHighlights: number;
  irayCrushBlacks: number;
  iraySaturation: number;

  gradeEnabled: boolean;
  gradeBlackPoint: [number, number, number];
  gradeWhitePoint: [number, number, number];
  gradeLift: [number, number, number];
  gradeGain: [number, number, number];
  gradeMultiply: [number, number, number];
  gradeOffset: [number, number, number];
  gradeGamma: [number, number, number];
  gradeContrast: [number, number, number];
  gradeSaturation: [number, number, number];

  autoExposureEnabled: boolean;
  autoExposureWhitePointScale: number;
  autoExposureAdaptationSpeed: number;
  autoExposureHistogramFilter: "median" | "average";
  autoExposureMinLuminance: number;
  autoExposureMaxLuminance: number;

  bloomEnabled: boolean;
  bloomScale: number;
  bloomCutoff: number;
  bloomCutoffFuzziness: number;
  bloomEnergyConservingBlend: boolean;
  bloomFStop: number;
  bloomFocalLength: number;
  bloomBlades: number;
  bloomApertureCircular: boolean;
  bloomApertureRotation: number;
  bloomAnisoFlareWeight: number;
  bloomHaloFlareWeight: number;
  bloomIsotropicFlareWeight: number;

  chromabEnabled: boolean;
  chromabStrengthR: number;
  chromabStrengthG: number;
  chromabStrengthB: number;
  chromabUseLanczos: boolean;

  vignetteEnabled: boolean;
  vignetteStrength: number;
  vignetteSize: number;
  filmGrainEnabled: boolean;
  filmGrainAmount: number;
  filmGrainSize: number;

  sharpness: number;
};

export const defaultPostFx: PostFxSettings = {
  tonemapOperator: "acesApproximation",
  tonemapColorMode: "sRGBLinear",
  exposureKey: 0.25,
  whiteScale: 40.2,
  maxWhiteLuminance: 10.0,
  whitepoint: [1.0, 1.0, 1.0],
  dither: 0.0,
  irayBurnHighlights: 0.7,
  irayCrushBlacks: 0.5,
  iraySaturation: 1.0,

  gradeEnabled: false,
  gradeBlackPoint: [0.0, 0.0, 0.0],
  gradeWhitePoint: [1.0, 1.0, 1.0],
  gradeLift: [0.0, 0.0, 0.0],
  gradeGain: [1.0, 1.0, 1.0],
  gradeMultiply: [1.0, 1.0, 1.0],
  gradeOffset: [0.0, 0.0, 0.0],
  gradeGamma: [1.0, 1.0, 1.0],
  gradeContrast: [1.0, 1.0, 1.0],
  gradeSaturation: [1.0, 1.0, 1.0],

  autoExposureEnabled: false,
  autoExposureWhitePointScale: 10.0,
  autoExposureAdaptationSpeed: 3.5,
  autoExposureHistogramFilter: "median",
  autoExposureMinLuminance: 50.0,
  autoExposureMaxLuminance: 100000.0,

  bloomEnabled: false,
  bloomScale: 1.0,
  bloomCutoff: 0.0,
  bloomCutoffFuzziness: 0.5,
  bloomEnergyConservingBlend: false,
  bloomFStop: 5.0,
  bloomFocalLength: 35.0,
  bloomBlades: 5,
  bloomApertureCircular: false,
  bloomApertureRotation: 5.0,
  bloomAnisoFlareWeight: 0.6,
  bloomHaloFlareWeight: 0.01,
  bloomIsotropicFlareWeight: 0.4,

  chromabEnabled: false,
  chromabStrengthR: -0.055,
  chromabStrengthG: -0.075,
  chromabStrengthB: 0.015,
  chromabUseLanczos: false,

  vignetteEnabled: false,
  vignetteStrength: 0.7,
  vignetteSize: 107.0,
  filmGrainEnabled: false,
  filmGrainAmount: 0.05,
  filmGrainSize: 1.6,

  sharpness: 0.5,
};

export type InstancerInstance = {
  id: string;
  sourcePath: string;
  visible: boolean;
  transform: Transform;
};
export type InstancerSettings = {
  movement: "ground" | "free" | "x" | "y" | "z" | "custom";
  lockedAxes: [boolean, boolean, boolean];
  instances: InstancerInstance[];
};
export const defaultInstancer: InstancerSettings = { movement: "ground", lockedAxes: [false, false, false], instances: [] };

export type FogSettings = {
  enabled: boolean; atmosphereEnabled: boolean; volumesEnabled: boolean;
  color: [number, number, number]; intensity: number; distanceDensity: number;
  startDistance: number; endDistance: number; height: number; heightDensity: number; heightFalloff: number;
  volumePaths: string[]; volumeAutoBounds: boolean; volumeCenter: [number, number, number]; volumeSize: [number, number, number]; densityScale: number; volumeColor: [number, number, number]; anisotropy: number;
};
export const defaultFog: FogSettings = {
  enabled: true, atmosphereEnabled: true, volumesEnabled: true,
  color: [0.75, 0.75, 0.75], intensity: 1, distanceDensity: 0.1,
  startDistance: 0, endDistance: 100, height: 0, heightDensity: 0.1, heightFalloff: 1,
  volumePaths: [], volumeAutoBounds: true, volumeCenter: [0, 0, 0], volumeSize: [2, 2, 2], densityScale: 1, volumeColor: [0.9, 0.9, 0.9], anisotropy: 0,
};

export type Data = {
  blenderSelections?: string[];
  blenderScene?: string | null;
  blenderObjects?: BlenderObjectInfo[];
  blenderMaterialMode?: "off" | "auto" | "octane" | "cycles";
  blenderConvertProcedurals?: boolean;
  blenderExportSkeletons?: boolean;
  materialX?:MaterialXSettings;
  materialXRules?:MaterialXRule[];
  materialBindings?: MaterialBindingRule[];
  transformTargets?: TransformTargetRule[];
  lightOverrides?: LightOverride[];
  lightIntensityScale?: number;
  hdri?: HdriSettings;
  postFx?: PostFxSettings;
  fog?: FogSettings;
  hair?: HairSettings;
  cacheEdit?: CacheEditSettings;
  metahumanLod?: number;
  metahumanBodyPath?: string;
  metahumanMaterials?: boolean;
  megascansLod?: number;
  megascansResolution?: string;
  megascansNormalConvention?: string;
  character?: CharacterSettings | null;
  pose?: PoseSettings | null;
  camera?: ViewportCamera | null;
  cameraViews?: CameraView[];
  publicationPolicy?:PolicySnapshot | null;
 publication?:Publication | null;
  applicationProfileId?:string | null;
  editScope?:string[];
  liveAdjustment?:LiveAdjustment|null;
  nativeImport?: NativeImport | null;
  manualBaseline?: NativeImport | null;
  nativePreparation?:Omit<NativeImport,"baselineDependencyId">|null;
  deletePaths?:string[];
  scatter?: ScatterSettings | null;
  instancer?: InstancerSettings | null;
  groupName?: string;
  loadPayloads?: boolean;
  useAsPayload?: boolean;
  handoff?: Handoff | null;
  handoffSelection?:string[]|null;
  applicationMetersPerUnit?: number | null;
  coordinateDraft?: CoordinateDraft | null;
  assetOverrides?: AssetOverride[];
  label: string;
  path: string;
  operation: string;
  app: string;
  axis: string;
  frontAxis?: string;
  scale: number;
  appExecutable?: string;
  nativeProject: string;
  mdBridgePath?: string;
  publishedPath: string;
  assetDependencyIds?: string[];
  transform?: Transform;
  instanced?: boolean;
};
export type StageNode = Node<Data, "stage">;
export type StoredData = Omit<
  Data,
  "path" | "metahumanBodyPath" | "nativeProject" | "mdBridgePath" | "publishedPath" | "appExecutable"
> & {
  appExecutableDependencyId?: string | null;
  inputDependencyId: string | null;
  metahumanBodyDependencyId?: string | null;
  nativeProjectDependencyId: string | null;
  mdBridgeDependencyId?: string | null;
  publishedDependencyId: string | null;
};
export type CameraProjection = "perspective" | "orthographic";

export type ViewportCamera = {
  yaw: number;
  pitch: number;
  zoom: number;
  panX: number;
  panY: number;
  projection?: CameraProjection;
  fov?: number;
  dofEnabled?: boolean;
  focusDistance?: number;
  fStop?: number;
};

export type CameraView = {
  id: string;
  name: string;
  camera: ViewportCamera;
};

export type ViewportSettings = {
  pinnedNodeId: string | null;
  renderer?: string | null;
  ovrtxMode?: "realtime" | "pathTracing" | null;
  upAxis?: "Y" | "Z" | null;
  materials?: "auto" | "scene" | "solid" | null;
  camera?: ViewportCamera | null;
  activeCameraViewId?: string | null;
};
export type StoredProject = {
  version: 2;
  nodes: (Omit<StageNode, "data"> & { data: StoredData })[];
  edges: Edge[];
  dependencies: Dependency[];
  viewport?: ViewportSettings;
  proceduralGraph?: ProceduralGraph | null;
};

/** Windows paths are case-insensitive; canonical filesystem matching happens in Rust. */
export function fileLink(path:string){
 const value=path.replace(/\\/g,"/");
 return value.startsWith("//?/UNC/")?"//"+value.slice(8):value.startsWith("//?/")?value.slice(4):path;
}
export function fileKey(path: string) {
  const normalized = fileLink(path).replace(/\\/g, "/");
  const windows = /^[a-z]:\//i.test(normalized) || normalized.startsWith("//");
  const parts: string[] = [];
  for (const part of normalized.split("/")) {
    if (part === "." || part === "") continue;
    if (
      part === ".." &&
      parts.length &&
      parts[parts.length - 1] !== ".." &&
      !parts[parts.length - 1]?.endsWith(":")
    )
      parts.pop();
    else parts.push(part);
  }
  const key =
    (normalized.startsWith("//")
      ? "//"
      : normalized.startsWith("/")
        ? "/"
        : "") + parts.join("/");
  return windows ? key.toLowerCase() : key;
}
export function registerFiles(
  existing: Dependency[],
  paths: string[],
  kind: DependencyKind,
  makeId = () => crypto.randomUUID(),
): { dependencies: Dependency[]; ids: string[]; reused: string[] } {
  const dependencies = existing.map(d=>({...d,path:fileLink(d.path)}));
  const ids: string[] = [],
    reused: string[] = [];
  for (const rawPath of paths) {
    const path=fileLink(rawPath);
    if (!path.trim()) continue;
    let dependency = dependencies.find(
      (d) => fileKey(d.path) === fileKey(path),
    );
    if (dependency) reused.push(dependency.id);
    else {
      dependency = { id: makeId(), kind, path };
      dependencies.push(dependency);
    }
    ids.push(dependency.id);
  }
  return { dependencies, ids: [...new Set(ids)], reused: [...new Set(reused)] };
}
export function storeProject(
  nodes: StageNode[],
  edges: Edge[],
  existing: Dependency[],
  makeId = () => crypto.randomUUID(),
): StoredProject {
  let dependencies = [...existing];
  function register(path: string, kind: DependencyKind) {
    const result = registerFiles(dependencies, [path], kind, makeId);
    dependencies = result.dependencies;
    return result.ids[0] || null;
  }
  const stored = nodes.map((node) => {
    const { path, metahumanBodyPath, nativeProject, mdBridgePath, publishedPath, appExecutable, ...data } = node.data;
    return {
      ...node,
      data: {
        ...data,
        assetDependencyIds: data.assetDependencyIds || [],
        inputDependencyId: register(path, data.operation === "metahuman" ? "asset" : "usd"),
        ...(data.operation === "metahuman" && metahumanBodyPath
          ? { metahumanBodyDependencyId: register(metahumanBodyPath, "asset") }
          : {}),
        appExecutableDependencyId: register(appExecutable || "", "executable"),
        nativeProjectDependencyId: register(nativeProject, "nativeProject"),
        mdBridgeDependencyId: register(mdBridgePath || "", "asset"),
        publishedDependencyId: register(publishedPath, "usd"),
      },
    };
  });
  return { version: 2, nodes: stored, edges, dependencies };
}
export function expandProject(project: StoredProject): {
  nodes: StageNode[];
  edges: Edge[];
} {
  const path = (id: string | null | undefined) =>
    project.dependencies.find((d) => d.id === id)?.path || "";
  return {
    nodes: project.nodes.map((node) => {
      const {
        inputDependencyId,
        metahumanBodyDependencyId,
        appExecutableDependencyId,
        nativeProjectDependencyId,
        mdBridgeDependencyId,
        publishedDependencyId,
        ...data
      } = node.data;
      return {
        ...node,
        data: {
          ...data,
          path: path(inputDependencyId),
          metahumanBodyPath: path(metahumanBodyDependencyId),
          appExecutable: path(appExecutableDependencyId),
          nativeProject: path(nativeProjectDependencyId),
          mdBridgePath: path(mdBridgeDependencyId),
          publishedPath: path(publishedDependencyId),
        },
      };
    }),
    edges: project.edges,
  };
}
export function referencedBy(project: StoredProject, dependencyId: string) {
  const dependency = project.dependencies.find((d) => d.id === dependencyId);
  const origins = new Set([
    dependencyId,
    ...(dependency?.usdReferences || []).map((ref) => ref.sourceDependencyId),
  ]);
  const referenced = new Set(
    project.nodes
      .filter((n) =>
        [
          ...(n.data.pose?.entries || []).map(e => e.dependencyId),
          ...(n.data.character?.clothCaches || []).map(c => c.dependencyId),
          ...(n.data.character?.headGrooms || []).filter(g => g.sourceMode === "file").map(g => g.dependencyId),
          ...materialTextures(n.data.materialX),
          ...(n.data.materialXRules || []).flatMap(r => materialTextures(r.material)),
              n.data.inputDependencyId,
              n.data.metahumanBodyDependencyId,
          n.data.nativeProjectDependencyId,
          n.data.appExecutableDependencyId,
          n.data.publishedDependencyId,
          ...(n.data.assetDependencyIds || []),
          ...(n.data.assetOverrides || []).map(o => o.replacementDependencyId),
          n.data.handoff?.dependencyId, ...(n.data.handoff?.layerDependencyIds || []),
          n.data.liveAdjustment?.editedDependencyId,n.data.liveAdjustment?.baselineDependencyId,n.data.liveAdjustment?.handoffDependencyId,
          n.data.nativePreparation?.nativeDependencyId,n.data.nativePreparation?.handoffDependencyId,
          n.data.manualBaseline?.baselineDependencyId,n.data.manualBaseline?.handoffDependencyId,n.data.manualBaseline?.nativeDependencyId,
          n.data.nativeImport?.baselineDependencyId,n.data.nativeImport?.handoffDependencyId,n.data.nativeImport?.nativeDependencyId,
          n.data.publication?.dependencyId,n.data.publication?.editLayerDependencyId,n.data.publication?.editedDependencyId,n.data.publication?.baselineDependencyId,n.data.publication?.handoffDependencyId,n.data.publication?.reconciliation?.inputDependencyId,...(n.data.publication?.reconciliation?.layerDependencyIds || []),
        ].some((id) => id != null && origins.has(id)),
      )
      .map((n) => n.id),
  );
  const outgoing = new Map<string, string[]>();
  for (const edge of project.edges)
    outgoing.set(edge.source, [
      ...(outgoing.get(edge.source) || []),
      edge.target,
    ]);
  const queue = [...referenced];
  while (queue.length) {
    const id = queue.pop()!;
    for (const target of outgoing.get(id) || [])
      if (!referenced.has(target)) {
        referenced.add(target);
        queue.push(target);
      }
  }
  return project.nodes.filter((n) => referenced.has(n.id));
}
export function relinkDependency(
  project: StoredProject,
  id: string,
  replacement: string,
): StoredProject {
  const dependency = project.dependencies.find((d) => d.id === id);
  if (!dependency) throw new Error("Dependency no longer exists");
  const existing = project.dependencies.find(
    (d) => d.id !== id && fileKey(d.path) === fileKey(replacement),
  );
  const target = existing?.id || id;
  return {
    ...project,
    dependencies: (existing
      ? project.dependencies
          .filter((d) => d.id !== id)
          .map((d) =>
            d.id === existing.id
              ? {
                  ...d,
                  usdReferences: uniqueReferences([
                    ...(d.usdReferences || []),
                    ...(dependency.usdReferences || []),
                  ]),
                }
              : d,
          )
      : project.dependencies.map((d) =>
          d.id === id ? { ...d, path: replacement } : d,
        )
    ).map((d) => ({
      ...d,
      usdReferences: uniqueReferences(
        (d.usdReferences || []).map((ref) =>
          ref.sourceDependencyId === id
            ? { ...ref, sourceDependencyId: target }
            : ref,
        ),
      ),
    })),
    nodes: project.nodes.map((n) => ({
      ...n,
      data: {
        ...n.data,
        pose: n.data.pose ? {...n.data.pose, entries:n.data.pose.entries.map(e => ({...e,dependencyId:e.dependencyId===id?target:e.dependencyId}))} : n.data.pose,
        materialXRules: n.data.materialXRules?.map((r) => ({
          ...r,
          material: relinkMaterialTextures(r.material, id, target)!,
        })),
        manualBaseline: n.data.manualBaseline
          ? {
              ...n.data.manualBaseline,
              baselineDependencyId:
                n.data.manualBaseline.baselineDependencyId === id
                  ? target
                  : n.data.manualBaseline.baselineDependencyId,
              nativeDependencyId:
                n.data.manualBaseline.nativeDependencyId === id
                  ? target
                  : n.data.manualBaseline.nativeDependencyId,
              handoffDependencyId:
                n.data.manualBaseline.handoffDependencyId === id
                  ? target
                  : n.data.manualBaseline.handoffDependencyId,
            }
          : n.data.manualBaseline,
        ...(n.data.character
          ? {
              character: {
                ...n.data.character,
                clothCaches: (n.data.character.clothCaches || []).map((cc) =>
                  cc.dependencyId === id ? { ...cc, dependencyId: target } : cc,
                ),
                headGrooms: (n.data.character.headGrooms || []).map((hg) =>
                  hg.dependencyId === id ? { ...hg, dependencyId: target } : hg,
                ),
              },
            }
          : {}),
        materialX: relinkMaterialTextures(n.data.materialX, id, target),
        inputDependencyId:
          n.data.inputDependencyId === id ? target : n.data.inputDependencyId,
        ...(n.data.metahumanBodyDependencyId !== undefined
          ? {
              metahumanBodyDependencyId:
                n.data.metahumanBodyDependencyId === id
                  ? target
                  : n.data.metahumanBodyDependencyId,
            }
          : {}),
        appExecutableDependencyId: n.data.appExecutableDependencyId === id ? target : n.data.appExecutableDependencyId,
        nativeProjectDependencyId:
          n.data.nativeProjectDependencyId === id
            ? target
            : n.data.nativeProjectDependencyId,
        publishedDependencyId:
          n.data.publishedDependencyId === id
            ? target
            : n.data.publishedDependencyId,
        assetDependencyIds: [
          ...new Set(
            (n.data.assetDependencyIds || []).map((asset) =>
              asset === id ? target : asset,
            ),
          ),
        ],
      },
    })),
  };
}

function uniqueReferences(references: UsdReference[]) {
  return [
    ...new Map(
      references.map((ref) => [
        `${ref.sourceDependencyId}\0${fileKey(ref.path)}`,
        ref,
      ]),
    ).values(),
  ];
}
export function mergeUsdDiscovery(
  project: StoredProject,
  sourceId: string,
  files: { path: string; kind: DependencyKind }[],
  makeId = () => crypto.randomUUID(),
): StoredProject {
  if (!project.dependencies.some((d) => d.id === sourceId)) return project;
  const dependencies = project.dependencies.map((d) => ({
    ...d,
    usdReferences: [...(d.usdReferences || [])],
  }));
  const previous = dependencies.map((d) => ({
    ...d,
    usdReferences: [...d.usdReferences],
  }));
  for (const dependency of dependencies)
    dependency.usdReferences = dependency.usdReferences.filter(
      (ref) => ref.sourceDependencyId !== sourceId,
    );
  for (const file of files) {
    if (!file.path.trim()) continue;
    const key = fileKey(file.path);
    // Provenance wins over the old authored path, preserving explicit Relink.
    const old = previous.find((d) =>
      d.usdReferences.some(
        (ref) =>
          ref.sourceDependencyId === sourceId && fileKey(ref.path) === key,
      ),
    );
    let dependency =
      dependencies.find((d) => d.id === old?.id) ||
      dependencies.find((d) => fileKey(d.path) === key);
    if (!dependency) {
      dependency = {
        id: makeId(),
        kind: file.kind,
        path: file.path,
        usdReferences: [],
      };
      dependencies.push(dependency);
    }
    if (dependency.id !== sourceId)
      dependency.usdReferences = uniqueReferences([
        ...dependency.usdReferences,
        { sourceDependencyId: sourceId, path: file.path },
      ]);
  }
  return { ...project, dependencies };
}

/** Inspector edits bind this node; global Relink is the explicit shared edit. */
export function editNodeData(
  project: StoredProject,
  nodeId: string,
  patch: Partial<Data>,
): StoredProject {
  let next = project;
  for (const [view, field, kind] of [
    ["path", "inputDependencyId", (patch.operation ?? project.nodes.find(n => n.id === nodeId)?.data.operation) === "metahuman" ? "asset" : "usd"],
    ["metahumanBodyPath", "metahumanBodyDependencyId", "asset"],
    ["appExecutable", "appExecutableDependencyId", "executable"],
    ["nativeProject", "nativeProjectDependencyId", "nativeProject"],
    ["mdBridgePath", "mdBridgeDependencyId", "asset"],
    ["publishedPath", "publishedDependencyId", "usd"],
  ] as const) {
    if (!(view in patch)) continue;
    const path = patch[view] || "";
    const node = next.nodes.find((n) => n.id === nodeId);
    if (!node) continue;
    const oldId = node.data[field];
    let id: string | null = null;
    if (path.trim()) {
      const match = next.dependencies.find(
        (d) => fileKey(d.path) === fileKey(path),
      );
      if (match) id = match.id;
      else {
        const references = next.nodes.reduce(
          (count, n) =>
            count +
            [
              ...(n.data.pose?.entries || []).map(e => e.dependencyId),
          ...materialTextures(n.data.materialX),
          ...(n.data.materialXRules || []).flatMap(r => materialTextures(r.material)),
              n.data.inputDependencyId,
              n.data.metahumanBodyDependencyId,
              n.data.nativeProjectDependencyId,
              n.data.mdBridgeDependencyId,
          n.data.appExecutableDependencyId,
              n.data.publishedDependencyId,
              ...(n.data.assetDependencyIds || []),
              n.data.handoff?.dependencyId, ...(n.data.handoff?.layerDependencyIds || []),
          n.data.liveAdjustment?.editedDependencyId,n.data.liveAdjustment?.baselineDependencyId,n.data.liveAdjustment?.handoffDependencyId,
          n.data.nativePreparation?.nativeDependencyId,n.data.nativePreparation?.handoffDependencyId,
          n.data.manualBaseline?.baselineDependencyId,n.data.manualBaseline?.handoffDependencyId,n.data.manualBaseline?.nativeDependencyId,
          n.data.nativeImport?.baselineDependencyId,n.data.nativeImport?.handoffDependencyId,n.data.nativeImport?.nativeDependencyId,
          n.data.publication?.dependencyId,n.data.publication?.editLayerDependencyId,n.data.publication?.editedDependencyId,n.data.publication?.baselineDependencyId,n.data.publication?.handoffDependencyId,n.data.publication?.reconciliation?.inputDependencyId,...(n.data.publication?.reconciliation?.layerDependencyIds || []),
            ].filter((ref) => ref === oldId && ref !== null).length,
          0,
        );
        if (oldId && references === 1) {
          id = oldId;
          next = {
            ...next,
            dependencies: next.dependencies.map((d) =>
              d.id === oldId ? { ...d, path, kind } : d,
            ),
          };
        } else {
          const added = registerFiles(next.dependencies, [path], kind);
          id = added.ids[0];
          next = { ...next, dependencies: added.dependencies };
        }
      }
    }
    next = {
      ...next,
      nodes: next.nodes.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, [field]: id } } : n,
      ),
    };
  }
  const {
    appExecutable: _executable,
    path: _path,
    metahumanBodyPath: _bodyPath,
    nativeProject: _native,
    mdBridgePath: _mdBridge,
    publishedPath: _published,
    ...settings
  } = patch;
  return {
    ...next,
    nodes: next.nodes.map((n) =>
      n.id === nodeId ? { ...n, data: { ...n.data, ...settings } } : n,
    ),
  };
}

/** Preview specification excludes canvas/UI state so dragging a node does not
 * reconstruct the USD scene. Original paths are references, never output files.
 */
export function graphPreview(
  project: StoredProject,
  targetNodeId: string,
  applicationInput = false,
): string {
  const needed = new Set<string>();
  const queue = [targetNodeId];
  const incoming = new Map<string, string[]>();
  for (const edge of project.edges)
    incoming.set(edge.target, [
      ...(incoming.get(edge.target) || []),
      edge.source,
    ]);
  while (queue.length) {
    const id = queue.pop()!;
    if (needed.has(id)) continue;
    needed.add(id);
    const node = project.nodes.find(n => n.id === id);
    if (node?.data.operation !== "application" || node?.data.liveAdjustment || node?.data.mdBridgeDependencyId || (id === targetNodeId && applicationInput))
      queue.push(...(incoming.get(id) || []));
  }
  const nodes = project.nodes
    .filter((n) => needed.has(n.id))
    .map((n) => ({
      id: n.id,
      operation: applicationInput && n.id === targetNodeId ? (n.data.inputDependencyId ? "source" : "output") : n.data.operation,
      inputDependencyId: n.data.inputDependencyId,
      ...(n.data.operation === "source"
        ? {
            ...(typeof n.data.megascansLod === "number" ? { megascansLod: n.data.megascansLod } : {}),
            ...(n.data.megascansResolution ? { megascansResolution: n.data.megascansResolution } : {}),
            ...(n.data.megascansNormalConvention
              ? { megascansNormalConvention: n.data.megascansNormalConvention }
              : {}),
          }
        : {}),
      ...(n.data.operation === "metahuman"
        ? {
            metahumanLod: n.data.metahumanLod ?? 0,
            metahumanMaterials: n.data.metahumanMaterials !== false,
            ...(n.data.metahumanBodyDependencyId
              ? { metahumanBodyDependencyId: n.data.metahumanBodyDependencyId }
              : {}),
          }
        : {}),
      publishedDependencyId: applicationInput && n.id === targetNodeId ? null : n.data.publishedDependencyId,
      nativeProjectDependencyId: n.data.nativeProjectDependencyId,
      mdBridgeDependencyId: applicationInput && n.id === targetNodeId ? null : n.data.mdBridgeDependencyId,
      applicationMetersPerUnit: n.data.applicationMetersPerUnit,
      blenderSelections: n.data.blenderSelections,
      blenderScene: n.data.blenderScene,
      blenderMaterialMode: n.data.blenderMaterialMode,
      blenderConvertProcedurals: Boolean(n.data.blenderConvertProcedurals),
      blenderExportSkeletons: Boolean(n.data.blenderExportSkeletons),
      instanced: Boolean(n.data.instanced),
      loadPayloads: n.data.loadPayloads !== false,
      useAsPayload: Boolean(n.data.useAsPayload),
      axis: n.data.axis || "preserve",
      frontAxis: n.data.frontAxis || "preserve",
      scale: typeof n.data.scale === "number" && n.data.scale > 0 ? n.data.scale : 1,
      transform: n.data.transform || identityTransform,
      ...(n.data.operation==="transform" && n.data.transformTargets?.length ? {transformTargets: n.data.transformTargets} : {}),
      ...(n.data.operation==="delete" ? {deletePaths:n.data.deletePaths || []}:{}),
      ...(n.data.operation==="scatter" ? {scatter: n.data.scatter || defaultScatter}:{}),
      ...(n.data.operation==="instancer" ? {instancer: n.data.instancer || defaultInstancer}:{}),
      ...(n.data.operation==="group" && n.data.groupName ? {groupName: n.data.groupName}:{}),
      ...(n.data.operation==="material_bind" ? {materialBindings:n.data.materialBindings || []}:{}),
      ...(n.data.operation==="light" ? {lightOverrides:n.data.lightOverrides || [], ...(typeof n.data.lightIntensityScale === "number" && n.data.lightIntensityScale > 0 ? {lightIntensityScale: n.data.lightIntensityScale} : {})}:{}),
      ...(n.data.operation==="hdri" && n.data.hdri ? {hdri: n.data.hdri}:{}),
      ...(n.data.operation==="fog" ? {fog: n.data.fog || defaultFog}:{}),
      ...(n.data.operation==="postfx" ? {postFx: n.data.postFx || defaultPostFx}:{}),
      ...(n.data.operation==="hair" && n.data.hair ? {hair: n.data.hair}:{}),
      ...(n.data.operation==="cache_edit" ? {cacheEdit: n.data.cacheEdit || defaultCacheEditSettings}:{}),
      ...(n.data.operation==="pose" ? {pose: n.data.pose || {mappings:[],entries:[]}}:{}),
      ...(n.data.operation==="character" ? {character: n.data.character || defaultCharacterSettings}:{}),
      ...(n.data.operation==="camera" && n.data.camera ? {camera: n.data.camera}:{}),
      ...(n.data.operation==="camera" && n.data.cameraViews ? {cameraViews: n.data.cameraViews}:{}),
      assetOverrides: n.data.assetOverrides || [],
      ...(n.data.operation === "materialx"
        ? {
            materialX: { ...defaultMaterialX, ...(n.data.materialX || {}) },
            ...(n.data.materialXRules
              ? {
                  materialXRules: n.data.materialXRules.map((r) => ({
                    ...r,
                    material: { ...defaultMaterialX, ...r.material },
                  })),
                }
              : {}),
          }
        : {}),
      ...(n.data.liveAdjustment && !(applicationInput && n.id===targetNodeId) ? {liveAdjustment:n.data.liveAdjustment,publishedDependencyId:null}:{}),
      publication:(applicationInput && n.id===targetNodeId) || n.data.liveAdjustment ? null : n.data.publication ? {sourceRevisions:n.data.publication.sourceRevisions,...(n.data.publication.revision?{revision:n.data.publication.revision}:{})}:null,
    }));
  const used = new Set(
    nodes
      .flatMap((n) => [
        n.inputDependencyId,
        n.metahumanBodyDependencyId,
        n.publishedDependencyId,
        n.nativeProjectDependencyId,
        n.mdBridgeDependencyId,
        ...n.assetOverrides.map(o => o.replacementDependencyId),
        ...materialTextures(n.materialX),
        n.liveAdjustment?.editedDependencyId,
        n.liveAdjustment?.baselineDependencyId,
        n.liveAdjustment?.handoffDependencyId,
        n.hdri?.textureDependencyId,
        ...(n.pose?.entries || []).map(e => e.dependencyId),
        ...(n.character?.clothCaches || []).map(c => c.dependencyId),
        ...(n.character?.headGrooms || []).filter(g => g.sourceMode === "file").map(g => g.dependencyId),
        ...(n.materialXRules || []).flatMap(r => materialTextures(r.material)),
      ])
      .filter(Boolean),
  );
  return JSON.stringify({
    namespaceVersion:2,
    targetNodeId,
    nodes,
    edges: project.edges
      .filter((e) => needed.has(e.source) && needed.has(e.target))
      .map((e) => ({
        id: project.nodes.find(n=>n.id===e.target)?.data.handoff?.inputEdges?.[e.source] || e.id,
        source: e.source,
        target: e.target,
      })),
    dependencies: project.dependencies
      .filter((d) => used.has(d.id))
      .map((d) => ({ id: d.id, path: d.path })),
    ...(project.proceduralGraph ? { proceduralGraph: project.proceduralGraph } : {}),
  });
}

/** A replacement belongs to one graph branch; the original registry is immutable. */
export function overrideDependency(project: StoredProject, nodeId: string, id: string, replacement: string): StoredProject {
  const original = project.dependencies.find(d => d.id === id);
  if (!original) throw new Error("Original dependency no longer exists");
  const registered = registerFiles(project.dependencies, [replacement], original.kind);
  return {...project, dependencies: registered.dependencies, nodes: project.nodes.map(n => n.id !== nodeId ? n : ({...n, data: {...n.data, assetOverrides: [
    ...(n.data.assetOverrides || []).filter(o => fileKey(o.originalPath) !== fileKey(original.path)),
    {originalPath: original.path, replacementDependencyId: registered.ids[0]}
  ]}}))};
}
export function mergeDiscoveredInputs(
  project: StoredProject,
  files: { path: string; kind: DependencyKind }[],
  makeId = () => crypto.randomUUID(),
): StoredProject {
  let dependencies = project.dependencies;
  let changed = false;
  for (const file of files) {
    if (!file.path.trim()) continue;
    if (!dependencies.some((d) => fileKey(d.path) === fileKey(file.path))) {
      dependencies = registerFiles(dependencies, [file.path], file.kind, makeId).dependencies;
      changed = true;
    }
  }
  return changed ? { ...project, dependencies } : project;
}

export function overrideInputs(project: StoredProject, nodeId: string) {
  const spec = JSON.parse(graphPreview(project, nodeId));
  const roots = new Set<string>(
    spec.nodes.flatMap((n: {
      inputDependencyId: string;
      publishedDependencyId: string;
      materialX?: MaterialXSettings;
      materialXRules?: MaterialXRule[];
      hdri?: { textureDependencyId?: string | null };
      liveAdjustment?: { editedDependencyId?: string | null };
    }) => [
      n.inputDependencyId,
      n.publishedDependencyId,
      ...materialTextures(n.materialX),
      ...(n.materialXRules || []).flatMap(r => materialTextures(r.material)),
      n.hdri?.textureDependencyId || "",
      n.liveAdjustment?.editedDependencyId || "",
    ]).filter(Boolean)
  );
  return project.dependencies.filter(d => (d.kind==="asset" && roots.has(d.id)) || (d.usdReferences || []).some(r => roots.has(r.sourceDependencyId)));
}

export function recordHandoff(project:StoredProject,nodeId:string,result:HandoffResult):StoredProject {
  const root=registerFiles(project.dependencies,[result.path],"usd");
  const layers=registerFiles(root.dependencies,result.layerPaths,"usd");
  return {...project,dependencies:layers.dependencies.map(d=>layers.ids.includes(d.id) ? {...d,usdReferences:uniqueReferences([...(d.usdReferences || []),{sourceDependencyId:root.ids[0],path:d.path}])} : d),nodes:project.nodes.map(n=>n.id !== nodeId ? n : {...n,data:{...n.data,handoffSelection:result.selection ?? null,handoff:{selection:result.selection ?? null,inputEdges:result.inputEdges,dependencyId:root.ids[0],layerDependencyIds:layers.ids,conversion:result.draft,policy:result.policy}}})};
}

export function prepareQuickstart(project: StoredProject, nodeId: string, handoff: HandoffResult): StoredProject {
  const staged = recordHandoff(project, nodeId, handoff);
  return {
    ...staged,
    nodes: staged.nodes.map(node => node.id !== nodeId ? node : {
      ...node,
      data: {
        ...node.data,
        nativeProjectDependencyId: null,
        nativePreparation: null,
        nativeImport: null,
        manualBaseline: null,
        publicationPolicy: null,
      },
    }),
  };
}

export function recordNativeCreation(project:StoredProject,nodeId:string,result:NativeCreationResult):StoredProject {
  const node=project.nodes.find(n=>n.id===nodeId);
  if(!node)throw new Error(`Created ${result.path}, but the node was deleted. Link the file to a new Application node.`);
  if(node.data.nativeProjectDependencyId)throw new Error(`Created ${result.path}, but the node acquired a native-project link during import. The current link was preserved.`);
  const files=registerFiles(project.dependencies,[result.path],"nativeProject");
  const nativeId=files.ids[0];
  if(!result.baselinePath)return {...project,dependencies:files.dependencies,nodes:project.nodes.map(n=>n.id!==nodeId?n:{...n,data:{...n.data,nativeProjectDependencyId:nativeId,nativePreparation:{nativeDependencyId:nativeId,handoffDependencyId:result.handoffDependencyId,conversion:result.conversion,policy:result.policy,report:result.report}}})};
  const baseline=registerFiles(files.dependencies,[result.baselinePath],"usd");
  return {...project,dependencies:baseline.dependencies,nodes:project.nodes.map(n=>n.id!==nodeId?n:{...n,data:{...n.data,nativeProjectDependencyId:nativeId,nativeImport:{baselineDependencyId:baseline.ids[0],nativeDependencyId:nativeId,handoffDependencyId:result.handoffDependencyId,conversion:result.conversion,policy:result.policy,report:result.report}}})};
}

/** JSON object field order is not policy state; array order and values still matter. */
function sameJsonValue(a:unknown,b:unknown):boolean {
 if(a===b)return true;
 if(a===null||b===null||typeof a!=="object"||typeof b!=="object")return false;
 if(Array.isArray(a)||Array.isArray(b))return Array.isArray(a)&&Array.isArray(b)&&a.length===b.length&&a.every((v,i)=>sameJsonValue(v,b[i]));
 const left=a as Record<string,unknown>,right=b as Record<string,unknown>;
 const keys=Object.keys(left);
 return keys.length===Object.keys(right).length&&keys.every(key=>Object.prototype.hasOwnProperty.call(right,key)&&sameJsonValue(left[key],right[key]));
}

export function recordPublication(project:StoredProject,nodeId:string,editedPath:string,result:PublicationResult):StoredProject {
 const node=project.nodes.find(n=>n.id===nodeId),native=node?.data.manualBaseline || node?.data.nativeImport;
 if(!node || !native)throw new Error("Publication created, but its native node no longer exists");
 if(result.reconciliation && result.replacesPublicationDependencyId!==node.data.publication?.dependencyId)throw new Error(`Created ${result.path}, but the previous publication changed. Current output was preserved.`);
 const policy=result.reconciliation ? node.data.publication?.policy : (node.data.publicationPolicy || native.policy);
 if(result.baselineDependencyId!==native.baselineDependencyId || result.handoffDependencyId!==native.handoffDependencyId || !sameJsonValue(result.policy,policy))throw new Error(`Created ${result.path}, but preparation or publishing permissions changed. The files were written; the node output link was not updated.`);
 const outputLinks=registerFiles(project.dependencies,[result.path,result.editLayerPath],"usd");
 const links=registerFiles(outputLinks.dependencies,[editedPath],/\.(obj|abc)$/i.test(editedPath)?"asset":"usd");
 const [root,layer]=outputLinks.ids;
 const edited=links.ids[0];
 let dependencies=links.dependencies,reconciliation:Reconciliation|undefined;
 if(result.reconciliation){
  const input=registerFiles(dependencies,[result.reconciliation.inputPath],"usd");
  const layers=registerFiles(input.dependencies,result.reconciliation.layerPaths,"usd");
  dependencies=layers.dependencies.map(d=>layers.ids.includes(d.id)?{...d,usdReferences:uniqueReferences([...(d.usdReferences || []),{sourceDependencyId:input.ids[0],path:d.path}])}:d);
  reconciliation={inputDependencyId:input.ids[0],layerDependencyIds:layers.ids,graphRevision:result.reconciliation.graphRevision};
 }
 return {...project,dependencies,nodes:project.nodes.map(n=>n.id!==nodeId?n:{...n,data:{...n.data,publishedDependencyId:root,publication:{revision:result.publicationRevision,reconciliation,dependencyId:root,editLayerDependencyId:layer,editedDependencyId:edited,baselineDependencyId:result.baselineDependencyId,handoffDependencyId:result.handoffDependencyId,policy:result.policy,conversion:result.conversion,report:result.report,sourceRevisions:result.sourceRevisions,correspondence:result.correspondence}}})};
}

export function recordManualBaseline(project:StoredProject,nodeId:string,result:ManualBaselineResult):StoredProject {
 const node=project.nodes.find(n=>n.id===nodeId);
 if(!node || node.data.handoff?.dependencyId!==result.handoffDependencyId)throw new Error("Node preparation changed during baseline validation. Select baseline again.");
 const files=registerFiles(project.dependencies,[result.baselinePath],/\.(obj|abc)$/i.test(result.baselinePath)?"asset":"usd");
 return {...project,dependencies:files.dependencies,nodes:project.nodes.map(n=>n.id!==nodeId?n:{...n,data:{...n.data,publicationPolicy:null,manualBaseline:{baselineDependencyId:files.ids[0],nativeDependencyId:result.nativeDependencyId,handoffDependencyId:result.handoffDependencyId,policy:result.policy,conversion:result.conversion,report:result.report}}})};
}

export function recordLiveAdjustment(project:StoredProject,nodeId:string,edited:string,result:LiveAdjustmentResult):StoredProject {
 const node=project.nodes.find(n=>n.id===nodeId),baseline=node?.data.manualBaseline || node?.data.nativeImport;
 const isSpMaterialSync = typeof result.revision === "string" && result.revision.startsWith("sp-");
 if(!node || (!isSpMaterialSync && (!baseline || baseline.baselineDependencyId!==result.baselineDependencyId || baseline.handoffDependencyId!==result.handoffDependencyId || !sameJsonValue(node.data.publicationPolicy || baseline.policy,result.policy))))throw new Error("Preparation or edit permissions changed; analyze again");
 const files=registerFiles(project.dependencies,[edited],/\.(obj|abc|json)$/i.test(edited)?"asset":"usd");
 return {...project,dependencies:files.dependencies,nodes:project.nodes.map(n=>n.id!==nodeId?n:{...n,data:{...n.data,publishedDependencyId:null,publication:null,liveAdjustment:{correspondence:result.correspondence,revision:result.revision,layer:result.layer,guards:result.guards,fileRevisions:result.fileRevisions,report:result.report,policy:result.policy,conversion:result.conversion,baselineDependencyId:result.baselineDependencyId,handoffDependencyId:result.handoffDependencyId,editedDependencyId:files.ids[0]}}})};
}

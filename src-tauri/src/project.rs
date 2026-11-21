use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Project {
    pub version: u32,
    pub nodes: Vec<Node>,
    pub edges: Vec<Edge>,
    pub dependencies: Vec<Dependency>,
    #[serde(default)]
    pub viewport: ViewportSettings,
    #[serde(
        rename = "proceduralGraph",
        default,
        skip_serializing_if = "Option::is_none"
    )]
    pub procedural_graph: Option<ProceduralGraph>,
}

const PROCEDURAL_NODE_KINDS: &[&str] = &[
    "uv",
    "place2d",
    "position",
    "place3d",
    "triplanar",
    "noise2d",
    "noise3d",
    "fractal3d",
    "worley2d",
    "worley3d",
    "cell2d",
    "cell3d",
    "unified2d",
    "unified3d",
    "checkerboard",
    "grid",
    "tiledcircles",
    "tiledhexagons",
    "ramplr",
    "ramptb",
    "mix",
    "multiply",
    "add",
    "remap",
    "clamp",
    "contrast",
    "saturate",
    "hsvadjust",
    "bump",
    "colorramp",
    "output",
];

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProceduralGraph {
    pub nodes: Vec<ProceduralNode>,
    pub edges: Vec<ProceduralEdge>,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProceduralNode {
    pub id: String,
    #[serde(rename = "type")]
    pub kind_type: String,
    pub position: Position,
    pub data: ProceduralNodeData,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProceduralNodeData {
    pub kind: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub label: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub params: Option<serde_json::Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub material_x_node_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rule_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parameter: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mapping_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub strength: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub enabled: Option<bool>,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProceduralEdge {
    pub id: String,
    pub source: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_handle: Option<String>,
    pub target: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_handle: Option<String>,
}
#[derive(Serialize, Deserialize, Clone, PartialEq, Debug)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ViewportCamera {
    pub yaw: f64,
    pub pitch: f64,
    pub zoom: f64,
    pub pan_x: f64,
    pub pan_y: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fov: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dof_enabled: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub focus_distance: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub f_stop: Option<f64>,
}

#[derive(Serialize, Deserialize, Clone, PartialEq, Debug)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CameraView {
    pub id: String,
    pub name: String,
    pub camera: ViewportCamera,
}
#[derive(Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ViewportSettings {
    pub pinned_node_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub renderer: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ovrtx_mode: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub up_axis: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub materials: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub active_camera_view_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub camera: Option<ViewportCamera>,
}
#[derive(Serialize, Deserialize, Clone, PartialEq, Eq, Hash, Debug)]
#[serde(rename_all = "camelCase")]
pub enum DependencyKind {
    Usd,
    NativeProject,
    Executable,
    Asset,
}
#[derive(Serialize, Deserialize)]
pub struct Dependency {
    pub id: String,
    pub kind: DependencyKind,
    pub path: String,
    #[serde(
        default,
        rename = "usdReferences",
        skip_serializing_if = "Vec::is_empty"
    )]
    pub usd_references: Vec<UsdReference>,
}
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UsdReference {
    pub source_dependency_id: String,
    pub path: String,
}
#[derive(Serialize, Deserialize)]
pub struct Node {
    pub id: String,
    #[serde(rename = "type")]
    pub kind: String,
    pub position: Position,
    pub data: NodeData,
    #[serde(flatten)]
    pub ui: HashMap<String, serde_json::Value>,
}
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Position {
    pub x: f64,
    pub y: f64,
}
#[derive(Clone, Serialize, Deserialize, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ScatterPrototypeRule {
    pub id: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
    pub prim_path: String,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ScatterSettings {
    #[serde(default = "default_scatter_count")]
    pub count: u32,
    #[serde(default = "default_scatter_seed")]
    pub seed: u64,
    #[serde(default = "default_scatter_distribution")]
    pub distribution: String,
    #[serde(default = "default_scatter_area")]
    pub area_size: [f64; 3],
    #[serde(default = "default_scatter_scale_min")]
    pub scale_min: f64,
    #[serde(default = "default_scatter_scale_max")]
    pub scale_max: f64,
    #[serde(default = "default_scatter_random_yaw")]
    pub random_yaw: bool,
    #[serde(default)]
    pub pass_through: bool,
    #[serde(default)]
    pub surface_prim_path: String,
    #[serde(default)]
    pub align_to_normal: bool,
    #[serde(default = "default_scatter_max_slope_deg")]
    pub max_slope_deg: f64,
    #[serde(default)]
    pub min_spacing: f64,
    #[serde(default)]
    pub prototypes: Vec<ScatterPrototypeRule>,
}
fn default_scatter_count() -> u32 {
    100
}
fn default_scatter_seed() -> u64 {
    42
}
fn default_scatter_distribution() -> String {
    "random".to_string()
}
fn default_scatter_area() -> [f64; 3] {
    [20.0, 0.0, 20.0]
}
fn default_scatter_scale_min() -> f64 {
    0.8
}
fn default_scatter_scale_max() -> f64 {
    1.2
}
fn default_scatter_random_yaw() -> bool {
    true
}
fn default_scatter_max_slope_deg() -> f64 {
    90.0
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct NodeData {
    #[serde(default = "default_true")]
    pub load_payloads: bool,
    #[serde(default)]
    pub use_as_payload: bool,
    #[serde(default)]
    pub material_x: MaterialXSettings,
    #[serde(default)]
    pub material_x_rules: Option<Vec<MaterialXRule>>,
    #[serde(default)]
    pub material_bindings: Option<Vec<MaterialBindingRule>>,
    #[serde(default)]
    pub light_overrides: Option<Vec<LightOverride>>,
    #[serde(default)]
    pub delete_paths: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scatter: Option<ScatterSettings>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub group_name: Option<String>,
    #[serde(default)]
    pub publication_policy: Option<crate::application_profiles::PolicySnapshot>,
    #[serde(default)]
    pub publication: Option<Publication>,
    #[serde(default)]
    pub live_adjustment: Option<LiveAdjustment>,
    #[serde(default)]
    pub application_profile_id: Option<String>,
    #[serde(default)]
    pub edit_scope: Vec<String>,
    #[serde(default)]
    pub native_import: Option<NativeImport>,
    #[serde(default)]
    pub manual_baseline: Option<NativeImport>,
    #[serde(default)]
    pub native_preparation: Option<NativePreparation>,
    #[serde(default)]
    pub handoff: Option<Handoff>,
    #[serde(default)]
    pub handoff_selection: Option<Vec<String>>,
    #[serde(default)]
    pub application_meters_per_unit: Option<f64>,
    #[serde(default)]
    pub coordinate_draft: Option<CoordinateDraft>,
    #[serde(default)]
    pub asset_overrides: Vec<AssetOverride>,
    #[serde(default)]
    pub app_executable_dependency_id: Option<String>,
    pub label: String,
    pub operation: String,
    pub app: String,
    pub axis: String,
    #[serde(default = "default_preserve_axis")]
    pub front_axis: String,
    pub scale: f64,
    #[serde(default)]
    pub input_dependency_id: Option<String>,
    #[serde(default)]
    pub native_project_dependency_id: Option<String>,
    #[serde(default)]
    pub published_dependency_id: Option<String>,
    #[serde(default)]
    pub asset_dependency_ids: Vec<String>,
    #[serde(default)]
    pub transform: Transform,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub transform_targets: Option<Vec<TransformTargetRule>>,
    #[serde(default)]
    pub instanced: Option<bool>,
    #[serde(default)]
    pub blender_selections: Option<Vec<String>>,
    #[serde(default)]
    pub blender_scene: Option<String>,
    #[serde(default)]
    pub blender_objects: Option<Vec<BlenderObjectInfo>>,
    #[serde(default)]
    pub blender_material_mode: Option<String>,
    #[serde(default)]
    pub blender_convert_procedurals: Option<bool>,
    #[serde(default)]
    pub blender_export_skeletons: Option<bool>,
    #[serde(default)]
    pub last_extraction_seconds: Option<f64>,
    #[serde(default)]
    pub hdri: Option<HdriSettings>,
    #[serde(default)]
    pub hair: Option<HairSettings>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cache_edit: Option<CacheEditSettings>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub character: Option<CharacterSettings>,
    #[serde(default)]
    pub pose: Option<PoseSettings>,
    #[serde(default)]
    pub camera: Option<ViewportCamera>,
    #[serde(default)]
    pub camera_views: Option<Vec<CameraView>>,
}
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct BlenderObjectInfo {
    pub name: String,
    #[serde(default)]
    pub r#type: String,
    #[serde(default)]
    pub vertices: Option<usize>,
    #[serde(default)]
    pub polygons: Option<usize>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub collections: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scene: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scene_visible: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub active_scene: Option<bool>,
}
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MaterialBindingRule {
    pub id: String,
    pub enabled: bool,
    pub target_query: String,
    pub material_path: String,
    #[serde(default = "default_binding_strength")]
    pub strength: String,
}
fn default_preserve_axis() -> String {
    "preserve".into()
}

fn default_binding_strength() -> String {
    "strongerThanDescendants".into()
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct TransformTargetRule {
    pub id: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
    pub pattern: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct LightOverride {
    pub id: String,
    pub light_path: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
    pub intensity: Option<f64>,
    pub exposure: Option<f64>,
    pub color: Option<[f64; 3]>,
    pub enable_color_temperature: Option<bool>,
    pub color_temperature: Option<f64>,
}
fn default_true() -> bool {
    true
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HairGroomDelta {
    pub point_count: usize,
    pub curve_count: usize,
    pub deltas_z_b64: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub counts_z_b64: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub points_z_b64: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub widths_z_b64: Option<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CharacterJointAttachment {
    pub id: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub target_query: String,
    #[serde(default)]
    pub skeleton_path: String,
    #[serde(default)]
    pub joint_name: String,
    #[serde(default)]
    pub offset_translation: [f64; 3],
    #[serde(default)]
    pub offset_rotation: [f64; 3],
    #[serde(default = "default_unit_scale")]
    pub offset_scale: [f64; 3],
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CharacterSkeletonBinding {
    pub id: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub target_query: String,
    #[serde(default)]
    pub skeleton_path: String,
    #[serde(default = "default_true")]
    pub deactivate_source_skeletons: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CharacterAnimationOverride {
    pub id: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub skeleton_path: String,
    #[serde(default)]
    pub animation_path: String,
    #[serde(default = "default_char_anim_mode")]
    pub mode: String,
    #[serde(default)]
    pub pose_time: f64,
}

fn default_char_anim_mode() -> String {
    "pose".into()
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CharacterClothCache {
    pub id: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub dependency_id: Option<String>,
    #[serde(default = "default_true")]
    pub attach_to_root: bool,
    #[serde(default = "default_preserve_axis")]
    pub axis: String,
    #[serde(default = "default_preserve_axis")]
    pub front_axis: String,
    #[serde(default = "default_one")]
    pub scale: f64,
    #[serde(default)]
    pub time_offset: f64,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Default)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CharacterSettings {
    #[serde(default)]
    pub base_skeleton_path: String,
    #[serde(default)]
    pub animation_overrides: Vec<CharacterAnimationOverride>,
    #[serde(default)]
    pub joint_attachments: Vec<CharacterJointAttachment>,
    #[serde(default)]
    pub skeleton_bindings: Vec<CharacterSkeletonBinding>,
    #[serde(default)]
    pub cloth_caches: Vec<CharacterClothCache>,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Default)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PoseSettings {
    #[serde(default)]
    pub mappings: Vec<PoseMapping>,
    #[serde(default)]
    pub entries: Vec<PoseEntry>,
}
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PoseEntry {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub dependency_id: Option<String>,
    pub animation_path: String,
    pub mode: String,
    pub pose_time: f64,
    #[serde(default)]
    pub joints: Option<Vec<PoseJoint>>,
    #[serde(default)]
    pub blend_shapes: Vec<String>,
    #[serde(default)]
    pub blend_shape_weights: Vec<f64>,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PoseMapping {
    pub id: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
    pub skeleton_path: String,
    #[serde(default)]
    pub entry_id: Option<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PoseJoint {
    pub name: String,
    pub translation: [f64; 3],
    pub rotation: [f64; 4],
    pub scale: [f64; 3],
    pub rotation_offset: [f64; 3],
    pub translation_offset: [f64; 3],
}

fn default_unit_scale() -> [f64; 3] {
    [1.0, 1.0, 1.0]
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HairSettings {
    #[serde(default)]
    pub target_query: String,
    #[serde(default = "default_hair_width_mode")]
    pub width_mode: String,
    #[serde(default = "default_hair_width")]
    pub width: f64,
    #[serde(default)]
    pub taper: f64,
    #[serde(default = "default_hair_width")]
    pub root_scale: f64,
    #[serde(default)]
    pub sim_enabled: bool,
    #[serde(default = "default_hair_sim_mode")]
    pub sim_mode: String,
    #[serde(default = "default_hair_settle_steps")]
    pub settle_steps: u32,
    #[serde(default = "default_hair_collider_query")]
    pub collider_query: String,
    #[serde(default = "default_hair_start_frame")]
    pub start_frame: f64,
    #[serde(default = "default_hair_end_frame")]
    pub end_frame: f64,
    #[serde(default = "default_hair_substeps")]
    pub substeps: u32,
    #[serde(default = "default_hair_iterations")]
    pub iterations: u32,
    #[serde(default = "default_hair_stiffness")]
    pub stiffness: f64,
    #[serde(default = "default_hair_bend_stiffness")]
    pub bend_stiffness: f64,
    #[serde(default = "default_hair_damping")]
    pub damping: f64,
    #[serde(default = "default_hair_gravity")]
    pub gravity: f64,
    #[serde(default)]
    pub wind: [f64; 3],
    #[serde(default = "default_hair_collision_margin")]
    pub collision_margin: f64,
    #[serde(default = "default_hair_friction")]
    pub friction: f64,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub groom_deltas: BTreeMap<String, HairGroomDelta>,
}
fn default_hair_width_mode() -> String {
    "multiply".into()
}
fn default_hair_width() -> f64 {
    1.0
}
fn default_hair_collider_query() -> String {
    "__none__".into()
}
fn default_hair_sim_mode() -> String {
    "static".into()
}
fn default_hair_settle_steps() -> u32 {
    24
}
fn default_hair_start_frame() -> f64 {
    1.0
}
fn default_hair_end_frame() -> f64 {
    48.0
}
fn default_hair_substeps() -> u32 {
    4
}
fn default_hair_iterations() -> u32 {
    8
}
fn default_hair_stiffness() -> f64 {
    0.95
}
fn default_hair_bend_stiffness() -> f64 {
    0.4
}
fn default_hair_damping() -> f64 {
    0.04
}
fn default_hair_gravity() -> f64 {
    9.81
}
fn default_hair_collision_margin() -> f64 {
    0.005
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CacheEditDelta {
    pub count: usize,
    #[serde(
        default,
        rename = "indicesZB64",
        alias = "indicesZb64",
        skip_serializing_if = "Option::is_none"
    )]
    pub indices_zb64: Option<String>,
    #[serde(rename = "deltasZB64", alias = "deltasZb64")]
    pub deltas_zb64: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CacheEditMask {
    pub count: usize,
    #[serde(rename = "weightsZB64", alias = "weightsZb64")]
    pub weights_zb64: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CacheEditLayer {
    pub id: String,
    pub name: String,
    #[serde(default = "default_true_flag")]
    pub enabled: bool,
    #[serde(default = "default_cache_weight")]
    pub weight: f64,
    #[serde(default = "default_cache_mode")]
    pub mode: String,
    #[serde(default)]
    pub center_frame: f64,
    #[serde(default = "default_cache_fade")]
    pub fade_in: f64,
    #[serde(default = "default_cache_fade")]
    pub fade_out: f64,
    #[serde(default = "default_cache_falloff")]
    pub falloff: String,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub deltas: BTreeMap<String, CacheEditDelta>,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CacheEditSettings {
    #[serde(default)]
    pub target_query: String,
    #[serde(default = "default_hair_collider_query")]
    pub collider_query: String,
    #[serde(default)]
    pub active_layer_id: String,
    #[serde(default)]
    pub temporal_smooth_frames: u32,
    #[serde(default)]
    pub temporal_smooth_strength: f64,
    #[serde(default = "default_cache_cloth_substeps")]
    pub cloth_substeps: u32,
    #[serde(default = "default_cache_cloth_iterations")]
    pub cloth_iterations: u32,
    #[serde(default = "default_cache_cloth_stretch")]
    pub cloth_stretch_stiffness: f64,
    #[serde(default = "default_cache_cloth_bend")]
    pub cloth_bend_stiffness: f64,
    #[serde(default = "default_cache_cloth_damping")]
    pub cloth_damping: f64,
    #[serde(default = "default_hair_gravity")]
    pub cloth_gravity: f64,
    #[serde(default = "default_cache_cloth_margin")]
    pub cloth_collision_margin: f64,
    #[serde(default = "default_cache_cloth_friction")]
    pub cloth_friction: f64,
    #[serde(default = "default_cache_cloth_patch_falloff")]
    pub cloth_drag_pin_falloff: f64,
    #[serde(default = "default_cache_sim_scale")]
    pub cloth_sim_scale: f64,
    #[serde(default)]
    pub sim_enabled: bool,
    #[serde(default = "default_cache_sim_mode")]
    pub sim_mode: String,
    #[serde(default)]
    pub sim_start_frame: f64,
    #[serde(default = "default_cache_sim_end_frame")]
    pub sim_end_frame: f64,
    #[serde(default = "default_cache_sim_fps")]
    pub sim_fps: f64,
    #[serde(default = "default_cache_sim_settle_steps")]
    pub sim_settle_steps: u32,
    #[serde(default = "default_cache_sim_fabric_preset")]
    pub sim_fabric_preset: String,
    #[serde(default = "default_cache_sim_air_drag")]
    pub sim_air_drag: f64,
    #[serde(default)]
    pub sim_wind: [f64; 3],
    #[serde(default = "default_true_flag")]
    pub sim_self_collision: bool,
    #[serde(default = "default_cache_sim_self_thickness")]
    pub sim_self_thickness: f64,
    #[serde(default = "default_true_flag")]
    pub sim_pin_to_collider: bool,
    #[serde(default)]
    pub sim_cache_version: u32,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub mask_weights: BTreeMap<String, CacheEditMask>,
    #[serde(default)]
    pub layers: Vec<CacheEditLayer>,
}

fn default_true_flag() -> bool {
    true
}
fn default_cache_weight() -> f64 {
    1.0
}
fn default_cache_mode() -> String {
    "frameRange".into()
}
fn default_cache_fade() -> f64 {
    8.0
}
fn default_cache_falloff() -> String {
    "smoothstep".into()
}
fn default_cache_cloth_substeps() -> u32 {
    4
}
fn default_cache_cloth_iterations() -> u32 {
    8
}
fn default_cache_cloth_stretch() -> f64 {
    0.95
}
fn default_cache_cloth_bend() -> f64 {
    0.35
}
fn default_cache_cloth_damping() -> f64 {
    0.08
}
fn default_cache_cloth_margin() -> f64 {
    0.004
}
fn default_cache_cloth_friction() -> f64 {
    0.35
}
fn default_cache_cloth_patch_falloff() -> f64 {
    1.4
}
fn default_cache_sim_scale() -> f64 {
    1.0
}
fn default_cache_sim_mode() -> String {
    "animated".into()
}
fn default_cache_sim_end_frame() -> f64 {
    48.0
}
fn default_cache_sim_fps() -> f64 {
    24.0
}
fn default_cache_sim_settle_steps() -> u32 {
    12
}
fn default_cache_sim_fabric_preset() -> String {
    "custom".into()
}
fn default_cache_sim_air_drag() -> f64 {
    0.05
}
fn default_cache_sim_self_thickness() -> f64 {
    0.004
}
fn default_hair_friction() -> f64 {
    0.25
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct HdriSettings {
    #[serde(default)]
    pub texture_dependency_id: Option<String>,
    #[serde(default = "default_hdri_intensity")]
    pub intensity: f64,
    #[serde(default)]
    pub exposure: f64,
    #[serde(default)]
    pub rotation: f64,
    #[serde(default = "default_hdri_color")]
    pub color: [f64; 3],
    #[serde(default = "default_hdri_format")]
    pub texture_format: String,
}
fn default_hdri_intensity() -> f64 {
    1.0
}
fn default_hdri_color() -> [f64; 3] {
    [1.0, 1.0, 1.0]
}
fn default_hdri_format() -> String {
    "latlong".into()
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MaterialXRule {
    pub id: String,
    pub enabled: bool,
    pub search_type: String,
    pub query: String,
    pub material: MaterialXSettings,
}
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProceduralConfig {
    pub r#type: String,
    #[serde(default = "default_space")]
    pub space: String,
    #[serde(default = "default_scale_vec")]
    pub scale: [f64; 2],
    #[serde(default = "default_one")]
    pub amplitude: f64,
    #[serde(default = "default_octaves")]
    pub octaves: i32,
    #[serde(default)]
    pub color1: Option<[f64; 3]>,
    #[serde(default)]
    pub color2: Option<[f64; 3]>,
}
fn default_space() -> String {
    "uv".into()
}
fn default_scale_vec() -> [f64; 2] {
    [1.0, 1.0]
}
fn default_one() -> f64 {
    1.0
}
fn default_octaves() -> i32 {
    3
}

fn deserialize_color_or_default<'de, D>(deserializer: D) -> Result<[f64; 3], D::Error>
where
    D: serde::Deserializer<'de>,
{
    let opt = Option::<[f64; 3]>::deserialize(deserializer)?;
    Ok(opt.unwrap_or([1.0, 1.0, 1.0]))
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MaterialXSettings {
    pub name: String,
    pub base_color: [f64; 3],
    pub metalness: f64,
    pub roughness: f64,
    pub specular: f64,
    pub opacity: f64,
    #[serde(default = "default_one")]
    pub base_weight: f64,
    #[serde(
        default = "default_one_color",
        deserialize_with = "deserialize_color_or_default"
    )]
    pub specular_color: [f64; 3],
    #[serde(default = "default_ior")]
    pub specular_ior: f64,
    #[serde(default)]
    pub transmission: f64,
    #[serde(
        default = "default_one_color",
        deserialize_with = "deserialize_color_or_default"
    )]
    pub transmission_color: [f64; 3],
    #[serde(default)]
    pub emission: f64,
    #[serde(
        default = "default_one_color",
        deserialize_with = "deserialize_color_or_default"
    )]
    pub emission_color: [f64; 3],
    #[serde(default)]
    pub coat: f64,
    #[serde(default = "default_coat_roughness")]
    pub coat_roughness: f64,
    #[serde(
        default = "default_one_color",
        deserialize_with = "deserialize_color_or_default"
    )]
    pub coat_color: [f64; 3],
    #[serde(default)]
    pub sheen: f64,
    #[serde(default = "default_sheen_roughness")]
    pub sheen_roughness: f64,
    #[serde(
        default = "default_one_color",
        deserialize_with = "deserialize_color_or_default"
    )]
    pub sheen_color: [f64; 3],
    #[serde(default)]
    pub subsurface: f64,
    #[serde(
        default = "default_one_color",
        deserialize_with = "deserialize_color_or_default"
    )]
    pub subsurface_color: [f64; 3],
    #[serde(default = "default_one")]
    pub subsurface_scale: f64,
    #[serde(
        default = "default_one_color",
        deserialize_with = "deserialize_color_or_default"
    )]
    pub subsurface_radius: [f64; 3],
    #[serde(default)]
    pub subsurface_anisotropy: f64,

    pub binding_targets: Vec<String>,
    pub base_color_texture_dependency_id: Option<String>,
    pub roughness_texture_dependency_id: Option<String>,
    #[serde(default)]
    pub metallic_texture_dependency_id: Option<String>,
    #[serde(default)]
    pub emission_texture_dependency_id: Option<String>,
    #[serde(default)]
    pub normal_texture_dependency_id: Option<String>,
    #[serde(default)]
    pub opacity_texture_dependency_id: Option<String>,
    #[serde(default)]
    pub transmission_texture_dependency_id: Option<String>,

    #[serde(default)]
    pub procedural_base_color: Option<ProceduralConfig>,
    #[serde(default)]
    pub procedural_roughness: Option<ProceduralConfig>,
    #[serde(default)]
    pub uv_mode: Option<String>,
}
fn default_ior() -> f64 {
    1.5
}
fn default_coat_roughness() -> f64 {
    0.1
}
fn default_sheen_roughness() -> f64 {
    0.3
}
fn default_one_color() -> [f64; 3] {
    [1.0, 1.0, 1.0]
}

impl MaterialXSettings {
    pub fn texture_dependencies(&self) -> Vec<&String> {
        [
            &self.base_color_texture_dependency_id,
            &self.roughness_texture_dependency_id,
            &self.metallic_texture_dependency_id,
            &self.emission_texture_dependency_id,
            &self.normal_texture_dependency_id,
            &self.opacity_texture_dependency_id,
            &self.transmission_texture_dependency_id,
        ]
        .into_iter()
        .flatten()
        .collect()
    }
}

impl Default for MaterialXSettings {
    fn default() -> Self {
        Self {
            name: "OrchFlowMaterial".into(),
            base_color: [0.7, 0.35, 0.15],
            metalness: 0.,
            roughness: 0.4,
            specular: 0.5,
            opacity: 1.,
            base_weight: 1.0,
            specular_color: [1.0, 1.0, 1.0],
            specular_ior: 1.5,
            transmission: 0.0,
            transmission_color: [1.0, 1.0, 1.0],
            emission: 0.0,
            emission_color: [1.0, 1.0, 1.0],
            coat: 0.0,
            coat_roughness: 0.1,
            coat_color: [1.0, 1.0, 1.0],
            sheen: 0.0,
            sheen_roughness: 0.3,
            sheen_color: [1.0, 1.0, 1.0],
            subsurface: 0.0,
            subsurface_color: [1.0, 1.0, 1.0],
            subsurface_scale: 1.0,
            subsurface_radius: [1.0, 1.0, 1.0],
            subsurface_anisotropy: 0.0,
            binding_targets: vec!["*".into()],
            base_color_texture_dependency_id: None,
            roughness_texture_dependency_id: None,
            metallic_texture_dependency_id: None,
            emission_texture_dependency_id: None,
            normal_texture_dependency_id: None,
            opacity_texture_dependency_id: None,
            transmission_texture_dependency_id: None,
            procedural_base_color: None,
            procedural_roughness: None,
            uv_mode: None,
        }
    }
}
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AssetOverride {
    pub original_path: String,
    pub replacement_dependency_id: String,
}
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct NativePreparation {
    pub policy: crate::application_profiles::PolicySnapshot,
    pub native_dependency_id: String,
    pub handoff_dependency_id: String,
    pub conversion: CoordinateDraft,
    pub report: serde_json::Value,
}
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct NativeImport {
    pub baseline_dependency_id: String,
    pub policy: crate::application_profiles::PolicySnapshot,
    pub native_dependency_id: Option<String>,
    pub handoff_dependency_id: String,
    pub conversion: CoordinateDraft,
    pub report: serde_json::Value,
}
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Reconciliation {
    pub input_dependency_id: String,
    pub layer_dependency_ids: Vec<String>,
    pub graph_revision: String,
}
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct LiveAdjustment {
    #[serde(default)]
    pub correspondence: Vec<serde_json::Value>,
    #[serde(default, skip_serializing, rename = "roundTripMs")]
    #[allow(dead_code)]
    pub transport_round_trip_ms: Option<f64>,
    pub revision: String,
    pub layer: String,
    pub edited_dependency_id: String,
    pub baseline_dependency_id: String,
    pub handoff_dependency_id: String,
    pub policy: crate::application_profiles::PolicySnapshot,
    pub conversion: CoordinateDraft,
    pub report: serde_json::Value,
    pub guards: serde_json::Value,
    pub file_revisions: Vec<serde_json::Value>,
}
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Publication {
    #[serde(default)]
    pub revision: String,
    #[serde(default)]
    pub reconciliation: Option<Reconciliation>,
    pub dependency_id: String,
    pub edit_layer_dependency_id: String,
    pub edited_dependency_id: String,
    pub baseline_dependency_id: String,
    pub handoff_dependency_id: String,
    pub policy: crate::application_profiles::PolicySnapshot,
    pub conversion: CoordinateDraft,
    pub report: serde_json::Value,
    pub source_revisions: Vec<serde_json::Value>,
    pub correspondence: Vec<serde_json::Value>,
}
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Handoff {
    #[serde(default)]
    pub selection: Option<Vec<String>>,
    #[serde(default)]
    pub input_edges: std::collections::BTreeMap<String, String>,
    pub policy: crate::application_profiles::PolicySnapshot,
    pub dependency_id: String,
    pub layer_dependency_ids: Vec<String>,
    pub conversion: CoordinateDraft,
}
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CoordinateDraft {
    pub version: u32,
    pub app: String,
    pub up_axis: String,
    pub meters_per_unit: f64,
    pub scale: f64,
    pub factor: f64,
    pub forward: [[f64; 4]; 4],
    pub inverse: [[f64; 4]; 4],
    pub input_revision: String,
    pub graph_revision: String,
    pub usd_version: String,
}
#[derive(Serialize, Deserialize)]
pub struct Transform {
    pub translation: [f64; 3],
    pub rotation: [f64; 3],
    pub scale: [f64; 3],
}
fn validate_draft(draft: &CoordinateDraft) -> Result<(), String> {
    if draft.version != 1
        || !["Y", "Z"].contains(&draft.up_axis.as_str())
        || [draft.meters_per_unit, draft.scale, draft.factor]
            .iter()
            .any(|v| !v.is_finite() || *v <= 0.)
        || draft
            .forward
            .iter()
            .chain(draft.inverse.iter())
            .flatten()
            .any(|v| !v.is_finite())
        || draft.graph_revision.len() != 64
        || draft.input_revision.len() != 64
    {
        return Err("Invalid coordinate preparation snapshot".into());
    }
    let f = draft.factor;
    if ((draft.scale / draft.meters_per_unit) - f).abs() > 1e-9 * f {
        return Err("Coordinate factor differs from its recorded units/scale".into());
    }
    let expected = if draft.up_axis == "Y" {
        [
            [f, 0., 0., 0.],
            [0., f, 0., 0.],
            [0., 0., f, 0.],
            [0., 0., 0., 1.],
        ]
    } else {
        [
            [f, 0., 0., 0.],
            [0., 0., f, 0.],
            [0., -f, 0., 0.],
            [0., 0., 0., 1.],
        ]
    };
    for i in 0..4 {
        for j in 0..4 {
            if (draft.forward[i][j] - expected[i][j]).abs() > 1e-9 * f.max(1.) {
                return Err("Coordinate forward matrix differs from recorded convention".into());
            }
            let product: f64 = (0..4)
                .map(|k| draft.forward[i][k] * draft.inverse[k][j])
                .sum();
            if (product - if i == j { 1. } else { 0. }).abs() > 1e-8 {
                return Err("Coordinate return matrix is not the inverse".into());
            }
        }
    }
    Ok(())
}
impl Default for Transform {
    fn default() -> Self {
        Self {
            translation: [0.; 3],
            rotation: [0.; 3],
            scale: [1.; 3],
        }
    }
}
#[derive(Serialize, Deserialize)]
pub struct Edge {
    pub id: String,
    pub source: String,
    pub target: String,
    #[serde(flatten)]
    pub ui: HashMap<String, serde_json::Value>,
}
/// Canonicalize existing originals (including junctions/symlinks), otherwise
/// retain a lexical key so missing/offline Windows files still match reliably.
pub fn file_key(path: &str) -> String {
    let resolved = std::fs::canonicalize(path).unwrap_or_else(|_| std::path::PathBuf::from(path));
    let mut text = resolved.to_string_lossy().replace('\\', "/");
    if text.starts_with("//?/UNC/") {
        text = format!("//{}", &text[8..]);
    } else if text.starts_with("//?/") {
        text = text[4..].into();
    }
    let windows = text.as_bytes().get(1) == Some(&b':') || text.starts_with("//");
    let prefix = if text.starts_with("//") {
        "//"
    } else if text.starts_with('/') {
        "/"
    } else {
        ""
    };
    let mut parts: Vec<&str> = Vec::new();
    for part in text.split('/') {
        if part.is_empty() || part == "." {
            continue;
        }
        if part == ".."
            && parts
                .last()
                .is_some_and(|last| *last != ".." && !last.ends_with(':'))
        {
            parts.pop();
        } else {
            parts.push(part);
        }
    }
    let key = format!("{prefix}{}", parts.join("/"));
    if windows { key.to_lowercase() } else { key }
}
impl Project {
    /// Upgrade v1 entirely in memory. Never alter external files or the opened JSON.
    pub fn from_value(mut value: serde_json::Value) -> Result<Self, String> {
        if value.get("applicationProfiles").is_some() {
            return Err("Project-level applicationProfiles are not supported. Use application-level settings and node overrides".into());
        }
        match value.get("version").and_then(|v| v.as_u64()) {
            Some(1) => {
                let mut dependencies = Vec::new();
                let mut links: HashMap<String, String> = HashMap::new();
                let nodes = value
                    .get_mut("nodes")
                    .and_then(|v| v.as_array_mut())
                    .ok_or("Missing nodes")?;
                for node in nodes {
                    let data = node
                        .get_mut("data")
                        .and_then(|v| v.as_object_mut())
                        .ok_or("Missing node data")?;
                    for (old, new, kind) in [
                        ("path", "inputDependencyId", DependencyKind::Usd),
                        (
                            "nativeProject",
                            "nativeProjectDependencyId",
                            DependencyKind::NativeProject,
                        ),
                        (
                            "publishedPath",
                            "publishedDependencyId",
                            DependencyKind::Usd,
                        ),
                    ] {
                        let path = match data.remove(old) {
                            Some(serde_json::Value::String(path)) => path,
                            None if old != "path" => String::new(),
                            _ => return Err(format!("Invalid legacy {old}")),
                        };
                        let id = if path.trim().is_empty() {
                            None
                        } else {
                            let key = file_key(&path);
                            let id = links
                                .entry(key)
                                .or_insert_with(|| {
                                    let id = format!("dep-{}", dependencies.len() + 1);
                                    dependencies.push(Dependency {
                                        id: id.clone(),
                                        kind,
                                        path,
                                        usd_references: Vec::new(),
                                    });
                                    id
                                })
                                .clone();
                            Some(id)
                        };
                        data.insert(
                            new.into(),
                            serde_json::to_value(id).map_err(|e| e.to_string())?,
                        );
                    }
                    data.insert("assetDependencyIds".into(), serde_json::json!([]));
                }
                value["version"] = 2.into();
                value["dependencies"] =
                    serde_json::to_value(dependencies).map_err(|e| e.to_string())?;
            }
            Some(2) => {}
            _ => return Err("Unsupported project version".into()),
        }
        let project: Self = serde_json::from_value(value).map_err(|e| e.to_string())?;
        project.validate()?;
        Ok(project)
    }
    pub fn validate(&self) -> Result<(), String> {
        if self.version != 2 {
            return Err("Unsupported project version".into());
        }
        if self.nodes.len() > 10_000
            || self.edges.len() > 50_000
            || self.dependencies.len() > 100_000
        {
            return Err("Project exceeds graph/dependency limits".into());
        }
        let mut dependencies = HashMap::new();
        for dependency in &self.dependencies {
            if dependency.id.is_empty()
                || dependency.path.trim().is_empty()
                || dependencies
                    .insert(dependency.id.as_str(), &dependency.kind)
                    .is_some()
            {
                return Err("Duplicate/empty dependency ID or empty file path".into());
            }
        }
        for dependency in &self.dependencies {
            for reference in &dependency.usd_references {
                if reference.path.trim().is_empty()
                    || !dependencies.contains_key(reference.source_dependency_id.as_str())
                {
                    return Err("Invalid USD dependency provenance".into());
                }
            }
        }
        let check = |id: &str, _kind: &DependencyKind| -> Result<(), String> {
            if !dependencies.contains_key(id) {
                return Err(format!("Missing dependency: {id}"));
            }
            Ok(())
        };
        let mut ids = HashSet::new();
        let mut indegree = HashMap::new();
        let mut outgoing: HashMap<&str, Vec<&str>> = HashMap::new();
        for n in &self.nodes {
            if n.data.blender_scene.as_ref().is_some_and(|scene| {
                scene.trim().is_empty() || scene.len() > 1024 || scene.contains('\0')
            }) {
                return Err("Blender scene must be a non-empty name of at most 1024 bytes".into());
            }
            if n.data
                .transform
                .translation
                .iter()
                .chain(n.data.transform.rotation.iter())
                .chain(n.data.transform.scale.iter())
                .any(|value| !value.is_finite())
            {
                return Err("Transform values must be finite".into());
            }
            if n.id.is_empty() || !ids.insert(n.id.as_str()) {
                return Err("Duplicate or empty node ID".into());
            }
            if n.kind != "stage"
                || ![
                    "source",
                    "blender",
                    "application",
                    "transform",
                    "output",
                    "assetOverride",
                    "materialx",
                    "material_bind",
                    "light",
                    "hdri",
                    "hair",
                    "cache_edit",
                    "camera",
                    "delete",
                    "group",
                    "scatter",
                    "character",
                    "pose",
                ]
                .contains(&n.data.operation.as_str())
            {
                return Err("Unknown node type or operation".into());
            }
            if !n.data.scale.is_finite()
                || n.data.scale <= 0.0
                || !n.position.x.is_finite()
                || !n.position.y.is_finite()
                || !["preserve", "Y", "-Y", "Z", "-Z", "X", "-X"].contains(&n.data.axis.as_str())
                || !["preserve", "Y", "-Y", "Z", "-Z", "X", "-X"]
                    .contains(&n.data.front_axis.as_str())
            {
                return Err("Invalid axis, scale, or position".into());
            }
            if let Some(id) = &n.data.input_dependency_id {
                check(id, &DependencyKind::Usd)?;
            }
            if let Some(id) = &n.data.published_dependency_id {
                check(id, &DependencyKind::Usd)?;
            }
            if let Some(id) = &n.data.native_project_dependency_id {
                check(id, &DependencyKind::NativeProject)?;
            }
            if let Some(id) = &n.data.app_executable_dependency_id {
                check(id, &DependencyKind::Executable)?;
            }
            if n.data
                .application_meters_per_unit
                .is_some_and(|v| !v.is_finite() || v <= 0.0)
            {
                return Err("Application meters per unit must be positive and finite".into());
            }
            let mut scope = HashSet::new();
            if n.data.edit_scope.iter().any(|s| {
                !crate::application_profiles::SCOPES.contains(&s.as_str()) || !scope.insert(s)
            }) {
                return Err("Unknown or duplicate node edit scope".into());
            }
            if let Some(scatter) = &n.data.scatter {
                if scatter.count == 0 || scatter.count > 1_000_000 {
                    return Err("Scatter count must be between 1 and 1,000,000".into());
                }
                if !["random", "grid", "circle", "surface"].contains(&scatter.distribution.as_str())
                {
                    return Err(
                        "Scatter distribution must be random, grid, circle, or surface".into(),
                    );
                }
                if scatter.area_size.iter().any(|v| !v.is_finite() || *v < 0.0)
                    || !scatter.scale_min.is_finite()
                    || !scatter.scale_max.is_finite()
                    || scatter.scale_min <= 0.0
                    || scatter.scale_max < scatter.scale_min
                    || !scatter.max_slope_deg.is_finite()
                    || !(0.0..=180.0).contains(&scatter.max_slope_deg)
                    || !scatter.min_spacing.is_finite()
                    || scatter.min_spacing < 0.0
                {
                    return Err("Invalid scatter area, slope, spacing, or scale bounds".into());
                }
                if scatter.prototypes.len() > 1000
                    || scatter.prototypes.iter().any(|p| p.id.trim().is_empty())
                {
                    return Err("Invalid scatter prototype list".into());
                }
            }
            if let Some(group_name) = &n.data.group_name {
                if group_name.len() > 128 {
                    return Err("Group name must be 128 characters or fewer".into());
                }
            }
            if n.data.operation == "application"
                && n.data.published_dependency_id.is_some()
                && n.data.publication.is_none()
            {
                return Err(
                    "Application output must be a reviewed publication, not a raw export link"
                        .into(),
                );
            }
            if let Some(policy) = &n.data.publication_policy {
                policy.validate()?;
            }
            if let Some(live) = &n.data.live_adjustment {
                if n.data.operation != "application"
                    || live.revision.is_empty()
                    || !live.layer.starts_with("#usda")
                {
                    return Err("Invalid live adjustment".into());
                }
                for id in [
                    &live.edited_dependency_id,
                    &live.baseline_dependency_id,
                    &live.handoff_dependency_id,
                ] {
                    check(id, &DependencyKind::Usd)?;
                }
                live.policy.validate()?;
                validate_draft(&live.conversion)?;
            }
            if let Some(publication) = &n.data.publication {
                if let Some(reconciliation) = &publication.reconciliation {
                    check(&reconciliation.input_dependency_id, &DependencyKind::Usd)?;
                    for id in &reconciliation.layer_dependency_ids {
                        check(id, &DependencyKind::Usd)?;
                    }
                    if reconciliation.graph_revision.len() != 64
                        || !reconciliation
                            .graph_revision
                            .bytes()
                            .all(|b| b.is_ascii_hexdigit())
                    {
                        return Err("Invalid reconciled graph revision".into());
                    }
                }
                publication.policy.validate()?;
                validate_draft(&publication.conversion)?;
                for id in [
                    &publication.dependency_id,
                    &publication.edit_layer_dependency_id,
                    &publication.edited_dependency_id,
                    &publication.baseline_dependency_id,
                    &publication.handoff_dependency_id,
                ] {
                    check(id, &DependencyKind::Usd)?;
                }
                if n.data.published_dependency_id.as_ref() != Some(&publication.dependency_id) {
                    return Err("Publication output link differs from validated output".into());
                }
            }
            if let Some(import) = &n.data.native_import {
                check(&import.baseline_dependency_id, &DependencyKind::Usd)?;
                import.policy.validate()?;
                check(
                    import
                        .native_dependency_id
                        .as_deref()
                        .ok_or("Native import requires a native project")?,
                    &DependencyKind::NativeProject,
                )?;
                check(&import.handoff_dependency_id, &DependencyKind::Usd)?;
                validate_draft(&import.conversion)?;
                if import
                    .report
                    .get("adapterVersion")
                    .and_then(serde_json::Value::as_u64)
                    != Some(2)
                    || import
                        .report
                        .get("blenderVersion")
                        .and_then(serde_json::Value::as_str)
                        .is_none()
                {
                    return Err("Invalid native import provenance".into());
                }
            }
            if let Some(prep) = &n.data.native_preparation {
                check(&prep.native_dependency_id, &DependencyKind::NativeProject)?;
                check(&prep.handoff_dependency_id, &DependencyKind::Usd)?;
                prep.policy.validate()?;
                validate_draft(&prep.conversion)?;
            }
            if let Some(baseline) = &n.data.manual_baseline {
                check(&baseline.baseline_dependency_id, &DependencyKind::Usd)?;
                if let Some(id) = &baseline.native_dependency_id {
                    check(id, &DependencyKind::NativeProject)?;
                }
                check(&baseline.handoff_dependency_id, &DependencyKind::Usd)?;
                baseline.policy.validate()?;
                validate_draft(&baseline.conversion)?;
                if baseline
                    .report
                    .get("origin")
                    .and_then(serde_json::Value::as_str)
                    != Some("manual")
                    || baseline
                        .report
                        .get("baselineDigest")
                        .and_then(serde_json::Value::as_str)
                        .is_none_or(|s| s.len() != 64 || !s.bytes().all(|b| b.is_ascii_hexdigit()))
                {
                    return Err("Invalid manual baseline record".into());
                }
            }
            if let Some(handoff) = &n.data.handoff {
                handoff.policy.validate()?;
                check(&handoff.dependency_id, &DependencyKind::Usd)?;
                for id in &handoff.layer_dependency_ids {
                    check(id, &DependencyKind::Usd)?;
                }
                validate_draft(&handoff.conversion)?;
            }
            if let Some(draft) = &n.data.coordinate_draft {
                validate_draft(draft)?;
            }
            for selection in [
                n.data.handoff_selection.as_ref(),
                n.data.handoff.as_ref().and_then(|h| h.selection.as_ref()),
            ]
            .into_iter()
            .flatten()
            {
                if selection.len() > 1000
                    || selection
                        .iter()
                        .any(|p| !p.starts_with('/') || p == "/" || p.len() > 4096)
                {
                    return Err("Invalid handoff object selection".into());
                }
            }
            if n.data.delete_paths.len() > 1000
                || n.data.delete_paths.iter().any(|p| {
                    !p.starts_with('/')
                        || p == "/"
                        || p.contains('.')
                        || p.contains('{')
                        || p.contains('}')
                        || p.contains('[')
                        || p.contains(']')
                        || p.contains("//")
                })
            {
                return Err("Delete targets must be absolute USD prim paths".into());
            }
            if let Some(pose) = &n.data.pose {
                let mut entry_ids = HashSet::new();
                let mut mapping_ids = HashSet::new();
                if pose.entries.len() > 128
                    || pose.entries.iter().any(|e| {
                        e.id.is_empty()
                            || !entry_ids.insert(&e.id)
                            || e.name.len() > 256
                            || !["pose", "clip"].contains(&e.mode.as_str())
                            || !e.pose_time.is_finite()
                            || (e.joints.is_none() && !e.animation_path.starts_with('/'))
                            || e.animation_path.len() > 4096
                    })
                    || pose.mappings.len() > 128
                    || pose.mappings.iter().any(|m| {
                        m.id.is_empty()
                            || !mapping_ids.insert(&m.id)
                            || m.skeleton_path.len() > 4096
                            || !m.skeleton_path.starts_with('/')
                            || m.entry_id
                                .as_ref()
                                .is_some_and(|id| !pose.entries.iter().any(|e| &e.id == id))
                    })
                {
                    return Err("Invalid Pose library or active entry".into());
                }
                for entry in &pose.entries {
                    if let Some(joints) = &entry.joints {
                        let mut names = HashSet::new();
                        if entry.blend_shapes.len() != entry.blend_shape_weights.len()
                            || entry.blend_shape_weights.iter().any(|v| !v.is_finite())
                            || entry.mode != "pose"
                            || entry.dependency_id.is_some()
                            || joints.is_empty()
                            || joints.len() > 20000
                            || joints.iter().any(|j| {
                                j.name.is_empty()
                                    || !names.insert(&j.name)
                                    || j.translation
                                        .iter()
                                        .chain(j.rotation.iter())
                                        .chain(j.scale.iter())
                                        .chain(j.rotation_offset.iter())
                                        .chain(j.translation_offset.iter())
                                        .any(|v| !v.is_finite())
                                    || j.rotation.iter().map(|v| v * v).sum::<f64>() < 1e-12
                            })
                        {
                            return Err("Invalid authored Pose joints".into());
                        }
                    }
                }
                for entry in &pose.entries {
                    if let Some(id) = &entry.dependency_id {
                        check(id, &DependencyKind::Usd)?;
                    }
                }
            }
            if n.data.operation == "character" {
                if let Some(ch) = &n.data.character {
                    let mut anim_ids = HashSet::new();
                    if ch.animation_overrides.len() > 64
                        || ch.animation_overrides.iter().any(|r| {
                            r.id.is_empty()
                                || !anim_ids.insert(&r.id)
                                || !["pose", "clip"].contains(&r.mode.as_str())
                                || !r.pose_time.is_finite()
                        })
                    {
                        return Err("Invalid character animation overrides".into());
                    }
                    let mut ja_ids = HashSet::new();
                    if ch.joint_attachments.len() > 256
                        || ch.joint_attachments.iter().any(|r| {
                            r.id.is_empty()
                                || !ja_ids.insert(&r.id)
                                || r.target_query.len() > 512
                                || r.offset_translation.iter().any(|v| !v.is_finite())
                                || r.offset_rotation.iter().any(|v| !v.is_finite())
                                || r.offset_scale.iter().any(|v| !v.is_finite() || *v == 0.0)
                        })
                    {
                        return Err("Invalid character joint attachment rules".into());
                    }
                    let mut sb_ids = HashSet::new();
                    if ch.skeleton_bindings.len() > 256
                        || ch.skeleton_bindings.iter().any(|r| {
                            r.id.is_empty() || !sb_ids.insert(&r.id) || r.target_query.len() > 512
                        })
                    {
                        return Err("Invalid character skeleton binding rules".into());
                    }
                    let mut cc_ids = HashSet::new();
                    if ch.cloth_caches.len() > 256 {
                        return Err("Too many character cloth cache entries".into());
                    }
                    for cc in &ch.cloth_caches {
                        if cc.id.is_empty()
                            || !cc_ids.insert(&cc.id)
                            || !cc.scale.is_finite()
                            || cc.scale <= 0.0
                            || !cc.time_offset.is_finite()
                            || !["preserve", "Y", "-Y", "Z", "-Z", "X", "-X"]
                                .contains(&cc.axis.as_str())
                            || !["preserve", "Y", "-Y", "Z", "-Z", "X", "-X"]
                                .contains(&cc.front_axis.as_str())
                        {
                            return Err("Invalid character cloth cache settings".into());
                        }
                        if let Some(did) = &cc.dependency_id {
                            if !did.is_empty() {
                                check(did, &DependencyKind::Usd)?;
                            }
                        }
                    }
                }
            }
            if n.data.operation == "material_bind" {
                let mut rule_ids = HashSet::new();
                if let Some(rules) = &n.data.material_bindings {
                    if rules.len() > 256
                        || rules.iter().any(|r| {
                            r.id.is_empty()
                                || !rule_ids.insert(&r.id)
                                || r.target_query.trim().is_empty()
                                || r.target_query.len() > 512
                                || r.material_path.trim().is_empty()
                                || !r.material_path.starts_with('/')
                                || !["strongerThanDescendants", "weakerThanDescendants"]
                                    .contains(&r.strength.as_str())
                        })
                    {
                        return Err("Invalid material binding rules".into());
                    }
                }
            }
            if n.data.operation == "hdri" {
                if let Some(h) = &n.data.hdri {
                    if let Some(tid) = &h.texture_dependency_id {
                        if !tid.is_empty() {
                            check(tid, &DependencyKind::Asset)?;
                        }
                    }
                    if !h.intensity.is_finite()
                        || h.intensity < 0.0
                        || !h.exposure.is_finite()
                        || !h.rotation.is_finite()
                        || h.color.iter().any(|v| !v.is_finite() || *v < 0.0)
                        || !["latlong", "automatic", "angular", "mirroredBall"]
                            .contains(&h.texture_format.as_str())
                    {
                        return Err("Invalid HDRI settings".into());
                    }
                }
            }
            if n.data.operation == "cache_edit" {
                if let Some(ce) = &n.data.cache_edit {
                    if ce.temporal_smooth_frames > 30
                        || !ce.temporal_smooth_strength.is_finite()
                        || ce.temporal_smooth_strength < 0.0
                        || ce.temporal_smooth_strength > 1.0
                        || ce.cloth_substeps == 0
                        || ce.cloth_substeps > 32
                        || ce.cloth_iterations == 0
                        || ce.cloth_iterations > 64
                        || !ce.cloth_stretch_stiffness.is_finite()
                        || ce.cloth_stretch_stiffness < 0.0
                        || ce.cloth_stretch_stiffness > 1.0
                        || !ce.cloth_bend_stiffness.is_finite()
                        || ce.cloth_bend_stiffness < 0.0
                        || ce.cloth_bend_stiffness > 1.0
                        || !ce.cloth_damping.is_finite()
                        || ce.cloth_damping < 0.0
                        || ce.cloth_damping > 1.0
                        || !ce.cloth_gravity.is_finite()
                        || !ce.cloth_collision_margin.is_finite()
                        || ce.cloth_collision_margin < 0.0
                        || !ce.cloth_friction.is_finite()
                        || ce.cloth_friction < 0.0
                        || ce.cloth_friction > 1.0
                        || !ce.cloth_drag_pin_falloff.is_finite()
                        || ce.cloth_drag_pin_falloff < 1.0
                        || ce.cloth_drag_pin_falloff > 10.0
                        || !ce.cloth_sim_scale.is_finite()
                        || ce.cloth_sim_scale <= 0.0
                        || ce.cloth_sim_scale > 10000.0
                        || !["animated", "static"].contains(&ce.sim_mode.as_str())
                        || !ce.sim_start_frame.is_finite()
                        || !ce.sim_end_frame.is_finite()
                        || ce.sim_end_frame < ce.sim_start_frame
                        || (ce.sim_end_frame - ce.sim_start_frame) > 10000.0
                        || !ce.sim_fps.is_finite()
                        || ce.sim_fps <= 0.0
                        || ce.sim_fps > 240.0
                        || ce.sim_settle_steps > 500
                        || !["custom", "silk", "cotton", "denim", "wool", "leather"]
                            .contains(&ce.sim_fabric_preset.as_str())
                        || !ce.sim_air_drag.is_finite()
                        || ce.sim_air_drag < 0.0
                        || ce.sim_air_drag > 5.0
                        || !ce.sim_wind.iter().all(|v| v.is_finite())
                        || !ce.sim_self_thickness.is_finite()
                        || ce.sim_self_thickness < 0.0
                        || ce.sim_self_thickness > 1.0
                    {
                        return Err("Invalid cache edit physics or temporal settings".into());
                    }
                    for layer in &ce.layers {
                        if layer.id.trim().is_empty()
                            || !layer.weight.is_finite()
                            || layer.weight < -10.0
                            || layer.weight > 10.0
                            || !["allFrames", "frameRange", "singleFrame"]
                                .contains(&layer.mode.as_str())
                            || !layer.center_frame.is_finite()
                            || !layer.fade_in.is_finite()
                            || layer.fade_in < 0.0
                            || !layer.fade_out.is_finite()
                            || layer.fade_out < 0.0
                            || !["smoothstep", "gaussian", "linear", "constant"]
                                .contains(&layer.falloff.as_str())
                        {
                            return Err("Invalid cache edit layer settings".into());
                        }
                    }
                }
            }
            if n.data.operation == "hair" {
                if let Some(hair) = &n.data.hair {
                    if !["multiply", "set"].contains(&hair.width_mode.as_str())
                        || !hair.width.is_finite()
                        || hair.width <= 0.0
                        || !hair.taper.is_finite()
                        || hair.taper < 0.0
                        || hair.taper > 1.0
                        || !hair.root_scale.is_finite()
                        || hair.root_scale <= 0.0
                        || hair.root_scale > 10.0
                    {
                        return Err("Invalid hair width or taper settings".into());
                    }
                    if !["static", "animated"].contains(&hair.sim_mode.as_str())
                        || hair.settle_steps == 0
                        || hair.settle_steps > 500
                        || !hair.start_frame.is_finite()
                        || !hair.end_frame.is_finite()
                        || hair.end_frame < hair.start_frame
                        || (hair.end_frame - hair.start_frame) > 10000.0
                        || hair.substeps == 0
                        || hair.substeps > 64
                        || hair.iterations == 0
                        || hair.iterations > 128
                        || !hair.stiffness.is_finite()
                        || hair.stiffness < 0.0
                        || hair.stiffness > 1.0
                        || !hair.bend_stiffness.is_finite()
                        || hair.bend_stiffness < 0.0
                        || hair.bend_stiffness > 1.0
                        || !hair.damping.is_finite()
                        || hair.damping < 0.0
                        || hair.damping > 1.0
                        || !hair.gravity.is_finite()
                        || hair.gravity < -100.0
                        || hair.gravity > 100.0
                        || hair.wind.iter().any(|w| !w.is_finite() || w.abs() > 200.0)
                        || !hair.collision_margin.is_finite()
                        || hair.collision_margin < 0.0
                        || hair.collision_margin > 1.0
                        || !hair.friction.is_finite()
                        || hair.friction < 0.0
                        || hair.friction > 1.0
                    {
                        return Err("Invalid hair simulation settings".into());
                    }
                    for (prim_path, delta) in &hair.groom_deltas {
                        if prim_path.trim().is_empty()
                            || delta.point_count == 0
                            || delta.curve_count == 0
                            || delta.deltas_z_b64.trim().is_empty()
                        {
                            return Err("Invalid hair groom delta entry".into());
                        }
                    }
                }
            }
            if n.data.operation == "light" {
                let mut light_ids = HashSet::new();
                if let Some(overrides) = &n.data.light_overrides {
                    if overrides.len() > 256
                        || overrides.iter().any(|o| {
                            o.id.is_empty()
                                || !light_ids.insert(&o.id)
                                || o.light_path.trim().is_empty()
                                || !o.light_path.starts_with('/')
                                || o.intensity.map_or(false, |v| !v.is_finite() || v < 0.)
                                || o.exposure.map_or(false, |v| !v.is_finite())
                                || o.color
                                    .map_or(false, |c| c.iter().any(|v| !v.is_finite() || *v < 0.))
                                || o.color_temperature
                                    .map_or(false, |v| !v.is_finite() || v < 0.)
                        })
                    {
                        return Err("Invalid light overrides".into());
                    }
                }
            }
            if n.data.operation == "materialx" {
                let mut rule_ids = HashSet::new();
                if let Some(rules) = &n.data.material_x_rules {
                    if rules.len() > 256
                        || rules.iter().any(|r| {
                            r.id.is_empty()
                                || !rule_ids.insert(&r.id)
                                || !["simple", "regex"].contains(&r.search_type.as_str())
                                || r.query.len() > 512
                        })
                    {
                        return Err("Invalid MaterialX search rules".into());
                    }
                }
                for m in std::iter::once(&n.data.material_x).chain(
                    n.data
                        .material_x_rules
                        .iter()
                        .flat_map(|rules| rules.iter().map(|r| &r.material)),
                ) {
                    if m.name.is_empty()
                        || m.name.len() > 100
                        || !m.name.chars().enumerate().all(|(i, c)| {
                            c == '_' || c.is_ascii_alphabetic() || (i > 0 && c.is_ascii_digit())
                        })
                        || m.base_color
                            .iter()
                            .chain([&m.metalness, &m.roughness, &m.specular, &m.opacity])
                            .any(|v| !v.is_finite() || *v < 0. || *v > 1.)
                        || m.binding_targets.is_empty()
                        || m.binding_targets.len() > 1000
                        || m.binding_targets.iter().any(|p| {
                            p != "*" && (!p.starts_with('/') || p.contains('.') || p.contains('{'))
                        })
                        || m.uv_mode
                            .as_deref()
                            .is_some_and(|mode| !matches!(mode, "auto" | "root" | "strand"))
                    {
                        return Err("Invalid MaterialX material parameters or binding paths".into());
                    }
                    for id in m.texture_dependencies() {
                        check(id, &DependencyKind::Asset)?;
                    }
                }
            }
            let mut overrides = HashSet::new();
            for item in &n.data.asset_overrides {
                check(&item.replacement_dependency_id, &DependencyKind::Asset)?;
                if item.original_path.trim().is_empty()
                    || !overrides.insert(item.original_path.to_lowercase())
                {
                    return Err("Empty or duplicate asset override".into());
                }
            }
            let mut assets = HashSet::new();
            for id in &n.data.asset_dependency_ids {
                check(id, &DependencyKind::Asset)?;
                if !assets.insert(id) {
                    return Err("Duplicate node asset reference".into());
                }
            }
            indegree.insert(n.id.as_str(), 0usize);
        }
        if let Some(id) = &self.viewport.pinned_node_id {
            if !ids.contains(id.as_str()) {
                return Err("Pinned viewport node does not exist".into());
            }
        }
        if let Some(axis) = &self.viewport.up_axis {
            if axis != "Y" && axis != "Z" {
                return Err("Viewport upAxis must be Y or Z".into());
            }
        }
        if let Some(mode) = &self.viewport.ovrtx_mode {
            if mode != "realtime" && mode != "pathTracing" {
                return Err("Viewport ovrtxMode must be realtime or pathTracing".into());
            }
        }
        if let Some(mat) = &self.viewport.materials {
            if !matches!(mat.as_str(), "auto" | "scene" | "solid") {
                return Err("Viewport materials must be auto, scene, or solid".into());
            }
        }
        let mut edge_ids = HashSet::new();
        for e in &self.edges {
            if e.id.is_empty()
                || !edge_ids.insert(&e.id)
                || !ids.contains(e.source.as_str())
                || !ids.contains(e.target.as_str())
            {
                return Err("Duplicate edge ID or dangling edge".into());
            }
            *indegree.get_mut(e.target.as_str()).unwrap() += 1;
            outgoing.entry(&e.source).or_default().push(&e.target);
        }
        let mut queue: VecDeque<_> = indegree
            .iter()
            .filter(|(_, d)| **d == 0)
            .map(|(id, _)| *id)
            .collect();
        let mut visited = 0;
        while let Some(id) = queue.pop_front() {
            visited += 1;
            for target in outgoing.get(id).into_iter().flatten() {
                let degree = indegree.get_mut(target).unwrap();
                *degree -= 1;
                if *degree == 0 {
                    queue.push_back(target);
                }
            }
        }
        if visited != self.nodes.len() {
            return Err("Graph contains a cycle".into());
        }
        if let Some(pg) = &self.procedural_graph {
            validate_procedural_graph(pg)?;
        }
        Ok(())
    }
}

fn validate_procedural_graph(pg: &ProceduralGraph) -> Result<(), String> {
    if pg.nodes.len() > 2_000 || pg.edges.len() > 10_000 {
        return Err("Procedural graph exceeds node/edge limits".into());
    }
    let mut ids = HashSet::new();
    let mut indegree = HashMap::new();
    let mut outgoing: HashMap<&str, Vec<&str>> = HashMap::new();
    for n in &pg.nodes {
        if n.id.is_empty() || !ids.insert(n.id.as_str()) {
            return Err("Duplicate or empty procedural node ID".into());
        }
        if !n.position.x.is_finite() || !n.position.y.is_finite() {
            return Err("Procedural node position must be finite".into());
        }
        if !PROCEDURAL_NODE_KINDS.contains(&n.data.kind.as_str()) {
            return Err(format!("Unknown procedural node kind: {}", n.data.kind));
        }
        if let Some(mapping) = &n.data.mapping_type {
            if !matches!(
                mapping.as_str(),
                "replace" | "multiply" | "add" | "mix" | "bump_normal"
            ) {
                return Err(format!("Invalid procedural mappingType: {mapping}"));
            }
        }
        if let Some(strength) = n.data.strength {
            if !strength.is_finite() {
                return Err("Procedural strength must be finite".into());
            }
        }
        indegree.insert(n.id.as_str(), 0usize);
    }
    let mut edge_ids = HashSet::new();
    for e in &pg.edges {
        if e.id.is_empty()
            || !edge_ids.insert(e.id.as_str())
            || e.source == e.target
            || !ids.contains(e.source.as_str())
            || !ids.contains(e.target.as_str())
        {
            return Err("Duplicate, self-looping, or dangling procedural edge".into());
        }
        *indegree.get_mut(e.target.as_str()).unwrap() += 1;
        outgoing
            .entry(e.source.as_str())
            .or_default()
            .push(e.target.as_str());
    }
    let mut queue: VecDeque<_> = indegree
        .iter()
        .filter(|(_, d)| **d == 0)
        .map(|(id, _)| *id)
        .collect();
    let mut visited = 0;
    while let Some(id) = queue.pop_front() {
        visited += 1;
        for target in outgoing.get(id).into_iter().flatten() {
            let degree = indegree.get_mut(target).unwrap();
            *degree -= 1;
            if *degree == 0 {
                queue.push_back(target);
            }
        }
    }
    if visited != pg.nodes.len() {
        return Err("Procedural graph contains a cycle".into());
    }
    Ok(())
}
pub fn save(path: &str, project: &Project) -> Result<(), String> {
    project.validate()?;
    let extension = std::path::Path::new(path)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if !["sublayer", "json"].contains(&extension.as_str()) {
        return Err("Save a Sublayer JSON (.sublayer or .json) file".into());
    }
    let target = file_key(path);
    if project
        .dependencies
        .iter()
        .any(|dependency| file_key(&dependency.path) == target)
    {
        return Err("The project JSON cannot overwrite a linked external file".into());
    }
    let bytes = serde_json::to_vec_pretty(project).map_err(|e| e.to_string())?;
    if bytes.len() > 20_000_000 {
        return Err(
            "Project exceeds the 20 MB persistence limit; previous file was preserved".into(),
        );
    }
    std::fs::write(path, bytes).map_err(|e| e.to_string())
}
pub fn load(path: &str) -> Result<Project, String> {
    let file = std::fs::File::open(path).map_err(|e| e.to_string())?;
    if file.metadata().map_err(|e| e.to_string())?.len() > 20_000_000 {
        return Err("Project exceeds 20 MB limit".into());
    }
    Project::from_value(serde_json::from_reader(file).map_err(|e| e.to_string())?)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn materialx_parameters_and_texture_dependencies_validate() {
        let mut p = Project::from_value(legacy()).unwrap();
        p.nodes[0].data.operation = "materialx".into();
        p.validate().unwrap();
        p.nodes[0].data.material_x.roughness = 1.1;
        assert!(p.validate().is_err());
        p.nodes[0].data.material_x.roughness = 0.3;
        p.nodes[0].data.material_x.base_color_texture_dependency_id = Some("missing".into());
        assert!(p.validate().is_err());
        p.nodes[0].data.material_x.base_color_texture_dependency_id = None;
        p.nodes[0].data.material_x.binding_targets = vec!["relative".into()];
        assert!(p.validate().is_err());

        let legacy_matx = serde_json::json!({
            "name": "LegacyMaterial",
            "baseColor": [0.5, 0.5, 0.5],
            "metalness": 0.0,
            "roughness": 0.5,
            "specular": 0.5,
            "opacity": 1.0,
            "specularColor": null,
            "coatColor": null,
            "bindingTargets": ["*"],
            "baseColorTextureDependencyId": null,
            "roughnessTextureDependencyId": null
        });
        let deserialized: MaterialXSettings = serde_json::from_value(legacy_matx).unwrap();
        assert_eq!(deserialized.specular_color, [1.0, 1.0, 1.0]);
        assert_eq!(deserialized.coat_color, [1.0, 1.0, 1.0]);
    }
    #[test]
    fn viewport_pin_and_usd_reference_provenance_round_trip() {
        let mut p = project();
        p.viewport.pinned_node_id = Some("b".into());
        let source_id = p.dependencies[0].id.clone();
        p.dependencies[1].usd_references.push(UsdReference {
            source_dependency_id: source_id,
            path: "I:/Texture/missing.png".into(),
        });
        let loaded = Project::from_value(serde_json::to_value(&p).unwrap()).unwrap();
        assert_eq!(loaded.viewport.pinned_node_id.as_deref(), Some("b"));
        assert_eq!(
            loaded.dependencies[1].usd_references[0].path,
            "I:/Texture/missing.png"
        );
        p.viewport.pinned_node_id = Some("missing".into());
        assert!(p.validate().is_err());
    }
    #[test]
    fn file_keys_match_windows_aliases() {
        assert_eq!(
            file_key("Z:/offline/folder/../Asset.usd"),
            file_key("z:\\offline\\asset.usd")
        );
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        assert_eq!(
            file_key(&root.join("Cargo.toml").to_string_lossy()),
            file_key(&root.join("src/../Cargo.toml").to_string_lossy())
        );
    }
    #[test]
    fn saves_only_project_json_and_never_overwrites_dependencies() {
        let folder = std::env::temp_dir().join(format!(
            "orchflow-json-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&folder).unwrap();
        let usd = folder.join("original.usd");
        let native = folder.join("original.json");
        let json = folder.join("scene.sublayer");
        std::fs::write(&usd, b"original USD bytes").unwrap();
        std::fs::write(&native, b"original native bytes").unwrap();
        let mut p = project();
        p.dependencies[0].path = usd.to_string_lossy().into_owned();
        p.dependencies[1].path = native.to_string_lossy().into_owned();
        save(&json.to_string_lossy(), &p).unwrap();
        assert_eq!(load(&json.to_string_lossy()).unwrap().dependencies.len(), 2);
        assert!(save(&usd.to_string_lossy(), &p).is_err());
        assert!(save(&native.to_string_lossy(), &p).is_err());
        assert_eq!(std::fs::read(&usd).unwrap(), b"original USD bytes");
        assert_eq!(std::fs::read(&native).unwrap(), b"original native bytes");
        assert_eq!(
            std::fs::read_dir(&folder).unwrap().count(),
            3,
            "no managed folders or asset copies"
        );
        for path in [usd, native, json] {
            std::fs::remove_file(path).unwrap();
        }
        std::fs::remove_dir(folder).unwrap();
    }
    fn legacy() -> serde_json::Value {
        serde_json::json!({"version":1,"nodes":[{"id":"a","type":"stage","position":{"x":0,"y":0},"selected":true,"data":{"label":"a","path":"I:/original.usd","operation":"source","app":"","axis":"preserve","scale":1}},{"id":"b","type":"stage","position":{"x":1,"y":0},"data":{"label":"b","path":"I:/original.usd","nativeProject":"I:/project.blend","operation":"application","app":"Blender","axis":"Y","scale":1}}],"edges":[{"id":"ab","source":"a","target":"b"}]})
    }
    fn project() -> Project {
        Project::from_value(legacy()).unwrap()
    }
    #[test]
    fn validates_dag() {
        assert!(project().validate().is_ok());
    }
    #[test]
    fn migrates_legacy_and_deduplicates_original_file_links() {
        let p = project();
        assert_eq!(p.version, 2);
        assert_eq!(p.dependencies.len(), 2);
        assert_eq!(
            p.nodes[0].data.input_dependency_id,
            p.nodes[1].data.input_dependency_id
        );
        assert_eq!(p.dependencies[0].path, "I:/original.usd");
        assert_eq!(p.nodes[0].ui["selected"], true);
        let json = serde_json::to_value(&p).unwrap();
        assert!(json["nodes"][0]["data"].get("path").is_none());
        assert!(json["nodes"][1]["data"].get("nativeProject").is_none());
        assert!(Project::from_value(json).is_ok());
    }
    #[test]
    fn rejects_cycle() {
        let mut p = project();
        p.edges.push(Edge {
            id: "ba".into(),
            source: "b".into(),
            target: "a".into(),
            ui: HashMap::new(),
        });
        assert!(p.validate().unwrap_err().contains("cycle"));
    }
    #[test]
    fn rejects_dangling_edge() {
        let mut p = project();
        p.nodes.pop();
        assert!(p.validate().is_err());
    }
    #[test]
    fn rejects_invalid_conversion() {
        let mut p = project();
        p.nodes[0].data.scale = 0.;
        assert!(p.validate().is_err());
    }
    #[test]
    fn rejects_missing_dependency() {
        let mut p = project();
        p.nodes[0].data.input_dependency_id = Some("missing".into());
        assert!(p.validate().is_err());
    }
    #[test]
    fn rejects_duplicate_dependency_ids() {
        let mut p = project();
        p.dependencies[1].id = p.dependencies[0].id.clone();
        assert!(p.validate().is_err());
    }
    #[test]
    fn accepts_offline_dependencies_without_touching_originals() {
        let mut p = project();
        p.dependencies[0].path = "Z:/offline/location.usd".into();
        assert!(p.validate().is_ok());
    }
    #[test]
    fn asset_overrides_round_trip_and_validate_replacement_links() {
        let mut p = project();
        p.nodes[1].data.operation = "assetOverride".into();
        p.nodes[1].data.asset_overrides.push(AssetOverride {
            original_path: "I:/missing.png".into(),
            replacement_dependency_id: p.dependencies[0].id.clone(),
        });
        let saved = serde_json::to_value(&p).unwrap();
        let restored = Project::from_value(saved).unwrap();
        assert_eq!(
            restored.nodes[1].data.asset_overrides[0].original_path,
            "I:/missing.png"
        );
        p.nodes[1].data.asset_overrides[0].replacement_dependency_id = "missing".into();
        assert!(p.validate().is_err());
    }
    #[test]
    fn coordinate_draft_profiles_round_trip_and_reject_invalid_settings() {
        let mut p = project();
        let identity = [
            [1., 0., 0., 0.],
            [0., 1., 0., 0.],
            [0., 0., 1., 0.],
            [0., 0., 0., 1.],
        ];
        p.nodes[0].data.coordinate_draft = Some(CoordinateDraft {
            version: 1,
            app: "Blender".into(),
            up_axis: "Y".into(),
            meters_per_unit: 1.,
            scale: 1.,
            factor: 1.,
            forward: identity,
            inverse: identity,
            input_revision: "a".repeat(64),
            graph_revision: "b".repeat(64),
            usd_version: "25.8".into(),
        });
        let conversion = serde_json::from_value(
            serde_json::to_value(p.nodes[0].data.coordinate_draft.as_ref().unwrap()).unwrap(),
        )
        .unwrap();
        p.nodes[0].data.handoff = Some(Handoff {
            selection: None,
            input_edges: std::collections::BTreeMap::new(),
            policy: crate::application_profiles::PolicySnapshot {
                profile: crate::application_profiles::Catalog::defaults()
                    .applications
                    .remove(0),
                edit_scope: vec![],
            },
            dependency_id: p.dependencies[0].id.clone(),
            layer_dependency_ids: vec![],
            conversion,
        });
        let restored = Project::from_value(serde_json::to_value(&p).unwrap()).unwrap();
        assert!(
            serde_json::to_value(&restored)
                .unwrap()
                .get("applicationProfiles")
                .is_none()
        );
        assert!(restored.nodes[0].data.handoff.is_some());
        assert_eq!(
            restored.nodes[0]
                .data
                .coordinate_draft
                .as_ref()
                .unwrap()
                .inverse,
            identity
        );
        p.nodes[0].data.application_meters_per_unit = Some(0.);
        assert!(p.validate().is_err());
        p.nodes[0].data.application_meters_per_unit = None;
        p.nodes[0].data.coordinate_draft.as_mut().unwrap().forward[0][0] = f64::NAN;
        assert!(p.validate().is_err());
    }
    #[test]
    fn rejects_project_level_profiles_without_migration() {
        let mut value = serde_json::to_value(project()).unwrap();
        value["applicationProfiles"] = serde_json::json!([]);
        assert!(
            Project::from_value(value)
                .err()
                .unwrap()
                .contains("Project-level")
        );
    }
    #[test]
    fn rejects_unknown_version() {
        let mut v = legacy();
        v["version"] = 99.into();
        assert!(Project::from_value(v).is_err());
    }
    #[test]
    fn validates_procedural_graph_dag_and_rejects_cycles_and_dangling_edges() {
        let mut p = project();
        p.procedural_graph = Some(ProceduralGraph {
            nodes: vec![
                ProceduralNode {
                    id: "n1".into(),
                    kind_type: "procedural".into(),
                    position: Position { x: 0.0, y: 0.0 },
                    data: ProceduralNodeData {
                        kind: "noise2d".into(),
                        label: Some("Noise 2D".into()),
                        params: Some(serde_json::json!({"amplitude": 1.0})),
                        material_x_node_id: None,
                        rule_id: None,
                        parameter: None,
                        mapping_type: None,
                        strength: None,
                        enabled: None,
                    },
                },
                ProceduralNode {
                    id: "out1".into(),
                    kind_type: "procedural".into(),
                    position: Position { x: 240.0, y: 0.0 },
                    data: ProceduralNodeData {
                        kind: "output".into(),
                        label: Some("Output".into()),
                        params: None,
                        material_x_node_id: Some("mx1".into()),
                        rule_id: Some("r1".into()),
                        parameter: Some("base_color".into()),
                        mapping_type: Some("mix".into()),
                        strength: Some(0.75),
                        enabled: Some(true),
                    },
                },
            ],
            edges: vec![ProceduralEdge {
                id: "e1".into(),
                source: "n1".into(),
                source_handle: Some("out".into()),
                target: "out1".into(),
                target_handle: Some("in".into()),
            }],
        });
        let val = serde_json::to_value(&p).unwrap();
        let restored = Project::from_value(val).unwrap();
        assert!(restored.procedural_graph.is_some());

        // Dangling edge
        let mut bad_dangling = project();
        bad_dangling.procedural_graph = Some(ProceduralGraph {
            nodes: p.procedural_graph.as_ref().unwrap().nodes.clone(),
            edges: vec![ProceduralEdge {
                id: "e1".into(),
                source: "n1".into(),
                source_handle: None,
                target: "missing".into(),
                target_handle: None,
            }],
        });
        assert!(bad_dangling.validate().is_err());

        // Cycle
        let mut bad_cycle = project();
        bad_cycle.procedural_graph = Some(ProceduralGraph {
            nodes: p.procedural_graph.as_ref().unwrap().nodes.clone(),
            edges: vec![
                ProceduralEdge {
                    id: "e1".into(),
                    source: "n1".into(),
                    source_handle: None,
                    target: "out1".into(),
                    target_handle: None,
                },
                ProceduralEdge {
                    id: "e2".into(),
                    source: "out1".into(),
                    source_handle: None,
                    target: "n1".into(),
                    target_handle: None,
                },
            ],
        });
        assert!(bad_cycle.validate().is_err());

        // Unknown kind
        let mut bad_kind = project();
        let mut pg = p.procedural_graph.clone().unwrap();
        pg.nodes[0].data.kind = "not_a_node".into();
        bad_kind.procedural_graph = Some(pg);
        assert!(bad_kind.validate().is_err());
    }
    #[test]
    fn pose_library_validates_and_survives_domain_graph() {
        let mut p = project();
        p.nodes[0].data.operation = "pose".into();
        p.nodes[0].data.pose = Some(PoseSettings {
            mappings: vec![PoseMapping {
                enabled: true,
                id: "body".into(),
                skeleton_path: "/Scene/Rig/Skeleton".into(),
                entry_id: Some("clip".into()),
            }],
            entries: vec![PoseEntry {
                id: "clip".into(),
                name: "Walk".into(),
                joints: None,
                blend_shapes: vec![],
                blend_shape_weights: vec![],
                dependency_id: p.nodes[0].data.input_dependency_id.clone(),
                animation_path: "/Rig/Animation".into(),
                mode: "clip".into(),
                pose_time: 13.0,
            }],
        });
        p.validate().unwrap();
        let saved = serde_json::to_value(&p).unwrap();
        let restored = Project::from_value(saved).unwrap();
        assert_eq!(restored.nodes[0].data.pose, p.nodes[0].data.pose);
        let graph = crate::application::evaluation_graph(&p, &p.nodes[0].id, false).unwrap();
        assert_eq!(graph["nodes"][0]["pose"]["mappings"][0]["entryId"], "clip");
        p.nodes[0].data.pose.as_mut().unwrap().mappings[0].entry_id = Some("missing".into());
        assert!(p.validate().is_err());
    }
}

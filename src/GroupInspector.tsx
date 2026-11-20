import { InspectorHint } from "./InspectorHint";
import { NumberInput } from "./NumberInput";
import { identityTransform, type Transform } from "./fileRegistry";

function sanitizeGroupIdentifier(raw: string): string {
  const cleaned = raw.trim().replace(/[^A-Za-z0-9_]/g, "_");
  if (!cleaned) return "Group";
  return /^[A-Za-z_]/.test(cleaned) ? cleaned : `_${cleaned}`;
}

export function GroupInspector({
  groupName,
  transform,
  onGroupNameChange,
  onTransformChange,
}: {
  groupName?: string;
  transform?: Transform;
  onGroupNameChange: (next: string) => void;
  onTransformChange: (next: Transform) => void;
}) {
  const current = transform || identityTransform;
  const resolvedPrim = `/Scene/${sanitizeGroupIdentifier(groupName || "Group")}`;

  function setAxis(field: keyof Transform, index: number, axisValue: number) {
    const vector: [number, number, number] = [...current[field]];
    vector[index] = Number.isFinite(axisValue) ? axisValue : field === "scale" ? 1 : 0;
    onTransformChange({ ...current, [field]: vector });
  }

  return (
    <div className="transform-settings">
      <label>
        <span className="field-label">
          Group Xform name
          <InspectorHint text="Wraps all connected upstream inputs under a single UsdGeom.Xform prim at /Scene/<GroupName> so the group can be positioned or selected as a unit (e.g. in Scatter)." />
        </span>
        <input
          type="text"
          placeholder="Group"
          value={groupName ?? ""}
          onChange={(e) => onGroupNameChange(e.target.value)}
        />
      </label>
      <small className="hint" style={{ fontFamily: "monospace" }}>
        Output prim: {resolvedPrim}
      </small>

      <h3 className="field-label" style={{ marginTop: 6 }}>
        Transform
        <InspectorHint text="Authored directly on the group Xform prim. Translation is in metres; rotation uses XYZ order in degrees." />
      </h3>
      {(["translation", "rotation", "scale"] as const).map((field) => (
        <div key={field}>
          <span className="vector-label">
            {field === "translation"
              ? "Translation · m"
              : field === "rotation"
                ? "Rotation · °"
                : "Scale"}
          </span>
          <div className="vector-input">
            {["X", "Y", "Z"].map((axis, index) => (
              <label key={axis}>
                {axis}
                <NumberInput
                  aria-label={`${field} ${axis}`}
                  step={field === "rotation" ? 1 : 0.1}
                  value={current[field][index]}
                  onChange={(e) => setAxis(field, index, Number(e.target.value))}
                />
              </label>
            ))}
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onTransformChange(structuredClone(identityTransform))}
      >
        Reset transform
      </button>
    </div>
  );
}

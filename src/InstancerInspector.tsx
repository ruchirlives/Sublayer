import { defaultInstancer, type InstancerSettings } from "./fileRegistry";
import { NumberInput } from "./NumberInput";
export function InstancerInspector({
  value = defaultInstancer,
  selectedId,
  editing,
  onEditingChange,
  onSelect,
  onChange,
}: {
  value: InstancerSettings;
  selectedId: string | null;
  editing: boolean;
  onEditingChange: (active: boolean) => void;
  onSelect: (id: string | null) => void;
  onChange: (value: InstancerSettings) => void;
}) {
  const selected = value.instances.find((i) => i.id === selectedId);
  return (
    <>
      <button aria-pressed={editing} onClick={() => onEditingChange(!editing)}>
        {editing ? "Stop editing instances" : "Edit instances"}
      </button>
      <small>
        In Scene mode, Alt-drag an object to instance it. Click a placement to
        edit; Delete removes it. Escape cancels a drag. Preview this node to
        edit.
      </small>
      <label>
        Movement constraint
        <select
          value={value.movement}
          onChange={(e) =>
            onChange({
              ...value,
              movement: e.target.value as InstancerSettings["movement"],
            })
          }
        >
          <option value="ground">Ground plane (lock up axis)</option>
          <option value="free">Free</option>
          <option value="x">X only</option>
          <option value="y">Y only</option>
          <option value="z">Z only</option>
          <option value="custom">Custom axis locks</option>
        </select>
      </label>
      {value.movement === "custom" && (
        <div>
          {["X", "Y", "Z"].map((axis, i) => (
            <label key={axis}>
              <input
                type="checkbox"
                checked={value.lockedAxes[i]}
                onChange={(e) => {
                  const lockedAxes = [...value.lockedAxes] as [
                    boolean,
                    boolean,
                    boolean,
                  ];
                  lockedAxes[i] = e.target.checked;
                  onChange({ ...value, lockedAxes });
                }}
              />
              Lock {axis}
            </label>
          ))}
        </div>
      )}
      <small>
        Translation constraints use world axes. Ground movement preserves
        starting height. Numeric fields allow explicit placement.
      </small>
      <label>
        Instances ({value.instances.length})
        <select
          size={Math.min(8, Math.max(2, value.instances.length))}
          value={selectedId || ""}
          onChange={(e) => onSelect(e.target.value || null)}
        >
          {value.instances.map((item, i) => (
            <option key={item.id} value={item.id}>
              {i + 1}. {item.sourcePath}
              {item.visible ? "" : " (hidden)"}
            </option>
          ))}
        </select>
      </label>
      {selected && (
        <>
          <label>
            Source prim
            <input
              value={selected.sourcePath}
              onChange={(e) =>
                onChange({
                  ...value,
                  instances: value.instances.map((item) =>
                    item.id === selected.id
                      ? { ...item, sourcePath: e.target.value }
                      : item,
                  ),
                })
              }
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={selected.visible}
              onChange={(e) =>
                onChange({
                  ...value,
                  instances: value.instances.map((item) =>
                    item.id === selected.id
                      ? { ...item, visible: e.target.checked }
                      : item,
                  ),
                })
              }
            />
            Visible
          </label>
          {(["translation", "rotation", "scale"] as const).map((field) => (
            <div key={field}>
              {field === "translation"
                ? "Translation (m)"
                : field === "rotation"
                  ? "Rotation (degrees)"
                  : "Scale"}
              <div className="vector-input">
                {[0, 1, 2].map((axis) => (
                  <label key={axis}>
                    {["X", "Y", "Z"][axis]}
                    <NumberInput
                      aria-label={`${field} ${["X", "Y", "Z"][axis]}`}
                      value={selected.transform[field][axis]}
                      min={field === "scale" ? 0.0001 : undefined}
                      step={field === "rotation" ? 1 : 0.1}
                      onChange={(e) => {
                        const v = Number(e.target.value);
                        if (
                          !Number.isFinite(v) ||
                          (field === "scale" && v <= 0)
                        )
                          return;
                        const vector = [...selected.transform[field]] as [
                          number,
                          number,
                          number,
                        ];
                        vector[axis] = v;
                        onChange({
                          ...value,
                          instances: value.instances.map((item) =>
                            item.id === selected.id
                              ? {
                                  ...item,
                                  transform: {
                                    ...item.transform,
                                    [field]: vector,
                                  },
                                }
                              : item,
                          ),
                        });
                      }}
                    />
                  </label>
                ))}
              </div>
            </div>
          ))}
          <button
            onClick={() => {
              const item = {
                ...structuredClone(selected),
                id: crypto.randomUUID(),
              };
              onChange({ ...value, instances: [...value.instances, item] });
              onSelect(item.id);
            }}
          >
            Duplicate
          </button>
          <button
            onClick={() => {
              onChange({
                ...value,
                instances: value.instances.filter(
                  (item) => item.id !== selected.id,
                ),
              });
              onSelect(null);
            }}
          >
            Delete instance
          </button>
        </>
      )}
    </>
  );
}

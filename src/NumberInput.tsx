import { useLayoutEffect, useRef, useState } from "react";
import type { ChangeEvent, InputHTMLAttributes, PointerEvent } from "react";

type NumberInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type">;

function stepDecimals(step: number): number {
  const s = String(step);
  if (s.includes("e-")) return Math.min(6, Number(s.split("e-")[1]) || 0);
  const dot = s.indexOf(".");
  return dot === -1 ? 0 : Math.min(6, s.length - dot - 1);
}

function clampAndRound(
  value: number,
  step: number,
  min?: number | string,
  max?: number | string,
): number {
  let next = value;
  const minNum = min !== undefined && min !== "" ? Number(min) : NaN;
  const maxNum = max !== undefined && max !== "" ? Number(max) : NaN;
  if (Number.isFinite(minNum)) next = Math.max(minNum, next);
  if (Number.isFinite(maxNum)) next = Math.min(maxNum, next);
  return Number(next.toFixed(stepDecimals(step)));
}

export function NumberInput({
  value,
  defaultValue,
  step,
  min,
  max,
  disabled,
  readOnly,
  onChange,
  onBlur,
  ...rest
}: NumberInputProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const dragRef = useRef<{
    startY: number;
    startValue: number;
    pointerId: number;
    lastValue: number | null;
  } | null>(null);
  const [dragging, setDragging] = useState(false);

  // Leave the focused input's text and caret in the browser's control.
  // The model stores a number, so writing it back would strip typed zeros.
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (input && value !== undefined && input.ownerDocument.activeElement !== input) {
      input.value = String(value);
    }
  }, [value]);

  const numericStep = (() => {
    const parsed = step !== undefined && step !== "any" ? Number(step) : NaN;
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0.1;
  })();

  const emitValue = (next: number) => {
    const el = inputRef.current;
    if (!el || !onChange) return;
    el.value = String(next);
    onChange({ target: el, currentTarget: el } as ChangeEvent<HTMLInputElement>);
  };

  const handlePointerDown = (e: PointerEvent<HTMLSpanElement>) => {
    if (disabled || readOnly || e.button !== 0) return;
    e.preventDefault();
    const raw =
      value !== undefined ? String(value) : (inputRef.current?.value ?? "");
    const parsed = parseFloat(raw);
    const startValue = Number.isFinite(parsed) ? parsed : 0;
    dragRef.current = {
      startY: e.clientY,
      startValue,
      pointerId: e.pointerId,
      lastValue: startValue,
    };
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragging(true);
  };

  const handlePointerMove = (e: PointerEvent<HTMLSpanElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    const deltaPx = drag.startY - e.clientY;
    const effectiveStep = e.shiftKey ? numericStep * 0.1 : numericStep;
    const steps = Math.round(deltaPx / 4);
    if (steps === 0 && drag.lastValue === drag.startValue) return;
    const next = clampAndRound(
      drag.startValue + steps * effectiveStep,
      effectiveStep,
      min,
      max,
    );
    if (next === drag.lastValue) return;
    drag.lastValue = next;
    emitValue(next);
  };

  const handlePointerUp = (e: PointerEvent<HTMLSpanElement>) => {
    if (!dragRef.current || dragRef.current.pointerId !== e.pointerId) return;
    dragRef.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    setDragging(false);
  };

  return (
    <div className="number-input-wrap">
      <input
        ref={inputRef}
        type="number"
        defaultValue={value ?? defaultValue}
        step={step}
        min={min}
        max={max}
        disabled={disabled}
        readOnly={readOnly}
        onChange={(event) => {
          const input = event.currentTarget;
          if (input.value !== "" && Number.isFinite(input.valueAsNumber)) onChange?.(event);
        }}
        onBlur={(event) => {
          onBlur?.(event);
          if (value !== undefined) event.currentTarget.value = String(value);
        }}
        {...rest}
      />
      <span
        role="slider"
        aria-label={
          rest["aria-label"]
            ? `${rest["aria-label"]} drag handle`
            : "Drag up or down to adjust"
        }
        title="Drag up/down to adjust (Shift for fine)"
        className={`number-drag-dot${dragging ? " dragging" : ""}${disabled || readOnly ? " disabled" : ""}`}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
      />
    </div>
  );
}

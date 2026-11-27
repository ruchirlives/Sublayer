/** Keep the displayed pixels until a complete decoded frame is available. */
export function paintViewportBitmap(
  canvas: HTMLCanvasElement | null,
  frame: { width: number; height: number; imageBitmap?: ImageBitmap },
): boolean {
  if (!canvas || !frame.imageBitmap) return false;
  const ctx = canvas.getContext("2d");
  if (!ctx) return false;
  // Assigning either canvas dimension clears it, even if its value is unchanged.
  if (canvas.width !== frame.width) canvas.width = frame.width;
  if (canvas.height !== frame.height) canvas.height = frame.height;
  ctx.drawImage(frame.imageBitmap, 0, 0);
  return true;
}

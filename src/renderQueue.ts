/** One render in flight, one replaceable pending view, paced to display frames.
 * Camera updates do not invalidate completed frames: displaying those frames is
 * what makes continuous dragging work. Only scene/settings changes invalidate.
 */
export class LatestRenderQueue<Request, Frame> {
  private context: string | undefined;
  private epoch = 0;
  private pending: { request: Request; epoch: number } | null = null;
  private running = false;
  private scheduled: number | null = null;
  private disposed = false;
  private options: {
    render: (request: Request) => Promise<Frame>;
    present: (frame: Frame) => void;
    error: (error: unknown) => void;
    busy: (busy: boolean) => void;
    schedule: (callback: () => void) => number;
    cancel: (id: number) => void;
  };

  constructor(options: LatestRenderQueue<Request, Frame>["options"]) {
    this.options = options;
  }

  enqueue(context: string, request: Request) {
    if (this.disposed) return;
    if (context !== this.context) {
      this.context = context;
      this.epoch++;
    }
    this.pending = { request, epoch: this.epoch };
    this.options.busy(true);
    this.schedule();
  }

  clear() {
    this.epoch++;
    this.context = undefined;
    this.pending = null;
    if (this.scheduled !== null) this.options.cancel(this.scheduled);
    this.scheduled = null;
    if (!this.disposed) this.options.busy(false);
  }

  dispose() {
    this.disposed = true;
    this.clear();
  }

  private schedule() {
    if (
      this.disposed ||
      this.running ||
      this.scheduled !== null ||
      !this.pending
    )
      return;
    this.scheduled = this.options.schedule(() => {
      this.scheduled = null;
      void this.flush();
    });
  }

  private async flush() {
    if (this.disposed || this.running || !this.pending) return;
    const job = this.pending;
    this.pending = null;
    this.running = true;
    try {
      const frame = await this.options.render(job.request);
      if (!this.disposed && job.epoch === this.epoch)
        this.options.present(frame);
    } catch (error) {
      if (!this.disposed && job.epoch === this.epoch) this.options.error(error);
    } finally {
      this.running = false;
      if (!this.disposed) {
        if (this.pending) this.schedule();
        else this.options.busy(false);
      }
    }
  }
}

export function previewSize(
  width: number,
  height: number,
  interactive: boolean,
) {
  const factor = interactive ? Math.min(1, 800 / width, 600 / height) : 1;
  return {
    width: Math.max(64, Math.round(width * factor)),
    height: Math.max(64, Math.round(height * factor)),
  };
}

/** Só o ciclo mais recente pode publicar resultados ou informar uma falha. */
export class LatestRefresh {
  private generation = 0;
  private controller: AbortController | null = null;

  async run(
    load: (isCurrent: () => boolean, signal: AbortSignal) => Promise<void>,
    failed: (error: unknown) => void,
  ): Promise<void> {
    const generation = ++this.generation;
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    const isCurrent = (): boolean => generation === this.generation;
    try {
      await load(isCurrent, controller.signal);
    } catch (error) {
      if (isCurrent()) failed(error);
    }
  }
}

/** Só o ciclo mais recente pode publicar resultados ou informar uma falha. */
export class LatestRefresh {
  private generation = 0;

  async run(
    load: (isCurrent: () => boolean) => Promise<void>,
    failed: (error: unknown) => void,
  ): Promise<void> {
    const generation = ++this.generation;
    const isCurrent = (): boolean => generation === this.generation;
    try {
      await load(isCurrent);
    } catch (error) {
      if (isCurrent()) failed(error);
    }
  }
}

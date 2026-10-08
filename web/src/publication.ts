// Resumo, ativos e diff na tela vêm sempre da mesma publicação, e só da atual.

interface Tagged {
  publication_id: string;
}

export interface Bundle {
  pub: Tagged;
  assets: Tagged;
  diff: Tagged;
}

export class PublicationSync {
  private latest: string | null = null; // último publication_id visto em /current
  private shown: string | null = null; // publication_id do que está na tela

  /** Resposta de /current (null: sem publicação). true: buscar ativos e diff dessa publicação. */
  seen(id: string | null): boolean {
    this.latest = id;
    if (id === null) this.shown = null;
    return id !== null && id !== this.shown;
  }

  /** true: o pacote é todo da publicação atual e ainda não está na tela; pode trocar. */
  accept(b: Bundle): boolean {
    const id = b.pub.publication_id;
    if (b.assets.publication_id !== id || b.diff.publication_id !== id) return false;
    if (id !== this.latest || id === this.shown) return false;
    this.shown = id;
    return true;
  }

  /** Troca de escopo, tenant, evento ou modo: o que estiver em voo é descartado. */
  reset(): void {
    this.latest = null;
    this.shown = null;
  }
}

# 0003: cálculo e publicação em uma transação

Status: Aceito. Registra a implementação do vetor de entrada e da geração.

Cada cálculo tem um identificador derivado de um vetor imutável: evento,
mapa, tenant, epochs, regra e modo. Fatos, totais e publicação atual entram
em uma transação PostgreSQL. O grid é carregado antes dessa transação.

Uma mudança de entrada incrementa a geração desejada do job na própria
transação que aplica a mudança. Antes de publicar, o worker compara a geração
que calculou com a geração desejada. Se divergem, descarta o resultado.

Um worker interrompido não deixa fatos publicados pela metade. Uma repetição
da mesma entrada não cria outra publicação. A duração do cálculo é limitada
pelos timeouts do papel de pipeline; a lease permite retomar jobs abandonados.

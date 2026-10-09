# Bootstrap a frio — 2026-10-09

Uma rodada no Windows 11, AMD Ryzen 7 5700X3D, 16 processadores lógicos e
31,9 GiB de RAM.
Docker Desktop 29.7.2, VM com 16 CPUs e 15,56 GiB. A stack de verificação,
uma stack de integração e outros projetos estavam ativos durante a medição.

Código da imagem: `bb230decd8ac58500f0969b880bdbbe6c575ad37`. Havia alterações
locais de documentos e testes. Imagens já em cache; nenhum build ou download
entrou no cronômetro. IDs das imagens, saída do bootstrap, clocks e leituras
do Docker estão em [m6-bootstrap-cold.json](m6-bootstrap-cold.json).

O projeto `qe-cold-808cc8ac` começou com volumes vazios e senhas próprias.
O cronômetro iniciou antes de `docker compose up -d --no-build --pull never cdc`.
Em seguida: `qe bootstrap --assets 10000 --seed 42`, worker e API. A API usou
porta efêmera de loopback. Não foram iniciados o coletor nem a demo de exclusão.
Requisições ao USGS: zero.

| Marco | Desde o início |
|---|---:|
| Infraestrutura e processo CDC iniciados | 64,861 s |
| Carteira recebida pelo CDC e capturas REPLAY projetadas | 183,814 s |
| Primeira publicação COMPLETE de `us7000tiur` observada na API | 356,208 s |
| Quatro publicações do evento, dois tenants e dois modos | 356,907 s |
| Todos os 998 jobs iniciais concluídos | 377,862 s |

O comando de bootstrap ocupou 118,953 s, incluindo criação do container
temporário. A seed gerou 10.000 ativos, 10.505 intervalos atuais e 11.010
versões em 23 comandos; os clocks terminaram em 12 e 11. A ordem inicial
dos jobs influencia quando o evento de demo aparece. Esta rodada mede a
carga inicial com trabalho concorrente; não é uma distribuição de latências.

Após a fila terminar: zero jobs pendentes, zero falhas, zero publicações
desatualizadas e zero transações CDC pendentes. O Connect estava RUNNING
e o slot ativo. A fonte HTTP aparece desatualizada porque o coletor ficou parado.

| Container | RAM observada |
|---|---:|
| platform-db | 431,60 MiB |
| source-db | 56,33 MiB |
| Kafka | 408,20 MiB |
| Connect | 1,062 GiB |
| CDC | 68,18 MiB |
| Worker | 41,59 MiB |
| API | 57,34 MiB |
| Total aproximado | 2,10 GiB |

RAM é uma amostra de `docker stats`, depois do processamento, sem coletor.
O projeto e seus volumes foram removidos ao final. As stacks existentes
permaneceram intactas.

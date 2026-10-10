# Padrão de documentação

O README apresenta o problema, o uso e a implementação. O [runbook](operacao.md) concentra manutenção e recuperação; os [ADRs](adr/) registram decisões; [results](results/) guarda execuções identificadas.

## Estrutura do README

| Seção | Conteúdo |
| --- | --- |
| Abertura | Finalidade do projeto, origem dos dados e trabalho implementado. Banner próprio, navegação por badges, idioma e CI. |
| Visão geral | Problema tratado, entradas, regras e significado do resultado. |
| Demonstração | Captura real, data e contexto; um caso com números ligados ao registro e um percurso para consultá-lo. |
| Arquitetura | Mermaid com fluxos reais e tabela de responsabilidades ligada ao código e aos ADRs. |
| Stack e decisões | Ícones locais de 64 × 64 px, papel de cada tecnologia e custo da escolha. |
| Executar localmente | Pré-requisitos, comandos da primeira carga, endereço e execução da demo. |
| Verificação | Resultado, contexto e fonte de cada execução; comandos para repetir as verificações. |
| Limites e segurança | Restrições que afetam o uso e a operação. |
| Documentação | Links organizados pelo que o leitor precisa consultar. |
| Autor e licença | Contato com ícone do LinkedIn, licença do código e créditos de dados e imagens. |

## Escrita e informações

- Usar pt-BR, frases diretas e termos concretos, conforme [CONTRIBUTING.md](../CONTRIBUTING.md). Explicar o comportamento antes dos detalhes internos.
- Apresentar a contribuição implementada sem slogans ou alegações de adoção, escala ou ganho não medidos.
- Distinguir capturas reais do USGS, carteira fictícia e alterações FAULT. Explicar o denominador das contagens e separar moedas.
- Associar números a uma execução identificada por data e commit, quando disponíveis. Manter ambiente e condições da medição no registro vinculado.
- Preservar registros históricos. Atualizar a apresentação não repete testes nem muda a data de uma medição.
- Mostrar comandos de início no README e detalhar operações no runbook. Evitar repetir o mesmo catálogo nos dois arquivos.
- Manter português e inglês com os mesmos fatos, números e referências. A interface e os guias operacionais são em português.

## Arquivos visuais

Usar SVGs locais para badges, stack e contato, com texto alternativo e dimensões explícitas. As fontes e a licença estão em [stack/README.md](stack/README.md). Preservar a marca descrita em [identidade-visual.md](identidade-visual.md).

Capturas devem mostrar a aplicação em uso e ter texto legível. Manter a visão principal no README e os recortes complementares em uma seção expansível. Conservar âncoras antigas quando um título mudar.

O padrão segue a apresentação dos projetos [ContainerOps](https://github.com/arthurjoanes/containerops), [API Sentinel](https://github.com/arthurjoanes/api-sentinel) e [EvidenceDesk](https://github.com/arthurjoanes/evidencedesk), com as regras de escrita deste repositório.

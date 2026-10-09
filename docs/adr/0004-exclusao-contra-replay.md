# 0004: bloqueio de titular antes da exclusão

Status: Aceito. Registra o fluxo de exclusão definido para a carteira.

Apagar linhas atuais não basta: uma mensagem antiga de CDC ou um backup
pode conter o mesmo titular. O fluxo grava primeiro um bloqueio persistente
e incrementa a epoch de privacidade do tenant. Depois apaga dados na origem
e na plataforma, invalida publicações e emite o recibo.

O consumidor consulta o bloqueio antes de aplicar qualquer intervalo. A API
só serve a publicação da epoch de privacidade atual. Um cálculo anterior à
exclusão não volta ao ar pela conclusão tardia do worker.

O ledger JSONL vive em volume próprio, separado dos bancos. A verificação
de restauração usa um banco isolado, reaplica esse ledger e consulta os
resíduos. Restaurar a origem exige repetir a exclusão dos titulares do ledger.
O recibo descreve os resíduos em WAL, tópicos e dumps. Kafka tem retenção de
sete dias; a expiração dos dumps é operação manual.

## Preparação no Windows

Abra o terminal nesta pasta e execute:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe conectar_perfis.py --login
```
## Executar

Depois de fazer o login conforme a preparação acima, localize o diretório da pasta aonde está a planilha e execute:

```powershell
.\.venv\Scripts\python.exe conectar_perfis.py "C:\Users\Downloads\Design-Profissional_TODOS_LindedIn_GitHub.xlsx" (exemplo)
```

Você pode corrigir as entradas ambíguas na planilha e validar novamente. Nenhuma correção de identificador é inferida automaticamente. Para outras tabelas:

```powershell
.\.venv\Scripts\python.exe conectar_perfis.py minha_tabela.csv
# Ou:
.\.venv\Scripts\python.exe conectar_perfis.py minha_tabela.xlsx
```

Usa lotes de até 50 pessoas; uma pessoa pode gerar duas ações, uma em cada plataforma. Entre perfis, espera um intervalo aleatório de 10 segundos. Os cliques de abertura e confirmação de um mesmo convite são uma única ação. Ao terminar cada lote, pede que você escolha se quer continuar. Para parar a qualquer momento, use Ctrl+C.

As pausas espaçam as ações, mas não garantem evitar detecção ou bloqueios. O código não tenta disfarçar o navegador nem contornar verificações. Ao detectar login, CAPTCHA ou restrição reconhecível, interrompe o processamento. A detecção não cobre todas as mensagens possíveis das plataformas.

O LinkedIn envia o convite sem nota. Solicitações de e-mail, opções de assinatura ou telas não reconhecidas interrompem a execução. Não há cancelamento de convites nem ação de deixar de seguir. Os seletores contemplam português e inglês, mas as interfaces podem mudar e exigir ajustes. Convites pendentes e usuários já seguidos são registrados sem repetir o clique.

## Resultados e retomada

`resultados.json` registra as URLs processadas. Uma nova execução ignora qualquer URL já registrada. O estado `revisar_acao_iniciada` é salvo antes do primeiro clique: se houver falha ou interrupção, o script não tenta enviar novamente automaticamente. Confira na plataforma todos os estados que começam com `revisar_`. Somente depois da conferência, remova do arquivo a entrada de uma URL que realmente precise ser tentada novamente. `revisar_conexao` indica que o script encontrou Mensagem, mas não conseguiu confirmar se já existe conexão.

O perfil persistente contém sessões autenticadas. Mantenha essa pasta privada. O arquivo de resultados também contém os endereços da sua lista.

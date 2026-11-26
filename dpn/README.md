# DPN Control

Aplicativo desktop (Python + PySide6) para **controle manual** de várias unidades
Deeper Connect (DPN / AtomOS) na rede local. Cada unidade tem sessão, credenciais,
TLS e estado **independentes** — uma ação destinada à DPN A nunca atinge a DPN B.

> **Estado atual**: toda a API AtomOS foi **confirmada inspecionando o bundle JS
> servido pela própria unidade** (`/assets/index.*.js` em `34.34.34.34`), não por
> suposição. A senha do login é criptografada com **RSA-OAEP/SHA-1** (chave
> pública embutida no JS) — confirmado ao vivo. Endpoints implementados:
> login, validateToken, logOut, getDeviceId, hardware/software/network info,
> accessControl/list (dispositivos), getDpnMode/setDpnMode (tipo de nó),
> listTunnels/switchNode/refreshTunnel/nodeLbMode/addTunnel (túneis) e reboot.
>
> A interface tem três abas: **Unidade** (tipo de nó, IP/saída, reboot),
> **Access Control** (dispositivos Online/Offline com edição de DPN Mode, túnel,
> HTTPS filter, bypass e banda) e **Túneis** (lista com Região/País, nº de IPs,
> IP ativo, "Switch active IP", "Refresh" e Node Selection Mode).

---

## 1. Requisitos (Windows)

- Python 3.11+ (testado com 3.14)
- Dependências:

```powershell
cd C:\AndroidProjects\dpn
python -m pip install -r requirements.txt
```

## 2. Executar

```powershell
cd C:\AndroidProjects\dpn
python -m dpn
```

ou clique duas vezes em `run.bat`.

### Gerar executável (opcional)

```powershell
python -m pip install pyinstaller
pyinstaller --noconfirm --windowed --name "DPNControl" dpn/__main__.py
# o .exe fica em dist\DPNControl\
```

## 3. Estrutura

```
dpn/
  app.py                 bootstrap QApplication
  config.py              config.json opcional (sem segredos)
  models.py              Device, DeviceInfo, ActionLog, TLSMode, ...
  security.py            redact() e gancho de derivacao de senha
  discovery.py           descoberta assistida na rede local
  service.py             unica porta de entrada da UI (storage+segredos+cliente)
  workers.py             QThreadPool — rede fora da thread da UI
  atomos/
    client.py            AtomOSClient (1 por unidade, sessao/TLS/lock isolados)
    endpoints.py         registro com flag CONFIRMADO/NAO-CONFIRMADO
    tls.py               contexto TLS por unidade (verify / CA / insecure)
    errors.py            excecoes tipadas
  storage/
    database.py          SQLite (devices + history) — SEM segredos
    secrets.py           senhas via DPAPI do Windows (nunca em claro)
  ui/
    main_window.py       janela + abas (Unidade / Access Control / Túneis)
    device_list_panel.py lista / cadastro / edicao / remocao / descoberta / ping
    device_detail_panel.py operacoes manuais + acompanhamento de reboot
    access_control_panel.py dispositivos Online/Offline editaveis (setOne)
    tunnels_panel.py     tuneis: Switch active IP / Refresh / Node Selection Mode
    auth_task.py         login + operacao na mesma sessao (helper comum)
    history_panel.py     historico local por unidade
    dialogs.py           cadastro/edicao + confirmacoes + bypass
  atomos/regions.py      mapa codigo->nome de regiao/pais (extraido do frontend)
tests/                   testes do cliente HTTP (respostas simuladas), DB, segredos
```

### Decisões de segurança

- **Senhas** ficam no DPAPI do Windows (`storage/secrets.py`), nunca no SQLite nem
  em texto puro. `keyring` (Credential Manager) pode ser usado futuramente.
- **TLS por unidade**: `verify` (padrão), `ca_bundle` (certificado próprio da
  unidade) ou `insecure` (autoassinado). O modo `insecure` aparece com **aviso
  vermelho** na interface e é configurado por unidade — nunca globalmente.
- **Sessões isoladas**: cada `AtomOSClient` tem sua própria `httpx.Client`
  (cookies), contexto TLS, timeout e `threading.Lock`.
- **Sem redirecionamentos**: qualquer `3xx` é rejeitado, para impedir que uma
  unidade "puxe" a ação para outro host.
- **Histórico** nunca grava senha/token/cookie/payload sensível (`security.redact`).
- **Sem fixar `34.34.34.34`**: o endereço de cada unidade é digitado pelo usuário
  e é o identificador de conexão. A descoberta apenas sugere candidatos.
- **Somente rede local**: o app não expõe nenhuma porta/servidor na internet.

## 4. Configuração de exemplo (sem segredos)

`config.example.json`:

```json
{
  "request_timeout": 10.0,
  "connect_timeout": 5.0,
  "discovery_timeout": 1.2,
  "discovery_max_hosts": 254,
  "log_requests": false
}
```

Copie para `%LOCALAPPDATA%\dpn\config.json` se quiser ajustar. Nenhum campo
deste arquivo contém credencial.

## 5. Verificação guiada em uma DPN

Endpoints já confirmados (ver acima). Para testar ao vivo, rode a sonda:

```powershell
python tools/probe.py 34.34.34.34
```

Ela pede usuário/senha (a senha não fica no histórico) e imprime o resultado de
login, device_info, device_list, node_type e exit_ip. Compare com o painel web
para validar os dados.

Depois, use a interface (`python -m dpn`): cadastre a unidade com o endereço real
(IP da LAN ou `34.34.34.34` conforme sua topologia), escolha o TLS (recomendado:
`ca_bundle` com o certificado da unidade), e clique em **"Testar conexão (login)"**.

> A senha é **sempre** criptografada (RSA-OAEP/SHA-1) antes de sair da máquina;
> ela nunca é enviada nem armazenada em claro.

## 6. O que ainda depende de teste no hardware

Validado ao vivo em `34.34.34.34`:
- login (RSA-OAEP/SHA-1), device_info, device_list, node_type, exit_ip;
- `setDpnMode` (`{success:true}`), `switchNode` (`activeIp` mudou de fato:
  `66.8.180.121` → `104.15.97.147`), `refreshTunnel` (`{success:true}`, novos IPs
  entram de forma assíncrona).

Ainda pendente:
- **reboot** (não testado — a unidade estava servindo dispositivos; validar em uma
  unidade que possa cair).
- Confirmar tempo de volta após reboot e se o IP de gerência muda.
- Firmwares com chave RSA diferente (o cliente busca a chave atual da unidade em
  `fetch_public_key` e usa a embutida como fallback).

## 7. Auditoria (resumo)

- O código AtomOS/ADB antigo **não foi localizado** no PC (`Harbor`, `APPS`,
  `prepositions`, `adsApp`, `wg` não contêm a API local; `DPN-App.apk`/`DPN-App.exe`
  são o app oficial da Deeper, que fala com a nuvem `dpn.deeper.network`).
- A API local foi **extraída do frontend servido pela própria DPN**
  (`/assets/index.*.js`): `POST /api/admin/login` (senha RSA-OAEP/SHA-1),
  `/api/accessControl/list`, `/api/smartRoute/getDpnMode|setDpnMode`,
  `/api/smartRoute/listTunnels|switchNode|refreshTunnel`, `/api/admin/reboot`,
  `/api/system-info/*`, entre outros.
- Certificado do painel é autoassinado (CN `www.deeper.network`), por isso o
  tratamento de TLS por unidade.

## 8. Agentes Android (arquitetura)

```
DPN Control (Windows) ──WSS──► Agente Android ──HTTPS──► AtomOS (34.34.34.34)
```

- O controlador sobe um servidor **WSS** (padrão `0.0.0.0:8765`) com certificado
  autoassinado; o fingerprint é mostrado na aba **Agentes**.
- Cada APK gera um **agent_id** próprio; o pareamento é por **código temporário**
  aprovado no controlador, que emite um **token individual** (sem chave fixa).
- A **sessão do AtomOS** (login/cookies) e as **credenciais** ficam no agente
  (Android Keystore). O controlador só envia **comandos tipados** (protocolo
  `dpn-agent/1`), com `request_id`, prazo, **identidade esperada** e operação.
- Antes de qualquer **alteração**, o agente confere o `getDeviceId` **no mesmo
  caminho de rede** e recusa se divergir. O controlador mantém o mapa
  agente→DPN→ID observado e sinaliza **"Conflito: mesma DPN"** quando dois
  cadastros apontam para o mesmo ID físico (bloqueando alterações).
- Resultado explícito: `success` / `failure` / `expired` / `uncertain`. Se a
  conexão cair após enviar uma alteração, o resultado é **incerto** e **não é
  reenviado automaticamente**.
- ADB é usado apenas para **instalação e diagnóstico**; não é requisito de
  operação.
- O agente **autoconecta** ao abrir o app e após o boot do aparelho
  (`BootReceiver`), e **autorreconecta** com backoff+jitter quando a conexão cai.

### Firewall do Windows (liberar só o necessário)

O controlador faz isso **automaticamente** ao iniciar e no assistente de
instalação: verifica se a regra de entrada TCP 8765 existe e, se não, cria uma
regra **escopada à rede local** (`LocalSubnet`, perfis Any). Se o processo não
for administrador, aparece **um único prompt de UAC** do Windows; depois a regra
já existe e não há mais prompt. Não exponha essa porta à internet.

Para criar manualmente (equivalente):

```powershell
New-NetFirewallRule -DisplayName "DPN Control Agent Server" -Direction Inbound `
  -Protocol TCP -LocalPort 8765 -Action Allow -Profile Any -RemoteAddress LocalSubnet
```

### Procedimento de validação real (2 DPNs antes da terceira)

1. Pareie **um** agente e associe à DPN 1. Confirme: agente online, AtomOS
   acessível, ID validado.
2. Pareie um **segundo** agente, associe à DPN 2. Confirme IDs **diferentes**.
3. Faça uma leitura (dispositivos, modo) e uma alteração inofensiva (ex.: troca
   de IP ativo) em cada uma; verifique na aba Túneis/IP ativo.
4. Só então habilite a terceira.
5. Teste de perda de conexão: desconecte o agente durante uma alteração e confirme
   que aparece **"resultado incerto"** sem reenvio automático.

### Build do APK

Ver `android-agent/README.md`. APK de teste já gerado em
`android-agent/app/build/outputs/apk/debug/app-debug.apk`.

## 9. Testes

```powershell
python -m pytest tests -q
```

Cobrem: cliente HTTP (mock), protocolo de agentes (comandos expirados, agente
errado, operação desconhecida), **servidor↔agente via WebSocket real**
(pareamento, não autorizado, comando expirado), identidade (três IDs distintos,
**dois agentes com o mesmo ID → conflito**, mudança de ID, agente up + AtomOS
down), `success:false`, e **migração do banco** preservando dados.

## 10. Configuração simplificada dos agentes

### 10.1 Configurar pelo Windows (ADB)
1. Rode `python -m dpn`. Na aba **Unidade**, selecione a DPN na lista e, na seção
   **Agente / transporte**, clique em **"Instalar e configurar agente"**.
2. No assistente, clique **"Atualizar lista"** e **selecione explicitamente** o
   Android desejado (serial ADB). O IP de referência por ARP aparece como ajuda,
   mas **não autentica** nada.
3. Confirme o **endereço do controlador** (o assistente prioriza a mesma /24 do
   Android; ajuste em "Avançado" se preciso) e informe a **senha do AtomOS**
   (pedida uma única vez).
4. Clique **"Instalar e provisionar"**: instala/atualiza o APK (`install -r`,
   preservando dados e `agent_id`), envia URL+fingerprint+código temporário+AtomOS
   (sem senha) e espera o agente parear. A senha AtomOS vai **pelo WSS** após o
   pareamento e é guardada no Android Keystore.
5. Ao concluir, o ID observado da DPN aparece e você confirma o vínculo.

Requisitos: APK compilado em `android-agent/app/build/outputs/apk/debug/app-debug.apk`,
ADB autorizado no aparelho e a porta do controlador liberada no firewall (§8).

### 10.2 Configurar pelo QR
1. Na seção **Agente** da aba Unidade (ou no assistente), clique **"Mostrar QR"** —
   ele contém URL, fingerprint do controlador e **código temporário** (sem senha/token).
2. No app, toque em **"Ler QR de configuração"** e escaneie.
3. Confirme no aparelho; o agente conecta e pareia como no fluxo ADB.

### 10.3 Alternar entre conexão direta e agente
- No cadastro (**Adicionar/Editar** ou aba do assistente), escolha
  **Transporte → Direta** (o PC acessa `https://34.34.34.34`) ou **Via agente**.
- A troca **não apaga** a associação do agente, a senha do outro transporte nem o
  ID esperado. Não há fallback silencioso. Mutações em andamento bloqueiam a troca.

### 10.4 Recuperar pareamento ou resolver conflito de identidade
- **Pareamento perdido/limpo**: no Android, "Avançado → Zerar pareamento" e
  configure de novo (novo `agent_id`/token). No controlador, despare/associe de novo.
- **ID não vinculado**: aba **Unidade → Conectar/Atualizar unidade → Vincular ID observado**.
- **ID divergente**: alterações ficam bloqueadas; confirme a DPN certa e revincule
  explicitamente (nunca é automático).
- **Conflito: mesma DPN**: dois cadastros apontam para o mesmo ID físico —
  altere um deles para o agente/DPN corretos; mutações ficam bloqueadas até resolver.
- **IP de túnel compartilhado**: aviso separado na aba Túneis (não é conflito físico).

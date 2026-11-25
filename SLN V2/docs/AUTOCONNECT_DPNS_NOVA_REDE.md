# Autoconnect de DPNs em uma nova rede

Guia operacional do Device Agent Controller, DPN pool e auto-register.

## 1. O que este fluxo faz

```text
APK Device Agent (Android)
        -> WSS 127.0.0.1:8766
Device Agent Controller
        -> PresenceRegistry / DPNTransportPool
        -> auto-register por physical_dpn_id
DPN Control
        -> lista/opera DPNs
```

O controller **não usa** um Android fixo por DPN. Qualquer Agent autenticado da
mesma `physical_dpn_id` pode transportar a operação.

O auto-register:

- descobre DPNs a partir do `physical_dpn_id` reportado pelos Agents;
- gera `dpn_groups_auto.json`;
- coloca Agents `ONLINE` com `lan_ip` válido em `members[]`;
- faz `member = <lan_ip>:5555`;
- não exige `authenticated=true` para membership;
- deduplica members;
- aguarda estabilização dos heartbeats antes de gravar members.

Membership de rota e transporte autenticado são coisas separadas:

```text
membership  = aparelhos que pertencem à DPN
control     = Agents autenticados no AtomOS que podem transportar comandos
```

## 2. Pré-requisitos

No PC:

```powershell
cd "C:\AndroidProjects\APPS\SLN V2"
```

- Python: `C:\Python314\python.exe`
- ADB funcionando
- APK:
  `device_agent_apk\app\build\outputs\apk\debug\app-debug.apk`
- Certificado do controller:
  `state\device_agents\tls\device-controller-cert.pem`
- Chave:
  `state\device_agents\tls\device-controller-key.pem`
- Nenhum outro processo usando `0.0.0.0:8766`

Verificar porta:

```powershell
netstat -ano | findstr ":8766"
```

Deve haver no máximo um controller.

## 3. Instalar o APK nos aparelhos

```powershell
adb -s SERIAL install -r "device_agent_apk\app\build\outputs\apk\debug\app-debug.apk"
```

Repetir para cada serial. O APK atual é `0.5`.

## 4. Corrigir ADB/reverse

O Agent usa:

```text
wss://127.0.0.1:8766
```

Por isso, em cada aparelho que for usar:

```powershell
adb -s SERIAL reverse tcp:8766 tcp:8766
```

Se o ADB travar:

```powershell
taskkill /F /IM adb.exe
adb start-server
adb devices
```

Executar em terminal elevado se necessário.

## 5. Subir o controller

Usar o perfil dev, que já tem as flags de agent pool/auto-register:

```powershell
cd "C:\AndroidProjects\APPS\SLN V2"
$env:SLN_CONFIG_OVERLAY="config_sln_v2.json"
C:\Python314\python.exe -u dpn_agent_controller.py
```

O controller é persistente. **Não use `--duration 0` dentro de automações de
teste**; para testes use duração finita (`--duration 30`/`1800`). Em produção,
inicie-o externamente.

Espere aparecer:

```text
[AGENT][CTRL] listening wss://0.0.0.0:8766
```

## 6. Parear os Agents

Cada Agent precisa de um token. O código de pareamento é **de uso único** e tem
validade curta.

Na interface:

1. Abra `interface_sln_v2.py`.
2. Aba **Agentes DPN**.
3. Clique **Gerar código**.
4. Use o código mostrado no provisionamento do aparelho.

Provisionar um aparelho:

```powershell
adb -s SERIAL shell am force-stop com.sln.deviceagent
adb -s SERIAL shell am start -n com.sln.deviceagent/.ProvisionActivity `
  -e controller_url wss://127.0.0.1:8766 `
  -e pairing_code CODIGO
```

O código expira. Se falhar, gere outro.

### Credenciais AtomOS

Para o auto-register descobrir `physical_dpn_id`, o Agent precisa autenticar no
AtomOS. O APK antigo pode migrar automaticamente a credencial. Em aparelho novo,
provisione também:

```powershell
adb -s SERIAL shell am start -n com.sln.deviceagent/.ProvisionActivity `
  -e atomos_url https://34.34.34.34 `
  -e atomos_password SENHA
```

Não coloque senha em arquivos versionados, logs, issues ou relatórios.

## 7. Descobrir as DPNs

O controller espera os heartbeats e registra:

- `physical_dpn_id` por Agent;
- `lan_ip`;
- tunnel/modo quando consegue autenticar.

Arquivos:

```text
state\dpn\agents_status.json
dpn_groups_auto.json
state\dpn\status.json
```

Conferir members:

```powershell
$g = Get-Content .\dpn_groups_auto.json -Raw | ConvertFrom-Json
$g.PSObject.Properties | ForEach-Object {
  "$($_.Name) members=$($_.Value.members.Count)"
}
```

Conferir grupos:

```powershell
$s = Get-Content .\state\dpn\status.json -Raw | ConvertFrom-Json
$s.dpns.PSObject.Properties | ForEach-Object {
  "$($_.Name) state=$($_.Value.state) active=$($_.Value.active_devices)"
}
```

## 8. Novas DPNs desconhecidas

O auto-register descobre o ID físico, mas o `tunnel_code` não é inventado. Se a
DPN não existir no catálogo, ela é registrada como **disabled** para evitar
mutação em unidade errada.

Para ativar uma DPN nova:

1. Descubra o ID físico em `agents_status.json`.
2. Descubra o túnel real da unidade (`fullTunnel` no modo AtomOS).
3. Cadastre a DPN em `dpn_groups.json` com `expected_dpn_id` e `tunnel_code`.
4. Reinicie o controller.
5. O auto-register vai casar o ID físico com o catálogo e criar o grupo com
   `transport=agent_pool`.

Exemplo de entrada no catálogo:

```json
{
  "DPN_NOVA": {
    "group_id": "DPN_NOVA",
    "provider": "deeper_connect",
    "tunnel_code": "BR",
    "expected_dpn_id": "ID_FISICO_DA_NOVA_DPN",
    "enabled": true,
    "controller": {
      "type": "deeper_connect",
      "transport": "agent_pool",
      "address": "34.34.34.34",
      "port": 443,
      "username": "admin",
      "tls_mode": "insecure",
      "tunnel_code": "BR",
      "node_lb_mode": "none",
      "expected_dpn_id": "ID_FISICO_DA_NOVA_DPN",
      "request_timeout_s": 25,
      "connect_timeout_s": 5
    }
  }
}
```

Não coloque `agent_id` fixo. O pool escolhe o Agent.

Se o tunnel não for conhecido, use o modo/leitura AtomOS para descobrir
`fullTunnel`; não use um tunnel aleatório para não trocar o IP de uma DPN errada.

## 9. Ordem correta de validação

1. Pelo menos um Agent por DPN com credencial válida.
2. `agents_status.json` mostra Agent `ONLINE`.
3. `GET_DPN_IDENTITY` retorna `physical_dpn_id`.
4. `members > 0` no `dpn_groups_auto.json`.
5. `active_devices == len(members)` no `status.json`.
6. Pelo menos um Agent autenticado por DPN para operações.
7. Somente depois testar switch/refresh.

Não execute switch só porque `members > 0`; confirme também que existe
`control candidate` autenticado.

## 10. Teste controlado

Para validar bots + drain + switch:

```powershell
cd "C:\AndroidProjects\APPS\SLN V2"
$env:SLN_CONFIG_OVERLAY="config_fleet_validation.json"
C:\Python314\python.exe fleet_validation.py --minutes 30 --start-bots
```

O teste deve ser executado externamente e com duração finita.

Antes de rodar, confirme:

- nenhum controller em `8766`;
- ADB/reverse estável;
- Agents pareados;
- members preenchidos;
- grupos com Agent autenticado;
- perfil de validação separado (`state/dpn_fleet_validation`).

## 11. Diagnóstico rápido

### `members=0`

Verifique:

- Agent está `ONLINE`?
- `lan_ip` está presente?
- `physical_dpn_id` está igual ao catálogo?
- heartbeat já chegou?
- o state dir correto foi usado?

### `ERROR: discover:ProviderConnectionError`

Verifique:

- existe Agent online/autenticado para a DPN?
- `presence.json` foi carregado no state dir?
- a DPN esperada está correta?
- o Agent tem permissão/capability de DPN?

### `AUTH_BACKOFF`

Aguarde o backoff; não fique gerando códigos ou tentando autenticar em loop.

### `UNCERTAIN`

Não reenvie a mutação. Faça reconcile por leitura em outro Agent.

### `DRAIN_TIMEOUT`

Um member não publicou `SAFE_TO_ROTATE`. Isso é diferente de falha do pool.
Verifique se o bot está parado em um rewarded sem countdown ou se o processo
morreu. Não altere Fast Close durante o teste.

## 12. Segurança e limites atuais

- Senhas ficam no Keystore Android; não são enviadas em texto pelo WSS.
- Um lease mutating por `physical_dpn_id`.
- `UNCERTAIN` não é retry automático.
- O auto-register de DPN conhecida está estável.
- DPN desconhecida precisa de catálogo/tunnel antes de mutations.
- A interface e o APK não devem ser alterados durante uma rodada de validação.

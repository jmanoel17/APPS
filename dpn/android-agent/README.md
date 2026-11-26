# DPN Agent (Android)

Agente intermediário entre o **DPN Control** (Windows) e o **AtomOS** de cada
Deeper Connect. O agente abre uma conexão persistente **WSS** com o controlador,
executa as chamadas locais ao AtomOS (`https://34.34.34.34`) e devolve respostas.

## Requisitos

- Android 8.0 (API 26) ou superior.
- Acesso HTTPS ao AtomOS da DPN que atende a rede do aparelho.
- Alcançar o controlador na rede (mesma LAN ou rota permitida).

## Build

O projeto é Gradle (Kotlin). Para gerar o APK:

```powershell
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
# use o Gradle 8.13 (ou ./gradlew se gerar o wrapper)
gradle assembleDebug
# saida: app/build/outputs/apk/debug/app-debug.apk
```

Instale no aparelho (ADB é usado **apenas** para instalação/diagnóstico):

```powershell
adb install -r android-agent\app\build\outputs\apk\debug\app-debug.apk
```

## Instalação e configuração (os três equipamentos)

1. Instale o **mesmo** APK nos três aparelhos.
2. No controlador (Windows), abra a aba **Agentes** e clique em
   **"Gerar código de pareamento"**. Anote o código e o **fingerprint do
   certificado** mostrados.
3. Em cada aparelho, abra o DPN Agent e preencha:
   - **Endereço do controlador**: `wss://<IP-do-PC>:8765` (o IP da LAN do PC).
   - **Fingerprint do controlador**: o valor mostrado na aba Agentes (SHA-256).
   - **Código de pareamento**: o código de 6 dígitos.
   - **AtomOS**: padrão `https://34.34.34.34`.
   - **Fingerprint do AtomOS**: deixe vazio no primeiro uso.
   - **Senha admin do AtomOS**: a senha local da DPN (fica no Keystore).
4. Toque em **Salvar** e **Conectar**. No controlador, o agente aparece na lista.
5. Associe o agente ao cadastro **DPN 1/2/3** (botão "Associar à DPN selecionada").

Cada instalação gera um **agent_id** próprio e recebe um **token** individual no
pareamento (não há chave fixa compartilhada). Reinstalar ou limpar os dados exige
**novo pareamento**.

## Fingerprint do AtomOS (pinning)

O certificado do AtomOS é autoassinado. Use **Diagnóstico AtomOS**: ele mostra o
fingerprint observado. Cole-o em **Fingerprint do AtomOS**, salve e reconecte.
Assim o agente deixa de aceitar qualquer certificado (sem trust-all global).

## Segurança

- Senha/token ficam cifrados com AES/GCM e chave do **Android Keystore**.
- O agente só executa as operações AtomOS previstas, no destino configurado.
- Não há shell arbitrário nem proxy aberto.
- O canal WSS valida o certificado do controlador por fingerprint.

## Autoconexão e autoreconexão

- Ao **abrir o app** já configurado, o serviço em primeiro plano sobe sozinho e
  conecta (não é preciso tocar em "Conectar").
- O `BootReceiver` inicia o agente após o **reinício do aparelho** (se configurado).
- O `WsClient` **reconecta automaticamente** com backoff exponencial + jitter
  (1s→60s) em falhas de rede; `START_STICKY` recria o serviço se o sistema matá-lo.
- Mudança de Wi‑Fi/Ethernet dispara reconexão e reinvalida a sessão do AtomOS.

## Limitações (reais)

- Não há garantia de execução "exatamente uma vez"; se a conexão cair após enviar
  uma alteração, o resultado fica **incerto** e o controlador sinaliza isso.
- Disponibilidade em segundo plano depende de Doze/rede do aparelho; teste nos
  equipamentos reais antes de confiar.
- O MAC é apenas referência de cadastro (o Android pode randomizar).

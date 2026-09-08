# Guia da Interface Otimizada com Grupos

## 🎯 Visão Geral

A interface foi completamente reformulada para incluir:
- ✅ Sistema de grupos de dispositivos
- ✅ Persistência de grupos em JSON
- ✅ Rastreamento correto de status ATIVO
- ✅ Remoção do modo automático/manual (desnecessário)

## 📋 Funcionalidades

### 1. Gerenciamento de Grupos

#### Criar Novo Grupo
1. Clique em **"➕ Novo Grupo"**
2. Digite o nome do grupo
3. O grupo é salvo automaticamente em `groups.json`

#### Deletar Grupo
1. Selecione o grupo no dropdown
2. Clique em **"🗑️ Deletar Grupo"**
3. Confirme a exclusão

#### Adicionar Dispositivos ao Grupo
1. Selecione um grupo no dropdown
2. Selecione um ou mais dispositivos na lista (Ctrl+Click para múltiplos)
3. Clique em **"➕ Adicionar ao Grupo"**

#### Remover Dispositivos do Grupo
1. Selecione um grupo no dropdown
2. Selecione os dispositivos que deseja remover
3. Clique em **"➖ Remover do Grupo"**

### 2. Visualização de Dispositivos

#### Filtrar por Grupo
- Selecione um grupo no dropdown para ver apenas seus dispositivos
- Selecione "Todos os Dispositivos" para ver todos

#### Indicadores na Lista
- **[ATIVO]**: Dispositivo com bot em execução
- **[Nome do Grupo]**: Mostra o grupo quando visualizando "Todos os Dispositivos"

### 3. Execução

#### Iniciar Grupo Inteiro
1. Selecione um grupo no dropdown
2. Clique em **"▶️ Iniciar Grupo"** (botão verde)
3. Todos os dispositivos do grupo que estiverem conectados serão iniciados

#### Iniciar Selecionados
1. Selecione dispositivos manualmente (Ctrl+Click)
2. Clique em **"▶️ Iniciar Selecionados"**

#### Iniciar Todos
- Clique em **"▶️▶️ Iniciar Todos"** para iniciar todos os dispositivos conectados

## 🔧 Correções Implementadas

### Bug do Status ATIVO
**Problema anterior**: O status ATIVO nunca era removido quando a janela do terminal era fechada.

**Solução**: 
- Rastreamento de processos usando `psutil`
- Verificação automática a cada 2 segundos
- Remoção automática de processos mortos
- Status sempre atualizado e preciso

### Modo Automático/Manual Removido
O toggle de modo foi removido pois não era mais necessário. Todos os dispositivos agora executam no modo padrão (0).

## 📁 Arquivo de Grupos

Os grupos são salvos em `groups.json` no mesmo diretório do script:

```json
{
  "Grupo 1": ["192.168.1.100:5555", "192.168.1.101:5555"],
  "Grupo 2": ["192.168.1.102:5555"],
  "Teste": ["emulator-5554"]
}
```

## 🚀 Fluxo de Trabalho Recomendado

1. **Organizar Dispositivos**:
   - Crie grupos lógicos (ex: "Produção", "Teste", "Grupo A", "Grupo B")
   - Adicione dispositivos aos grupos apropriados

2. **Executar por Grupo**:
   - Selecione o grupo desejado
   - Clique em "Iniciar Grupo"
   - Monitore o status ATIVO

3. **Gerenciar Execução**:
   - Feche janelas de terminal para parar dispositivos específicos
   - O status ATIVO será atualizado automaticamente
   - Use "Atualizar Lista" se necessário

## 📦 Dependências

A interface agora requer o pacote `psutil` para rastreamento de processos:

```bash
pip install psutil
```

## 💡 Dicas

- Use nomes descritivos para grupos (ex: "Celulares Samsung", "Emuladores")
- Um dispositivo pode estar em apenas um grupo por vez
- Grupos vazios são permitidos
- O filtro de grupo facilita visualizar dispositivos específicos
- O status ATIVO é atualizado automaticamente, não precisa clicar em "Atualizar"

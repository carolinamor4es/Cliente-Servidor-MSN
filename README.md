# 💬 Chat Cliente-Servidor — MSN Messenger

Chat em rede com servidor TCP em Python (`socket` + `threading`) e cliente
desktop que abre o front-end (`index.html`) numa janela via `pywebview`. A
interface é um tributo ao clássico **MSN Messenger**, com direito a
winks animados (via emulador Flash Ruffle), nudge, emoticons e todo o clima
dos anos 2000.

![MSN](frontend/images/logomsn.png)

## 📖 Sobre o projeto

O projeto implementa, do zero, um sistema de chat cliente-servidor real: o
servidor (`server.py`) escuta conexões TCP e gerencia múltiplos clientes
simultâneos, cada um em sua própria thread, trocando mensagens em um
protocolo próprio serializado em JSON. O cliente (`client.py`) abre o
front-end em uma janela desktop nativa via `pywebview` e se comunica com o
servidor por sockets.

## ✨ Funcionalidades

- 👤 Cadastro e login de usuários, com senha protegida por hash + salt
- 💬 Chat geral (broadcast para todos os usuários conectados)
- ✉️ Mensagens privadas entre dois usuários
- 🚪 Salas de chat em grupo, com pedido de entrada e aprovação/recusa pelo
  dono da sala
- 📎 Envio e recebimento de arquivos entre usuários
- 🟢 Status de presença dos usuários (online/offline)
- 🕓 Histórico de mensagens, por conversa e por sala
- 😉 Winks animados (Flash, via emulador Ruffle), nudge (sacudida de tela) e
  emoticons, para recriar a experiência clássica do MSN

## 🧠 Conceitos abordados

- **Sockets TCP** para a comunicação cliente-servidor
- **Threads**: o servidor atende cada cliente conectado em uma thread
  dedicada, além de uma thread separada de escrita por usuário
- **Protocolo de aplicação próprio**, com mensagens serializadas em JSON
- **Hashing de senha com salt** (`hashlib`) para não guardar senhas em texto
  puro
- **Persistência simples em arquivo** (JSON) para usuários, salas, metadados
  de arquivos e histórico de conversas

## 🛠️ Tecnologias utilizadas

- **Python** (`socket`, `threading`, `json`, `hashlib`, `argparse`)
- **pywebview** para exibir o front-end em uma janela desktop nativa
- **HTML, CSS e JavaScript** no front-end
- **Ruffle** (emulador de Flash) para os winks em `.swf`

## Estrutura de pastas

```
.
├── backend/
│   ├── server.py                # servidor; roda sozinho, sem interface grafica
│   └── client.py                # abre index.html numa janela desktop e conecta no servidor
├── frontend/
│   ├── index.html               # front-end (HTML/CSS/JS)
│   ├── images/
│   ├── winks/
│   └── ruffle/                  # emulador de Flash para os winks (.swf)
├── data/                        # criada automaticamente pelo servidor na primeira execucao
├── requirements.txt
└── README.md
```

## Instalação

### Opção 1 — Ambiente virtual Python (recomendado)

Em **cada máquina que for rodar o cliente**:

```bash
python3 -m venv venv
venv\Scripts\activate # Linux/Mac: source venv/bin/activate
pip install pywebview
```

O servidor não precisa de instalação: usa só bibliotecas padrão do Python.

`pywebview` usa o motor de navegador já instalado no sistema (Edge WebView2 no Windows, GTK/QT no Linux). Se faltar o WebView2 no Windows, o instalador está em https://developer.microsoft.com/microsoft-edge/webview2/.

### Opção 2 — Máquina virtual com ambiente pronto

Se não quiser instalar nada nos computadores do laboratório, é possível rodar o cliente dentro de uma máquina virtual (VirtualBox, VMware) que já tenha Python e `pywebview` configurados. Basta garantir que a VM esteja na mesma rede que o servidor.

## Teste rápido na mesma máquina

```bash
# terminal 1 — servidor
cd backend
python3 server.py --host 0.0.0.0 --port 5000

# terminal 2 — cliente (pode abrir varios, um por usuario)
cd backend
venv\Scripts\activate # Linux/Mac: source venv/bin/activate
python3 client.py
```

Na tela de login, use **Endereço = 127.0.0.1** e **Porta = 5000**. Crie uma conta pelo link "Faça uma nova conta" e depois faça login normalmente.

## Rodando em duas (ou mais) máquinas na rede do laboratório

### 1. Na máquina que será o servidor

Descubra o IP local:

- **Windows:** `ipconfig` → "Endereço IPv4" da placa ativa
- **Linux/Mac:** `ip addr show` → IP da interface conectada à rede (normalmente `192.168.x.x` ou `10.x.x.x`)

Inicie o servidor a partir da raiz do projeto:

```bash
python backend/server.py --host 0.0.0.0 --port 5000
```

`--host 0.0.0.0` faz o servidor escutar em todas as interfaces — necessário para aceitar conexões de outros computadores. `--port` pode ser trocado se 5000 estiver ocupada.

**Libere a porta no firewall:**

- **Windows (PowerShell como administrador):**
  ```powershell
  New-NetFirewallRule -DisplayName "Chat RC2" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow
  ```
- **Linux:** `sudo ufw allow 5000/tcp`
- **Mac:** Preferências do Sistema → Segurança → Firewall → Opções → permitir conexões de entrada para o Python

### 2. Nas máquinas cliente

```bash
cd backend
venv\Scripts\activate # Linux/Mac: source venv/bin/activate
python3 client.py
```

Na tela de login:

- **Endereço do servidor:** IP anotado no passo anterior (ex.: `192.168.0.12`) — não use `localhost` se servidor e cliente estiverem em máquinas diferentes
- **Porta:** a mesma do `--port` do servidor (padrão `5000`)
- Crie uma conta pelo link "Faça uma nova conta" e depois faça login normalmente

## 👩‍💻 Autoras

**Carolina de Moraes Carneiro** e **Cibelly Henrique Nogueira Batista**
Projeto desenvolvido para a disciplina de Redes de Computadores 2.

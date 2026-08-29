# Chat Cliente-Servidor

Chat em rede com servidor TCP em Python (`socket` + `threading`) e cliente desktop que abre o front-end (`index.html`) numa janela via `pywebview`.

Este README cobre só **instalação e execução**. Protocolo, decisões de projeto e demais detalhes técnicos estão no relatório.

## Estrutura de pastas

```
.
├── backend/
│   ├── server/
│   │   └── server.py           # servidor; roda sozinho, sem interface grafica
│   └── client/
│       ├── client.py           # abre index.html numa janela desktop e conecta no servidor
│       └── teste_webview.py    # checa se o WebView2/GTK esta funcionando na maquina
├── frontend/
│   ├── index.html              # front-end (HTML/CSS/JS)
│   ├── images/
│   ├── winks/
│   └── ruffle/                 # emulador de Flash para os winks (.swf)
├── data/                       # criada automaticamente pelo servidor na primeira execucao
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

Para testar se o WebView2 está funcionando antes de rodar o cliente:

```bash
python backend/client/teste_webview.py
```

### Opção 2 — Máquina virtual com ambiente pronto

Se não quiser instalar nada nos computadores do laboratório, é possível rodar o cliente dentro de uma máquina virtual (VirtualBox, VMware) que já tenha Python e `pywebview` configurados. Basta garantir que a VM esteja na mesma rede que o servidor.

## Teste rápido na mesma máquina

```bash
# terminal 1 — servidor
cd backend/server
python3 server.py --host 0.0.0.0 --port 5000

# terminal 2 — cliente (pode abrir varios, um por usuario)
cd backend/client
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
python backend/server/server.py --host 0.0.0.0 --port 5000
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
cd backend/client
venv\Scripts\activate # Linux/Mac: source venv/bin/activate
python3 client.py
```

Na tela de login:

- **Endereço do servidor:** IP anotado no passo anterior (ex.: `192.168.0.12`) — não use `localhost` se servidor e cliente estiverem em máquinas diferentes
- **Porta:** a mesma do `--port` do servidor (padrão `5000`)
- Crie uma conta pelo link "Faça uma nova conta" e depois faça login normalmente
# Discentes: Carolina de Moraes Carneiro (202410077)
#            Cibelly Henrique Nogueira Batista (202410076)            

# server.py: servidor de chat cliente-servidor

import argparse
import base64
import hashlib
import json
import os
import queue
import re
import socket
import threading
import time
import unicodedata
from datetime import datetime

# --------------------------------------------------------------------------
# 0. VISUAL DO TERMINAL
# --------------------------------------------------------------------------

if os.name == "nt":
  # truque pra habilitar cores ANSI no cmd.exe/PowerShell mais antigos 
  os.system("")


class Cor:
  RESET = "\033[0m"
  NEGRITO = "\033[1m"
  CINZA = "\033[90m"
  VERMELHO = "\033[91m"
  VERDE = "\033[92m"
  AMARELO = "\033[93m"
  AZUL = "\033[94m"
  MAGENTA = "\033[95m"
  CIANO = "\033[96m"
  BRANCO = "\033[97m"


def colorir(texto, cor):
  return f"{cor}{texto}{Cor.RESET}"


#-------------------------------------------------------
# desenha o cabecalho de abertura do servidor: titulo do
# trabalho, dupla e onde ele esta escutando
#-------------------------------------------------------
def imprimir_banner(host, porta):
  largura = 64
  topo = "╔" + "═" * (largura - 2) + "╗"
  base = "╚" + "═" * (largura - 2) + "╝"
  meio = "╠" + "═" * (largura - 2) + "╣"

  def linha(texto="", cor=Cor.BRANCO, centralizar=False):
    texto = texto[:largura - 4] # nunca deixa passar da caixa
    conteudo = texto.center(largura - 4) if centralizar else texto.ljust(largura - 4)
    print(f"{Cor.CIANO}║ {Cor.RESET}{colorir(conteudo, cor)}{Cor.CIANO} ║{Cor.RESET}")

  print(f"{Cor.CIANO}{topo}{Cor.RESET}")
  linha("CHAT CLIENTE-SERVIDOR", Cor.NEGRITO + Cor.BRANCO, centralizar=True)
  linha("Redes de Computadores 2", Cor.CIANO, centralizar=True)
  print(f"{Cor.CIANO}{meio}{Cor.RESET}")
  linha("Discentes:", Cor.CINZA)
  linha("  Carolina de Moraes Carneiro   (202410077)", Cor.BRANCO)
  linha("  Cibelly Henrique N. Batista   (202410076)", Cor.BRANCO)
  print(f"{Cor.CIANO}{meio}{Cor.RESET}")
  linha(f"Escutando em:  {host}:{porta}", Cor.VERDE)
  linha("Pressione Ctrl+C para encerrar", Cor.CINZA)
  print(f"{Cor.CIANO}{base}{Cor.RESET}")


# --------------------------------------------------------------------------
# 1. CONFIGURACAO INICIAL
# --------------------------------------------------------------------------

DIR_BASE = os.path.dirname(os.path.abspath(__file__))  # pasta onde este arquivo esta

# sobe um nivel (backend -> raiz) para chegar em data/, que fica na raiz
# do projeto junto com backend/ e frontend/
DIR_DADOS = os.path.abspath(os.path.join(DIR_BASE, os.pardir, "data"))
ARQ_USUARIOS = os.path.join(DIR_DADOS, "usuarios.json")  # cadastro de usuarios (hash + salt)
ARQ_SALAS = os.path.join(DIR_DADOS, "salas.json")  # donos, membros e pendentes de cada sala

# arquivo_salvo -> sala ou par de usuarios
ARQ_ARQUIVOS_META = os.path.join(DIR_DADOS, "arquivos_meta.json")

DIR_HISTORICO = os.path.join(DIR_DADOS, "historico")  # um .json por sala/conversa privada

# arquivos ficam fora dos .json de historico para nao inflar o historico
# com binario e permitir baixar depois (via PEDIR_ARQUIVO) sem guardar tudo em memoria
DIR_ARQUIVOS = os.path.join(DIR_DADOS, "arquivos_recebidos")

TAMANHO_MAX_ARQUIVO = 5 * 1024 * 1024  # 5 MB por arquivo

# maior mensagem possivel: arquivo em base64 (+33% de overhead) + 1 MB de folga
# evita buffer de leitura crescer indefinidamente se o cliente nunca fechar a linha
TAMANHO_MAX_BUFFER = (TAMANHO_MAX_ARQUIVO * 4 // 3) + (1 * 1024 * 1024)

MAX_CLIENTES_SIMULTANEOS = 50  # conexoes aceitas ao mesmo tempo

SALA_GERAL = "geral"  # sala publica; todo usuario entra nela ao logar
QTD_HISTORICO_PADRAO = 50  # mensagens antigas enviadas por padrao
TIMEOUT_SOCK = 30  # segundos; limite por operacao de socket
TAMANHO_MIN_SENHA = 8  # minimo de caracteres na senha
TAMANHO_MAX_NOME_SALA = 60  # nomes longos viram nomes de arquivo invalidos

# TCP keepalive: detecta cliente que caiu sem avisar (crash, queda de rede).
KEEPALIVE_OCIOSO_SEGUNDOS = 10
KEEPALIVE_INTERVALO_SEGUNDOS = 5 # o SO sonda apos ocioso segundos sem trafego
KEEPALIVE_TENTATIVAS = 3 # e desiste apos TENTATIVAS sem resposta

os.makedirs(DIR_DADOS, exist_ok=True)  # garante que as pastas de dados existam
os.makedirs(DIR_HISTORICO, exist_ok=True)
os.makedirs(DIR_ARQUIVOS, exist_ok=True)


# --------------------------------------------------------------------------
# 2. PERSISTENCIA SIMPLES EM ARQUIVOS JSON (SEM BANCO DE DADOS) +
#    HASHING DE SENHA
# --------------------------------------------------------------------------

lock_dados = threading.RLock()  # protege leitura/escrita dos arquivos de historico


#-------------------------------------------------------
# le um JSON do disco; se nao existir ou estiver
# corrompido, devolve o valor padrao
#-------------------------------------------------------
def carregar_json(caminho, padrao):
  if not os.path.exists(caminho): # arquivo ainda nao existe
    return padrao
  try:
    with open(caminho, "r", encoding="utf-8") as f:
      return json.load(f) # le e converte o conteudo para objeto Python
  except (json.JSONDecodeError, OSError): # arquivo corrompido ou ilegivel
    return padrao


#-------------------------------------------------------
# grava dados como JSON em caminho de forma atomica
#-------------------------------------------------------
def salvar_json(caminho, dados):
  tmp = caminho + ".tmp" # escreve primeiro num arquivo temporario
  with open(tmp, "w", encoding="utf-8") as f:
    json.dump(dados, f, ensure_ascii=False, indent=2) # grava os dados formatados
  os.replace(tmp, caminho) # so troca de nome no final, evita arquivo pela metade se cair no meio da escrita


#-------------------------------------------------------
# gera o hash PBKDF2-HMAC-SHA256 de uma senha
#-------------------------------------------------------
def hash_senha(senha, salt=None):
  if salt is None:
    salt = os.urandom(16) # gera um salt novo (cadastro)
  else:
    salt = bytes.fromhex(salt) # reaproveita o salt ja salvo (login)
  derivado = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, 100_000) # aplica o algoritmo de hash
  return derivado.hex(), salt.hex() # devolve hash e salt como texto hexadecimal


#-------------------------------------------------------
# confere se `senha` bate com o hash/salt cadastrados
#-------------------------------------------------------
def verificar_senha(senha, hash_esperado, salt_hex):
  calculado, _ = hash_senha(senha, salt_hex) # recalcula o hash com o mesmo salt
  return calculado == hash_esperado # compara com o hash gravado


#-------------------------------------------------------
# normaliza um nome de usuario so pra COMPARACAO
# (maiusculas/acentos nao contam como nomes diferentes)
#-------------------------------------------------------
def normalizar_usuario(nome):
  sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii") # remove acentos
  return sem_acento.strip().lower() # tira espacos das pontas e poe em minusculo


#-------------------------------------------------------
# valida o FORMATO de um nome de usuario no cadastro: so
# letras (sem acento), numeros, "_" e "-", sem espaco. Isso
# evita "João Silva" ou "usuário 1" (nome de usuario deve
# ser um "user" mesmo, facil de digitar em qualquer teclado
# e sem ambiguidade de acento entre maquinas diferentes)
#-------------------------------------------------------
PADRAO_USUARIO_VALIDO = re.compile(r"^[A-Za-z0-9_-]+$")

def nome_usuario_valido(nome):
  return bool(nome) and PADRAO_USUARIO_VALIDO.fullmatch(nome) is not None


#-------------------------------------------------------
# busca, entre os cadastrados, o usuario cujo nome
# normalizado bate com `usuario`; devolve o nome ORIGINAL
#-------------------------------------------------------
def buscar_usuario_cadastrado(usuario):
  alvo = normalizar_usuario(usuario) # normaliza o nome que estamos procurando
  for nome_cadastrado in estado.usuarios: # percorre todos os cadastrados
    if normalizar_usuario(nome_cadastrado) == alvo: # compara de forma normalizada
      return nome_cadastrado # encontrou, devolve o nome original
  return None # ninguem cadastrado bate com esse nome


#-------------------------------------------------------
# monta o caminho do historico de uma conversa privada
#-------------------------------------------------------
def nome_arquivo_privado(u1, u2):
  a, b = sorted([u1, u2]) # ordena os dois nomes sempre na mesma ordem, "A com B" cai igual a "B com A"
  return os.path.join(DIR_HISTORICO, f"privado_{a}_{b}.json")


#-------------------------------------------------------
# sanitiza um nome de sala (so letras, numeros, "-", "_")
#-------------------------------------------------------
def _sanitizar_nome_sala(sala):
  return "".join(c for c in sala if c.isalnum() or c in ("-", "_")) or "sala" # remove caracteres invalidos


#-------------------------------------------------------
# monta o caminho do historico de uma sala
#-------------------------------------------------------
def nome_arquivo_sala(sala):
  return os.path.join(DIR_HISTORICO, f"sala_{_sanitizar_nome_sala(sala)}.json")


#-------------------------------------------------------
# chave de comparacao pra nomes de sala (junta sanitizacao
# de arquivo com normalizacao de maiuscula/acento, assim
# "Sala Top" e "sala-top" contam como a mesma sala)
#-------------------------------------------------------
def chave_sala(nome):
  return normalizar_usuario(_sanitizar_nome_sala(nome))


#-------------------------------------------------------
# acrescenta uma entrada ao final do historico salvo em
# `caminho`
#-------------------------------------------------------
def registrar_no_historico(caminho, entrada):
  with lock_dados: # evita corrida entre threads escrevendo no mesmo arquivo
    historico = carregar_json(caminho, []) # le o historico atual (ou lista vazia)
    historico.append(entrada) # acrescenta a nova mensagem/evento
    salvar_json(caminho, historico) # regrava o arquivo inteiro


#-------------------------------------------------------
# le o historico salvo em `caminho` e devolve so as
# ultimas `qtd` entradas
#-------------------------------------------------------
def ler_historico(caminho, qtd=QTD_HISTORICO_PADRAO):
  with lock_dados:
    historico = carregar_json(caminho, [])
    return historico[-qtd:] # so as ultimas qtd entradas


#-------------------------------------------------------
# devolve o instante atual formatado como string legivel
#-------------------------------------------------------
def timestamp():
  return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


#-------------------------------------------------------
# print padronizado do debug do servidor: sempre com
# horario na frente, pra dar pra saber QUANDO cada coisa
# aconteceu (util pra comparar com o horario que o
# problema foi percebido no teste). A cor muda conforme o
# tipo de evento, pra ficar facil de escanear o terminal
# visualmente durante o teste (verde = gente entrando,
# amarelo = saindo, vermelho = erro/problema, ciano = sala)
#-------------------------------------------------------
def log(mensagem):
  texto = mensagem.lower()
  if "erro" in texto or "cheio" in texto:
    cor = Cor.VERMELHO
  elif "desconectou" in texto or "encerrada" in texto:
    cor = Cor.AMARELO
  elif "entrou na sala" in texto or "criou e entrou" in texto:
    cor = Cor.CIANO
  elif "nova conexao" in texto:
    cor = Cor.VERDE
  else:
    cor = Cor.BRANCO
  print(f"{Cor.CINZA}[{timestamp()}]{Cor.RESET} {colorir(mensagem, cor)}")


# --------------------------------------------------------------------------
# 3. ESTADO EM MEMORIA DO SERVIDOR
# --------------------------------------------------------------------------

class EstadoServidor:
  # concentra tudo que precisa ser compartilhado entre as threads dos
  # clientes; qualquer leitura/escrita nele deve acontecer dentro do self.lock

  #-------------------------------------------------------
  # inicializa o estado compartilhado do servidor
  #-------------------------------------------------------
  def __init__(self):
    self.lock = threading.RLock() # protege todo o estado compartilhado abaixo

    self.clientes = {} # usuario (str) -> socket.socket

    # usuario -> queue.Queue: fila de saida dedicada de cada cliente. cada
    # cliente tem sua propria thread escritora (_thread_escritor) consumindo
    # essa fila; assim um cliente lento so bloqueia a thread que escreve pra
    # ele, nunca quem esta originando um broadcast
    self.filas_saida = {}

    # usuario -> "online" | "offline"; so existe pra quem esta CONECTADO no
    # momento -- "offline" aqui significa "conectado mas escolheu aparecer
    # offline", nao "desconectado"
    self.status = {}

    self.usuarios = carregar_json(ARQ_USUARIOS, {}) # usuario -> {"hash":..., "salt":...}
    self.salas = carregar_json(ARQ_SALAS, {}) # nome -> {"dono":..., "membros":[...], "pendentes":[...]}

    if SALA_GERAL not in self.salas: # primeira vez que o servidor roda
      self.salas[SALA_GERAL] = {"dono": None, "membros": [], "pendentes": []}
      salvar_json(ARQ_SALAS, self.salas)

    # arquivo_salvo -> {"tipo": "sala", "sala": str} ou
    # {"tipo": "privada", "usuarios": [str, str]} -- usado pra checar
    # permissao de download/historico
    self.arquivos_meta = carregar_json(ARQ_ARQUIVOS_META, {})

  #-------------------------------------------------------
  # persiste os usuarios cadastrados em disco
  #-------------------------------------------------------
  def salvar_usuarios(self):
    salvar_json(ARQ_USUARIOS, self.usuarios)

  #-------------------------------------------------------
  # persiste as salas (donos, membros, pendentes) em disco
  #-------------------------------------------------------
  def salvar_salas(self):
    salvar_json(ARQ_SALAS, self.salas)

  #-------------------------------------------------------
  # persiste os metadados de dono/canal de cada arquivo
  #-------------------------------------------------------
  def salvar_arquivos_meta(self):
    salvar_json(ARQ_ARQUIVOS_META, self.arquivos_meta)


estado = EstadoServidor() # instancia unica, compartilhada por todas as threads


# --------------------------------------------------------------------------
# 4. ENVIO DE MENSAGENS
# --------------------------------------------------------------------------

#-------------------------------------------------------
# envia `obj` como JSON direto no socket `conn`. So usar
# para o proprio socket do cliente que chamou (respostas
# imediatas/erros); para mandar a OUTRO usuario, usar
# `enviar_para_usuario`
#-------------------------------------------------------
def enviar(conn, obj):
  try:
    linha = json.dumps(obj, ensure_ascii=False) + "\n" # serializa e adiciona o separador de mensagem
    conn.sendall(linha.encode("utf-8")) # envia os bytes pelo socket
  except OSError: # o socket ja caiu, nao ha o que fazer aqui
    pass


#-------------------------------------------------------
# thread dedicada de escrita de um cliente: consome a
# `fila` dele e envia cada item pelo socket
#-------------------------------------------------------
def _thread_escritor(handler, usuario, conn, fila):
  while True:
    obj = fila.get() # espera ate ter algo pra enviar (bloqueia a thread, nao o resto do servidor)
    if obj is None: # sinal de "pode encerrar", ver _desconectar
      break
    try:
      linha = json.dumps(obj, ensure_ascii=False) + "\n"
      conn.sendall(linha.encode("utf-8")) # se o cliente estiver lento/travado, so essa thread fica presa aqui
    except OSError: # falha ao enviar, o cliente provavelmente caiu
      handler._desconectar()
      break
  with estado.lock:
    if estado.filas_saida.get(usuario) is fila: # so remove se ainda for a fila atual desse usuario
      del estado.filas_saida[usuario]


#-------------------------------------------------------
# enfileira `obj` pra `usuario`; nunca bloqueia quem chama
#-------------------------------------------------------
def enviar_para_usuario(usuario, obj):
  with estado.lock:
    fila = estado.filas_saida.get(usuario) # pega a fila de saida do destinatario
  if fila:
    fila.put(obj) # so coloca na fila, quem envia de fato e a thread escritora dele


#-------------------------------------------------------
# envia `obj` pra todos os conectados (menos `excluir`)
#-------------------------------------------------------
def broadcast_geral(obj, excluir=None):
  with estado.lock:
    destinatarios = [u for u in estado.clientes if u != excluir] # todo mundo, menos quem foi excluido
  for usuario in destinatarios:
    enviar_para_usuario(usuario, obj)


#-------------------------------------------------------
# envia `obj` pros membros conectados de `sala`
#-------------------------------------------------------
def broadcast_sala(sala, obj, excluir=None):
  with estado.lock:
    info = estado.salas.get(sala)
    if not info: # sala nao existe (nao deveria acontecer, mas evita crash)
      return
    destinatarios = [
      u for u in info["membros"]
      if u != excluir and u in estado.clientes # so quem esta conectado agora
    ]
  for usuario in destinatarios:
    enviar_para_usuario(usuario, obj)


#-------------------------------------------------------
# envia uma mensagem de erro padronizada (tipo "ERRO")
#-------------------------------------------------------
def erro(conn, mensagem, contexto=None):
  enviar(conn, {"tipo": "ERRO", "mensagem": mensagem, "contexto": contexto})


#-------------------------------------------------------
# valida o campo "conteudo" de uma mensagem de texto
#-------------------------------------------------------
def conteudo_valido(conteudo):
  return isinstance(conteudo, str) and conteudo.strip() != "" # precisa ser string e nao ficar vazio depois de tirar espacos


# --------------------------------------------------------------------------
# 5. MONTAR ESTRUTURAS PARA MANDAR AO CLIENTE
# --------------------------------------------------------------------------

#-------------------------------------------------------
# monta a lista de usuarios conectados com seu status
#-------------------------------------------------------
def montar_lista_usuarios():
  with estado.lock:
    return [
      {"usuario": u, "status": estado.status.get(u, "online")}
      for u in sorted(estado.clientes.keys()) # ordem alfabetica
    ]


#-------------------------------------------------------
# lista todos os usuarios ja cadastrados, online ou nao
#-------------------------------------------------------
def listar_usuarios_cadastrados():
  with estado.lock:
    return sorted(estado.usuarios.keys())


#-------------------------------------------------------
# monta a estrutura de salas do ponto de vista de usuario
#-------------------------------------------------------
def montar_salas_para(usuario):
  with estado.lock:
    minhas = [] # salas onde o usuario e dono ou membro
    outras = [] # salas que existem mas o usuario nao participa
    pendentes_por_sala = {} # pedidos de entrada esperando aprovacao (so das salas dele)
    for nome, info in estado.salas.items():
      if nome == SALA_GERAL: # a sala geral nao entra nessas listas
        continue
      if usuario in info["membros"] or info["dono"] == usuario:
        minhas.append({"sala": nome, "dono": info["dono"]})
        if info["dono"] == usuario and info["pendentes"]:
          pendentes_por_sala[nome] = list(info["pendentes"])
      else:
        aguardando = usuario in info["pendentes"] # ja pediu entrada e esta esperando?
        outras.append({"sala": nome, "dono": info["dono"], "aguardando": aguardando})
    return {
      "tipo": "SALAS",
      "geral": SALA_GERAL,
      "minhas": minhas,
      "outras": outras,
      "pendentes": pendentes_por_sala,
    }


# --------------------------------------------------------------------------
# 6. TRATAMENTO DE CADA TIPO DE MENSAGEM -- UM HANDLER POR TIPO, AGRUPADOS
#    POR FUNCIONALIDADE DENTRO DA CLASSE (VER SECOES INTERNAS ABAIXO)
# --------------------------------------------------------------------------

class ClienteHandler:
  # uma instancia por conexao de cliente; self.usuario fica None ate o
  # LOGIN ser confirmado

  #-------------------------------------------------------
  # inicializa o estado de uma conexao recem-aceita
  #-------------------------------------------------------
  def __init__(self, conn, addr):
    self.conn = conn # socket da conexao com esse cliente
    self.addr = addr # endereco (ip, porta) do cliente
    self.usuario = None # so recebe valor apos login confirmado
    self.buffer = b"" # acumula bytes recebidos ate achar uma linha completa ("\n")
    self._ja_desconectou = False # evita logar a mesma desconexao duas vezes (thread de leitura + escrita)

    # protege _desconectar() de rodar em paralelo consigo mesma: tanto a
    # thread de leitura quanto a de escrita podem tentar desconectar o mesmo
    # cliente ao mesmo tempo (ver comentario completo em _desconectar)
    self._lock_desconexao = threading.Lock()

  # ----------------------------------------------------------------
  # 6.1 LOOP DE LEITURA / ROTEAMENTO
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # loop principal da thread do cliente: le do socket,
  # remonta as mensagens JSON e despacha cada uma
  #-------------------------------------------------------
  def rodar(self):
    try:
      while True:
        try:
          dados = self.conn.recv(65536) # tenta ler mais bytes do socket
        except socket.timeout:
          continue # nada chegou nesse intervalo, normal em chat ocioso, so volta a esperar
        if not dados: # cliente fechou a conexao (FIN do TCP)
          break
        self.buffer += dados # acumula os bytes crus, sem decodificar ainda

        # protecao contra buffer sem limite: um cliente que nunca fecha a
        # linha faria self.buffer crescer pra sempre
        if len(self.buffer) > TAMANHO_MAX_BUFFER:
          erro(self.conn, "Dados recebidos sem terminador de mensagem excederam o limite; conexao encerrada.")
          break

        # TCP e um fluxo continuo de bytes, sem fronteiras de mensagem;
        # processamos linha a linha conforme "\n" aparece no buffer
        while b"\n" in self.buffer:
          linha_bytes, self.buffer = self.buffer.split(b"\n", 1) # separa a primeira linha do resto
          linha = linha_bytes.decode("utf-8", errors="replace").strip()
          if not linha: # linha em branco, ignora
            continue
          self._processar_linha(linha)
    except (ConnectionResetError, ConnectionAbortedError, OSError): # conexao caiu de forma abrupta
      pass
    finally:
      self._desconectar() # garante a limpeza mesmo se algo der errado acima

  #-------------------------------------------------------
  # interpreta uma linha (uma mensagem JSON) e despacha
  # pro handler certo em ROTEADOR
  #-------------------------------------------------------
  def _processar_linha(self, linha):
    try:
      msg = json.loads(linha) # converte o texto em objeto Python
    except json.JSONDecodeError:
      erro(self.conn, "Mensagem mal formada (JSON invalido).")
      return

    tipo = msg.get("tipo")
    handler = ROTEADOR.get(tipo) # busca a funcao responsavel por esse tipo de mensagem
    if not handler:
      erro(self.conn, f"Tipo de mensagem desconhecido: {tipo}")
      return

    if tipo not in ("REGISTRAR", "LOGIN") and self.usuario is None: # so permite REGISTRAR/LOGIN antes de autenticado
      erro(self.conn, "Voce precisa fazer login primeiro.")
      return

    handler(self, msg) # despacha para o handler correspondente

  # ----------------------------------------------------------------
  # 6.2 AUTENTICACAO (CADASTRO E LOGIN)
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # cria um novo cadastro de usuario
  #-------------------------------------------------------
  def h_registrar(self, msg):
    usuario = (msg.get("usuario") or "").strip()
    senha = msg.get("senha") or ""
    if not usuario or not senha: # campos obrigatorios
      enviar(self.conn, {"tipo": "REGISTRO_ERRO", "mensagem": "Usuario e senha sao obrigatorios."})
      return
    if not nome_usuario_valido(usuario): # sem espaco e sem acento, so letras/numeros/"_"/"-"
      enviar(self.conn, {
        "tipo": "REGISTRO_ERRO",
        "mensagem": "Nome de usuario invalido: use apenas letras (sem acento), numeros, '_' ou '-', sem espacos.",
      })
      return
    if len(senha) < TAMANHO_MIN_SENHA: # senha curta demais
      enviar(self.conn, {"tipo": "REGISTRO_ERRO", "mensagem": f"Senha deve ter pelo menos {TAMANHO_MIN_SENHA} caracteres."})
      return
    with estado.lock:
      if buscar_usuario_cadastrado(usuario) is not None: # nome ja existe (comparacao normalizada, ignora maiuscula/acento)
        enviar(self.conn, {"tipo": "REGISTRO_ERRO", "mensagem": "Esse nome de usuario ja existe (maiusculas/minusculas e acentos nao contam como nomes diferentes)."})
        return
      h, salt = hash_senha(senha) # gera hash e salt novos
      estado.usuarios[usuario] = {"hash": h, "salt": salt, "criado_em": timestamp()}
      estado.salvar_usuarios() # persiste o novo cadastro em disco
    enviar(self.conn, {"tipo": "REGISTRO_OK", "mensagem": "Conta criada com sucesso. Faca login."})
    broadcast_geral({"tipo": "LISTA", "usuarios": montar_lista_usuarios(), "cadastrados": listar_usuarios_cadastrados()}) # atualiza a contagem X/Y pra quem ja esta conectado

  #-------------------------------------------------------
  # autentica um usuario existente e abre a sessao dele
  #-------------------------------------------------------
  def h_login(self, msg):
    usuario_digitado = (msg.get("usuario") or "").strip()
    senha = msg.get("senha") or ""
    with estado.lock:
      usuario = buscar_usuario_cadastrado(usuario_digitado) # resolve pro nome exato do cadastro
      cadastro = estado.usuarios.get(usuario) if usuario else None
      if not cadastro or not verificar_senha(senha, cadastro["hash"], cadastro["salt"]):
        enviar(self.conn, {"tipo": "LOGIN_ERRO", "mensagem": "Usuario ou senha invalidos."})
        return
      if usuario in estado.clientes: # ja tem uma sessao aberta com esse nome
        enviar(self.conn, {"tipo": "LOGIN_ERRO", "mensagem": "Esse usuario ja esta conectado."})
        return
      # daqui pra frente sempre usamos o nome CANONICO (como foi cadastrado),
      # nunca a grafia que o usuario digitou no login
      estado.clientes[usuario] = self.conn
      fila = queue.Queue() # fila de saida dedicada desse cliente
      estado.filas_saida[usuario] = fila
      estado.status[usuario] = "online"
      if usuario not in estado.salas[SALA_GERAL]["membros"]: # garante presenca na sala geral
        estado.salas[SALA_GERAL]["membros"].append(usuario)
        estado.salvar_salas()
    self.usuario = usuario
    threading.Thread(target=_thread_escritor, args=(self, usuario, self.conn, fila), daemon=True).start() # dispara a thread escritora dele

    enviar(self.conn, {"tipo": "LOGIN_OK", "usuario": usuario})
    with estado.lock:
      criado_em = estado.usuarios.get(usuario, {}).get("criado_em", "")
    enviar(self.conn, { # historico da sala geral para o recem-chegado
      "tipo": "HISTORICO", "canal_tipo": "sala", "canal": SALA_GERAL,
      "mensagens": [m for m in ler_historico(nome_arquivo_sala(SALA_GERAL)) if m.get("timestamp", "") >= criado_em], # so mensagens depois do cadastro dele
    })
    enviar(self.conn, {"tipo": "LISTA", "usuarios": montar_lista_usuarios(), "cadastrados": listar_usuarios_cadastrados()})
    enviar(self.conn, montar_salas_para(usuario))

    entrada_entrou = {"tipo": "ENTROU", "usuario": usuario, "timestamp": timestamp()} # notifica entrada pra todo mundo
    registrar_no_historico(nome_arquivo_sala(SALA_GERAL), entrada_entrou)
    broadcast_geral(entrada_entrou)
    broadcast_geral({"tipo": "LISTA", "usuarios": montar_lista_usuarios(), "cadastrados": listar_usuarios_cadastrados()})

  # ----------------------------------------------------------------
  # 6.3 MENSAGENS (GERAL, PRIVADA, SALA)
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # mensagem para o chat geral: valida, grava e distribui
  #-------------------------------------------------------
  def h_msg_geral(self, msg):
    conteudo = msg.get("conteudo", "") # texto digitado pelo cliente
    if not conteudo_valido(conteudo): # o front-end ja bloqueia isso, mas o servidor nao confia so no cliente
      erro(self.conn, "Mensagem vazia ou invalida.")
      return
    entrada = {"tipo": "MSG_GERAL", "de": self.usuario, "conteudo": conteudo, "timestamp": timestamp()} # monta o registro da mensagem
    registrar_no_historico(nome_arquivo_sala(SALA_GERAL), entrada) # salva no historico da sala geral
    broadcast_geral(entrada) # distribui pra todo mundo conectado

  #-------------------------------------------------------
  # mensagem privada pra outro usuario
  #-------------------------------------------------------
  def h_msg_privada(self, msg):
    para = msg.get("para")
    conteudo = msg.get("conteudo", "")
    if not conteudo_valido(conteudo):
      erro(self.conn, "Mensagem vazia ou invalida.")
      return
    if not isinstance(para, str) or not para.strip(): # sem isso, nome_arquivo_privado tentaria ordenar [usuario, None] e quebraria
      erro(self.conn, "Destinatario da mensagem privada nao informado.")
      return
    with estado.lock:
      para_real = buscar_usuario_cadastrado(para) # resolve pro nome exato do cadastro
    if para_real is None: # sem essa checagem, mensagem pra nome digitado errado "funcionaria" sem nunca chegar a ninguem
      erro(self.conn, f"Usuario '{para}' nao existe.", contexto=para)
      return
    entrada = {
      "tipo": "MSG_PRIVADA", "de": self.usuario, "para": para_real,
      "conteudo": conteudo, "timestamp": timestamp(),
    }
    registrar_no_historico(nome_arquivo_privado(self.usuario, para_real), entrada)
    enviar_para_usuario(para_real, entrada)
    if para_real != self.usuario: # evita duplicar quando alguem manda mensagem pra si mesmo
      enviar(self.conn, entrada) # eco para o proprio remetente ver na sua tela

  #-------------------------------------------------------
  # mensagem para uma sala especifica
  #-------------------------------------------------------
  def h_msg_sala(self, msg):
    sala = msg.get("sala") # nome da sala de destino
    conteudo = msg.get("conteudo", "")
    if not conteudo_valido(conteudo):
      erro(self.conn, "Mensagem vazia ou invalida.")
      return
    with estado.lock:
      info = estado.salas.get(sala)
      membro = info and self.usuario in info["membros"] # so pode mandar quem ja e membro
    if not info:
      erro(self.conn, f"Sala '{sala}' nao existe.")
      return
    if not membro:
      erro(self.conn, f"Voce nao e membro da sala '{sala}'.")
      return
    entrada = {
      "tipo": "MSG_SALA", "de": self.usuario, "sala": sala,
      "conteudo": conteudo, "timestamp": timestamp(),
    }
    registrar_no_historico(nome_arquivo_sala(sala), entrada) # salva no historico dessa sala
    broadcast_sala(sala, entrada) # distribui so pros membros conectados

  # ----------------------------------------------------------------
  # 6.4 SALAS (CRIAR, SOLICITAR ENTRADA, APROVAR/RECUSAR, LISTAR)
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # cria uma nova sala com o remetente como dono
  #-------------------------------------------------------
  def h_criar_sala(self, msg):
    sala = (msg.get("sala") or "").strip() # nome digitado pelo usuario
    if not sala: # nome vazio
      erro(self.conn, "Nome de sala invalido.")
      return
    if len(sala) > TAMANHO_MAX_NOME_SALA: # nome longo demais
      erro(self.conn, f"Nome de sala muito longo (maximo {TAMANHO_MAX_NOME_SALA} caracteres).")
      return
    chave_nova = chave_sala(sala) # chave normalizada, usada pra checar duplicidade abaixo
    if chave_nova == SALA_GERAL: # bloqueia "Geral", "GERAL", "ge ral" etc, nome reservado
      erro(self.conn, "Nome de sala invalido.")
      return
    with estado.lock:
      if any(chave_sala(existente) == chave_nova for existente in estado.salas): # colide com sala ja existente (mesma chave normalizada)
        erro(self.conn, f"Ja existe uma sala com esse nome (ou bem parecido): '{sala}'.")
        return
      estado.salas[sala] = {"dono": self.usuario, "membros": [self.usuario], "pendentes": []}
      estado.salvar_salas()
    log(f"[servidor] {self.usuario} criou e entrou na sala '{sala}'")
    enviar(self.conn, {"tipo": "SALA_CRIADA", "sala": sala, "dono": self.usuario})
    enviar(self.conn, montar_salas_para(self.usuario))
    broadcast_geral({"tipo": "NOVA_SALA_DISPONIVEL", "sala": sala, "dono": self.usuario}, excluir=self.usuario) # avisa geral, pra quem quiser pedir entrada

  #-------------------------------------------------------
  # registra um pedido de entrada numa sala
  #-------------------------------------------------------
  def h_solicitar_entrada(self, msg):
    sala = msg.get("sala") # nome da sala que o usuario quer entrar
    with estado.lock:
      info = estado.salas.get(sala)
      if not info:
        erro(self.conn, f"Sala '{sala}' nao existe.")
        return
      if self.usuario in info["membros"]: # ja e membro, nao precisa pedir
        erro(self.conn, "Voce ja e membro dessa sala.")
        return
      if self.usuario not in info["pendentes"]: # evita duplicar pedido
        info["pendentes"].append(self.usuario)
        estado.salvar_salas()
      dono = info["dono"]
    enviar(self.conn, montar_salas_para(self.usuario))
    if dono: # avisa o dono na hora, se estiver conectado (o pedido fica salvo de qualquer forma)
      enviar_para_usuario(dono, {"tipo": "NOVO_PEDIDO", "sala": sala, "usuario": self.usuario})
      enviar_para_usuario(dono, montar_salas_para(dono))

  #-------------------------------------------------------
  # atalho: dono aprova um pedido pendente
  #-------------------------------------------------------
  def h_aprovar_entrada(self, msg):
    self._resolver_pedido(msg, aprovar=True)

  #-------------------------------------------------------
  # atalho: dono recusa um pedido pendente
  #-------------------------------------------------------
  def h_recusar_entrada(self, msg):
    self._resolver_pedido(msg, aprovar=False)

  #-------------------------------------------------------
  # logica compartilhada por aprovar/recusar pedido
  #-------------------------------------------------------
  def _resolver_pedido(self, msg, aprovar):
    sala = msg.get("sala")
    usuario_alvo = msg.get("usuario")
    with estado.lock:
      info = estado.salas.get(sala)
      if not info or info["dono"] != self.usuario: # so o dono pode decidir
        erro(self.conn, "Apenas o dono da sala pode aprovar/recusar pedidos.")
        return
      if usuario_alvo not in info["pendentes"]:
        erro(self.conn, "Esse pedido nao existe (ja foi resolvido?).")
        return
      info["pendentes"].remove(usuario_alvo)
      if aprovar:
        info["membros"].append(usuario_alvo)
      estado.salvar_salas()
    if aprovar:
      log(f"[servidor] {usuario_alvo} entrou na sala '{sala}'")
    enviar(self.conn, montar_salas_para(self.usuario))
    tipo_evento = "ENTRADA_APROVADA" if aprovar else "ENTRADA_RECUSADA"
    enviar_para_usuario(usuario_alvo, {"tipo": tipo_evento, "sala": sala})
    enviar_para_usuario(usuario_alvo, montar_salas_para(usuario_alvo))
    if aprovar: # avisa quem ja esta na sala que um novo membro entrou (mesmo padrao do ENTROU da sala geral)
      entrada_entrou_sala = {"tipo": "ENTROU_SALA", "sala": sala, "usuario": usuario_alvo, "timestamp": timestamp()}
      registrar_no_historico(nome_arquivo_sala(sala), entrada_entrou_sala)
      broadcast_sala(sala, entrada_entrou_sala)

  #-------------------------------------------------------
  # devolve as salas do ponto de vista do remetente
  #-------------------------------------------------------
  def h_listar_salas(self, msg):
    enviar(self.conn, montar_salas_para(self.usuario))

  # ----------------------------------------------------------------
  # 6.5 ARQUIVOS (ENVIAR, BAIXAR)
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # recebe um arquivo em base64, valida e salva em disco
  #-------------------------------------------------------
  def h_arquivo(self, msg):
    conteudo_b64 = msg.get("conteudo_base64", "")
    tamanho_estimado = len(conteudo_b64) * 3 // 4 # base64 infla o tamanho em ~33%, aqui desfazemos essa conta
    if tamanho_estimado > TAMANHO_MAX_ARQUIVO: # valida tamanho antes de processar/repassar
      erro(self.conn, "Arquivo maior que o limite de 5MB.")
      return
    try:
      dados_binarios = base64.b64decode(conteudo_b64, validate=True) # decodifica de volta para binario
    except Exception:
      erro(self.conn, "Arquivo corrompido (base64 invalido).")
      return

    # resolve e valida o DESTINO antes de gravar qualquer coisa em disco --
    # assim um pedido invalido nunca deixa arquivo/historico orfaos pra tras
    sala = None
    para_real = None
    if msg.get("sala"): # arquivo destinado a uma sala
      sala = msg["sala"]
      with estado.lock:
        info = estado.salas.get(sala)
        membro = info and self.usuario in info["membros"]
      if not membro:
        erro(self.conn, f"Voce nao e membro da sala '{sala}'.")
        return
    else: # arquivo destinado a um usuario especifico (privado)
      para = msg.get("para")
      if not isinstance(para, str) or not para.strip():
        erro(self.conn, "Destinatario do arquivo nao informado.")
        return
      with estado.lock:
        para_real = buscar_usuario_cadastrado(para)
      if not para_real:
        erro(self.conn, f"Usuario '{para}' nao existe.")
        return

    nome_original = msg.get("nome") or "arquivo"
    nome_seguro = "".join(c for c in nome_original if c.isalnum() or c in ("-", "_", ".")) or "arquivo" # remove caracteres invalidos do nome
    arquivo_salvo = f"{int(time.time() * 1000)}_{self.usuario}_{nome_seguro}" # nome unico em disco (timestamp + usuario + nome)
    caminho_disco = os.path.join(DIR_ARQUIVOS, arquivo_salvo)
    try:
      with open(caminho_disco, "wb") as f:
        f.write(dados_binarios) # grava o conteudo binario de fato, fora do historico JSON
    except OSError:
      erro(self.conn, "Nao foi possivel salvar o arquivo no servidor.")
      return

    with estado.lock: # registra quem tem permissao de baixar esse arquivo depois (ver _arquivo_autorizado)
      if sala is not None:
        estado.arquivos_meta[arquivo_salvo] = {"tipo": "sala", "sala": sala}
      else:
        estado.arquivos_meta[arquivo_salvo] = {
          "tipo": "privada", "usuarios": sorted([self.usuario, para_real]),
        }
      estado.salvar_arquivos_meta()

    entrada = { # so os metadados vao na mensagem/historico; o conteudo e pedido depois via PEDIR_ARQUIVO
      "tipo": "ARQUIVO",
      "de": self.usuario,
      "nome": nome_original,
      "tamanho": msg.get("tamanho"),
      "arquivo_salvo": arquivo_salvo,
      "timestamp": timestamp(),
    }
    if sala is not None: # entrega para a sala
      entrada["sala"] = sala
      registrar_no_historico(nome_arquivo_sala(sala), entrada)
      broadcast_sala(sala, entrada)
    else: # entrega privada
      entrada["para"] = para_real
      registrar_no_historico(nome_arquivo_privado(self.usuario, para_real), entrada)
      enviar_para_usuario(para_real, entrada)
      if para_real != self.usuario:
        enviar(self.conn, entrada) # eco para o proprio remetente

  #-------------------------------------------------------
  # confere se `usuario` pode acessar `arquivo_salvo`
  #-------------------------------------------------------
  def _arquivo_autorizado(self, arquivo_salvo, usuario):
    with estado.lock:
      meta = estado.arquivos_meta.get(arquivo_salvo)
      if not meta: # sem metadados, nega por seguranca
        return False
      if meta["tipo"] == "sala":
        info = estado.salas.get(meta["sala"])
        return bool(info) and (usuario in info["membros"] or info["dono"] == usuario)
      if meta["tipo"] == "privada":
        return usuario in meta["usuarios"]
      return False

  #-------------------------------------------------------
  # responde a um pedido de download com o base64 do arquivo
  #-------------------------------------------------------
  def h_pedir_arquivo(self, msg):
    arquivo_salvo = os.path.basename(msg.get("arquivo_salvo") or "") # remove qualquer caminho de pasta embutido (evita path traversal)
    caminho_disco = os.path.join(DIR_ARQUIVOS, arquivo_salvo)
    if not arquivo_salvo or not os.path.isfile(caminho_disco):
      erro(self.conn, "Arquivo nao encontrado no servidor (pode ter sido removido).", contexto=msg.get("arquivo_salvo"))
      return
    if not self._arquivo_autorizado(arquivo_salvo, self.usuario):
      erro(self.conn, "Voce nao tem permissao para baixar esse arquivo.", contexto=arquivo_salvo)
      return
    try:
      with open(caminho_disco, "rb") as f:
        conteudo_b64 = base64.b64encode(f.read()).decode("ascii") # le o binario e codifica em base64
    except OSError:
      erro(self.conn, "Nao foi possivel ler o arquivo no servidor.", contexto=arquivo_salvo)
      return
    enviar(self.conn, {
      "tipo": "ARQUIVO_BYTES",
      "arquivo_salvo": arquivo_salvo,
      "conteudo_base64": conteudo_b64,
    })

  # ----------------------------------------------------------------
  # 6.6 PRESENCA E HISTORICO (LISTAR USUARIOS, STATUS, HISTORICO)
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # devolve a lista atual de usuarios conectados/cadastrados
  #-------------------------------------------------------
  def h_listar(self, msg):
    enviar(self.conn, {"tipo": "LISTA", "usuarios": montar_lista_usuarios(), "cadastrados": listar_usuarios_cadastrados()})

  #-------------------------------------------------------
  # atualiza o status cosmetico (online/offline) do remetente
  #-------------------------------------------------------
  def h_status(self, msg):
    novo_status = msg.get("status")
    if novo_status not in ("online", "offline"):
      erro(self.conn, "status invalido, use 'online' ou 'offline'.")
      return
    with estado.lock:
      estado.status[self.usuario] = novo_status
    broadcast_geral({"tipo": "LISTA", "usuarios": montar_lista_usuarios(), "cadastrados": listar_usuarios_cadastrados()}) # todo mundo recebe a lista atualizada na hora

  #-------------------------------------------------------
  # devolve as ultimas mensagens de uma sala/conversa
  #-------------------------------------------------------
  def h_historico_request(self, msg):
    canal_tipo = msg.get("canal_tipo")
    canal = msg.get("canal")
    if canal_tipo == "sala":
      with estado.lock:
        info = estado.salas.get(canal)
        membro = info and (self.usuario in info["membros"] or info["dono"] == self.usuario)
      if not membro: # sem essa checagem, qualquer logado leria o historico de uma sala restrita
        erro(self.conn, f"Voce nao e membro da sala '{canal}'.")
        return
      caminho = nome_arquivo_sala(canal)
    elif canal_tipo == "privada": # aqui o acesso ja e seguro por construcao: o arquivo e sempre o par self.usuario+canal
      if not isinstance(canal, str) or not canal.strip():
        erro(self.conn, "Canal invalido para HISTORICO_REQUEST.")
        return
      with estado.lock:
        canal_real = buscar_usuario_cadastrado(canal) or canal
      caminho = nome_arquivo_privado(self.usuario, canal_real)
    else:
      erro(self.conn, "canal_tipo invalido para HISTORICO_REQUEST.")
      return
    with estado.lock:
      criado_em = estado.usuarios.get(self.usuario, {}).get("criado_em", "")
    enviar(self.conn, {
      "tipo": "HISTORICO", "canal_tipo": canal_tipo, "canal": canal,
      "mensagens": [m for m in ler_historico(caminho) if m.get("timestamp", "") >= criado_em], # so mensagens depois do cadastro do remetente
    })

  # ----------------------------------------------------------------
  # 6.7 ENCERRAMENTO DE SESSAO
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # encerramento explicito pedido pelo cliente (/sair)
  #-------------------------------------------------------
  def h_sair(self, msg):
    enviar(self.conn, {"tipo": "SAIU_OK"})
    self._desconectar()

  #-------------------------------------------------------
  # limpa uma conexao encerrada (pedido explicito, queda
  # de rede, falha de envio ou keepalive constatando que
  # o peer sumiu)
  #-------------------------------------------------------
  def _desconectar(self):
    # pode ser chamada por DUAS threads do mesmo cliente quase ao mesmo
    # tempo (leitura e escrita); por isso tudo roda dentro do lock, e
    # `usuario` e capturado e zerado logo no inicio, atomicamente: quem
    # chegar primeiro "reivindica" a limpeza, quem chegar depois ve
    # self.usuario ja None e sai sem repetir o SAIU/historico
    with self._lock_desconexao:
      usuario = self.usuario
      self.usuario = None
      if usuario is None: # ja foi desconectado por outra thread, so garante o socket fechado
        if not self._ja_desconectou: # so a PRIMEIRA chamada loga; evita linha duplicada
          self._ja_desconectou = True
          log(f"[servidor] conexao de {self.addr[0]}:{self.addr[1]} encerrada (sem login concluido)")
        try:
          self.conn.close()
        except OSError:
          pass
        return
      self._ja_desconectou = True
      log(f"[servidor] {usuario} desconectou ({self.addr[0]}:{self.addr[1]})")
      with estado.lock:
        if estado.clientes.get(usuario) is self.conn: # so remove se ainda for essa mesma conexao
          del estado.clientes[usuario]
        estado.status.pop(usuario, None)
        fila = estado.filas_saida.get(usuario)
      if fila:
        fila.put(None) # sinaliza pra _thread_escritor desse usuario parar
      try:
        self.conn.close()
      except OSError:
        pass
      entrada_saiu = {"tipo": "SAIU", "usuario": usuario, "timestamp": timestamp()} # notifica a saida pra todo mundo
      registrar_no_historico(nome_arquivo_sala(SALA_GERAL), entrada_saiu)
      broadcast_geral(entrada_saiu)
      broadcast_geral({"tipo": "LISTA", "usuarios": montar_lista_usuarios(), "cadastrados": listar_usuarios_cadastrados()})


# --------------------------------------------------------------------------
# 7. ROTEADOR DE MENSAGENS: TIPO RECEBIDO -> METODO HANDLER CORRESPONDENTE
# --------------------------------------------------------------------------

ROTEADOR = {
  # cada chave e o "tipo" que vem na mensagem do cliente; o valor e o
  # metodo de ClienteHandler que sabe tratar aquele tipo (ver _processar_linha)
  "REGISTRAR": ClienteHandler.h_registrar,
  "LOGIN": ClienteHandler.h_login,
  "MSG_GERAL": ClienteHandler.h_msg_geral,
  "MSG_PRIVADA": ClienteHandler.h_msg_privada,
  "MSG_SALA": ClienteHandler.h_msg_sala,
  "CRIAR_SALA": ClienteHandler.h_criar_sala,
  "SOLICITAR_ENTRADA": ClienteHandler.h_solicitar_entrada,
  "APROVAR_ENTRADA": ClienteHandler.h_aprovar_entrada,
  "RECUSAR_ENTRADA": ClienteHandler.h_recusar_entrada,
  "ARQUIVO": ClienteHandler.h_arquivo,
  "PEDIR_ARQUIVO": ClienteHandler.h_pedir_arquivo,
  "LISTAR": ClienteHandler.h_listar,
  "STATUS": ClienteHandler.h_status,
  "LISTAR_SALAS": ClienteHandler.h_listar_salas,
  "HISTORICO_REQUEST": ClienteHandler.h_historico_request,
  "SAIR": ClienteHandler.h_sair,
}


# --------------------------------------------------------------------------
# 8. BOOTSTRAP DO SERVIDOR
# --------------------------------------------------------------------------

#-------------------------------------------------------
# liga o TCP keepalive no socket de um cliente recem-aceito
#-------------------------------------------------------
def configurar_keepalive(conn):
  conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) # liga o keepalive basico
  try:
    if hasattr(socket, "TCP_KEEPIDLE"): # disponivel no Linux
      conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, KEEPALIVE_OCIOSO_SEGUNDOS)
      conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, KEEPALIVE_INTERVALO_SEGUNDOS)
      conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, KEEPALIVE_TENTATIVAS)
    elif hasattr(socket, "SIO_KEEPALIVE_VALS"): # disponivel no Windows
      conn.ioctl(socket.SIO_KEEPALIVE_VALS, (
        1,
        KEEPALIVE_OCIOSO_SEGUNDOS * 1000,
        KEEPALIVE_INTERVALO_SEGUNDOS * 1000,
      ))
  except OSError: # plataforma nao suporta esse ajuste fino; segue so com o keepalive basico e os tempos padrao do SO
    pass


#-------------------------------------------------------
# roda o handler de um cliente e libera a vaga do
# semaforo quando ele desconectar
#-------------------------------------------------------
def _rodar_cliente_e_liberar_vaga(handler, semaforo):
  try:
    handler.rodar()
  finally:
    semaforo.release() # garante que a vaga sempre seja liberada, mesmo se der excecao


#-------------------------------------------------------
# ponto de entrada: sobe o socket de escuta e aceita
# conexoes em loop, uma thread por cliente
#-------------------------------------------------------
def main():
  parser = argparse.ArgumentParser(description="Servidor de chat (Redes de Computadores 2)") # le os argumentos da linha de comando
  # "0.0.0.0" faz o servidor escutar em TODAS as interfaces de rede da
  # maquina, necessario pra aceitar clientes de outras maquinas no laboratorio
  parser.add_argument("--host", default="0.0.0.0", help="IP para o servidor escutar (padrao: 0.0.0.0, todas as interfaces)")
  parser.add_argument("--port", type=int, default=5000, help="Porta TCP para escutar (padrao: 5000)")
  args = parser.parse_args()

  servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # cria o socket TCP de escuta
  servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # evita erro de "endereco em uso" ao reiniciar rapido
  try:
    servidor.bind((args.host, args.port)) # associa o socket ao endereco/porta escolhidos
  except OSError as e:
    # sem isso, um traceback cru do socket some no meio do terminal e nao
    # deixa claro qual e o problema real (porta ja em uso e o caso mais comum)
    log(f"[servidor] ERRO: nao foi possivel escutar em {args.host}:{args.port} ({e}).")
    log(f"[servidor] a porta {args.port} provavelmente ja esta em uso por outro programa "
        "(ou por outra instancia deste servidor ja rodando).")
    log("[servidor] tente novamente com outra porta, ex.: "
        f"python backend/server.py --host {args.host} --port {args.port + 1}")
    servidor.close()
    return
  servidor.listen() # comeca a aceitar conexoes

  # controla quantos clientes estao sendo atendidos ao mesmo tempo; ao
  # atingir MAX_CLIENTES_SIMULTANEOS, o proximo accept() ainda acontece
  # (pra podermos avisar o cliente), mas a conexao e recusada com uma
  # mensagem de erro em vez de ficar pendurada esperando vaga
  semaforo_clientes = threading.Semaphore(MAX_CLIENTES_SIMULTANEOS)

  imprimir_banner(args.host, args.port)

  try:
    while True:
      try:
        conn, addr = servidor.accept() # bloqueia ate uma nova conexao chegar
      except OSError as e:
        # erro pontual do SO ao aceitar; sem esse except, a excecao cairia
        # no finally de baixo e derrubaria o servidor pra todo mundo
        log(f"[servidor] erro ao aceitar conexao (ignorado): {e}")
        continue

      if not semaforo_clientes.acquire(blocking=False): # sem vaga: recusa AVISANDO o cliente, em vez de sumir
        log(f"[servidor] limite de {MAX_CLIENTES_SIMULTANEOS} conexoes atingido, recusando {addr}")
        try:
          conn.settimeout(TIMEOUT_SOCK)
          erro(
            conn,
            f"Servidor cheio (limite de {MAX_CLIENTES_SIMULTANEOS} conexoes simultaneas). "
            "Tente novamente em alguns instantes ou combine com o grupo outra porta/servidor.",
            contexto="SERVIDOR_CHEIO",
          )
        finally:
          conn.close() # libera o cliente que ficaria esperando resposta ate dar timeout
        continue

      conn.settimeout(TIMEOUT_SOCK) # limita quanto tempo um recv()/sendall() pode ficar preso
      configurar_keepalive(conn) # detecta cliente que caiu sem avisar em segundos, nao em horas
      # desliga o algoritmo de Nagle: por padrao o TCP segura mensagens
      # pequenas por ate ~40-200ms esperando juntar mais dados antes de
      # mandar (bom pra transferencia de arquivo grande, ruim pra chat, onde
      # cada mensagem e pequena e queremos ela na tela o mais rapido
      # possivel). Isso reduz a latencia PERCEBIDA ao trocar de conversa e
      # ao mandar/receber mensagem, principalmente em rede real (nao
      # localhost), que e exatamente o caso do teste em laboratorio.
      conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
      # addr = (ip_do_cliente, porta_de_origem_do_cliente). Essa porta NAO e a
      # porta do servidor (--port): e uma porta aleatoria e alta que o SO do
      # CLIENTE escolhe sozinho pra essa conexao (ex.: 65235) -- normal, serve
      # so pra identificar essa conexao especifica entre varias do mesmo IP.
      log(f"[servidor] nova conexao de {addr[0]}:{addr[1]}")
      handler = ClienteHandler(conn, addr)
      t = threading.Thread( # uma thread por cliente conectado
        target=_rodar_cliente_e_liberar_vaga,
        args=(handler, semaforo_clientes),
        daemon=True,
      )
      t.start()
  except KeyboardInterrupt:
    log("[servidor] encerrando...")
  finally:
    servidor.close()


if __name__ == "__main__":
  main()
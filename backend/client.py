# Discentes: Carolina de Moraes Carneiro (202410077)
#            Cibelly Henrique Nogueira Batista (202410076)            

# client.py: cliente de chat cliente-servidor

import base64
import json
import os
import queue
import socket
import threading
import time
import webview

# --------------------------------------------------------------------------
# 1. CONFIGURACAO INICIAL
# --------------------------------------------------------------------------

DIR_BASE = os.path.dirname(os.path.abspath(__file__))  # pasta onde este arquivo esta

# client.py fica em backend/client/, mas o index.html fica em frontend/ 
CAMINHO_INDEX = os.path.abspath(
    os.path.join(DIR_BASE, os.pardir, "frontend", "index.html")
)

TIMEOUT_SOCK = 30  # segundos; limite para qualquer operacao de socket depois de conectado
INTERVALO_RECONEXAO = 3  # segundos entre tentativas automaticas de reconexao apos queda


# --------------------------------------------------------------------------
# 2. CLASSE CHATAPI -- PONTE PYTHON <-> JAVASCRIPT (EXPOSTA VIA js_api=api)
# --------------------------------------------------------------------------

class ChatAPI:
  # ----------------------------------------------------------------
  # 2.1 CICLO DE VIDA DA INSTANCIA
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # inicializa o estado interno (socket, filas, geracao de
  # conexao e downloads em andamento)
  #-------------------------------------------------------
  def __init__(self):
    self._window = None  # referencia da janela pywebview, preenchida em set_window
    self._sock = None  # socket TCP da conexao atual
    self.usuario = None  # nome do usuario logado; usado como remetente nas mensagens
    self.conectado = False  # flag checada pelas threads de leitura/escrita
    self._alvo_atual = None  # (servidor, porta) da conexao atual; detecta troca de servidor
    # incrementado a cada nova conexao; threads guardam a geracao em que
    # nasceram e param sozinhas se ela mudar -- evita thread do socket
    # antigo concorrer com a do novo
    self._geracao = 0
    # fila de saida; quem chama pelo JS so enfileira e retorna --
    # o sock.sendall() (bloqueante) fica numa thread dedicada (_loop_envio)
    self._fila_envio = queue.Queue()
    # arquivo_salvo -> Queue(1); _escutar deposita ARQUIVO_BYTES ou ERRO
    # aqui em vez de repassar pro JS; consumido por baixar_arquivo()
    self._respostas_arquivo = {}
    # credenciais da sessao autenticada com sucesso (LOGIN_OK), usadas
    # para reconectar sozinho se a conexao cair sem o usuario pedir
    # (ex.: servidor derrubado por forca bruta e depois reiniciado na
    # mesma porta). Fica None enquanto nao ha login confirmado, e volta
    # a None num logout explicito (sair()) ou numa falha de autenticacao
    # -- nesses casos nao faz sentido tentar de novo sozinho
    self._credenciais = None
    self._senha_pendente = None  # senha usada no ultimo login(), guardada so pra poder reenviar numa reconexao
    self._reconectando = False  # trava simples pra nunca ter duas threads de reconexao ao mesmo tempo

  #-------------------------------------------------------
  # guarda a referencia da janela pywebview, usada depois
  # para injetar JavaScript e abrir dialogos nativos
  #-------------------------------------------------------
  def set_window(self, window):
    self._window = window

  # ----------------------------------------------------------------
  # 2.2 CONEXAO TCP (ABRIR/FECHAR SOCKET, THREADS DE LEITURA E ESCRITA)
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # abre (ou reaproveita) a conexao TCP com `servidor`/`porta`
  #-------------------------------------------------------
  def _conectar(self, servidor, porta):
    alvo = (servidor, str(porta)) # normaliza a porta pra string, pra comparar sempre igual
    if self.conectado and self._alvo_atual == alvo:
      return {"ok": True} # ja conectado exatamente nesse servidor/porta
    if self.conectado:
      # conectado, mas a um servidor/porta diferente do pedido agora 
      self._encerrar_conexao_atual()
    try:
      s = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # cria o socket TCP
      s.settimeout(6) # timeout curto so pra fase de connect(), pra nao travar a tela de login
      s.connect((servidor, int(porta))) # tenta conectar de fato no servidor informado
      # desliga o algoritmo de Nagle (precisa ser identico dos dois lados
      # pra fazer efeito de verdade -- ver comentario completo no server.py,
      # em configurar_keepalive/TCP_NODELAY): reduz a latencia de cada
      # mensagem pequena (chat, historico) numa rede real
      s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
      # timeout de operacao (nao None/infinito): se o servidor sumir ou o
      # buffer de envio encher e nunca esvaziar, recv()/sendall() no
      # maximo demoram TIMEOUT_SOCK segundos em vez de travar pra sempre.
      # um timeout em recv() eh tratado como "sem novidade ainda" (ver
      # _escutar), nao como erro
      s.settimeout(TIMEOUT_SOCK)
      self._sock = s
      self._alvo_atual = alvo
      self._geracao += 1 # nova geracao de conexao comeca aqui
      minha_geracao = self._geracao
      self.conectado = True
      self._fila_envio = queue.Queue() # zera fila: nada de mensagem antiga indo pro servidor novo
      self._respostas_arquivo = {} # idem: nenhum download pendente da conexao antiga
      threading.Thread(target=self._escutar, args=(minha_geracao,), daemon=True).start() # thread de leitura dessa geracao
      threading.Thread(target=self._loop_envio, args=(minha_geracao,), daemon=True).start() # thread de escrita dessa geracao
      return {"ok": True}
    except Exception as e: # qualquer falha de rede (endereco invalido, recusado, timeout etc)
      return {"ok": False, "erro": str(e)}

  #-------------------------------------------------------
  # derruba a conexao atual antes de abrir uma nova
  #-------------------------------------------------------
  def _encerrar_conexao_atual(self):
    self.conectado = False # sinaliza pras threads _escutar/_loop_envio pararem
    try:
      if self._sock:
        self._sock.close() # fecha o socket, o que tambem destrava um recv() bloqueado
    except OSError: # socket ja podia estar fechado/invalido
      pass

  #-------------------------------------------------------
  # unica thread que le o socket dessa geracao de conexao
  #-------------------------------------------------------
  def _escutar(self, minha_geracao):
    meu_sock = self._sock # fixa o socket desta geracao, mesmo se self._sock trocar depois
    buffer = b"" # acumula bytes recebidos ate achar uma linha completa ("\n")
    try:
      while self.conectado and self._geracao == minha_geracao: # para sozinho se a conexao cair ou virar geracao antiga
        try:
          dados = meu_sock.recv(65536) # tenta ler mais bytes do socket
        except socket.timeout:
          continue # nenhuma mensagem chegou nesse intervalo, normal
        if not dados: # servidor fechou a conexao (FIN do TCP)
          break
        buffer += dados # acumula os bytes recebidos como texto

        # TCP eh um fluxo continuo de bytes, sem fronteiras de mensagem;
        # processamos linha a linha conforme "\n" aparece no buffer
        while b"\n" in buffer:
          linha, buffer = buffer.split(b"\n", 1) # separa a primeira linha do resto
          linha = linha.decode("utf-8", errors="replace").strip()
          if not linha: # linha em branco, ignora
            continue
          try:
            msg = json.loads(linha) # converte o texto em objeto Python
          except json.JSONDecodeError:
            continue # linha corrompida, descarta em vez de derrubar a thread
          if self._interceptar_resposta_arquivo(msg):
            continue # resposta de PEDIR_ARQUIVO: nao repassa pro JS
          self._observar_autenticacao(msg) # guarda/limpa credenciais conforme LOGIN_OK / LOGIN_ERRO
          self._entregar_ao_js(msg) # qualquer outra mensagem vai direto pro front-end
    except OSError: # conexao caiu de forma abrupta
      pass
    finally:
      self._tratar_desconexao(minha_geracao) # decide entre avisar o front-end ou tentar reconectar sozinho

  #-------------------------------------------------------
  # unico lugar que chama sock.sendall() para essa geracao
  # de conexao; consome objetos da fila de envio e os
  # serializa/envia um a um
  #-------------------------------------------------------
  def _loop_envio(self, minha_geracao):
    meu_sock = self._sock # fixa o socket desta geracao, mesmo se self._sock trocar depois
    while self.conectado and self._geracao == minha_geracao: # para sozinho se a conexao cair ou virar geracao antiga
      try:
        obj = self._fila_envio.get(timeout=1) # espera ate ter algo pra enviar, sem travar pra sempre
      except queue.Empty:
        continue # nada na fila nesse intervalo, so volta a checar as condicoes do while
      linha = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8") # serializa e adiciona o separador de mensagem
      try:
        meu_sock.sendall(linha) # se o envio travar (peer lento), so essa thread fica presa aqui
      except OSError: # falha ao enviar, o servidor provavelmente caiu
        self._tratar_desconexao(minha_geracao) # decide entre avisar o front-end ou tentar reconectar sozinho
        break

  # ----------------------------------------------------------------
  # 2.3 PONTE COM O JAVASCRIPT
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # repassa uma mensagem recebida do servidor para o
  # front-end, chamando `window.receberMensagem(...)`
  #-------------------------------------------------------
  def _entregar_ao_js(self, msg_dict):
    # json.dumps novamente para escapar corretamente aspas/caracteres
    # especiais antes de injetar como literal dentro do codigo JS
    payload = json.dumps(msg_dict, ensure_ascii=False)
    self._chamar_js(f"window.receberMensagem({payload})")

  #-------------------------------------------------------
  # executa um trecho de JavaScript na janela pywebview, se
  # ela ja existir; ignora silenciosamente qualquer erro de
  # execucao (ex.: janela fechada no meio do caminho)
  #-------------------------------------------------------
  def _chamar_js(self, codigo):
    if self._window is not None: # so tenta se a janela ja foi criada e associada via set_window
      try:
        self._window.evaluate_js(codigo) # injeta e executa o codigo JS dentro da pagina
      except Exception: # janela pode ter sido fechada no meio do caminho, entre outros erros pontuais
        pass

  #-------------------------------------------------------
  # enfileira `obj` para ser enviado ao servidor sem
  # bloquear quem chama -- a thread `_loop_envio` e quem de
  # fato manda pelo socket
  #-------------------------------------------------------
  def _enviar_json(self, obj):
    if not self._sock or not self.conectado: # sem conexao ativa, nao ha pra onde mandar
      return
    self._fila_envio.put(obj) # nao bloqueia: so entra na fila, a _loop_envio e quem envia

  #-------------------------------------------------------
  # verifica se `msg` e a resposta de um PEDIR_ARQUIVO que
  # `baixar_arquivo`/`obter_preview_arquivo` esta esperando;
  # se for, entrega na fila correlacionada e devolve True
  # (sinal pra `_escutar` NAO repassar essa mensagem ao JS)
  #-------------------------------------------------------
  def _interceptar_resposta_arquivo(self, msg):
    tipo = msg.get("tipo")
    if tipo == "ARQUIVO_BYTES":
      chave = msg.get("arquivo_salvo") # identifica qual pedido de download essa resposta atende
    elif tipo == "ERRO" and msg.get("contexto") in self._respostas_arquivo:
      chave = msg.get("contexto") # erro tambem carrega o "contexto" com a mesma chave do pedido
    else:
      return False # nao e resposta de PEDIR_ARQUIVO, segue o fluxo normal
    fila = self._respostas_arquivo.get(chave)
    if fila is None: # ninguem esta esperando essa chave (pedido ja expirou, por exemplo)
      return False
    try:
      fila.put_nowait(msg) # entrega a resposta pra quem esta bloqueado em fila_resposta.get()
    except queue.Full: # a fila tem capacidade 1 e ja tinha algo, nao deveria acontecer na pratica
      pass
    return True

  #-------------------------------------------------------
  # olha toda mensagem recebida em busca de LOGIN_OK/LOGIN_ERRO
  # pra manter self._credenciais em dia -- sao elas que dizem
  # se vale a pena tentar reconectar sozinho quando a conexao
  # cair mais tarde
  #-------------------------------------------------------
  def _observar_autenticacao(self, msg):
    tipo = msg.get("tipo")
    if tipo == "LOGIN_OK":
      # login confirmado pelo servidor: guarda os dados usados agora,
      # pra poder repetir o mesmo LOGIN sozinho se a conexao cair depois
      alvo = self._alvo_atual
      if alvo is not None:
        self._credenciais = {
          "usuario": self.usuario,
          "senha": self._senha_pendente,
          "servidor": alvo[0],
          "porta": alvo[1],
        }
    elif tipo == "LOGIN_ERRO":
      # usuario/senha invalidos (ou outra recusa do servidor): nao ha
      # por que ficar tentando de novo sozinho com os mesmos dados
      self._credenciais = None

  #-------------------------------------------------------
  # chamada sempre que a thread de leitura ou a de escrita
  # percebem que a conexao caiu; decide entre avisar o
  # front-end (desconexao "definitiva") ou tentar reconectar
  # sozinho em segundo plano, dependendo se havia um login
  # confirmado (self._credenciais) nessa conexao
  #-------------------------------------------------------
  def _tratar_desconexao(self, minha_geracao):
    if self._geracao != minha_geracao:
      # geracao antiga (ja trocamos de conexao depois que essa caiu):
      # nao mexe em nada, senao apagaria por engano o estado da conexao
      # nova que ja esta ativa
      return
    self.conectado = False
    credenciais = self._credenciais
    if credenciais is None:
      # nunca chegou a logar de verdade nessa conexao (ou o usuario ja
      # tinha saido explicitamente antes) -- avisa o front-end como
      # antes, volta pra tela de login
      self._chamar_js("window.aoDesconectar && window.aoDesconectar()")
      return
    if self._reconectando:
      return # ja existe uma thread tentando reconectar, nao duplica
    self._reconectando = True
    self._chamar_js("window.aoReconectando && window.aoReconectando()") # avisa sem tirar o usuario da tela atual
    threading.Thread(target=self._loop_reconexao, args=(credenciais,), daemon=True).start()

  #-------------------------------------------------------
  # tenta reconectar no mesmo servidor/porta em intervalos
  # regulares ate conseguir (ou ate o usuario desistir com um
  # logout explicito no meio do caminho); ao conseguir abrir o
  # socket de novo, reenvia o LOGIN com as mesmas credenciais
  # -- a partir dai o fluxo normal de LOGIN_OK/LOGIN_ERRO
  # (via _observar_autenticacao) assume de novo
  #-------------------------------------------------------
  def _loop_reconexao(self, credenciais):
    try:
      while True:
        if self._credenciais is None:
          return # usuario deslogou (ou LOGIN_ERRO) enquanto tentavamos: desiste
        if self.conectado:
          return # alguma outra tentativa (ou o proprio usuario) ja reconectou antes
        time.sleep(INTERVALO_RECONEXAO)
        if self._credenciais is None or self.conectado:
          continue # reavalia a condicao acima antes de gastar uma tentativa de conexao
        r = self._conectar(credenciais["servidor"], credenciais["porta"])
        if r["ok"]:
          self.usuario = credenciais["usuario"]
          self._senha_pendente = credenciais["senha"]
          self._enviar_json({"tipo": "LOGIN", "usuario": credenciais["usuario"], "senha": credenciais["senha"]})
          return # conexao TCP e LOGIN enviados; o resto do fluxo cuida do resultado
    finally:
      self._reconectando = False

  # ----------------------------------------------------------------
  # 2.4 AUTENTICACAO (CADASTRO E LOGIN)
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # conecta ao servidor informado e envia um pedido de
  # cadastro (REGISTRAR)
  #-------------------------------------------------------
  def registrar(self, usuario, senha, servidor, porta):
    r = self._conectar(servidor, porta) # garante que existe socket aberto com esse servidor/porta
    if not r["ok"]:
      return r # propaga o erro de conexao pro JS, sem tentar enviar nada
    self._enviar_json({"tipo": "REGISTRAR", "usuario": usuario, "senha": senha})
    return {"ok": True}

  #-------------------------------------------------------
  # conecta ao servidor informado e envia um pedido de
  # login (LOGIN), guardando o usuario localmente pra usar
  # como remetente nas mensagens
  #-------------------------------------------------------
  def login(self, usuario, senha, servidor, porta):
    r = self._conectar(servidor, porta) # garante que existe socket aberto com esse servidor/porta
    if not r["ok"]:
      return r
    self.usuario = usuario # guarda localmente pra usar como "de" nas mensagens enviadas depois
    self._senha_pendente = senha # guardada so pra poder reenviar sozinho num LOGIN de reconexao futura
    self._enviar_json({"tipo": "LOGIN", "usuario": usuario, "senha": senha})
    return {"ok": True}

  # ----------------------------------------------------------------
  # 2.5 MENSAGENS (GERAL, PRIVADA, SALA)
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # envia uma mensagem para o chat geral (MSG_GERAL)
  #-------------------------------------------------------
  def enviar_mensagem_geral(self, conteudo):
    self._enviar_json({"tipo": "MSG_GERAL", "de": self.usuario, "conteudo": conteudo})

  #-------------------------------------------------------
  # envia uma mensagem privada (MSG_PRIVADA) para outro
  # usuario
  #-------------------------------------------------------
  def enviar_mensagem_privada(self, para, conteudo):
    self._enviar_json({"tipo": "MSG_PRIVADA", "de": self.usuario, "para": para, "conteudo": conteudo})

  #-------------------------------------------------------
  # envia uma mensagem para uma sala especifica (MSG_SALA)
  #-------------------------------------------------------
  def enviar_mensagem_sala(self, sala, conteudo):
    self._enviar_json({"tipo": "MSG_SALA", "de": self.usuario, "sala": sala, "conteudo": conteudo})

  # ----------------------------------------------------------------
  # 2.6 SALAS (CRIAR, SOLICITAR ENTRADA, APROVAR/RECUSAR)
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # pede ao servidor a criacao de uma nova sala
  # (CRIAR_SALA), com o usuario atual como dono
  #-------------------------------------------------------
  def criar_sala(self, sala):
    self._enviar_json({"tipo": "CRIAR_SALA", "sala": sala, "dono": self.usuario})

  #-------------------------------------------------------
  # pede entrada numa sala existente (SOLICITAR_ENTRADA)
  #-------------------------------------------------------
  def solicitar_entrada(self, sala):
    self._enviar_json({"tipo": "SOLICITAR_ENTRADA", "sala": sala, "usuario": self.usuario})

  #-------------------------------------------------------
  # como dono de uma sala, aprova o pedido de entrada de
  # `usuario` (APROVAR_ENTRADA)
  #-------------------------------------------------------
  def aprovar_entrada(self, sala, usuario):
    self._enviar_json({"tipo": "APROVAR_ENTRADA", "sala": sala, "usuario": usuario})

  #-------------------------------------------------------
  # como dono de uma sala, recusa o pedido de entrada de
  # `usuario` (RECUSAR_ENTRADA)
  #-------------------------------------------------------
  def recusar_entrada(self, sala, usuario):
    self._enviar_json({"tipo": "RECUSAR_ENTRADA", "sala": sala, "usuario": usuario})

  # ----------------------------------------------------------------
  # 2.7 ARQUIVOS (ENVIAR, BAIXAR, PREVIEW)
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # envia um arquivo (ja em base64) para uma sala ou para
  # uma conversa privada, conforme `destino_tipo`
  #-------------------------------------------------------
  def enviar_arquivo(self, destino_tipo, destino, nome, tamanho, conteudo_base64):
    """destino_tipo: 'sala' ou 'privada'."""
    msg = {
      "tipo": "ARQUIVO", "de": self.usuario, "nome": nome,
      "tamanho": tamanho, "conteudo_base64": conteudo_base64,
    }
    if destino_tipo == "sala":
      msg["sala"] = destino # rota pra sala: servidor identifica pelo campo "sala"
    else:
      msg["para"] = destino # rota privada: servidor identifica pelo campo "para"
    self._enviar_json(msg)
    return {"ok": True}

  #-------------------------------------------------------
  # pede ao servidor (PEDIR_ARQUIVO) o base64 de um
  # arquivo salvo e espera a resposta
  #-------------------------------------------------------
  def _pedir_conteudo_arquivo(self, arquivo_salvo):
    if not self._sock or not self.conectado: # sem conexao ativa, nem adianta pedir
      return {"ok": False, "erro": "Sem conexao com o servidor."}
    if not arquivo_salvo: # nome de arquivo vazio/None, pedido invalido
      return {"ok": False, "erro": "Arquivo invalido."}

    fila_resposta = queue.Queue(maxsize=1) # so cabe uma resposta, que e exatamente o que esperamos
    self._respostas_arquivo[arquivo_salvo] = fila_resposta # registra a fila ANTES de enviar, pra nao perder a resposta se ela for rapida demais
    self._enviar_json({"tipo": "PEDIR_ARQUIVO", "arquivo_salvo": arquivo_salvo})
    try:
      resp = fila_resposta.get(timeout=15) # bloqueia essa chamada ate _escutar entregar a resposta (ou estourar o timeout)
    except queue.Empty:
      return {"ok": False, "erro": "Tempo esgotado esperando o arquivo do servidor."}
    finally:
      self._respostas_arquivo.pop(arquivo_salvo, None) # limpa o registro, esperada ou nao a resposta

    if resp.get("tipo") == "ERRO": # servidor recusou o pedido (arquivo nao existe, sem permissao etc)
      return {"ok": False, "erro": resp.get("mensagem") or "Erro ao baixar arquivo."}

    conteudo_b64 = resp.get("conteudo_base64")
    if not conteudo_b64: # resposta veio sem conteudo util
      return {"ok": False, "erro": "Arquivo vazio ou nao encontrado."}
    return {"ok": True, "conteudo_base64": conteudo_b64}

  #-------------------------------------------------------
  # chamado pelo JS para mostrar a miniatura de uma imagem
  # direto no chat; so devolve o base64 pronto pra virar
  # `<img src="data:...;base64,...">`, nao grava em disco
  #-------------------------------------------------------
  def obter_preview_arquivo(self, arquivo_salvo):
    return self._pedir_conteudo_arquivo(arquivo_salvo)

  #-------------------------------------------------------
  # chamado pelo JS quando o usuario clica em "baixar";
  # pede o conteudo ao servidor e abre o dialogo nativo
  # "Salvar como" do sistema operacional
  #-------------------------------------------------------
  def baixar_arquivo(self, arquivo_salvo, nome_sugerido):
    r = self._pedir_conteudo_arquivo(arquivo_salvo) # busca o conteudo no servidor primeiro
    if not r["ok"]:
      return r # propaga o erro (sem conexao, timeout, arquivo nao encontrado etc)
    try:
      dados = base64.b64decode(r["conteudo_base64"]) # decodifica de volta para bytes crus
    except Exception:
      return {"ok": False, "erro": "Arquivo corrompido (base64 invalido)."}

    if self._window is None: # sem janela nao ha como abrir o dialogo nativo de salvar
      return {"ok": False, "erro": "Janela indisponivel."}
    try:
      caminho_escolhido = self._window.create_file_dialog(
        webview.SAVE_DIALOG, save_filename=nome_sugerido or arquivo_salvo
      ) # abre o dialogo nativo "Salvar como" e espera o usuario escolher o caminho
    except Exception as e:
      return {"ok": False, "erro": f"Nao foi possivel abrir o dialogo de salvar: {e}"}

    if not caminho_escolhido: # usuario cancelou o dialogo
      return {"ok": False, "erro": "Download cancelado."}
    if isinstance(caminho_escolhido, (list, tuple)): # algumas plataformas devolvem tupla/lista mesmo pra selecao unica
      caminho_escolhido = caminho_escolhido[0]

    try:
      with open(caminho_escolhido, "wb") as f:
        f.write(dados) # grava os bytes decodificados no caminho escolhido
    except OSError as e:
      return {"ok": False, "erro": f"Nao foi possivel salvar o arquivo: {e}"}

    return {"ok": True, "caminho": caminho_escolhido}

  # ----------------------------------------------------------------
  # 2.8 STATUS, LISTAGEM, HISTORICO E ENCERRAMENTO
  # ----------------------------------------------------------------

  #-------------------------------------------------------
  # pede ao servidor a lista atual de usuarios conectados/
  # cadastrados (LISTAR)
  #-------------------------------------------------------
  def listar(self):
    self._enviar_json({"tipo": "LISTAR"})

  #-------------------------------------------------------
  # informa ao servidor a mudanca de status cosmetico do
  # usuario (online/offline) via mensagem STATUS
  #-------------------------------------------------------
  def alterar_status(self, novo_status):
    self._enviar_json({"tipo": "STATUS", "status": novo_status})

  #-------------------------------------------------------
  # pede ao servidor a estrutura atualizada de salas
  # (LISTAR_SALAS)
  #-------------------------------------------------------
  def listar_salas(self):
    self._enviar_json({"tipo": "LISTAR_SALAS"})

  #-------------------------------------------------------
  # pede ao servidor o historico de uma sala ou conversa
  # privada especifica (HISTORICO_REQUEST)
  #-------------------------------------------------------
  def pedir_historico(self, canal_tipo, canal):
    self._enviar_json({"tipo": "HISTORICO_REQUEST", "canal_tipo": canal_tipo, "canal": canal})

  #-------------------------------------------------------
  # encerra a sessao do usuario -- avisa o servidor (SAIR)
  # e limpa o estado local de conexao
  #-------------------------------------------------------
  def sair(self):
    # envio direto (bypass da fila): e uma acao explicita do usuario e
    # vamos fechar o socket logo em seguida, entao nao ha por que esperar
    # a thread _loop_envio pegar da fila -- so entraria numa corrida com
    # o self.conectado = False abaixo e a mensagem podia nunca sair.
    # sendall aqui ainda esta limitado por TIMEOUT_SOCK
    try:
      if self._sock and self.conectado:
        linha = (json.dumps({"tipo": "SAIR"}, ensure_ascii=False) + "\n").encode("utf-8")
        self._sock.sendall(linha) # manda o aviso de saida direto, sem passar pela fila de envio
    except Exception: # conexao pode ja ter caido, tanto faz pra quem esta saindo mesmo assim
      pass
    self.conectado = False # a partir daqui as threads de leitura/escrita param sozinhas
    self._credenciais = None # logout explicito: nao tenta reconectar sozinho depois disso
    # muda a geracao ANTES de fechar o socket: quando _escutar acordar (por
    # causa do close() abaixo) e cair em _tratar_desconexao, o teste
    # `self._geracao != minha_geracao` ja vai ser verdadeiro e a funcao
    # retorna sem chamar window.aoDesconectar -- senao apareceria o aviso
    # de "problema na conexao" mesmo tendo sido o usuario que pediu pra sair
    self._geracao += 1
    try:
      if self._sock:
        self._sock.close() # fecha o socket local
    except OSError:
      pass
    self.usuario = None
    self._alvo_atual = None # esquece o servidor/porta atual, pra permitir novo login em outro endereco
    return {"ok": True}


# --------------------------------------------------------------------------
# 3. BOOTSTRAP DO CLIENTE
# --------------------------------------------------------------------------

#-------------------------------------------------------
# ponto de entrada: cria a instancia de ChatAPI, abre a
# janela pywebview carregando o frontend e inicia o loop
# da interface grafica
#-------------------------------------------------------
def main():
  api = ChatAPI() # instancia unica da ponte Python <-> JavaScript
  janela = webview.create_window(
    "MSN Messenger — Cliente/Servidor",
    CAMINHO_INDEX, # carrega o frontend sem alteracao visual
    js_api=api, # expoe os metodos publicos de `api` como window.pywebview.api.*
    width=770,
    height=540,
    resizable=True,
  )
  api.set_window(janela) # guarda a referencia da janela pra uso posterior (evaluate_js, dialogos)

  gui_kwargs = {}
  if os.name == "nt": # so no Windows
    # forca o WebView2 (Edge Chromium) explicitamente. Sem isso, se o
    # WebView2 Runtime nao estiver instalado na maquina, o pywebview cai
    # silenciosamente no motor antigo, que e instavel com o JS moderno do
    # index.html e trava mesmo sem nenhuma atividade de rede
    gui_kwargs["gui"] = "edgechromium"

  try:
    webview.start(**gui_kwargs) # inicia o loop da interface grafica (chamada bloqueante)
  except Exception:
    if os.name == "nt": # mensagem de ajuda especifica pro caso mais comum de falha (falta o runtime do WebView2)
      print(
        "[cliente] Nao foi possivel iniciar o motor Edge WebView2.\n"
        "Instale o WebView2 Runtime nesta maquina e tente novamente:\n"
        "https://developer.microsoft.com/microsoft-edge/webview2/"
      )
    raise


if __name__ == "__main__":
  main()
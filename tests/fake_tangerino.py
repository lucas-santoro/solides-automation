"""Tangerino falso (Starlette) para testar login + Apropriação de horas de ponta a ponta, sem rede."""

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

FIXTURES = Path(__file__).parent / "fixtures"
LOGIN_HTML = (FIXTURES / "login_colaborador.html").read_text(encoding="utf-8")
APROPRIACAO_HTML = (FIXTURES / "apropriacao_horas.html").read_text(encoding="utf-8")
HOME_HTML = "<html><head><title>Início</title></head><body><h1>Bem-vindo</h1></body></html>"
DIAS_SEMANA = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]


@dataclass
class DiaFake:
    horas: list[str]
    marcador: str | None = None
    previsto: str | None = "08:00"
    pendente: bool = False


@dataclass
class Lancamento:
    data: str
    hora: str
    justificativa: str
    observacao: str


@dataclass
class EstadoFake:
    dias: dict[date, DiaFake] = field(default_factory=dict)
    lancamentos: list[Lancamento] = field(default_factory=list)
    recusar_horas: dict[str, str] = field(default_factory=dict)
    """hora -> mensagem de erro do toast."""
    consultas_atrasadas: int = 0
    """Quantas consultas depois de um lançamento ainda mostram o estado antigo."""
    _congelado: dict[date, list[str]] | None = None


def _dia_json(data: date, dia: DiaFake, horas: list[str]) -> dict:
    return {
        "data": data.strftime("%d/%m/%Y"),
        "dia_semana": DIAS_SEMANA[data.weekday()],
        "marcador": dia.marcador,
        "horas": horas,
        "pendente": dia.pendente,
        "previsto": dia.previsto,
        "saldo": "00:00" if len(horas) >= 4 or not dia.previsto else "-08:00",
    }


class FakeTangerino:
    def __init__(self) -> None:
        self.estado = EstadoFake()
        self.base_url = ""
        self._server: uvicorn.Server | None = None

    def reset(self, dias: dict[date, DiaFake]) -> EstadoFake:
        self.estado = EstadoFake(dias=dias)
        return self.estado

    def _periodo(self, inicio: date, fim: date) -> list[dict]:
        congelado = self.estado._congelado
        return [
            _dia_json(d, dia, list(congelado[d]) if congelado and d in congelado else dia.horas)
            for d, dia in sorted(self.estado.dias.items())
            if inicio <= d <= fim
        ]

    # --- rotas ---

    async def raiz(self, request: Request) -> Response:
        if request.method == "POST":  # AJAX do btnLogin
            return JSONResponse({"redirect": "/Tangerino/pages/HomeFuncionario"})
        return HTMLResponse(LOGIN_HTML)

    async def home(self, request: Request) -> Response:
        return HTMLResponse(HOME_HTML)

    async def apropriacao(self, request: Request) -> Response:
        datas = sorted(self.estado.dias)
        html = (
            APROPRIACAO_HTML.replace("__INICIO__", datas[0].strftime("%d/%m/%Y"))
            .replace("__FIM__", datas[-1].strftime("%d/%m/%Y"))
            .replace("__DIAS__", json.dumps(self._periodo(datas[0], datas[-1])))
        )
        return HTMLResponse(html)

    async def consultar(self, request: Request) -> Response:
        form = await request.form()
        inicio = datetime.strptime(str(form["inicio"]), "%d/%m/%Y").date()
        fim = datetime.strptime(str(form["fim"]), "%d/%m/%Y").date()
        resposta = self._periodo(inicio, fim)
        if self.estado._congelado is not None:
            self.estado.consultas_atrasadas -= 1
            if self.estado.consultas_atrasadas <= 0:
                self.estado._congelado = None
        return JSONResponse(resposta)

    async def lancar(self, request: Request) -> Response:
        form = await request.form()
        hora = str(form["hora"])
        if hora in self.estado.recusar_horas:
            return JSONResponse({"erro": self.estado.recusar_horas[hora]})
        if str(form["justificativa"]) == "Escolha":
            return JSONResponse({"erro": "Informe a justificativa"})
        data = datetime.strptime(str(form["data"]), "%d/%m/%Y").date()
        if self.estado.consultas_atrasadas and self.estado._congelado is None:
            self.estado._congelado = {d: list(dia.horas) for d, dia in self.estado.dias.items()}
        dia = self.estado.dias[data]
        dia.horas = sorted([*dia.horas, hora])
        dia.pendente = True
        self.estado.lancamentos.append(
            Lancamento(str(form["data"]), hora, str(form["justificativa"]), str(form.get("observacao", "")))
        )
        return JSONResponse({"ok": True})

    # --- servidor ---

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/Tangerino/", self.raiz, methods=["GET", "POST"]),
                Route("/Tangerino/pages/HomeFuncionario", self.home),
                Route("/Tangerino/pages/apropriacao-horas", self.apropriacao),
                Route("/Tangerino/pages/apropriacao-horas/consultar", self.consultar, methods=["POST"]),
                Route("/Tangerino/pages/apropriacao-horas/lancar", self.lancar, methods=["POST"]),
            ]
        )

    def start(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            porta = sock.getsockname()[1]
        self._server = uvicorn.Server(uvicorn.Config(self.app(), host="127.0.0.1", port=porta, log_level="warning"))
        threading.Thread(target=self._server.run, daemon=True).start()
        while not self._server.started:
            time.sleep(0.05)
        self.base_url = f"http://127.0.0.1:{porta}/Tangerino/"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True

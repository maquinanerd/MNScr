"""As rotas do painel. Tudo atrás de login; nenhuma rota muda o robô.

Os únicos POST são os do próprio acesso (primeiro acesso, login, sair), todos com CSRF.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import dados
from .acesso import (
    AuthBusy,
    LoginThrottle,
    PainelStore,
    check_login,
    create_admin,
    csrf_ok,
    csrf_token,
    session_secret,
)

TEMPLATES = Path(__file__).parent / "templates"

#: Sem JavaScript nenhum; CSS só no <style> da página base.
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

STATUS_FILA = {
    "NEW": "esperando a vez",
    "QUEUED": "de volta à fila",
    "DEFERRED": "adiada",
    "PROCESSING": "sendo escrita agora",
}
STATUS_FALHA = {
    "FAILED": "falhou",
    "FAILED_PERMANENT": "falhou de vez",
    "DRAFT_FAILED": "rascunho falhou",
}


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _quando(value: Optional[datetime]) -> str:
    return value.strftime("%d/%m %H:%M") if value else "—"


def _ha(value: Optional[datetime], now: Optional[datetime] = None) -> str:
    if value is None:
        return "nunca"
    seconds = int(((now or dados.agora()) - value).total_seconds())
    if seconds < 90:
        return "agora há pouco"
    if seconds < 3600:
        return f"há {seconds // 60} min"
    if seconds < 48 * 3600:
        return f"há {seconds // 3600} h"
    return f"há {seconds // 86400} dias"


def _usd(value: Optional[float], casas: int = 2) -> str:
    if value is None:
        return "—"
    return "US$ " + f"{value:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def create_app(
    store: PainelStore,
    *,
    robot_db: str,
    teto: Optional[float],
    secure_cookies: bool = True,
    base_url: str = "",
    intervalo_min: int = 15,
) -> FastAPI:
    """`teto`: o teto diário de IA em dólares (0 desligado, None inválido), o mesmo do robô."""
    app = FastAPI(title="Painel MNScr", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret(store),
        session_cookie="mnscr_painel",
        same_site="lax",
        https_only=secure_cookies,
        max_age=12 * 3600,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.globals.update(STATUS_FILA=STATUS_FILA, STATUS_FALHA=STATUS_FALHA)
    templates.env.filters.update(quando=_quando, ha=_ha, usd=_usd)
    throttle = LoginThrottle()

    def flash(request: Request, message: str, kind: str = "ok") -> None:
        request.session.setdefault("flash", []).append({"kind": kind, "message": message})

    def render(request: Request, name: str, **context: Any) -> HTMLResponse:
        messages = request.session.pop("flash", [])
        return templates.TemplateResponse(
            request,
            name,
            {
                "csrf": csrf_token(request.session),
                "flash": messages,
                "admin": request.session.get("admin"),
                "path": request.url.path,
                **context,
            },
        )

    def require_admin(request: Request) -> None:
        if not store.admin_exists():
            raise HTTPException(status_code=303, headers={"Location": "/setup"})
        if (
            request.session.get("admin") != store.setting("admin_user")
            or request.session.get("epoch") != store.session_epoch()
        ):
            raise HTTPException(status_code=303, headers={"Location": "/login"})

    def sign_in(request: Request, user: str) -> None:
        request.session.clear()
        request.session["admin"] = user.strip()
        request.session["epoch"] = store.session_epoch()

    def require_csrf(request: Request, submitted: str) -> None:
        if not csrf_ok(request.session, submitted):
            raise HTTPException(status_code=400, detail="formulário expirado; recarregue a página")

    def client_ip(request: Request) -> str:
        # O uvicorn só aceita X-Forwarded-For do proxy confiável (FORWARDED_ALLOW_IPS).
        return request.client.host if request.client else "?"

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
            return _redirect(exc.headers["Location"])
        return HTMLResponse(
            f"<p>{html.escape(str(exc.detail))}</p><p><a href='/'>voltar</a></p>", status_code=exc.status_code
        )

    @app.get("/health")
    def health() -> JSONResponse:
        # Público: só diz que o painel está de pé. Nada do robô sai daqui sem login.
        return JSONResponse({"status": "ok"})

    # -- primeiro acesso e login -------------------------------------------------

    @app.get("/setup", response_class=HTMLResponse)
    def setup_form(request: Request) -> Response:
        if store.admin_exists():
            return _redirect("/login")
        return render(request, "setup.html")

    @app.post("/setup")
    def setup(
        request: Request,
        user: str = Form(""),
        password: str = Form(""),
        confirm: str = Form(""),
        code: str = Form(""),
        csrf: str = Form(""),
    ) -> Response:
        require_csrf(request, csrf)
        if store.admin_exists():
            return _redirect("/login")
        if not throttle.attempt(client_ip(request)):
            flash(request, "muitas tentativas; espere alguns minutos", "erro")
            return _redirect("/setup")
        if password != confirm:
            flash(request, "as senhas não conferem", "erro")
            return _redirect("/setup")
        try:
            create_admin(store, user, password, code=code)
        except AuthBusy as exc:
            throttle.refund(client_ip(request))
            flash(request, str(exc), "erro")
            return _redirect("/setup")
        except ValueError as exc:
            flash(request, str(exc), "erro")
            return _redirect("/setup")
        throttle.reset(client_ip(request))
        sign_in(request, user)
        flash(request, "Administrador criado.")
        return _redirect("/")

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> Response:
        if not store.admin_exists():
            return _redirect("/setup")
        return render(request, "login.html")

    @app.post("/login")
    def login(request: Request, user: str = Form(""), password: str = Form(""), csrf: str = Form("")) -> Response:
        require_csrf(request, csrf)
        ip = client_ip(request)
        if not throttle.attempt(ip):
            flash(request, "muitas tentativas; espere alguns minutos", "erro")
            return _redirect("/login")
        try:
            ok = check_login(store, user, password)
        except AuthBusy as exc:
            throttle.refund(ip)
            flash(request, str(exc), "erro")
            return _redirect("/login")
        if not ok:
            flash(request, "usuário ou senha incorretos", "erro")
            return _redirect("/login")
        throttle.reset(ip)
        sign_in(request, user)
        return _redirect("/")

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")) -> Response:
        require_csrf(request, csrf)
        # Todo cookie emitido antes deixa de valer, inclusive um copiado — mas só quando
        # quem sai é o administrador. Um visitante qualquer tem CSRF próprio (a sessão
        # anônima do /login), e trocar a época por ele derrubaria o administrador.
        if store.admin_exists() and (
            request.session.get("admin") == store.setting("admin_user")
            and request.session.get("epoch") == store.session_epoch()
        ):
            store.bump_session_epoch()
        request.session.clear()
        return _redirect("/login")

    # -- páginas -------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def inicio(request: Request) -> Response:
        require_admin(request)
        v = dados.visao(robot_db, teto=teto, base_url=base_url, intervalo_min=intervalo_min)
        return render(request, "visao.html", v=v, intervalo_min=intervalo_min)

    @app.get("/publicacoes", response_class=HTMLResponse)
    def lista_publicacoes(request: Request) -> Response:
        require_admin(request)
        return render(request, "publicacoes.html", itens=dados.publicacoes(robot_db, base_url=base_url))

    @app.get("/gasto", response_class=HTMLResponse)
    def pagina_gasto(request: Request) -> Response:
        require_admin(request)
        return render(request, "gasto.html", dias=dados.gasto(robot_db), teto=teto)

    @app.get("/falhas", response_class=HTMLResponse)
    def pagina_falhas(request: Request) -> Response:
        require_admin(request)
        return render(request, "falhas.html", f=dados.falhas(robot_db, base_url=base_url))

    return app

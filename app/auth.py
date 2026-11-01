import secrets
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette import status

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def require_user(request: Request) -> str:
    user = request.session.get("user")
    if user:
        return str(user)
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    login_url = f"/login?next={quote(next_path, safe='')}"
    raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": login_url})


def safe_next(value: str) -> str:
    parsed = urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or parsed.scheme
        or parsed.netloc
    ):
        return "/"
    return value


@router.get("/login")
def login_form(request: Request, next: str = "/") -> Response:
    if request.session.get("user"):
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"next": next, "error": None},
    )


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
) -> Response:
    settings = request.app.state.settings
    user_matches = secrets.compare_digest(
        username.encode("utf-8"), settings.app_user.encode("utf-8")
    )
    password_matches = secrets.compare_digest(
        password.encode("utf-8"), settings.app_password.encode("utf-8")
    )
    request.session.clear()
    if not (user_matches and password_matches):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"next": next, "error": "Неверный логин или пароль"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    request.session["user"] = settings.app_user
    return RedirectResponse(url=safe_next(next), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from app.auth import require_user

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@router.get("/")
def summary(request: Request, user: str = Depends(require_user)) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"user": user},
    )

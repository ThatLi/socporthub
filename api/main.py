import logging
import asyncio

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from api.routes import auth, bug_reports, calendar, committees, disposables, email, proposals, reminders, telegram_webhook, users
from api.routes import grading, ai_review

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Social Port Hub API")
collection_reminder_task = None
grading_task = None


@app.middleware("http")
async def disable_webapp_asset_caching(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.on_event("startup")
async def start_scheduled_notifications() -> None:
    global collection_reminder_task, grading_task
    from api.config import get_settings
    if get_settings().environment == "production":
        from api.services.scheduled_notifications import collection_reminder_loop
        collection_reminder_task = asyncio.create_task(collection_reminder_loop())
    if get_settings().grading_enabled:
        from api.services.scheduled_notifications import grading_loop
        grading_task = asyncio.create_task(grading_loop())


@app.on_event("shutdown")
async def stop_scheduled_notifications() -> None:
    if collection_reminder_task:
        collection_reminder_task.cancel()
    if grading_task:
        grading_task.cancel()

app.include_router(auth.router)
app.include_router(bug_reports.router)
app.include_router(users.router)
app.include_router(committees.router)
app.include_router(proposals.router)
app.include_router(grading.router)
app.include_router(ai_review.router)
app.include_router(calendar.router)
app.include_router(disposables.router)
app.include_router(email.router)
app.include_router(reminders.router)
app.include_router(telegram_webhook.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# Serve the WebApp SPA as static files. Mounted last so it doesn't shadow /api routes.
app.mount("/", StaticFiles(directory="webapp", html=True), name="webapp")

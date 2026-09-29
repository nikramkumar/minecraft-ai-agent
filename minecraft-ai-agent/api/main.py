"""
FastAPI application entrypoint.

Deliberately thin: this file only assembles routers into an app. Route
logic lives in api/routes/, dependency construction lives in
api/dependencies.py, and business logic lives in agent/ and monitoring/ --
none of it is duplicated or reimplemented here.
"""

from __future__ import annotations

from fastapi import FastAPI

from api.routes import chat, investigations, server


def create_app() -> FastAPI:
    app = FastAPI(
        title="Minecraft Server AI Incident Response Agent",
        description="An AI agent that inspects Minecraft/Linux telemetry to diagnose server health issues.",
        version="0.1.0",
    )
    app.include_router(chat.router)
    app.include_router(server.router)
    app.include_router(investigations.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()

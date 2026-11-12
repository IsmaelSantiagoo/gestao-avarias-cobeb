#!/usr/bin/env python3
"""Recebe webhooks assinados do GitHub e enfileira o deploy da Cobeb."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import queue
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


LOG = logging.getLogger("cobeb-webhook")
MAX_BODY_BYTES = 2 * 1024 * 1024


def csv_set(variable: str) -> set[str]:
    return {value.strip() for value in os.environ.get(variable, "").split(",") if value.strip()}


SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "").encode()
REPOSITORIES = csv_set("GITHUB_REPOSITORIES")
WORKFLOWS = csv_set("GITHUB_WORKFLOW_NAMES")
BRANCH = os.environ.get("GITHUB_BRANCH", "main")
DEPLOY_SCRIPT = Path(os.environ.get("DEPLOY_SCRIPT", "/opt/cobeb/deploy.sh")).resolve()
DEPLOY_TIMEOUT = int(os.environ.get("DEPLOY_TIMEOUT_SECONDS", "1800"))
HOST = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
PORT = int(os.environ.get("WEBHOOK_PORT", "9000"))

deployments: queue.Queue[tuple[str, str, str]] = queue.Queue(maxsize=20)
seen_deliveries: set[str] = set()
seen_lock = threading.Lock()


def validate_configuration() -> None:
    missing = []
    if not SECRET:
        missing.append("GITHUB_WEBHOOK_SECRET")
    if not REPOSITORIES:
        missing.append("GITHUB_REPOSITORIES")
    if not WORKFLOWS:
        missing.append("GITHUB_WORKFLOW_NAMES")
    if missing:
        raise SystemExit("Variaveis obrigatorias ausentes: " + ", ".join(missing))
    if not DEPLOY_SCRIPT.is_file():
        raise SystemExit(f"Script de deploy nao encontrado: {DEPLOY_SCRIPT}")


def valid_signature(body: bytes, signature: str) -> bool:
    if not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def deploy_candidate(event: str, payload: dict[str, Any]) -> tuple[bool, str]:
    repository = str(payload.get("repository", {}).get("full_name") or "")
    if repository not in REPOSITORIES:
        return False, "repositorio diferente"
    if event != "workflow_run":
        return False, "evento ignorado"

    run = payload.get("workflow_run", {})
    checks = (
        (payload.get("action") == "completed", "workflow ainda nao concluido"),
        (run.get("conclusion") == "success", "workflow sem sucesso"),
        (run.get("head_branch") == BRANCH, "branch diferente"),
        (run.get("name") in WORKFLOWS, "workflow diferente"),
    )
    for accepted, reason in checks:
        if not accepted:
            return False, reason
    return True, str(run.get("head_sha") or "unknown")


def deployment_worker() -> None:
    while True:
        delivery_id, repository, commit = deployments.get()
        LOG.info(
            "Iniciando deploy repository=%s commit=%s delivery=%s",
            repository,
            commit,
            delivery_id,
        )
        env = os.environ.copy()
        env.pop("GITHUB_WEBHOOK_SECRET", None)
        env["DEPLOY_COMMIT_SHA"] = commit
        env["DEPLOY_GITHUB_REPOSITORY"] = repository
        try:
            result = subprocess.run(
                ["/bin/bash", str(DEPLOY_SCRIPT)],
                cwd=str(DEPLOY_SCRIPT.parent),
                env=env,
                check=False,
                timeout=DEPLOY_TIMEOUT,
            )
            if result.returncode == 0:
                LOG.info("Deploy concluido repository=%s commit=%s", repository, commit)
            else:
                LOG.error(
                    "Deploy falhou codigo=%s repository=%s commit=%s",
                    result.returncode,
                    repository,
                    commit,
                )
        except subprocess.TimeoutExpired:
            LOG.exception("Deploy excedeu o tempo limite commit=%s", commit)
        except Exception:
            LOG.exception("Erro inesperado no deploy commit=%s", commit)
        finally:
            deployments.task_done()


class Handler(BaseHTTPRequestHandler):
    server_version = "CobebWebhook/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def send_json(self, status: int, data: dict[str, Any]) -> None:
        encoded = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self.send_json(200, {"status": "ok", "queue_size": deployments.qsize()})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/webhook/github":
            self.send_json(404, {"error": "not found"})
            return

        self.connection.settimeout(10)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "invalid content length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_json(413, {"error": "invalid payload size"})
            return

        body = self.rfile.read(length)
        if not valid_signature(body, self.headers.get("X-Hub-Signature-256", "")):
            self.send_json(401, {"error": "invalid signature"})
            return

        delivery_id = self.headers.get("X-GitHub-Delivery", "")
        if not delivery_id:
            self.send_json(400, {"error": "missing delivery id"})
            return
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": "invalid JSON"})
            return

        accepted, detail = deploy_candidate(self.headers.get("X-GitHub-Event", ""), payload)
        if not accepted:
            self.send_json(202, {"accepted": False, "reason": detail})
            return

        repository = str(payload["repository"]["full_name"])
        with seen_lock:
            if delivery_id in seen_deliveries:
                self.send_json(200, {"accepted": True, "duplicate": True})
                return
            try:
                deployments.put_nowait((delivery_id, repository, detail))
            except queue.Full:
                self.send_json(503, {"error": "deployment queue is full"})
                return
            seen_deliveries.add(delivery_id)
            if len(seen_deliveries) > 1000:
                seen_deliveries.pop()

        self.send_json(202, {"accepted": True, "commit": detail})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    validate_configuration()
    threading.Thread(target=deployment_worker, name="deployment-worker", daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    LOG.info("Escutando em http://%s:%s", HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()

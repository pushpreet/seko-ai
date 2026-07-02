"""Self-host kit generator: renders a personalized bootstrap for a user's own hardware.

Produces a docker-compose.yml, a .env (with the user's issued LLM key + endpoint), and an
install.sh so a friend can run the *same* GHCR workspace image locally against the public
LLM endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass

from seko_ai.config import Settings
from seko_ai.harness import DEFAULT_HARNESS, harness_binary

DEFAULT_LOCAL_SSH_PORT = 2222


@dataclass(frozen=True)
class SelfHostKit:
    """The three rendered files handed to a user."""

    env: str
    compose: str
    install: str


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def build_env(*, base_url: str, api_key: str, model: str, authorized_keys: str) -> str:
    """Render the .env consumed by the workspace container."""
    return (
        "# seko-ai self-host environment. Keep this file private — it holds your LLM key.\n"
        f"LLM_BASE_URL={base_url}\n"
        f"LLM_API_KEY={api_key}\n"
        f"LLM_MODEL={model}\n"
        f"TZ=UTC\n"
        f"# Your SSH public key (used to log into the workspace).\n"
        f"SEKO_AUTHORIZED_KEYS={authorized_keys}\n"
    )


def build_compose(*, image: str, ssh_port: int = DEFAULT_LOCAL_SSH_PORT) -> str:
    """Render the docker-compose.yml for a local workspace."""
    return (
        "services:\n"
        "  seko-workspace:\n"
        f"    image: {image}\n"
        "    container_name: seko-workspace\n"
        "    restart: unless-stopped\n"
        "    env_file: .env\n"
        "    # Lets the container reach an LLM running on your own host, if you point\n"
        "    # LLM_BASE_URL at http://host.docker.internal:PORT/v1 instead of the public URL.\n"
        "    extra_hosts:\n"
        '      - "host.docker.internal:host-gateway"\n'
        "    ports:\n"
        f'      - "{ssh_port}:22"\n'
        "    volumes:\n"
        "      - seko-home:/home/dev\n"
        "    security_opt:\n"
        "      - no-new-privileges:true\n"
        "\n"
        "volumes:\n"
        "  seko-home:\n"
    )


def build_install(*, ssh_port: int = DEFAULT_LOCAL_SSH_PORT, harness: str = DEFAULT_HARNESS) -> str:
    """Render install.sh (expects compose + .env alongside it)."""
    binary = harness_binary(harness)
    return (
        "#!/usr/bin/env bash\n"
        "# seko-ai self-host installer. Run in a directory containing docker-compose.yml\n"
        "# and .env (both downloaded from the seko-ai web UI alongside this script).\n"
        "set -euo pipefail\n"
        "\n"
        'if ! command -v docker >/dev/null 2>&1; then\n'
        '  echo "Docker is required: https://docs.docker.com/get-docker/" >&2\n'
        "  exit 1\n"
        "fi\n"
        'if ! docker compose version >/dev/null 2>&1; then\n'
        '  echo "Docker Compose v2 is required." >&2\n'
        "  exit 1\n"
        "fi\n"
        "\n"
        'for f in docker-compose.yml .env; do\n'
        '  [ -f "$f" ] || { echo "missing $f (download it from the seko-ai UI)" >&2; exit 1; }\n'
        "done\n"
        "\n"
        'echo "Pulling the workspace image and starting it..."\n'
        "docker compose pull\n"
        "docker compose up -d\n"
        "\n"
        'echo\n'
        f'echo "Workspace is up. Connect with: ssh dev@localhost -p {ssh_port}"\n'
        f'echo "Drive the {harness} harness inside: ssh dev@localhost -p '
        f'{ssh_port} -t {binary}"\n'
    )


def build_kit(
    settings: Settings,
    *,
    api_key: str,
    authorized_keys: str,
    harness: str = DEFAULT_HARNESS,
) -> SelfHostKit:
    """Render the full kit for a user."""
    return SelfHostKit(
        env=build_env(
            base_url=settings.llm_public_url,
            api_key=api_key,
            model=settings.llm_model,
            authorized_keys=authorized_keys,
        ),
        compose=build_compose(image=settings.workspace_image),
        install=build_install(harness=harness),
    )

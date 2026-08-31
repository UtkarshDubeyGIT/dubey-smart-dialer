from pathlib import Path

import yaml


def test_render_web_command_uses_native_shell_expansion() -> None:
    blueprint = yaml.safe_load(Path("render.yaml").read_text())
    web = next(service for service in blueprint["services"] if service["type"] == "web")

    assert web["dockerCommand"] == "smart-dialer serve --port $PORT"

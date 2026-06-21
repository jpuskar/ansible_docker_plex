from __future__ import annotations

import asyncio
import logging
import os
import yaml

from aiohttp import web

from baseline_manager import BaselineManager
from discord_notifier import DiscordNotifier
from object_detector import ObjectDetector, TARGET_CLASSES
from proxy_types.camera import CameraTuning, build_camera_configs
from shinobi_notifier import ShinobiNotifier

with open("/config/config.yaml") as f:
    config = yaml.safe_load(f)

# Custom TRACE level (below DEBUG) for high-frequency per-inference timing logs.
# These are already captured as Prometheus metrics; TRACE keeps them out of
# normal DEBUG output but available via log_level: "TRACE" when needed.
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

logging.basicConfig(
    level=config.get("log_level", "INFO"),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("smtp-proxy")


async def main() -> None:
    # Set up Discord notifier if configured
    discord_notifier = None
    discord_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    discord_channel = os.environ.get(
        "DISCORD_CHANNEL_ID", config.get("discord_channel_id", "")
    )
    if discord_token and discord_channel:
        discord_notifier = DiscordNotifier(
            bot_token=discord_token,
            channel_id=discord_channel,
            cooldown_seconds=config.get("discord_cooldown_seconds", 60),
        )
        log.info(
            "Discord notifications enabled (channel %s, cooldown %ds)",
            discord_channel,
            config.get("discord_cooldown_seconds", 60),
        )

    # Set up Shinobi notifier if configured
    shinobi_notifier = None
    shinobi_cfg = config.get("shinobi", {})
    shinobi_api_key = os.environ.get("SHINOBI_API_KEY", shinobi_cfg.get("api_key", ""))
    if shinobi_cfg.get("base_url") and shinobi_api_key:
        shinobi_notifier = ShinobiNotifier(
            base_url=shinobi_cfg["base_url"],
            api_key=shinobi_api_key,
            group_key=shinobi_cfg.get("group_key", ""),
            monitor_map=shinobi_cfg.get("monitor_map", {}),
        )
        log.info("Shinobi notifications enabled (%s)", shinobi_cfg["base_url"])

    # Set up baseline manager if cameras are configured
    baseline_manager = None
    default_camera_tuning = CameraTuning.from_mapping(config, path="config")
    camera_configs = build_camera_configs(
        config.get("cameras", []),
        default_tuning=default_camera_tuning,
    )

    # Auto-discover Shinobi monitor IDs from camera IPs
    if shinobi_notifier and camera_configs:
        await shinobi_notifier.discover_monitors(camera_configs)

    if camera_configs:
        # Detector is shared between baseline polling and email filtering
        detector = ObjectDetector(
            confidence_threshold=config.get("confidence_threshold", 0.25),
            target_classes=TARGET_CLASSES,
            ir_confidence_threshold=config.get("ir_confidence_threshold", 0.45),
        )
        baseline_manager = BaselineManager(
            camera_configs=camera_configs,
            username=os.environ.get(
                "CAMERA_USERNAME", config.get("camera_username", "admin")
            ),
            password=os.environ.get(
                "CAMERA_PASSWORD", config.get("camera_password", "")
            ),
            detector=detector,
            buffer_seconds=config.get("buffer_seconds", 10),
            baseline_interval_seconds=config.get("baseline_interval_seconds", 60),
            discord_notifier=discord_notifier,
            motion_detection=config.get("motion_detection", True),
            shinobi_notifier=shinobi_notifier,
        )
        await baseline_manager.start()

    # Health check + Prometheus metrics endpoint
    health_port = config.get("health_port", 8080)
    app = web.Application()
    app.router.add_get("/healthz", lambda _: web.Response(text="ok"))

    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

    async def metrics_handler(_request):
        body = generate_latest()
        return web.Response(body=body, content_type="text/plain", charset="utf-8")

    app.router.add_get("/metrics", metrics_handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", health_port).start()
    log.info("Health endpoint on :%s/healthz", health_port)

    try:
        await asyncio.Event().wait()  # block forever
    finally:
        if baseline_manager:
            await baseline_manager.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

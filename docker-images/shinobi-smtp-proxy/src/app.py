from __future__ import annotations

import asyncio
import logging
import os
import warnings
import yaml

from aiohttp import web
from aiosmtpd.controller import Controller

from SMTPProxyHandler import SMTPProxyHandler
from auth_helper import authenticator
from baseline_manager import BaselineManager
from discord_notifier import DiscordNotifier
from object_detector import ObjectDetector
from shinobi_notifier import ShinobiNotifier
from SMTPProxyHandler import TARGET_CLASSES

# aiosmtpd emits a UserWarning on every connection when auth_require_tls=False.
# Show it once at startup, route through logging, and strip the source code line.
warnings.filterwarnings("once", message="Requiring AUTH while not requiring TLS")
warnings.filterwarnings("ignore", message="Session.login_data is deprecated")
warnings.formatwarning = lambda msg, cat, *a, **kw: f"{cat.__name__}: {msg}"
logging.captureWarnings(True)

with open("/config/config.yaml") as f:
    config = yaml.safe_load(f)

logging.basicConfig(
    level=config.get("log_level", "INFO"),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("smtp-proxy")

# Silence aiosmtpd's per-connection SMTP verb logging (EHLO, MAIL, RCPT, QUIT, etc.)
logging.getLogger("mail.log").setLevel(logging.WARNING)


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
            cooldown_seconds=config.get("discord_cooldown", 60),
        )
        log.info(
            "Discord notifications enabled (channel %s, cooldown %ds)",
            discord_channel,
            config.get("discord_cooldown", 60),
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
    cameras_cfg = config.get("cameras", {})

    # Auto-discover Shinobi monitor IDs from camera IPs
    if shinobi_notifier and cameras_cfg:
        await shinobi_notifier.discover_monitors(cameras_cfg)

    if cameras_cfg:
        # Detector is shared between baseline polling and email filtering
        detector = ObjectDetector(
            confidence_threshold=config.get("confidence_threshold", 0.25),
            target_classes=TARGET_CLASSES,
            ir_confidence_threshold=config.get("ir_confidence_threshold", 0.45),
        )
        baseline_manager = BaselineManager(
            cameras=cameras_cfg,
            username=os.environ.get(
                "CAMERA_USERNAME", config.get("camera_username", "admin")
            ),
            password=os.environ.get(
                "CAMERA_PASSWORD", config.get("camera_password", "")
            ),
            detector=detector,
            snapshot_interval=config.get("snapshot_interval", 1),
            buffer_seconds=config.get("buffer_seconds", 10),
            baseline_interval=config.get("baseline_interval", 60),
            position_tolerance=config.get("position_tolerance", 0.15),
            strategy=config.get("camera_strategy", "rtsp"),
            discord_notifier=discord_notifier,
            motion_detection=config.get("motion_detection", True),
            motion_threshold=config.get("motion_threshold", 25),
            motion_min_area=config.get("motion_min_area", 500),
            detection_zones=config.get("detection_zones", {}),
            confirm_cameras=config.get("confirm_cameras", []),
            min_detection_area=config.get("min_detection_area", 0.003),
            static_baselines=config.get("static_baselines", {}),
            shinobi_notifier=shinobi_notifier,
        )
        await baseline_manager.start()

    # SMTP proxy (optional — can run as standalone motion detector without it)
    controller = None
    if config.get("smtp_enabled", False):
        handler = SMTPProxyHandler(
            forward_host=config["forward_host"],
            forward_port=config["forward_port"],
            fallback_subject=config.get("fallback_subject", "Motion Detected"),
            filter_keywords=config.get("filter_keywords", []),
            ai_detection_enabled=config.get("ai_detection_enabled", True),
            confidence_threshold=config.get("confidence_threshold", 0.25),
            debug_mime=config.get("debug_mime", False),
            baseline_manager=baseline_manager,
            discord_notifier=discord_notifier,
        )

        controller = Controller(
            handler,
            hostname=config.get("listen_host", "0.0.0.0"),
            port=config.get("listen_port", 2525),
            auth_required=True,
            auth_require_tls=False,
            authenticator=authenticator,
        )
        controller.start()
        log.info(
            "SMTP listening on %s:%s, forwarding to %s:%s",
            config["listen_host"],
            config["listen_port"],
            config["forward_host"],
            config["forward_port"],
        )
    else:
        log.info("SMTP disabled — running as standalone motion detector")

    # Health check so k8s probes don't poke the SMTP port
    health_port = config.get("health_port", 8080)
    app = web.Application()
    app.router.add_get("/healthz", lambda _: web.Response(text="ok"))
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
        if controller:
            controller.stop()


if __name__ == "__main__":
    asyncio.run(main())

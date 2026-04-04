import asyncio
import logging
import warnings
import yaml

from aiohttp import web
from aiosmtpd.controller import Controller


from SMTPProxyHandler import SMTPProxyHandler
from auth_helper import authenticator_callback


# Load configuration
with open('/config/config.yaml', 'r') as f:
    config = yaml.safe_load(f)

# Setup logging
logging.basicConfig(
    level=getattr(logging, config['log_level']),
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Route warnings.warn() through the logging system so they get formatted
# consistently (fixes aiosmtpd's smtp.py:372 UserWarning going to raw stderr)
logging.captureWarnings(True)

# Suppress the repetitive aiosmtpd TLS warning — it fires on every new connection
import warnings
warnings.filterwarnings('once', message='Requiring AUTH while not requiring TLS')


async def main():
    handler = SMTPProxyHandler(
        forward_host=config['forward_host'],
        forward_port=config['forward_port'],
        fallback_subject=config['fallback_subject'],
        filter_keywords=config['filter_keywords'],
        ai_detection_enabled=config['ai_detection_enabled'],
        confidence_threshold=config['confidence_threshold'],
    )

    controller = Controller(
        handler,
        hostname=config['listen_host'],
        port=config['listen_port'],
        auth_required=True,  # Force use of our authenticator
        auth_require_tls=False,
        authenticator=authenticator_callback
    )

    logger.info(f"Starting SMTP proxy on {config['listen_host']}:{config['listen_port']}")
    logger.info(f"Forwarding to {config['forward_host']}:{config['forward_port']}")

    controller.start()

    # Health check HTTP endpoint
    health_port = config.get('health_port', 8080)

    async def health_handler(request):
        return web.Response(text="ok")

    health_app = web.Application()
    health_app.router.add_get('/healthz', health_handler)
    runner = web.AppRunner(health_app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', health_port)
    await site.start()
    logger.info(f"Health check endpoint listening on port {health_port}")

    try:
        # Keep the server running
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down SMTP proxy")
    finally:
        await runner.cleanup()
        controller.stop()


if __name__ == "__main__":
    asyncio.run(main())

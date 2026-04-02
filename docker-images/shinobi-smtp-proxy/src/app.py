import asyncio
import logging
import yaml

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

    try:
        # Keep the server running
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down SMTP proxy")
    finally:
        controller.stop()


if __name__ == "__main__":
    asyncio.run(main())

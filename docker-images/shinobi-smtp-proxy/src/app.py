import asyncio
import logging
import warnings
import yaml

from aiohttp import web
from aiosmtpd.controller import Controller

from SMTPProxyHandler import SMTPProxyHandler
from auth_helper import authenticator

# aiosmtpd emits a UserWarning on every connection when auth_require_tls=False.
# Show it once at startup, route through logging, and strip the source code line.
warnings.filterwarnings('once', message='Requiring AUTH while not requiring TLS')
warnings.formatwarning = lambda msg, cat, *a, **kw: f'{cat.__name__}: {msg}'
logging.captureWarnings(True)

with open('/config/config.yaml') as f:
    config = yaml.safe_load(f)

logging.basicConfig(
    level=config.get('log_level', 'INFO'),
    format='%(asctime)s - %(levelname)s - %(message)s',
)
log = logging.getLogger('smtp-proxy')


async def main():
    handler = SMTPProxyHandler(
        forward_host=config['forward_host'],
        forward_port=config['forward_port'],
        fallback_subject=config.get('fallback_subject', 'Motion Detected'),
        filter_keywords=config.get('filter_keywords', []),
        ai_detection_enabled=config.get('ai_detection_enabled', True),
        confidence_threshold=config.get('confidence_threshold', 0.25),
        debug_mime=config.get('debug_mime', False),
    )

    controller = Controller(
        handler,
        hostname=config.get('listen_host', '0.0.0.0'),
        port=config.get('listen_port', 2525),
        auth_required=True,
        auth_require_tls=False,
        authenticator=authenticator,
    )
    controller.start()
    log.info("SMTP listening on %s:%s, forwarding to %s:%s",
             config['listen_host'], config['listen_port'],
             config['forward_host'], config['forward_port'])

    # Health check so k8s probes don't poke the SMTP port
    health_port = config.get('health_port', 8080)
    app = web.Application()
    app.router.add_get('/healthz', lambda _: web.Response(text='ok'))
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', health_port).start()
    log.info("Health endpoint on :%s/healthz", health_port)

    try:
        await asyncio.Event().wait()  # block forever
    finally:
        await runner.cleanup()
        controller.stop()


if __name__ == '__main__':
    asyncio.run(main())

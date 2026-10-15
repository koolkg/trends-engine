"""te-api entrypoint."""
import logging
import signal

from api.cache import FeedCache
from api.campaigns import CampaignService
from api.config import load_config
from api.mailchimp import MailchimpClient
from api.redis_client import Redis
from api.repository import Repository
from api.server import make_server
from api.trends import TrendService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("te-api")


def _raise_keyboard_interrupt(*_):
    raise KeyboardInterrupt


def main() -> None:
    c = load_config()

    def service() -> TrendService:  # one per request thread (own Redis + Postgres connections)
        return TrendService(FeedCache(Redis(c.redis_host, c.redis_port), c.cache_prefix, c.cache_ttl_seconds),
                            Repository(c.postgres_host, c.postgres_port, c.postgres_user, c.postgres_password,
                                       c.postgres_db),
                            c.default_limit, c.max_limit)

    def campaigns() -> CampaignService:  # one per request thread, like the feed service
        return CampaignService(Repository(c.postgres_host, c.postgres_port, c.postgres_user, c.postgres_password,
                                          c.postgres_db),
                               MailchimpClient(c.mailchimp_api_url, c.mailchimp_api_key, c.mailchimp_timeout_seconds),
                               c.mailchimp_from_name, c.mailchimp_reply_to)

    server = make_server(c.host, c.port, service, campaigns)
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    logger.info("listening on %s:%d (cache prefix %r, mailchimp %s)", c.host, c.port, c.cache_prefix,
                c.mailchimp_api_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

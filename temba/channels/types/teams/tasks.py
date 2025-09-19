import logging

import requests
from celery import shared_task
from django_valkey import get_valkey_connection

from django.utils import timezone

from temba.channels.models import Channel
from temba.request_logs.models import HTTPLog

from .type import TeamsType

logger = logging.getLogger(__name__)


@shared_task(track_started=True, name="refresh_teams_tokens")
def refresh_teams_tokens():
    r = get_valkey_connection()
    if r.get("refresh_teams_tokens"):  # pragma: no cover
        return
    with r.lock("refresh_teams_tokens", 1800):
        # iterate across each of our teams channels and get a new token
        for channel in Channel.objects.filter(is_active=True, channel_type="TM").order_by("id"):
            try:
                url = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"

                request_body = {
                    "client_id": channel.config[TeamsType.CONFIG_TEAMS_APPLICATION_ID],
                    "grant_type": "client_credentials",
                    "scope": "https://api.botframework.com/.default",
                    "client_secret": channel.config[TeamsType.CONFIG_TEAMS_APPLICATION_PASSWORD],
                }
                headers = {"Content-Type": "application/x-www-form-urlencoded"}

                start = timezone.now()
                resp = requests.post(url, data=request_body, headers=headers)

                HTTPLog.from_response(
                    HTTPLog.TEAMS_TOKENS_SYNCED,
                    resp,
                    start,
                    timezone.now(),
                    channel=channel,
                )

                if resp.status_code != 200:
                    continue

                channel.config["auth_token"] = resp.json()["access_token"]
                channel.save(update_fields=["config"])

            except Exception as e:
                logger.error(f"Error refreshing teams tokens: {str(e)}", exc_info=True)

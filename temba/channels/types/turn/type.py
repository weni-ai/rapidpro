import logging

import requests

from django.urls import re_path
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.channels.types.turn.views import ClaimView
from temba.contacts.models import URN
from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from temba.utils.whatsapp.views import SyncLogsView, TemplatesView

from ...models import ChannelType

CONFIG_FB_BUSINESS_ID = "fb_business_id"
CONFIG_FB_ACCESS_TOKEN = "fb_access_token"
CONFIG_FB_NAMESPACE = "fb_namespace"
CONFIG_FB_TEMPLATE_LIST_DOMAIN = "fb_template_list_domain"
CONFIG_FB_TEMPLATE_API_VERSION = "fb_template_list_domain_api_version"

TEMPLATE_LIST_URL = "https://%s/%s/%s/message_templates"

logger = logging.getLogger(__name__)


class TurnType(ChannelType):
    """
    A Turn.io WhatsApp Channel Type
    """

    extra_links = [dict(label=_("Message Templates"), view_name="channels.types.turn.templates")]

    code = "TRN"
    name = "Turn.io WhatsApp"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^trn/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.WHATSAPP_SCHEME]
    max_length = 4096

    claim_blurb = _(
        "If you have an enterprise Turn.io WhatsApp account, you can connect it to communicate with your contacts"
    )
    claim_view = ClaimView

    configuration_blurb = _(
        "To finish configuring this channel, you'll need Turn.io to use the following callback URL."
    )
    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.trn' channel.uuid 'receive' %}",
            description=_("This URL should be called by Turn.io when new messages are received."),
        ),
    )

    def get_urls(self):
        return [
            self.get_claim_url(),
            re_path(r"^(?P<uuid>[a-z0-9\-]+)/templates$", TemplatesView.as_view(channel_type=self), name="templates"),
            re_path(r"^(?P<uuid>[a-z0-9\-]+)/sync_logs$", SyncLogsView.as_view(channel_type=self), name="sync_logs"),
        ]

    def deactivate(self, channel):
        TemplateTranslation.trim(channel, [])

    def get_api_templates(self, channel):
        if CONFIG_FB_BUSINESS_ID not in channel.config or CONFIG_FB_ACCESS_TOKEN not in channel.config:
            return [], False

        facebook_template_domain = channel.config.get(CONFIG_FB_TEMPLATE_LIST_DOMAIN, "graph.facebook.com")
        facebook_business_id = channel.config.get(CONFIG_FB_BUSINESS_ID)
        facebook_template_api_version = channel.config.get(CONFIG_FB_TEMPLATE_API_VERSION, "v14.0")
        url = TEMPLATE_LIST_URL % (facebook_template_domain, facebook_template_api_version, facebook_business_id)
        template_data = []

        start = timezone.now()
        try:
            while url:
                response = requests.get(
                    url, params={"access_token": channel.config[CONFIG_FB_ACCESS_TOKEN], "limit": 255}
                )
                HTTPLog.from_response(
                    HTTPLog.WHATSAPP_TEMPLATES_SYNCED, response, start, timezone.now(), channel=channel
                )
                response.raise_for_status()

                template_data.extend(response.json()["data"])
                url = response.json().get("paging", {}).get("next", None)
            return template_data, True
        except requests.RequestException as e:
            HTTPLog.from_exception(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, e, start, channel=channel)
            return [], False

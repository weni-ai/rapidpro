import json
from unittest.mock import call, patch

from requests import RequestException

from django.urls import reverse

from temba.channels.types.turn.tasks import refresh_turn_whatsapp_tokens
from temba.channels.types.turn.type import (
    CONFIG_FB_ACCESS_TOKEN,
    CONFIG_FB_BUSINESS_ID,
    CONFIG_FB_NAMESPACE,
    CONFIG_FB_TEMPLATE_LIST_DOMAIN,
    TurnType,
)
from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from temba.tests import CRUDLTestMixin, MockResponse, TembaTest

from ...models import Channel


class TurnTypeTest(CRUDLTestMixin, TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse("channels.types.turn.claim")
        self.login(self.admin)

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["address"] = "1234"
        post_data["username"] = "temba"
        post_data["password"] = "tembapasswd"
        post_data["country"] = "RW"
        post_data["namespace"] = "my-custom-app"
        post_data["access_token"] = "token123"

        response = self.client.post(url, post_data)
        self.assertFormError(response.context["form"], None, ["Please enter a valid phone number"])

        post_data["address"] = "0788123123"

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(400, '{ "error": "true" }')
            response = self.client.post(url, post_data)
            self.assertEqual(200, response.status_code)
            self.assertFalse(Channel.objects.all())
            self.assertContains(response, "check username and password")

        with (
            patch("socket.gethostbyname", return_value="123.123.123.123"),
            patch("requests.post") as mock_post,
            patch("requests.get") as mock_get,
        ):
            mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc123"}]}')
            mock_get.return_value = MockResponse(400, '{"data": []}')
            response = self.client.post(url, post_data)

            self.assertEqual(200, response.status_code)
            self.assertFalse(Channel.objects.all())
            self.assertFormError(response.context["form"], None, ["Unable to access Messages templates from turn.io"])

        with (
            patch("socket.gethostbyname", return_value="123.123.123.123"),
            patch("requests.post") as mock_post,
            patch("requests.get") as mock_get,
        ):
            mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc123"}]}')
            mock_get.return_value = MockResponse(200, '{"data": []}')

            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()
        self.assertRedirects(response, reverse("channels.channel_configuration", args=[channel.uuid]))
        self.assertEqual(channel.channel_type, "TRN")

        self.assertEqual("abc123", channel.config[Channel.CONFIG_AUTH_TOKEN])
        self.assertEqual("https://whatsapp.turn.io", channel.config[Channel.CONFIG_BASE_URL])

        self.assertEqual("+250788123123", channel.address)
        self.assertEqual("RW", channel.country)
        self.assertEqual("TRN", channel.channel_type)
        self.assertEqual(45, channel.tps)
        self.assertEqual("TRN", channel.type.code)

        response = self.client.get(reverse("channels.channel_configuration", args=[channel.uuid]))
        self.assertContains(response, reverse("courier.trn", args=[channel.uuid, "receive"]))

    @patch("requests.get")
    def test_get_api_templates(self, mock_get):
        TemplateTranslation.objects.all().delete()
        Channel.objects.all().delete()
        HTTPLog.objects.all().delete()

        channel = self.create_channel(
            "TRN",
            "Turn: 1234",
            "1234",
            config={
                Channel.CONFIG_BASE_URL: "https://whatsapp.turn.io",
                Channel.CONFIG_USERNAME: "temba",
                Channel.CONFIG_PASSWORD: "tembapasswd",
                Channel.CONFIG_AUTH_TOKEN: "authtoken123",
                CONFIG_FB_BUSINESS_ID: "1234",
                CONFIG_FB_ACCESS_TOKEN: "token123",
                CONFIG_FB_NAMESPACE: "my-custom-app",
                CONFIG_FB_TEMPLATE_LIST_DOMAIN: "whatsapp.turn.io",
            },
        )

        mock_get.side_effect = [
            RequestException("Network is unreachable", response=MockResponse(100, "")),
            MockResponse(400, '{ "meta": { "success": false } }'),
            MockResponse(200, '{"data": ["foo", "bar"]}'),
            MockResponse(
                200,
                '{"data": ["foo"], "paging": {"next": "https://whatsapp.turn.io/v14.0/1234/message_templates?cursor=MjQZD"} }',
            ),
            MockResponse(200, '{"data": ["bar"], "paging": {"next": null} }'),
        ]

        templates_data, no_error = TurnType().get_api_templates(channel)
        self.assertEqual(1, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED).count())
        self.assertFalse(no_error)
        self.assertEqual([], templates_data)

        templates_data, no_error = TurnType().get_api_templates(channel)
        self.assertFalse(no_error)
        self.assertEqual([], templates_data)

        templates_data, no_error = TurnType().get_api_templates(channel)
        self.assertTrue(no_error)
        self.assertEqual(["foo", "bar"], templates_data)

        for log in HTTPLog.objects.all():
            self.assertNotIn("token123", json.dumps(log.get_display()))

        mock_get.assert_called_with(
            "https://whatsapp.turn.io/v14.0/1234/message_templates",
            params={"access_token": "token123", "limit": 255},
        )

        templates_data, no_error = TurnType().get_api_templates(channel)
        self.assertTrue(no_error)
        self.assertEqual(["foo", "bar"], templates_data)

        mock_get.assert_has_calls(
            [
                call(
                    "https://whatsapp.turn.io/v14.0/1234/message_templates",
                    params={"access_token": "token123", "limit": 255},
                ),
                call(
                    "https://whatsapp.turn.io/v14.0/1234/message_templates?cursor=MjQZD",
                    params={"access_token": "token123", "limit": 255},
                ),
            ]
        )

    def test_refresh_tokens(self):
        TemplateTranslation.objects.all().delete()
        Channel.objects.all().delete()
        HTTPLog.objects.all().delete()

        channel = self.create_channel(
            "TRN",
            "Turn: 1234",
            "1234",
            config={
                Channel.CONFIG_BASE_URL: "https://whatsapp.turn.io",
                Channel.CONFIG_USERNAME: "temba",
                Channel.CONFIG_PASSWORD: "tembapasswd",
                Channel.CONFIG_AUTH_TOKEN: "authtoken123",
                CONFIG_FB_BUSINESS_ID: "1234",
                CONFIG_FB_ACCESS_TOKEN: "token123",
                CONFIG_FB_NAMESPACE: "my-custom-app",
                CONFIG_FB_TEMPLATE_LIST_DOMAIN: "whatsapp.turn.io",
            },
        )

        channel2 = self.create_channel(
            "TRN",
            "Turn: 1235",
            "1235",
            config={
                Channel.CONFIG_BASE_URL: "https://whatsapp.turn.io",
                Channel.CONFIG_USERNAME: "temba",
                Channel.CONFIG_PASSWORD: "tembapasswd",
                Channel.CONFIG_AUTH_TOKEN: "authtoken123",
                CONFIG_FB_BUSINESS_ID: "1234",
                CONFIG_FB_ACCESS_TOKEN: "token123",
                CONFIG_FB_NAMESPACE: "my-custom-app",
                CONFIG_FB_TEMPLATE_LIST_DOMAIN: "whatsapp.turn.io",
            },
        )

        with patch("temba.channels.types.turn.tasks.requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc345"}]}')
            self.assertFalse(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TOKENS_SYNCED, is_error=False))
            refresh_turn_whatsapp_tokens()
            self.assertTrue(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TOKENS_SYNCED, is_error=False))
            channel.refresh_from_db()
            self.assertEqual("abc345", channel.config[Channel.CONFIG_AUTH_TOKEN])

        with patch("temba.channels.types.turn.tasks.requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(400, '{ "error": true }')]
            self.assertFalse(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TOKENS_SYNCED, is_error=True))
            refresh_turn_whatsapp_tokens()
            self.assertTrue(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TOKENS_SYNCED, is_error=True))
            channel.refresh_from_db()
            self.assertEqual("abc345", channel.config[Channel.CONFIG_AUTH_TOKEN])

        with patch("temba.channels.types.turn.tasks.requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(200, ""), MockResponse(200, '{"users": [{"token": "abc098"}]}')]
            refresh_turn_whatsapp_tokens()

            channel.refresh_from_db()
            channel2.refresh_from_db()
            self.assertEqual("abc345", channel.config[Channel.CONFIG_AUTH_TOKEN])
            self.assertEqual("abc098", channel2.config[Channel.CONFIG_AUTH_TOKEN])

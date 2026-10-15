"""Mailchimp Marketing API (v3) client: just the calls te-api needs, on the
standard library. Pointed at te-infra's mailchimp-fake in the PoC; the real API
is the same requests against https://<dc>.api.mailchimp.com/3.0 with a real key.

Auth is HTTP Basic, any username, the API key as the password (Mailchimp's scheme).
"""
import base64
import json
import socket
import urllib.error
import urllib.request
from typing import Dict, Optional


class MailchimpError(Exception):
    """Mailchimp answered with an error (4xx/5xx). `status` is its HTTP status."""

    def __init__(self, status: int, title: str, detail: str):
        super().__init__(f"{status} {title}: {detail}")
        self.status, self.title, self.detail = status, title, detail


class MailchimpUnavailable(Exception):
    """Couldn't reach Mailchimp at all (connection refused, DNS, timeout)."""


class MailchimpClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._auth = "Basic " + base64.b64encode(f"te-api:{api_key}".encode()).decode()

    def _call(self, method: str, path: str, body: Optional[Dict] = None) -> Dict:
        req = urllib.request.Request(self.base_url + path, method=method,
                                     data=json.dumps(body).encode("utf-8") if body is not None else None)
        req.add_header("Authorization", self._auth)
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                problem = json.loads(e.read() or b"{}")
            except ValueError:
                problem = {}
            raise MailchimpError(e.code, problem.get("title", e.reason or "error"), problem.get("detail", ""))
        except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
            raise MailchimpUnavailable(str(getattr(e, "reason", e)))

    def ping(self) -> bool:
        return self._call("GET", "/ping").get("health_status") is not None

    def create_campaign(self, subject_line: str, preview_text: str, title: str, from_name: str,
                        reply_to: str) -> Dict:
        """A draft ("save") regular campaign. No audience yet: the marketer picks it in Mailchimp."""
        return self._call("POST", "/campaigns", {
            "type": "regular",
            "settings": {"subject_line": subject_line, "preview_text": preview_text, "title": title,
                         "from_name": from_name, "reply_to": reply_to},
        })

    def get_campaign(self, campaign_id: str) -> Dict:
        return self._call("GET", f"/campaigns/{campaign_id}")

    def set_content(self, campaign_id: str, html: str, plain_text: str) -> Dict:
        return self._call("PUT", f"/campaigns/{campaign_id}/content", {"html": html, "plain_text": plain_text})

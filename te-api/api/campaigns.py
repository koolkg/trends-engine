"""POST /v1/campaigns/draft-block: turn a trend into a draft content block in a
Mailchimp campaign (Flow 2 steps 8-11). A module of te-api rather than a
separate service (a design decision).

    request  {"trendId": "<uuid>", "campaignId": null | "<mailchimp id>", "tone": "casual"}
    response 201 {"blockId", "suggestedCopy", "status": "draft",
                  + trendId, campaignId, tone, subjectLine, previewText, headline, previewUrl}

POST /v1/campaigns/draft-block/preview takes the same body and returns the copy
that *would* be inserted (no Mailchimp call, no row), so te-ui can show the
marketer exactly what goes into the campaign before they insert it.

1. Validate the request; look the trend up in trend_ranking (404 if unknown).
2. Write the copy for the requested tone (copywriter.py).
3. Mailchimp: with no campaignId, create a draft campaign; with one, check it
   exists (404 if not). Then put the block in as the campaign's content.
4. Record a campaign_draft_block row, and answer with its id.

Mailchimp is called before the row is written: if the insert then fails, the
draft exists in Mailchimp without a row here, which is harmless (a marketer
can still see and delete it there). The reverse would leave rows pointing at
drafts that don't exist.
"""
import logging
import re
import uuid
from typing import Dict, Optional, Tuple

from api import copywriter
from api.mailchimp import MailchimpError, MailchimpUnavailable
from api.trends import UUID_RE, ApiError

logger = logging.getLogger(__name__)

CAMPAIGN_ID_RE = re.compile(r"^[0-9a-zA-Z]{1,64}$")
DEFAULT_TONE = "casual"


def parse_request(body) -> Tuple[str, Optional[str], str]:
    if not isinstance(body, dict):
        raise ApiError(400, "bad_request", "body must be a JSON object")
    trend_id = body.get("trendId")
    if not isinstance(trend_id, str) or not UUID_RE.match(trend_id.lower()):
        raise ApiError(400, "bad_trend_id", "trendId must be a trend id from GET /v1/trends")
    campaign_id = body.get("campaignId")
    if campaign_id is not None and (not isinstance(campaign_id, str) or not CAMPAIGN_ID_RE.match(campaign_id)):
        raise ApiError(400, "bad_campaign_id", "campaignId must be a Mailchimp campaign id or null")
    tone = body.get("tone", DEFAULT_TONE) or DEFAULT_TONE
    tone = copywriter.ALIASES.get(tone, tone) if isinstance(tone, str) else tone  # "playful" (the doc's example) = casual
    if tone not in copywriter.TONES:
        raise ApiError(400, "bad_tone", f"tone must be one of {', '.join(copywriter.TONES)}")
    return trend_id.lower(), campaign_id, tone


def copy_fields(copy: copywriter.Copy) -> Dict:
    """Everything that goes into the campaign, as te-ui shows it."""
    return {"subjectLine": copy.subject_line, "previewText": copy.preview_text, "headline": copy.headline,
            "suggestedCopy": copy.body}


class CampaignService:
    def __init__(self, repo, mailchimp, from_name: str = "Trends Engine", reply_to: str = "hello@example.com"):
        self.repo, self.mailchimp = repo, mailchimp
        self.from_name, self.reply_to = from_name, reply_to

    def _trend(self, trend_id: str) -> Dict:
        trend = self.repo.trend_detail(trend_id)
        if trend is None:
            raise ApiError(404, "trend_not_found", "trend not found")
        return trend

    def preview(self, body) -> Dict:
        """The copy draft_block would insert, without inserting anything."""
        trend_id, _, tone = parse_request(body)
        return {"trendId": trend_id, "tone": tone, **copy_fields(copywriter.write(self._trend(trend_id), tone))}

    def draft_block(self, body) -> Dict:
        trend_id, campaign_id, tone = parse_request(body)
        trend = self._trend(trend_id)
        copy = copywriter.write(trend, tone)
        try:
            if campaign_id is None:
                campaign = self.mailchimp.create_campaign(
                    copy.subject_line, copy.preview_text, f"Trend: {trend.get('name', trend_id)}",
                    self.from_name, self.reply_to)
                campaign_id = campaign["id"]
            else:
                campaign = self.mailchimp.get_campaign(campaign_id)
            self.mailchimp.set_content(campaign_id, copywriter.to_html(copy), copywriter.to_plain(copy))
        except MailchimpUnavailable as e:
            logger.warning("mailchimp unreachable: %s", e)
            raise ApiError(502, "mailchimp_unavailable", "Mailchimp can't be reached right now. Try again shortly.")
        except MailchimpError as e:
            if e.status == 404:
                raise ApiError(404, "campaign_not_found", f"Mailchimp campaign {campaign_id} not found")
            logger.warning("mailchimp error: %s", e)
            raise ApiError(502, "mailchimp_error", f"Mailchimp rejected the request ({e.status} {e.title})")
        block_id = str(uuid.uuid4())
        self.repo.insert_draft_block(block_id, trend_id, campaign_id, copy.body)
        logger.info("draft block %s: trend %s -> campaign %s (%s)", block_id, trend_id, campaign_id, tone)
        # Mailchimp's web view of the campaign (real campaigns have one too), for "Open the draft".
        preview_url = campaign.get("long_archive_url") or campaign.get("archive_url")
        return {"blockId": block_id, "status": "draft", "trendId": trend_id, "campaignId": campaign_id,
                "tone": tone, "previewUrl": preview_url, **copy_fields(copy)}

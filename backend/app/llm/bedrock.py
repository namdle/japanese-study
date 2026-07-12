"""AWS Bedrock adapter for the LLMProvider interface.

Uses boto3 to invoke a Bedrock model (defaults to Claude on Bedrock).
Reads AWS credentials from the environment (AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY, AWS_REGION) or from the default credential chain.
"""

from __future__ import annotations

import base64
import json
import os

import boto3

from app.llm.base import ChatResponse, LLMProvider, Message
from app.llm.claude import ProviderUnavailableError
from app.session.uploads import detect_image_mime

DEFAULT_MODEL_ID = "anthropic.claude-sonnet-5"


class BedrockProvider(LLMProvider):
    name = "bedrock"

    def __init__(self, *, model_id: str = DEFAULT_MODEL_ID, client=None):
        if client is not None:
            self._client = client
        else:
            region = os.getenv("AWS_REGION", "us-east-1")
            try:
                self._client = boto3.client("bedrock-runtime", region_name=region)
                self._client.meta.region_name  # noqa: B018 - tickles cred chain
            except Exception as exc:
                raise ProviderUnavailableError(
                    "Bedrock provider is not configured. "
                    "Set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_REGION."
                ) from exc
        self._model_id = model_id

    def chat(
        self,
        messages: list[Message],
        *,
        system: str,
        images: list[bytes] | None = None,
        temperature: float = 0.6,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        bedrock_messages: list[dict] = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        # Same Claude-on-Bedrock content shape as the Anthropic SDK.
        if images and bedrock_messages and bedrock_messages[-1]["role"] == "user":
            text_part = {"type": "text", "text": bedrock_messages[-1]["content"]}
            image_parts: list[dict] = []
            for raw in images:
                mime = detect_image_mime(raw) or "image/jpeg"
                image_parts.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": base64.b64encode(raw).decode("ascii"),
                        },
                    }
                )
            bedrock_messages[-1] = {
                "role": "user",
                "content": [*image_parts, text_part],
            }

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "system": system,
            "messages": bedrock_messages,
            "max_tokens": max_tokens or 1024,
            "temperature": temperature,
        }
        response = self._client.invoke_model(
            modelId=self._model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
        result = json.loads(response["body"].read())
        text = "".join(
            block.get("text", "") for block in result.get("content", [])
        ).strip()
        return ChatResponse(text=text)

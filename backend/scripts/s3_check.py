from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    ProfileNotFound,
)
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_BUCKET = "amasecrag"
TEST_PREFIX = "_connectivity-test"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify S3 access for the filing-storage bucket.",
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("S3_BUCKET") or DEFAULT_BUCKET,
        help=f"Bucket name (default: {DEFAULT_BUCKET}).",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
        help="Bucket region; detected automatically when omitted.",
    )
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE"))
    parser.add_argument(
        "--endpoint-url",
        default=os.getenv("S3_ENDPOINT_URL"),
        help="Override the S3 endpoint for S3-compatible storage.",
    )
    parser.add_argument(
        "--write-test",
        action="store_true",
        help="Test PutObject, GetObject and DeleteObject with a temporary object.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the temporary object created by --write-test.",
    )
    parser.add_argument("--json", action="store_true", help="Emit a JSON report.")
    return parser.parse_args()


def make_client(
    session: boto3.session.Session,
    region: str,
    endpoint_url: str | None,
):
    return session.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint_url,
        config=Config(
            connect_timeout=10,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
            signature_version="s3v4",
        ),
    )


def bucket_region(exc: ClientError) -> str | None:
    headers = exc.response.get("ResponseMetadata", {}).get("HTTPHeaders") or {}
    return headers.get("x-amz-bucket-region") or headers.get("X-Amz-Bucket-Region")


def explain(exc: ClientError) -> str:
    error = exc.response.get("Error") or {}
    code = str(error.get("Code") or "Unknown")
    status = (exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    region = bucket_region(exc)
    if code in {"404", "NoSuchBucket", "NotFound"} or status == 404:
        return "bucket not found; check the name and region"
    if code in {"403", "AccessDenied", "Forbidden"} or status == 403:
        if region:
            return (
                f"access denied (bucket region {region}); grant s3:ListBucket "
                "and s3:GetBucketLocation to these credentials"
            )
        return "access denied; grant s3:ListBucket to these credentials"
    if code in {"301", "PermanentRedirect"}:
        return f"wrong region; bucket is in {region}" if region else "wrong region; pass --region"
    if code in {
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "ExpiredToken",
        "InvalidToken",
    }:
        return f"credential problem ({code})"
    return f"{code}: {error.get('Message') or 'unknown S3 error'}"


def main() -> int:
    load_dotenv(ENV_PATH, override=False)
    args = parse_args()
    report: dict[str, Any] = {"bucket": args.bucket, "checks": []}

    def add(check: str, ok: bool, detail: str, *, critical: bool = True) -> None:
        report["checks"].append(
            {"check": check, "ok": ok, "detail": detail, "critical": critical}
        )
        if not args.json:
            print(f"[{'ok' if ok else 'FAIL'}] {check}: {detail}")

    def finish() -> int:
        failed = any(
            not entry["ok"] for entry in report["checks"] if entry["critical"]
        )
        report["ok"] = not failed
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            state = "PASS" if not failed else "FAIL"
            print(f"\nResult: {state} for s3://{args.bucket}")
            if failed:
                print(
                    "Required IAM actions: s3:ListBucket and s3:GetBucketLocation "
                    "on the bucket, plus s3:PutObject, s3:GetObject and "
                    "s3:DeleteObject on the object prefix for --write-test."
                )
        return 0 if not failed else 1

    try:
        session_kwargs: dict[str, Any] = {}
        if args.profile:
            session_kwargs["profile_name"] = args.profile
        session = boto3.session.Session(**session_kwargs)
    except ProfileNotFound as exc:
        add("credentials", False, str(exc))
        return finish()

    region = args.region or session.region_name or "us-east-1"
    client = make_client(session, region, args.endpoint_url)

    try:
        client.head_bucket(Bucket=args.bucket)
        add("head_bucket", True, f"reachable in region {region}")
    except ClientError as exc:
        discovered = bucket_region(exc)
        if discovered and discovered != region:
            region = discovered
            client = make_client(session, region, args.endpoint_url)
            try:
                client.head_bucket(Bucket=args.bucket)
                add("head_bucket", True, f"reachable in detected region {region}")
            except ClientError as retry_exc:
                add("head_bucket", False, explain(retry_exc))
                return finish()
        else:
            add("head_bucket", False, explain(exc))
            return finish()
    except NoCredentialsError:
        add(
            "head_bucket",
            False,
            "no AWS credentials; attach an IAM role or set "
            "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY",
        )
        return finish()
    except BotoCoreError as exc:
        add("head_bucket", False, f"connection/config error: {exc}")
        return finish()

    try:
        identity = session.client("sts", region_name=region).get_caller_identity()
        add(
            "identity",
            True,
            identity.get("Arn") or identity.get("UserId") or "resolved",
            critical=False,
        )
    except (BotoCoreError, ClientError) as exc:
        add("identity", True, f"unavailable ({exc})", critical=False)

    try:
        listed = client.list_objects_v2(Bucket=args.bucket, MaxKeys=1)
        first_key = (listed.get("Contents") or [{}])[0].get("Key")
        detail = f"ListBucket allowed ({listed.get('KeyCount', 0)} key(s) returned"
        detail += f", first: {first_key})" if first_key else ")"
        add("list_objects", True, detail)
    except ClientError as exc:
        add("list_objects", False, explain(exc))

    if args.write_test:
        key = f"{TEST_PREFIX}/{uuid.uuid4().hex}.txt"
        payload = b"sec-filings-rag s3 connectivity check\n"
        try:
            client.put_object(
                Bucket=args.bucket,
                Key=key,
                Body=payload,
                ContentType="text/plain",
            )
            add("put_object", True, f"uploaded s3://{args.bucket}/{key}")
        except ClientError as exc:
            add("put_object", False, explain(exc))
        else:
            try:
                body = client.get_object(Bucket=args.bucket, Key=key)["Body"].read()
                if body == payload:
                    add("get_object", True, "downloaded object matches uploaded bytes")
                else:
                    add("get_object", False, "downloaded object does not match upload")
            except ClientError as exc:
                add("get_object", False, explain(exc))
            if args.keep:
                add("cleanup", True, f"skipped; object kept at s3://{args.bucket}/{key}")
            else:
                try:
                    client.delete_object(Bucket=args.bucket, Key=key)
                    add("delete_object", True, f"removed s3://{args.bucket}/{key}")
                except ClientError as exc:
                    add("delete_object", False, explain(exc))

    return finish()


if __name__ == "__main__":
    sys.exit(main())
